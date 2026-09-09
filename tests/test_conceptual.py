"""Wave A of the conceptual redesign: Archify's model on our evidence.

What a box IS (frontend/backend/database/cloud/security/messagebus) comes from
evidence the vocabulary knows; what it SAYS under its name is a semantic
sublabel, never a path; what WRAPS it is a compose service with a build
context; what an arrow SAYS is a short verb with the count in the note.
Everything here cites, and everything that cannot be drawn honestly is
reported rather than drawn.
"""

from __future__ import annotations

from pathlib import Path

from svarupa.build import Graph, build, module_roles
from svarupa.cluster import cluster
from svarupa.derive import derive_all
from svarupa.derive.base import DiagramKind, DiagramNode
from svarupa.detect import detect
from svarupa.extract import declared_dependencies, extract
from svarupa.extract.vocabulary import classify_import
from svarupa.layout import lay_out_set
from svarupa.layout.geometry import Style


def write(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf8")


def graph_of(root: Path) -> Graph:
    scan = detect(root)
    return build(scan, extract(scan, declared_dependencies(scan)), strict=False)


def boxes_of(root: Path, kind: DiagramKind = DiagramKind.ARCHITECTURE) -> list[DiagramNode]:
    g = graph_of(root)
    produced, _ = derive_all(g, cluster(g))
    return [n for spec in produced[kind].specs.values() for n in spec.nodes]


API = (
    "from fastapi import APIRouter\n"
    "from motor.motor_asyncio import AsyncIOMotorClient\n"
    "import openai\n"
    "router = APIRouter()\n"
    "\n"
    '@router.get("/items")\n'
    "def items():\n"
    "    return []\n"
)


def _service_repo(root: Path) -> None:
    write(root, "api/__init__.py", "")
    write(root, "api/routes.py", API)
    write(root, "web/__init__.py", "")
    write(root, "web/App.tsx", "import React from 'react';\nexport const App = () => 1;\n")
    write(root, "web/uses.py", "from api import routes\n")
    write(root, "guard/__init__.py", "")
    write(root, "guard/jwt_check.py", "import jwt\n")
    write(root, "guard/uses.py", "from api import routes\n")
    write(
        root,
        "docker-compose.yml",
        "services:\n"
        "  backend:\n"
        "    build:\n"
        "      context: ./api\n"
        "  redis:\n"
        "    image: redis:7\n",
    )


# --- vocabulary -------------------------------------------------------------


def test_the_vocabulary_classifies_by_package_root_and_dotted_prefix() -> None:
    def c(spec: str, lang: str = "python") -> tuple[str, str] | None:
        hit = classify_import(spec, lang)
        return None if hit is None else (hit[0], hit[1])

    assert c("motor.motor_asyncio") == ("database", "MongoDB")
    assert c("fastapi.security") == ("security", "FastAPI security")
    assert c("fastapi") is None, "the root alone is not security"
    assert c("google.cloud.storage") == ("cloud", "Google Cloud")
    assert c("google.generativeai") == ("cloud", "Gemini API")
    assert c("google") is None
    assert c("@google-cloud/storage", "typescript") == ("cloud", "Google Cloud")
    assert c("@google-cloud/pubsub", "typescript") == ("messagebus", "Pub/Sub"), (
        "the longest matching key wins, so Pub/Sub is not just Google Cloud"
    )
    assert c("@prisma/client", "typescript") == ("database", "Prisma database")
    assert c("requests") is None, "a library is not a component"
    assert classify_import("google.cloud.storage", "python")[2] == "google.cloud", (  # type: ignore[index]
        "the matched key identifies the fact, not the bare root"
    )


# --- externals and roles --------------------------------------------------------


def test_externals_cite_the_import_line_and_relative_imports_are_never_external(
    tmp_path: Path,
) -> None:
    write(tmp_path, "svc/__init__.py", "")
    write(tmp_path, "svc/db.py", "import redis\nfrom . import redis as local\nimport redis\n")
    g = graph_of(tmp_path)
    assert [(x.category, x.label, x.evidence.start_line) for x in g.externals] == [
        ("database", "Redis", 1)
    ], "one fact per package, at its first import; the relative import is ours"


def test_roles_from_imports_and_their_archetypes(tmp_path: Path) -> None:
    _service_repo(tmp_path)
    g = graph_of(tmp_path)
    roles = module_roles(g)
    assert roles["api"] == ("api",)
    assert roles["web"] == ("frontend",)
    assert roles["guard"] == ("auth",)
    kinds = {n.id: n.kind for n in boxes_of(tmp_path) if n.kind != "group"}
    assert kinds["api"] == "backend"
    assert kinds["web"] == "frontend"
    assert kinds["guard"] == "security"


# --- sublabels ------------------------------------------------------------------


def test_sublabels_are_semantic_and_never_paths(tmp_path: Path) -> None:
    _service_repo(tmp_path)
    nodes = [n for n in boxes_of(tmp_path) if n.kind != "group"]
    by_id = {n.id: n for n in nodes}
    assert by_id["api"].sublabel == "FastAPI · 1 route"
    assert by_id["web"].sublabel == "React"
    assert by_id["guard"].sublabel == "JWT"
    for n in nodes:
        assert "/" not in n.sublabel or n.sublabel.startswith("via "), (
            f"a path leaked into a sublabel: {n.sublabel!r}"
        )
        assert ".py" not in n.sublabel and ".ts" not in n.sublabel


# --- external boxes and verbs ---------------------------------------------------


def test_external_stores_and_apis_are_boxes_with_dashed_verb_edges(tmp_path: Path) -> None:
    _service_repo(tmp_path)
    g = graph_of(tmp_path)
    produced, _ = derive_all(g, cluster(g))
    specs = produced[DiagramKind.ARCHITECTURE].specs
    ext = {n.id: n for spec in specs.values() for n in spec.nodes if n.id.startswith("ext:")}
    assert "ext:database:MongoDB" in ext and ext["ext:database:MongoDB"].kind == "database"
    assert "ext:cloud:OpenAI API" in ext and ext["ext:cloud:OpenAI API"].kind == "cloud"
    assert ext["ext:database:MongoDB"].sublabel == "via motor"
    assert ext["ext:database:MongoDB"].evidence[0].file == "api/routes.py"
    edges = [e for spec in specs.values() for e in spec.edges if e.dst.startswith("ext:")]
    assert edges
    assert {e.label for e in edges} <= {"reads/writes", "publishes", "calls"}
    assert all(e.variant == "dashed" for e in edges)
    # Redis is a compose datastore here, not an import: it belongs to the
    # system view, and nothing invents an import-derived Redis box.
    assert not any("Redis" in nid for nid in ext)


def test_edge_labels_are_verbs_and_counts_live_in_notes(tmp_path: Path) -> None:
    _service_repo(tmp_path)
    g = graph_of(tmp_path)
    produced, _ = derive_all(g, cluster(g))
    import re

    for kind in (
        DiagramKind.ARCHITECTURE,
        DiagramKind.MODULE_DEPS,
        DiagramKind.DATA_FLOW,
        DiagramKind.REQUEST_FLOW,
    ):
        for spec in produced[kind].specs.values():
            for e in spec.edges:
                assert not any(ch.isdigit() for ch in e.label), (e.label, spec.id)
                if re.match(r"\d+ imports?\b", e.note):
                    # An import arrow is silent in every view (review #21 N9
                    # found 79 `imports` labels left in the two flow views).
                    assert e.label == "", (kind, spec.id, e.src, e.dst, e.label)
                if e.variant == "default" and not e.dst.startswith("ext:"):
                    # Structural arrows are silent: 76 identical `imports` on
                    # one view doubled the ink (review #20 M5). The legend says
                    # once what a solid arrow is; the count lives in the note.
                    assert e.label == "", "structural arrows carry no drawn word"
                    assert e.note, "the count lives in the note, not the label"


# --- boundaries -------------------------------------------------------------------


def test_a_compose_service_with_a_build_context_wraps_its_modules(tmp_path: Path) -> None:
    _service_repo(tmp_path)
    g = graph_of(tmp_path)
    produced, _ = derive_all(g, cluster(g))
    regions = [
        r for spec in produced[DiagramKind.ARCHITECTURE].specs.values() for r in spec.regions
    ]
    assert regions, "the backend service builds ./api, so it wraps api"
    backend = next(r for r in regions if r.label == "backend")
    assert "api" in backend.members
    assert backend.evidence[0].file == "docker-compose.yml"


def test_a_root_build_context_wraps_nothing(tmp_path: Path) -> None:
    """Two services built from `.` would both wrap every module and overlap;
    a boundary around the whole diagram says nothing anyway."""
    _service_repo(tmp_path)
    write(
        tmp_path,
        "docker-compose.yml",
        "services:\n  a:\n    build: .\n  b:\n    build:\n      context: .\n",
    )
    g = graph_of(tmp_path)
    produced, _ = derive_all(g, cluster(g))
    assert not any(spec.regions for spec in produced[DiagramKind.ARCHITECTURE].specs.values())


def test_drawn_boundaries_contain_their_members_and_no_outsiders(tmp_path: Path) -> None:
    _service_repo(tmp_path)
    g = graph_of(tmp_path)
    produced, _ = derive_all(g, cluster(g))
    lo = lay_out_set(produced[DiagramKind.ARCHITECTURE], Style())
    assert not lo.withheld
    drawn = [(c, r) for c in lo.canvases.values() for r in c.regions]
    assert drawn, "at least one boundary must be drawable on this fixture"
    for canvas, region in drawn:
        members = set(region.members)
        for b in canvas.boxes:
            if b.id in canvas.waypoints:
                continue
            inside = (
                b.x >= region.x
                and b.right <= region.right
                and b.y >= region.y
                and b.bottom <= region.bottom
            )
            intersects = (
                b.x < region.right
                and b.right > region.x
                and b.y < region.bottom
                and b.bottom > region.y
            )
            if b.id in members:
                assert inside, f"member {b.id} outside its boundary"
            else:
                assert not intersects, f"outsider {b.id} inside boundary {region.label}"


# --- labels settle, never smear ---------------------------------------------------


def test_route_labels_never_collide_and_a_cycle_still_lays_out(tmp_path: Path) -> None:
    write(tmp_path, "a/__init__.py", "")
    write(tmp_path, "a/x.py", "from b import y\n")
    write(tmp_path, "b/__init__.py", "")
    write(tmp_path, "b/y.py", "from a import x\n")
    g = graph_of(tmp_path)
    produced, _ = derive_all(g, cluster(g))
    lo = lay_out_set(produced[DiagramKind.MODULE_DEPS], Style())
    assert not lo.withheld
    from svarupa.layout.validate import validate

    for c in lo.canvases.values():
        # Whatever the engine kept, no two label masks may overlap and none
        # may cover a box: the gate is the validator, and the engine's job is
        # to place or drop labels so it stays silent.
        assert not [d for d in validate(c, Style()) if d.code == "SVA-G-013"]
        assert len([r for r in c.routes if r.label_at is not None]) <= len(c.routes)


# --- system view --------------------------------------------------------------------


def test_the_system_view_types_services_and_attaches_externals(tmp_path: Path) -> None:
    _service_repo(tmp_path)
    nodes = {n.id: n for n in boxes_of(tmp_path, DiagramKind.DEPLOY_TOPOLOGY)}
    backend = next(n for n in nodes.values() if n.label == "backend")
    assert backend.kind == "backend"
    assert backend.sublabel.startswith("built from api/")
    assert "1 route" in backend.sublabel
    redis = next(n for n in nodes.values() if n.label == "redis")
    assert redis.kind == "database"
    assert any(n.kind == "cloud" and n.label == "OpenAI API" for n in nodes.values())


def test_services_built_from_the_root_claim_no_per_service_counts(tmp_path: Path) -> None:
    """Two services from `.` would each claim every route in the repository."""
    _service_repo(tmp_path)
    write(
        tmp_path,
        "docker-compose.yml",
        "services:\n  a:\n    build: .\n  b:\n    build:\n      context: .\n",
    )
    nodes = {n.label: n for n in boxes_of(tmp_path, DiagramKind.DEPLOY_TOPOLOGY)}
    assert nodes["a"].sublabel == "built from the repository root"
    assert "route" not in nodes["b"].sublabel


def test_services_sharing_a_build_context_each_get_the_stores_of_the_code(
    tmp_path: Path,
) -> None:
    """Two services built from `.` with no readable Dockerfile: the context is
    all the evidence there is, so both ship the code that talks to the store
    (review #20 M2 gave `api` no database and cited its file under `agent`)."""
    _service_repo(tmp_path)
    write(tmp_path, "api/db.py", "import psycopg2\n")
    write(
        tmp_path,
        "docker-compose.yml",
        "services:\n  a:\n    build: .\n  b:\n    build:\n      context: .\n",
    )
    g = graph_of(tmp_path)
    produced, _ = derive_all(g, cluster(g))
    spec = produced[DiagramKind.DEPLOY_TOPOLOGY].root_spec
    store = [e for e in spec.edges if e.dst == "ext:database:PostgreSQL"]
    assert sorted(e.src.split("#")[-1] for e in store) == ["service.a", "service.b"], store
    assert all(e.evidence[0].file == "api/db.py" for e in store)
    assert store[0].evidence == store[1].evidence, "the same line justifies both"


def test_a_dockerfile_says_which_code_a_service_ships(tmp_path: Path) -> None:
    """Review #21 N1: two services from `.` with `Dockerfile.api` (COPY api)
    and `Dockerfile.agent` (COPY agent) ship disjoint code; the context alone
    drew `api -> Gemini API` from a file only the agent image copies. Each
    arrow cites the import and the COPY line that puts it in the image."""
    _service_repo(tmp_path)
    write(tmp_path, "api/db.py", "import psycopg2\n")
    write(tmp_path, "agent/__init__.py", "")
    write(tmp_path, "agent/llm.py", "import openai\n")
    write(
        tmp_path,
        "docker-compose.yml",
        "services:\n"
        "  api:\n    build:\n      context: .\n      dockerfile: Dockerfile.api\n"
        "  agent:\n    build:\n      context: .\n      dockerfile: Dockerfile.agent\n"
        "  all:\n    build:\n      context: .\n      dockerfile: Dockerfile.all\n",
    )
    write(
        tmp_path,
        "Dockerfile.api",
        "FROM python:3.12\nCOPY requirements.txt .\nCOPY api /app/api\n",
    )
    write(
        tmp_path,
        "Dockerfile.agent",
        "FROM python:3.12 AS build\nCOPY agent/ /app/agent\nFROM python:3.12\n"
        "COPY --from=build /app /app\n",
    )
    write(tmp_path, "Dockerfile.all", 'FROM python:3.12\nCOPY ["." , "/app"]\n')
    g = graph_of(tmp_path)
    api = g.nodes["docker-compose.yml#service.api"]
    assert api.attr("dockerfile") == "Dockerfile.api"
    boxes = {n.label: n for n in boxes_of(tmp_path, DiagramKind.DEPLOY_TOPOLOGY)}
    assert boxes["api"].sublabel.startswith("ships api/"), boxes["api"].sublabel
    assert boxes["all"].sublabel.startswith("ships the repository"), boxes["all"].sublabel
    assert api.attr("ships") == "requirements.txt:2\napi:3", (
        "every copied source, with its line"
    )
    agent = g.nodes["docker-compose.yml#service.agent"]
    assert agent.attr("ships") == "agent:2", "the --from copy is not repository code"
    assert g.nodes["docker-compose.yml#service.all"].attr("ships") == ":2"
    produced, _ = derive_all(g, cluster(g))
    spec = produced[DiagramKind.DEPLOY_TOPOLOGY].root_spec
    by_src: dict[str, list[str]] = {}
    for e in spec.edges:
        if e.dst.startswith("ext:"):
            by_src.setdefault(e.src.split("#")[-1], []).append(e.dst)

    def ext_of(prefix: str) -> list[str]:
        return sorted(
            {
                f"ext:{x.category}:{x.label}"
                for x in g.externals
                if x.file.startswith(prefix)
                and x.category in ("database", "messagebus", "cloud")
            }
        )

    # Each service talks to exactly what the code its image copies imports.
    assert (
        sorted(by_src["service.api"]) == ext_of("api/")
        and "ext:database:PostgreSQL" in by_src["service.api"]
    )
    assert sorted(by_src["service.agent"]) == ext_of("agent/") == ["ext:cloud:OpenAI API"]
    assert sorted(by_src["service.all"]) == sorted(set(ext_of("api/")) | set(ext_of("agent/")))
    api_store = next(
        e
        for e in spec.edges
        if e.src.endswith("service.api") and e.dst == "ext:database:PostgreSQL"
    )
    files = {ev.file for ev in api_store.evidence}
    assert files == {"api/db.py", "Dockerfile.api"}, files
    assert any(ev.file == "Dockerfile.api" and ev.start_line == 3 for ev in api_store.evidence)
    # A Dockerfile that copies nothing from the repository ships no module.
    write(tmp_path, "Dockerfile.api", "FROM python:3.12\nRUN pip install x\n")
    g2 = graph_of(tmp_path)
    assert g2.nodes["docker-compose.yml#service.api"].attr("ships") == ""
    spec2 = derive_all(g2, cluster(g2))[0][DiagramKind.DEPLOY_TOPOLOGY].root_spec
    assert not any(
        e.src.endswith("service.api") and e.dst.startswith("ext:") for e in spec2.edges
    )


# --- unit pins the integration fixtures cannot reach -------------------------------


def test_a_plain_module_says_how_many_files_it_holds(tmp_path: Path) -> None:
    _service_repo(tmp_path)
    write(tmp_path, "util/__init__.py", "")
    write(tmp_path, "util/helpers.py", "x = 1\n")
    write(tmp_path, "util/uses.py", "from api import routes\n")
    nodes = {n.id: n for n in boxes_of(tmp_path) if n.kind != "group"}
    assert nodes["util"].sublabel == "3 files"
    assert all(n.sublabel != n.id for n in nodes.values()), "a sublabel is never the module id"


def test_a_boundary_that_would_enclose_an_outsider_is_not_drawn() -> None:
    """Hand-built geometry: two members on two rows with an outsider sitting in
    the rectangle between them. The boundary is skipped and reported, never
    drawn around the outsider."""
    from svarupa.derive.base import DiagramSpec, Region
    from svarupa.layout.engines import _regions
    from svarupa.layout.geometry import Box
    from svarupa.model import Evidence

    ev = (Evidence("docker-compose.yml", 2, 2),)

    def b(i: str, x: int, y: int, w: int) -> Box:
        return Box(id=i, label=i, full_label=i, kind="module", x=x, y=y, w=w, h=44, evidence=ev)

    boxes = [b("m1", 100, 100, 100), b("m2", 100, 300, 400), b("out", 300, 100, 100)]
    spec = DiagramSpec(
        kind=DiagramKind.ARCHITECTURE,
        id="/spec/x",
        title="x",
        nodes=(),
        edges=(),
        regions=(Region(id="svc:a", label="a", members=("m1", "m2"), evidence=ev),),
    )
    diags: list = []
    assert _regions(spec, boxes, Style(), diags) == ()
    assert [d.code for d in diags] == ["SVA-G-014"]
    # Without the outsider the same boundary is drawn and contains both.
    diags.clear()
    (region,) = _regions(spec, boxes[:2], Style(), diags)
    assert region.members == ("m1", "m2") and not diags


def test_rows_keep_a_boundarys_members_adjacent() -> None:
    from svarupa.derive.base import DiagramSpec, Region
    from svarupa.layout.engines import _group_rows_by_region
    from svarupa.layout.geometry import Box
    from svarupa.model import Evidence

    ev = (Evidence("docker-compose.yml", 2, 2),)

    def b(i: str) -> Box:
        return Box(
            id=i, label=i, full_label=i, kind="module", x=0, y=0, w=10, h=10, evidence=ev
        )

    spec = DiagramSpec(
        kind=DiagramKind.ARCHITECTURE,
        id="/spec/x",
        title="x",
        nodes=(),
        edges=(),
        regions=(Region(id="svc:a", label="a", members=("m1", "m2"), evidence=ev),),
    )
    rows = [[b("out"), b("m1"), b("other"), b("m2")]]
    assert [x.id for x in _group_rows_by_region(rows, spec)[0]] == ["m1", "m2", "out", "other"]


def test_every_external_arrow_carries_its_verb(tmp_path: Path) -> None:
    """One verb per target left both MongoDB arrows unlabelled on the demo
    when the label gate dropped that one (review #20 C3). Every store arrow
    carries the verb; the gate drops the ones that would collide."""
    _service_repo(tmp_path)
    write(tmp_path, "web/db.py", "import openai\n")
    write(tmp_path, "guard/db.py", "import openai\n")
    g = graph_of(tmp_path)
    produced, _ = derive_all(g, cluster(g))
    for spec in produced[DiagramKind.ARCHITECTURE].specs.values():
        to_openai = [e for e in spec.edges if e.dst == "ext:cloud:OpenAI API"]
        if len(to_openai) > 1:
            assert all(e.label == "calls" and e.note == "calls" for e in to_openai)
            break
    else:
        raise AssertionError("fixture produced no shared external target")


def test_external_boxes_sit_below_every_module(tmp_path: Path) -> None:
    """What a system talks to is drawn below what talks; left to inference a
    store used from inside a dependency cycle sat in the cycle's own row."""
    _service_repo(tmp_path)
    write(tmp_path, "api/back.py", "from web import uses\n")  # a cycle: api <-> web
    g = graph_of(tmp_path)
    produced, _ = derive_all(g, cluster(g))
    lo = lay_out_set(produced[DiagramKind.ARCHITECTURE], Style())
    checked = 0
    for c in lo.canvases.values():
        ext = [b for b in c.boxes if b.id.startswith("ext:")]
        mods = [b for b in c.boxes if not b.id.startswith("ext:") and b.id not in c.waypoints]
        if ext and mods:
            assert min(b.y for b in ext) > max(b.bottom for b in mods), c.spec_id
            checked += 1
    assert checked, "no canvas held both modules and externals"


def test_a_one_service_topology_explains_its_single_box(tmp_path: Path) -> None:
    """Review #22 A5: mcp-finnhub's deploy topology was one box with no arrow
    and nothing saying why."""
    write(tmp_path, "svc/__init__.py", "")
    write(tmp_path, "svc/main.py", "import httpx\n")
    write(tmp_path, "docker-compose.yml", "services:\n  svc:\n    build: ./svc\n")
    g = graph_of(tmp_path)
    produced, _ = derive_all(g, cluster(g))
    spec = produced[DiagramKind.DEPLOY_TOPOLOGY].root_spec
    assert len(spec.nodes) == 1 and not spec.edges
    assert spec.subtitle.startswith("1 backend; it declares no dependency"), spec.subtitle
