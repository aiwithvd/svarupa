"""Check explanations, laid out for the report and the viewer."""

from __future__ import annotations

from svarupa.build import Graph
from svarupa.health import BY_ID, Health
from svarupa.health.explain import example_for, explanation

__all__ = ["checks_in_order", "explanation_markdown", "file_lang", "languages_by_check"]


def file_lang(graph: Graph | None, path: str) -> str | None:
    """The language Svarupa parsed this file as, if it parsed it."""
    node = graph.nodes.get(path) if graph is not None else None
    return node.lang if node is not None else None


def checks_in_order(health: Health, graph: Graph | None) -> list[tuple[str, str | None]]:
    """Each check with violations, in the order its first violation is
    listed, with the language of that violation's file."""
    seen: dict[str, str | None] = {}
    for v in health.violations:
        if v.check not in seen:
            seen[v.check] = file_lang(graph, v.evidence[0].file)
    return list(seen.items())


def languages_by_check(health: Health, graph: Graph | None) -> dict[str, list[str]]:
    """Every language each check fired in, in the order first seen."""
    out: dict[str, list[str]] = {}
    for v in health.violations:
        lang = file_lang(graph, v.evidence[0].file)
        langs = out.setdefault(v.check, [])
        if lang is not None and lang not in langs:
            langs.append(lang)
    return out


def explanation_markdown(check_id: str, lang: str | None) -> list[str]:
    e = explanation(check_id)
    lines = [
        f"#### {BY_ID[check_id].title} (`{check_id}`)",
        "",
        f"What: {e.what}",
        "",
        f"Why: {e.why}",
        "",
        f"How to fix: {e.fix}",
        "",
    ]
    ex = example_for(check_id, lang)
    if ex is not None and lang is not None:
        lines += [f"Before ({lang}):", "", f"```{lang}", ex.before, "```", "", "After:", ""]
        lines += [f"```{lang}", ex.after, "```", ""]
    return lines
