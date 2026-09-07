"""Wave D and E: the containment check, export, and the MCP server.

The containment check is Archify's four-viewport gate as arithmetic on the
canvas, an INFO because wide views scroll at natural size by decision. Export
is the open view's SVG with the stylesheet inlined. The MCP server is the
query CLI's seven functions under Graphify's names, one implementation.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from svarupa.cli import main
from svarupa.diagnostics import DiagnosticError
from svarupa.layout import CHROME_H, CHROME_W, VIEWPORTS, fits
from svarupa.layout.geometry import Canvas
from svarupa.query import GraphIndex


def write(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf8")


def _canvas(w: int, h: int) -> Canvas:
    from svarupa.derive.base import DiagramKind

    return Canvas(
        spec_id="/spec/root",
        kind=DiagramKind.MODULE_DEPS,
        engine="layered",
        title="t",
        subtitle="",
        width=w,
        height=h,
        boxes=(),
        routes=(),
    )


def test_fits_measures_the_canvas_against_the_four_viewports() -> None:
    assert fits(_canvas(800, 400)) == VIEWPORTS
    assert fits(_canvas(1440 - CHROME_W, 900 - CHROME_H)) == VIEWPORTS
    assert fits(_canvas(1441 - CHROME_W, 500)) == VIEWPORTS[1:]
    assert fits(_canvas(3000, 500)) == ()
    assert fits(_canvas(500, 3000)) == ()


def test_a_scrolling_root_is_an_info_in_the_report_not_a_failure(tmp_path: Path) -> None:
    # Forty unrelated modules: a wide root view that fits no viewport.
    for i in range(40):
        write(tmp_path, f"m{i:02d}/__init__.py", "")
        write(tmp_path, f"m{i:02d}/a.py", f"x = {i}\n")
    out = tmp_path / "out"
    assert main([str(tmp_path), "--out", str(out)]) == 0
    report = (out / "REPORT.md").read_text(encoding="utf8")
    assert "| Root fits |" in report
    assert "| scrolls |" in report
    assert "`SVA-G-016`" in report and "fits none of the four reference viewports" in report


def test_a_small_root_names_the_smallest_viewport_it_fits(tmp_path: Path) -> None:
    write(tmp_path, "a/__init__.py", "")
    write(tmp_path, "a/x.py", "from b import y\n")
    write(tmp_path, "b/__init__.py", "")
    write(tmp_path, "b/y.py", "z = 1\n")
    out = tmp_path / "out"
    assert main([str(tmp_path), "--out", str(out)]) == 0
    report = (out / "REPORT.md").read_text(encoding="utf8")
    assert "| 1440x900 |" in report and "SVA-G-016" not in report


def test_export_buttons_and_serialiser_ship(tmp_path: Path) -> None:
    write(tmp_path, "a/__init__.py", "")
    write(tmp_path, "a/x.py", "x = 1\n")
    out = tmp_path / "out"
    assert main([str(tmp_path), "--out", str(out)]) == 0
    html = (out / "index.html").read_text(encoding="utf8")
    assert 'data-export="svg"' in html and 'data-export="png"' in html
    assert "new XMLSerializer().serializeToString(copy)" in html, "serialised, not string-built"
    assert "copy.setAttribute('data-theme'" in html, "the theme travels with the export"


# --- MCP ---------------------------------------------------------------------------------


def _artifact(tmp_path: Path) -> Path:
    write(tmp_path, "api/__init__.py", "")
    write(
        tmp_path,
        "api/routes.py",
        "from fastapi import APIRouter\nfrom store import db\nr = APIRouter()\n\n@r.get('/x')\ndef x():\n    pass\n",
    )
    write(tmp_path, "store/__init__.py", "")
    write(tmp_path, "store/db.py", "import psycopg2\n")
    out = tmp_path / "out"
    assert main([str(tmp_path), "--out", str(out)]) == 0
    return out


def test_mcp_server_exposes_graphifys_seven_tools_over_run_query(tmp_path: Path) -> None:
    pytest.importorskip("mcp")
    from svarupa.query.mcp_server import build_server

    out = _artifact(tmp_path)
    server = build_server(GraphIndex.load(out))
    tools = asyncio.run(server.list_tools())
    names = sorted(t.name for t in tools)
    assert names == [
        "affected",
        "get_neighbors",
        "get_node",
        "god_nodes",
        "graph_stats",
        "query_graph",
        "shortest_path",
    ]

    def payload(result: object) -> dict[str, object]:
        structured = getattr(result, "structured_content", None)
        if isinstance(structured, dict):
            return structured  # type: ignore[return-value]
        content = getattr(result, "content", None)
        assert content, result
        return json.loads(content[0].text)  # type: ignore[index]

    got = payload(asyncio.run(server.call_tool("get_node", {"label": "store/db.py"})))
    assert got["matched_by"] == "id" and got["match"]["id"] == "store/db.py"  # type: ignore[index]
    none = payload(asyncio.run(server.call_tool("get_node", {"label": "nothing_here"})))
    assert none["match"] is None and "reason" in none, "the MCP answer is the CLI answer"


def test_mcp_refuses_without_the_sdk_and_without_a_graph(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import builtins

    from svarupa.query import mcp_server

    real_import = builtins.__import__

    def no_mcp(name: str, *args: object, **kwargs: object) -> object:
        if name.startswith("mcp"):
            raise ImportError("no mcp")
        return real_import(name, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(builtins, "__import__", no_mcp)
    with pytest.raises(DiagnosticError) as exc:
        mcp_server._sdk()
    assert exc.value.diagnostic.code == "SVA-Q-002"
    monkeypatch.undo()
    assert main(["mcp", str(tmp_path)]) == 1, "no graph.json here refuses with SVA-Q-001"
