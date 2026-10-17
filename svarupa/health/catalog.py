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
    Check("long-function", "Long function", 50, "Fowler, Refactoring: Long Method", "maintainability", "minor", 10, maturity="stable"),
    Check("many-parameters", "Too many parameters", 5, "pylint max-args default", "maintainability", "minor", 5, maturity="stable"),
    Check("deep-nesting", "Deep nesting", 4, "ESLint max-depth default", "maintainability", "minor", 10),
    Check("large-file", "Large file", 1000, "pylint max-module-lines default", "maintainability", "minor", 30),
    Check("large-class", "Large class", 47, "Lanza and Marinescu, WMC", "maintainability", "major", 60),
    Check("duplicated-block", "Duplicated block", 9, "SonarQube CPD default (10 lines)", "maintainability", "major", 15),
    Check("module-cycle", "Module import cycle", 0, "Martin, Acyclic Dependencies Principle", "maintainability", "major", 60, maturity="stable"),
    Check("hub-module", "Hub module", 9, "Arcan hub-like dependency", "maintainability", "major", 60),
    Check("layer-direction", "Layer imported the wrong way", 0, "the unit's design style (docs/design.md)", "maintainability", "major", 30),
    Check("part-independence", "Independent parts import each other", 0, "the unit's design style (docs/design.md)", "maintainability", "major", 30),
    Check("public-entry", "Part imported past its public entry", 0, "the unit's design style (docs/design.md)", "maintainability", "minor", 10),
    Check("core-purity", "Core uses frameworks or I/O", 0, "the unit's design style (docs/design.md)", "maintainability", "major", 60),
    Check("hardcoded-secret", "Secret committed in the repository", 0, "CWE-798; OWASP A07:2021; gitleaks default rules", "security", "blocker", 30, maturity="stable"),
    Check("sql-injection", "SQL built from strings", 0, "CWE-89; OWASP A03:2021", "security", "critical", 30),
    Check("command-injection", "Shell command built from strings", 0, "CWE-78; OWASP A03:2021", "security", "critical", 30),
    Check("code-injection", "Code built from strings and run", 0, "CWE-94; OWASP A03:2021", "security", "critical", 30),
    Check("tls-verify-off", "TLS certificate check turned off", 0, "CWE-295; OWASP A02:2021", "security", "critical", 10),
    Check("unsafe-deserialize", "Unsafe deserialization", 0, "CWE-502; OWASP A08:2021", "security", "critical", 30),
    Check("weak-hash", "Weak hash (MD5 or SHA-1)", 0, "CWE-327; OWASP A02:2021", "security", "major", 10),
    Check("debug-enabled", "Debug mode turned on", 0, "CWE-489; OWASP A05:2021", "security", "major", 5),
    Check("cors-any-origin", "CORS allows every origin", 0, "CWE-942; OWASP A05:2021", "security", "major", 10, maturity="stable"),
    Check("endpoint-without-auth", "Write endpoint without auth", 0, "CWE-306; OWASP A01:2021", "security", "major", 20),
    Check("shared-datastore", "Datastore shared by services", 1, "Newman, Building Microservices: database per service", "performance", "major", 240),
    Check("query-in-loop", "Query in a loop (N+1)", 0, "N+1 query anti-pattern; Fowler, PoEAA", "performance", "major", 20),
    Check("blocking-in-async", "Blocking call in async code", 0, "Python asyncio docs: running blocking code; Node.js: Don't block the event loop", "performance", "major", 15),
    Check("hub-on-request-path", "Hub module on every request path", 2, "Arcan hub-like dependency on the request path", "performance", "major", 60),
    Check("no-timeout", "Outbound call without a timeout", 0, "CWE-1088; Nygard, Release It!: Timeouts", "reliability", "major", 10),
    Check("swallowed-error", "Error caught and ignored", 0, "CWE-390; Bandit B110", "reliability", "major", 10),
    Check("no-retry", "Connection without retry", 0, "Nygard, Release It!: retries", "reliability", "minor", 15),
)  # fmt: skip

BY_ID = {c.id: c for c in CATALOG}
