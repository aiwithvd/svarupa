# One-Command Setup for AI Coding Tools (phase 3) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `svarupa setup ide` teaches every AI coding tool a team uses the Svarupa workflow and this repository's rules, and wires up the MCP server, without changing anything the team wrote.

**Architecture:** Two pure modules do the risky work: `svarupa/setup/merge.py` (marked Markdown blocks and MCP JSON merges) and `svarupa/setup/rules.py` (the shared rules text, with a section generated from the repository's design). The installer learns a second kind of file, a merge, next to the existing whole files. `svarupa/setup/ide.py` defines one target per tool and a detecting `ide` target.

**Tech Stack:** Python 3.10+, json, argparse, pytest.

**Spec:** `docs/superpowers/specs/2026-10-24-ide-setup-phase3-design.md`

## Global Constraints

- Markers: `<!-- svarupa:begin -->` and `<!-- svarupa:end -->`; a missing block is appended after one blank line; an existing block is replaced in place; text outside the markers never changes; a begin marker without an end marker is refused.
- MCP entry: `{"command": "svarupa", "args": ["mcp", ".svarupa"]}` under `mcpServers` (Claude Code `.mcp.json`, Cursor `.cursor/mcp.json`); under `servers` with `"type": "stdio"` first (VS Code `.vscode/mcp.json`); other keys and servers kept; written with 2-space indentation and a trailing newline; a file that is not a JSON object is refused.
- Owned whole files (`.cursor/rules/svarupa.mdc` with `alwaysApply: true`, `.windsurf/rules/svarupa.md` with `trigger: always_on`, the skill) keep the differs-means-refuse rule; `--update` replaces them (it is `--force` for owned files).
- `ide` installs `agents` always, plus `claude` (`CLAUDE.md` or `.claude/`), `cursor` (`.cursor/`), `copilot` (`.vscode/` or `.github/copilot-instructions.md`), `windsurf` (`.windsurf/`); `--all` installs every target.
- Global configs (Windsurf, Codex) are never edited; next steps print the snippet. Next steps say to run `svarupa .` first and install `svarupa[mcp]`.
- `--dry-run` prints each path with `create`, `update` or `unchanged` and writes nothing.
- The symlink and escape guards apply to every path, merges included.
- This branch must pass its own gate: `uv run svarupa check . --base main` exits 0 before the last commit.
- Plain English, no em dashes. Commits SSH-signed (repo-local), imperative, under 72 characters, no AI attribution.

## Review Focus

1. A user's `CLAUDE.md` with Windows line endings or no trailing newline keeps every byte outside the block. Pinned in Task 1 (`test_block_append_keeps_the_user_text_byte_for_byte`).
2. A second begin marker, or markers inside a code fence the user wrote, never swallow user text. Pinned in Task 1 (`test_a_second_begin_marker_is_refused`).
3. An `.mcp.json` whose `mcpServers` is not an object is refused, not overwritten. Pinned in Task 1 (`test_mcp_merge_refuses_a_non_object_server_map`).
4. Setup in a repository Svarupa cannot analyze yet (no source files) still writes the workflow and says how to refresh. Pinned in Task 2 (`test_rules_for_an_empty_repository_say_how_to_refresh`).
5. A refused merge writes nothing, including files of the same target that would have been fine. Pinned in Task 3 (`test_a_refused_merge_writes_nothing`).

---

### Task 1: Marked blocks and MCP merges

**Files:**
- Create: `svarupa/setup/merge.py`
- Test: `tests/test_setup_merge.py`

**Interfaces:**
- Produces: `BEGIN = "<!-- svarupa:begin -->"`, `END = "<!-- svarupa:end -->"`, `MergeError(ValueError)`, `merge_block(existing: str | None, body: str) -> str`, `merge_mcp(existing: str | None, key: str, entry: dict[str, object]) -> str`.

- [ ] **Step 1: Write the failing tests** (create `tests/test_setup_merge.py`)

```python
"""Editing files the user may own: a marked block, a merged MCP entry."""

from __future__ import annotations

import json

import pytest

from svarupa.setup.merge import BEGIN, END, MergeError, merge_block, merge_mcp

BODY = "# Svarupa\n\nRules here.\n"
ENTRY = {"command": "svarupa", "args": ["mcp", ".svarupa"]}


def test_a_new_file_is_just_the_block() -> None:
    assert merge_block(None, BODY) == f"{BEGIN}\n{BODY}{END}\n"
    assert merge_block("", BODY) == f"{BEGIN}\n{BODY}{END}\n"


def test_block_append_keeps_the_user_text_byte_for_byte() -> None:
    mine = "# My rules\r\n\r\nUse tabs.\r\n"
    out = merge_block(mine, BODY)
    assert out.startswith(mine)
    assert out[len(mine) :] == f"\n{BEGIN}\n{BODY}{END}\n"
    no_newline = "Use tabs."
    assert merge_block(no_newline, BODY) == f"Use tabs.\n\n{BEGIN}\n{BODY}{END}\n"


def test_an_existing_block_is_replaced_in_place() -> None:
    before, after = "intro\n\n", "\n## Later\nkept\n"
    old = f"{before}{BEGIN}\nold rules\n{END}\n{after}"
    out = merge_block(old, BODY)
    assert out == f"{before}{BEGIN}\n{BODY}{END}\n{after}"
    assert merge_block(out, BODY) == out


def test_a_begin_marker_without_an_end_is_refused() -> None:
    with pytest.raises(MergeError, match="no end marker"):
        merge_block(f"intro\n{BEGIN}\nhalf\n", BODY)


def test_a_second_begin_marker_is_refused() -> None:
    text = f"{BEGIN}\na\n{END}\n```\n{BEGIN}\n```\n"
    with pytest.raises(MergeError, match="more than one"):
        merge_block(text, BODY)


def test_mcp_merge_adds_svarupa_and_keeps_others() -> None:
    mine = json.dumps({"mcpServers": {"other": {"command": "x"}}, "keep": 1})
    out = json.loads(merge_mcp(mine, "mcpServers", ENTRY))
    assert out == {"mcpServers": {"other": {"command": "x"}, "svarupa": ENTRY}, "keep": 1}
    text = merge_mcp(mine, "mcpServers", ENTRY)
    assert text.endswith("}\n") and '\n  "mcpServers"' in text
    assert merge_mcp(text, "mcpServers", ENTRY) == text


def test_mcp_merge_creates_a_new_file() -> None:
    assert json.loads(merge_mcp(None, "servers", ENTRY)) == {"servers": {"svarupa": ENTRY}}


def test_mcp_merge_refuses_invalid_json_and_arrays() -> None:
    for bad in ("{not json", "[]", "null", '"x"'):
        with pytest.raises(MergeError, match="JSON object"):
            merge_mcp(bad, "mcpServers", ENTRY)


def test_mcp_merge_refuses_a_non_object_server_map() -> None:
    with pytest.raises(MergeError, match="mcpServers"):
        merge_mcp(json.dumps({"mcpServers": []}), "mcpServers", ENTRY)
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_setup_merge.py -q`
Expected: FAIL at import (`svarupa.setup.merge` does not exist).

- [ ] **Step 3: Write `svarupa/setup/merge.py`**

```python
"""Editing files a user may own, without touching what they wrote.

A Markdown rules file gets one block between two markers: appended once,
replaced in place after that, and nothing outside the markers changes. An
MCP config gets one `svarupa` server merged in next to the user's own. A
file that cannot be edited safely is refused, never rewritten.
"""

from __future__ import annotations

import json
from typing import Any, cast

__all__ = ["BEGIN", "END", "MergeError", "merge_block", "merge_mcp"]

BEGIN = "<!-- svarupa:begin -->"
END = "<!-- svarupa:end -->"


class MergeError(ValueError):
    """A file that cannot be edited without guessing."""


def _block(body: str) -> str:
    return f"{BEGIN}\n{body if body.endswith(chr(10)) else body + chr(10)}{END}\n"


def merge_block(existing: str | None, body: str) -> str:
    """`existing` with the Svarupa block added or replaced."""
    block = _block(body)
    if not existing:
        return block
    count = existing.count(BEGIN)
    if count > 1:
        raise MergeError("has more than one svarupa begin marker; remove the extra one")
    if count == 0:
        sep = "" if existing.endswith("\n") else "\n"
        return f"{existing}{sep}\n{block}"
    start = existing.index(BEGIN)
    end = existing.find(END, start)
    if end == -1:
        raise MergeError("has a svarupa begin marker but no end marker; add the end marker")
    stop = end + len(END)
    if existing.startswith("\n", stop):
        stop += 1
    elif existing.startswith("\r\n", stop):
        stop += 2
    return existing[:start] + block + existing[stop:]


def merge_mcp(existing: str | None, key: str, entry: dict[str, object]) -> str:
    """`existing` MCP JSON with `key.svarupa` set to `entry`, others kept."""
    data: Any = {}
    if existing and existing.strip():
        try:
            data = json.loads(existing)
        except ValueError as exc:
            raise MergeError(f"is not a JSON object ({exc}); fix or remove it") from exc
    if not isinstance(data, dict):
        raise MergeError("is not a JSON object; fix or remove it")
    doc = cast("dict[str, Any]", data)
    servers: Any = doc.setdefault(key, {})
    if not isinstance(servers, dict):
        raise MergeError(f"has a {key} value that is not a JSON object")
    cast("dict[str, Any]", servers)["svarupa"] = entry
    return json.dumps(doc, indent=2, ensure_ascii=False) + "\n"
```

- [ ] **Step 4: Run them to verify they pass**

Run: `uv run pytest tests/test_setup_merge.py -q`
Expected: PASS.

- [ ] **Step 5: Lint, commit**

```bash
uv run ruff format svarupa tests && uv run ruff check svarupa tests && uv run pyright svarupa
git add svarupa/setup/merge.py tests/test_setup_merge.py
git commit -m "Merge marked rules blocks and MCP entries without touching user text"
```

(If the import reachability test fails because nothing imports `svarupa.setup.merge` yet, record Task 1 after Task 3 wires it in, and ledger it.)

---

### Task 2: The shared rules text

**Files:**
- Create: `svarupa/setup/rules.py`
- Test: `tests/test_setup_rules.py`

**Interfaces:**
- Consumes: `SKILL_MD` (svarupa/setup/skill.py), `detect`, `build`, `extract`, `declared_dependencies`, `load_design`, `DESIGN_FILE`, `design_for`, `svarupa.design.BY_ID` (styles with `.order`, `.forbidden`), `CATALOG`, `explanation`.
- Produces: `workflow_text() -> str`; `repo_rules(root: Path) -> str`; `rules_body(root: Path) -> str` (cached per resolved root).

- [ ] **Step 1: Write the failing tests** (create `tests/test_setup_rules.py`)

```python
"""The rules text every AI coding tool gets."""

from __future__ import annotations

from pathlib import Path

from svarupa.health import CATALOG
from svarupa.setup.rules import repo_rules, rules_body, workflow_text
from svarupa.setup.skill import SKILL_MD
from tests.test_design_outputs import layered


def test_the_workflow_is_the_skill_section() -> None:
    text = workflow_text()
    assert text.startswith("## Before and after you write code\n")
    assert text in SKILL_MD
    assert "svarupa check" in text and "\n## " not in text.rstrip()[3:]


def test_repo_rules_list_forbidden_directions_and_every_check(tmp_path: Path) -> None:
    layered(tmp_path)
    text = repo_rules(tmp_path)
    assert text.startswith("## This repository's rules\n")
    assert "layered" in text
    assert "must not import" in text and "api" in text
    for c in CATALOG:
        assert f"- {c.id} (" in text
    assert "svarupa setup" in text and "--update" in text


def test_rules_for_an_empty_repository_say_how_to_refresh(tmp_path: Path) -> None:
    text = repo_rules(tmp_path)
    assert "no rules could be read yet" in text
    assert "--update" in text
    assert "- query-in-loop (" in text


def test_the_body_starts_with_a_title_and_holds_both_parts(tmp_path: Path) -> None:
    layered(tmp_path)
    body = rules_body(tmp_path)
    assert body.startswith("# Svarupa\n")
    assert "## Before and after you write code" in body
    assert "## This repository's rules" in body
```

(`layered(root)` in `tests/test_design_outputs.py` writes a repository with a layered unit; if it needs extra arguments, call it as that module's own tests do and ledger it.)

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_setup_rules.py -q`
Expected: FAIL at import.

- [ ] **Step 3: Write `svarupa/setup/rules.py`**

```python
"""The rules every AI coding tool gets: the fixed workflow, plus this
repository's design and health rules, generated from the code."""

from __future__ import annotations

from functools import cache
from pathlib import Path

from svarupa.setup.skill import SKILL_MD

__all__ = ["repo_rules", "rules_body", "workflow_text"]

_WORKFLOW = "## Before and after you write code\n"


def workflow_text() -> str:
    """The skill's workflow section, so the two can never drift."""
    start = SKILL_MD.index(_WORKFLOW)
    end = SKILL_MD.find("\n## ", start + len(_WORKFLOW))
    return SKILL_MD[start : end + 1 if end != -1 else len(SKILL_MD)]


def _design_lines(root: Path) -> list[str]:
    from svarupa.build import build
    from svarupa.design import BY_ID as STYLES
    from svarupa.design import design_for
    from svarupa.design.file import DESIGN_FILE, load_design
    from svarupa.detect import detect
    from svarupa.extract import declared_dependencies, extract

    scan = detect(str(root))
    graph = build(scan, extract(scan, declared_dependencies(scan)), strict=False)
    accepted, _ = load_design(root / DESIGN_FILE)
    design = design_for(scan, graph, accepted)
    lines: list[str] = []
    for u in design.units:
        name = u.unit.id or "."
        if u.chosen is None:
            lines.append(f"- `{name}`: no style accepted yet")
            continue
        style = STYLES[u.chosen.style]
        order = list(style.order)
        pairs = [(order[j], order[i]) for i in range(len(order)) for j in range(i + 1, len(order))]
        pairs += list(style.forbidden)
        rules = "; ".join(f"{a} must not import {b}" for a, b in pairs) or "no direction rules"
        lines.append(f"- `{name}` follows {style.id}: {rules}")
    for e in design.exceptions:
        where = e.at or f"{e.src} -> {e.dst}"
        lines.append(f"- Accepted exception: {e.rule} at {where} ({e.reason})")
    return lines


def repo_rules(root: Path) -> str:
    """This repository's design and health rules, as Markdown."""
    from svarupa.diagnostics import DiagnosticError
    from svarupa.health import CATALOG
    from svarupa.health.explain import explanation

    out = [
        "## This repository's rules",
        "",
        "Generated by `svarupa setup`. After the design changes, refresh this "
        "section with `svarupa setup ide --update`.",
        "",
        "### Design",
        "",
    ]
    try:
        out += _design_lines(root) or ["- No unit found yet."]
    except DiagnosticError:
        out.append(
            "- Svarupa found no source it can analyze, so no rules could be read yet. "
            "Run `svarupa setup ide --update` once the repository has code."
        )
    out += ["", "### Health checks (a change must not add any)", ""]
    out += [f"- {c.id} ({c.area}, {c.severity}): {explanation(c.id).fix}" for c in CATALOG]
    return "\n".join(out) + "\n"


@cache
def _body(resolved: str) -> str:
    return (
        "# Svarupa\n\n"
        "This repository is analyzed by Svarupa. Follow the workflow and the rules "
        "below when you change code.\n\n"
        f"{workflow_text()}\n{repo_rules(Path(resolved))}"
    )


def rules_body(root: Path) -> str:
    """The full text for every rules file, computed once per repository."""
    return _body(str(root.resolve()))
```

- [ ] **Step 4: Run them to verify they pass**

Run: `uv run pytest tests/test_setup_rules.py -q`
Expected: PASS. If `detect` on an empty directory does not raise `DiagnosticError` (it may return an empty scan instead), make `_design_lines` return `[]` for an empty scan and keep the "no rules could be read yet" line for both cases; ledger it.

- [ ] **Step 5: Lint, commit**

```bash
uv run ruff format svarupa tests && uv run ruff check svarupa tests && uv run pyright svarupa
git add svarupa/setup/rules.py tests/test_setup_rules.py
git commit -m "Build the shared rules text from the skill and the repository"
```

(Same reachability note as Task 1.)

---

### Task 3: The installer learns merges, `--update`, `--dry-run`

**Files:**
- Modify: `svarupa/setup/base.py` (`Target.__init__(root)`, `Merge`, `Target.merges()`)
- Modify: `svarupa/setup/__init__.py` (`install`, `Installed`)
- Modify: `svarupa/cli.py` (`_setup`: `--update`, `--dry-run`, `--all`)
- Test: `tests/test_setup_install.py`

**Interfaces:**
- Consumes: `merge_block`, `merge_mcp`, `MergeError` (Task 1).
- Produces:
  - `Merge(path: str, kind: str, body: str = "", key: str = "", entry: Mapping[str, object] | None = None)` frozen dataclass; `kind` is `"block"` or `"mcp"`.
  - `Target.__init__(self, root: Path = Path("."))` storing `self.root`; `Target.merges(self) -> tuple[Merge, ...]` (default `()`).
  - `install(target, dest, force, *, dry_run=False) -> Installed`; `Installed(written, unchanged, actions: tuple[tuple[Path, str], ...] = ())`, where `actions` holds `create`/`update`/`unchanged` per path.
  - SVA-S-005 for a refused merge (message from `MergeError`).

- [ ] **Step 1: Write the failing tests** (create `tests/test_setup_install.py`)

```python
"""The installer: whole files, merges, update and dry run."""

from __future__ import annotations

import json
from pathlib import Path
from typing import ClassVar

import pytest

from svarupa.diagnostics import DiagnosticError
from svarupa.setup import install
from svarupa.setup.base import Merge, Target
from svarupa.setup.merge import BEGIN


class Demo(Target):
    name: ClassVar[str] = "demo"
    summary: ClassVar[str] = "a demo"

    def files(self) -> tuple[tuple[str, str], ...]:
        return (("owned/rules.md", "owned\n"),)

    def merges(self) -> tuple[Merge, ...]:
        return (
            Merge("NOTES.md", "block", body="rules\n"),
            Merge(".mcp.json", "mcp", key="mcpServers", entry={"command": "svarupa"}),
        )

    def next_steps(self) -> tuple[str, ...]:
        return ()


def test_merges_and_files_are_written(tmp_path: Path) -> None:
    (tmp_path / "NOTES.md").write_text("mine\n", encoding="utf8")
    result = install(Demo(tmp_path), tmp_path, force=False)
    assert (tmp_path / "owned/rules.md").read_text(encoding="utf8") == "owned\n"
    notes = (tmp_path / "NOTES.md").read_text(encoding="utf8")
    assert notes.startswith("mine\n\n" + BEGIN)
    assert json.loads((tmp_path / ".mcp.json").read_text(encoding="utf8"))["mcpServers"]["svarupa"]
    assert len(result.written) == 3


def test_a_second_run_changes_nothing(tmp_path: Path) -> None:
    install(Demo(tmp_path), tmp_path, force=False)
    again = install(Demo(tmp_path), tmp_path, force=False)
    assert again.written == () and len(again.unchanged) == 3


def test_a_refused_merge_writes_nothing(tmp_path: Path) -> None:
    (tmp_path / ".mcp.json").write_text("[]", encoding="utf8")
    with pytest.raises(DiagnosticError) as exc:
        install(Demo(tmp_path), tmp_path, force=False)
    assert exc.value.diagnostic.code == "SVA-S-005"
    assert not (tmp_path / "owned").exists() and not (tmp_path / "NOTES.md").exists()


def test_dry_run_reports_and_writes_nothing(tmp_path: Path) -> None:
    (tmp_path / "NOTES.md").write_text("mine\n", encoding="utf8")
    result = install(Demo(tmp_path), tmp_path, force=False, dry_run=True)
    assert [(p.name, a) for p, a in result.actions] == [
        ("rules.md", "create"),
        ("NOTES.md", "update"),
        (".mcp.json", "create"),
    ]
    assert (tmp_path / "NOTES.md").read_text(encoding="utf8") == "mine\n"
    assert not (tmp_path / ".mcp.json").exists() and result.written == ()


def test_a_symlinked_merge_target_is_refused(tmp_path: Path) -> None:
    outside = tmp_path / "outside.md"
    outside.write_text("x\n", encoding="utf8")
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "NOTES.md").symlink_to(outside)
    with pytest.raises(DiagnosticError) as exc:
        install(Demo(repo), repo, force=False)
    assert exc.value.diagnostic.code == "SVA-S-003"
    assert outside.read_text(encoding="utf8") == "x\n"
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_setup_install.py -q`
Expected: FAIL (`ImportError: cannot import name 'Merge'`).

- [ ] **Step 3: `svarupa/setup/base.py`**

Add `from collections.abc import Mapping`, `from dataclasses import dataclass`, `from pathlib import Path`; add `"Merge"` to `__all__`; add above `class Target`:

```python
@dataclass(frozen=True, slots=True)
class Merge:
    """A file the user may own, edited without touching their text: a marked
    Markdown block (`kind="block"`) or one MCP server entry (`kind="mcp"`)."""

    path: str  # repository-relative POSIX path
    kind: str  # "block" | "mcp"
    body: str = ""
    key: str = ""
    entry: Mapping[str, object] | None = None
```

In `class Target`, add:

```python
    def __init__(self, root: Path = Path(".")) -> None:
        self.root = root

    def merges(self) -> tuple[Merge, ...]:
        """Files edited in place rather than written whole. Empty for most targets."""
        return ()
```

- [ ] **Step 4: `install` in `svarupa/setup/__init__.py`**

Import `from svarupa.setup.base import Merge` and `from svarupa.setup.merge import MergeError, merge_block, merge_mcp`. Add `actions: tuple[tuple[Path, str], ...] = ()` as the last field of `Installed`. Add the helper:

```python
def _merged(path: Path, merge: Merge) -> str:
    """The content `merge` leaves at `path`, or SVA-S-005 when it cannot."""
    try:
        existing = path.read_text(encoding="utf8") if path.is_file() else None
        if merge.kind == "block":
            return merge_block(existing, merge.body)
        return merge_mcp(existing, merge.key, dict(merge.entry or {}))
    except (MergeError, OSError, UnicodeDecodeError) as exc:
        raise DiagnosticError(
            Diagnostic(
                code="SVA-S-005",
                severity=Severity.ERROR,
                message=f"{exc}; nothing was written",
                subject=str(path),
                suggested_fixes=("Fix the file by hand, then re-run setup.",),
            )
        ) from exc


def _action(path: Path, content: str) -> str:
    if not path.exists():
        return "create"
    return "update" if _differs(path, content) else "unchanged"
```

Change the signature to `def install(target: Target, dest: Path, force: bool, *, dry_run: bool = False) -> Installed:` and make these four edits inside it:

1. Right after `planned = [...]`, add
   `    merges = [(dest / Path(m.path), m) for m in target.merges()]`
2. Change the escaping line to
   `    escaping = [p for p in [*(p for p, _ in planned), *(p for p, _ in merges)] if _escapes(p, dest)]`
3. After the collision refusal block (`if clashes and not force: raise ...`), add:

```python
    # Every merge is computed before anything is written, so a refused merge
    # (SVA-S-005) leaves the destination exactly as it was.
    merged = [(p, _merged(p, m)) for p, m in merges]
    todo = [*planned, *merged]
    actions = tuple((p, _action(p, c)) for p, c in todo)
    if dry_run:
        return Installed(
            written=(),
            unchanged=tuple(p for p, a in actions if a == "unchanged"),
            actions=actions,
        )
```

4. Change the write loop header from `for path, content in planned:` to `for path, content in todo:`, and the final line to `return Installed(written=tuple(written), unchanged=tuple(unchanged), actions=actions)`.

- [ ] **Step 5: The CLI** (`svarupa/cli.py`, `_setup`)

Add flags after `--force`:

```python
    parser.add_argument(
        "--update",
        action="store_true",
        help="refresh Svarupa's own files and marked blocks after the design changed",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="show what would change and write nothing"
    )
    parser.add_argument(
        "--all", action="store_true", help="with ide: install every tool, not only detected ones"
    )
```

Construct the target with the root (and `every` for `ide`, which Task 4 adds; until then use `TARGETS[args.target](Path(args.dest))`), call `install(target, Path(args.dest), args.force or args.update, dry_run=args.dry_run)`, and for a dry run print `  {action:<9} {path}` per action instead of the written/unchanged lines.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/test_setup_install.py tests/test_setup.py tests/test_setup_merge.py -q`
Expected: PASS (existing targets keep working: `SkillTarget()` still constructs with the default root).

- [ ] **Step 7: Full suite, lint, mutations, commit**

```bash
uv run pytest -q 2>&1 | tail -2
uv run ruff format svarupa tests && uv run ruff check svarupa tests scripts && uv run pyright svarupa
uv run python scripts/mutate_setup.py 2>&1 | tail -3
git add svarupa/setup svarupa/cli.py tests/test_setup_install.py
git commit -m "Install merged files, refresh with --update, preview with --dry-run"
```

Expected: suite PASS (the reachability test now passes for merge.py; rules.py is wired in Task 4), every setup mutation caught (if an existing mutation's `old` text moved, retarget it to the same promise and say so).

---

### Task 4: The tool targets and `svarupa setup ide`

**Files:**
- Create: `svarupa/setup/ide.py`
- Modify: `svarupa/setup/__init__.py` (`TARGETS`), `svarupa/cli.py` (`ide` construction with `every=args.all`)
- Test: `tests/test_setup_ide.py`

**Interfaces:**
- Consumes: `Merge`, `Target` (Task 3), `rules_body` (Task 2), `SKILL_MD`.
- Produces: targets `agents`, `claude`, `cursor`, `copilot`, `windsurf`, `ide` (`IdeTarget(root, every=False)` with `selected() -> list[Target]`).

- [ ] **Step 1: Write the failing tests** (create `tests/test_setup_ide.py`)

```python
"""One target per AI coding tool, and `ide` that detects them."""

from __future__ import annotations

import json
from pathlib import Path

from svarupa.cli import main
from svarupa.setup import TARGETS
from svarupa.setup.merge import BEGIN

ENTRY = {"command": "svarupa", "args": ["mcp", ".svarupa"]}


def files(root: Path) -> set[str]:
    return {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}


def test_every_tool_target_is_registered() -> None:
    assert {"agents", "claude", "cursor", "copilot", "windsurf", "ide"} <= set(TARGETS)


def test_each_target_writes_exactly_its_files(tmp_path: Path) -> None:
    expected = {
        "agents": {"AGENTS.md"},
        "claude": {"CLAUDE.md", ".claude/skills/svarupa/SKILL.md", ".mcp.json"},
        "cursor": {".cursor/rules/svarupa.mdc", ".cursor/mcp.json"},
        "copilot": {".github/copilot-instructions.md", ".vscode/mcp.json"},
        "windsurf": {".windsurf/rules/svarupa.md"},
    }
    for name, want in expected.items():
        root = tmp_path / name
        root.mkdir()
        assert main(["setup", name, "--dest", str(root)]) == 0
        assert files(root) == want, name


def test_mcp_shapes_per_tool(tmp_path: Path) -> None:
    assert main(["setup", "cursor", "--dest", str(tmp_path)]) == 0
    assert main(["setup", "copilot", "--dest", str(tmp_path)]) == 0
    cursor = json.loads((tmp_path / ".cursor/mcp.json").read_text(encoding="utf8"))
    vscode = json.loads((tmp_path / ".vscode/mcp.json").read_text(encoding="utf8"))
    assert cursor == {"mcpServers": {"svarupa": ENTRY}}
    assert vscode == {"servers": {"svarupa": {"type": "stdio", **ENTRY}}}


def test_owned_rules_files_have_their_front_matter(tmp_path: Path) -> None:
    assert main(["setup", "cursor", "--dest", str(tmp_path)]) == 0
    assert main(["setup", "windsurf", "--dest", str(tmp_path)]) == 0
    mdc = (tmp_path / ".cursor/rules/svarupa.mdc").read_text(encoding="utf8")
    assert mdc.startswith("---\ndescription: ") and "\nalwaysApply: true\n---\n" in mdc
    ws = (tmp_path / ".windsurf/rules/svarupa.md").read_text(encoding="utf8")
    assert ws.startswith("---\ntrigger: always_on\n---\n")
    assert "## Before and after you write code" in mdc and "## This repository's rules" in ws


def test_ide_installs_agents_plus_detected_tools(tmp_path: Path) -> None:
    (tmp_path / ".cursor").mkdir()
    (tmp_path / "CLAUDE.md").write_text("# mine\n", encoding="utf8")
    assert main(["setup", "ide", "--dest", str(tmp_path)]) == 0
    got = files(tmp_path)
    assert "AGENTS.md" in got and ".cursor/rules/svarupa.mdc" in got and ".mcp.json" in got
    assert ".vscode/mcp.json" not in got and ".windsurf/rules/svarupa.md" not in got
    claude = (tmp_path / "CLAUDE.md").read_text(encoding="utf8")
    assert claude.startswith("# mine\n\n" + BEGIN)


def test_ide_all_installs_every_tool(tmp_path: Path) -> None:
    assert main(["setup", "ide", "--all", "--dest", str(tmp_path)]) == 0
    got = files(tmp_path)
    for path in ("AGENTS.md", "CLAUDE.md", ".cursor/mcp.json", ".vscode/mcp.json", ".windsurf/rules/svarupa.md"):
        assert path in got


def test_update_refreshes_only_the_block(tmp_path: Path) -> None:
    assert main(["setup", "agents", "--dest", str(tmp_path)]) == 0
    path = tmp_path / "AGENTS.md"
    text = path.read_text(encoding="utf8")
    path.write_text("top\n\n" + text.replace("follow its rules", "OLD") + "\nbottom\n", encoding="utf8")
    assert main(["setup", "agents", "--update", "--dest", str(tmp_path)]) == 0
    new = path.read_text(encoding="utf8")
    assert new.startswith("top\n\n" + BEGIN) and new.endswith("\nbottom\n") and "OLD" not in new


def test_next_steps_print_the_global_snippets(tmp_path: Path, capsys) -> None:
    assert main(["setup", "windsurf", "--dest", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "mcp_config.json" in out and '"args": ["mcp", ".svarupa"]' in out
    assert "svarupa[mcp]" in out and "svarupa ." in out
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_setup_ide.py -q`
Expected: FAIL (targets not registered; argparse rejects the names with exit 2 raised as SystemExit).

- [ ] **Step 3: Write `svarupa/setup/ide.py`**

```python
"""One setup target per AI coding tool, and `ide`, which installs the ones a
repository uses. Every tool gets the same rules text; tools with a project
MCP config get Svarupa's server merged in next to the user's own."""

from __future__ import annotations

import json
from pathlib import Path
from typing import ClassVar

from svarupa.setup.base import Merge, Target
from svarupa.setup.rules import rules_body
from svarupa.setup.skill import SKILL_MD

__all__ = [
    "AgentsTarget",
    "ClaudeTarget",
    "CopilotTarget",
    "CursorTarget",
    "IdeTarget",
    "WindsurfTarget",
]

ENTRY: dict[str, object] = {"command": "svarupa", "args": ["mcp", ".svarupa"]}
_FIRST = (
    "Run `svarupa .` so `.svarupa/graph.json` exists, and install the MCP extra "
    "(`pip install 'svarupa[mcp]'`) so the server can start."
)
_REFRESH = "After the design changes, run `svarupa setup ide --update`."


class AgentsTarget(Target):
    name: ClassVar[str] = "agents"
    summary: ClassVar[str] = "rules for AGENTS.md readers (Codex and others), as a marked block"

    def files(self) -> tuple[tuple[str, str], ...]:
        return ()

    def merges(self) -> tuple[Merge, ...]:
        return (Merge("AGENTS.md", "block", body=rules_body(self.root)),)

    def next_steps(self) -> tuple[str, ...]:
        snippet = '[mcp_servers.svarupa]\ncommand = "svarupa"\nargs = ["mcp", ".svarupa"]'
        return (
            "Commit AGENTS.md.",
            _REFRESH,
            f"For Codex MCP, add to ~/.codex/config.toml:\n{snippet}",
        )


class ClaudeTarget(Target):
    name: ClassVar[str] = "claude"
    summary: ClassVar[str] = "Claude Code: CLAUDE.md block, the skill, and .mcp.json"

    def files(self) -> tuple[tuple[str, str], ...]:
        return ((".claude/skills/svarupa/SKILL.md", SKILL_MD),)

    def merges(self) -> tuple[Merge, ...]:
        return (
            Merge("CLAUDE.md", "block", body=rules_body(self.root)),
            Merge(".mcp.json", "mcp", key="mcpServers", entry=ENTRY),
        )

    def next_steps(self) -> tuple[str, ...]:
        return ("Commit CLAUDE.md, .claude/skills/svarupa/SKILL.md and .mcp.json.", _FIRST, _REFRESH)


class CursorTarget(Target):
    name: ClassVar[str] = "cursor"
    summary: ClassVar[str] = "Cursor: .cursor/rules/svarupa.mdc and .cursor/mcp.json"

    def files(self) -> tuple[tuple[str, str], ...]:
        head = "---\ndescription: Svarupa architecture and health rules for this repository\nalwaysApply: true\n---\n\n"
        return ((".cursor/rules/svarupa.mdc", head + rules_body(self.root)),)

    def merges(self) -> tuple[Merge, ...]:
        return (Merge(".cursor/mcp.json", "mcp", key="mcpServers", entry=ENTRY),)

    def next_steps(self) -> tuple[str, ...]:
        return ("Commit .cursor/rules/svarupa.mdc and .cursor/mcp.json.", _FIRST, _REFRESH)


class CopilotTarget(Target):
    name: ClassVar[str] = "copilot"
    summary: ClassVar[str] = "GitHub Copilot: copilot-instructions.md block and .vscode/mcp.json"

    def files(self) -> tuple[tuple[str, str], ...]:
        return ()

    def merges(self) -> tuple[Merge, ...]:
        return (
            Merge(".github/copilot-instructions.md", "block", body=rules_body(self.root)),
            Merge(".vscode/mcp.json", "mcp", key="servers", entry={"type": "stdio", **ENTRY}),
        )

    def next_steps(self) -> tuple[str, ...]:
        return ("Commit .github/copilot-instructions.md and .vscode/mcp.json.", _FIRST, _REFRESH)


class WindsurfTarget(Target):
    name: ClassVar[str] = "windsurf"
    summary: ClassVar[str] = "Windsurf: .windsurf/rules/svarupa.md (MCP config is global)"

    def files(self) -> tuple[tuple[str, str], ...]:
        return ((".windsurf/rules/svarupa.md", "---\ntrigger: always_on\n---\n\n" + rules_body(self.root)),)

    def next_steps(self) -> tuple[str, ...]:
        snippet = json.dumps({"mcpServers": {"svarupa": ENTRY}}, indent=2)
        return (
            "Commit .windsurf/rules/svarupa.md.",
            f"For Windsurf MCP, add to ~/.codeium/windsurf/mcp_config.json:\n{snippet}",
            _FIRST,
            _REFRESH,
        )


_TOOLS: tuple[type[Target], ...] = (ClaudeTarget, CursorTarget, CopilotTarget, WindsurfTarget)


def _detected(root: Path, tool: type[Target]) -> bool:
    signs = {
        "claude": ("CLAUDE.md", ".claude"),
        "cursor": (".cursor",),
        "copilot": (".vscode", ".github/copilot-instructions.md"),
        "windsurf": (".windsurf",),
    }[tool.name]
    return any((root / s).exists() for s in signs)


class IdeTarget(Target):
    name: ClassVar[str] = "ide"
    summary: ClassVar[str] = "every AI coding tool this repository uses (AGENTS.md always; --all for all)"

    def __init__(self, root: Path = Path("."), every: bool = False) -> None:
        super().__init__(root)
        self.every = every

    def selected(self) -> list[Target]:
        tools = [t(self.root) for t in _TOOLS if self.every or _detected(self.root, t)]
        return [AgentsTarget(self.root), *tools]

    def files(self) -> tuple[tuple[str, str], ...]:
        return tuple(f for t in self.selected() for f in t.files())

    def merges(self) -> tuple[Merge, ...]:
        return tuple(m for t in self.selected() for m in t.merges())

    def next_steps(self) -> tuple[str, ...]:
        seen: list[str] = []
        for t in self.selected():
            seen += [s for s in t.next_steps() if s not in seen]
        return tuple(seen)
```

Register in `svarupa/setup/__init__.py` `TARGETS`: `AgentsTarget.name: AgentsTarget, ClaudeTarget.name: ClaudeTarget, CursorTarget.name: CursorTarget, CopilotTarget.name: CopilotTarget, WindsurfTarget.name: WindsurfTarget, IdeTarget.name: IdeTarget` (import them from `svarupa.setup.ide`). In `_setup`, construct `IdeTarget(Path(args.dest), every=args.all)` when `args.target == "ide"`, else `TARGETS[args.target](Path(args.dest))`; `--all` with another target is a usage error (`parser.error("--all is only for the ide target")`).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_setup_ide.py tests/test_setup.py tests/test_setup_install.py -q`
Expected: PASS. The existing test that every `TARGETS` key equals its class's `name` covers the new entries.

- [ ] **Step 5: Lint, commit**

```bash
uv run ruff format svarupa tests && uv run ruff check svarupa tests && uv run pyright svarupa
git add svarupa/setup svarupa/cli.py tests/test_setup_ide.py
git commit -m "Set up Claude Code, Cursor, Copilot, Windsurf and AGENTS.md in one command"
```

---

### Task 5: Mutations, docs and the self-gate

**Files:**
- Modify: `scripts/mutate_setup.py`, `README.md`, `svarupa/setup/skill.py` (`SKILL_MD`), `skills/svarupa/SKILL.md`, `docs/health.md`

- [ ] **Step 1: Mutations** (append to `MUTATIONS` in `scripts/mutate_setup.py`)

```python
    (
        "text after the marked block is dropped",
        "svarupa/setup/merge.py",
        "    return existing[:start] + block + existing[stop:]\n",
        "    return existing[:start] + block\n",
    ),
    (
        "other MCP servers are dropped",
        "svarupa/setup/merge.py",
        '    cast("dict[str, Any]", servers)["svarupa"] = entry\n',
        '    doc[key] = {"svarupa": entry}\n',
    ),
    (
        "detection ignores Cursor",
        "svarupa/setup/ide.py",
        '        "cursor": (".cursor",),\n',
        '        "cursor": (),\n',
    ),
    (
        "a refused merge still writes the other files",
        "svarupa/setup/__init__.py",
        "    merged = [(p, _merged(p, m)) for p, m in merges]\n",
        "    merged = []\n",
    ),
```

Also extend its `SUITE` list (now `["tests/test_setup.py", "tests/test_diagnostics.py", "tests/test_detect.py"]`) with `"tests/test_setup_merge.py"`, `"tests/test_setup_rules.py"`, `"tests/test_setup_install.py"` and `"tests/test_setup_ide.py"`.

Run: `uv run python scripts/mutate_setup.py 2>&1 | tail -4`
Expected: every mutation caught.

- [ ] **Step 2: Docs**

In `README.md`, where the setup targets are listed, add: "`svarupa setup ide` teaches your AI coding tools (Claude Code, Cursor, GitHub Copilot, Windsurf, and AGENTS.md readers such as Codex) the Svarupa workflow and this repository's rules, and adds Svarupa's MCP server to their project config. It only edits text between `<!-- svarupa:begin -->` and `<!-- svarupa:end -->`; refresh with `--update`, preview with `--dry-run`."

In `SKILL_MD` (svarupa/setup/skill.py), after the `svarupa setup ci_github` paragraph, add one sentence: "`svarupa setup ide` installs the same rules for Claude Code, Cursor, GitHub Copilot, Windsurf and AGENTS.md, with the MCP server where the tool has a project config." Regenerate `skills/svarupa/SKILL.md` with `uv run python -c "from pathlib import Path; from svarupa.setup.skill import SKILL_MD; Path('skills/svarupa/SKILL.md').write_text(SKILL_MD, encoding='utf8')"`.

In `docs/health.md`, at the end of "Checking a change", add: "AI coding tools learn these rules from `svarupa setup ide`, which writes them into each tool's rules file."

- [ ] **Step 3: The self-gate and the full checks**

Run: `uv run svarupa check . --base main`
Expected: exit 0. A new violation from this branch is fixed in the code, never in the check.

Run: `uv run pytest -q 2>&1 | tail -2`, `uv run ruff format svarupa tests && uv run ruff check svarupa tests scripts && uv run pyright svarupa`, `uv run python scripts/mutate_setup.py 2>&1 | tail -3`, `uv run python scripts/mutate_packs.py 2>&1 | grep -c "^caught"`
Expected: suite PASS, every setup mutation caught, 44 pack mutations caught.

- [ ] **Step 4: Commit**

```bash
git add scripts/mutate_setup.py README.md svarupa/setup/skill.py skills/svarupa/SKILL.md docs/health.md
git commit -m "Document svarupa setup ide and cover it with mutations"
```
