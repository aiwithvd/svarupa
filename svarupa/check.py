"""`svarupa check`: the health findings a change added, the ones it fixed,
and every grade that moved.

A finding is matched by what it is, not where it is: the same check in the
same file on the same definition (or, with no definition, the same message
with its numbers blanked) is the same finding, so moving code never makes
it new. Matching is a multiset: a second copy of an old finding is new.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from svarupa.health import BY_ID, Health
from svarupa.health.catalog import SEVERITIES

__all__ = [
    "CheckError",
    "Delta",
    "Finding",
    "Snapshot",
    "analyze",
    "base_from_ref",
    "compare",
    "snapshot",
    "snapshot_from_json",
]

_GRADES = ("A", "B", "C", "D", "E")
_DIGITS = re.compile(r"\d+")


class CheckError(Exception):
    """A base that cannot be built or read."""


@dataclass(frozen=True, slots=True)
class Finding:
    check: str
    file: str
    line: int
    symbol: str | None
    message: str

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.check, self.file, self.symbol or _DIGITS.sub("#", self.message))

    def to_json(self) -> dict[str, object]:
        return {
            "check": self.check,
            "file": self.file,
            "line": self.line,
            "symbol": self.symbol,
            "message": self.message,
        }


@dataclass(frozen=True, slots=True)
class Snapshot:
    findings: tuple[Finding, ...]
    ratings: tuple[tuple[str, str | None], ...]


def snapshot(health: Health) -> Snapshot:
    return Snapshot(
        tuple(
            Finding(v.check, v.evidence[0].file, v.evidence[0].start_line, v.symbol, v.message)
            for v in health.violations
        ),
        health.ratings,
    )


def snapshot_from_json(data: Mapping[str, Any]) -> Snapshot:
    """The health in an artifact's graph.json."""
    raw = data.get("health")
    if raw is None:
        raise CheckError("the base graph.json has no health; rebuild it with this svarupa")
    if not isinstance(raw, dict):
        raise CheckError("the base graph.json health is malformed: it is not an object")
    health = cast("dict[str, Any]", raw)
    try:
        return _parse_health(health)
    except (KeyError, AttributeError, ValueError, TypeError) as exc:
        raise CheckError(f"the base graph.json health is malformed: {exc!r}") from exc


def _parse_health(health: dict[str, Any]) -> Snapshot:
    found: list[Finding] = []
    for v in cast("list[dict[str, Any]]", health.get("violations", [])):
        ev = cast("list[dict[str, Any]]", v.get("evidence") or [{}])[0]
        found.append(
            Finding(
                str(v["check"]),
                str(ev.get("file", "")),
                int(ev.get("start_line", 0)),
                cast("str | None", v.get("symbol")),
                str(v.get("message", "")),
            )
        )
    ratings = cast("dict[str, str | None]", health.get("ratings", {}))
    for area, grade in ratings.items():
        if grade is not None and grade not in _GRADES:
            raise ValueError(f"unknown grade {grade!r} for {area}")
    return Snapshot(tuple(found), tuple(ratings.items()))


@dataclass(frozen=True, slots=True)
class Delta:
    new: tuple[Finding, ...]
    fixed: tuple[Finding, ...]
    grades: tuple[tuple[str, str | None, str | None], ...]  # (area, base, head), changed only

    def worse(self) -> list[str]:
        return [
            area
            for area, before, after in self.grades
            if before is not None
            and after is not None
            and _GRADES.index(after) > _GRADES.index(before)
        ]

    def failed(self, min_severity: str) -> bool:
        floor = SEVERITIES.index(min_severity)
        return bool(self.worse()) or any(
            SEVERITIES.index(BY_ID[f.check].severity) >= floor
            for f in self.new
            if f.check in BY_ID
        )

    def to_json(self, failed: bool) -> dict[str, object]:
        return {
            "new": [f.to_json() for f in self.new],
            "fixed": [f.to_json() for f in self.fixed],
            "grades": {area: [before, after] for area, before, after in self.grades},
            "failed": failed,
        }

    def render(self) -> str:
        from svarupa.health.explain import explanation

        lines = [f"{len(self.new)} new, {len(self.fixed)} fixed"]
        if self.new:
            lines.append("new:")
            for f in self.new:
                lines.append(f"  {f.file}:{f.line} {f.check}: {f.message}")
                if f.check in BY_ID:
                    lines.append(f"    fix: {explanation(f.check).fix}")
        if self.fixed:
            lines.append("fixed:")
            lines += [f"  {f.file}:{f.line} {f.check}: {f.message}" for f in self.fixed]
        if self.grades:
            lines.append("grades:")
            lines += [
                f"  {area} {before or 'not assessed'} -> {after or 'not assessed'}"
                for area, before, after in self.grades
            ]
        return "\n".join(lines)


def _unmatched(items: tuple[Finding, ...], other: tuple[Finding, ...]) -> tuple[Finding, ...]:
    left = Counter(f.key for f in other)
    out: list[Finding] = []
    for f in items:
        if left[f.key] > 0:
            left[f.key] -= 1
        else:
            out.append(f)
    return tuple(out)


def compare(base: Snapshot, head: Snapshot) -> Delta:
    before, after = dict(base.ratings), dict(head.ratings)
    areas = [a for a, _ in head.ratings] + [a for a in before if a not in after]
    return Delta(
        new=_unmatched(head.findings, base.findings),
        fixed=_unmatched(base.findings, head.findings),
        grades=tuple(
            (a, before.get(a), after.get(a)) for a in areas if before.get(a) != after.get(a)
        ),
    )


def analyze(root: Path, rules_root: Path) -> Health:
    """The health of the tree at `root`, judged by the rules (design.yaml,
    exceptions, auth markers) at `rules_root`. Writes nothing."""
    from svarupa.build import build
    from svarupa.design import design_for
    from svarupa.design.file import DESIGN_FILE, load_design
    from svarupa.detect import detect
    from svarupa.extract import declared_dependencies, extract
    from svarupa.health import assess, secret_texts, source_texts

    scan = detect(str(root))
    graph = build(scan, extract(scan, declared_dependencies(scan)), strict=False)
    accepted, _ = load_design(rules_root / DESIGN_FILE)
    design = design_for(scan, graph, accepted)
    return assess(
        graph,
        source_texts(scan, graph),
        design,
        design.exceptions,
        secrets=secret_texts(scan),
        auth_markers=tuple(accepted.auth_markers) if accepted else (),
    )


def _git(cwd: Path, *args: str) -> str:
    git = shutil.which("git")
    if git is None:
        raise CheckError("git is not installed; pass an artifact's graph.json as --base")
    proc = subprocess.run([git, "-C", str(cwd), *args], capture_output=True, text=True)
    if proc.returncode:
        raise CheckError(proc.stderr.strip() or f"git {args[0]} failed")
    return proc.stdout.strip()


def base_from_ref(root: Path, ref: str) -> Health:
    """The health of `root` at `ref`, built in a temporary worktree that is
    always removed. The rules are the working tree's: today's rules judge
    both sides."""
    root = root.resolve()
    try:
        top = Path(_git(root, "rev-parse", "--show-toplevel")).resolve()
    except CheckError as exc:
        raise CheckError(f"{root} is not inside a git checkout ({exc})") from exc
    try:
        _git(top, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
    except CheckError as exc:
        raise CheckError(f"unknown ref {ref!r}") from exc
    tmp = Path(tempfile.mkdtemp(prefix="svarupa-check-"))
    tree = tmp / "base"
    try:
        _git(top, "worktree", "add", "--detach", "--quiet", str(tree), ref)
        return analyze(tree / root.relative_to(top), root)
    finally:
        git = shutil.which("git")
        if git is not None:
            subprocess.run(
                [git, "-C", str(top), "worktree", "remove", "--force", str(tree)],
                capture_output=True,
            )
            subprocess.run([git, "-C", str(top), "worktree", "prune"], capture_output=True)
        shutil.rmtree(tmp, ignore_errors=True)
