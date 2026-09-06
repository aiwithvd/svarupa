"""Wave B: data flow and request flow, derived from routes, imports and the
vocabulary, drawn in stages; lifecycle and workflow named as not derivable.

The honesty rules: every stage member cites; a module reachable from no
handler is not drawn and the count is reported; request flow is reachability
in hops, never sequence notation; the two undrawable types say why.
"""

from __future__ import annotations

from pathlib import Path

from svarupa.build import Graph, build
from svarupa.cli import main
from svarupa.cluster import cluster
from svarupa.derive import NOT_DERIVABLE, derive_all
from svarupa.derive.base import DiagramKind
from svarupa.detect import detect
from svarupa.extract import declared_dependencies, extract
from svarupa.layout import lay_out_set
from svarupa.layout.geometry import Style


def write(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf8")


def graph_of(root: Path) -> Graph:
    scan = detect(root)
    return build(scan, extract(scan, declared_dependencies(scan)), strict=False)


def _service(root: Path) -> None:
    write(root, "api/__init__.py", "")
    write(
        root,
        "api/routes.py",
        "from fastapi import APIRouter\nfrom domain import orders\nrouter = APIRouter()\n\n"
        '@router.get("/orders")\ndef list_orders():\n    return orders.all()\n\n'
        '@router.post("/orders")\ndef create():\n    return 1\n',
    )
    write(root, "domain/__init__.py", "")
    write(root, "domain/orders.py", "from store import db\n\n\ndef all():\n    return db.q()\n")
    write(root, "store/__init__.py", "")
    write(root, "store/db.py", "import psycopg2\n\n\ndef q():\n    return []\n")
    write(root, "deep/__init__.py", "")
    write(root, "deep/far.py", "x = 1\n")
    write(root, "store/uses_far.py", "from deep import far\n")  # hop 3: beyond DOMAIN_DEPTH
    write(
        root, "store/back.py", "from domain import orders\n"
    )  # upstream import: never an arrow
    write(root, "island/__init__.py", "")
    write(root, "island/alone.py", "y = 2\n")
    write(root, "manage.py", "z = 3\n")  # the repo root is a module too, and unstaged


def _produced(root: Path):
    g = graph_of(root)
    return g, derive_all(g, cluster(g))


# --- data flow ------------------------------------------------------------------


def test_data_flow_stages_are_evidenced_and_ordered(tmp_path: Path) -> None:
    _service(tmp_path)
    _, (produced, _notes) = _produced(tmp_path)
    root = produced[DiagramKind.DATA_FLOW].root_spec
    by_id = {n.id: n for n in root.nodes}
    ingress = by_id["in:api"]
    assert ingress.kind == "endpoint" and ingress.sublabel == "2 routes"
    assert ingress.label == "/orders", "the route paths, deduplicated"
    assert ingress.evidence[0].file == "api/routes.py" and ingress.evidence[0].start_line == 5
    assert by_id["api"].attr("stage") == "Handlers"
    assert by_id["domain"].attr("stage") == "Domain" and by_id["domain"].attr("hop") == "1"
    assert by_id["store"].attr("hop") == "2"
    assert "ext:database:PostgreSQL" in by_id
    assert by_id["ext:database:PostgreSQL"].attr("stage") == "Storage / External"
    labels = {r.label for r in root.regions}
    assert labels == {"01 / Ingress", "02 / Handlers", "03 / Domain", "04 / Storage / External"}
    assert ("in:api", "api") in {(e.src, e.dst) for e in root.edges}
    hop = {n.id: int(n.attr("hop") or 0) for n in root.nodes}
    module_edges = [
        e for e in root.edges if not e.src.startswith("in:") and not e.dst.startswith("ext:")
    ]
    assert module_edges, "handler -> domain -> store import edges are drawn"
    assert all(hop[e.dst] > hop[e.src] for e in module_edges), "data flows downstream only"


def test_modules_reachable_from_no_handler_are_not_drawn_and_counted(tmp_path: Path) -> None:
    _service(tmp_path)
    _, (produced, _notes) = _produced(tmp_path)
    ds = produced[DiagramKind.DATA_FLOW]
    ids = {n.id for n in ds.root_spec.nodes}
    assert "island" not in ids and "deep" not in ids, "beyond reach or beyond depth: not drawn"
    note = next(d for d in ds.diagnostics if d.code == "SVA-R-007")
    assert "3 module(s)" in note.message
    assert note.subject == "(repo root), deep, island"


def test_data_flow_lays_out_with_stage_frames(tmp_path: Path) -> None:
    _service(tmp_path)
    _, (produced, _notes) = _produced(tmp_path)
    lo = lay_out_set(produced[DiagramKind.DATA_FLOW], Style())
    assert not lo.withheld
    root = lo.canvases["/spec/root"]
    assert len(root.regions) == 4, [r.label for r in root.regions]
    xs = [r.x for r in sorted(root.regions, key=lambda r: r.label)]
    assert xs == sorted(xs), "stages read left to right"


def test_data_flow_is_absent_without_routes(tmp_path: Path) -> None:
    write(tmp_path, "lib/__init__.py", "")
    write(tmp_path, "lib/x.py", "x = 1\n")
    _, (produced, notes) = _produced(tmp_path)
    assert DiagramKind.DATA_FLOW not in produced
    assert any(n.startswith("data-flow:") and "no ingress" in n for n in notes)


# --- request flow -----------------------------------------------------------------


def test_request_flow_has_one_story_per_endpoint_group(tmp_path: Path) -> None:
    _service(tmp_path)
    _, (produced, _notes) = _produced(tmp_path)
    ds = produced[DiagramKind.REQUEST_FLOW]
    root = ds.root_spec
    assert [n.id for n in root.nodes] == ["req:api"]
    story = ds.specs[root.nodes[0].child_spec or ""]
    hops = {n.id: n.attr("hop") for n in story.nodes if n.attr("hop") is not None}
    assert hops == {"api": "0", "domain": "1", "store": "2"}
    assert any(n.id == "ext:database:PostgreSQL" for n in story.nodes)
    assert {r.label for r in story.regions} == {"handler", "hop 1", "hop 2"}
    assert "not call order" in story.subtitle and "not call order" in root.subtitle


def test_request_flow_lays_out_and_drills(tmp_path: Path) -> None:
    _service(tmp_path)
    assert main([str(tmp_path)]) == 0
    _, (produced, _notes) = _produced(tmp_path)
    lo = lay_out_set(produced[DiagramKind.REQUEST_FLOW], Style())
    assert not lo.withheld, list(lo.withheld)


# --- what is named as not derivable ----------------------------------------------


def test_lifecycle_and_workflow_are_named_as_not_derivable(tmp_path: Path) -> None:
    _service(tmp_path)
    out = tmp_path / "out"
    assert main([str(tmp_path), "--out", str(out)]) == 0
    html = (out / "index.html").read_text(encoding="utf8")
    for note in NOT_DERIVABLE:
        assert note.split(";")[0] in html, note
    assert "authored rather than extracted" in html


def test_report_states_how_many_modules_fell_in_no_stage(tmp_path: Path) -> None:
    """A deriver's own findings reach REPORT.md; only layout findings did
    before Wave B, so a capped component list or an unstaged module was
    counted as informational and never named."""
    _service(tmp_path)
    out = tmp_path / "out"
    assert main([str(tmp_path), "--out", str(out)]) == 0
    report = (out / "REPORT.md").read_text(encoding="utf8")
    assert "`SVA-R-007`" in report
    assert "3 module(s) are reachable from no route handler" in report
    assert "(repo root), deep, island" in report
    assert "**0** error(s), **0** warning(s), **0** informational" not in report


# --- the corridor routes beside a column ---------------------------------------------


def test_a_corridor_edge_into_a_lower_stacked_box_crosses_nothing(tmp_path: Path) -> None:
    """Two handlers in one column both reach two externals stacked in the last
    column; the corridor drop into the lower external must not pass through
    the upper one (this withheld the request-flow views on the demo repo)."""
    # A wide cloud box (Gemini API) sorts above a narrow store (Redis), the
    # exact pair that failed on the acceptance repo.
    # Both handlers also import a domain module, so the externals sit one
    # column further right and every handler -> external edge skips a
    # column: that is what puts it in the corridor.
    body = (
        "from fastapi import APIRouter\nimport google.generativeai\nimport redis\n"
        "from core import x\nrouter = APIRouter()\n\n@router.get('{p}')\ndef f():\n    pass\n"
    )
    write(tmp_path, "core/__init__.py", "")
    write(tmp_path, "core/x.py", "x = 1\n")
    # The handlers are unequal too: a wide one sorts above a narrow one, so a
    # climb at the narrow box's own right edge would cut through the wide one.
    write(tmp_path, "aaa_long_named_handlers/__init__.py", "")
    write(tmp_path, "aaa_long_named_handlers/r.py", body.format(p="/a"))
    write(tmp_path, "b/__init__.py", "")
    write(tmp_path, "b/r.py", body.format(p="/b"))
    _, (produced, _notes) = _produced(tmp_path)
    assert DiagramKind.DATA_FLOW in produced and DiagramKind.REQUEST_FLOW in produced
    for kind in (DiagramKind.DATA_FLOW, DiagramKind.REQUEST_FLOW):
        lo = lay_out_set(produced[kind], Style())
        assert not lo.withheld, (kind, [d.render()[:120] for d in lo.problems])
    root = lay_out_set(produced[DiagramKind.DATA_FLOW], Style()).canvases["/spec/root"]
    for prefix, ids in (("ext:", ("ext:cloud", "ext:database")), ("", ("aaa", "b"))):
        col = sorted((b for b in root.boxes if b.id.startswith(ids)), key=lambda b: b.y)
        assert len(col) == 2 and col[0].w > col[1].w, (prefix, [(b.id, b.w) for b in col])
