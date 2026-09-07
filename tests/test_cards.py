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
from svarupa.emit.cards import MAX_CHAPTERS, Card, cards_for, chapters_for, render_cards
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


def test_viewer_carries_the_strip_and_the_cards_once_per_tab(tmp_path: Path) -> None:
    _repo(tmp_path)
    out = tmp_path / "out"
    assert main([str(tmp_path), "--out", str(out)]) == 0
    html = (out / "index.html").read_text(encoding="utf8")
    tabs = len(re.findall(r'class="tab" id="d-(?!unavailable)', html))
    assert html.count('<div class="guided"') == tabs, "one strip per diagram tab"
    assert html.count('<div class="cards">') == tabs, "cards under every root view only"
    assert 'class="chapter" data-anchor="api" data-focus="' in html
    assert "Guided views " in html and "Explore this system" in html
    assert 'data-kind="fact"' in html and "SRC 2" in html
    empty = str(render_cards((Card("Nothing here", "cyan", (), 0, "nothing was found"),)))
    assert 'class="card-empty"' in empty and "nothing was found" in empty, (
        "an empty card says so instead of vanishing"
    )
    assert "function openChapter(li)" in html and "Play story" in html
