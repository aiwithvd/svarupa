"""Design styles: which industry style each unit follows, and where it breaks.

`design_for` proposes the best-fitting style per unit (fit = coverage x
compliance, proposed at 0.5 or more), or the accepted one from
`.svarupa/design.yaml`. Its violations become health checks.
"""

from __future__ import annotations

from svarupa.build import Graph
from svarupa.design.classify import classify_system
from svarupa.design.match import assign, fit_assigned, fit_style, unplaced
from svarupa.design.model import Design, Fit, Style, Unit, UnitDesign, conformance
from svarupa.design.styles import BY_ID, CATALOG
from svarupa.design.units import find_units, signals
from svarupa.detect import Scan

__all__ = [
    "BY_ID",
    "CATALOG",
    "MIN_FIT",
    "Design",
    "Fit",
    "Style",
    "Unit",
    "UnitDesign",
    "conformance",
    "design_for",
]

MIN_FIT = 0.5


def design_for(scan: Scan, graph: Graph, accepted: object | None = None) -> Design:
    from svarupa.design.file import DesignFile

    units = find_units(scan, graph)
    sig = signals(graph)
    chosen_by_file = _accepted_units(accepted)
    excepted = tuple(accepted.exceptions) if isinstance(accepted, DesignFile) else ()
    excused = frozenset((e.rule, e.src, e.dst) for e in excepted)
    out: list[UnitDesign] = []
    for unit in units:
        # A unit with no proven level may be a backend whose framework routes
        # are not read yet (Gin, Echo, Spring): compare it with both.
        levels = {"backend", "library"} if unit.level == "library" else {unit.level}
        candidates = [s for s in CATALOG if s.level in levels]
        # Ties go to the style whose parts the code actually fills: layered
        # with api, service and data all present explains code better than
        # clean with two modules in one adapters ring.
        fits = sorted(
            (fit_style(unit, s, graph, sig, excused) for s in candidates),
            key=lambda f: (-f.fit, -_filled(f), f.style),
        )
        key = unit.id or "."
        note = ""
        if key in chosen_by_file:
            accepted_fit, note = _apply_accepted(unit, chosen_by_file[key], graph, sig, excused)
            if accepted_fit is not None:
                rest = tuple(f for f in fits if f.style != accepted_fit.style)[:3]
                out.append(UnitDesign(unit, accepted_fit, "accepted", rest, note))
                continue
        if fits and fits[0].fit >= MIN_FIT:
            out.append(UnitDesign(unit, fits[0], "inferred", tuple(fits[1:4]), note))
            continue
        out.append(_unclear(unit, fits, note))
    system, why = classify_system(scan, graph, units)
    return Design(system, why, tuple(out), excepted)


def _unclear(unit: Unit, fits: list[Fit], note: str) -> UnitDesign:
    """No style fits well: name the closest one and what would move the code there."""
    if not fits or fits[0].fit == 0.0:
        text = "no clear design: no style's parts were found in this unit"
        return UnitDesign(
            unit, None, "none", tuple(fits[:3]), "; ".join(filter(None, (note, text)))
        )
    closest = fits[0]
    style = BY_ID[closest.style]
    moves = [
        f"fix {v.evidence.file}:{v.evidence.start_line}: {v.message}"
        for v in closest.violations[:5]
    ] + [
        f"place {m} in a {closest.style} part (rename or move it)"
        for m in unplaced(unit, style, closest)[:5]
    ]
    text = f"no clear design; closest is {closest.style} (fit {closest.fit:.2f})"
    return UnitDesign(
        unit,
        None,
        "none",
        tuple(fits[:3]),
        "; ".join(filter(None, (note, text))),
        tuple(moves),
    )


def _filled(fit: Fit) -> float:
    parts = len(BY_ID[fit.style].parts)
    return len({a.part for a in fit.assignments}) / parts if parts else 0.0


def _accepted_units(accepted: object | None) -> dict[str, tuple[str, dict[str, list[str]]]]:
    from svarupa.design.file import DesignFile

    if not isinstance(accepted, DesignFile):
        return {}
    return {u: (spec.style, spec.parts) for u, spec in accepted.units.items()}


def _apply_accepted(
    unit: Unit,
    spec: tuple[str, dict[str, list[str]]],
    graph: Graph,
    sig: dict[str, frozenset[str]],
    excused: frozenset[tuple[str, str, str]],
) -> tuple[Fit | None, str]:
    from svarupa.design.model import Assignment

    style_id, parts = spec
    style = BY_ID.get(style_id)
    if style is None:
        return None, f"design.yaml names unknown style '{style_id}'; using the inferred design"
    known = {p.id for p in style.parts}
    listed = {m: p for p, ms in parts.items() if p in known for m in ms}
    missing = sorted(m for m in listed if m not in unit.modules)
    assignments = [
        Assignment(m, p, None, "accepted in design.yaml")
        for m, p in sorted(listed.items())
        if m in unit.modules
    ]
    if not parts:
        assignments = assign(unit, style, sig)
    fit = fit_assigned(unit, style, assignments, graph, sig, excused)
    notes: list[str] = []
    if missing:
        notes.append(f"design.yaml lists modules that no longer exist: {', '.join(missing)}")
    if fit.coverage == 0.0:
        notes.append("the accepted design matches no current modules: nothing to check")
    return fit, "; ".join(notes)
