"""SQALE grading: technical debt ratio for maintainability, worst severity
for the other areas, and the order fixes are worth doing in."""

from __future__ import annotations

from collections.abc import Mapping

from svarupa.build import Graph
from svarupa.extract.base import module_of
from svarupa.health.catalog import AREAS, BY_ID, CATALOG, SEVERITIES
from svarupa.health.model import Health, Violation

__all__ = ["grade", "ncloc"]

MINUTES_PER_LINE = 30  # SQALE default development cost
_RATIO = ((0.05, "A"), (0.10, "B"), (0.20, "C"), (0.50, "D"))
_BY_SEVERITY = {"info": "A", "minor": "B", "major": "C", "critical": "D", "blocker": "E"}
_COMMENT = ("#", "//", "/*", "*", "--")


def ncloc(texts: Mapping[str, str]) -> int:
    """Lines of code: not blank and not comment-only."""
    return sum(
        1
        for text in texts.values()
        for raw in text.splitlines()
        if (line := raw.strip()) and not line.startswith(_COMMENT)
    )


def _ratio_rating(ratio: float) -> str:
    for limit, letter in _RATIO:
        if ratio <= limit:
            return letter
    return "E"


def _area_rating(area: str, violations: list[Violation]) -> str | None:
    if not any(c.area == area for c in CATALOG):
        return None  # not assessed yet: no grade is better than a free A
    severities = [BY_ID[v.check].severity for v in violations if BY_ID[v.check].area == area]
    if not severities:
        return "A"
    return _BY_SEVERITY[max(severities, key=SEVERITIES.index)]


def _impacts(graph: Graph) -> dict[str, int]:
    fan_in: dict[str, int] = {}
    for _, dst in graph.module_deps:
        fan_in[dst] = fan_in.get(dst, 0) + 1
    request = {module_of(r.file) for r in graph.routes}
    frontier = list(request)
    while frontier:
        here = frontier.pop()
        for src, dst in graph.module_deps:
            if src == here and dst not in request:
                request.add(dst)
                frontier.append(dst)
    modules = set(fan_in) | request | {m for d in graph.module_deps for m in d}
    return {m: fan_in.get(m, 0) + (5 if m in request else 0) for m in modules}


def grade(graph: Graph, texts: Mapping[str, str], violations: list[Violation]) -> Health:
    impacts = _impacts(graph)
    ranked = sorted(
        (
            Violation(
                v.check, v.message, v.evidence, v.module, v.value, v.minutes,
                impacts.get(v.module, 0), v.symbol,
            )
            for v in violations
        ),
        key=lambda v: (
            -v.impact, -v.minutes, v.check, v.evidence[0].file, v.evidence[0].start_line
        ),
    )  # fmt: skip
    lines = ncloc(texts)
    debt = sum(v.minutes for v in ranked if BY_ID[v.check].area == "maintainability")
    ratio = debt / (lines * MINUTES_PER_LINE) if lines else 0.0
    ratings = tuple(
        (
            area,
            _ratio_rating(ratio) if area == "maintainability" else _area_rating(area, ranked),
        )
        for area in AREAS
    )
    return Health(lines, debt, ratio, ratings, tuple(ranked))
