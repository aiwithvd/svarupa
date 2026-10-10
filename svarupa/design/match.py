"""Assign modules to a style's parts, check the rules, score the fit."""

from __future__ import annotations

from svarupa.build import Graph
from svarupa.design.model import Assignment, DesignViolation, Fit, Style, Unit
from svarupa.extract.base import module_of
from svarupa.model import EdgeKind, Evidence

__all__ = ["assign", "fit_assigned", "fit_style"]

_IO = frozenset({"routes", "datastore", "messagebus", "cloud", "ui"})
# Cross-cutting helpers and composition roots exist in every style. They are
# left out of a style's score unless one of its parts names them (for
# example Feature-Sliced Design's `shared`).
# fmt: off
_UTILITY = frozenset(
    {
        "config", "configs", "utils", "util", "helpers", "helper", "lib", "libs",
        "common", "shared", "types", "constants", "docs", "scripts", "validations",
        "validators", "middleware", "middlewares", "errors", "exceptions",
    }
)
# fmt: on
_COMPOSITION = frozenset({"src", "cmd", "bin", "main"})


def _neutral(segs: list[str], style: Style) -> bool:
    if not segs:
        return True  # the unit root wires everything together
    last = segs[-1]
    if last not in _UTILITY and last not in _COMPOSITION:
        return False
    return not any(last in p.names for p in style.parts)


def _segments(module: str, unit: str) -> list[str]:
    rel = module[len(unit) :].strip("/") if unit else module
    return [s.lower() for s in rel.split("/") if s]


def assign(unit: Unit, style: Style, sig: dict[str, frozenset[str]]) -> list[Assignment]:
    out: list[Assignment] = []
    for module in unit.modules:
        segs = _segments(module, unit.id)
        if _neutral(segs, style):
            continue
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


def _import_evidence(graph: Graph) -> dict[tuple[str, str], dict[str, Evidence]]:
    """(source module, target module) -> importing file -> its first import line."""
    first: dict[tuple[str, str], dict[str, Evidence]] = {}
    for e in graph.edges:
        if e.kind is not EdgeKind.IMPORTS or not e.evidence:
            continue
        src_file = e.src.split("#", 1)[0]
        key = (module_of(src_file), module_of(e.dst.split("#", 1)[0]))
        ev = e.evidence[0]
        files = first.setdefault(key, {})
        if src_file not in files or ev.start_line < files[src_file].start_line:
            files[src_file] = ev
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
    bad = 0
    for a, b in deps:
        src, dst = by_module[a], by_module[b]
        files = evidence.get((a, b))
        if not files:
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
            bad += 1  # compliance counts module pairs; violations count importing files
            violations.extend(
                DesignViolation(rule, a, b, ev, why) for _, ev in sorted(files.items())
            )
    for a in sorted(by_module):
        # A breach counts; a pure module is not a free pass.
        if by_module[a].part in style.pure and sig.get(a, frozenset()) & _IO:
            checked += 1
            bad += 1
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
    scored = [
        m for m in unit.modules if m in by_module or not _neutral(_segments(m, unit.id), style)
    ]
    coverage = len(by_module) / len(scored) if scored else 0.0
    # A style is relations between parts: with one filled part no structure
    # was recognised, however many modules landed in it.
    if len(style.parts) > 1 and len({x.part for x in by_module.values()}) < 2:
        coverage = 0.0
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
