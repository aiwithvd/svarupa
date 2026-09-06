"""Review #16 regressions: each test is a demonstration the review made.

The three lessons: a vocabulary hit is not a fact until resolution and
type-only-ness have been asked; a boundary's context is a path, anchored at
the compose file and normalised with path operations; and what the engine
cannot draw honestly it reports without withholding the view.
"""

from __future__ import annotations

from pathlib import Path

from svarupa.build import Graph, build, build_context_of, module_roles
from svarupa.cli import main
from svarupa.cluster import cluster
from svarupa.derive import derive_all
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


def produced_of(root: Path):
    g = graph_of(root)
    return g, derive_all(g, cluster(g))[0]


# --- F1: the System view draws with externals -----------------------------------


def test_the_system_view_draws_with_externals_stacked(tmp_path: Path) -> None:
    """Two services with two externals each: at HEAD-1 the corridor drop into
    the lower external crossed the upper one and the view was withheld."""
    write(tmp_path, "api/__init__.py", "")
    write(tmp_path, "api/db.py", "import openai\nfrom motor.motor_asyncio import X\n")
    write(tmp_path, "agent/__init__.py", "")
    write(tmp_path, "agent/llm.py", "import openai\nfrom motor.motor_asyncio import X\n")
    write(
        tmp_path,
        "docker-compose.yml",
        "services:\n  api:\n    build: .\n  agent:\n    build: .\n  redis:\n    image: redis:7\n",
    )
    assert main([str(tmp_path)]) == 0
    _, produced = produced_of(tmp_path)
    lo = lay_out_set(produced[DiagramKind.DEPLOY_TOPOLOGY], Style())
    assert not lo.withheld


# --- F2/F3: INFO never withholds; waypoints are not intruders --------------------


def _service_with_skipping_imports(root: Path) -> None:
    for name, body in (
        ("a", "from svc import b\nfrom svc import c\nfrom svc import d\n"),
        ("b", "from svc import c\n"),
        ("c", "from svc import d\n"),
        ("d", "x = 1\n"),
    ):
        write(root, f"svc/{name}.py", body)
    write(root, "svc/__init__.py", "")
    write(root, "docker-compose.yml", "services:\n  svc:\n    build:\n      context: ./svc\n")


def test_a_skipping_import_inside_a_service_keeps_its_boundary_and_view(tmp_path: Path) -> None:
    _service_with_skipping_imports(tmp_path)
    assert main([str(tmp_path)]) == 0
    _, produced = produced_of(tmp_path)
    lo = lay_out_set(produced[DiagramKind.ARCHITECTURE], Style())
    assert not lo.withheld, "an INFO about a boundary must never withhold a view"
    assert not any(d.code == "SVA-G-014" for d in lo.problems), (
        "a waypoint is a bend in a line, not an intruder"
    )
    assert any(c.regions for c in lo.canvases.values()), "the boundary is drawn"


def test_an_info_diagnostic_does_not_withhold() -> None:
    """A canvas whose only findings are INFO stays drawn."""
    from svarupa.derive.base import DiagramNode, DiagramSet, DiagramSpec, Region
    from svarupa.layout import lay_out_set as lay
    from svarupa.model import Evidence

    ev = (Evidence("docker-compose.yml", 2, 2),)
    mods = [
        DiagramNode(id=f"m{i}", label=f"m{i}", kind="module", evidence=ev, sublabel="")
        for i in range(2)
    ]
    # Two regions over the same members overlap: one is skipped with an INFO.
    spec = DiagramSpec(
        kind=DiagramKind.ARCHITECTURE,
        id="/spec/root",
        title="t",
        nodes=tuple(mods),
        edges=(),
        regions=(
            Region(id="svc:a", label="a", members=("m0", "m1"), evidence=ev),
            Region(id="svc:b", label="b", members=("m0", "m1"), evidence=ev),
        ),
    )
    lo = lay(
        DiagramSet(DiagramKind.ARCHITECTURE, "/spec/root", {"/spec/root": spec}, ()), Style()
    )
    assert not lo.withheld
    assert [d.code for d in lo.problems] == ["SVA-G-014"]


# --- F4: a name is not an object, at the vocabulary -----------------------------


def test_a_local_package_named_like_a_vocabulary_key_is_not_external(tmp_path: Path) -> None:
    write(tmp_path, "jwt/__init__.py", "def decode(t):\n    return t\n")
    write(tmp_path, "app/__init__.py", "")
    write(tmp_path, "app/auth.py", "import jwt\nfrom stripe import pay\n")
    write(tmp_path, "stripe/__init__.py", "def pay():\n    pass\n")
    g = graph_of(tmp_path)
    assert g.externals == (), "both names resolve to this repository's own packages"
    assert "auth" not in module_roles(g).get("app", ())


def test_a_tsconfig_alias_hit_is_not_external(tmp_path: Path) -> None:
    write(
        tmp_path,
        "web/tsconfig.json",
        '{"compilerOptions": {"baseUrl": ".", "paths": {"stripe": ["src/lib/stripe"]}}}',
    )
    write(tmp_path, "web/src/lib/stripe.ts", "export const pay = () => 1;\n")
    write(
        tmp_path, "web/src/app.ts", "import { pay } from 'stripe';\nexport const x = pay();\n"
    )
    g = graph_of(tmp_path)
    assert not [x for x in g.externals if x.label == "Stripe"]


def test_a_type_only_import_talks_to_nothing(tmp_path: Path) -> None:
    write(
        tmp_path,
        "web/src/types.ts",
        "import type { Redis } from 'ioredis';\nexport type R = Redis;\n",
    )
    g = graph_of(tmp_path)
    assert g.externals == ()


# --- F5: contexts are paths, anchored at the compose file -------------------------


def test_build_contexts_are_anchored_and_normalised(tmp_path: Path) -> None:
    write(
        tmp_path,
        "deploy/docker-compose.yml",
        "services:\n  api:\n    build:\n      context: ./api\n",
    )
    write(tmp_path, "deploy/api/main.py", "x = 1\n")
    write(tmp_path, "api/decoy.py", "x = 2\n")
    write(
        tmp_path,
        "docker-compose.yml",
        "services:\n  esc:\n    build:\n      context: ../api\n  dot:\n    build:\n      context: ./.web\n",
    )
    write(tmp_path, ".web/index.ts", "export const w = 1;\n")
    g = graph_of(tmp_path)
    assert build_context_of(g, "deploy/docker-compose.yml#service.api") == "deploy/api"
    assert build_context_of(g, "docker-compose.yml#service.esc") is None, "escapes the repo"
    assert build_context_of(g, "docker-compose.yml#service.dot") == ".web"


def test_a_group_with_outsiders_is_not_inside_a_boundary(tmp_path: Path) -> None:
    """At the top level a box stands for several modules; it is inside a
    service only if every one of them is under the context."""
    for i in range(3):
        write(tmp_path, f"api/p{i}/__init__.py", "")
        write(tmp_path, f"api/p{i}/m.py", "from web.q0 import m\n")
        write(tmp_path, f"web/q{i}/__init__.py", "")
        write(tmp_path, f"web/q{i}/m.py", "x = 1\n")
    write(
        tmp_path,
        "docker-compose.yml",
        "services:\n  backend:\n    build:\n      context: ./api\n",
    )
    _, produced = produced_of(tmp_path)
    root = produced[DiagramKind.ARCHITECTURE].root_spec
    member_group = {n.id: n for n in root.nodes}
    for region in root.regions:
        for m in region.members:
            box = member_group[m]
            assert box.kind != "group" or all(
                mod.startswith("api/") for mod in _modules_of_group(produced, m)
            ), f"boundary {region.label} wraps a group with code outside api/"


def _modules_of_group(produced, anchor: str) -> list[str]:
    from svarupa.derive.base import spec_id

    child = produced[DiagramKind.ARCHITECTURE].specs.get(spec_id(anchor))
    return [n.id for n in child.nodes if not n.id.startswith("ext:")] if child else [anchor]


# --- F6: the System view counts what it has -------------------------------------


def test_compose_stores_and_import_stores_are_one_thing(tmp_path: Path) -> None:
    write(tmp_path, "app/__init__.py", "")
    write(tmp_path, "app/db.py", "import psycopg2\nimport sqlalchemy\nimport redis\n")
    write(
        tmp_path,
        "docker-compose.yml",
        "services:\n  app:\n    build:\n      context: ./app\n"
        "  postgres:\n    image: postgres:16\n  cache:\n    image: redis:7\n",
    )
    _, produced = produced_of(tmp_path)
    root = produced[DiagramKind.DEPLOY_TOPOLOGY].root_spec
    kinds = [n.kind for n in root.nodes]
    assert kinds.count("database") == 2, [(n.label, n.kind) for n in root.nodes]
    assert not any(n.id.startswith("ext:") for n in root.nodes), (
        "psycopg is the postgres service, sqlalchemy attaches to the only SQL store"
    )
    targets = {e.dst for e in root.edges if e.src.endswith("#service.app")}
    assert "docker-compose.yml#service.postgres" in targets
    assert "docker-compose.yml#service.cache" in targets
    postgres = next(n for n in root.nodes if n.label == "postgres")
    assert postgres.sublabel == "PostgreSQL · postgres:16"


def test_image_only_services_are_services_not_backends(tmp_path: Path) -> None:
    write(tmp_path, "docker-compose.yml", "services:\n  grafana:\n    image: grafana/grafana\n")
    write(tmp_path, "x.py", "x = 1\n")
    _, produced = produced_of(tmp_path)
    root = produced[DiagramKind.DEPLOY_TOPOLOGY].root_spec
    assert [n.kind for n in root.nodes] == ["service"]


# --- S1: dotted keys stay distinct facts -----------------------------------------


def test_two_google_products_in_one_file_are_two_facts(tmp_path: Path) -> None:
    write(
        tmp_path,
        "a.py",
        "import google.generativeai as genai\nfrom google.cloud import storage\n",
    )
    g = graph_of(tmp_path)
    assert sorted(x.label for x in g.externals) == ["Gemini API", "Google Cloud"]


# --- S2/C2: what the artifact says ------------------------------------------------


def test_emit_carries_the_kind_scopes_and_boundary_data(tmp_path: Path) -> None:
    _service_with_skipping_imports(tmp_path)
    write(tmp_path, "svc/ext.py", "import redis\n")
    out = tmp_path / "out"
    assert main([str(tmp_path), "--out", str(out)]) == 0
    html = (out / "index.html").read_text(encoding="utf8")
    assert 'class="sv-node sv-kind-' in html, "the kind class is on the group"
    # The element form, not the bare attribute: the viewer's own script now
    # contains the selector text `[data-scope="child"]`, which satisfied the
    # old assertion with the scopes deleted.
    assert '<g data-scope="parent">' in html and '<g data-scope="child">' in html
    # A boundary's kind is its own (service, stage), not the generic word:
    # the passport called a stage frame a boundary (review #17 F10).
    assert 'data-kind="service"' in html
    assert "sv-boundary sv-kind-service" in html


def test_a_boundary_cites_the_build_line(tmp_path: Path) -> None:
    _service_with_skipping_imports(tmp_path)
    _, produced = produced_of(tmp_path)
    regions = [r for s in produced[DiagramKind.ARCHITECTURE].specs.values() for r in s.regions]
    assert regions
    assert regions[0].evidence[0].start_line == 3, "the `build:` key line, not the service key"


# --- pins for the layout guards the integration fixtures do not reach --------------


def test_flow_keeps_an_external_next_to_the_service_that_uses_it() -> None:
    """In columns, sinking every external to one trailing column stacks them
    and a corridor drop into the lower one crosses the upper one. The flow
    engine must leave externals at their inferred column."""
    from svarupa.derive.base import DiagramEdge, DiagramNode, DiagramSpec
    from svarupa.layout.engines import flow
    from svarupa.model import Evidence

    ev = (Evidence("docker-compose.yml", 2, 2),)

    def node(i: str, kind: str, external: bool = False) -> DiagramNode:
        return DiagramNode(
            id=i,
            label=i,
            kind=kind,
            evidence=ev,
            attrs=(("external", kind),) if external else (),
        )

    spec = DiagramSpec(
        kind=DiagramKind.DEPLOY_TOPOLOGY,
        id="/spec/root",
        title="t",
        nodes=(
            node("a", "backend"),
            node("b", "backend"),
            node("ext1", "cloud", True),
            node("ext2", "database", True),
        ),
        edges=(
            DiagramEdge("a", "b", "depends on", ev),
            DiagramEdge("a", "ext1", "calls", ev, variant="dashed"),
            DiagramEdge("b", "ext2", "reads/writes", ev, variant="dashed"),
        ),
    )
    canvas = flow(spec, Style())
    x = {b.id: b.x for b in canvas.boxes}
    assert x["ext1"] < x["ext2"], "ext1 is used one column earlier than ext2 and stays there"
    assert x["ext1"] == x["b"]


def test_a_waypoint_inside_a_boundary_rectangle_is_not_an_intruder() -> None:
    from svarupa.derive.base import DiagramSpec, Region
    from svarupa.layout.engines import _regions
    from svarupa.layout.geometry import Box
    from svarupa.model import Evidence

    ev = (Evidence("docker-compose.yml", 2, 2),)

    def b(i: str, x: int, y: int, w: int) -> Box:
        return Box(id=i, label=i, full_label=i, kind="module", x=x, y=y, w=w, h=44, evidence=ev)

    dummy = "\x00dummy\x00m1\x00m2\x001"
    boxes = [b("m1", 100, 100, 100), b("m2", 100, 300, 100), b(dummy, 120, 200, 8)]
    spec = DiagramSpec(
        kind=DiagramKind.ARCHITECTURE,
        id="/spec/x",
        title="x",
        nodes=(),
        edges=(),
        regions=(Region(id="svc:a", label="a", members=("m1", "m2"), evidence=ev),),
    )
    diags: list = []
    drawn = _regions(spec, boxes, Style(), diags, frozenset({dummy}))
    assert len(drawn) == 1 and not diags, "a bend in a line is not a box"
    assert _regions(spec, boxes, Style(), [], frozenset()) == (), (
        "without the waypoint set the same dummy would be an intruder"
    )


def test_every_ordinary_node_group_carries_its_kind_class(tmp_path: Path) -> None:
    import re

    _service_with_skipping_imports(tmp_path)
    out = tmp_path / "out"
    assert main([str(tmp_path), "--out", str(out)]) == 0
    html = (out / "index.html").read_text(encoding="utf8")
    groups = re.findall(r'<g class="sv-node([^"]*)" data-id=', html)
    assert groups
    for classes in groups:
        assert re.match(r" sv-kind-[a-z-]+", classes), (
            f"a node group without its kind: {classes!r}"
        )
