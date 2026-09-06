"""Wave C: the Graphify-class graph and the query surface over it.

graph.json schema 2: a typed `context` on every edge, routes and externals as
nodes, rationale nodes from docstrings and marker comments, `built_at_commit`,
a reserved `hyperedges` slot. `svarupa query` answers with exact matches,
explicit ambiguity lists and structured output, and exits 1 when it could not
answer.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from svarupa.cli import main
from svarupa.extract.rationale import rationale_facts
from svarupa.query import (
    GraphIndex,
    affected,
    get_neighbors,
    get_node,
    god_nodes,
    graph_stats,
    query_graph,
    shortest_path,
)
from svarupa.query.cli import query_main


def write(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf8")


def _repo(root: Path) -> None:
    write(root, "api/__init__.py", "")
    write(
        root,
        "api/routes.py",
        '"""HTTP surface of the shop.\n\nSecond paragraph is not the summary."""\n'
        "from fastapi import APIRouter\nfrom domain import orders\nrouter = APIRouter()\n\n"
        '@router.get("/orders")\ndef list_orders():\n    # NOTE: paginated in the client, not here\n'
        "    return orders.all()\n",
    )
    write(root, "domain/__init__.py", "")
    write(
        root,
        "domain/orders.py",
        "from store import db\n\n\nclass Orders:\n"
        '    """Order aggregate; owns the invariants."""\n\n    def all(self):\n        return db.q()\n\n\n'
        "def all():\n    return Orders().all()\n\n# TODO stale comment at module level\n",
    )
    write(root, "store/__init__.py", "")
    write(root, "store/db.py", "import psycopg2\n\n\ndef q():\n    return []\n")
    write(
        root, "vendor/lib.py", '"""Vendored; must not become a rationale."""\n# HACK vendored\n'
    )


@pytest.fixture
def artifact(tmp_path: Path) -> tuple[Path, dict[str, object]]:
    _repo(tmp_path)
    out = tmp_path / "out"
    assert main([str(tmp_path), "--out", str(out)]) == 0
    return out, json.loads((out / "graph.json").read_text(encoding="utf8"))


# --- graph.json schema 2 ---------------------------------------------------------


def test_graph_json_is_schema_2_with_context_on_every_edge(artifact) -> None:  # type: ignore[no-untyped-def]
    _, g = artifact
    assert g["schema"] == 2 and g["hyperedges"] == []
    assert g["built_at_commit"] is None, "a tmp directory is not a git checkout"
    edges = g["edges"]
    assert edges and all(e.get("context") for e in edges)
    contexts = {e["context"] for e in edges}
    assert {"import", "route", "store", "rationale"} <= contexts, contexts
    by_kind = {e["kind"]: e["context"] for e in edges}
    assert by_kind["imports"] == "import"


def test_routes_and_externals_are_nodes_with_their_lines(artifact) -> None:  # type: ignore[no-untyped-def]
    _, g = artifact
    nodes = {n["id"]: n for n in g["nodes"]}
    route = nodes["api/routes.py#route:GET /orders"]
    assert route["kind"] == "endpoint" and route["label"] == "GET /orders"
    assert route["evidence"][0] == {"file": "api/routes.py", "start_line": 8, "end_line": 8}
    assert route["attrs"]["framework"] == "fastapi"
    exposes = next(e for e in g["edges"] if e["dst"] == route["id"])
    assert exposes["src"] == "api/routes.py" and exposes["context"] == "route"
    ext = nodes["ext:database:PostgreSQL"]
    assert ext["kind"] == "datastore" and ext["attrs"]["packages"] == "psycopg2"
    store = next(e for e in g["edges"] if e["dst"] == "ext:database:PostgreSQL")
    assert store["src"] == "store/db.py" and store["context"] == "store"
    assert store["evidence"] == [{"file": "store/db.py", "start_line": 1, "end_line": 1}]


def test_rationale_nodes_cite_their_lines_and_attach_to_the_innermost_definition(
    artifact,
) -> None:  # type: ignore[no-untyped-def]
    _, g = artifact
    why = {n["qualified_name"]: n for n in g["nodes"] if n["kind"] == "rationale"}
    targets = {e["src"]: e["dst"] for e in g["edges"] if e["kind"] == "rationale_for"}
    # Module docstring: first paragraph line only, attached to the module.
    mod = why["api/routes.py:1"]
    assert mod["attrs"] == {"kind": "docstring", "text": "HTTP surface of the shop."}
    assert targets[mod["id"]] == "api/routes.py"
    # Class docstring attaches to the class node.
    cls = why["domain/orders.py:5"]
    assert cls["attrs"]["text"] == "Order aggregate; owns the invariants."
    assert targets[cls["id"]].endswith("#domain.orders.Orders")
    # A NOTE inside a function attaches to that function, not the module.
    note = why["api/routes.py:10"]
    assert note["attrs"] == {"kind": "note", "text": "paginated in the client, not here"}
    assert targets[note["id"]].endswith("#api.routes.list_orders")
    # A module-level TODO attaches to the module.
    todo = why["domain/orders.py:14"]
    assert todo["attrs"]["kind"] == "todo" and targets[todo["id"]] == "domain/orders.py"
    assert not any(q.startswith("vendor/") for q in why), "vendored text is not a rationale"
    assert all(
        n["evidence"][0]["file"] == n["qualified_name"].split(":")[0] for n in why.values()
    )


def test_rationale_scanner_handles_typescript_blocks_and_one_line_docstrings(
    tmp_path: Path,
) -> None:
    write(
        tmp_path,
        "a.ts",
        "/**\n * Client for the rates API.\n */\nexport const x = 1; // WHY: rates change hourly\n",
    )
    write(tmp_path, "b.py", '"""One line."""\nx = 1  # FIXME  \n')
    write(tmp_path, "c.py", '"""\nSummary on its own line.\nMore words below it.\n"""\n')
    facts = rationale_facts(tmp_path, {"a.ts", "b.py", "c.py"})
    got = {(f.file, f.line, f.kind, f.text) for f in facts}
    assert ("a.ts", 1, "docstring", "Client for the rates API.") in got
    assert ("a.ts", 4, "why", "rates change hourly") in got
    assert ("b.py", 1, "docstring", "One line.") in got
    assert ("c.py", 1, "docstring", "Summary on its own line.") in got, "the first line only"
    assert not any(f.kind == "fixme" for f in facts), "a marker with no text is not a rationale"


def test_built_at_commit_names_the_checkout(tmp_path: Path) -> None:
    _repo(tmp_path)
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "add", "."],
        cwd=tmp_path,
        check=True,
    )
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "x"],
        cwd=tmp_path,
        check=True,
    )
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=tmp_path, capture_output=True, text=True
    ).stdout.strip()
    out = tmp_path / "out"
    assert main([str(tmp_path), "--out", str(out)]) == 0
    g = json.loads((out / "graph.json").read_text(encoding="utf8"))
    assert g["built_at_commit"] == head and len(head) == 40


# --- the query functions ---------------------------------------------------------------


def test_exact_match_ambiguity_and_absence_are_three_different_answers(artifact) -> None:  # type: ignore[no-untyped-def]
    out, _ = artifact
    index = GraphIndex.load(out)
    hit = get_node(index, "store/db.py")
    assert hit["match"]["kind"] == "module" and hit["out_degree"] >= 1
    amb = get_node(index, "all")  # the function and the method both label "all"
    assert amb["match"] is None and len(amb["ambiguous"]) == 2
    assert {n["id"] for n in amb["ambiguous"]} == {
        "domain/orders.py#domain.orders.all",
        "domain/orders.py#domain.orders.Orders.all",
    }
    none = get_node(index, "to_html")
    assert none["match"] is None and "no node" in none["reason"] and "ambiguous" not in none
    partial = get_node(index, "order")  # a substring of Orders, orders.py, list_orders
    assert partial["match"] is None and "ambiguous" not in partial, (
        "no fuzzy or substring match"
    )
    assert get_node(index, "domain.orders.Orders")["match"]["id"].endswith(
        "#domain.orders.Orders"
    )


def test_neighbors_paths_blast_radius_and_stats(artifact) -> None:  # type: ignore[no-untyped-def]
    out, _ = artifact
    index = GraphIndex.load(out)
    nb = get_neighbors(index, "ext:database:PostgreSQL")
    assert nb["outgoing"] == [] and [x["node"]["id"] for x in nb["incoming"]] == ["store/db.py"]
    assert nb["incoming"][0]["edge"]["evidence"][0]["start_line"] == 1
    assert (
        get_neighbors(index, "store/db.py", relation="import")["incoming"][0]["node"]["id"]
        == "domain/orders.py"
    )
    assert get_neighbors(index, "store/db.py", relation="store")["incoming"] == []
    path = shortest_path(index, "api/routes.py", "ext:database:PostgreSQL")
    assert path["hops"] == 3 and [h["context"] for h in path["path"]] == [
        "import",
        "import",
        "store",
    ]
    assert shortest_path(index, "ext:database:PostgreSQL", "api/routes.py")["path"] is None
    assert (
        shortest_path(index, "ext:database:PostgreSQL", "api/routes.py", undirected=True)[
            "hops"
        ]
        == 3
    )
    assert (
        shortest_path(index, "api/routes.py", "ext:database:PostgreSQL", max_hops=2)["path"]
        is None
    )
    blast = affected(index, "store/db.py", depth=2)
    ids = {a["id"]: a["hops"] for a in blast["affected"]}
    assert ids["domain/orders.py"] == 1 and ids["api/routes.py"] == 2
    assert (
        affected(index, "store/db.py", relation="import", depth=1)["affected"][0]["id"]
        == "domain/orders.py"
    )
    top = god_nodes(index, top_n=2)["nodes"]
    assert len(top) == 2 and top[0]["degree"] >= top[1]["degree"]
    stats = graph_stats(index)
    assert stats["schema"] == 2 and stats["node_kinds"]["endpoint"] == 1
    assert stats["edge_contexts"]["rationale"] == 4 and stats["hyperedges"] == 0


def test_query_graph_is_keyword_search_that_says_so_and_announces_truncation(artifact) -> None:  # type: ignore[no-untyped-def]
    out, _ = artifact
    index = GraphIndex.load(out)
    r = query_graph(index, "who talks to postgres?", depth=1)
    assert r["words"] == ["postgres"], "stopwords and the question mark are dropped"
    assert "not semantic" in r["matching"]
    assert r["nodes"][0]["id"] == "ext:database:PostgreSQL" and r["nodes"][0]["hops"] == 0
    assert any(n["id"] == "store/db.py" and n["hops"] == 1 for n in r["nodes"])
    assert r["truncated"] is None
    small = query_graph(index, "orders", depth=1, token_budget=120)
    assert small["truncated"] and small["truncated"].startswith("TRUNCATED to 120 tokens")
    assert len(small["nodes"]) < small["hits"] + 1
    camel = query_graph(index, "listOrders", depth=0)
    assert next(n["id"] for n in camel["nodes"]).endswith("#api.routes.list_orders")


def test_query_cli_exit_codes_and_json(artifact, capsys: pytest.CaptureFixture[str]) -> None:  # type: ignore[no-untyped-def]
    out, _ = artifact
    assert main(["query", str(out), "get_node", "store/db.py"]) == 0
    assert '"' not in capsys.readouterr().out.splitlines()[0]
    assert main(["query", str(out), "get_node", "all", "--json"]) == 1, (
        "ambiguity is not an answer"
    )
    data = json.loads(capsys.readouterr().out)
    assert data["match"] is None and len(data["ambiguous"]) == 2
    assert (
        main(["query", str(out), "shortest_path", "ext:database:PostgreSQL", "api/routes.py"])
        == 1
    )
    capsys.readouterr()
    assert main(["query", str(out), "graph_stats"]) == 0
    assert main(["query", str(out / "graph.json"), "god_nodes", "--top", "1"]) == 0
    capsys.readouterr()
    assert query_main([str(out.parent), "graph_stats"]) == 1, "no graph.json here"
    assert "SVA-Q-001" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        query_main([str(out), "shortest_path", "only-one"])


# --- the explorer controls in the viewer ------------------------------------------------


def test_every_tab_carries_the_explorer_controls(artifact) -> None:  # type: ignore[no-untyped-def]
    """Search, kind toggles on the legend, clickable neighbours and the
    shift-click path tool (design section 4). The controls only toggle classes
    on the SVG the reader sees, so the artifact stays byte-deterministic and
    the picture never gains a claim the layout did not make."""
    out, _ = artifact
    html = (out / "index.html").read_text(encoding="utf8")
    import re

    diagram_tabs = re.findall(r'class="tab" id="d-(?!unavailable)', html)
    assert diagram_tabs, "the fixture produced no diagram tab"
    assert html.count('<div class="explore">') == len(diagram_tabs), (
        "one toolbar per diagram tab"
    )
    assert 'class="search" placeholder="find a box by id or label"' in html
    assert 'class="sv-kind-module sw-toggle" data-kind="module"' in html
    assert "li.setAttribute('data-target'" in html, "passport neighbours are clickable"
    assert "function pinPath(node)" in html and "ev.shiftKey" in html
    assert "svg.is-pinned .sv-node:not(.is-path)" in html
    assert "closest('.explore input')" in html
