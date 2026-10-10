"""Design styles: which industry style each unit follows, and where it breaks.

`design_for` proposes the best-fitting style per unit (fit = coverage x
compliance, proposed at 0.5 or more), or the accepted one from
`.svarupa/design.yaml`. Its violations become health checks.
"""

from __future__ import annotations

from svarupa.build import Graph
from svarupa.design.classify import classify_system
from svarupa.design.match import assign, fit_assigned, fit_style
from svarupa.design.model import Design, Fit, Style, Unit, UnitDesign
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
    "design_for",
]

MIN_FIT = 0.5


def design_for(scan: Scan, graph: Graph, accepted: object | None = None) -> Design:
    units = find_units(scan, graph)
    sig = signals(graph)
    chosen_by_file = _accepted_units(accepted)
    out: list[UnitDesign] = []
    for unit in units:
        candidates = [s for s in CATALOG if s.level == unit.level]
        # Ties go to the style whose parts the code actually fills: layered
        # with api, service and data all present explains code better than
        # clean with two modules in one adapters ring.
        fits = sorted(
            (fit_style(unit, s, graph, sig) for s in candidates),
            key=lambda f: (-f.fit, -_filled(f), f.style),
        )
        key = unit.id or "."
        if key in chosen_by_file:
            accepted_fit, note = _apply_accepted(unit, chosen_by_file[key], graph, sig)
            if accepted_fit is not None:
                rest = tuple(f for f in fits if f.style != accepted_fit.style)[:3]
                out.append(UnitDesign(unit, accepted_fit, "accepted", rest, note))
                continue
            best = fits[0] if fits else None
            if best is not None and best.fit >= MIN_FIT:
                out.append(UnitDesign(unit, best, "inferred", tuple(fits[1:4]), note))
            else:
                out.append(UnitDesign(unit, None, "none", tuple(fits[:3]), note))
            continue
        if fits and fits[0].fit >= MIN_FIT:
            out.append(UnitDesign(unit, fits[0], "inferred", tuple(fits[1:4])))
        else:
            closest = fits[0].style if fits else "none"
            out.append(
                UnitDesign(
                    unit,
                    None,
                    "none",
                    tuple(fits[:3]),
                    f"no clear design; closest is {closest}",
                )
            )
    system, why = classify_system(scan, graph, units)
    from svarupa.design.file import DesignFile

    excepted = tuple(accepted.exceptions) if isinstance(accepted, DesignFile) else ()
    return Design(system, why, tuple(out), excepted)


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
    note = (
        f"design.yaml lists modules that no longer exist: {', '.join(missing)}"
        if missing
        else ""
    )
    return fit_assigned(unit, style, assignments, graph, sig), note
