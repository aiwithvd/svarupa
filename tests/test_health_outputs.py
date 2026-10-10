"""Health reaches every output: graph.json, REPORT.md, the viewer, query, CLI."""

from __future__ import annotations

import json
from pathlib import Path

from svarupa.cli import main


def make_repo(root: Path) -> None:
    (root / "app").mkdir(parents=True)
    (root / "app" / "m.py").write_text(
        "def args(a, b, c, d, e, f):\n    return a\n", encoding="utf8"
    )


def test_cli_writes_health_everywhere(tmp_path: Path, capsys) -> None:
    make_repo(tmp_path)
    assert main([str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "health: maintainability B" in out
    assert "many-parameters" in out

    gj = json.loads((tmp_path / ".svarupa" / "graph.json").read_text(encoding="utf8"))
    assert gj["health"]["ratings"]["maintainability"] == "B"
    assert gj["health"]["ratings"]["security"] is None
    assert gj["health"]["violations"][0]["check"] == "many-parameters"

    report = (tmp_path / ".svarupa" / "REPORT.md").read_text(encoding="utf8")
    assert "## Health" in report
    assert "not assessed yet" in report
    assert "app/m.py:1" in report

    html = (tmp_path / ".svarupa" / "index.html").read_text(encoding="utf8")
    assert 'id="d-health"' in html and 'href="#d-health"' in html


def test_get_health_query(tmp_path: Path, capsys) -> None:
    make_repo(tmp_path)
    assert main([str(tmp_path)]) == 0
    capsys.readouterr()
    assert main(["query", str(tmp_path / ".svarupa"), "get_health", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["ratings"]["maintainability"] == "B"
    assert result["violations"][0]["check"] == "many-parameters"
