"""Semantic facts: routes, tasks, entrypoints, roles.

The honesty rules under test:

* a decorator shape claims nothing without the framework import in the same
  file, so `@app.get` in a homemade DSL produces no route;
* every fact cites its own declaring line (the decorator's line, the manifest
  key's line), never the definition below it or a guessed one;
* lockfile records are keyed on modules, so renaming a handler churns zero
  lines while moving it between modules shows;
* the schema step is additive: a 1.1-era parser diffs the new kinds as
  opaque adds, never refuses.
"""

from __future__ import annotations

from pathlib import Path

from svarupa import __version__
from svarupa.build import Graph, build, module_roles
from svarupa.detect import detect
from svarupa.extract import declared_dependencies, extract
from svarupa.lock import Lockfile, build_lock, diff


def write(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf8")


def graph_of(root: Path) -> Graph:
    scan = detect(root)
    return build(scan, extract(scan, declared_dependencies(scan)), strict=False)


FASTAPI_FILE = """\
from fastapi import APIRouter

router = APIRouter()


@router.get("/items/{item_id}")
def read_item(item_id: int):
    return item_id


@router.websocket("/live")
def live(ws):
    pass
"""


# --- routes -------------------------------------------------------------


def test_fastapi_routes_cite_the_decorator_line(tmp_path: Path) -> None:
    write(tmp_path, "api/routes.py", FASTAPI_FILE)
    g = graph_of(tmp_path)
    routes = {(r.method, r.path): r for r in g.routes}
    assert set(routes) == {("GET", "/items/{item_id}"), ("WS", "/live")}
    get = routes[("GET", "/items/{item_id}")]
    assert get.evidence.file == "api/routes.py"
    assert get.evidence.start_line == 6, "the claim cites the decorator, not the def"
    assert get.handler.endswith("read_item")
    assert get.framework == "fastapi"


def test_a_route_shape_without_the_framework_import_claims_nothing(
    tmp_path: Path,
) -> None:
    """`@app.get` in a file that never imports fastapi is somebody's DSL."""
    write(
        tmp_path,
        "dsl/thing.py",
        'app = object()\n\n\n@app.get("/not-a-route")\ndef f():\n    pass\n',
    )
    assert graph_of(tmp_path).routes == ()


def test_flask_route_methods_kwarg_and_get_default(tmp_path: Path) -> None:
    write(
        tmp_path,
        "web/app.py",
        "from flask import Flask\n"
        "app = Flask(__name__)\n"
        "\n"
        '@app.route("/things", methods=["POST", "PUT"])\n'
        "def create():\n"
        "    pass\n"
        "\n"
        '@app.route("/things")\n'
        "def index():\n"
        "    pass\n",
    )
    g = graph_of(tmp_path)
    assert {(r.method, r.path) for r in g.routes} == {
        ("POST", "/things"),
        ("PUT", "/things"),
        ("GET", "/things"),
    }


def test_a_dynamic_route_path_is_not_guessed(tmp_path: Path) -> None:
    write(
        tmp_path,
        "api/dyn.py",
        "from fastapi import FastAPI\n"
        "app = FastAPI()\n"
        "PREFIX = '/v1'\n"
        "\n"
        '@app.get(f"{PREFIX}/items")\n'
        "def items():\n"
        "    pass\n",
    )
    assert graph_of(tmp_path).routes == ()


# --- tasks --------------------------------------------------------------


def test_celery_tasks_require_the_celery_import(tmp_path: Path) -> None:
    write(
        tmp_path,
        "jobs/worker.py",
        "from celery import shared_task\n\n@shared_task\ndef crunch():\n    pass\n",
    )
    write(
        tmp_path,
        "jobs/fake.py",
        "@shared_task\ndef not_a_task():\n    pass\n",
    )
    g = graph_of(tmp_path)
    assert len(g.tasks) == 1
    task = g.tasks[0]
    assert task.file == "jobs/worker.py"
    assert task.evidence.start_line == 3


# --- entrypoints --------------------------------------------------------


def test_pyproject_scripts_cite_the_declaring_line(tmp_path: Path) -> None:
    # The decoy table comes FIRST: a scanner that ignores table scope would
    # cite line 2 and still find "a" line, so ordering is what makes this test
    # able to fail.
    write(
        tmp_path,
        "pyproject.toml",
        "[tool.other]\n"
        'demo = "pkg.cli:main"\n'
        "\n"
        "[project]\n"
        'name = "demo"\n'
        "\n"
        "# a comment to offset the lines\n"
        "[project.scripts]\n"
        'demo = "pkg.cli:main"\n',
    )
    write(tmp_path, "pkg/__init__.py", "")
    write(tmp_path, "pkg/cli.py", "def main():\n    pass\n")
    g = graph_of(tmp_path)
    assert len(g.entrypoints) == 1
    e = g.entrypoints[0]
    assert (e.name, e.target) == ("demo", "pkg.cli:main")
    assert e.evidence.start_line == 9, "the same key in [tool.other] must not be cited"


def test_poetry_scripts_are_read_too(tmp_path: Path) -> None:
    write(
        tmp_path,
        "pyproject.toml",
        '[tool.poetry]\nname = "demo"\n\n[tool.poetry.scripts]\ndemo = "pkg.cli:main"\n',
    )
    write(tmp_path, "pkg/cli.py", "def main():\n    pass\n")
    g = graph_of(tmp_path)
    assert [e.target for e in g.entrypoints] == ["pkg.cli:main"]


def test_package_json_bin_string_and_dict(tmp_path: Path) -> None:
    write(
        tmp_path,
        "package.json",
        '{\n  "name": "demo",\n  "bin": {\n    "demo": "./cli.js"\n  }\n}\n',
    )
    write(tmp_path, "cli.js", "export const x = 1;\n")
    g = graph_of(tmp_path)
    assert len(g.entrypoints) == 1
    e = g.entrypoints[0]
    assert (e.name, e.target, e.lang) == ("demo", "./cli.js", "javascript")
    assert e.evidence.start_line == 4


def test_an_unlocatable_declaration_is_a_diagnostic_not_a_guess(
    tmp_path: Path,
) -> None:
    """An inline table parses fine but has no `[project.scripts]` header line,
    so the scanner cannot place the key. No fact, and SVA-X-007 says why."""
    write(
        tmp_path,
        "pyproject.toml",
        '[project]\nname = "demo"\nscripts = { demo = "pkg.cli:main" }\n',
    )
    write(tmp_path, "pkg/cli.py", "def main():\n    pass\n")
    g = graph_of(tmp_path)
    assert g.entrypoints == ()
    assert any(d.code == "SVA-X-007" for d in g.diagnostics)


# --- lockfile records ----------------------------------------------------


def _service_repo(root: Path) -> None:
    write(root, "api/__init__.py", "")
    write(root, "api/routes.py", FASTAPI_FILE)
    write(
        root,
        "jobs/__init__.py",
        "",
    )
    write(
        root,
        "jobs/worker.py",
        "from celery import shared_task\n\n\n@shared_task\ndef crunch():\n    pass\n",
    )
    write(root, "pkg/__init__.py", "")
    write(root, "pkg/cli.py", "def main():\n    pass\n")
    write(
        root,
        "pyproject.toml",
        '[project]\nname = "demo"\n\n[project.scripts]\ndemo = "pkg.cli:main"\n',
    )


def _lock_text(root: Path) -> str:
    return build_lock(graph_of(root), __version__).lockfile.render()


def test_lockfile_carries_endpoint_entrypoint_and_role_records(tmp_path: Path) -> None:
    _service_repo(tmp_path)
    text = _lock_text(tmp_path)
    assert "endpoint\tGET /items/{item_id}\tapi" in text
    assert "endpoint\tWS /live\tapi" in text
    assert "entrypoint\tdemo\tpkg" in text
    assert "role\tapi\tapi" in text
    assert "role\tjobs\tworker" in text
    assert "role\tpkg\tcli" in text
    assert "# schema 1.4" in text


def test_renaming_a_handler_churns_zero_lines(tmp_path: Path) -> None:
    """Endpoint records carry the module, not the function name."""
    _service_repo(tmp_path)
    before = _lock_text(tmp_path)
    routes = tmp_path / "api/routes.py"
    routes.write_text(
        routes.read_text(encoding="utf8").replace("read_item", "fetch_item"),
        encoding="utf8",
    )
    assert _lock_text(tmp_path) == before


def test_a_route_in_a_test_file_is_not_an_architectural_fact(tmp_path: Path) -> None:
    write(tmp_path, "pkg/__init__.py", "")
    write(tmp_path, "pkg/real.py", "x = 1\n")
    write(tmp_path, "tests/test_app.py", FASTAPI_FILE)
    text = _lock_text(tmp_path)
    assert "endpoint" not in text
    assert "role" not in text


def test_an_unresolvable_entrypoint_diagnoses_instead_of_inventing(
    tmp_path: Path,
) -> None:
    write(tmp_path, "pkg/__init__.py", "")
    write(tmp_path, "pkg/cli.py", "def main():\n    pass\n")
    write(
        tmp_path,
        "pyproject.toml",
        '[project]\nname = "demo"\n\n[project.scripts]\ngone = "no.such.module:main"\n',
    )
    result = build_lock(graph_of(tmp_path), __version__)
    assert "entrypoint" not in result.lockfile.render()
    assert any(d.code == "SVA-L-012" for d in result.diagnostics)


def test_an_older_parser_diffs_the_new_kinds_as_opaque_adds(tmp_path: Path) -> None:
    """The additive path: a 1.1-stamped base against a 1.2 head must produce
    adds (some flagged unknown to the old schema), never a refusal."""
    _service_repo(tmp_path)
    head = build_lock(graph_of(tmp_path), __version__).lockfile
    old_base = Lockfile.parse(
        "# svarupa 0.0.9\n# schema 1.1\n# grammars python@0.25.0\nmodule\tapi\n"
    )
    delta = diff(old_base, head)
    rendered = delta.render()
    assert "+ entrypoint" in rendered
    assert "+ role" in rendered
    assert "+ endpoint" in rendered


# --- roles feed the diagrams ----------------------------------------------


def test_module_roles_are_shared_by_lockfile_and_diagrams(tmp_path: Path) -> None:
    _service_repo(tmp_path)
    g = graph_of(tmp_path)
    roles = module_roles(g)
    assert roles["api"] == ("api",)
    assert roles["jobs"] == ("worker",)
    assert roles["pkg"] == ("cli",)


def test_architecture_boxes_wear_their_role_and_cite_it(tmp_path: Path) -> None:
    from svarupa.cluster import cluster
    from svarupa.derive import derive_all
    from svarupa.derive.base import DiagramKind

    _service_repo(tmp_path)
    # A cross-module import so the architecture view has structure to draw.
    write(
        tmp_path,
        "api/uses.py",
        "from jobs import worker\n",
    )
    g = graph_of(tmp_path)
    produced, _notes = derive_all(g, cluster(g))
    arch = produced[DiagramKind.ARCHITECTURE]
    # A top-level group box can share the id of its anchor module, so collect
    # every box and pick the module-level one by kind.
    all_boxes = [n for spec in arch.specs.values() for n in spec.nodes]
    api = next(n for n in all_boxes if n.id == "api" and n.kind != "group")
    assert api.kind == "api"
    assert api.attr("roles") == "api"
    assert any(ev.file == "api/routes.py" and ev.start_line == 6 for ev in api.evidence), (
        "the role colour must be clickable through to the decorator line"
    )
    assert any(n.kind == "worker" for n in all_boxes if n.id == "jobs")


# --- build re-verifies fact evidence ----------------------------------------


def test_build_drops_a_semantic_fact_with_invented_evidence(tmp_path: Path) -> None:
    """The independent re-check applies to facts, not only nodes and edges."""
    from dataclasses import replace as dc_replace

    from svarupa.extract.base import RouteFact
    from svarupa.model import Evidence

    write(tmp_path, "pkg/__init__.py", "")
    write(tmp_path, "pkg/app.py", "x = 1\n")
    scan = detect(tmp_path)
    extracted = extract(scan, declared_dependencies(scan))
    bogus = RouteFact(
        method="GET",
        path="/ghost",
        file="pkg/app.py",
        handler="pkg.app.ghost",
        framework="fastapi",
        evidence=Evidence("pkg/app.py", 999, 999),
    )
    g = build(scan, dc_replace(extracted, routes=(bogus,)), strict=False)
    assert g.routes == ()
    assert any(d.code == "SVA-B-002" and "semantic" in d.message for d in g.diagnostics)


def test_an_entrypoint_naming_a_missing_file_in_a_real_module_is_not_trusted(
    tmp_path: Path,
) -> None:
    """`pkg` exists as a module, `pkg/missing.py` does not. Deriving the
    module from the dotted string alone would mint a plausible-looking record
    for a file that is not there; resolution against the graph's own nodes is
    what stops it."""
    write(tmp_path, "pkg/__init__.py", "")
    write(tmp_path, "pkg/cli.py", "def main():\n    pass\n")
    write(
        tmp_path,
        "pyproject.toml",
        '[project]\nname = "demo"\n\n[project.scripts]\ngone = "pkg.missing:main"\n',
    )
    result = build_lock(graph_of(tmp_path), __version__)
    assert "entrypoint" not in result.lockfile.render()
    assert any(d.code == "SVA-L-012" for d in result.diagnostics)


# --- review #13 fixes, each with the demonstration that forced it ------------


def test_flask_tuple_methods_are_literal_and_identifier_methods_claim_nothing(
    tmp_path: Path,
) -> None:
    """`methods=("POST",)` is as literal as a list and records POST; a present
    but dynamic `methods` is unknown and must not fall back to GET, which
    recorded `endpoint GET /pay` for a POST-only route."""
    write(
        tmp_path,
        "web/app.py",
        "from flask import Flask\n"
        "app = Flask(__name__)\n"
        "M = ['DELETE']\n"
        "\n"
        '@app.route("/pay", methods=("POST",))\n'
        "def pay():\n"
        "    pass\n"
        "\n"
        '@app.route("/dyn", methods=M)\n'
        "def dyn():\n"
        "    pass\n",
    )
    g = graph_of(tmp_path)
    assert {(r.method, r.path) for r in g.routes} == {("POST", "/pay")}


def test_a_route_shaped_decorator_on_a_non_path_claims_nothing(tmp_path: Path) -> None:
    """`import fastapi` plus somebody's `@cache.get("user:profile")` recorded
    `endpoint GET user:profile`. A route path starts with `/` (or is empty,
    FastAPI's router-prefix idiom)."""
    write(
        tmp_path,
        "api/cachey.py",
        "import fastapi\n"
        "cache = object()\n"
        "\n"
        '@cache.get("user:profile")\n'
        "def profile():\n"
        "    pass\n",
    )
    write(
        tmp_path,
        "api/routes.py",
        "from fastapi import APIRouter\n"
        "router = APIRouter()\n"
        "\n"
        '@router.get("")\n'
        "def index():\n"
        "    pass\n",
    )
    g = graph_of(tmp_path)
    assert {(r.method, r.path) for r in g.routes} == {("GET", "")}


def test_a_manifest_in_a_test_fixture_mints_no_committed_record(tmp_path: Path) -> None:
    """tests/fixtures/pyproject.toml declared `evil = "pkg.cli:main"` and the
    lockfile came out with `entrypoint evil pkg` plus `role pkg cli`: a test
    fixture assigning a role to a production module."""
    write(tmp_path, "pkg/__init__.py", "")
    write(tmp_path, "pkg/cli.py", "def main():\n    pass\n")
    write(
        tmp_path,
        "tests/fixtures/pyproject.toml",
        '[project]\nname = "evil"\n\n[project.scripts]\nevil = "pkg.cli:main"\n',
    )
    text = _lock_text(tmp_path)
    assert "entrypoint" not in text
    assert "role" not in text
    # And at the graph level, because after manifest-anchored resolution the
    # lockfile filters would mask a removed gate for this shape.
    assert graph_of(tmp_path).entrypoints == ()


def test_a_monorepo_entrypoint_is_anchored_at_its_manifest_not_the_root(
    tmp_path: Path,
) -> None:
    """A root-level decoy `pkg/` captured `packages/a`'s script silently."""
    write(tmp_path, "pkg/__init__.py", "")
    write(tmp_path, "pkg/cli.py", "def main():\n    pass\n")  # the decoy
    write(tmp_path, "packages/a/pkg/__init__.py", "")
    write(tmp_path, "packages/a/pkg/cli.py", "def main():\n    pass\n")
    write(
        tmp_path,
        "packages/a/pyproject.toml",
        '[project]\nname = "a"\n\n[project.scripts]\nrun = "pkg.cli:main"\n',
    )
    text = _lock_text(tmp_path)
    assert "entrypoint\trun\tpackages/a/pkg" in text
    assert "entrypoint\trun\tpkg\n" not in text


def test_a_src_layout_entrypoint_resolves(tmp_path: Path) -> None:
    write(tmp_path, "src/pkg/__init__.py", "")
    write(tmp_path, "src/pkg/cli.py", "def main():\n    pass\n")
    write(
        tmp_path,
        "pyproject.toml",
        '[project]\nname = "demo"\n\n[project.scripts]\ndemo = "pkg.cli:main"\n',
    )
    assert "entrypoint\tdemo\tsrc/pkg" in _lock_text(tmp_path)


def test_a_js_bin_is_anchored_at_its_manifest_not_the_root(tmp_path: Path) -> None:
    write(tmp_path, "cli.js", "export const decoy = 1;\n")
    write(tmp_path, "packages/a/cli.js", "export const real = 1;\n")
    write(
        tmp_path,
        "packages/a/package.json",
        '{\n  "name": "a",\n  "bin": {\n    "a": "./cli.js"\n  }\n}\n',
    )
    g = graph_of(tmp_path)
    from svarupa.build import entrypoint_module

    e = next(x for x in g.entrypoints if x.name == "a")
    assert entrypoint_module(g, e.target, e.lang, e.file) == "packages/a"


def test_the_toml_scan_is_not_fooled_by_a_string_containing_key_equals(
    tmp_path: Path,
) -> None:
    """A multiline string reading `serve = ...` was cited instead of the real
    declaration two lines below it."""
    write(
        tmp_path,
        "pyproject.toml",
        "[project]\n"
        'name = "demo"\n'
        "\n"
        "[project.scripts]\n"
        'helper = """\n'
        'serve = "decoy text inside a string"\n'
        '"""\n'
        'serve = "pkg.cli:main"\n',
    )
    write(tmp_path, "pkg/cli.py", "def main():\n    pass\n")
    g = graph_of(tmp_path)
    serves = [e for e in g.entrypoints if e.name == "serve"]
    assert [e.evidence.start_line for e in serves] == [8]


def test_the_bin_scan_is_scoped_to_the_bin_object(tmp_path: Path) -> None:
    """`"config": {"serve": "./dist/serve.js"}` above `bin` was cited for the
    bin entry below it."""
    write(
        tmp_path,
        "package.json",
        "{\n"
        '  "name": "demo",\n'
        '  "config": { "serve": "./dist/serve.js" },\n'
        '  "bin": {\n'
        '    "serve": "./dist/serve.js"\n'
        "  }\n"
        "}\n",
    )
    write(tmp_path, "dist/serve.js", "export const x = 1;\n")
    g = graph_of(tmp_path)
    assert len(g.entrypoints) == 1
    assert g.entrypoints[0].evidence.start_line == 5


def test_a_quoted_script_key_is_located(tmp_path: Path) -> None:
    write(
        tmp_path,
        "pyproject.toml",
        '[project]\nname = "demo"\n\n[project.scripts]\n"run.dev" = "pkg.cli:main"\n',
    )
    write(tmp_path, "pkg/cli.py", "def main():\n    pass\n")
    g = graph_of(tmp_path)
    assert [(e.name, e.evidence.start_line) for e in g.entrypoints] == [("run.dev", 5)]


def test_a_schema_minor_step_is_attributed_to_the_upgrade_not_the_change(
    tmp_path: Path,
) -> None:
    """Diffing a 1.1 base against a 1.2 head showed every route and role as
    this PR's additions and drift called the base 'out of date with the
    code'. The tool grew record kinds; the code did not change."""
    from svarupa.lock import drift_check

    _service_repo(tmp_path)
    head = build_lock(graph_of(tmp_path), __version__).lockfile
    old_base = Lockfile.parse(
        "# svarupa 0.0.9\n# schema 1.1\n# grammars python@0.25.0\nmodule\tapi\n"
    )
    delta = diff(old_base, head)
    assert "SVA-L-013" in [d.code for d in delta.diagnostics]
    drift = drift_check(old_base, head)
    assert any("record kinds the old build could not emit" in d.message for d in drift)


def test_dual_role_module_wears_api_by_priority_and_records_both(tmp_path: Path) -> None:
    from svarupa.cluster import cluster
    from svarupa.derive import derive_all
    from svarupa.derive.base import DiagramKind

    write(tmp_path, "svc/__init__.py", "")
    write(tmp_path, "svc/routes.py", FASTAPI_FILE)
    write(
        tmp_path,
        "svc/jobs.py",
        "from celery import shared_task\n\n\n@shared_task\ndef crunch():\n    pass\n",
    )
    write(tmp_path, "other/__init__.py", "")
    write(tmp_path, "other/uses.py", "x = 1\n")
    text = _lock_text(tmp_path)
    assert "role\tsvc\tapi" in text
    assert "role\tsvc\tworker" in text
    g = graph_of(tmp_path)
    produced, _ = derive_all(g, cluster(g))
    boxes = [
        n for spec in produced[DiagramKind.ARCHITECTURE].specs.values() for n in spec.nodes
    ]
    svc = next(n for n in boxes if n.id == "svc" and n.kind != "group")
    assert svc.kind == "api", "api outranks worker in the box colour"
    assert svc.attr("roles") == "api,worker"


def test_module_roles_itself_gates_on_architecture_eligibility(tmp_path: Path) -> None:
    """The diagrams consume module_roles directly; the lockfile's own module
    filter must not be the only guard."""
    write(tmp_path, "pkg/__init__.py", "")
    write(tmp_path, "pkg/real.py", "x = 1\n")
    write(tmp_path, "tests/test_app.py", FASTAPI_FILE)
    assert module_roles(graph_of(tmp_path)) == {}


def test_semantic_facts_are_in_canonical_order(tmp_path: Path) -> None:
    write(
        tmp_path,
        "api/z_first.py",
        "from fastapi import APIRouter\nrouter = APIRouter()\n\n"
        '@router.get("/zzz")\ndef z():\n    pass\n\n'
        '@router.get("/aaa")\ndef a():\n    pass\n',
    )
    g = graph_of(tmp_path)
    assert g.routes == tuple(sorted(g.routes))


def test_the_report_states_the_semantics_framework_boundary(tmp_path: Path) -> None:
    """Framework-level, not language-level: a Django or Koa repo is inside
    the covered languages and still invisible to route extraction, so the
    boundary sentence names the frameworks."""
    from svarupa.emit.report import _semantics_scope

    write(tmp_path, "api/routes.py", FASTAPI_FILE)
    write(tmp_path, "web/app.ts", "export const x = 1;\n")
    lines = "\n".join(_semantics_scope(graph_of(tmp_path)))
    assert "framework detection" in lines
    assert "express" in lines and "nestjs" in lines and "fastapi" in lines
    assert "not as a missing API" in lines


def test_the_cli_door_writes_semantic_records(tmp_path: Path) -> None:
    from svarupa.cli import main
    from svarupa.lock import LOCK_NAME

    _service_repo(tmp_path)
    assert main([str(tmp_path), "--lock"]) == 0
    text = (tmp_path / ".svarupa" / LOCK_NAME).read_text(encoding="utf8")
    assert "endpoint\tGET /items/{item_id}\tapi" in text
    assert "role\tpkg\tcli" in text


def test_role_evidence_respects_the_evidence_cap(tmp_path: Path) -> None:
    from svarupa.cluster import cluster
    from svarupa.derive import derive_all
    from svarupa.derive.base import MAX_EVIDENCE_PER_BOX, DiagramKind

    write(tmp_path, "api/__init__.py", "")
    for i in range(11):
        write(tmp_path, f"api/m{i:02d}.py", "x = 1\n")
    write(tmp_path, "api/routes.py", FASTAPI_FILE)
    write(tmp_path, "other/__init__.py", "")
    write(tmp_path, "other/uses.py", "x = 1\n")
    g = graph_of(tmp_path)
    produced, _ = derive_all(g, cluster(g))
    boxes = [
        n for spec in produced[DiagramKind.ARCHITECTURE].specs.values() for n in spec.nodes
    ]
    api = next(n for n in boxes if n.id == "api" and n.kind == "api")
    assert len(api.evidence) <= MAX_EVIDENCE_PER_BOX
    assert any(ev.file == "api/routes.py" and ev.start_line == 6 for ev in api.evidence), (
        "the cap must not evict the role citation"
    )


# --- TypeScript/JavaScript routes: Express and NestJS -------------------------

EXPRESS_FILE = """\
import express, { Router as R } from 'express';
import axios from 'axios';

const app = express();
const r = R();
const sub = express.Router();

app.get('/items', (req, res) => res.send('ok'));
r.post(`/tpl`, h);
sub.all('/any', h);
axios.get('/decoy');
r.put(`/dyn/${x}`, h);
app.get('not-a-path', h);
"""


def test_express_routes_are_receiver_scoped(tmp_path: Path) -> None:
    """`axios.get('/decoy')` sits in a file that imports express; only calls
    on objects assigned from express()/Router() are routes. Review #13 S6's
    lesson applied to the language where it bites hardest."""
    write(tmp_path, "src/app.ts", EXPRESS_FILE)
    g = graph_of(tmp_path)
    got = {(r.method, r.path, r.framework) for r in g.routes}
    assert got == {
        ("GET", "/items", "express"),
        ("POST", "/tpl", "express"),
        ("ALL", "/any", "express"),
    }
    items = next(r for r in g.routes if r.path == "/items")
    assert items.evidence.file == "src/app.ts"
    assert items.evidence.start_line == 8


def test_express_works_in_plain_javascript(tmp_path: Path) -> None:
    write(
        tmp_path,
        "server.js",
        "const express = require('express');\n"
        "import express2 from 'express';\n"
        "const app = express2();\n"
        "app.get('/js', h);\n",
    )
    g = graph_of(tmp_path)
    assert {(r.method, r.path) for r in g.routes} == {("GET", "/js")}


def test_nest_controller_prefix_composes_with_method_paths(tmp_path: Path) -> None:
    write(
        tmp_path,
        "src/users.controller.ts",
        "import { Controller, Get, Post } from '@nestjs/common';\n"
        "\n"
        "@Controller('users')\n"
        "export class UsersController {\n"
        "  @Get(':id')\n"
        "  findOne(id: string) { return id; }\n"
        "  @Get()\n"
        "  list() { return []; }\n"
        "  @Post('bulk')\n"
        "  bulk() { return []; }\n"
        "  helper() { return 1; }\n"
        "}\n",
    )
    g = graph_of(tmp_path)
    got = {(r.method, r.path) for r in g.routes}
    assert got == {("GET", "/users/:id"), ("GET", "/users"), ("POST", "/users/bulk")}
    find_one = next(r for r in g.routes if r.path == "/users/:id")
    assert find_one.evidence.start_line == 5, "the claim cites the @Get line"
    assert find_one.handler.endswith("UsersController.findOne")


def test_nest_decorators_without_the_import_claim_nothing(tmp_path: Path) -> None:
    write(
        tmp_path,
        "src/fake.ts",
        # An unrelated import, so an import gate reduced to "imports anything"
        # cannot pass vacuously on an empty import list.
        "import axios from 'axios';\n"
        "@Controller('users')\n"
        "export class Fake {\n"
        "  @Get(':id')\n"
        "  findOne(id: string) { return id; }\n"
        "}\n",
    )
    assert graph_of(tmp_path).routes == ()


def test_nest_route_methods_outside_a_controller_claim_nothing(tmp_path: Path) -> None:
    write(
        tmp_path,
        "src/plain.ts",
        "import { Get } from '@nestjs/common';\n"
        "export class Plain {\n"
        "  @Get(':id')\n"
        "  findOne(id: string) { return id; }\n"
        "}\n",
    )
    assert graph_of(tmp_path).routes == ()


def test_ts_routes_reach_the_lockfile_and_roles(tmp_path: Path) -> None:
    write(tmp_path, "src/app.ts", EXPRESS_FILE)
    write(tmp_path, "other/util.ts", "export const x = 1;\n")
    text = _lock_text(tmp_path)
    assert "endpoint\tGET /items\tsrc" in text
    assert "role\tsrc\tapi" in text


# --- review #14 fixes ---------------------------------------------------------


def test_a_dynamic_nest_path_or_prefix_mints_nothing(tmp_path: Path) -> None:
    """`@Get(PATH)` minted `endpoint GET /users` for a route living at
    `/users/:id`; `@Controller(['a','b'])` composed as if it had no prefix.
    Present-but-dynamic must stay distinguishable from absent, the same
    three-state rule as Flask's methods, one review earlier."""
    write(
        tmp_path,
        "src/c.ts",
        "import { Controller, Get, Post } from '@nestjs/common';\n"
        "const PATH = ':id';\n"
        "@Controller('users')\n"
        "export class C {\n"
        # @Post, so a wrongly composed record (POST /users) is distinguishable
        # from list()'s legitimate GET /users in the asserted set.
        "  @Post(PATH)\n"
        "  find() { return 1; }\n"
        "  @Get()\n"
        "  list() { return []; }\n"
        "}\n"
        "@Controller(['a', 'b'])\n"
        "export class Multi {\n"
        "  @Get(':id')\n"
        "  byId() { return 1; }\n"
        "}\n",
    )
    g = graph_of(tmp_path)
    assert {(r.method, r.path) for r in g.routes} == {("GET", "/users")}


def test_commonjs_require_express_is_covered(tmp_path: Path) -> None:
    """`const express = require('express')` is the dominant Express dialect;
    it claimed nothing while the report said express was covered."""
    write(
        tmp_path,
        "server.js",
        "const express = require('express');\n"
        "const { Router } = require('express');\n"
        "const app = express();\n"
        "const r = Router();\n"
        "app.get('/legacy-cjs', h);\n"
        "r.post('/legacy-router', h);\n",
    )
    g = graph_of(tmp_path)
    assert {(r.method, r.path) for r in g.routes} == {
        ("GET", "/legacy-cjs"),
        ("POST", "/legacy-router"),
    }


def test_a_name_bound_by_a_non_express_ctor_leaves_the_receiver_set(
    tmp_path: Path,
) -> None:
    """The gate is on the object, and a name is not an object: a helper's
    `const app = makeCache()` shares the top-level `app`'s name, and its
    `.get('/decoy-cache-key')` was a wrong committed edge."""
    write(
        tmp_path,
        "src/app.ts",
        "import express from 'express';\n"
        "const app = express();\n"
        "app.get('/real', h);\n"
        "function make() {\n"
        "  const app = makeCache();\n"
        "  app.get('/decoy-cache-key', h);\n"
        "}\n",
    )
    g = graph_of(tmp_path)
    # Losing /real to the collision is the accepted cost; claiming the decoy
    # is the disallowed outcome.
    assert {(r.method, r.path) for r in g.routes} == set()


def test_escape_sequences_survive_in_route_paths(tmp_path: Path) -> None:
    r"""Joining only string fragments turned '/a\'b' into /ab: a corrupted
    committed value, and two distinct routes could collide onto one key."""
    write(
        tmp_path,
        "src/app.ts",
        "import express from 'express';\nconst app = express();\napp.get('/a\\'b', h);\n",
    )
    g = graph_of(tmp_path)
    assert [r.path for r in g.routes] == ["/a\\'b"], "source-spelled, nothing deleted"
    assert "endpoint" in _lock_text(tmp_path)


def test_a_later_position_string_is_not_a_path(tmp_path: Path) -> None:
    write(
        tmp_path,
        "src/app.ts",
        "import express from 'express';\nconst app = express();\napp.get(handler, '/late');\n",
    )
    assert graph_of(tmp_path).routes == ()


def test_a_relative_express_module_is_not_the_framework(tmp_path: Path) -> None:
    write(
        tmp_path,
        "src/app.ts",
        "import express from './express';\nconst app = express();\napp.get('/fake', h);\n",
    )
    write(tmp_path, "src/express.ts", "export default () => ({});\n")
    assert graph_of(tmp_path).routes == ()


def test_a_slash_spelled_controller_prefix_composes_once(tmp_path: Path) -> None:
    write(
        tmp_path,
        "src/c.ts",
        "import { Controller, Get } from '@nestjs/common';\n"
        "@Controller('/users')\n"
        "export class C {\n"
        "  @Get('/:id')\n"
        "  find() { return 1; }\n"
        "}\n",
    )
    assert {r.path for r in graph_of(tmp_path).routes} == {"/users/:id"}


def test_a_non_exported_controller_is_seen(tmp_path: Path) -> None:
    write(
        tmp_path,
        "src/internal.ts",
        "import { Controller, Get } from '@nestjs/common';\n"
        "@Controller('internal')\n"
        "class InternalController {\n"
        "  @Get('x')\n"
        "  go() { return 1; }\n"
        "}\n",
    )
    assert {r.path for r in graph_of(tmp_path).routes} == {"/internal/x"}


def test_the_coverage_expansion_is_attributed_to_the_upgrade(tmp_path: Path) -> None:
    """A 1.2-era base (before TS routes) against this build's head must carry
    SVA-L-013: the six new endpoint lines come from the tool, not the PR."""
    write(tmp_path, "src/app.ts", EXPRESS_FILE)
    head = build_lock(graph_of(tmp_path), __version__).lockfile
    old_base = Lockfile.parse(
        "# svarupa 0.1.0\n# schema 1.2\n# grammars typescript@0.23.2\nmodule\tsrc\n"
    )
    delta = diff(old_base, head)
    assert "SVA-L-013" in [d.code for d in delta.diagnostics]


def test_express_handler_is_the_enclosing_function_when_there_is_one(
    tmp_path: Path,
) -> None:
    write(
        tmp_path,
        "src/app.ts",
        "import express from 'express';\n"
        "const app = express();\n"
        "export function mount() {\n"
        "  app.get('/in-fn', h);\n"
        "}\n",
    )
    g = graph_of(tmp_path)
    assert g.routes[0].handler.endswith("mount")


def test_ts_pass1_details_pin_the_reviewers_probes() -> None:
    """Direct pass-1 pins: destructuring never enters ctor_assigns, and
    `export const f = () => {}` keeps its exported flag through the
    declaration hop."""
    from svarupa.extract.typescript import TypeScriptExtractor

    f = TypeScriptExtractor().parse(
        "src/a.ts",
        b"export const handler = () => 1;\nconst { get } = app;\n",
    )
    assert f.ctor_assigns == ()
    sym = next(s for s in f.symbols if s.name == "handler")
    assert sym.exported is True


# --- Express chaining and same-file mount composition ---------------------------

MOUNT_FILE = """\
import express, { Router } from 'express';
const app = express();
const r = Router();
const child = Router();
const twice = Router();
const dyn = Router();
app.route('/items').get(list).post(create).put(update);
app.route(DYN).get(h);
app.locals('/notroute').get(h);
app.use('/api', r);
r.get('/things', h);
r.use('/nested', child);
child.get('/deep', h);
app.use('/a', twice);
app.use('/b', twice);
twice.get('/x', h);
app.use(PFX, dyn);
dyn.get('/declared', h);
app.use('/mw', logger);
app.get('/plain', h);
"""


def _express_paths(tmp_path: Path) -> set[tuple[str, str]]:
    write(tmp_path, "src/app.ts", MOUNT_FILE)
    return {(r.method, r.path) for r in graph_of(tmp_path).routes}


def test_route_chaining_claims_each_verb_on_the_chain_root_path(tmp_path: Path) -> None:
    got = _express_paths(tmp_path)
    assert ("GET", "/items") in got and ("POST", "/items") in got


def test_a_dynamic_chain_root_claims_nothing(tmp_path: Path) -> None:
    """`app.route(DYN).get(h)`: the route exists and its path is unknown."""
    assert not any(p in ("", "/") or "DYN" in p for _, p in _express_paths(tmp_path))
    # And specifically no GET record that is not one of the known ones.
    gets = {p for m, p in _express_paths(tmp_path) if m == "GET"}
    assert gets == {
        "/items",
        "/api/things",
        "/api/nested/deep",
        "/a/x",
        "/b/x",
        "/declared",
        "/plain",
    }
    assert ("PUT", "/items") in _express_paths(tmp_path), "the third hop of the chain"


def test_same_file_mounts_compose_including_nested_and_multiple(tmp_path: Path) -> None:
    got = _express_paths(tmp_path)
    assert ("GET", "/api/things") in got, "app.use('/api', r) composes r's routes"
    assert ("GET", "/api/nested/deep") in got, "router-on-router mounts compose through"
    assert ("GET", "/a/x") in got and ("GET", "/b/x") in got, "mounted twice exists twice"
    assert ("GET", "/things") not in got, "the composed path replaces the declared one"


def test_a_dynamic_mount_prefix_leaves_the_declared_path(tmp_path: Path) -> None:
    """`app.use(PFX, dyn)`: composing would guess; the declared path is the
    documented mount-relative boundary. It once read as "" and only looked
    right because "" composes to the declared path; a second static mount
    of the same router would then have been silently dropped."""
    got = _express_paths(tmp_path)
    assert ("GET", "/declared") in got


def test_middleware_and_app_routes_are_untouched_by_mounting(tmp_path: Path) -> None:
    got = _express_paths(tmp_path)
    assert ("GET", "/plain") in got
    assert not any("/mw" in p for _, p in got)


def test_a_dynamic_mount_poisons_even_when_another_mount_is_static(tmp_path: Path) -> None:
    write(
        tmp_path,
        "src/app.ts",
        "import express, { Router } from 'express';\n"
        "const app = express();\n"
        "const r = Router();\n"
        "app.use('/static', r);\n"
        "app.use(PFX, r);\n"
        "r.get('/x', h);\n",
    )
    assert {r.path for r in graph_of(tmp_path).routes} == {"/x"}


def test_a_cross_file_mount_does_not_compose(tmp_path: Path) -> None:
    write(
        tmp_path,
        "src/routes.ts",
        "import { Router } from 'express';\nexport const r = Router();\nr.get('/things', h);\n",
    )
    write(
        tmp_path,
        "src/app.ts",
        "import express from 'express';\nimport { r } from './routes';\n"
        "const app = express();\napp.use('/api', r);\n",
    )
    assert {r.path for r in graph_of(tmp_path).routes} == {"/things"}


def test_mount_composed_routes_reach_the_lockfile(tmp_path: Path) -> None:
    write(tmp_path, "src/app.ts", MOUNT_FILE)
    write(tmp_path, "other/x.ts", "export const y = 1;\n")
    text = _lock_text(tmp_path)
    assert "endpoint\tGET /api/nested/deep\tsrc" in text
    assert "endpoint\tPOST /items\tsrc" in text


# --- review #15 fixes ---------------------------------------------------------


def test_a_router_name_bound_twice_composes_nothing(tmp_path: Path) -> None:
    """The ordinary factory idiom: top-level `router` mounted at /users, and a
    helper's inner `const router = Router()` serving /healthz. Name-keyed
    composition committed `GET /users/healthz` for a route served at
    `/healthz`. A name bound twice is not one object: both fall to declared."""
    write(
        tmp_path,
        "src/app.ts",
        "import express, { Router } from 'express';\n"
        "const app = express();\n"
        "const router = Router();\n"
        "router.get('/:id', h);\n"
        "app.use('/users', router);\n"
        "function healthRouter() {\n"
        "  const router = Router();\n"
        "  router.get('/healthz', ok);\n"
        "  return router;\n"
        "}\n",
    )
    got = {r.path for r in graph_of(tmp_path).routes}
    assert got == {"/:id", "/healthz"}
    assert "/users/healthz" not in got


def test_mount_cycles_and_self_mounts_diagnose_and_fall_to_declared(tmp_path: Path) -> None:
    write(
        tmp_path,
        "src/app.ts",
        "import express, { Router } from 'express';\n"
        "const app = express();\n"
        "const a = Router();\n"
        "const b = Router();\n"
        "const me = Router();\n"
        "a.use('/a', b);\n"
        "b.use('/b', a);\n"
        "me.use('/me', me);\n"
        "a.get('/x', h);\n"
        "me.get('/y', h);\n",
    )
    g = graph_of(tmp_path)
    assert {r.path for r in g.routes} == {"/x", "/y"}
    assert any(d.code == "SVA-X-009" for d in g.diagnostics)


def _deep_nine(order: str) -> str:
    routers = [f"r{i}" for i in range(1, 10)]
    decl = "\n".join(f"const {r} = Router();" for r in routers)
    mounts = ["app.use('/r1', r1);"] + [
        f"r{i}.use('/r{i + 1}', r{i + 1});" for i in range(1, 9)
    ]
    if order == "leaf-first":
        mounts = list(reversed(mounts))
    return (
        "import express, { Router } from 'express';\nconst app = express();\n"
        + decl
        + "\n"
        + "\n".join(mounts)
        + "\nr1.get('/x', h);\nr9.get('/leaf', h);\n"
    )


def test_composition_does_not_depend_on_mount_statement_order(tmp_path: Path) -> None:
    """Nine nested routers, mounted root-first or leaf-first, compose the
    same. A memoized depth cap poisoned routers depending on which mount
    statement was met first: a reorder with no semantic content churned
    committed lines."""
    results: list[set[str]] = []
    for order in ("root-first", "leaf-first"):
        root = tmp_path / order
        write(root, "src/app.ts", _deep_nine(order))
        results.append({r.path for r in graph_of(root).routes})
    assert results[0] == results[1]
    assert "/r1/r2/r3/r4/r5/r6/r7/r8/r9/leaf" in results[0]
    assert "/r1/x" in results[0]


def test_only_use_mounts_and_only_route_roots_a_chain(tmp_path: Path) -> None:
    write(
        tmp_path,
        "src/app.ts",
        "import express, { Router } from 'express';\n"
        "const app = express();\n"
        "const r = Router();\n"
        "app.get('/direct', r);\n"
        "r.get('/things', h);\n"
        "app.use('/api2', r).get('/x', h);\n",
    )
    got = {(r.method, r.path) for r in graph_of(tmp_path).routes}
    assert ("GET", "/direct") in got, "app.get(path, r) registers a handler, it is not a mount"
    assert ("GET", "/direct/things") not in got
    assert ("GET", "/api2") not in got, "a chain rooted at use() is not a route"
    assert ("GET", "/api2/things") in got, "but the use() itself still mounts"


def test_a_slashless_mount_prefix_poisons(tmp_path: Path) -> None:
    write(
        tmp_path,
        "src/app.ts",
        "import express, { Router } from 'express';\nconst app = express();\n"
        "const r = Router();\napp.use('api', r);\nr.get('/x', h);\n",
    )
    assert {r.path for r in graph_of(tmp_path).routes} == {"/x"}


def test_mounted_routes_keep_their_trailing_slash_spelling(tmp_path: Path) -> None:
    write(
        tmp_path,
        "src/app.ts",
        "import express, { Router } from 'express';\nconst app = express();\n"
        "const r = Router();\napp.use('/api/', r);\n"
        "r.get('/things/', h);\nr.get('/things', h);\napp.get('/plain/', h);\n",
    )
    assert {r.path for r in graph_of(tmp_path).routes} == {
        "/api/things/",
        "/api/things",
        "/plain/",
    }


def test_composed_routes_carry_the_mount_lines_and_chains_cite_each_verb(
    tmp_path: Path,
) -> None:
    write(
        tmp_path,
        "src/app.ts",
        "import express, { Router } from 'express';\n"
        "const app = express();\n"
        "const r = Router();\n"
        "app.use('/api', r);\n"
        "r.get('/things', h);\n"
        "app.route('/items')\n"
        "  .get(list)\n"
        "  .delete(remove);\n",
    )
    routes = {(r.method, r.path): r for r in graph_of(tmp_path).routes}
    composed = routes[("GET", "/api/things")]
    assert composed.evidence.start_line == 5, "the handler line"
    assert [v.start_line for v in composed.via] == [4], "and the mount line it rests on"
    assert routes[("GET", "/items")].evidence.start_line == 7
    assert routes[("DELETE", "/items")].evidence.start_line == 8
    assert routes[("GET", "/items")].via == ()


def test_the_respelling_upgrade_is_attributed_with_the_right_words(tmp_path: Path) -> None:
    """A 1.3-era base holds the mount-relative `GET /things`; this build emits
    `GET /api/things`. The pair is one removal and one addition of an OLD
    kind, so the attribution sentence must cover re-spelling, not only new
    kinds."""
    write(
        tmp_path,
        "src/app.ts",
        "import express, { Router } from 'express';\nconst app = express();\n"
        "const r = Router();\napp.use('/api', r);\nr.get('/things', h);\n",
    )
    head = build_lock(graph_of(tmp_path), __version__).lockfile
    old_base = Lockfile.parse(
        "# svarupa 0.1.0\n# schema 1.3\n# grammars typescript@0.23.2\n"
        "endpoint\tGET /things\tsrc\nmodule\tsrc\nrole\tsrc\tapi\n"
    )
    delta = diff(old_base, head)
    note = next(d for d in delta.diagnostics if d.code == "SVA-L-013")
    assert "spells differently" in note.message
