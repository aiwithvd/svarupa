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
    assert gj["health"]["ratings"]["security"] == "A"
    assert gj["health"]["ratings"]["reliability"] == "A"
    assert gj["health"]["violations"][0]["check"] == "many-parameters"

    report = (tmp_path / ".svarupa" / "REPORT.md").read_text(encoding="utf8")
    assert "## Health" in report
    assert "| performance | A |" in report
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


def test_explanations_reach_graph_json_and_get_health(tmp_path: Path, capsys) -> None:
    make_repo(tmp_path)
    assert main([str(tmp_path)]) == 0
    capsys.readouterr()
    gj = json.loads((tmp_path / ".svarupa" / "graph.json").read_text(encoding="utf8"))
    checks = {c["id"]: c for c in gj["health"]["checks"]}
    assert checks["many-parameters"]["explanation"]["what"].startswith("This function takes")
    assert "python" in checks["many-parameters"]["explanation"]["examples"]
    assert "explanation" not in gj["health"]["violations"][0]
    assert main(["query", str(tmp_path / ".svarupa"), "get_health", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert {c["id"]: c for c in result["checks"]}["many-parameters"]["explanation"]["fix"]


def test_report_and_viewer_explain_the_checks(tmp_path: Path) -> None:
    make_repo(tmp_path)
    (tmp_path / "web").mkdir()
    (tmp_path / "web" / "m.js").write_text(
        "function f(a, b, c, d, e, g) {\n  return a;\n}\n", encoding="utf8"
    )
    assert main([str(tmp_path)]) == 0
    report = (tmp_path / ".svarupa" / "REPORT.md").read_text(encoding="utf8")
    assert "### What these mean" in report
    assert "#### Too many parameters (`many-parameters`)" in report
    assert report.count("#### Too many parameters") == 1
    assert "How to fix:" in report
    assert "```python" in report  # the first many-parameters violation is app/m.py
    html = (tmp_path / ".svarupa" / "index.html").read_text(encoding="utf8")
    assert 'id="why-many-parameters"' in html
    assert html.count('id="why-many-parameters"') == 1
    assert 'data-why="many-parameters"' in html
    assert "This function takes more than 5 parameters." in html


def test_a_javascript_finding_shows_the_typescript_example(tmp_path: Path) -> None:
    (tmp_path / "web").mkdir()
    (tmp_path / "web" / "m.js").write_text(
        "function f(a, b, c, d, e, g) {\n  return a;\n}\n", encoding="utf8"
    )
    assert main([str(tmp_path)]) == 0
    report = (tmp_path / ".svarupa" / "REPORT.md").read_text(encoding="utf8")
    assert "```javascript" in report and "interface Address" in report
