"""Codebase health: checks against published limits, graded with SQALE.

`assess` is pure: it reads the graph and the source text it is given and
reads no file itself. `source_texts` is the one place that reads files, for
the CLI and the benchmark.
"""

from __future__ import annotations

from collections.abc import Mapping

from svarupa.build import Graph
from svarupa.detect import Scan, read_text
from svarupa.health.catalog import AREAS, BY_ID, CATALOG, Check
from svarupa.health.checks import class_checks, file_checks, function_checks, module_checks
from svarupa.health.duplication import duplicated_blocks
from svarupa.health.model import Health, Violation
from svarupa.health.score import grade

__all__ = [
    "AREAS",
    "BY_ID",
    "CATALOG",
    "Check",
    "Health",
    "Violation",
    "assess",
    "source_texts",
]

# Languages whose source is code a person maintains (SQL schemas are not).
_CODE = frozenset({"python", "typescript", "javascript", "go", "java"})


def source_texts(scan: Scan, graph: Graph) -> dict[str, str]:
    """Text of every architecture source file in an analyzed language."""
    out: dict[str, str] = {}
    for rec in scan.files:
        if rec.path in graph.architecture_paths and rec.lang in _CODE:
            try:
                out[rec.path] = read_text(scan.root, rec.path)
            except OSError:
                continue
    return out


def assess(graph: Graph, texts: Mapping[str, str]) -> Health:
    violations = [
        *function_checks(graph),
        *file_checks(texts),
        *class_checks(graph),
        *duplicated_blocks(texts),
        *module_checks(graph),
    ]
    return grade(graph, texts, violations)
