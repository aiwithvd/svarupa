"""Wave D: the two Archify surfaces around the canvas, computed.

Cards are facts from the graph with citations; guided views are chapters from
the root spec, each a box plus what the drawn arrows connect to it. Nothing
is authored, nothing is fabricated to fill a slot: an empty card says so.
"""

from __future__ import annotations

import re
from pathlib import Path

from svarupa.build import Graph, build
from svarupa.cli import main
from svarupa.cluster import cluster
from svarupa.derive import derive_all
from svarupa.derive.base import DiagramKind
from svarupa.detect import detect
from svarupa.emit.cards import (
    MAX_CHAPTERS,
    Card,
    cards_for,
    chapters_for,
    render_cards,
    render_guided,
)
from svarupa.extract import declared_dependencies, extract


def write(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf8")


def _repo(root: Path) -> None:
    write(root, "api/__init__.py", "")
    write(
        root,
        "api/routes.py",
        "from fastapi import APIRouter\nfrom domain import orders\nimport openai\nrouter = APIRouter()\n\n"
        '@router.get("/orders")\ndef a():\n    pass\n\n@router.post("/orders")\ndef b():\n    pass\n',
    )
    write(root, "domain/__init__.py", "")
    write(root, "domain/orders.py", "from store import db\n")
    write(root, "store/__init__.py", "")
    write(root, "store/db.py", "import psycopg2\nimport redis\n")
    write(
        root,
        "pyproject.toml",
        '[project]\nname = "shop"\n\n[project.scripts]\nshop = "api.routes:a"\n',
    )
    write(
        root,
        "tests/test_x.py",
        "from fastapi import APIRouter\nimport boto3\nr = APIRouter()\n\n@r.get('/t')\ndef t():\n    pass\n",
    )


def graph_of(root: Path) -> Graph:
    scan = detect(root)
    return build(scan, extract(scan, declared_dependencies(scan)), strict=False)


def test_cards_are_computed_facts_with_citations(tmp_path: Path) -> None:
    _repo(tmp_path)
    g = graph_of(tmp_path)
    cards = {c.title: c for c in cards_for(g)}
    entry = cards["Entry points"]
    assert entry.items[0].text == "api: 2 routes · GET /orders, POST /orders"
    assert {(e.file, e.start_line) for e in entry.items[0].evidence} == {
        ("api/routes.py", 6),
        ("api/routes.py", 10),
    }
    assert any(i.text.startswith("shop → api.routes:a") for i in entry.items), (
        "declared entrypoints"
    )
    assert not any("/t" in i.text for i in entry.items), (
        "a test file's route is not an entry point"
    )
    stores = cards["Data stores"]
    assert [i.text for i in stores.items] == ["PostgreSQL via psycopg2", "Redis via redis"]
    assert stores.items[0].evidence[0].file == "store/db.py"
    apis = cards["External APIs"]
    assert [i.text for i in apis.items] == ["OpenAI API via openai"], (
        "boto3 in a test file counts for nothing"
    )
    assert cards["Unresolved"].empty.startswith("every reference")


def test_chapters_are_api_modules_with_their_neighbours(tmp_path: Path) -> None:
    _repo(tmp_path)
    g = graph_of(tmp_path)
    produced, _ = derive_all(g, cluster(g))
    flow = produced[DiagramKind.DATA_FLOW].root_spec
    chapters = chapters_for(flow)
    by_anchor = {c.anchor: c for c in chapters}
    assert "in:api" in by_anchor and "api" in by_anchor, (
        "ingress and handler both lead a chapter"
    )
    assert set(by_anchor["api"].focus) >= {"api", "in:api", "domain"}, by_anchor["api"].focus
    assert by_anchor["api"].title == "api" and len(chapters) <= MAX_CHAPTERS
    # The architecture root is grouped, so chapters fall back to the most
    # connected boxes; each chapter still starts from a box it names.
    arch = chapters_for(produced[DiagramKind.ARCHITECTURE].root_spec)
    assert arch and all(c.anchor in c.focus for c in arch)
    ids = {n.id for n in produced[DiagramKind.ARCHITECTURE].root_spec.nodes}
    assert all(set(c.focus) <= ids for c in arch), "a chapter lights only boxes in its view"


def test_chapters_star_built_services_not_only_image_only_ones() -> None:
    """The System view types a built service by its code (backend, frontend,
    security), so a chapter rule keyed on `service` skipped exactly the
    services with a story and told grafana's instead (review #20 S8)."""
    from svarupa.derive.base import DiagramEdge, DiagramNode, DiagramSpec
    from svarupa.model import Evidence

    ev = (Evidence(file="docker-compose.yml", start_line=1, end_line=1),)
    nodes = tuple(
        DiagramNode(id=i, label=i, kind=k, evidence=ev)
        for i, k in (
            ("app", "backend"),
            ("db", "database"),
            ("grafana", "service"),
            ("ui", "frontend"),
        )
    )
    edges = (
        DiagramEdge(src="app", dst="db", label="", evidence=ev),
        DiagramEdge(src="ui", dst="app", label="", evidence=ev),
        DiagramEdge(src="grafana", dst="db", label="", evidence=ev),
    )
    spec = DiagramSpec(
        kind=DiagramKind.DEPLOY_TOPOLOGY, id="/spec/root", title="t", nodes=nodes, edges=edges
    )
    anchors = [c.anchor for c in chapters_for(spec)]
    assert anchors[0] == "app", anchors
    assert set(anchors) == {"app", "grafana", "ui"}, "stores do not lead a chapter"


def test_viewer_carries_the_strip_and_the_cards_once_per_tab(tmp_path: Path) -> None:
    _repo(tmp_path)
    out = tmp_path / "out"
    assert main([str(tmp_path), "--out", str(out)]) == 0
    html = (out / "index.html").read_text(encoding="utf8")
    tabs = len(re.findall(r'class="tab" id="d-(?!unavailable)', html))
    strips = html.count('<div class="guided"')
    # Every root has arrows here: the one request story is the request-flow
    # root itself (review #20 C10), so it has a strip like the others. A
    # root of boxes with no arrows gets none (review #19 F9), pinned below.
    assert strips == tabs, (strips, tabs)
    request_tab = html.split('id="d-request-flow"')[1].split('class="tab"')[0]
    assert '<div class="guided"' in request_tab
    assert '<div class="guided"' not in str(render_guided((), "/spec/root"))
    assert html.count('<div class="cards">') == tabs, "cards under every root view only"
    assert 'class="chapter"' in html and 'data-anchor="api" data-focus="' in html
    assert "Guided views " in html and "Explore this system" in html
    assert 'data-kind="fact"' in html and "SRC 2" in html
    empty = str(render_cards((Card("Nothing here", "cyan", (), 0, "nothing was found"),)))
    assert 'class="card-empty"' in empty and "nothing was found" in empty, (
        "an empty card says so instead of vanishing"
    )
    assert "function openChapter(li)" in html and "Play story" in html


def test_cards_pin_what_the_review_found_unpinned(tmp_path: Path) -> None:
    """Review #19 F7: entrypoint eligibility, unresolved samples, chapter box
    counts, chapter ranking and message buses were all mutable with the suite
    green."""
    _repo(tmp_path)
    write(tmp_path, "store/bus.py", "import pika\n")
    write(
        tmp_path, "tests/fixtures/package.json", '{"name": "x", "bin": {"decoy": "./d.js"}}\n'
    )
    g = graph_of(tmp_path)
    cards = {c.title: c for c in cards_for(g)}
    stores = [i.text for i in cards["Data stores"].items]
    assert "RabbitMQ via pika" in stores, stores
    assert not any("decoy" in i.text for i in cards["Entry points"].items), (
        "a fixture manifest under tests/ mints no entry point"
    )
    unresolved = cards["Unresolved"]
    assert unresolved.items, "the fixture has unresolved calls (orders.all, db.q)"
    assert all("e.g. " in i.text for i in unresolved.items), "samples are shown"
    assert not any(":" in i.text.split("e.g. ", 1)[1] for i in unresolved.items), (
        "the scorecard's bucket tag is not shown"
    )
    produced, _ = derive_all(g, cluster(g))
    flow = produced[DiagramKind.DATA_FLOW].root_spec
    chapters = chapters_for(flow)
    degree: dict[str, int] = {}
    for e in flow.edges:
        degree[e.src] = degree.get(e.src, 0) + 1
        degree[e.dst] = degree.get(e.dst, 0) + 1
    ranks = [degree[c.anchor] for c in chapters]
    assert ranks == sorted(ranks, reverse=True), "chapters lead with the most connected box"
    for c in chapters:
        neighbours = {e.dst for e in flow.edges if e.src == c.anchor} | {
            e.src for e in flow.edges if e.dst == c.anchor
        }
        assert set(c.focus) == {c.anchor} | neighbours, (c.anchor, c.focus)
        assert degree[c.anchor] > 0
    html = str(render_guided_for_test(chapters))
    assert " box</span>" not in html or "1 box<" in html


def test_chapters_rank_by_connections_not_by_name() -> None:
    from svarupa.derive.base import DiagramEdge, DiagramKind, DiagramNode, DiagramSpec
    from svarupa.model import Evidence

    ev = (Evidence(file="a.py", start_line=1, end_line=1),)

    def node(nid: str) -> DiagramNode:
        return DiagramNode(id=nid, label=nid, kind="endpoint", evidence=ev)

    spec = DiagramSpec(
        kind=DiagramKind.DATA_FLOW,
        id="/spec/root",
        title="t",
        nodes=(node("alpha"), node("beta"), node("gamma"), node("delta")),
        edges=(
            DiagramEdge(src="beta", dst="gamma", label="", evidence=ev),
            DiagramEdge(src="beta", dst="delta", label="", evidence=ev),
            DiagramEdge(src="beta", dst="alpha", label="", evidence=ev),
            DiagramEdge(src="gamma", dst="delta", label="", evidence=ev),
        ),
    )
    chapters = chapters_for(spec)
    assert [c.anchor for c in chapters][:2] == ["beta", "delta"], (
        "most connected first, then id"
    )
    assert chapters[0].focus == ("beta", "alpha", "delta", "gamma")
    assert all(len(c.focus) >= 2 for c in chapters), "a chapter has a story"


def test_frames_name_their_members_for_the_passport(tmp_path: Path) -> None:
    _repo(tmp_path)
    out = tmp_path / "out"
    assert main([str(tmp_path), "--out", str(out)]) == 0
    html = (out / "index.html").read_text(encoding="utf8")
    frames = re.findall(r'class="sv-boundary[^"]*"[^>]*data-members="([^"]*)"', html)
    assert frames, "stage frames carry data-members"
    assert any("api" in f.split("&#10;") or "api" in f.split("\n") for f in frames), frames[:3]


def render_guided_for_test(chapters):  # type: ignore[no-untyped-def]
    from svarupa.emit.cards import render_guided

    return render_guided(chapters, "/spec/root")
