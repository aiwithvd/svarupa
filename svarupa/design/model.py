"""Design styles and what detection found."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from svarupa.model import Evidence

if TYPE_CHECKING:
    from svarupa.design.file import DesignException

__all__ = [
    "Assignment",
    "Design",
    "DesignViolation",
    "Fit",
    "Part",
    "Style",
    "Unit",
    "UnitDesign",
]


@dataclass(frozen=True, slots=True)
class Part:
    id: str
    names: frozenset[str]  # directory names, lowercase
    evidence: frozenset[str] = frozenset()  # routes, datastore, messagebus, cloud, ui
    slices: bool = False  # each directory under a matching name is its own slice


@dataclass(frozen=True, slots=True)
class Style:
    id: str
    name: str
    level: str  # backend | frontend | mobile | data | library
    parts: tuple[Part, ...]
    order: tuple[str, ...] = ()  # top to bottom; dependencies point down
    independent: tuple[str, ...] = ()  # parts whose slices must not import each other
    public_entry: tuple[str, ...] = ()  # parts imported only through a slice's root
    pure: tuple[str, ...] = ()  # parts that must not use frameworks or I/O
    forbidden: tuple[tuple[str, str], ...] = ()  # (from part, to part) never allowed
    source: str = ""
    maturity: str = "experimental"


@dataclass(frozen=True, slots=True)
class Unit:
    id: str  # directory of the manifest, "" for the repository root
    level: str
    manifest: str  # the manifest file, "" when none
    modules: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Assignment:
    module: str
    part: str
    slice: str | None
    reason: str
    by_name: bool = True  # False when only evidence (routes, datastore...) placed it


@dataclass(frozen=True, slots=True)
class DesignViolation:
    rule: str  # layer-direction | part-independence | public-entry | core-purity
    src: str  # module
    dst: str  # module ("" for core-purity)
    evidence: Evidence
    message: str


def _violation_json(v: DesignViolation) -> dict[str, object]:
    return {
        "rule": v.rule,
        "from": v.src,
        "to": v.dst,
        "file": v.evidence.file,
        "line": v.evidence.start_line,
        "message": v.message,
    }


@dataclass(frozen=True, slots=True)
class Fit:
    unit: str
    style: str
    coverage: float
    compliance: float
    assignments: tuple[Assignment, ...]
    violations: tuple[DesignViolation, ...]
    # Violations the team accepted in design.yaml: listed, not counted.
    excepted: tuple[DesignViolation, ...] = ()

    @property
    def fit(self) -> float:
        return self.coverage * self.compliance

    def to_json(self) -> dict[str, object]:
        return {
            "style": self.style,
            "fit": round(self.fit, 4),
            "coverage": round(self.coverage, 4),
            "compliance": round(self.compliance, 4),
            "parts": [
                {"module": a.module, "part": a.part, "slice": a.slice, "reason": a.reason}
                for a in self.assignments
            ],
            "violations": [_violation_json(v) for v in self.violations],
            "excepted": [_violation_json(v) for v in self.excepted],
        }


def conformance(fit: Fit) -> str:
    """Conformance as a reader should see it: an accepted design that matches
    no current module checks nothing, which is not 100%."""
    return "nothing to check" if fit.coverage == 0.0 else f"conformance {fit.compliance:.0%}"


@dataclass(frozen=True, slots=True)
class UnitDesign:
    unit: Unit
    chosen: Fit | None
    source: str  # accepted | inferred | none
    runners_up: tuple[Fit, ...]
    note: str = ""
    # For a unit without a clear design: what would bring it to the closest style.
    moves: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Design:
    system: tuple[str, ...]
    system_evidence: tuple[str, ...]
    units: tuple[UnitDesign, ...]
    exceptions: tuple[DesignException, ...] = ()

    def to_json(self) -> dict[str, object]:
        return {
            "exceptions": [
                {"rule": e.rule, "from": e.src, "to": e.dst, "reason": e.reason}
                for e in self.exceptions
            ],
            "system": list(self.system),
            "system_evidence": list(self.system_evidence),
            "units": [
                {
                    "unit": u.unit.id or ".",
                    "level": u.unit.level,
                    "manifest": u.unit.manifest,
                    "source": u.source,
                    "note": u.note,
                    "moves": list(u.moves),
                    "chosen": u.chosen.to_json() if u.chosen else None,
                    "runners_up": [
                        {"style": f.style, "fit": round(f.fit, 4)} for f in u.runners_up
                    ],
                }
                for u in self.units
            ],
        }
