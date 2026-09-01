"""`cluster` tests.

The governing constraint is negative: **nothing here may reach the lockfile.**
Community detection is chaotically sensitive to input perturbation, so the
first test below is the one that matters most.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from svarupa.build import build, module_records
from svarupa.cluster import Clustering, cluster
from svarupa.detect import detect
from svarupa.diagnostics import DiagnosticError
from svarupa.extract import declared_dependencies, extract
from svarupa.lock import Lockfile, dep_record, module_record


def write(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf8")


def graph_of(root: Path):
    scan = detect(root)
    return build(scan, extract(scan, declared_dependencies(scan)), strict=False)


def layered(root: Path) -> None:
    """Two clearly separate clusters joined by one edge."""
    for pkg in ("api", "api/routes", "api/views", "worker", "worker/tasks", "worker/queue"):
        write(root, f"src/{pkg}/__init__.py", "")
    write(root, "src/api/routes/handler.py", "from ..views.render import show\n")
    write(root, "src/api/views/render.py", "def show():\n    pass\n")
    write(root, "src/worker/tasks/job.py", "from ..queue.broker import push\n")
    write(root, "src/worker/queue/broker.py", "def push():\n    pass\n")
    write(root, "src/api/routes/kick.py", "from ...worker.tasks.job import run\n")


# --------------------------------------------------------------------------
# The constraint that matters
# --------------------------------------------------------------------------


def _render_lock(g) -> str:
    records = [module_record(m) for m, _ in module_records(g)]
    records += [dep_record(a, b) for a, b in g.module_deps]
    return Lockfile.build("0.1.0", {}, records).render()


def test_no_community_identifier_reaches_the_lockfile(tmp_path: Path) -> None:
    """The whole reason clustering is quarantined to presentation.

    Measured on real repositories: one added import flips 25-38% of community
    assignments. If any of that reached a committed artifact, every pull
    request would show architecture changes that did not happen.

    Anchors are deliberately NOT checked: an anchor is a module id, so it
    appears in the lockfile as a module in its own right. What must never
    appear is a *grouping* fact -- a fingerprint, a cohesion score, or a
    community-membership record.
    """
    layered(tmp_path)
    g = graph_of(tmp_path)
    cl = cluster(g)
    assert cl.communities, "fixture must actually produce communities"

    # Record bodies only. The header carries a schema version like "1.0",
    # which would false-positive against a cohesion score of 1.0.
    body = "\n".join(ln for ln in _render_lock(g).splitlines() if not ln.startswith("#"))
    kinds = {ln.split("\t")[0] for ln in body.splitlines() if ln}
    assert kinds <= {"module", "dep"}, f"a non-structural record kind appeared: {kinds}"
    for c in cl.communities:
        assert c.fingerprint() not in body
    assert "community" not in body.lower()
    assert "cohesion" not in body.lower()


def test_lockfile_is_unchanged_by_reclustering(tmp_path: Path) -> None:
    """Different clustering parameters must not move a single committed byte."""
    layered(tmp_path)
    g = graph_of(tmp_path)

    before = _render_lock(g)
    partitions = set()
    for res in (0.5, 1.0, 4.0):
        for seed in (1, 99, 12345):
            partitions.add(cluster(g, seed=seed, resolution=res).communities)
    assert len(partitions) > 1, "the fixture must actually produce differing partitions"
    assert _render_lock(g) == before


# --------------------------------------------------------------------------
# Determinism
# --------------------------------------------------------------------------


def test_planted_structure_is_recovered(tmp_path: Path) -> None:
    """Clustering quality, which nothing previously asserted.

    A stub returning arbitrary per-seed partitions passed every other test in
    this file. This one fails if the backend regresses to noise.
    """
    layered(tmp_path)
    cl = cluster(graph_of(tmp_path))
    api = cl.of("src/api/routes")
    assert api is not None
    assert "src/api/views" in api.members, "api routes and views were separated"
    worker = cl.of("src/worker/tasks")
    assert worker is not None
    assert "src/worker/queue" in worker.members, "worker tasks and queue were separated"


def test_a_cohesive_group_is_not_split_for_size() -> None:
    """A clique explains itself, however large.

    Splitting one produced N singletons, turning a three-box diagram into
    twenty-two: worse than the crowding it was meant to fix.
    """
    from svarupa.build import Graph
    from svarupa.cluster import cluster as run_cluster

    names = [f"src/m{i:02d}" for i in range(20)]
    deps = tuple(sorted((a, b) for a in names for b in names if a < b))
    from svarupa.build import Module
    from svarupa.extract.base import Scorecard

    g = Graph(
        nodes={},
        edges=(),
        modules={n: Module(n, None, 1) for n in names},
        module_deps=deps,
        scorecard=Scorecard(),
    )
    cl = run_cluster(g)
    biggest = max(cl.communities, key=lambda c: c.size)
    assert biggest.size == 20, f"clique shattered into {[c.size for c in cl.communities]}"


@pytest.mark.determinism
def test_same_seed_gives_the_same_partition(tmp_path: Path) -> None:
    layered(tmp_path)
    g = graph_of(tmp_path)
    runs = {cluster(g, seed=7).communities for _ in range(10)}
    assert len(runs) == 1


@pytest.mark.determinism
def test_partition_is_independent_of_module_insertion_order(tmp_path: Path) -> None:
    """The module graph is built in canonical order so tie-breaking is stable."""
    layered(tmp_path)
    g = graph_of(tmp_path)
    a = cluster(g).communities

    from svarupa.build import Graph

    shuffled = Graph(
        nodes=g.nodes,
        edges=g.edges,
        modules=dict(reversed(list(g.modules.items()))),
        module_deps=tuple(reversed(g.module_deps)),
        scorecard=g.scorecard,
    )
    assert cluster(shuffled).communities == a


def test_anchor_ties_break_lexicographically(tmp_path: Path) -> None:
    """Without it, the same graph names itself differently on two machines."""
    for name in ("zeta", "alpha", "mid"):
        write(tmp_path, f"src/{name}/__init__.py", "")
        write(tmp_path, f"src/{name}/mod.py", "x = 1\n")
    cl = cluster(graph_of(tmp_path))
    for c in cl.communities:
        assert c.anchor == min(c.members) or c.size == 1


# --------------------------------------------------------------------------
# Behaviour
# --------------------------------------------------------------------------


def test_edge_weights_reflect_import_multiplicity(tmp_path: Path) -> None:
    """Forty imports between two modules is not the same as one.

    The original version of this test asserted only that a `weight` key
    existed, which was true of a constant: weights were counted from
    `module_deps`, a deduplicated set of pairs, so twenty imports scored
    exactly the same as one. The feature was dead and the test could not see it.
    """
    from svarupa.cluster import _weighted_module_graph

    for pkg in ("a", "b"):
        write(tmp_path, f"src/{pkg}/__init__.py", "")
    write(tmp_path, "src/__init__.py", "")
    write(tmp_path, "src/b/impl.py", "def h():\n    pass\n")
    for i in range(12):
        write(tmp_path, f"src/a/m{i}.py", "from ...src.b.impl import h\n")

    wg = _weighted_module_graph(graph_of(tmp_path))
    assert wg["src/a"]["src/b"]["weight"] >= 12


def test_weights_change_the_partition(tmp_path: Path) -> None:
    """The claim the previous test could not make.

    Two candidate merges, one backed by many imports and one by a single
    import. A weighted partition must keep the heavily-coupled pair together.
    """
    from svarupa.cluster import _weighted_module_graph

    for pkg in ("hot", "hotdep", "cold", "colddep"):
        write(tmp_path, f"src/{pkg}/__init__.py", "")
    write(tmp_path, "src/__init__.py", "")
    write(tmp_path, "src/hotdep/impl.py", "def h():\n    pass\n")
    write(tmp_path, "src/colddep/impl.py", "def c():\n    pass\n")
    for i in range(15):
        write(tmp_path, f"src/hot/m{i}.py", "from ...src.hotdep.impl import h\n")
    write(tmp_path, "src/cold/one.py", "from ...src.colddep.impl import c\n")

    g = graph_of(tmp_path)
    wg = _weighted_module_graph(g)
    assert wg["src/hot"]["src/hotdep"]["weight"] > wg["src/cold"]["src/colddep"]["weight"]

    cl = cluster(g)
    assert cl.of("src/hot") is cl.of("src/hotdep"), "heavily-coupled pair was separated"


def test_cohesion_is_reported(tmp_path: Path) -> None:
    layered(tmp_path)
    cl = cluster(graph_of(tmp_path))
    assert all(0.0 <= c.cohesion <= 1.0 for c in cl.communities)


def test_every_module_lands_in_exactly_one_community(tmp_path: Path) -> None:
    layered(tmp_path)
    g = graph_of(tmp_path)
    cl = cluster(g)
    seen = [m for c in cl.communities for m in c.members]
    assert sorted(seen) == sorted(g.modules)
    assert len(seen) == len(set(seen)), "a module appeared in two communities"


def test_empty_graph_yields_no_communities(tmp_path: Path) -> None:
    cl = cluster(graph_of(tmp_path))
    assert cl.communities == ()


# --------------------------------------------------------------------------
# Backend honesty
# --------------------------------------------------------------------------


def test_unknown_backend_is_refused(tmp_path: Path) -> None:
    layered(tmp_path)
    with pytest.raises(DiagnosticError) as exc:
        cluster(graph_of(tmp_path), backend="nope")
    assert exc.value.diagnostic.code == "SVA-C-001"


def test_missing_backend_refuses_rather_than_substituting(tmp_path: Path) -> None:
    """Silently swapping the algorithm changes the output while the run still
    looks successful, which is worse than refusing."""
    try:
        import leidenalg  # noqa: F401
    except ImportError:
        pass
    else:
        pytest.skip("leidenalg is installed; the refusal path cannot be exercised")

    layered(tmp_path)
    with pytest.raises(DiagnosticError) as exc:
        cluster(graph_of(tmp_path), backend="leiden")
    assert exc.value.diagnostic.code == "SVA-C-002"
    assert "silently" in exc.value.diagnostic.message


def test_backend_and_parameters_are_recorded(tmp_path: Path) -> None:
    """A partition is only reproducible if the settings that produced it are."""
    layered(tmp_path)
    cl: Clustering = cluster(graph_of(tmp_path), seed=42, resolution=1.5)
    assert (cl.backend, cl.seed, cl.resolution) == ("louvain", 42, 1.5)
