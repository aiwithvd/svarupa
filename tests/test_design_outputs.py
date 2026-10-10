"""Design reaches health and every output."""

from __future__ import annotations

import json
from pathlib import Path

from svarupa.cli import main
from tests.test_design import layered


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
