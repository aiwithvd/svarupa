"""`svarupa check` warns when an AI tool's rules no longer match the design."""

from __future__ import annotations

import json
from pathlib import Path

from svarupa.cli import main
from svarupa.setup.stale import stale_rules
from tests.test_check import git_repo
from tests.test_design_outputs import layered
from tests.test_health import write


def setup_all(root: Path) -> None:
    assert main(["setup", "ide", "--all", "--dest", str(root)]) == 0


def test_fresh_rules_are_not_stale(tmp_path: Path) -> None:
    layered(tmp_path)
    setup_all(tmp_path)
    assert stale_rules(tmp_path) == []


def test_an_edited_block_and_an_edited_owned_file_are_stale(tmp_path: Path) -> None:
    layered(tmp_path)
    setup_all(tmp_path)
    claude = tmp_path / "CLAUDE.md"
    claude.write_text(
        claude.read_text(encoding="utf8").replace("must not import", "may import"),
        encoding="utf8",
    )
    mdc = tmp_path / ".cursor/rules/svarupa.mdc"
    mdc.write_text(mdc.read_text(encoding="utf8") + "old line\n", encoding="utf8")
    assert stale_rules(tmp_path) == [".cursor/rules/svarupa.mdc", "CLAUDE.md"]


def test_text_outside_the_block_and_line_endings_do_not_count(tmp_path: Path) -> None:
    layered(tmp_path)
    setup_all(tmp_path)
    agents = tmp_path / "AGENTS.md"
    text = agents.read_text(encoding="utf8")
    agents.write_bytes(
        ("# Team notes\n\n" + text + "\nMore notes.\n").replace("\n", "\r\n").encode("utf8")
    )
    assert stale_rules(tmp_path) == []


def test_files_without_svarupa_rules_are_ignored(tmp_path: Path) -> None:
    layered(tmp_path)
    write(tmp_path, "CLAUDE.md", "# Our own rules\n")
    assert stale_rules(tmp_path) == []


def test_a_design_change_makes_the_rules_stale(tmp_path: Path) -> None:
    layered(tmp_path)
    setup_all(tmp_path)
    write(
        tmp_path,
        ".svarupa/design.yaml",
        "units: {}\nexceptions:\n  - rule: layer-direction\n    from: shop/repository\n    to: shop/api\n    reason: legacy\n",
    )
    assert "AGENTS.md" in stale_rules(tmp_path)


def test_check_warns_about_stale_rules_without_failing(tmp_path: Path, capsys) -> None:
    git_repo(tmp_path)
    setup_all(tmp_path)
    claude = tmp_path / "CLAUDE.md"
    claude.write_text(
        claude.read_text(encoding="utf8").replace("Health checks", "Old checks"),
        encoding="utf8",
    )
    capsys.readouterr()
    assert main(["check", str(tmp_path), "--base", "HEAD", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["stale_rules"] == ["CLAUDE.md"]
    assert main(["check", str(tmp_path), "--base", "HEAD"]) == 0
    assert "stale rules: CLAUDE.md" in capsys.readouterr().out
    assert main(["check", str(tmp_path), "--base", "HEAD", "--format", "github"]) == 0
    out = capsys.readouterr().out
    assert "::warning file=CLAUDE.md,title=Stale rules::" in out
    assert "svarupa setup ide --update" in out
