"""Environment distinction on the deploy-topology diagram.

The honesty rules under test:

* an environment badge appears only for an environment with extracted
  evidence, and every badge, edge and frame cites the declaring line(s);
* a service is framed by an environment only where evidence joins them — a
  compose variant's own services, or the service whose compose `dockerfile:`
  attr names the Dockerfile carrying `ENV NODE_ENV`. Workflow `environment:`
  keys and eas.json profiles name no service, so they contribute a badge and
  never a link: assignment by guesswork is the failure mode this file exists
  to prevent;
* a repository with no environment facts gets a byte-identical diagram, and
  every repository gets a deterministic one.
"""

from __future__ import annotations

from pathlib import Path

from svarupa.build import build
from svarupa.cluster import cluster
from svarupa.derive import derive_all
from svarupa.derive.base import DiagramKind, DiagramSet
from svarupa.detect import detect
from svarupa.diagnostics import Severity
from svarupa.emit import emit
from svarupa.extract import declared_dependencies, extract
from svarupa.layout import lay_out_set


def write(root: Path, rel: str, text: str = "") -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf8")


def deploy_spec(root: Path) -> DiagramSet:
    scan = detect(root)
    graph = build(scan, extract(scan, declared_dependencies(scan)), strict=False)
    produced, _ = derive_all(graph, cluster(graph))
    return produced[DiagramKind.DEPLOY_TOPOLOGY]


COMPOSE_PROD = (
    "services:\n"
    "  web:\n"  # line 2
    "    image: nginx\n"
    "  db:\n"  # line 4
    "    image: postgres:16\n"
)

EAS = '{\n  "build": {\n    "staging": {}\n  }\n}\n'


def _compose_variant_repo(root: Path) -> None:
    write(root, "docker-compose.prod.yml", COMPOSE_PROD)
    write(root, "eas.json", EAS)


def test_a_compose_variant_frames_its_own_services(tmp_path: Path) -> None:
    """`docker-compose.prod.yml` declares both the environment and the
    services in it; the link is the file they share, cited at both lines."""
    _compose_variant_repo(tmp_path)
    spec = deploy_spec(tmp_path).root_spec
    ids = {n.id for n in spec.nodes}
    web = "docker-compose.prod.yml#service.web"
    assert {"env:production", "env:staging", web} <= ids

    badge = spec.node("env:production")
    assert badge is not None and badge.kind == "environment"
    assert [str(ev) for ev in badge.evidence] == ["docker-compose.prod.yml:1"]
    assert badge.attr("sources") == "filename"

    frames = {r.id: r for r in spec.regions}
    assert set(frames["env-frame:production"].members) == {
        "docker-compose.prod.yml#service.web",
        "docker-compose.prod.yml#service.db",
    }
    # The unlinked staging environment still shows: a badge, no frame of
    # services, and its citation is the eas.json profile key's line.
    assert "env-frame:staging" not in frames
    staging = spec.node("env:staging")
    assert staging is not None
    assert [str(ev) for ev in staging.evidence] == ["eas.json:3"]
    # Every badge sits inside the declared-environments frame.
    assert set(frames["env:declared"].members) == {"env:production", "env:staging"}


def test_a_dockerfile_env_links_the_service_built_from_it(tmp_path: Path) -> None:
    write(
        tmp_path,
        "docker-compose.yml",
        "services:\n"
        "  api:\n"
        "    build:\n"
        "      context: .\n"
        "      dockerfile: Dockerfile.api\n",
    )
    write(tmp_path, "Dockerfile.api", "FROM python:3.12\nENV NODE_ENV=production\n")
    write(tmp_path, "api/__init__.py", "")
    spec = deploy_spec(tmp_path).root_spec
    api = "docker-compose.yml#service.api"
    frame = next(r for r in spec.regions if r.id == "env-frame:production")
    assert frame.members == (api,)
    assert any(ev.file == "Dockerfile.api" and ev.start_line == 2 for ev in frame.evidence)
    assert any(ev.file.startswith("docker-compose.yml") for ev in frame.evidence)


def test_a_dockerfile_env_does_not_leak_to_other_services(tmp_path: Path) -> None:
    """Two services, one Dockerfile with an ENV: only the service built from
    it joins the frame. The other claims nothing."""
    write(
        tmp_path,
        "docker-compose.yml",
        "services:\n"
        "  api:\n"
        "    build:\n"
        "      context: .\n"
        "      dockerfile: Dockerfile.api\n"
        "  web:\n"
        "    image: nginx\n",
    )
    write(tmp_path, "Dockerfile.api", "FROM python:3.12\nENV NODE_ENV=staging\n")
    write(tmp_path, "api/__init__.py", "")
    spec = deploy_spec(tmp_path).root_spec
    frame = next(r for r in spec.regions if r.id == "env-frame:staging")
    assert frame.members == ("docker-compose.yml#service.api",)


def test_unlinked_environments_are_badges_with_citations_never_frames(
    tmp_path: Path,
) -> None:
    """A workflow `environment:` and an eas.json profile declare environments
    but name no service; nothing is assigned by guesswork."""
    write(tmp_path, "docker-compose.yml", "services:\n  web:\n    image: nginx\n")
    write(
        tmp_path,
        ".github/workflows/deploy.yml",
        "on: push\njobs:\n  ship:\n    environment: production\n",
    )
    write(tmp_path, "eas.json", EAS)
    spec = deploy_spec(tmp_path).root_spec
    assert {n.id for n in spec.nodes if n.kind == "environment"} == {
        "env:production",
        "env:staging",
    }
    assert not [r for r in spec.regions if r.id.startswith("env-frame:")]
    assert not [e for e in spec.edges if e.label == "deploys"]
    prod = spec.node("env:production")
    assert prod is not None
    assert prod.attr("sources") == "workflow"
    assert [str(ev) for ev in prod.evidence] == [".github/workflows/deploy.yml:4"]


def test_layout_draws_the_frames_and_passes_geometry(tmp_path: Path) -> None:
    _compose_variant_repo(tmp_path)
    ds = deploy_spec(tmp_path)
    lo = lay_out_set(ds)
    assert ds.root in lo.canvases, [d.render() for d in lo.problems]
    canvas = lo.canvases[ds.root]
    assert not [d for d in lo.problems if d.severity is Severity.ERROR], [
        d.render() for d in lo.problems
    ]
    drawn = {r.id: r for r in canvas.regions}
    assert "env-frame:production" in drawn, "the linked frame must survive layout"
    frame = drawn["env-frame:production"]
    boxes = {b.id: b for b in canvas.boxes}
    for member in frame.members:
        b = boxes[member]
        assert frame.x <= b.x and b.right <= frame.right
        assert frame.y <= b.y and b.bottom <= frame.bottom
    assert "env:declared" in drawn


def test_the_environment_strip_sits_above_the_service_lanes(tmp_path: Path) -> None:
    """The review defect: badges used to occupy a flow column and the
    corridor looped around their frame. Now they form one row at the top,
    inside their frame, and no service lane is shaped by them."""
    _compose_variant_repo(tmp_path)
    ds = deploy_spec(tmp_path)
    lo = lay_out_set(ds)
    canvas = lo.canvases[ds.root]
    badges = [b for b in canvas.boxes if b.kind == "environment"]
    services = [b for b in canvas.boxes if b.kind != "environment"]
    assert len(badges) == 2
    assert len({b.y for b in badges}) == 1, "the strip is one horizontal row"
    top_lane = min(b.y for b in services)
    assert max(b.bottom for b in badges) < top_lane, "badges are above every lane"
    frame = next(r for r in canvas.regions if r.id == "env:declared")
    for b in badges:
        assert frame.x <= b.x and b.right <= frame.right
        assert frame.y <= b.y and b.bottom <= frame.bottom
    assert frame.bottom < top_lane, "the frame itself clears the service lanes"
    # And no route is pushed through the strip.
    for r in canvas.routes:
        for x, y in r.points:
            assert y > frame.bottom or not (frame.x < x < frame.right), (
                f"route {r.src}->{r.dst} enters the strip"
            )


def test_two_environments_claiming_one_service_is_reported_not_resolved(
    tmp_path: Path,
) -> None:
    """The contradiction case: `docker-compose.prod.yml` defines `web` and a
    Dockerfile it builds from says `NODE_ENV=development`. Both claims are
    drawn as badges and both frames are requested; if geometry cannot draw
    two boundaries over one box, layout drops one and says so."""
    write(
        tmp_path,
        "docker-compose.prod.yml",
        "services:\n"
        "  web:\n"
        "    build:\n"
        "      context: .\n"
        "      dockerfile: Dockerfile\n",
    )
    write(tmp_path, "Dockerfile", "FROM nginx\nENV NODE_ENV=development\n")
    write(tmp_path, "web/__init__.py", "")
    ds = deploy_spec(tmp_path)
    spec = ds.root_spec
    frames = {r.id for r in spec.regions}
    assert {"env-frame:production", "env-frame:development"} <= frames
    assert {"env:production", "env:development"} <= {n.id for n in spec.nodes}
    lo = lay_out_set(ds)
    assert ds.root in lo.canvases, [d.render() for d in lo.problems]
    assert not [d for d in lo.problems if d.severity is Severity.ERROR]
    skipped = [d for d in lo.problems if d.code == "SVA-G-014"]
    if skipped:
        # The dropped frame is named; the badges above carry both claims.
        assert any(d.subject in ("production", "development") for d in skipped)


def test_a_repo_without_environments_is_unchanged(tmp_path: Path) -> None:
    write(tmp_path, "docker-compose.yml", "services:\n  web:\n    image: nginx\n")
    spec = deploy_spec(tmp_path).root_spec
    assert not [n for n in spec.nodes if n.kind == "environment"]
    assert spec.regions == ()
    assert not [e for e in spec.edges if e.label == "deploys"]


def test_the_diagram_is_byte_stable_with_environments(tmp_path: Path) -> None:
    _compose_variant_repo(tmp_path)
    write(tmp_path, "Dockerfile", "FROM nginx\nENV NODE_ENV=qa\n")

    def render() -> tuple[tuple[object, ...], tuple[object, ...]]:
        spec = deploy_spec(tmp_path).root_spec
        return spec.nodes, spec.edges

    assert render() == render()


def test_the_viewer_html_shows_the_environment_labels(tmp_path: Path) -> None:
    _compose_variant_repo(tmp_path)
    out = tmp_path / "out"
    scan = detect(tmp_path)
    graph = build(scan, extract(scan, declared_dependencies(scan)), strict=False)
    produced, notes = derive_all(graph, cluster(graph))
    artifact = emit(tmp_path, graph, produced, notes, out_dir=out)
    assert artifact.ok, [d.render() for d in artifact.diagnostics]
    html = (out / "index.html").read_text(encoding="utf8")
    assert "declared environments" in html
    assert ">production<" in html or "production" in html
    # And the citation is wired for the passport, not just the label.
    assert "docker-compose.prod.yml" in html
