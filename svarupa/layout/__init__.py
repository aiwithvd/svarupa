"""Stage 6a: assign coordinates and check the geometry.

`derive` decides what a diagram says; this decides where it says it, and then
proves the result is drawable without opening a browser.

Which engine serves which diagram is declared once, in `ENGINE_FOR_KIND`. A
kind with no entry is a bug rather than a default: silently falling back to a
generic layout is how a flow diagram ends up laid out as a dependency tree.
"""

from __future__ import annotations

from collections.abc import Mapping

from svarupa.derive.base import DiagramKind, DiagramSet
from svarupa.diagnostics import Diagnostic, Severity
from svarupa.layout.engines import ENGINES, lay_out
from svarupa.layout.geometry import Band, Box, Canvas, Route, Style
from svarupa.layout.validate import MIN_GAP, validate

__all__ = [
    "ENGINES",
    "ENGINE_FOR_KIND",
    "MIN_GAP",
    "Band",
    "Box",
    "Canvas",
    "LaidOutDiagram",
    "Route",
    "Style",
    "lay_out",
    "lay_out_set",
    "validate",
]

ENGINE_FOR_KIND: dict[DiagramKind, str] = {
    DiagramKind.ARCHITECTURE: "clustered",
    DiagramKind.DEPLOY_TOPOLOGY: "flow",
    DiagramKind.MODULE_DEPS: "layered",
    DiagramKind.CLASS_HIERARCHY: "layered",
    DiagramKind.REQUEST_FLOW: "flow",
    DiagramKind.ERD: "grid",
    DiagramKind.API_SURFACE: "grid",
}


class LaidOutDiagram:
    """One diagram set, positioned, plus whatever the geometry check found.

    Canvases that failed validation are kept separately rather than discarded.
    The report has to be able to say which diagram was withheld and why, and it
    cannot do that from an absence.
    """

    __slots__ = ("canvases", "engine_name", "kind", "problems", "withheld")

    def __init__(
        self,
        kind: DiagramKind,
        engine_name: str,
        canvases: dict[str, Canvas],
        withheld: dict[str, Canvas],
        problems: tuple[Diagnostic, ...],
    ) -> None:
        self.kind = kind
        self.engine_name = engine_name
        self.canvases = canvases
        self.withheld = withheld
        self.problems = problems

    @property
    def ok(self) -> bool:
        return not self.withheld


def lay_out_set(ds: DiagramSet, style: Style | None = None) -> LaidOutDiagram:
    """Lay out every spec in a set and validate each one.

    A canvas that fails geometry validation is withheld, not shipped. The other
    canvases in the set survive, matching how `derive_all` treats a broken
    deriver: one defect loses one thing, never the run. The CLI still exits
    non-zero, so a withheld diagram cannot pass unnoticed.
    """
    style = style or Style()
    engine = ENGINE_FOR_KIND[ds.kind]
    good: dict[str, Canvas] = {}
    bad: dict[str, Canvas] = {}
    problems: list[Diagnostic] = []
    for spec_id in sorted(ds.specs):
        canvas = lay_out(ds.specs[spec_id], style, engine)
        found = validate(canvas, style) + canvas.diagnostics
        if found:
            bad[spec_id] = canvas
            problems.extend(found)
        else:
            good[spec_id] = canvas
    problems.extend(_navigability_after_withholding(ds, good, bad))
    return LaidOutDiagram(ds.kind, engine, good, bad, tuple(problems))


def _navigability_after_withholding(
    ds: DiagramSet, good: Mapping[str, Canvas], bad: Mapping[str, Canvas]
) -> list[Diagnostic]:
    """Re-establish the navigation invariant after canvases are dropped.

    `DiagramSet.validate` guarantees the set was navigable, but withholding
    changes the set. Two consequences it does not cover: a surviving box can
    point `child_spec` at a spec that is no longer drawn, and the root itself
    can be the one withheld, leaving sub-diagrams with no entry point.

    Reported rather than repaired. Pruning the dangling links would silently
    turn a drillable group into a leaf, which tells a reader the group has no
    internal structure. That is a wrong claim, and a missing diagram with a
    stated reason is always preferable to a quiet one.
    """
    if not bad:
        return []
    out: list[Diagnostic] = []
    if ds.root not in good:
        out.append(
            Diagnostic(
                code="SVA-R-005",
                severity=Severity.ERROR,
                message=(
                    f"the root canvas was withheld, so the remaining "
                    f"{len(good)} view(s) have no entry point"
                ),
                subject=ds.kind.value,
            )
        )
    dangling = sorted(
        {
            n.child_spec
            for sid in good
            for n in ds.specs[sid].nodes
            if n.child_spec is not None and n.child_spec not in good
        }
    )
    out.extend(
        Diagnostic(
            code="SVA-R-005",
            severity=Severity.ERROR,
            message="a drawn box drills into a view that was withheld",
            subject=target,
        )
        for target in dangling
    )
    return out
