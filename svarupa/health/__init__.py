"""Codebase health: checks against published limits, graded with SQALE.

`assess` is pure: it reads the graph and the source text it is given and
reads no file itself. `source_texts` is the one place that reads files, for
the CLI and the benchmark.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import TYPE_CHECKING

from svarupa.build import Graph
from svarupa.detect import Scan, read_text
from svarupa.health.auth import auth_checks
from svarupa.health.catalog import AREAS, BY_ID, CATALOG, Check
from svarupa.health.checks import (
    class_checks,
    design_checks,
    file_checks,
    function_checks,
    module_checks,
)
from svarupa.health.duplication import duplicated_blocks
from svarupa.health.model import Health, Violation
from svarupa.health.score import grade
from svarupa.health.secrets import secret_checks, secret_texts
from svarupa.health.security import security_checks

if TYPE_CHECKING:
    from svarupa.design.file import DesignException
    from svarupa.design.model import Design

__all__ = [
    "AREAS",
    "BY_ID",
    "CATALOG",
    "Check",
    "Health",
    "Violation",
    "assess",
    "secret_texts",
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


def _excepted(v: Violation, exceptions: Sequence[DesignException]) -> bool:
    ev = v.evidence[0]
    places = {ev.file, f"{ev.file}:{ev.start_line}"}
    return any(e.at and e.rule == v.check and e.at in places for e in exceptions)


def assess(
    graph: Graph,
    texts: Mapping[str, str],
    design: Design | None = None,
    exceptions: Sequence[DesignException] = (),
    *,
    secrets: Mapping[str, str] | None = None,
    auth_markers: Sequence[str] = (),
) -> Health:
    unit_ids = [u.unit.id for u in design.units] if design is not None else []
    auth, notes = auth_checks(graph, texts, auth_markers, unit_ids or [""])
    found = [
        *function_checks(graph),
        *file_checks(texts),
        *class_checks(graph),
        *duplicated_blocks(texts),
        *module_checks(graph),
        *design_checks(design, exceptions),
        *security_checks(graph),
        *secret_checks(secrets or {}),
        *auth,
    ]
    kept = [v for v in found if not _excepted(v, exceptions)]
    accepted = sorted(
        (v for v in found if _excepted(v, exceptions)),
        key=lambda v: (v.check, v.evidence[0].file, v.evidence[0].start_line),
    )
    return replace(grade(graph, texts, kept), accepted=tuple(accepted), notes=tuple(notes))
