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


def _story_of(ds):  # type: ignore[no-untyped-def]
    """The request story: the root itself when one endpoint group is the
    whole diagram (review #20 C10), else the first menu item's child."""
    root = ds.root_spec
    if root.nodes and root.nodes[0].id.startswith("req:"):
        return ds.specs[root.nodes[0].child_spec or ""]
    return root


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
    assert root.subtitle.endswith("; 1 upstream import not drawn"), root.subtitle


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
    # One endpoint group: the story IS the diagram, with no one-item menu in
    # front of it (review #20 C10), and it has no crumb to a parent.
    story = ds.root_spec
    assert story.id == "/spec/req:api" and story.parent is None, (story.id, story.parent)
    assert not any(n.id.startswith("req:") for n in story.nodes)
    hops = {n.id: n.attr("hop") for n in story.nodes if n.attr("hop") is not None}
    assert hops == {"api": "0", "domain": "1", "store": "2"}
    assert any(n.id == "ext:database:PostgreSQL" for n in story.nodes)
    assert {r.label for r in story.regions} == {"handler", "hop 1", "hop 2"}
    assert "not call order" in story.subtitle
    assert story.subtitle.endswith("; 1 upstream import not drawn"), story.subtitle
    assert {(e.src, e.dst) for e in story.edges if e.src == "store"} == {
        ("store", "ext:database:PostgreSQL")
    }, "store -> domain runs against the hops and is counted, not drawn"


def test_two_endpoint_groups_get_a_menu_root_and_one_story_each(tmp_path: Path) -> None:
    _service(tmp_path)
    write(tmp_path, "admin/__init__.py", "")
    write(
        tmp_path,
        "admin/routes.py",
        "from fastapi import APIRouter\nfrom domain import orders\nrouter = APIRouter()\n\n"
        '@router.get("/admin")\ndef home():\n    return orders.all()\n',
    )
    _, (produced, _notes) = _produced(tmp_path)
    ds = produced[DiagramKind.REQUEST_FLOW]
    root = ds.root_spec
    assert [n.id for n in root.nodes] == ["req:admin", "req:api"]
    assert "2 endpoint groups; drill into one" in root.subtitle
    for n in root.nodes:
        story = ds.specs[n.child_spec or ""]
        assert story.parent == "/spec/root"
        assert any(m.attr("hop") == "0" for m in story.nodes)


def test_an_empty_route_path_counts_but_is_not_shown(tmp_path: Path) -> None:
    """A handler-relative route declared as `""` joined into `, /orders …`:
    a leading comma in 51 places on the acceptance repo (review #20 S4)."""
    _service(tmp_path)
    write(
        tmp_path,
        "api/routes.py",
        "from fastapi import APIRouter\nfrom domain import orders\nrouter = APIRouter()\n\n"
        '@router.get("")\ndef index():\n    return orders.all()\n\n'
        '@router.get("/orders")\ndef list_orders():\n    return orders.all()\n',
    )
    _, (produced, _notes) = _produced(tmp_path)
    ingress = _by_id(produced[DiagramKind.DATA_FLOW].root_spec)["in:api"]
    assert ingress.sublabel == "2 routes" and ingress.label == "/orders", ingress
    from svarupa.emit.cards import cards_for

    entry = cards_for(graph_of(tmp_path))[0].items[0].text
    assert "2 routes" in entry and ", ," not in entry and ": ," not in entry, entry


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


# --- review #17: the properties the wave named, pinned ------------------------------
#
# Thirteen of the reviewer's mutations survived the suite, including deleting
# every drill door in both views. Each test below is one of those claims.


def _by_id(spec):  # type: ignore[no-untyped-def]
    return {n.id: n for n in spec.nodes}


def test_every_module_box_in_both_flow_views_has_a_drill_door(tmp_path: Path) -> None:
    _service(tmp_path)
    _, (produced, _notes) = _produced(tmp_path)
    root = produced[DiagramKind.DATA_FLOW].root_spec
    for mid in ("api", "domain", "store"):
        assert _by_id(root)[mid].child_spec, f"{mid} in data flow drills nowhere"
    story = _story_of(produced[DiagramKind.REQUEST_FLOW])
    for mid in ("api", "domain", "store"):
        assert _by_id(story)[mid].child_spec, f"{mid} in the request story drills nowhere"
    assert _by_id(story)["api"].attr("roles") == "api", "roles travel into the story"
    assert _by_id(story)["domain"].attr("stage") == "Domain"
    assert _by_id(story)["domain"].sublabel.startswith("hop 1 · ")
    assert _by_id(story)["api"].sublabel.startswith("handler · ")


def test_kinds_edges_and_counts_are_the_named_ones(tmp_path: Path) -> None:
    _service(tmp_path)
    _, (produced, _notes) = _produced(tmp_path)
    root = produced[DiagramKind.DATA_FLOW].root_spec
    by = _by_id(root)
    assert by["api"].kind == "backend" and by["in:api"].kind == "endpoint"
    assert by["ext:database:PostgreSQL"].attr("layer") == "4", "one column after hop 2"
    handles = next(e for e in root.edges if e.label == "handles")
    assert handles.evidence == by["in:api"].evidence, "cites the route lines"
    assert handles.variant == "emphasis"
    api_domain = next(e for e in root.edges if (e.src, e.dst) == ("api", "domain"))
    assert api_domain.note == "1 import" and api_domain.weight == 1
    assert {(e.file, e.start_line) for e in api_domain.evidence} == {("api/routes.py", 2)}
    assert root.subtitle.startswith("1 ingress module, 2 domain modules, 1 external")
    assert all(r.kind == "stage" for r in root.regions)
    story = _story_of(produced[DiagramKind.REQUEST_FLOW])
    assert story.subtitle.startswith("3 modules within 2 import hops")
    assert {n.attr("layer") for n in story.nodes if n.attr("hop")} == {"0", "1", "2"}
    sev = next(d for d in produced[DiagramKind.DATA_FLOW].diagnostics if d.code == "SVA-R-007")
    assert sev.severity.value == "INFO"


def test_stage_and_hop_frames_cite_the_lines_that_put_members_in_them(tmp_path: Path) -> None:
    """Not "the first member's first line": the route lines for ingress and
    handlers, the import that reached a domain module, the classified import
    for a store."""
    _service(tmp_path)
    _, (produced, _notes) = _produced(tmp_path)
    root = produced[DiagramKind.DATA_FLOW].root_spec
    frames = {r.label: {(e.file, e.start_line) for e in r.evidence} for r in root.regions}
    # One citation per member: the first route line places the ingress box
    # and its handler in their stages.
    assert frames["01 / Ingress"] == {("api/routes.py", 5)}
    assert frames["02 / Handlers"] == {("api/routes.py", 5)}
    assert frames["03 / Domain"] == {("api/routes.py", 2), ("domain/orders.py", 1)}
    assert frames["04 / Storage / External"] == {("store/db.py", 1)}
    story = _story_of(produced[DiagramKind.REQUEST_FLOW])
    hop_frames = {r.label: {(e.file, e.start_line) for e in r.evidence} for r in story.regions}
    assert hop_frames["handler"] == {("api/routes.py", 5)}
    assert hop_frames["hop 1"] == {("api/routes.py", 2)}
    assert hop_frames["hop 2"] == {("domain/orders.py", 1)}


def test_routes_in_test_files_are_not_ingress(tmp_path: Path) -> None:
    _service(tmp_path)
    # Inside the api package, so the module is the same and only the
    # architecture gate keeps the test file's route out of the count.
    write(
        tmp_path,
        "api/test_routes.py",
        "from fastapi import APIRouter\nrouter = APIRouter()\n\n@router.get('/only-in-a-test')\n"
        "def t():\n    pass\n",
    )
    _, (produced, _notes) = _produced(tmp_path)
    by = _by_id(produced[DiagramKind.DATA_FLOW].root_spec)
    assert by["in:api"].sublabel == "2 routes" and not any(k.startswith("in:tests") for k in by)


def test_a_lateral_import_is_counted_as_lateral_and_not_drawn(tmp_path: Path) -> None:
    """A handler importing another handler (the include_router shape) is
    sideways, not backwards; one number called "against the flow" hid the
    difference (review #17 F5)."""
    for mod in ("api", "admin"):
        write(tmp_path, f"{mod}/__init__.py", "")
    write(
        tmp_path,
        "api/routes.py",
        "from fastapi import APIRouter\nfrom admin import routes\nrouter = APIRouter()\n\n"
        "@router.get('/a')\ndef a():\n    pass\n",
    )
    write(
        tmp_path,
        "admin/routes.py",
        "from fastapi import APIRouter\nrouter = APIRouter()\n\n@router.get('/b')\ndef b():\n    pass\n",
    )
    _, (produced, _notes) = _produced(tmp_path)
    root = produced[DiagramKind.DATA_FLOW].root_spec
    assert root.subtitle.endswith("; 1 lateral import not drawn"), root.subtitle
    assert not any((e.src, e.dst) == ("api", "admin") for e in root.edges)
    # In api's own story admin is a hop-1 module, so the same import is
    # downstream there and drawn: lateral is a property of the stage view.
    ds = produced[DiagramKind.REQUEST_FLOW]
    api_story = ds.specs[_by_id(ds.root_spec)["req:api"].child_spec or ""]
    assert any((e.src, e.dst) == ("api", "admin") for e in api_story.edges)
    assert "not drawn" not in api_story.subtitle


def test_identical_ingress_labels_name_their_handler(tmp_path: Path) -> None:
    for mod in ("api", "admin"):
        write(tmp_path, f"{mod}/__init__.py", "")
        write(
            tmp_path,
            f"{mod}/routes.py",
            "from fastapi import APIRouter\nrouter = APIRouter()\n\n@router.get('/health')\n"
            "def h():\n    pass\n",
        )
    _, (produced, _notes) = _produced(tmp_path)
    by = _by_id(produced[DiagramKind.DATA_FLOW].root_spec)
    assert (
        by["in:api"].sublabel == "1 route · api"
        and by["in:admin"].sublabel == "1 route · admin"
    )


def test_ingress_labels_cut_at_a_path_boundary() -> None:
    from svarupa.derive.dataflow import _cut

    assert _cut(["/api/v1/organisations/{org_id}/billing/invoices"]) == (
        "/api/v1/organisations/{org_id}/billing…"
    )
    assert _cut(["/a", "/b", "/c", "/d"]) == "/a, /b, /c …"
    assert _cut(["/short"]) == "/short"


def test_reachability_passes_through_generated_code(tmp_path: Path) -> None:
    """A handler -> generated schema -> store chain reaches the store. Following
    module-to-module pairs only cut the request there and then reported the
    store as reachable from no handler (review #17 F6)."""
    write(tmp_path, "api/__init__.py", "")
    write(
        tmp_path,
        "api/routes.py",
        "from fastapi import APIRouter\nfrom gen import schema_pb2\nrouter = APIRouter()\n\n"
        "@router.get('/x')\ndef x():\n    pass\n",
    )
    write(tmp_path, "gen/__init__.py", "")
    write(tmp_path, "gen/schema_pb2.py", "from store import db\n")
    write(tmp_path, "store/__init__.py", "")
    write(tmp_path, "store/db.py", "import psycopg2\n")
    _, (produced, _notes) = _produced(tmp_path)
    ds = produced[DiagramKind.DATA_FLOW]
    by = _by_id(ds.root_spec)
    assert by["store"].attr("hop") == "1", "the generated module is not a hop"
    assert "ext:database:PostgreSQL" in by
    edge = next(e for e in ds.root_spec.edges if (e.src, e.dst) == ("api", "store"))
    assert edge.note == "2 imports via gen"
    assert {(e.file, e.start_line) for e in edge.evidence} == {
        ("api/routes.py", 2),
        ("gen/schema_pb2.py", 1),
    }
    assert not any(
        "store" in (d.subject or "") for d in ds.diagnostics if d.code == "SVA-R-007"
    )


def test_two_stories_sharing_a_module_expand_their_own_copies(tmp_path: Path) -> None:
    """Expansion ids name the host view and the box. Named for the child
    alone, drilling a shared module from one story opened the other story's
    copy, crumb and siblings included (review #17 F1)."""
    import re

    _service(tmp_path)
    write(tmp_path, "admin/__init__.py", "")
    write(
        tmp_path,
        "admin/routes.py",
        "from fastapi import APIRouter\nfrom domain import orders\nrouter = APIRouter()\n\n"
        "@router.get('/admin')\ndef a():\n    pass\n",
    )
    out = tmp_path / "out"
    assert main([str(tmp_path), "--out", str(out)]) == 0
    html = (out / "index.html").read_text(encoding="utf8")
    assert 'data-view="/spec/req:api//domain//expanded"' in html
    assert 'data-view="/spec/req:admin//domain//expanded"' in html
    for tab in html.split('class="tab"')[1:]:
        views = re.findall(r'data-view="([^"]+)"', tab)
        dupes = {v for v in views if views.count(v) > 1}
        assert not dupes, f"duplicate view ids in one tab: {sorted(dupes)}"
    assert "view.dataset.host" in html, "the viewer resolves the expansion from the host view"


def test_diagram_json_carries_frames_notes_and_sublabels(tmp_path: Path) -> None:
    import json

    _service(tmp_path)
    out = tmp_path / "out"
    assert main([str(tmp_path), "--out", str(out)]) == 0
    root = json.loads((out / "diagrams" / "data-flow.json").read_text())["views"]["/spec/root"]
    labels = [g["label"] for g in root["regions"]]
    assert labels == ["01 / Ingress", "02 / Handlers", "03 / Domain", "04 / Storage / External"]
    assert all(g["kind"] == "stage" and g["evidence"] and g["members"] for g in root["regions"])
    assert any(r["note"] == "1 import" for r in root["routes"])
    assert any(b["sublabel"] == "2 routes" for b in root["boxes"])
    html = (out / "index.html").read_text(encoding="utf8")
    assert 'data-kind="stage"' in html and ".sv-boundary.sv-kind-stage" in html


def test_report_groups_informational_findings_and_counts_findings_not_lines() -> None:
    from svarupa.diagnostics import Diagnostic, Severity
    from svarupa.emit.report import MAX_LISTED, _grouped

    def d(code: str, i: int) -> Diagnostic:
        return Diagnostic(code=code, severity=Severity.INFO, message=f"m{i}", subject=f"s{i}")

    five = [d("SVA-R-006", i) for i in range(5)]
    lines = _grouped(five)
    assert len(lines) == 4 and lines[-1].endswith("*... and 2 more of this code*")
    # Eight codes of five findings: each code is four lines standing for
    # five findings, so a count of lines and a count of findings differ.
    many = [d(f"SVA-X-{c:03d}", i) for c in range(8) for i in range(5)]
    lines = _grouped(many)
    assert len(lines) == MAX_LISTED
    shown = 3 * 5 + 2  # three full codes (5 findings each) and two lines of the fourth
    assert lines[-1] == (
        f"*... and {40 - shown} more finding(s) of these codes; the build prints every one*"
    )
