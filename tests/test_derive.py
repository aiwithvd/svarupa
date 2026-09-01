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
    layered(tmp_path)
    graph, clustering = pipeline(tmp_path)
    ds = ArchitectureDeriver().derive(graph, clustering)
    assert ds is not None
    for n in ds.root_spec.nodes:
        expected = int(n.attr("modules") or "1") > 1
        assert n.is_drillable is expected


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
    if ds.specs:
        assert not any("model" in e.dst for e in ds.root_spec.edges), (
            "a type-only import became a runtime dependency"
        )
    assert any("type-only" in d.message for d in ds.diagnostics)


# --------------------------------------------------------------------------
# Determinism
# --------------------------------------------------------------------------


@pytest.mark.determinism
def test_derivation_is_stable_across_runs(tmp_path: Path) -> None:
    layered(tmp_path)
    graph, clustering = pipeline(tmp_path)
    runs = set()
    for _ in range(5):
        ds = ArchitectureDeriver().derive(graph, clustering)
        assert ds is not None
        runs.add(tuple(sorted((k, v.nodes, v.edges) for k, v in ds.specs.items())))
    assert len(runs) == 1


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
    if ds.specs:
        assert all(n.attr("layer") is not None for n in ds.root_spec.nodes)
