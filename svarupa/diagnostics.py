"""Structured diagnostics.

Every failure produces one of these, never a bare traceback. Codes are stable
so an agent can consume them and act on ``suggested_fixes`` rather than parsing
prose. See design 11.

Code prefixes:
  SVA-D-*  detect
  SVA-X-*  extract
  SVA-B-*  build
  SVA-L-*  lock
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


# --- lock codes -----------------------------------------------------------

SVA_L_001_BAD_KIND = "SVA-L-001"
SVA_L_002_BAD_ARITY = "SVA-L-002"
SVA_L_003_UNKNOWN_ESCAPE = "SVA-L-004"
SVA_L_004_NO_SCHEMA_STAMP = "SVA-L-005"
SVA_L_005_SCHEMA_MAJOR = "SVA-L-006"
SVA_L_006_ID_COLLISION = "SVA-L-007"
