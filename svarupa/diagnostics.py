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
    "CODES",
    "DYNAMIC_MESSAGE_CODES",
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
    "SVA-D-001": "a symlink points outside the repository and was not followed",
    "SVA-D-002": "a file exceeded the size cap and was skipped",
    "SVA-D-003": "the scan hit the file cap, so results are partial",
    "SVA-D-004": "directory nesting exceeded the depth cap",
    "SVA-D-005": "a directory could not be read",
    "SVA-D-006": "an ignore pattern would not compile and was dropped",
    "SVA-D-007": "the scan root does not exist or is not a directory",
    # extract
    "SVA-X-001": "a file has syntax errors, so extraction over it is incomplete",
    "SVA-X-002": "a symbol is defined more than once in one file",
    "SVA-X-003": "a syntax tree nests deeper than the cap, so it was not fully walked",
    "SVA-X-004": "an extractor raised, so one file contributed no facts",
    # build
    "SVA-B-001": "two different nodes claim the same id",
    "SVA-B-002": "an evidence range is not a valid source location",
    "SVA-B-003": "one relationship was classified differently by different sites",
    "SVA-B-004": "an edge endpoint is not a node in the graph",
    "SVA-B-005": "evidence names a file that was never scanned",
    "SVA-B-006": "evidence points past the end of the file",
    "SVA-B-007": "an element has no evidence",
    "SVA-B-008": "a merged edge violated its own contract and was dropped",
    # cluster
    "SVA-C-001": "unknown clustering backend",
    "SVA-C-002": "the requested clustering backend is not installed",
    "SVA-C-003": "an oversized or low-cohesion community was re-split",
    "SVA-C-004": "a community could not be split usefully",
    # derive
    "SVA-R-001": "a diagram is unavailable, with the reason",
    "SVA-R-002": "type-only imports were excluded from a runtime view",
    "SVA-R-003": "groups exceeded the top-box budget and were merged",
    "SVA-R-004": "elements were omitted for lack of any extractable source",
    "SVA-R-005": "a view was withheld or is unreachable, so navigation is broken",
    # geometry / layout
    "SVA-G-001": "boxes overlap",
    "SVA-G-002": "a box falls outside the canvas",
    "SVA-G-003": "a label does not fit its box",
    "SVA-G-004": "a route endpoint is not a box on this canvas",
    "SVA-G-005": "a route polyline is degenerate or leaves the canvas",
    "SVA-G-006": "a band does not contain the boxes it claims",
    "SVA-G-007": "duplicate box ids on one canvas",
    "SVA-G-008": "a label was not sanitized before layout",
    "SVA-G-009": "a box or route carries no evidence",
    "SVA-G-010": "a coordinate is not an int",
    "SVA-G-011": "a route passes through the interior of a box",
    # emit
    "SVA-E-001": "the output directory holds files this tool does not own",
    # lock
    "SVA-L-001": "a record kind violates the published grammar",
    "SVA-L-002": "a record has the wrong number of fields for its kind",
    "SVA-L-003": "an unknown escape sequence in a record field",
    "SVA-L-004": "a normalization or case collision between ids",
}

# Codes whose message text comes from the caller, so there is no literal at the
# emission site for a drift check to compare a description against. Listed by
# name rather than inferred, so a *new* code with no literal message fails the
# check instead of being exempted silently.
DYNAMIC_MESSAGE_CODES = frozenset({"SVA-R-001"})
