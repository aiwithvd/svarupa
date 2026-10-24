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


class Twice(Target):
    name: ClassVar[str] = "twice"
    summary: ClassVar[str] = "duplicate destinations"

    def __init__(
        self, root: Path, items: tuple[Merge, ...], files: tuple[str, ...] = ()
    ) -> None:
        super().__init__(root)
        self._items = items
        self._files = files

    def files(self) -> tuple[tuple[str, str], ...]:
        return tuple((f, "x\n") for f in self._files)

    def merges(self) -> tuple[Merge, ...]:
        return self._items

    def next_steps(self) -> tuple[str, ...]:
        return ()


def test_two_merges_of_one_path_are_refused(tmp_path: Path) -> None:
    m = Merge("NOTES.md", "block", body="a\n")
    with pytest.raises(DiagnosticError) as exc:
        install(Twice(tmp_path, (m, m)), tmp_path, force=False)
    assert exc.value.diagnostic.code == "SVA-S-005"
    assert list(tmp_path.iterdir()) == []


def test_a_file_and_a_merge_of_one_path_are_refused(tmp_path: Path) -> None:
    t = Twice(tmp_path, (Merge("NOTES.md", "block", body="a\n"),), ("NOTES.md",))
    with pytest.raises(DiagnosticError) as exc:
        install(t, tmp_path, force=True)
    assert exc.value.diagnostic.code == "SVA-S-005"
    assert list(tmp_path.iterdir()) == []


def test_a_directory_at_a_merge_path_is_refused(tmp_path: Path) -> None:
    (tmp_path / "NOTES.md").mkdir()
    with pytest.raises(DiagnosticError) as exc:
        install(Demo(tmp_path), tmp_path, force=False)
    assert exc.value.diagnostic.code == "SVA-S-005"
    assert not (tmp_path / "owned").exists() and not (tmp_path / ".mcp.json").exists()


def test_a_dry_run_refuses_a_bad_merge_like_a_real_run(tmp_path: Path) -> None:
    (tmp_path / ".mcp.json").write_text("[]", encoding="utf8")
    with pytest.raises(DiagnosticError) as exc:
        install(Demo(tmp_path), tmp_path, force=False, dry_run=True)
    assert exc.value.diagnostic.code == "SVA-S-005"


def test_update_keeps_user_text_outside_the_markers(tmp_path: Path) -> None:
    # No registered target has a block merge yet, so this drives install()
    # with force=True (what the CLI passes for --update) on the Demo target.
    (tmp_path / "NOTES.md").write_text("mine\n", encoding="utf8")
    install(Demo(tmp_path), tmp_path, force=False)
    (tmp_path / "owned").mkdir(exist_ok=True)
    (tmp_path / "owned/rules.md").write_text("stale\n", encoding="utf8")
    install(Demo(tmp_path), tmp_path, force=True)
    notes = (tmp_path / "NOTES.md").read_text(encoding="utf8")
    assert notes.startswith("mine\n") and notes.count(BEGIN) == 1
    assert (tmp_path / "owned/rules.md").read_text(encoding="utf8") == "owned\n"


def test_a_crlf_file_survives_install_byte_for_byte(tmp_path: Path) -> None:
    path = tmp_path / "NOTES.md"
    path.write_bytes(b"# Mine\r\nline two\r\n")
    install(Demo(tmp_path), tmp_path, force=False)
    data = path.read_bytes()
    assert data.startswith(b"# Mine\r\nline two\r\n")
    assert b"\n" not in data.replace(b"\r\n", b"")
    again = install(Demo(tmp_path), tmp_path, force=False)
    assert path not in again.written and path.read_bytes() == data


def test_a_merge_symlink_inside_the_destination_is_edited_through(tmp_path: Path) -> None:
    (tmp_path / "CLAUDE.md").write_text("mine\n", encoding="utf8")
    (tmp_path / "AGENTS.md").symlink_to("CLAUDE.md")
    same = (Merge("CLAUDE.md", "block", body="a\n"), Merge("AGENTS.md", "block", body="a\n"))
    install(Twice(tmp_path, same), tmp_path, force=False)
    assert (tmp_path / "AGENTS.md").is_symlink()
    text = (tmp_path / "CLAUDE.md").read_text(encoding="utf8")
    assert text.count(BEGIN) == 1 and text.startswith("mine\n")
    again = install(Twice(tmp_path, same), tmp_path, force=False)
    assert again.written == ()


def test_symlinked_merges_with_different_bodies_are_refused(tmp_path: Path) -> None:
    (tmp_path / "CLAUDE.md").write_text("mine\n", encoding="utf8")
    (tmp_path / "AGENTS.md").symlink_to("CLAUDE.md")
    diff = (Merge("CLAUDE.md", "block", body="a\n"), Merge("AGENTS.md", "block", body="b\n"))
    with pytest.raises(DiagnosticError) as exc:
        install(Twice(tmp_path, diff), tmp_path, force=False)
    assert exc.value.diagnostic.code == "SVA-S-005"
    assert (tmp_path / "CLAUDE.md").read_text(encoding="utf8") == "mine\n"


def test_a_symlinked_whole_file_is_still_refused(tmp_path: Path) -> None:
    (tmp_path / "real.md").write_text("x\n", encoding="utf8")
    (tmp_path / "owned").mkdir()
    (tmp_path / "owned/rules.md").symlink_to(tmp_path / "real.md")
    with pytest.raises(DiagnosticError) as exc:
        install(Demo(tmp_path), tmp_path, force=True)
    assert exc.value.diagnostic.code == "SVA-S-003"


def test_a_collision_refusal_suggests_update(tmp_path: Path) -> None:
    (tmp_path / "owned").mkdir()
    (tmp_path / "owned/rules.md").write_text("old\n", encoding="utf8")
    with pytest.raises(DiagnosticError) as exc:
        install(Demo(tmp_path), tmp_path, force=False)
    assert exc.value.diagnostic.code == "SVA-S-001"
    assert any("--update" in f for f in exc.value.diagnostic.suggested_fixes)


def test_a_dangling_merge_symlink_is_refused(tmp_path: Path) -> None:
    (tmp_path / "NOTES.md").symlink_to(tmp_path / "missing.md")
    with pytest.raises(DiagnosticError) as exc:
        install(Demo(tmp_path), tmp_path, force=False)
    assert exc.value.diagnostic.code == "SVA-S-003"
    assert not (tmp_path / "missing.md").exists()
