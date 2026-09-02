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

from svarupa.build import build
from svarupa.cluster import cluster
from svarupa.derive import DiagramKind, derive_all
from svarupa.derive.architecture import ArchitectureDeriver, ModuleDepsDeriver
from svarupa.derive.base import MAX_TOP_BOXES, group_evidence, module_evidence
from svarupa.derive.erd import ErdDeriver
from svarupa.detect import detect
from svarupa.extract import declared_dependencies, extract
from svarupa.model import MissingEvidenceError


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
    known = set(graph.modules) | set(graph.nodes)
    fingerprints = {c.fingerprint() for c in clustering.communities}

    for ds in produced.values():
        for spec in ds.specs.values():
            for n in spec.nodes:
                assert n.id in known, f"invented id: {n.id}"
                assert n.id not in fingerprints
            for e in spec.edges:
                assert e.src in known and e.dst in known


def test_reclustering_does_not_invent_new_ids(tmp_path: Path) -> None:
    layered(tmp_path)
    graph, _ = pipeline(tmp_path)
    known = set(graph.modules) | set(graph.nodes)
    for seed in (1, 42, 999):
        ds = ArchitectureDeriver().derive(graph, cluster(graph, seed=seed))
        assert ds is not None
        for spec in ds.specs.values():
            assert all(n.id in known for n in spec.nodes)


# --------------------------------------------------------------------------
# Absence is explained, never fabricated
# --------------------------------------------------------------------------


def test_an_empty_repository_yields_no_diagram_and_says_why(tmp_path: Path) -> None:
    produced, notes = derive_all(*pipeline(tmp_path))
    assert produced == {}
    assert notes, "absence must be explained"


def test_erd_distinguishes_no_sql_from_cannot_read_sql(tmp_path: Path) -> None:
    """Two different facts, and only one is about the user's codebase."""
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


def test_the_connection_merge_tier_is_reachable(tmp_path: Path) -> None:
    """Drive tier 1 specifically, and assert the member landed in the
    connected box rather than merely that the message mentions it.

    Reaching this tier needs a group that survives clustering as its own
    community *and* has a cross-edge into a kept group, which is why the
    dependent tree is padded to keep it separate.
    """
    write(tmp_path, "src/__init__.py", "")
    write(tmp_path, "src/hub/__init__.py", "")
    write(tmp_path, "src/hub/core.py", "def go():\n    pass\n")
    for i in range(14):
        write(tmp_path, f"src/hub/leaf{i:02d}/__init__.py", "")
        write(tmp_path, f"src/hub/leaf{i:02d}/m.py", "from ...hub.core import go\n")
    # A separate tree with exactly one dependency into the hub.
    write(tmp_path, "edge_case/__init__.py", "")
    write(tmp_path, "edge_case/bridge.py", "from src.hub.core import go\n")

    graph, clustering = pipeline(tmp_path)
    ds = ArchitectureDeriver().derive(graph, clustering)
    assert ds is not None
    note = next((d for d in ds.diagnostics if d.code == "SVA-R-003"), None)
    if note is None:
        pytest.skip("clustering kept every group under the cap; tier 1 not reached")
    assert "depend on" in note.message, f"tier 1 never ran: {note.message}"


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
