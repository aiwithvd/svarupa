"""Violations and the health result."""

from __future__ import annotations

from dataclasses import dataclass

from svarupa.model import Evidence

__all__ = ["Health", "Violation"]


@dataclass(frozen=True, slots=True)
class Violation:
    check: str
    message: str
    evidence: tuple[Evidence, ...]  # never empty
    module: str
    value: int  # the measured value
    minutes: int  # remediation estimate
    impact: int = 0  # fan-in, plus 5 when the module serves a request path
    symbol: str | None = None  # graph node id when it is one definition

    def to_json(self) -> dict[str, object]:
        return {
            "check": self.check,
            "message": self.message,
            "evidence": [
                {"file": e.file, "start_line": e.start_line, "end_line": e.end_line}
                for e in self.evidence
            ],
            "module": self.module,
            "value": self.value,
            "minutes": self.minutes,
            "impact": self.impact,
            "symbol": self.symbol,
        }


@dataclass(frozen=True, slots=True)
class Health:
    ncloc: int
    debt_minutes: int
    debt_ratio: float
    ratings: tuple[tuple[str, str | None], ...]  # (area, A..E or None)
    violations: tuple[Violation, ...]  # highest priority first

    def rating(self, area: str) -> str | None:
        return dict(self.ratings)[area]

    def to_json(self) -> dict[str, object]:
        from svarupa.health.catalog import CATALOG

        return {
            "model": "SQALE",
            "ncloc": self.ncloc,
            "debt_minutes": self.debt_minutes,
            "debt_ratio": round(self.debt_ratio, 4),
            "ratings": dict(self.ratings),
            "violations": [v.to_json() for v in self.violations],
            "checks": [
                {
                    "id": c.id,
                    "title": c.title,
                    "threshold": c.threshold,
                    "source": c.source,
                    "area": c.area,
                    "severity": c.severity,
                    "minutes": c.minutes,
                    "per_unit": c.per_unit,
                    "maturity": c.maturity,
                }
                for c in CATALOG
            ],
        }
