"""One target per AI coding tool, and `ide` that detects them."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from svarupa.cli import main
from svarupa.setup import TARGETS, ide
from svarupa.setup.ide import IdeTarget
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
    for path in (
        "AGENTS.md",
        "CLAUDE.md",
        ".cursor/mcp.json",
        ".vscode/mcp.json",
        ".windsurf/rules/svarupa.md",
    ):
        assert path in got


def test_update_refreshes_only_the_block(tmp_path: Path) -> None:
    assert main(["setup", "agents", "--dest", str(tmp_path)]) == 0
    path = tmp_path / "AGENTS.md"
    text = path.read_text(encoding="utf8")
    path.write_text(
        "top\n\n" + text.replace("follow its rules", "OLD") + "\nbottom\n", encoding="utf8"
    )
    assert main(["setup", "agents", "--update", "--dest", str(tmp_path)]) == 0
    new = path.read_text(encoding="utf8")
    assert new.startswith("top\n\n" + BEGIN) and new.endswith("\nbottom\n") and "OLD" not in new


def test_next_steps_print_the_global_snippets(tmp_path: Path, capsys) -> None:
    assert main(["setup", "windsurf", "--dest", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "mcp_config.json" in out and '"args": ["mcp", ".svarupa"]' in out
    assert "svarupa[mcp]" in out and "svarupa ." in out


def test_ide_computes_the_rules_once_per_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[Path] = []

    def counting(root: Path) -> str:
        calls.append(root)
        return "rules\n"

    monkeypatch.setattr(ide, "rules_body", counting)
    target = IdeTarget(tmp_path, every=True)
    target.files()
    target.merges()
    target.next_steps()
    assert len(calls) == 1


def test_all_is_only_for_the_ide_target(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["setup", "agents", "--all", "--dest", str(tmp_path)])
    assert exc.value.code == 2
    assert "--all is only for the ide target" in capsys.readouterr().err


def test_ide_all_has_no_duplicate_destinations(tmp_path: Path) -> None:
    target = IdeTarget(tmp_path, every=True)
    paths = [p for p, _ in target.files()] + [m.path for m in target.merges()]
    assert len(paths) == len(set(paths))
