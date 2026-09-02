"""Stage 6a: assign coordinates and check the geometry.

`derive` decides what a diagram says; this decides where it says it, and then
proves the result is drawable without opening a browser.

Which engine serves which diagram is declared once, in `ENGINE_FOR_KIND`. A
kind with no entry is a bug rather than a default: silently falling back to a
generic layout is how a flow diagram ends up laid out as a dependency tree.
"""

from __future__ import annotations

from svarupa.derive.base import DiagramKind, DiagramSet
from svarupa.diagnostics import Diagnostic
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
    DiagramKind.DEPLOY_TOPOLOGY: "clustered",
    DiagramKind.MODULE_DEPS: "layered",
    DiagramKind.CLASS_HIERARCHY: "layered",
    DiagramKind.REQUEST_FLOW: "layered",
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
        found = validate(canvas, style)
        if found:
            bad[spec_id] = canvas
            problems.extend(found)
        else:
            good[spec_id] = canvas
    return LaidOutDiagram(ds.kind, engine, good, bad, tuple(problems))
