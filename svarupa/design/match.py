"""Assign modules to a style's parts, check the rules, score the fit."""

from __future__ import annotations

from svarupa.build import Graph
from svarupa.design.model import Assignment, DesignViolation, Fit, Style, Unit
from svarupa.extract.base import module_of
from svarupa.model import EdgeKind, Evidence

__all__ = ["assign", "fit_assigned", "fit_style"]

_IO = frozenset({"routes", "datastore", "messagebus", "cloud", "ui"})


def _segments(module: str, unit: str) -> list[str]:
    rel = module[len(unit) :].strip("/") if unit else module
    return [s.lower() for s in rel.split("/") if s]


def assign(unit: Unit, style: Style, sig: dict[str, frozenset[str]]) -> list[Assignment]:
    out: list[Assignment] = []
    for module in unit.modules:
        segs = _segments(module, unit.id)
        best: tuple[int, int, Assignment] | None = None
        for rank, part in enumerate(style.parts):
            score, reason, at = 0, "", -1
            if segs and segs[-1] in part.names:
                score, reason, at = (
                    3,
                    f"directory '{segs[-1]}' names the {part.id} part",
                    len(segs) - 1,
                )
            else:
                hits = [i for i, s in enumerate(segs) if s in part.names]
                if hits:
                    at = hits[-1]
                    score, reason = 2, f"inside '{segs[at]}', the {part.id} part"
            shown = sig.get(module, frozenset()) & part.evidence
            if shown and score < 2:
                score, reason = (
                    2,
                    f"proves {', '.join(sorted(shown))}, which the {part.id} part does",
                )
            if score and (best is None or (score, -rank) > (best[0], best[1])):
                slice_ = segs[at + 1] if part.slices and 0 <= at < len(segs) - 1 else None
                best = (score, -rank, Assignment(module, part.id, slice_, reason))
        if best is not None:
            out.append(best[2])
    return out


def _import_evidence(graph: Graph) -> dict[tuple[str, str], Evidence]:
    first: dict[tuple[str, str], Evidence] = {}
    for e in graph.edges:
        if e.kind is not EdgeKind.IMPORTS or not e.evidence:
            continue
        key = (module_of(e.src.split("#", 1)[0]), module_of(e.dst.split("#", 1)[0]))
        ev = e.evidence[0]
        if key not in first or (ev.file, ev.start_line) < (
            first[key].file,
            first[key].start_line,
        ):
            first[key] = ev
    return first


def _purity_evidence(graph: Graph, module: str) -> Evidence | None:
    found = [r.evidence for r in graph.routes if module_of(r.file) == module]
    found += [
        x.evidence
        for x in graph.externals
        if module_of(x.file) == module
        and x.category in ("database", "messagebus", "cloud", "frontend")
    ]
    return min(found, key=lambda ev: (ev.file, ev.start_line)) if found else None


def fit_assigned(
    unit: Unit,
    style: Style,
    assignments: list[Assignment],
    graph: Graph,
    sig: dict[str, frozenset[str]],
) -> Fit:
    by_module = {a.module: a for a in assignments}
    order = {p: i for i, p in enumerate(style.order)}
    evidence = _import_evidence(graph)
    deps = sorted(
        (a, b) for a, b in graph.module_deps if a in by_module and b in by_module and a != b
    )
    violations: list[DesignViolation] = []
    checked = 0
    for a, b in deps:
        src, dst = by_module[a], by_module[b]
        ev = evidence.get((a, b))
        if ev is None:
            continue
        checked += 1
        rule, why = None, ""
        if (src.part, dst.part) in style.forbidden or (
            src.part in order and dst.part in order and order[dst.part] < order[src.part]
        ):
            rule, why = (
                "layer-direction",
                f"the {src.part} part may not depend on the {dst.part} part",
            )
        elif (
            src.part == dst.part
            and src.part in style.independent
            and src.slice
            and dst.slice
            and src.slice != dst.slice
        ):
            rule, why = (
                "part-independence",
                f"{src.part} slices '{src.slice}' and '{dst.slice}' must not import each other",
            )
        if (
            rule is None
            and dst.part in style.public_entry
            and dst.slice
            and (src.part != dst.part or src.slice != dst.slice)
        ):
            root = _slice_root(b, dst.slice)
            if b != root:
                rule, why = (
                    "public-entry",
                    f"import {dst.part} '{dst.slice}' through its root {root}, not {b}",
                )
        if rule is not None:
            violations.append(DesignViolation(rule, a, b, ev, why))
    for a in sorted(by_module):
        # A breach counts; a pure module is not a free pass.
        if by_module[a].part in style.pure and sig.get(a, frozenset()) & _IO:
            checked += 1
            ev = _purity_evidence(graph, a)
            if ev is not None:
                violations.append(
                    DesignViolation(
                        "core-purity",
                        a,
                        "",
                        ev,
                        f"the {by_module[a].part} part must not use frameworks or I/O",
                    )
                )
    bad = len(violations)
    coverage = len(by_module) / len(unit.modules) if unit.modules else 0.0
    compliance = (checked - bad) / checked if checked else 1.0
    return Fit(
        unit.id,
        style.id,
        coverage,
        compliance,
        tuple(sorted(assignments, key=lambda x: x.module)),
        tuple(
            sorted(violations, key=lambda v: (v.rule, v.evidence.file, v.evidence.start_line))
        ),
    )


def _slice_root(module: str, slice_: str) -> str:
    parts = module.split("/")
    return "/".join(parts[: parts.index(slice_) + 1]) if slice_ in parts else module


def fit_style(unit: Unit, style: Style, graph: Graph, sig: dict[str, frozenset[str]]) -> Fit:
    return fit_assigned(unit, style, assign(unit, style, sig), graph, sig)
