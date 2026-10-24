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
    assert json.loads((tmp_path / ".mcp.json").read_text(encoding="utf8"))["mcpServers"][
        "svarupa"
    ]
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
