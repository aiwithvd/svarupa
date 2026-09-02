"""Structured diagnostics.

Every failure produces one of these, never a bare traceback. Codes are stable
so an agent can consume them and act on ``suggested_fixes`` rather than parsing
prose. See design 11.

Codes are the machine contract, so `CODES` below is the registry and a test
asserts it agrees with reality in **both** directions: every code emitted in
the package is registered, and every registered code is emitted somewhere.
One direction alone is not enough. Checking only that emissions are registered
lets the registry accumulate codes nothing produces, which is what happened
here: three lock codes were declared, never emitted, and their constant names
disagreed with their own values by one (`SVA_L_003_UNKNOWN_ESCAPE = "SVA-L-004"`),
while `lock/grammar.py` bypassed the constants entirely and hardcoded strings.
An attribute in the output that is never produced is the shape fail-closed
forbids, applied to the diagnostic surface.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum

__all__ = [
    "Diagnostic",
    "DiagnosticError",
    "Severity",
]


class Severity(str, Enum):
    ERROR = "ERROR"
    WARNING = "WARNING"
    INFO = "INFO"


@dataclass(frozen=True, slots=True)
class Diagnostic:
    code: str
    severity: Severity
    message: str
    subject: str | None = None
    location: str | None = None
    suggested_fixes: tuple[str, ...] = field(default_factory=tuple)

    def render(self) -> str:
        where = f" [{self.location}]" if self.location else ""
        what = f" {self.subject!r}" if self.subject else ""
        head = f"{self.severity.value} {self.code}{where}:{what} {self.message}"
        if not self.suggested_fixes:
            return head
        fixes = "\n".join(f"  fix: {f}" for f in self.suggested_fixes)
        return f"{head}\n{fixes}"

    def to_json(self) -> str:
        return json.dumps(
            {
                "code": self.code,
                "severity": self.severity.value,
                "message": self.message,
                "subject": self.subject,
                "location": self.location,
                "suggested_fixes": list(self.suggested_fixes),
            },
            sort_keys=True,
        )


class DiagnosticError(Exception):
    """An error carrying a structured diagnostic."""

    def __init__(self, diagnostic: Diagnostic) -> None:
        self.diagnostic = diagnostic
        super().__init__(diagnostic.render())


# --- the code registry ----------------------------------------------------
#
# Prefix per stage: D detect, X extract, B build, C cluster, R derive,
# G geometry (layout), L lock. Numbers are contiguous within a prefix, because
# a gap is indistinguishable from a code that was removed without telling
# anyone consuming it.

CODES: dict[str, str] = {
    # detect
    "SVA-D-001": "scan root is missing or not a directory",
    "SVA-D-002": "file exceeded the size cap and was skipped",
    "SVA-D-003": "scan hit the file cap and stopped early",
    "SVA-D-004": "symlink loop or a link pointing outside the root",
    "SVA-D-005": "an ignore pattern would not compile and was dropped",
    "SVA-D-006": "a manifest declaring a workspace could not be parsed",
    # extract
    "SVA-X-001": "a file could not be parsed by its language grammar",
    "SVA-X-002": "a config file could not be read",
    # build
    "SVA-B-001": "an element reached the graph without evidence",
    "SVA-B-002": "evidence cites a file that is not in the scan",
    "SVA-B-003": "evidence cites lines that do not exist in the file",
    "SVA-B-004": "an edge endpoint is not a node in the graph",
    "SVA-B-005": "candidate arity exceeded the cap and was truncated",
    "SVA-B-006": "a workspace member glob matched nothing",
    "SVA-B-007": "a go.work use directive pointed outside the repository",
    # cluster
    "SVA-C-001": "unknown clustering backend",
    "SVA-C-002": "the requested clustering backend is not installed",
    "SVA-C-003": "an oversized or low-cohesion community was re-split",
    "SVA-C-004": "a community could not be split usefully",
    # derive
    "SVA-R-001": "a diagram is unavailable, with the reason",
    "SVA-R-002": "a spill group was stranded or a spec was merged",
    "SVA-R-003": "groups exceeded the top-box budget and were merged",
    "SVA-R-004": "modules or edges were dropped by a cap",
    "SVA-R-005": "the diagram set is not navigable",
    # geometry / layout
    "SVA-G-001": "boxes overlap",
    "SVA-G-002": "a box falls outside the canvas",
    "SVA-G-003": "a label does not fit its box",
    "SVA-G-004": "a route endpoint is not a box on this canvas",
    "SVA-G-005": "a route polyline is degenerate or leaves the canvas",
    "SVA-G-006": "a band does not contain the boxes it claims",
    "SVA-G-007": "duplicate box ids on one canvas",
    "SVA-G-008": "a label was not sanitized before layout",
    # lock
    "SVA-L-001": "a record kind violates the published grammar",
    "SVA-L-002": "a record has the wrong number of fields for its kind",
    "SVA-L-003": "an unknown escape sequence in a record field",
    "SVA-L-004": "a normalization or case collision between ids",
}
