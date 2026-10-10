"""The check catalog: what is measured, the limit, and where the limit comes from.

Thresholds are published defaults of the tools and papers named in
`source`, so a grade means the same thing in every repository. Remediation
minutes are this project's documented estimates (docs/health.md) and feed
the SQALE technical debt ratio.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["AREAS", "BY_ID", "CATALOG", "SEVERITIES", "Check"]

# ISO/IEC 25010 quality characteristics the grades are reported for.
AREAS = ("maintainability", "reliability", "security", "performance")
SEVERITIES = ("info", "minor", "major", "critical", "blocker")


@dataclass(frozen=True, slots=True)
class Check:
    id: str
    title: str
    threshold: int  # violated when the measured value is strictly greater
    source: str
    area: str
    severity: str
    minutes: int  # remediation estimate
    per_unit: int = 0  # extra minutes per unit over the threshold
    maturity: str = "experimental"


CATALOG: tuple[Check, ...] = (
    Check("complex-function", "Complex function", 10, "McCabe 1976; NIST SP 500-235", "maintainability", "major", 10, 1),
    Check("long-function", "Long function", 50, "Fowler, Refactoring: Long Method", "maintainability", "minor", 10),
    Check("many-parameters", "Too many parameters", 5, "pylint max-args default", "maintainability", "minor", 5),
    Check("deep-nesting", "Deep nesting", 4, "ESLint max-depth default", "maintainability", "minor", 10),
    Check("large-file", "Large file", 1000, "pylint max-module-lines default", "maintainability", "minor", 30),
    Check("large-class", "Large class", 47, "Lanza and Marinescu, WMC", "maintainability", "major", 60),
    Check("duplicated-block", "Duplicated block", 9, "SonarQube CPD default (10 lines)", "maintainability", "major", 15),
    Check("module-cycle", "Module import cycle", 0, "Martin, Acyclic Dependencies Principle", "maintainability", "major", 60),
    Check("hub-module", "Hub module", 9, "Arcan hub-like dependency", "maintainability", "major", 60),
)  # fmt: skip

BY_ID = {c.id: c for c in CATALOG}
