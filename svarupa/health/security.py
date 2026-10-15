"""Security checks from the risk facts the walker recorded."""

from __future__ import annotations

from svarupa.build import Graph
from svarupa.extract.base import module_of
from svarupa.health.catalog import BY_ID
from svarupa.health.model import Violation

__all__ = ["security_checks"]

_SINKS = {
    "sql": ("sql-injection", "SQL", "pass values as query parameters"),
    "shell": (
        "command-injection",
        "a shell command",
        "pass arguments as a list, without a shell",
    ),
    "code": ("code-injection", "code", "do not run code built from data"),
}


def _cwe(check_id: str) -> str:
    return BY_ID[check_id].source.split(";", 1)[0]


def security_checks(graph: Graph) -> list[Violation]:
    out: list[Violation] = []
    for r in graph.risks:
        where = f" in {r.function}" if r.function else ""
        if r.kind == "sink-built" and r.tag in _SINKS:
            check_id, what, fix = _SINKS[r.tag]
            message = (
                f"{r.detail}{where} runs {what} built from strings; {fix} ({_cwe(check_id)})"
            )
        elif r.kind == "unsafe" and r.tag in BY_ID:
            check_id = r.tag
            message = f"{r.detail}{where}: {BY_ID[check_id].title.lower()} ({_cwe(check_id)})"
        else:
            continue
        out.append(
            Violation(
                check=check_id,
                message=message,
                evidence=(r.evidence,),
                module=module_of(r.evidence.file),
                value=1,
                minutes=BY_ID[check_id].minutes,
            )
        )
    return out
