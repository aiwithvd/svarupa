"""Checks from the risk facts the walker recorded: security, performance
and reliability."""

from __future__ import annotations

from svarupa.build import Graph
from svarupa.extract.base import module_of
from svarupa.health.catalog import BY_ID
from svarupa.health.model import Violation

__all__ = ["risk_checks"]

_SINKS = {
    "sql": ("sql-injection", "SQL", "pass values as query parameters"),
    "shell": (
        "command-injection",
        "a shell command",
        "pass arguments as a list, without a shell",
    ),
    "code": ("code-injection", "code", "do not run code built from data"),
}

# Fact kind -> (check, what happens and what to do).
_RUNTIME = {
    "in-loop": (
        "query-in-loop",
        "runs a query on every pass of a loop; load what the loop needs in one query before it",
    ),
    "blocking-async": (
        "blocking-in-async",
        "blocks the event loop inside an async function; "
        "use an async client or run it in a worker thread",
    ),
    "empty-catch": (
        "swallowed-error",
        "hides the error; log it, handle it or let it raise",
    ),
}


def _ref(check_id: str) -> str:
    """The first source of a check: its CWE, or the book or rule it comes from."""
    return BY_ID[check_id].source.split(";", 1)[0]


def risk_checks(graph: Graph) -> list[Violation]:
    out: list[Violation] = []
    for r in graph.risks:
        where = f" in {r.function}" if r.function else ""
        if r.kind == "sink-built" and r.tag in _SINKS:
            check_id, what, fix = _SINKS[r.tag]
            message = (
                f"{r.detail}{where} runs {what} built from strings; {fix} ({_ref(check_id)})"
            )
        elif r.kind == "unsafe" and r.tag in BY_ID:
            check_id = r.tag
            message = f"{r.detail}{where}: {BY_ID[check_id].title.lower()} ({_ref(check_id)})"
        elif r.kind in _RUNTIME:
            check_id, what = _RUNTIME[r.kind]
            message = f"{r.detail}{where} {what} ({_ref(check_id)})"
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
