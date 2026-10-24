# Agent Rules and Check My Change (1d) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An AI agent reads the design and health rules before it writes code and proves afterwards, with `svarupa check`, that its change added no violation and lowered no grade.

**Architecture:** `get_design_rules` gains a `checks` list built from the catalog and the 1c-4 explanations. A new module `svarupa/check.py` analyzes a tree in memory (the same steps `_scan` runs, minus output), snapshots the health result, and compares two snapshots with a line-shift-proof key. The CLI's `svarupa check` builds the base from a git ref in a temporary worktree or reads a `graph.json`. The skill gains the before/after workflow.

**Tech Stack:** Python 3.10+, git CLI (`worktree add --detach`), argparse, pytest.

**Spec:** `docs/superpowers/specs/2026-10-24-agent-rules-1d-design.md`

## Global Constraints

- Exit codes: 3 new violation at or above `--min-severity` (default `minor`) or any area grade worse; 0 otherwise; 1 errors (unknown ref, no git, not a git checkout with a ref base, unreadable or health-less base file); 2 usage.
- Match key: `(check, file, symbol)`, or `(check, file, message with digit runs replaced by "#")` when there is no symbol; multiset comparison.
- The base uses the head's `.svarupa/design.yaml`.
- The worktree is always removed (`finally`).
- Nothing is written into the analyzed repository by `svarupa check`.
- Plain English, no em dashes. Commits SSH-signed, imperative, under 72 characters, no AI attribution.

## Review Focus

1. `--base HEAD` with no edits reports nothing new and exits 0. Pinned in Task 3 (`test_check_with_no_change_passes`).
2. A repository path that is a subfolder of the git checkout analyzes the same subfolder in the base. Pinned in Task 3 (`test_check_in_a_subfolder_of_the_checkout`).
3. A failed base run leaves no worktree behind. Pinned in Task 3 (`test_unknown_ref_exits_1_and_leaves_no_worktree`).
4. A grade that goes from "not assessed" to a letter is not a drop. Pinned in Task 2 (`test_a_newly_graded_area_is_not_worse`).
5. An old `graph.json` without `health` fails clearly. Pinned in Task 3 (`test_a_base_without_health_exits_1`).

---

### Task 1: Health rules in `get_design_rules`

**Files:**
- Modify: `svarupa/query/__init__.py` (`get_design_rules`)
- Test: `tests/test_design_outputs.py`

**Interfaces:**
- Produces: `get_design_rules(index)["checks"]: list[{"id", "rule", "area", "severity", "limit", "fix"}]`, present with or without a design.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_design_outputs.py`)

```python
def test_design_rules_carry_the_health_rules(tmp_path: Path, capsys) -> None:
    from svarupa.health import CATALOG

    layered(tmp_path)
    assert main([str(tmp_path)]) == 0
    capsys.readouterr()
    assert main(["query", str(tmp_path / ".svarupa"), "get_design_rules", "--json"]) == 0
    rules = json.loads(capsys.readouterr().out)
    assert [c["id"] for c in rules["checks"]] == [c.id for c in CATALOG]
    q = {c["id"]: c for c in rules["checks"]}["query-in-loop"]
    assert (q["rule"], q["area"], q["severity"], q["limit"]) == (
        "Query in a loop (N+1)",
        "performance",
        "major",
        0,
    )
    assert q["fix"].startswith("Load everything the loop needs")


def test_health_rules_without_a_design(tmp_path: Path) -> None:
    from svarupa.query import GraphIndex, get_design_rules

    (tmp_path / "graph.json").write_text(
        json.dumps({"schema": 2, "nodes": [], "edges": []}), encoding="utf8"
    )
    rules = get_design_rules(GraphIndex.load(tmp_path))
    assert rules["available"] is False and len(rules["checks"]) == 30
```

(If `GraphIndex` is built differently than `GraphIndex.load(dir)`, use the constructor the query CLI uses in `svarupa/query/cli.py` and ledger it.)

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_design_outputs.py -q -k "health_rules"`
Expected: FAIL with `KeyError: 'checks'`.

- [ ] **Step 3: Implement** (`svarupa/query/__init__.py`)

Add above `get_design_rules`:

```python
def _health_rules() -> list[dict[str, Any]]:
    """Every check as a rule an agent follows: what it is, the limit, the fix."""
    from svarupa.health import CATALOG
    from svarupa.health.explain import explanation

    return [
        {
            "id": c.id,
            "rule": c.title,
            "area": c.area,
            "severity": c.severity,
            "limit": c.threshold,
            "fix": explanation(c.id).fix,
        }
        for c in CATALOG
    ]
```

In `get_design_rules`, change the no-design return to:

```python
        return {
            "available": False,
            "reason": "this artifact was built without design",
            "checks": _health_rules(),
        }
```

and the final return to:

```python
    return {
        "available": True,
        "units": units,
        "exceptions": design.get("exceptions", []),
        "checks": _health_rules(),
    }
```

Update the docstring: `"""What an agent must follow: per unit, parts, allowed and forbidden directions, and every health rule."""`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_design_outputs.py tests/test_graph_query.py -q`
Expected: PASS.

- [ ] **Step 5: Lint, commit**

```bash
uv run ruff format svarupa tests && uv run ruff check svarupa tests && uv run pyright svarupa
git add svarupa/query/__init__.py tests/test_design_outputs.py
git commit -m "Give agents the health rules with the design rules"
```

---

### Task 2: Snapshots and the comparison

**Files:**
- Create: `svarupa/check.py`
- Test: `tests/test_check.py`

**Interfaces:**
- Produces:
  - `CheckError(Exception)`.
  - `Finding(check, file, line, symbol, message)` with `.key -> tuple[str, str, str]` and `.to_json()`.
  - `Snapshot(findings: tuple[Finding, ...], ratings: tuple[tuple[str, str | None], ...])`.
  - `snapshot(health: Health) -> Snapshot`; `snapshot_from_json(data: Mapping[str, Any]) -> Snapshot` (raises `CheckError` without `health`).
  - `Delta(new, fixed, grades: tuple[tuple[str, str | None, str | None], ...])` with `.worse() -> list[str]`, `.failed(min_severity: str) -> bool`, `.to_json(failed: bool) -> dict`, `.render() -> str`.
  - `compare(base: Snapshot, head: Snapshot) -> Delta`.

- [ ] **Step 1: Write the failing tests** (create `tests/test_check.py`)

```python
"""svarupa check: what a change added, matched so line shifts never count."""

from __future__ import annotations

from svarupa.check import Finding, Snapshot, compare

RATINGS = (("maintainability", "A"), ("reliability", "A"), ("security", "A"), ("performance", "A"))


def f(check: str = "many-parameters", line: int = 1, symbol: str | None = "a.py#m.f", message: str = "f has 6 parameters (limit 5)", file: str = "a.py") -> Finding:
    return Finding(check, file, line, symbol, message)


def snap(*found: Finding, ratings=RATINGS) -> Snapshot:
    return Snapshot(tuple(found), ratings)


def test_a_moved_finding_is_not_new() -> None:
    d = compare(snap(f(line=1)), snap(f(line=40)))
    assert d.new == () and d.fixed == ()


def test_a_second_copy_is_new_and_a_removed_one_is_fixed() -> None:
    d = compare(snap(f()), snap(f(), f(line=9)))
    assert [x.line for x in d.new] == [9]
    d = compare(snap(f(), f(check="deep-nesting")), snap(f()))
    assert [x.check for x in d.fixed] == ["deep-nesting"]


def test_digits_in_messages_do_not_break_matching() -> None:
    base = f(check="large-file", symbol=None, message="a.py has 1200 lines (limit 1000)")
    head = f(check="large-file", symbol=None, message="a.py has 1300 lines (limit 1000)")
    assert compare(snap(base), snap(head)).new == ()


def test_a_grade_drop_fails_alone() -> None:
    worse = (("maintainability", "A"), ("reliability", "A"), ("security", "C"), ("performance", "A"))
    d = compare(snap(), snap(ratings=worse))
    assert d.new == () and d.worse() == ["security"] and d.failed("minor")
    assert d.grades == (("security", "A", "C"),)


def test_a_newly_graded_area_is_not_worse() -> None:
    before = (("maintainability", "A"), ("reliability", None), ("security", "A"), ("performance", None))
    d = compare(snap(ratings=before), snap())
    assert d.worse() == [] and not d.failed("minor")


def test_min_severity_ignores_small_new_findings() -> None:
    d = compare(snap(), snap(f()))  # many-parameters is minor
    assert d.failed("minor") and not d.failed("major")


def test_json_and_text_say_the_same() -> None:
    d = compare(snap(f(check="deep-nesting")), snap(f()))
    j = d.to_json(d.failed("minor"))
    assert j["failed"] is True and j["new"][0]["check"] == "many-parameters"
    assert j["fixed"][0]["check"] == "deep-nesting"
    text = d.render()
    assert text.startswith("1 new, 1 fixed")
    assert "a.py:1 many-parameters: f has 6 parameters (limit 5)" in text
    assert "fix: Group the values that travel together" in text
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_check.py -q`
Expected: FAIL at import (`svarupa.check` does not exist).

- [ ] **Step 3: Write `svarupa/check.py`**

```python
"""`svarupa check`: the health findings a change added, the ones it fixed,
and every grade that moved.

A finding is matched by what it is, not where it is: the same check in the
same file on the same definition (or, with no definition, the same message
with its numbers blanked) is the same finding, so moving code never makes
it new. Matching is a multiset: a second copy of an old finding is new.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, cast

from svarupa.health import BY_ID, Health
from svarupa.health.catalog import SEVERITIES

__all__ = ["CheckError", "Delta", "Finding", "Snapshot", "compare", "snapshot", "snapshot_from_json"]

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
    if not isinstance(raw, dict):
        raise CheckError("the base graph.json has no health; rebuild it with this svarupa")
    health = cast("dict[str, Any]", raw)
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
            if before is not None and after is not None and _GRADES.index(after) > _GRADES.index(before)
        ]

    def failed(self, min_severity: str) -> bool:
        floor = SEVERITIES.index(min_severity)
        return bool(self.worse()) or any(
            SEVERITIES.index(BY_ID[f.check].severity) >= floor for f in self.new if f.check in BY_ID
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_check.py -q`
Expected: PASS.

- [ ] **Step 5: Lint, commit**

```bash
uv run ruff format svarupa tests && uv run ruff check svarupa tests && uv run pyright svarupa
git add svarupa/check.py tests/test_check.py
git commit -m "Compare health snapshots without counting moved code as new"
```

(If the import reachability test fails because nothing imports `svarupa.check` yet, record Task 2 after Task 3 wires it into the CLI, and ledger it.)

---

### Task 3: `svarupa check` on a real tree

**Files:**
- Modify: `svarupa/check.py` (`analyze`, `base_from_ref`)
- Modify: `svarupa/cli.py` (dispatch, `_check`, epilog)
- Test: `tests/test_check.py`

**Interfaces:**
- Consumes: Task 2's `snapshot`, `snapshot_from_json`, `compare`, `CheckError`.
- Produces: `analyze(root: Path, rules_root: Path) -> Health`; `base_from_ref(root: Path, ref: str) -> Health`; CLI `svarupa check <path> --base <ref|graph.json> [--min-severity S] [--json]`.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_check.py`)

```python
import json  # noqa: E402
import shutil  # noqa: E402
import subprocess  # noqa: E402
from pathlib import Path  # noqa: E402

import pytest  # noqa: E402

from svarupa.cli import main  # noqa: E402
from tests.test_health import write  # noqa: E402

CLEAN = "def ok(a):\n    return a\n"
SIX = "def six(a, b, c, d, e, f):\n    return a\n"


def git_repo(root: Path) -> None:
    if not shutil.which("git"):
        pytest.skip("git is not installed")
    def git(*args: str) -> None:
        subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)
    git("init", "-q")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "t")
    git("config", "commit.gpgsign", "false")
    write(root, "app/m.py", CLEAN)
    git("add", ".")
    git("commit", "-q", "-m", "base")


def worktrees(root: Path) -> int:
    out = subprocess.run(["git", "-C", str(root), "worktree", "list"], capture_output=True, text=True, check=True)
    return len(out.stdout.strip().splitlines())


def test_check_against_a_graph_json(tmp_path: Path, capsys) -> None:
    write(tmp_path, "app/m.py", CLEAN)
    assert main([str(tmp_path)]) == 0
    base = tmp_path / "base.json"
    shutil.copy(tmp_path / ".svarupa" / "graph.json", base)
    write(tmp_path, "app/n.py", SIX)
    capsys.readouterr()
    assert main(["check", str(tmp_path), "--base", str(base), "--json"]) == 3
    out = json.loads(capsys.readouterr().out)
    assert [v["check"] for v in out["new"]] == ["many-parameters"] and out["failed"] is True


def test_check_against_a_git_ref(tmp_path: Path, capsys) -> None:
    git_repo(tmp_path)
    write(tmp_path, "app/m.py", CLEAN + "\n\n" + SIX)
    assert main(["check", str(tmp_path), "--base", "HEAD"]) == 3
    out = capsys.readouterr().out
    assert out.startswith("1 new, 0 fixed") and "fix: Group the values" in out
    assert worktrees(tmp_path) == 1


def test_check_with_no_change_passes(tmp_path: Path, capsys) -> None:
    git_repo(tmp_path)
    assert main(["check", str(tmp_path), "--base", "HEAD"]) == 0
    assert capsys.readouterr().out.startswith("0 new, 0 fixed")


def test_check_in_a_subfolder_of_the_checkout(tmp_path: Path, capsys) -> None:
    git_repo(tmp_path)
    write(tmp_path, "svc/m.py", SIX)
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-q", "-m", "svc"], check=True)
    assert main(["check", str(tmp_path / "app"), "--base", "HEAD", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["new"] == []


def test_unknown_ref_exits_1_and_leaves_no_worktree(tmp_path: Path, capsys) -> None:
    git_repo(tmp_path)
    assert main(["check", str(tmp_path), "--base", "no-such-ref"]) == 1
    assert "unknown ref" in capsys.readouterr().err
    assert worktrees(tmp_path) == 1


def test_a_base_without_health_exits_1(tmp_path: Path, capsys) -> None:
    write(tmp_path, "app/m.py", CLEAN)
    base = tmp_path / "old.json"
    base.write_text(json.dumps({"schema": 2, "nodes": []}), encoding="utf8")
    assert main(["check", str(tmp_path), "--base", str(base)]) == 1
    assert "no health" in capsys.readouterr().err


def test_an_excepted_violation_is_not_new(tmp_path: Path) -> None:
    git_repo(tmp_path)
    write(tmp_path, "app/n.py", SIX)
    write(
        tmp_path,
        ".svarupa/design.yaml",
        "units: {}\nexceptions:\n  - rule: many-parameters\n    at: app/n.py\n    reason: generated adapter\n",
    )
    assert main(["check", str(tmp_path), "--base", "HEAD"]) == 0


def test_min_severity_major_ignores_a_new_minor(tmp_path: Path) -> None:
    git_repo(tmp_path)
    write(tmp_path, "app/n.py", SIX)
    assert main(["check", str(tmp_path), "--base", "HEAD", "--min-severity", "major"]) == 0
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_check.py -q`
Expected: the new tests FAIL (`check` is treated as a repository path).

- [ ] **Step 3: Add `analyze` and `base_from_ref`** (append to `svarupa/check.py`)

```python
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
            subprocess.run([git, "-C", str(top), "worktree", "remove", "--force", str(tree)], capture_output=True)
            subprocess.run([git, "-C", str(top), "worktree", "prune"], capture_output=True)
        shutil.rmtree(tmp, ignore_errors=True)
```

Add to the imports: `import shutil`, `import subprocess`, `import tempfile`, `from pathlib import Path`; add `"analyze"` and `"base_from_ref"` to `__all__`.

- [ ] **Step 4: Add the command** (`svarupa/cli.py`)

In `main`, before `if argv[:1] == ["explain"]:`, add:

```python
        if argv[:1] == ["check"]:
            return _check(argv[1:])
```

Add `'"svarupa check <repo> --base main" lists the health violations a change added; '` to the epilog after the `svarupa explain` sentence, and add `"check"` to the sentence about directories named `setup`, `query` or `mcp` (`"setup", "query", "mcp", "check" or "explain"`).

Add before `def main(`:

```python
def _check(argv: list[str]) -> int:
    """The health violations a change added, against a base."""
    import json

    from svarupa.check import (
        CheckError,
        analyze,
        base_from_ref,
        compare,
        snapshot,
        snapshot_from_json,
    )

    parser = argparse.ArgumentParser(
        prog="svarupa check",
        description=(
            "List the health violations the working tree adds against a base, and any "
            f"grade that got worse. Exit {EXIT_CHANGED} when the change should not ship."
        ),
    )
    parser.add_argument("path", help="repository root; the working tree is used as it is")
    parser.add_argument(
        "--base", required=True, help="a git ref (main, HEAD~1) or an artifact's graph.json"
    )
    parser.add_argument(
        "--min-severity",
        default="minor",
        choices=["minor", "major", "critical", "blocker"],
        help="ignore new violations below this severity (grade drops always count)",
    )
    parser.add_argument("--json", action="store_true", help="print the result as JSON")
    args = parser.parse_args(argv)
    root = Path(args.path)
    try:
        if args.base.endswith(".json"):
            base = snapshot_from_json(json.loads(Path(args.base).read_text(encoding="utf8")))
        else:
            base = snapshot(base_from_ref(root, args.base))
        head = snapshot(analyze(root, root))
    except (CheckError, OSError, ValueError) as exc:
        print(f"svarupa check: {exc}", file=sys.stderr)
        return 1
    delta = compare(base, head)
    failed = delta.failed(args.min_severity)
    if args.json:
        print(json.dumps(delta.to_json(failed), indent=2, sort_keys=True))
    else:
        print(delta.render())
    return EXIT_CHANGED if failed else 0
```

(`EXIT_CHANGED` is the exit code `--fail-on-change` uses; it is 3. If it is named differently in `cli.py`, use that name.)

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_check.py -q`
Expected: PASS.

- [ ] **Step 6: Full suite, lint, commit**

```bash
uv run pytest -q -x 2>&1 | tail -3
uv run ruff format svarupa tests && uv run ruff check svarupa tests && uv run pyright svarupa
git add svarupa/check.py svarupa/cli.py tests/test_check.py
git commit -m "Add svarupa check: the violations a change adds"
```

---

### Task 4: The skill workflow, mutations and docs

**Files:**
- Modify: `svarupa/setup/skill.py` (`SKILL_MD`), `skills/svarupa/SKILL.md` (regenerated)
- Modify: `tests/test_setup.py` (`_real_flags` reads `check --help` and `explain --help`; tool count test)
- Modify: `scripts/mutate_packs.py`, `docs/health.md`

- [ ] **Step 1: Write the failing tests** (append to `tests/test_setup.py`)

```python
def test_the_skill_teaches_the_check_workflow() -> None:
    assert "## Before and after you write code" in SKILL_MD
    assert "svarupa check <path> --base HEAD --json" in SKILL_MD
    assert "get_design_rules --json" in SKILL_MD


def test_the_skill_names_the_real_number_of_mcp_tools() -> None:
    from svarupa.query import FUNCTIONS

    words = {7: "seven", 8: "eight", 9: "nine", 10: "ten", 11: "eleven", 12: "twelve"}
    assert f"serves the same {words[len(FUNCTIONS)]}" in SKILL_MD
```

In `_real_flags`, change the tuple to `(["--help"], ["setup", "--help"], ["query", "--help"], ["check", "--help"], ["explain", "--help"])`.

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_setup.py -q`
Expected: FAIL on the two new tests.

- [ ] **Step 3: Update `SKILL_MD`**

Replace `serves the same seven` with `serves the same nine`. After the "## Query the graph" section (before "## Architecture diff between commits"), insert:

```markdown
## Before and after you write code

When you change code in a repository Svarupa has analyzed, follow its rules
and prove the change is clean:

1. Run `svarupa <path>` once, or reuse a fresh `<path>/.svarupa/`.
2. Before editing, read the rules:
   `svarupa query <path>/.svarupa get_design_rules --json`. Each unit lists
   its parts and the import directions that are forbidden; `checks` lists
   every health rule with its limit and how to stay within it.
3. After editing, check the change against the last commit:
   `svarupa check <path> --base HEAD --json`. It lists only the violations
   your change added (moved code does not count) and any grade that got
   worse. Exit 0 means clean; exit 3 means fix it before you finish.
4. For each new violation, `svarupa explain <check>` says why it matters and
   how to fix it. If the violation is right on purpose, propose an exception
   with a reason in `.svarupa/design.yaml` and say so; never weaken a rule
   silently. Use `--min-severity major` only when the user asked for it.
```

Then write the repository copy: `uv run python -c "from pathlib import Path; from svarupa.setup.skill import SKILL_MD; Path('skills/svarupa/SKILL.md').write_text(SKILL_MD, encoding='utf8')"`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_setup.py -q`
Expected: PASS (including the existing flag and byte-equality tests).

- [ ] **Step 5: Mutations** (append to `MUTATIONS` in `scripts/mutate_packs.py`, add `"tests/test_check.py"` to `SUITE`, add the promises to the docstring list)

```python
    (
        "line numbers decide whether a finding is new",
        "svarupa/check.py",
        '        return (self.check, self.file, self.symbol or _DIGITS.sub("#", self.message))\n',
        '        return (self.check, self.file, f"{self.line}")\n',
    ),
    (
        "a grade drop does not fail the check",
        "svarupa/check.py",
        "        return bool(self.worse()) or any(\n",
        "        return False or any(\n",
    ),
```

Docstring lines:

```
* moved code is never a new finding;
* a grade drop fails the check.
```

Run: `uv run python scripts/mutate_packs.py 2>&1 | tail -3`
Expected: every mutation caught (44/44).

- [ ] **Step 6: Docs** (`docs/health.md`, after the Explanations section)

```markdown
## Checking a change

    svarupa check <repo> --base main          # a git ref, built in a temporary worktree
    svarupa check <repo> --base old/graph.json --json

Lists the violations the working tree adds against the base, the ones it
fixed, and every area whose grade moved. A finding is the same finding when
it is the same check in the same file on the same definition, so moving
code never makes it new. Exit 3 when a new violation is at or above
`--min-severity` (default minor) or a grade got worse; exit 0 otherwise.
Both sides are judged by the working tree's `.svarupa/design.yaml`.

AI agents get every rule up front from `get_design_rules` (`checks`: id,
rule, area, severity, limit, fix), and the agent skill tells them to run
`svarupa check` before they finish.
```

- [ ] **Step 7: Full suite, lint, commit**

```bash
uv run pytest -q 2>&1 | tail -3
uv run ruff format svarupa tests && uv run ruff check svarupa tests scripts && uv run pyright svarupa
git add svarupa/setup/skill.py skills/svarupa/SKILL.md tests/test_setup.py scripts/mutate_packs.py docs/health.md
git commit -m "Teach agents to read the rules and check their change"
```
