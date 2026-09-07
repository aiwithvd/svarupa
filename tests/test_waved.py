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
    # Literal numbers, not the module's constants: a test that imports the
    # value it checks cannot fail when the value changes (review #19 F8).
    assert (CHROME_W, CHROME_H) == (57, 290), "measured in a 1440x900 viewport on the demo"
    assert fits(_canvas(800, 400)) == VIEWPORTS
    assert fits(_canvas(1383, 610)) == VIEWPORTS
    assert fits(_canvas(1384, 500)) == VIEWPORTS[1:]
    assert fits(_canvas(1383, 611)) == VIEWPORTS[1:]
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
    assert "| Canvas fits |" in report and "*Canvas fits*" in report
    assert "| scrolls |" in report
    assert "`SVA-G-016`" in report and "fits none of the four reference viewports" in report
    diagnostics = report.split("## Diagnostics")[1]
    errors_and_warnings = diagnostics.split("### Informational")[0]
    assert "SVA-G-016" not in errors_and_warnings, (
        "a scrolling root is information, never a warning"
    )
    assert "`SVA-G-016`" in diagnostics.split("### Informational")[1]


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
    write(tmp_path, "web/__init__.py", "")
    write(tmp_path, "web/app.py", "from api import routes\n")  # depth 2 from store/db.py
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
    deep = payload(
        asyncio.run(server.call_tool("affected", {"label": "store/db.py", "depth": 2}))
    )
    shallow = payload(
        asyncio.run(server.call_tool("affected", {"label": "store/db.py", "depth": 1}))
    )
    clamped = payload(
        asyncio.run(server.call_tool("affected", {"label": "store/db.py", "depth": -5}))
    )
    assert len(deep["affected"]) > len(shallow["affected"]) > 0, "depth is plumbed through"  # type: ignore[arg-type]
    assert clamped["affected"] == [] and clamped["depth"] == 0, "a negative depth is clamped"
    forward = payload(
        asyncio.run(
            server.call_tool(
                "shortest_path", {"source": "store/db.py", "target": "api/routes.py"}
            )
        )
    )
    both = payload(
        asyncio.run(
            server.call_tool(
                "shortest_path",
                {"source": "store/db.py", "target": "api/routes.py", "undirected": True},
            )
        )
    )
    assert forward["path"] is None and both["path"], "undirected is plumbed through"


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


def _route(src: str, dst: str, *pts: tuple[int, int]):  # type: ignore[no-untyped-def]
    from svarupa.layout.geometry import Route
    from svarupa.model import Evidence

    return Route(
        src=src,
        dst=dst,
        label="",
        points=tuple(pts),
        evidence=(Evidence(file="a", start_line=1, end_line=1),),
    )


def _boxes(*ids: str):  # type: ignore[no-untyped-def]
    from svarupa.layout.geometry import Box
    from svarupa.model import Evidence

    ev = (Evidence(file="a", start_line=1, end_line=1),)
    return tuple(
        Box(
            id=i,
            label=i,
            full_label=i,
            kind="module",
            x=40 + 200 * k,
            y=40,
            w=96,
            h=44,
            evidence=ev,
        )
        for k, i in enumerate(ids)
    )


def test_the_overlap_gate_exempts_a_mutual_pair_and_tolerates_no_pixels() -> None:
    from svarupa.layout import validate
    from svarupa.layout.geometry import Canvas, Style

    def canvas(routes):  # type: ignore[no-untyped-def]
        from svarupa.derive.base import DiagramKind

        return Canvas(
            spec_id="/spec/root",
            kind=DiagramKind.MODULE_DEPS,
            engine="layered",
            title="t",
            subtitle="",
            width=800,
            height=400,
            boxes=_boxes("a", "b", "c", "d"),
            routes=tuple(routes),
        )

    mutual = canvas(
        [
            _route("a", "b", (136, 62), (170, 62), (170, 300), (240, 300)),
            _route("b", "a", (240, 310), (170, 310), (170, 62), (136, 62)),
        ]
    )
    assert not [d for d in validate(mutual, Style()) if d.code == "SVA-G-015"], (
        "a mutual pair may share"
    )
    two_px = canvas(
        [
            _route("a", "b", (136, 62), (170, 62), (170, 302), (240, 302)),
            _route("c", "d", (536, 400), (170, 400), (170, 300), (640, 300)),
        ]
    )
    found = [d for d in validate(two_px, Style()) if d.code == "SVA-G-015"]
    assert found and "for 2px" in found[0].message, (
        "two pixels of a different edge is still a shared line"
    )


def test_flow_exits_are_even_and_entries_odd_so_they_never_meet() -> None:
    from svarupa.derive.base import DiagramEdge, DiagramKind, DiagramNode, DiagramSpec
    from svarupa.layout import lay_out
    from svarupa.layout.geometry import Style
    from svarupa.model import Evidence

    ev = (Evidence(file="a.py", start_line=1, end_line=1),)

    def node(nid: str, layer: str) -> DiagramNode:
        return DiagramNode(
            id=nid, label=nid, kind="module", evidence=ev, attrs=(("layer", layer),)
        )

    spec = DiagramSpec(
        kind=DiagramKind.DEPLOY_TOPOLOGY,
        id="/spec/root",
        title="t",
        nodes=(node("a", "0"), node("b", "0"), node("c", "1"), node("d", "1")),
        edges=(
            DiagramEdge(src="a", dst="d", label="", evidence=ev),
            DiagramEdge(src="b", dst="c", label="", evidence=ev),
        ),
    )
    canvas = lay_out(spec, Style(), "flow")
    for r in canvas.routes:
        assert r.points[0][1] % 2 == 0, ("exits are even", r.src, r.points[0])
        assert r.points[-1][1] % 2 == 1, ("left-side entries are odd", r.dst, r.points[-1])


def test_a_corridor_verb_settles_beside_a_box_not_on_the_top_lane() -> None:
    from svarupa.derive.base import DiagramEdge, DiagramKind, DiagramNode, DiagramSpec
    from svarupa.layout import lay_out
    from svarupa.layout.geometry import Style
    from svarupa.model import Evidence

    ev = (Evidence(file="a.py", start_line=1, end_line=1),)

    def node(nid: str, layer: str) -> DiagramNode:
        return DiagramNode(
            id=nid, label=nid, kind="module", evidence=ev, attrs=(("layer", layer),)
        )

    # Two boxes per column so rows align and the skip must take the corridor.
    spec = DiagramSpec(
        kind=DiagramKind.DEPLOY_TOPOLOGY,
        id="/spec/root",
        title="t",
        nodes=(
            node("a1", "0"),
            node("a2", "0"),
            node("m1", "1"),
            node("m2", "1"),
            node("x", "2"),
        ),
        edges=(
            DiagramEdge(src="a1", dst="m1", label="", evidence=ev),
            DiagramEdge(src="a2", dst="m2", label="", evidence=ev),
            DiagramEdge(src="m2", dst="x", label="", evidence=ev),
            DiagramEdge(src="a1", dst="x", label="calls", evidence=ev),
        ),
    )
    canvas = lay_out(spec, Style(), "flow")
    skip = next(r for r in canvas.routes if (r.src, r.dst) == ("a1", "x"))
    assert len(skip.points) == 6, "the fixture must put the verb on a corridor edge"
    assert skip.label_at is not None, "the verb found a clear place"
    lane_y = min(y for _, y in skip.points)
    assert skip.label_at[1] != lane_y, "the verb is not on the top lane"
    a1, x = canvas.box("a1"), canvas.box("x")
    assert a1 is not None and x is not None
    near_source = abs(skip.label_at[0] - a1.right) <= 80
    near_target = abs(skip.label_at[0] - x.x) <= 80
    assert near_source or near_target, ("the verb sits near one of its boxes", skip.label_at)


def test_route_counts_count_routes_not_distinct_paths(tmp_path: Path) -> None:
    from svarupa.emit.cards import cards_for

    write(tmp_path, "api/__init__.py", "")
    write(
        tmp_path,
        "api/a.py",
        "from fastapi import APIRouter\nr = APIRouter()\n\n@r.get('/same')\ndef a():\n    pass\n",
    )
    write(
        tmp_path,
        "api/b.py",
        "from fastapi import APIRouter\nr = APIRouter()\n\n@r.get('/same')\ndef b():\n    pass\n",
    )
    from svarupa.build import build
    from svarupa.detect import detect
    from svarupa.extract import declared_dependencies, extract

    scan = detect(tmp_path)
    g = build(scan, extract(scan, declared_dependencies(scan)), strict=False)
    entry = {c.title: c for c in cards_for(g)}["Entry points"]
    assert entry.items[0].text.startswith("api: 2 routes · GET /same"), entry.items[0].text
    assert len(entry.items[0].evidence) == 2


def test_flow_same_column_and_backward_edges_take_different_verticals() -> None:
    """Review #19 F3: a same-column edge and a backward edge into the same
    column drew from two counters and shared a vertical; SVA-G-015 then
    withheld a legitimate view."""
    from svarupa.derive.base import DiagramEdge, DiagramKind, DiagramNode, DiagramSpec
    from svarupa.layout import lay_out, validate
    from svarupa.layout.geometry import Style
    from svarupa.model import Evidence

    ev = (Evidence(file="a.py", start_line=1, end_line=1),)

    def node(nid: str, layer: str) -> DiagramNode:
        return DiagramNode(
            id=nid, label=nid, kind="module", evidence=ev, attrs=(("layer", layer),)
        )

    spec = DiagramSpec(
        kind=DiagramKind.DEPLOY_TOPOLOGY,
        id="/spec/root",
        title="t",
        nodes=(node("a", "1"), node("b", "0"), node("c", "0")),
        edges=(
            DiagramEdge(src="a", dst="b", label="", evidence=ev),
            DiagramEdge(src="a", dst="c", label="", evidence=ev),
            DiagramEdge(src="b", dst="c", label="", evidence=ev),
        ),
    )
    canvas = lay_out(spec, Style(), "flow")
    assert validate(canvas, Style()) == (), [d.render() for d in validate(canvas, Style())]


def test_grid_routes_a_backward_edge_round_the_lane() -> None:
    """Review #19 F4: an ERD of three rows with a foreign key two rows up was
    withheld because the reversed edge ran straight through the rows between."""
    from svarupa.derive.base import DiagramEdge, DiagramKind, DiagramNode, DiagramSpec
    from svarupa.layout import lay_out, validate
    from svarupa.layout.geometry import Style
    from svarupa.model import Evidence

    ev = (Evidence(file="schema.sql", start_line=1, end_line=1),)
    nodes = tuple(
        DiagramNode(id=f"t{i:02d}", label=f"t{i:02d}", kind="table", evidence=ev)
        for i in range(30)
    )
    spec = DiagramSpec(
        kind=DiagramKind.ERD,
        id="/spec/root",
        title="t",
        nodes=nodes,
        edges=(
            DiagramEdge(src="t29", dst="t00", label="fk", evidence=ev),
            DiagramEdge(src="t20", dst="t05", label="fk", evidence=ev),
            DiagramEdge(src="t00", dst="t29", label="fk", evidence=ev),
        ),
    )
    canvas = lay_out(spec, Style(), "grid")
    problems = validate(canvas, Style())
    assert problems == (), [d.render() for d in problems]
    back = next(r for r in canvas.routes if (r.src, r.dst) == ("t29", "t00"))
    assert max(x for x, _ in back.points) >= max(b.right for b in canvas.boxes), (
        "the backward edge runs round the side lane"
    )


def _skipping_lattice(width: int, depth: int):  # type: ignore[no-untyped-def]
    """A layered graph whose skipping edges force waypoint columns to line up
    across gaps: the shape where the channel-routing vertical constraint
    decides whether two chains share a vertical (review #19 F7, M23)."""
    from svarupa.derive.base import DiagramEdge, DiagramKind, DiagramNode, DiagramSpec
    from svarupa.model import Evidence

    ev = (Evidence(file="a.py", start_line=1, end_line=1),)

    def node(nid: str, layer: str) -> DiagramNode:
        return DiagramNode(
            id=nid, label=nid, kind="module", evidence=ev, attrs=(("layer", layer),)
        )

    nodes = [node(f"n{lv}{i}", str(lv)) for lv in range(depth) for i in range(width)]
    edges = []
    for lv in range(depth - 2):
        for i in range(width):
            edges.append(
                DiagramEdge(
                    src=f"n{lv}{i}", dst=f"n{lv + 2}{(i + 1) % width}", label="", evidence=ev
                )
            )
            edges.append(
                DiagramEdge(
                    src=f"n{lv}{i}", dst=f"n{lv + 2}{(i + 2) % width}", label="", evidence=ev
                )
            )
    for lv in range(depth - 1):
        for i in range(width):
            edges.append(
                DiagramEdge(
                    src=f"n{lv}{i}", dst=f"n{lv + 1}{(i + 3) % width}", label="", evidence=ev
                )
            )
    return DiagramSpec(
        kind=DiagramKind.MODULE_DEPS,
        id="/spec/root",
        title="t",
        nodes=tuple(nodes),
        edges=tuple(edges),
    )


@pytest.mark.parametrize(("width", "depth"), [(4, 4), (5, 5), (6, 4), (7, 6), (3, 6)])
def test_layered_tracks_obey_the_vertical_constraint(width: int, depth: int) -> None:
    from svarupa.layout import lay_out, validate
    from svarupa.layout.geometry import Style

    # (5, 5) is the shape where two chains cross inside one gap, each with a
    # terminal on the other's column: a constraint cycle that only a dogleg
    # resolves. Exits are 0 mod 4, entries 2 mod 4, waypoints odd.
    canvas = lay_out(_skipping_lattice(width, depth), Style(), "layered")
    problems = [d for d in validate(canvas, Style()) if d.severity.value == "ERROR"]
    assert problems == [], [d.render() for d in problems]
    for r in canvas.routes:
        assert r.points[0][0] % 4 == 0, ("exits are 0 mod 4", r.src, r.points[0])
        assert r.points[-1][0] % 4 == 2, ("entries are 2 mod 4", r.dst, r.points[-1])
