"""`svarupa design`: propose, accept, write design.yaml."""

from __future__ import annotations

import io
from pathlib import Path

import yaml

from svarupa.cli import main
from tests.test_design import layered, write


def test_design_without_a_terminal_only_prints(tmp_path: Path, capsys, monkeypatch) -> None:
    layered(tmp_path)
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    assert main(["design", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "layered" in out and "fit" in out
    assert not (tmp_path / ".svarupa" / "design.yaml").exists()


def test_accept_writes_the_design_file(tmp_path: Path) -> None:
    layered(tmp_path)
    assert main(["design", str(tmp_path), "--accept"]) == 0
    data = yaml.safe_load((tmp_path / ".svarupa" / "design.yaml").read_text(encoding="utf8"))
    assert data["units"]["."]["style"] == "layered"
    assert data["units"]["."]["parts"]["api"] == ["shop/api"]
    assert data["exceptions"] == []


def test_style_flag_overrides_the_proposal(tmp_path: Path) -> None:
    layered(tmp_path)
    assert main(["design", str(tmp_path), "--style", ".=clean", "--accept"]) == 0
    data = yaml.safe_load((tmp_path / ".svarupa" / "design.yaml").read_text(encoding="utf8"))
    assert data["units"]["."]["style"] == "clean"


def test_interactive_accept(tmp_path: Path, monkeypatch) -> None:
    layered(tmp_path)

    class Tty(io.StringIO):
        def isatty(self) -> bool:
            return True

    monkeypatch.setattr("sys.stdin", Tty("a\n"))
    assert main(["design", str(tmp_path)]) == 0
    assert (tmp_path / ".svarupa" / "design.yaml").exists()


def test_accepted_design_is_used_by_the_scan(tmp_path: Path) -> None:
    layered(tmp_path, back_edge=True)
    assert main(["design", str(tmp_path), "--accept"]) == 0
    assert main([str(tmp_path)]) == 0
    from svarupa.build import build
    from svarupa.design import design_for
    from svarupa.design.file import load_design
    from svarupa.detect import detect
    from svarupa.extract import declared_dependencies, extract

    scan = detect(tmp_path)
    graph = build(scan, extract(scan, declared_dependencies(scan)), strict=False)
    accepted, problem = load_design(tmp_path / ".svarupa" / "design.yaml")
    assert problem == ""
    unit = design_for(scan, graph, accepted).units[0]
    assert unit.source == "accepted"
    assert [v.rule for v in unit.chosen.violations] == ["layer-direction"]


def test_stale_design_file_falls_back_with_a_note(tmp_path: Path) -> None:
    layered(tmp_path)
    write(
        tmp_path,
        ".svarupa/design.yaml",
        "units:\n  .:\n    style: nonsense\n    parts: {}\nexceptions: []\n",
    )
    from svarupa.build import build
    from svarupa.design import design_for
    from svarupa.design.file import load_design
    from svarupa.detect import detect
    from svarupa.extract import declared_dependencies, extract

    scan = detect(tmp_path)
    graph = build(scan, extract(scan, declared_dependencies(scan)), strict=False)
    accepted, _ = load_design(tmp_path / ".svarupa" / "design.yaml")
    unit = design_for(scan, graph, accepted).units[0]
    assert unit.source == "inferred"
    assert "unknown style 'nonsense'" in unit.note


def test_a_broken_design_file_is_reported(tmp_path: Path) -> None:
    from svarupa.design.file import load_design

    write(tmp_path, "design.yaml", "units: [not, a, mapping\n")
    accepted, problem = load_design(tmp_path / "design.yaml")
    assert accepted is None and "design.yaml" in problem
