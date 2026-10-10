"""Design reaches health and every output."""

from __future__ import annotations

import json
from pathlib import Path

from svarupa.cli import main
from tests.test_design import layered, write


def test_layer_violation_is_a_health_violation(tmp_path: Path, capsys) -> None:
    layered(tmp_path, back_edge=True)
    assert main([str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "design: . layered (inferred)" in out
    gj = json.loads((tmp_path / ".svarupa" / "graph.json").read_text(encoding="utf8"))
    checks = [v["check"] for v in gj["health"]["violations"]]
    assert "layer-direction" in checks
    assert gj["design"]["units"][0]["chosen"]["style"] == "layered"
    report = (tmp_path / ".svarupa" / "REPORT.md").read_text(encoding="utf8")
    assert "## Design" in report and "layered" in report


def test_exceptions_are_listed_not_counted(tmp_path: Path) -> None:
    layered(tmp_path, back_edge=True)
    assert main(["design", str(tmp_path), "--accept"]) == 0
    path = tmp_path / ".svarupa" / "design.yaml"
    text = path.read_text(encoding="utf8").replace(
        "exceptions: []",
        "exceptions:\n- rule: layer-direction\n  from: shop/repository\n  to: shop/api\n  reason: legacy",
    )
    path.write_text(text, encoding="utf8")
    assert main([str(tmp_path)]) == 0
    gj = json.loads((tmp_path / ".svarupa" / "graph.json").read_text(encoding="utf8"))
    assert "layer-direction" not in [v["check"] for v in gj["health"]["violations"]]
    assert gj["design"]["exceptions"][0]["reason"] == "legacy"


def test_get_design_rules(tmp_path: Path, capsys) -> None:
    layered(tmp_path)
    assert main([str(tmp_path)]) == 0
    capsys.readouterr()
    assert main(["query", str(tmp_path / ".svarupa"), "get_design_rules", "--json"]) == 0
    rules = json.loads(capsys.readouterr().out)
    unit = rules["units"][0]
    assert unit["style"] == "layered"
    assert {"from": "service", "to": "api"} in unit["forbidden"]
    assert unit["parts"]["data"] == ["shop/repository"]


def test_excepted_imports_do_not_lower_conformance(tmp_path: Path, capsys) -> None:
    layered(tmp_path, back_edge=True)
    assert main(["design", str(tmp_path), "--accept"]) == 0
    path = tmp_path / ".svarupa" / "design.yaml"
    path.write_text(
        path.read_text(encoding="utf8").replace(
            "exceptions: []",
            "exceptions:\n- rule: layer-direction\n  from: shop/repository\n  to: shop/api\n  reason: legacy",
        ),
        encoding="utf8",
    )
    capsys.readouterr()
    assert main([str(tmp_path)]) == 0
    assert "conformance 100%" in capsys.readouterr().out
    gj = json.loads((tmp_path / ".svarupa" / "graph.json").read_text(encoding="utf8"))
    chosen = gj["design"]["units"][0]["chosen"]
    assert chosen["violations"] == []
    assert chosen["excepted"][0]["rule"] == "layer-direction"


def test_stale_design_notes_reach_the_cli(tmp_path: Path, capsys) -> None:
    layered(tmp_path)
    write(
        tmp_path,
        ".svarupa/design.yaml",
        "units:\n  .:\n    style: nope\n    parts: {}\nexceptions: []\n",
    )
    assert main([str(tmp_path)]) == 0
    assert "unknown style 'nope'" in capsys.readouterr().out


def test_an_accepted_design_that_checks_nothing_says_so(tmp_path: Path, capsys) -> None:
    layered(tmp_path)
    write(
        tmp_path,
        ".svarupa/design.yaml",
        "units:\n  .:\n    style: layered\n    parts: {api: [gone/a], data: [gone/b]}\nexceptions: []\n",
    )
    assert main([str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "nothing to check" in out and "conformance 100%" not in out


def test_accepting_again_keeps_the_teams_design(tmp_path: Path) -> None:
    import yaml

    layered(tmp_path)
    write(
        tmp_path,
        ".svarupa/design.yaml",
        "units:\n  .:\n    style: mvc\n    parts: {controller: [shop/api], model: [shop/repository]}\nexceptions: []\n",
    )
    assert main(["design", str(tmp_path), "--accept"]) == 0
    data = yaml.safe_load((tmp_path / ".svarupa" / "design.yaml").read_text(encoding="utf8"))
    assert data["units"]["."]["style"] == "mvc"
    assert data["units"]["."]["parts"]["controller"] == ["shop/api"]
