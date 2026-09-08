"""`derive` tests.

Derivation is the first stage whose output a person looks at, so the tests are
about two things: that no element is drawn without evidence, and that a box's
**label agrees with what it cites**. A box labelled `flask` that cites
`examples/javascript/js_example` is a small lie, and small lies are how a
reader stops trusting a diagram.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from svarupa.build import Graph, build
from svarupa.cluster import Clustering, Community, cluster
from svarupa.derive import DiagramKind, derive_all
from svarupa.derive.architecture import ArchitectureDeriver, ModuleDepsDeriver
from svarupa.derive.base import (
    MAX_TOP_BOXES,
    ROOT,
    DiagramNode,
    DiagramSet,
    DiagramSpec,
    UnnavigableDiagramSet,
    group_evidence,
    module_evidence,
    runtime_edges,
)
from svarupa.derive.erd import ErdDeriver
from svarupa.detect import detect
from svarupa.extract import declared_dependencies, extract
from svarupa.model import Evidence, MissingEvidenceError


def write(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf8")


def pipeline(root: Path):
    scan = detect(root)
    graph = build(scan, extract(scan, declared_dependencies(scan)), strict=False)
    return graph, cluster(graph)


def layered(root: Path) -> None:
    for pkg in ("", "api", "api/routes", "api/views", "worker", "worker/tasks"):
        write(root, f"src/{pkg}/__init__.py".replace("//", "/"), "")
    write(root, "src/api/views/render.py", "def show():\n    pass\n")
    write(root, "src/api/routes/handler.py", "from ..views.render import show\n")
    write(root, "src/worker/tasks/job.py", "from ...api.views.render import show\n")


# --------------------------------------------------------------------------
# Fail-closed
# --------------------------------------------------------------------------


def test_a_diagram_node_without_evidence_cannot_exist() -> None:
    from svarupa.derive.base import DiagramNode

    with pytest.raises(MissingEvidenceError):
        DiagramNode(id="x", label="x", kind="module", evidence=())


def test_a_diagram_edge_without_evidence_cannot_exist() -> None:
    from svarupa.derive.base import DiagramEdge

    with pytest.raises(MissingEvidenceError):
        DiagramEdge(src="a", dst="b", label="", evidence=())


def test_every_element_of_every_spec_carries_evidence(tmp_path: Path) -> None:
    """Tautological by construction, and kept anyway.

    `DiagramNode.__post_init__` raises on empty evidence, so nothing reaching
    the assertions below can violate them. This is not coverage of the
    derivers; it is a regression guard against that constructor check being
    removed. Read it as such. The real evidence coverage is `_verify_evidence`
    in `build`, which re-reads the file and checks the cited lines exist.
    """
    layered(tmp_path)
    produced, _ = derive_all(*pipeline(tmp_path))
    assert produced
    for ds in produced.values():
        for spec in ds.specs.values():
            for n in spec.nodes:
                assert n.evidence
            for e in spec.edges:
                assert e.evidence


def test_evidence_points_at_lines_that_exist(tmp_path: Path) -> None:
    layered(tmp_path)
    graph, clustering = pipeline(tmp_path)
    counts = {rec.path: rec.line_count for rec in detect(tmp_path).files}
    produced, _ = derive_all(graph, clustering)
    for ds in produced.values():
        for spec in ds.specs.values():
            for el in (*spec.nodes, *spec.edges):
                for ev in el.evidence:
                    assert ev.end_line <= counts[ev.file], f"{el} cites {ev}"


# --------------------------------------------------------------------------
# A label must agree with what it cites
# --------------------------------------------------------------------------


def test_a_group_box_cites_the_module_it_is_named_after(tmp_path: Path) -> None:
    """Iterating members alphabetically made a box named after `src/flask` cite
    `examples/javascript/js_example`, because `examples` sorts first."""
    write(tmp_path, "src/__init__.py", "")
    write(tmp_path, "aaa_first/__init__.py", "")
    write(tmp_path, "aaa_first/mod.py", "from src.core import go\n")
    write(tmp_path, "src/core.py", "def go():\n    pass\n")

    graph, _ = pipeline(tmp_path)
    ev = group_evidence(graph, ("aaa_first", "src"), anchor="src")
    assert ev, "no evidence for the group"
    assert ev[0].file.startswith("src/"), f"anchor did not lead: {ev[0]}"


def test_module_evidence_never_cites_a_test_file(tmp_path: Path) -> None:
    """The graph keeps test code on purpose, but a box citing a test file
    sends a reader to the wrong place."""
    write(tmp_path, "src/gateway/access_control.py", "def check():\n    pass\n")
    write(tmp_path, "src/gateway/test_access_control.py", "def test_check():\n    pass\n")
    graph, _ = pipeline(tmp_path)
    ev = module_evidence(graph, "src/gateway")
    assert ev
    assert all("test_" not in e.file for e in ev), f"cited a test file: {ev}"


# --------------------------------------------------------------------------
# Hierarchical zoom
# --------------------------------------------------------------------------


def test_top_level_is_capped_regardless_of_repository_size(tmp_path: Path) -> None:
    """The whole answer to a 50,000-node monorepo: you drill, you do not zoom out."""
    write(tmp_path, "src/__init__.py", "")
    for i in range(40):
        write(tmp_path, f"src/p{i:02d}/__init__.py", "")
        write(tmp_path, f"src/p{i:02d}/mod.py", "x = 1\n")
    graph, clustering = pipeline(tmp_path)
    ds = ArchitectureDeriver().derive(graph, clustering)
    assert ds is not None
    assert len(ds.root_spec.nodes) <= MAX_TOP_BOXES


def test_groups_are_drillable_and_singletons_are_not(tmp_path: Path) -> None:
    """The expectation comes from the fixture, not from the code.

    The previous version derived `expected` from `n.attr("modules")`, which the
    same loop in `derive` sets from the same `len(members)` that decides
    `child_spec`. A stub emitting `modules="1"` and `child_spec=None`
    everywhere passed it.
    """
    layered(tmp_path)
    graph, clustering = pipeline(tmp_path)
    ds = ArchitectureDeriver().derive(graph, clustering)
    assert ds is not None

    multi = [n for n in ds.root_spec.nodes if n.is_drillable]
    singles = [n for n in ds.root_spec.nodes if not n.is_drillable]
    assert multi, "fixture produced no multi-module group"

    for n in multi:
        assert n.child_spec is not None
        assert len(ds.specs[n.child_spec].nodes) > 1, (
            "a drillable box must lead somewhere with more than one module"
        )
    for n in singles:
        assert n.id in graph.modules, "a non-drillable box must be a single real module"


def test_every_child_spec_reference_resolves(tmp_path: Path) -> None:
    layered(tmp_path)
    graph, clustering = pipeline(tmp_path)
    ds = ArchitectureDeriver().derive(graph, clustering)
    assert ds is not None
    for spec in ds.specs.values():
        for n in spec.nodes:
            if n.child_spec is not None:
                assert n.child_spec in ds.specs, f"dangling drill-down: {n.child_spec}"


def test_drilling_reaches_the_members_of_the_group(tmp_path: Path) -> None:
    layered(tmp_path)
    graph, clustering = pipeline(tmp_path)
    ds = ArchitectureDeriver().derive(graph, clustering)
    assert ds is not None
    drillable = [n for n in ds.root_spec.nodes if n.is_drillable]
    assert drillable, "fixture produced no groups to drill into"
    child = ds.specs[drillable[0].child_spec or ""]
    assert child.parent == ds.root
    assert len(child.nodes) >= 2


# --------------------------------------------------------------------------
# Community identity must not leak
# --------------------------------------------------------------------------


def test_no_diagram_id_is_a_community_artifact(tmp_path: Path) -> None:
    """Every id must be a module id or node id from the graph.

    Anchors are module ids, so they are legitimate. Fingerprints and cohesion
    scores are not, and would churn on every pull request.
    """
    layered(tmp_path)
    graph, clustering = pipeline(tmp_path)
    produced, _ = derive_all(graph, clustering)
    # A community box has its own `group:<anchor>` id (review #20 M3), anchored
    # on a module id and so as stable as the anchor; it is never a module's id.
    known = set(graph.modules) | set(graph.nodes) | {f"group:{m}" for m in graph.modules}
    fingerprints = {c.fingerprint() for c in clustering.communities}

    for ds in produced.values():
        for spec in ds.specs.values():
            for n in spec.nodes:
                assert n.id in known, f"invented id: {n.id}"
                assert n.id not in fingerprints
            for e in spec.edges:
                assert e.src in known and e.dst in known


def test_a_group_box_has_its_own_id_and_a_name_that_reads_as_a_set(tmp_path: Path) -> None:
    """Groups borrowed their anchor's name and id for nineteen reviews, so an
    arrow into the group read as an arrow into that module and the passport
    jumped between the two (review #20 M3)."""
    layered(tmp_path)
    graph, _ = pipeline(tmp_path)
    members = ("src/api", "src/api/routes", "src/api/views")
    clustering = Clustering(
        (
            Community("src/api/views", members, 1.0),
            Community("src/worker/tasks", ("src/worker/tasks",), 1.0),
        ),
        "hand",
        1,
        1.0,
    )
    ds = ArchitectureDeriver().derive(graph, clustering)
    assert ds is not None
    by_id = {n.id: n for n in ds.root_spec.nodes}
    group = by_id["group:src/api/views"]
    assert group.kind == "group" and group.label == "api", group
    assert group.attr("members") == "\n".join(members)
    assert "src/api/views" not in by_id, "the anchor module is not a box beside its group"
    assert by_id["src/worker/tasks"].kind != "group", "a singleton is the module itself"
    edge = next(e for e in ds.root_spec.edges if e.src == "src/worker/tasks")
    assert edge.dst == "group:src/api/views"
    # With no shared directory the group is named for its anchor and the size
    # of the rest, so it cannot be read as the anchor alone.
    from svarupa.derive.architecture import top_box_labels

    labels = top_box_labels([("b/x", ("a/one", "b/x", "c/y")), ("d", ("d",))])
    assert labels == {"b/x": "x +2", "d": "d"}


def test_reclustering_does_not_invent_new_ids(tmp_path: Path) -> None:
    layered(tmp_path)
    graph, _ = pipeline(tmp_path)
    known = set(graph.modules) | set(graph.nodes) | {f"group:{m}" for m in graph.modules}
    for seed in (1, 42, 999):
        ds = ArchitectureDeriver().derive(graph, cluster(graph, seed=seed))
        assert ds is not None
        for spec in ds.specs.values():
            assert all(n.id in known for n in spec.nodes)


# --------------------------------------------------------------------------
# Absence is explained, never fabricated
# --------------------------------------------------------------------------


def test_an_empty_repository_yields_no_diagram_and_says_why(tmp_path: Path) -> None:
    # A repository with no source (an empty directory is a refusal, SVA-D-008).
    write(tmp_path, "README.md", "# no code here\n")
    produced, notes = derive_all(*pipeline(tmp_path))
    assert produced == {}
    assert notes, "absence must be explained"


def test_erd_distinguishes_no_sql_from_cannot_read_sql(tmp_path: Path) -> None:
    """Two different facts, and only one is about the user's codebase."""
    write(tmp_path, "README.md", "# no code here\n")
    graph, clustering = pipeline(tmp_path)
    ds = ErdDeriver().derive(graph, clustering)
    assert ds is not None and not ds.specs
    assert "no SQL schema" in ds.diagnostics[0].message

    write(tmp_path, "schema/init.sql", "CREATE TABLE t (id int);\n")
    graph, clustering = pipeline(tmp_path)
    ds = ErdDeriver().derive(graph, clustering)
    assert ds is not None and not ds.specs
    assert "cannot be read yet" in ds.diagnostics[0].message


def test_module_deps_declines_when_nothing_resolved(tmp_path: Path) -> None:
    write(tmp_path, "src/lonely.py", "import os\n")
    graph, clustering = pipeline(tmp_path)
    ds = ModuleDepsDeriver().derive(graph, clustering)
    assert ds is not None and not ds.specs
    assert "disconnected boxes" in ds.diagnostics[0].message


def test_a_failing_deriver_does_not_lose_the_others(tmp_path: Path, monkeypatch) -> None:
    """Partial failure degrades, it does not disable."""
    layered(tmp_path)

    def boom(self, graph, clustering):  # noqa: ARG001
        raise RuntimeError("deliberate")

    monkeypatch.setattr(ArchitectureDeriver, "derive", boom)
    produced, notes = derive_all(*pipeline(tmp_path))
    assert DiagramKind.MODULE_DEPS in produced
    assert any("failed" in n for n in notes)


# --------------------------------------------------------------------------
# Type-only edges are not runtime dependencies
# --------------------------------------------------------------------------


def test_type_only_imports_are_excluded_and_counted(tmp_path: Path) -> None:
    write(tmp_path, "src/model.ts", "export interface User { id: string }\n")
    write(tmp_path, "src/svc/impl.ts", "export function go(): void {}\n")
    write(tmp_path, "src/app.ts", "import type { User } from './model';\n")
    write(tmp_path, "src/user.ts", "import { go } from './svc/impl';\n")

    graph, clustering = pipeline(tmp_path)
    ds = ModuleDepsDeriver().derive(graph, clustering)
    assert ds is not None
    assert ds.specs, "fixture produced no diagram, so the real assertion never ran"
    assert not any("model" in e.dst for e in ds.root_spec.edges), (
        "a type-only import became a runtime dependency"
    )
    assert any("type-only" in d.message for d in ds.diagnostics)


# --------------------------------------------------------------------------
# Determinism
# --------------------------------------------------------------------------


@pytest.mark.determinism
def test_derivation_is_stable_across_rebuilds(tmp_path: Path) -> None:
    """Rebuild the whole pipeline each iteration.

    Calling `derive` five times on the *same* graph and clustering objects is
    deterministic by construction -- Python guarantees identical iteration --
    so it could not catch the churn sources that matter.
    """
    layered(tmp_path)
    runs: set[tuple[object, ...]] = set()
    for _ in range(4):
        graph, clustering = pipeline(tmp_path)
        ds = ArchitectureDeriver().derive(graph, clustering)
        assert ds is not None
        runs.add(tuple(sorted((k, v.nodes, v.edges) for k, v in ds.specs.items())))
    assert len(runs) == 1


@pytest.mark.determinism
def test_derivation_survives_a_varied_hash_seed(tmp_path: Path) -> None:
    """The real churn source: cross-process string-hash ordering.

    A subprocess is required, because PYTHONHASHSEED is fixed at interpreter
    start.
    """
    import os
    import subprocess
    import sys

    layered(tmp_path)
    script = (
        f"import sys; sys.path.insert(0, {str(Path(__file__).resolve().parents[1])!r})\n"
        "from svarupa.detect import detect\n"
        "from svarupa.extract import extract, declared_dependencies\n"
        "from svarupa.build import build\n"
        "from svarupa.cluster import cluster\n"
        "from svarupa.derive.architecture import ArchitectureDeriver\n"
        f"s = detect({str(tmp_path)!r})\n"
        "g = build(s, extract(s, declared_dependencies(s)), strict=False)\n"
        "ds = ArchitectureDeriver().derive(g, cluster(g))\n"
        "print([(k, [n.id for n in v.nodes], [(e.src, e.dst, e.weight) for e in v.edges])"
        " for k, v in sorted(ds.specs.items())])\n"
    )

    outputs: set[str] = set()
    for seed in ("0", "1", "4242"):
        env = {**os.environ, "PYTHONHASHSEED": seed}
        out = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            env=env,
            check=True,
        )
        outputs.add(out.stdout)
    assert len(outputs) == 1, f"hash seed changed derivation ({len(outputs)} variants)"


def test_dependency_layers_survive_a_cycle(tmp_path: Path) -> None:
    """A cycle is exactly what a reader wants to see, so refusing to lay one
    out is worse than laying it out imperfectly."""
    write(tmp_path, "src/__init__.py", "")
    write(tmp_path, "src/a/__init__.py", "from ..b.mod import bee\n")
    write(tmp_path, "src/b/__init__.py", "")
    write(tmp_path, "src/a/mod.py", "from ..b.mod import bee\n")
    write(tmp_path, "src/b/mod.py", "from ..a.mod import ay\n\n\ndef bee():\n    pass\n")

    graph, clustering = pipeline(tmp_path)
    ds = ModuleDepsDeriver().derive(graph, clustering)  # must terminate
    assert ds is not None
    assert ds.specs, "fixture produced no diagram, so the real assertion never ran"
    assert all(n.attr("layer") is not None for n in ds.root_spec.nodes)


def test_an_empty_allow_list_allows_nothing(tmp_path: Path) -> None:
    """`if eligible and nid not in eligible` was a guard with an escape hatch.

    With `architecture_paths` empty the check was skipped entirely, so a
    directly-constructed graph could cite a test file. An empty allow-list must
    allow nothing: that is the safe default and also the correct one, since a
    repository with no architecture-eligible files has nothing to draw.
    """
    from svarupa.build import Graph

    write(tmp_path, "src/gateway/impl.py", "def go():\n    pass\n")
    write(tmp_path, "src/gateway/test_impl.py", "def test_go():\n    pass\n")
    graph, _ = pipeline(tmp_path)
    assert module_evidence(graph, "src/gateway"), "sanity: normally there is evidence"

    stripped = Graph(
        nodes=graph.nodes,
        edges=graph.edges,
        modules=graph.modules,
        module_deps=graph.module_deps,
        scorecard=graph.scorecard,
    )
    assert module_evidence(stripped, "src/gateway") == ()


def test_group_evidence_is_order_independent(tmp_path: Path) -> None:
    """The anchor leads, but the rest must not depend on input order."""
    write(tmp_path, "src/__init__.py", "")
    for name in ("zeta", "alpha", "mid"):
        write(tmp_path, f"src/{name}/__init__.py", "")
        write(tmp_path, f"src/{name}/mod.py", "x = 1\n")
    graph, _ = pipeline(tmp_path)
    members = ["src/zeta", "src/alpha", "src/mid"]
    a = group_evidence(graph, members, anchor="src/mid")
    b = group_evidence(graph, list(reversed(members)), anchor="src/mid")
    assert a == b
    assert a[0].file.startswith("src/mid/"), "anchor did not lead"


def test_the_top_box_cap_is_actually_exercised(tmp_path: Path) -> None:
    """A cap test proves nothing if the fixture stays under the cap.

    Verified: this fixture yields 41 communities, so the merge path runs.
    """
    write(tmp_path, "src/__init__.py", "")
    for i in range(40):
        write(tmp_path, f"src/p{i:02d}/__init__.py", "")
        write(tmp_path, f"src/p{i:02d}/mod.py", "x = 1\n")
    graph, clustering = pipeline(tmp_path)
    assert len(clustering.communities) > MAX_TOP_BOXES, "fixture did not exceed the cap"

    ds = ArchitectureDeriver().derive(graph, clustering)
    assert ds is not None
    assert len(ds.root_spec.nodes) <= MAX_TOP_BOXES
    # Nothing may be lost to capping: every drawable module still appears
    # somewhere, either as a top box or inside one.
    seen = {n.id for s in ds.specs.values() for n in s.nodes}
    drawable = {m for m in graph.modules if module_evidence(graph, m)}
    assert drawable <= seen, f"capping lost modules: {sorted(drawable - seen)[:5]}"


def test_a_type_only_pair_is_runtime_if_any_site_is(tmp_path: Path) -> None:
    """End-to-end across build's merge rule and derive's exclusion.

    One type-only and one runtime import between the same pair must render as a
    runtime dependency, or impact analysis silently drops a real edge.
    """
    write(
        tmp_path, "src/model.ts", "export interface User { id: string }\nexport const K = 1;\n"
    )
    write(tmp_path, "src/a/one.ts", "import type { User } from '../model';\n")
    write(tmp_path, "src/a/two.ts", "import { K } from '../model';\n")

    graph, clustering = pipeline(tmp_path)
    ds = ModuleDepsDeriver().derive(graph, clustering)
    assert ds is not None and ds.specs, "expected a dependency diagram"
    pairs = {(e.src, e.dst) for e in ds.root_spec.edges}
    assert ("src/a", "src") in pairs, f"runtime site was excluded: {pairs}"


# --------------------------------------------------------------------------
# Capping must not invent relationships
# --------------------------------------------------------------------------


def _many_groups(root: Path) -> None:
    """Four genuine clusters plus fifteen wholly unconnected modules.

    Enough groups to force spill merging, with a decoy structure: the isolated
    modules live in a different top-level directory from the clusters, so
    absorbing them into a cluster is visibly wrong.
    """
    write(root, "src/__init__.py", "")
    for grp in ("alpha", "beta", "gamma", "delta"):
        write(root, f"src/{grp}/__init__.py", "")
        write(root, f"src/{grp}/core.py", "def go():\n    pass\n")
        for i in range(3):
            write(root, f"src/{grp}/sub{i}/__init__.py", "")
            write(root, f"src/{grp}/sub{i}/m.py", f"from ...{grp}.core import go\n")
    for i in range(15):
        write(root, f"lonely/zz{i:02d}/__init__.py", "")
        write(root, f"lonely/zz{i:02d}/m.py", "import os\n")


def test_capping_never_merges_an_unconnected_group_into_a_cluster(tmp_path: Path) -> None:
    """`best_score` started at -1, so a group connected to nothing still beat
    the first candidate and was absorbed into an arbitrary neighbour.

    Verified before the fix: eight `lonely/*` modules were pulled into the
    `alpha` cluster, and the diagnostic reported them as "merged into the
    groups they connect to most" -- a claim of connection where there was none.
    """
    _many_groups(tmp_path)
    graph, clustering = pipeline(tmp_path)
    assert len(clustering.communities) > MAX_TOP_BOXES, "fixture must force spill"

    ds = ArchitectureDeriver().derive(graph, clustering)
    assert ds is not None
    for n in ds.root_spec.nodes:
        members = [x.id for x in ds.specs[n.child_spec].nodes] if n.child_spec else [n.id]
        trees = {m.split("/")[0] for m in members}
        assert len(trees) == 1, f"box {n.label!r} mixes unrelated trees: {sorted(trees)}"


def test_capping_diagnostic_describes_what_actually_happened(tmp_path: Path) -> None:
    """The old message claimed connection-based merging unconditionally.

    A diagnostic that misreports its own reasoning is worse than none: it
    launders an invented grouping as a measured one.
    """
    _many_groups(tmp_path)
    graph, clustering = pipeline(tmp_path)
    ds = ArchitectureDeriver().derive(graph, clustering)
    assert ds is not None
    message = next(d.message for d in ds.diagnostics if d.code == "SVA-R-003")
    assert "shared directory" in message
    assert "connect to most" not in message


def test_capping_prefers_a_real_dependency_over_proximity(tmp_path: Path) -> None:
    """Proximity is the fallback, not the first choice.

    The previous version asserted `"depend on" in msg or "shared directory" in
    msg`, which passes on either branch -- and in fact only the proximity
    branch ever ran, so the highest-stakes tier of the merge was untested.
    """
    write(tmp_path, "src/__init__.py", "")
    for grp in ("alpha", "beta", "gamma", "delta"):
        write(tmp_path, f"src/{grp}/__init__.py", "")
        write(tmp_path, f"src/{grp}/core.py", "def go():\n    pass\n")
        for i in range(3):
            write(tmp_path, f"src/{grp}/sub{i}/__init__.py", "")
            write(tmp_path, f"src/{grp}/sub{i}/m.py", f"from ...{grp}.core import go\n")
    # A far-away module that genuinely depends on alpha.
    write(tmp_path, "far/__init__.py", "")
    write(tmp_path, "far/dep.py", "from src.alpha.core import go\n")
    for i in range(12):
        write(tmp_path, f"far/pad{i:02d}/__init__.py", "")
        write(tmp_path, f"far/pad{i:02d}/m.py", "import os\n")

    graph, clustering = pipeline(tmp_path)
    ds = ArchitectureDeriver().derive(graph, clustering)
    assert ds is not None
    message = next(d.message for d in ds.diagnostics if d.code == "SVA-R-003")
    assert "shared directory" in message


def _capping_fixture(tmp_path: Path) -> tuple[Graph, Clustering]:
    """Fourteen groups, so two must spill past the twelve-box budget.

    Clustering is supplied by hand rather than by Louvain. The unit under test
    is the *capping*, and letting Louvain decide the input means the branch the
    test is named after may never run: the previous attempt at this test
    skipped, because clustering had already absorbed the dependent group into
    its target before capping saw it.
    """
    write(tmp_path, "src/__init__.py", "")
    big = [f"src/core/p{i}" for i in range(5)]
    for m in big:
        write(tmp_path, f"{m}/__init__.py", "")
        write(tmp_path, f"{m}/m.py", "VALUE = 1\n")
    pairs: list[list[str]] = []
    for g in range(11):
        members = [f"far{g:02d}/a", f"far{g:02d}/b"]
        for m in members:
            write(tmp_path, f"{m}/__init__.py", "")
            write(tmp_path, f"{m}/m.py", "VALUE = 1\n")
        pairs.append(members)

    # Spills, and depends on the big group. Deliberately placed where its
    # shared prefix with the big group is zero, so tier 2 cannot rescue it and
    # only tier 1 can explain the merge.
    write(tmp_path, "dependent/__init__.py", "")
    write(tmp_path, "dependent/m.py", "from src.core.p0.m import VALUE\n")
    # Spills, depends on nothing, but sits under src/ next to the big group.
    write(tmp_path, "src/quiet/__init__.py", "")
    write(tmp_path, "src/quiet/m.py", "VALUE = 1\n")

    graph, _ = pipeline(tmp_path)
    groups = [big, *pairs, ["dependent"], ["src/quiet"]]
    clustering = Clustering(
        communities=tuple(
            Community(anchor=g[0], members=tuple(g), cohesion=1.0) for g in groups
        ),
        backend="fixture",
        seed=0,
        resolution=1.0,
    )
    return graph, clustering


def test_capping_merges_a_spilled_group_into_one_it_depends_on(tmp_path: Path) -> None:
    """Tier 1, asserted on the placement and not only on the message.

    The previous version of this test asserted `"depend on" in msg or "shared
    directory" in msg`, which passes on either branch. Measured: only the
    proximity branch ever ran, so the tier this test is named after was
    entirely uncovered.
    """
    graph, clustering = _capping_fixture(tmp_path)
    ds = ArchitectureDeriver().derive(graph, clustering)
    assert ds is not None
    note = next(d for d in ds.diagnostics if d.code == "SVA-R-003")
    assert "1 merged into a group they depend on" in note.message, note.message

    assert len(ds.root_spec.nodes) <= MAX_TOP_BOXES
    home = next(n for n in ds.root_spec.nodes if "dependent" in _members(ds, n))
    assert any(m.startswith("src/core/") for m in _members(ds, home)), (
        "the spilled module was reported as merged into its dependency but did "
        "not land in that box"
    )


def test_capping_falls_back_to_shared_directory(tmp_path: Path) -> None:
    """Tier 2, on the same fixture, so the two tiers are told apart."""
    graph, clustering = _capping_fixture(tmp_path)
    ds = ArchitectureDeriver().derive(graph, clustering)
    assert ds is not None
    note = next(d for d in ds.diagnostics if d.code == "SVA-R-003")
    assert "1 merged by shared directory" in note.message, note.message

    home = next(n for n in ds.root_spec.nodes if "src/quiet" in _members(ds, n))
    assert any(m.startswith("src/") and m != "src/quiet" for m in _members(ds, home)), (
        "merged by shared directory, into a box sharing no directory"
    )


def _members(ds: DiagramSet, node: DiagramNode) -> list[str]:
    """Every module a top-level box stands for, via its sub-diagram."""
    if node.child_spec is None:
        return [node.id]
    return [n.id for n in ds.specs[node.child_spec].nodes]


def test_nothing_is_lost_to_capping(tmp_path: Path) -> None:
    _many_groups(tmp_path)
    graph, clustering = pipeline(tmp_path)
    ds = ArchitectureDeriver().derive(graph, clustering)
    assert ds is not None
    seen = {n.id for spec in ds.specs.values() for n in spec.nodes}
    drawable = {m for m in graph.modules if module_evidence(graph, m)}
    assert drawable <= seen, f"capping dropped: {sorted(drawable - seen)[:5]}"


def test_sub_diagram_ids_cannot_collide_with_module_ids(tmp_path: Path) -> None:
    """A directory named `group:payments` is scannable on POSIX.

    So a `group:`-prefixed sub-diagram id shared a namespace with module ids,
    and the moment a viewer keys both in one map they would collide. Every
    repo-relative path is built from components and can never begin with "/",
    which makes a "/spec/" prefix provably disjoint rather than conventionally
    so.
    """
    from svarupa.derive.base import SPEC_PREFIX

    write(tmp_path, "src/__init__.py", "")
    write(tmp_path, "src/core.py", "def go():\n    pass\n")
    (tmp_path / "group:core").mkdir()
    write(tmp_path, "group:core/m.py", "from src.core import go\n")

    graph, clustering = pipeline(tmp_path)
    assert any("group:" in p for p in graph.architecture_paths), "fixture did not scan"

    ds = ArchitectureDeriver().derive(graph, clustering)
    assert ds is not None
    module_ids = set(graph.modules) | set(graph.nodes)
    for spec_key in ds.specs:
        if spec_key == "root":
            continue
        assert spec_key.startswith(SPEC_PREFIX)
        assert spec_key not in module_ids
    assert not any(m.startswith(SPEC_PREFIX) for m in module_ids)


# --------------------------------------------------------------------------
# Navigation invariant (review #7 F7, deferred from P1-5 to here)
# --------------------------------------------------------------------------


def _spec(sid: str, *children: str, parent: str | None = None) -> DiagramSpec:
    """A minimal spec whose boxes drill into `children`."""
    ev = (Evidence(file="a.py", start_line=1, end_line=1),)
    nodes = tuple(
        DiagramNode(
            id=f"{sid}:box{i}", label=f"box{i}", kind="module", evidence=ev, child_spec=c
        )
        for i, c in enumerate(children)
    ) or (DiagramNode(id=f"{sid}:leaf", label="leaf", kind="module", evidence=ev),)
    return DiagramSpec(
        kind=DiagramKind.ARCHITECTURE, id=sid, title=sid, nodes=nodes, edges=(), parent=parent
    )


def _set(root: str, *specs: DiagramSpec) -> DiagramSet:
    return DiagramSet(DiagramKind.ARCHITECTURE, root, {s.id: s for s in specs})


def test_a_navigable_set_reports_no_problems() -> None:
    """The baseline. Without it, every test below could pass on a validator
    that always complains."""
    ds = _set(ROOT, _spec(ROOT, "/spec/a"), _spec("/spec/a", parent=ROOT))
    assert ds.validate() == ()
    assert ds.root_spec.id == ROOT


def test_a_missing_root_is_reported_not_raised_as_keyerror() -> None:
    ds = _set(ROOT, _spec("/spec/a"))
    problems = ds.validate()
    assert [d.code for d in problems] == ["SVA-R-005"]
    assert "no entry point" in problems[0].message
    # And the accessor names the diagram instead of leaking a bare KeyError.
    with pytest.raises(UnnavigableDiagramSet, match="root spec"):
        _ = ds.root_spec


def test_a_dangling_child_spec_is_reported() -> None:
    ds = _set(ROOT, _spec(ROOT, "/spec/gone"))
    assert any("does not exist" in d.message for d in ds.validate())


def test_an_unreachable_spec_is_reported() -> None:
    """Dead weight shipped to the browser, or a lost drill-down link."""
    ds = _set(
        ROOT,
        _spec(ROOT, "/spec/a"),
        _spec("/spec/a", parent=ROOT),
        _spec("/spec/orphan", parent=ROOT),
    )
    problems = ds.validate()
    assert [d.subject for d in problems if "unreachable" in d.message] == ["/spec/orphan"]


def test_a_drill_down_cycle_is_reported_and_does_not_hang() -> None:
    ds = _set(
        ROOT,
        _spec(ROOT, "/spec/a"),
        _spec("/spec/a", "/spec/b", parent=ROOT),
        _spec("/spec/b", "/spec/a", parent="/spec/a"),
    )
    assert any("cycle" in d.message for d in ds.validate())


def test_a_shared_sub_diagram_is_not_mistaken_for_a_cycle() -> None:
    """Two boxes drilling into one spec is a diamond, not a loop.

    Tracking "everything seen" rather than "the current chain" would report
    this as a cycle, so the distinction is asserted rather than assumed.
    """
    ds = _set(
        ROOT,
        _spec(ROOT, "/spec/a", "/spec/b"),
        _spec("/spec/a", "/spec/shared", parent=ROOT),
        _spec("/spec/b", "/spec/shared", parent=ROOT),
        _spec("/spec/shared", parent="/spec/a"),
    )
    assert not any("cycle" in d.message for d in ds.validate())


def test_a_lying_parent_pointer_is_reported() -> None:
    """`parent` is what the viewer's back button uses, so a wrong one navigates
    the reader somewhere they did not come from."""
    ds = _set(
        ROOT,
        _spec(ROOT, "/spec/a"),
        _spec("/spec/a", parent="/spec/somewhere-else"),
    )
    problems = ds.validate()
    assert [d.subject for d in problems] == ["/spec/a"]
    assert "declares parent" in problems[0].message


def test_derive_all_drops_an_unnavigable_set_and_keeps_the_others(tmp_path: Path) -> None:
    """Partial failure degrades. A broken set is dropped, never repaired:
    inventing the missing link would put a fabricated drill-down in front of a
    reader."""
    import svarupa.derive as derive_mod

    class Broken(ArchitectureDeriver):
        def derive(self, graph: Graph, clustering: Clustering) -> DiagramSet:
            _ = graph, clustering
            return _set(ROOT, _spec(ROOT, "/spec/gone"))

    original = derive_mod.DERIVERS
    derive_mod.DERIVERS = (Broken(), ModuleDepsDeriver())
    try:
        layered(tmp_path)
        produced, notes = derive_mod.derive_all(*pipeline(tmp_path))
    finally:
        derive_mod.DERIVERS = original

    assert DiagramKind.ARCHITECTURE not in produced
    assert DiagramKind.MODULE_DEPS in produced, "one broken set took the others down"
    assert any("unnavigable, dropped" in n for n in notes)


def _wide_repo(root: Path) -> None:
    """Fourteen modules under two directories, more than the top-box budget."""
    for i in range(8):
        write(root, f"src/p{i}/__init__.py", "")
        write(root, f"src/p{i}/m.py", f"from src.p{(i + 1) % 8} import m\n")
    for i in range(5):
        write(root, f"tools/t{i}/__init__.py", "")
        write(root, f"tools/t{i}/m.py", "from src.p0 import m\n")
    write(root, "tools/__init__.py", "")
    write(
        root, "tools/shared.py", "from tools.t0 import m\n"
    )  # `tools` is a module AND a directory
    write(root, "src/__init__.py", "")


def test_module_deps_follow_the_directory_tree_past_the_top_box_budget(tmp_path: Path) -> None:
    """Review #20 M5: 30 boxes and 76 arrows validated clean and could not be
    read. Past the budget the view is the directory tree: parts at the root,
    the modules of a part one drill down, every dependency drawn at exactly
    one level, none summarized away."""
    _wide_repo(tmp_path)
    graph, clustering = pipeline(tmp_path)
    pairs, _ = runtime_edges(graph)
    assert len({m for a, b, _w, _e in pairs for m in (a, b)}) > MAX_TOP_BOXES
    ds = ModuleDepsDeriver().derive(graph, clustering)
    assert ds is not None
    root = ds.root_spec
    by_id = {n.id: n for n in root.nodes}
    assert set(by_id) == {"tree:src", "tree:tools"}, sorted(by_id)
    assert by_id["tree:src"].sublabel == "8 modules" and by_id["tree:src"].kind == "module"
    assert by_id["tree:src"].attr("members") == "\n".join(f"src/p{i}" for i in range(8))
    assert [(e.src, e.dst, e.weight) for e in root.edges] == [("tree:tools", "tree:src", 5)]
    assert "14 modules in 2 parts of the repository" in root.subtitle
    # 8 ring imports inside src, `tools -> tools/t0` inside tools; the five
    # `tools/t* -> src/p0` imports are the one aggregated arrow between parts.
    assert "1 dependencies between parts, 9 inside them" in root.subtitle
    tools = ds.specs[by_id["tree:tools"].child_spec or ""]
    assert tools.parent == "/spec/root" and tools.title == "tools module dependencies"
    # `tools` is a module beside its own subdirectories: its own box, a leaf.
    assert {n.id for n in tools.nodes} == {"tools", *(f"tools/t{i}" for i in range(5))}
    assert all(
        n.child_spec is None or not n.child_spec.startswith("/spec/tree:") for n in tools.nodes
    )
    assert [(e.src, e.dst) for e in tools.edges] == [("tools", "tools/t0")]
    src = ds.specs[by_id["tree:src"].child_spec or ""]
    assert len(src.edges) == 8, "the ring of eight imports is drawn inside src"
    # Completeness: every module pair appears at exactly one level.
    drawn: list[tuple[str, str]] = []
    for spec in ds.specs.values():
        if spec.id == "/spec/root" or spec.id.startswith("/spec/tree:"):
            for e in spec.edges:
                drawn.append((e.src, e.dst))
    assert len(drawn) == 1 + 1 + 8, drawn
    assert sum(
        e.weight
        for spec in ds.specs.values()
        if spec.id in ("/spec/root", "/spec/tree:src", "/spec/tree:tools")
        for e in spec.edges
    ) == len(pairs)
    # No level has one box, and every level validates.
    for spec in ds.specs.values():
        if spec.id.startswith("/spec/tree:") or spec.id == "/spec/root":
            assert len(spec.nodes) >= 2, spec.id
    from svarupa.layout import lay_out_set
    from svarupa.layout.geometry import Style

    assert not lay_out_set(ds, Style()).withheld


def test_module_deps_stay_flat_within_the_top_box_budget(tmp_path: Path) -> None:
    layered(tmp_path)
    graph, clustering = pipeline(tmp_path)
    ds = ModuleDepsDeriver().derive(graph, clustering)
    assert ds is not None
    assert not any(n.id.startswith("tree:") for n in ds.root_spec.nodes)
    assert all(n.sublabel for n in ds.root_spec.nodes), "flat boxes say what they are too"
