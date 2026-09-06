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
        'demo = "not.this:one"\n'
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
    assert "# schema 1.2" in text


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
