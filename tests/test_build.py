"""`build` tests.

Build is the enforcement point for the product's central promise, so each of
its four contracts gets a test that constructs the violation rather than
assuming it cannot happen.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from svarupa.build import Graph, build, module_records
from svarupa.detect import detect
from svarupa.diagnostics import DiagnosticError
from svarupa.extract import declared_dependencies, extract
from svarupa.extract.base import ExtractResult, Scorecard
from svarupa.model import (
    Edge,
    EdgeKind,
    Evidence,
    MissingEvidenceError,
    Node,
    NodeKind,
    Resolution,
)

EV1 = Evidence("src/a.py", 1, 1)
EV2 = Evidence("src/a.py", 7, 7)


def write(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf8")


def node(nid: str, **kw: object) -> Node:
    base: dict[str, object] = {
        "id": nid,
        "kind": NodeKind.FUNCTION,
        "label": nid.rsplit("#", 1)[-1],
        "qualified_name": nid.rsplit("#", 1)[-1],
        "evidence": (EV1,),
    }
    base.update(kw)
    return Node(**base)  # type: ignore[arg-type]


def edge(src: str, dst: str, **kw: object) -> Edge:
    base: dict[str, object] = {
        "src": src,
        "dst": dst,
        "kind": EdgeKind.IMPORTS,
        "evidence": (EV1,),
    }
    base.update(kw)
    return Edge(**base)  # type: ignore[arg-type]


def result(nodes: list[Node], edges: list[Edge]) -> ExtractResult:
    return ExtractResult(tuple(nodes), tuple(edges), Scorecard())


def scan_of(root: Path):
    return detect(root)


def full(root: Path) -> Graph:
    scan = detect(root)
    return build(scan, extract(scan, declared_dependencies(scan)))


# --------------------------------------------------------------------------
# Contract 1: evidence is re-validated independently
# --------------------------------------------------------------------------


def test_evidence_free_node_is_rejected_even_bypassing_the_constructor(
    tmp_path: Path,
) -> None:
    """The reason this check is duplicated.

    `object.__new__` plus `__setattr__` builds a node the model never
    validated, and it survives pickling. Python cannot prevent that, so
    constructor validation is a convenience and build is the enforcement point.
    """
    smuggled = object.__new__(Node)
    for f, v in (
        ("id", "src/a.py#ghost"),
        ("kind", NodeKind.FUNCTION),
        ("label", "ghost"),
        ("qualified_name", "ghost"),
        ("evidence", ()),
        ("lang", "python"),
        ("attrs", ()),
        ("producer", "smuggler"),
    ):
        object.__setattr__(smuggled, f, v)

    assert not smuggled.evidence, "fixture must actually bypass validation"
    with pytest.raises(MissingEvidenceError) as exc:
        build(scan_of(tmp_path), result([smuggled], []))
    assert "smuggler" in str(exc.value)


def test_impossible_evidence_range_is_rejected(tmp_path: Path) -> None:
    bad = object.__new__(Evidence)
    for f, v in (("file", "src/a.py"), ("start_line", 9), ("end_line", 2), ("rev", None)):
        object.__setattr__(bad, f, v)
    n = node("src/a.py#x")
    object.__setattr__(n, "evidence", (bad,))

    with pytest.raises(DiagnosticError) as exc:
        build(scan_of(tmp_path), result([n], []))
    assert exc.value.diagnostic.code == "SVA-B-002"


# --------------------------------------------------------------------------
# Contract 2: node ids are unique
# --------------------------------------------------------------------------


def test_two_different_nodes_claiming_one_id_is_an_error(tmp_path: Path) -> None:
    a = node("src/a.py#dup", evidence=(EV1,))
    b = node("src/a.py#dup", evidence=(EV2,), label="other")
    with pytest.raises(DiagnosticError) as exc:
        build(scan_of(tmp_path), result([a, b], []))
    assert exc.value.diagnostic.code == "SVA-B-001"


def test_identical_duplicates_are_harmless(tmp_path: Path) -> None:
    a = node("src/a.py#same")
    g = build(scan_of(tmp_path), result([a, a], []))
    assert len(g.nodes) == 1


def test_non_strict_collects_instead_of_raising(tmp_path: Path) -> None:
    """A report wants every problem in one pass; a gate wants the first."""
    a = node("src/a.py#dup", evidence=(EV1,))
    b = node("src/a.py#dup", evidence=(EV2,), label="other")
    g = build(scan_of(tmp_path), result([a, b], []), strict=False)
    assert {d.code for d in g.errors} == {"SVA-B-001"}


# --------------------------------------------------------------------------
# Contract 3: merging unions evidence, never drops it
# --------------------------------------------------------------------------


def test_repeated_edge_keys_union_their_evidence(tmp_path: Path) -> None:
    """Two import lines to one target are two facts sharing a key.

    Measured on a real repo: 0 of 135 repeated keys had identical evidence
    across copies, so deduping by key would silently discard provenance.
    """
    n1, n2 = node("src/a.py"), node("src/b.py")
    e1 = edge("src/a.py", "src/b.py", evidence=(EV1,))
    e2 = edge("src/a.py", "src/b.py", evidence=(EV2,))
    g = build(scan_of(tmp_path), result([n1, n2], [e1, e2]))

    assert len(g.edges) == 1
    assert {ev.start_line for ev in g.edges[0].evidence} == {1, 7}
    assert g.edges[0].attr("sites") == "2"


def test_merge_keeps_the_least_confident_classification(tmp_path: Path) -> None:
    """Claiming RESOLVED when one site said CANDIDATE overstates certainty."""
    n1, n2 = node("src/a.py"), node("src/b.py")
    confident = edge("src/a.py", "src/b.py", evidence=(EV1,), resolution=Resolution.RESOLVED)
    hedged = edge(
        "src/a.py",
        "src/b.py",
        evidence=(EV1, EV2),
        resolution=Resolution.CANDIDATE,
        arity=3,
    )
    g = build(scan_of(tmp_path), result([n1, n2], [confident, hedged]))
    assert g.edges[0].resolution is Resolution.CANDIDATE
    assert "SVA-B-003" in {d.code for d in g.diagnostics}


def test_merge_is_order_independent(tmp_path: Path) -> None:
    n1, n2 = node("src/a.py"), node("src/b.py")
    e1 = edge("src/a.py", "src/b.py", evidence=(EV1,))
    e2 = edge("src/a.py", "src/b.py", evidence=(EV2,))
    a = build(scan_of(tmp_path), result([n1, n2], [e1, e2]))
    b = build(scan_of(tmp_path), result([n2, n1], [e2, e1]))
    assert a.edges == b.edges


# --------------------------------------------------------------------------
# Endpoint integrity
# --------------------------------------------------------------------------


def test_dangling_endpoint_is_an_error(tmp_path: Path) -> None:
    n = node("src/a.py")
    e = edge("src/a.py", "src/nowhere.py")
    with pytest.raises(DiagnosticError) as exc:
        build(scan_of(tmp_path), result([n], [e]))
    assert exc.value.diagnostic.code == "SVA-B-004"


def test_real_extraction_produces_no_dangling_endpoints(tmp_path: Path) -> None:
    write(tmp_path, "src/__init__.py", "")
    write(tmp_path, "src/a.py", "def helper():\n    pass\n")
    write(tmp_path, "src/b.py", "from .a import helper\n\nhelper()\n")
    g = full(tmp_path)  # strict: would raise on any dangling endpoint
    assert g.nodes and g.edges


# --------------------------------------------------------------------------
# Contract 4: structural module identity
# --------------------------------------------------------------------------


def test_modules_come_from_directories(tmp_path: Path) -> None:
    write(tmp_path, "src/api/main.py", "x = 1\n")
    write(tmp_path, "src/api/routes.py", "y = 2\n")
    write(tmp_path, "src/auth/jwt.py", "z = 3\n")
    g = full(tmp_path)
    assert set(g.modules) >= {"src/api", "src/auth"}
    assert g.modules["src/api"].file_count == 2


def test_workspace_membership_is_recorded(tmp_path: Path) -> None:
    """The member is the useful grouping, not the root.

    A root-level workspace declaration contains everything and says nothing,
    so `packages/web` is the answer rather than `""`.
    """
    write(tmp_path, "pnpm-workspace.yaml", "packages:\n  - 'packages/*'\n")
    write(tmp_path, "packages/web/src/a.ts", "export const x = 1;\n")
    write(tmp_path, "packages/api/src/b.ts", "export const y = 2;\n")
    g = full(tmp_path)
    assert g.modules["packages/web/src"].package == "packages/web"
    assert g.modules["packages/api/src"].package == "packages/api"


@pytest.mark.parametrize(
    ("manifest", "content", "member_file", "expected"),
    [
        (
            "pnpm-workspace.yaml",
            "packages:\n  - 'packages/*'\n",
            "packages/web/a.ts",
            "packages/web",
        ),
        ("package.json", '{"name":"r","workspaces":["apps/*"]}', "apps/web/a.ts", "apps/web"),
        (
            "Cargo.toml",
            '[workspace]\nmembers = ["crates/core"]\n',
            "crates/core/src/lib.rs",
            "crates/core",
        ),
        (
            "pyproject.toml",
            '[tool.uv.workspace]\nmembers = ["libs/*"]\n',
            "libs/core/mod.py",
            "libs/core",
        ),
        ("go.work", "go 1.22\nuse ./svc\n", "svc/main.go", "svc"),
    ],
)
def test_member_globs_expand_for_every_workspace_kind(
    tmp_path: Path, manifest: str, content: str, member_file: str, expected: str
) -> None:
    """`detect` finds roots because that needs only filenames.

    Members need the full file list to expand `packages/*`, which is why the
    design puts the expansion here. Ancestor directories must all be
    considered: `packages/*` has to match `packages/web` even when the only
    file under it is `packages/web/src/a.ts`.
    """
    from svarupa.build import workspace_members

    write(tmp_path, manifest, content)
    write(tmp_path, member_file, "x = 1\n")
    assert workspace_members(detect(tmp_path)) == (expected,)


def test_module_dependencies_aggregate_from_file_imports(tmp_path: Path) -> None:
    write(tmp_path, "src/__init__.py", "")
    write(tmp_path, "src/api/__init__.py", "")
    write(tmp_path, "src/auth/__init__.py", "")
    write(tmp_path, "src/auth/jwt.py", "def verify():\n    pass\n")
    write(tmp_path, "src/api/main.py", "from ..auth.jwt import verify\n")
    g = full(tmp_path)
    assert ("src/api", "src/auth") in g.module_deps


def test_intra_module_imports_do_not_become_dependencies(tmp_path: Path) -> None:
    """A module does not depend on itself.

    This is also why resolution targets files rather than directories: at
    directory granularity every intra-package edge collapses to a self-edge
    and vanishes.
    """
    write(tmp_path, "src/__init__.py", "")
    write(tmp_path, "src/a.py", "def helper():\n    pass\n")
    write(tmp_path, "src/b.py", "from .a import helper\n")
    g = full(tmp_path)
    assert not [d for d in g.module_deps if d[0] == d[1]]


def test_test_files_do_not_shape_module_dependencies(tmp_path: Path) -> None:
    """Test code is 30-50% of a repo and imports everything.

    Letting it into the aggregation would bury the structure it is meant to
    reveal, so architecture-ineligible files are excluded here too.
    """
    write(tmp_path, "src/__init__.py", "")
    write(tmp_path, "src/api/__init__.py", "")
    write(tmp_path, "src/auth/__init__.py", "")
    write(tmp_path, "src/auth/jwt.py", "def verify():\n    pass\n")
    write(tmp_path, "src/api/main.py", "x = 1\n")
    write(tmp_path, "tests/test_all.py", "from src.auth.jwt import verify\n")
    g = full(tmp_path)
    assert not [d for d in g.module_deps if d[0].startswith("tests")]


# --------------------------------------------------------------------------
# Graph view and determinism
# --------------------------------------------------------------------------


def test_networkx_view_matches_the_typed_graph(tmp_path: Path) -> None:
    write(tmp_path, "src/__init__.py", "")
    write(tmp_path, "src/a.py", "def helper():\n    pass\n")
    write(tmp_path, "src/b.py", "from .a import helper\n")
    g = full(tmp_path)
    nxg = g.nx()
    assert set(nxg.nodes) == set(g.nodes)
    assert nxg.number_of_edges() <= len(g.edges)


@pytest.mark.determinism
def test_build_is_stable_across_runs(tmp_path: Path) -> None:
    write(tmp_path, "src/__init__.py", "")
    write(tmp_path, "src/a.py", "def helper():\n    pass\n")
    write(tmp_path, "src/b.py", "from .a import helper\n\nhelper()\n")
    runs = [
        (tuple(full(tmp_path).nodes), full(tmp_path).edges, full(tmp_path).module_deps)
        for _ in range(5)
    ]
    assert len(set(runs)) == 1


@pytest.mark.determinism
def test_module_records_are_canonically_ordered(tmp_path: Path) -> None:
    for d in ("zeta", "alpha", "Mid"):
        write(tmp_path, f"src/{d}/__init__.py", "")
        write(tmp_path, f"src/{d}/mod.py", "x = 1\n")
    recs = module_records(full(tmp_path))
    ids = [mid for mid, _ in recs]
    assert ids == sorted(ids)
