"""`extract` tests.

The invariant that matters most: **nothing enters the graph without evidence,
and nothing is invented to fill a gap.** A resolution we cannot make produces
no edge and increments a bin the report surfaces.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from svarupa.detect import detect
from svarupa.extract import declared_dependencies, extract
from svarupa.extract.base import CallShape, node_id
from svarupa.extract.python import PythonExtractor
from svarupa.model import EdgeKind, Resolution


def write(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf8")


def run(root: Path):
    scan = detect(root)
    return extract(scan, declared_dependencies(scan))


# --------------------------------------------------------------------------
# Pass 1: facts from one file, looking at nothing else
# --------------------------------------------------------------------------


def facts(src: str, path: str = "src/mod.py"):
    return PythonExtractor().parse(path, src.encode())


def test_collects_definitions_with_line_evidence() -> None:
    f = facts(
        "import os\n\n\ndef alpha():\n    pass\n\n\nclass Beta:\n    def gamma(self):\n        pass\n"
    )
    names = {s.name: s for s in f.symbols}
    assert set(names) == {"alpha", "Beta", "gamma"}
    assert names["alpha"].evidence.start_line == 4
    assert names["Beta"].evidence.start_line == 8
    assert names["gamma"].evidence.start_line == 9
    assert names["gamma"].enclosing_class == "Beta"
    assert names["gamma"].kind == "method"


def test_qualified_names_are_path_derived() -> None:
    f = facts("class A:\n    def m(self):\n        pass\n", "src/pkg/thing.py")
    quals = {s.qualified_name for s in f.symbols}
    assert quals == {"src.pkg.thing.A", "src.pkg.thing.A.m"}


def test_package_init_qualname_drops_the_init() -> None:
    f = facts("def go():\n    pass\n", "src/pkg/__init__.py")
    assert f.symbols[0].qualified_name == "src.pkg.go"


def test_underscore_names_are_not_exported() -> None:
    f = facts("def _hidden():\n    pass\n\n\ndef shown():\n    pass\n")
    export = {s.name: s.exported for s in f.symbols}
    assert export == {"_hidden": False, "shown": True}


@pytest.mark.parametrize(
    ("src", "shape", "receiver"),
    [
        ("def f():\n    helper()\n", CallShape.BARE, None),
        ("class A:\n    def f(self):\n        self.other()\n", CallShape.SELF, "self"),
        ("class A:\n    def f(self):\n        super().f()\n", CallShape.SUPER, "super"),
        ("def f():\n    mod.thing()\n", CallShape.QUALIFIED, "mod"),
        ("def f():\n    a.b.thing()\n", CallShape.MEMBER, "a.b"),
    ],
)
def test_call_shapes(src: str, shape: CallShape, receiver: str | None) -> None:
    """Shape decides which resolver applies, and the rates differ hugely."""
    calls = list(facts(src).calls)
    assert calls, "expected a call site"
    assert calls[0].shape is shape
    assert calls[0].receiver == receiver


def test_builtins_are_not_call_sites() -> None:
    """`print()` is not an unresolved intra-repo call."""
    assert facts("def f():\n    print(len([]))\n").calls == ()


def test_locally_defined_builtin_name_still_wins() -> None:
    """A project may define its own `filter`; the local definition is real."""
    f = facts("def filter(x):\n    return x\n\n\ndef g():\n    return filter(1)\n")
    assert any(s.name == "filter" for s in f.symbols)


def test_relative_import_records_its_level() -> None:
    f = facts("from ..pkg.mod import Thing, Other\n")
    imp = f.imports[0]
    assert imp.is_relative and imp.level == 2
    assert imp.names == ("Thing", "Other")


def test_aliased_import_keeps_both_names() -> None:
    f = facts("from .models import Request as Req\n")
    assert f.imports[0].alias_of == {"Req": "Request"}


def test_star_import_binds_nothing_rather_than_guessing() -> None:
    """`from x import *` binds an unknowable set.

    Emitting a guess would be exactly the invention fail-closed forbids.
    """
    f = facts("from .things import *\n")
    assert f.imports[0].names == ()


def test_syntax_errors_degrade_rather_than_crash() -> None:
    f = facts("def broken(:\n    pass\n\n\ndef fine():\n    pass\n")
    assert "SVA-X-001" in {d.code for d in f.diagnostics}
    assert any(s.name == "fine" for s in f.symbols), "parseable regions still extracted"


# --------------------------------------------------------------------------
# Fail-closed: the core invariant
# --------------------------------------------------------------------------


def test_every_node_and_edge_carries_evidence(tmp_path: Path) -> None:
    write(tmp_path, "src/a.py", "from .b import helper\n\n\ndef go():\n    return helper()\n")
    write(tmp_path, "src/b.py", "def helper():\n    return 1\n")
    write(tmp_path, "src/__init__.py", "")
    res = run(tmp_path)
    assert res.nodes and res.edges
    for n in res.nodes:
        assert n.evidence
    for e in res.edges:
        assert e.evidence


def test_evidence_points_at_lines_that_exist(tmp_path: Path) -> None:
    """A claim you cannot click through to is not a claim."""
    write(tmp_path, "src/a.py", "def one():\n    pass\n\n\ndef two():\n    pass\n")
    res = run(tmp_path)
    for n in res.nodes:
        for ev in n.evidence:
            lines = (tmp_path / ev.file).read_text().splitlines()
            assert 1 <= ev.start_line <= len(lines)
            assert ev.start_line <= ev.end_line <= len(lines)


def test_unresolvable_call_produces_no_edge(tmp_path: Path) -> None:
    """The dominant service-code shape: `x.method()` with x a local.

    Measured at ~5% resolvable. Emitting a plausible target here is exactly
    the invention the product exists to avoid.
    """
    write(tmp_path, "src/a.py", "def go(client):\n    return client.send()\n")
    res = run(tmp_path)
    assert not [e for e in res.edges if e.kind is EdgeKind.CALLS]
    assert res.scorecard.get("python", "calls", Resolution.UNRESOLVED) >= 1


# --------------------------------------------------------------------------
# Scorecard: three bins, and the difference between them
# --------------------------------------------------------------------------


def test_declared_dependency_is_external_not_unresolved(tmp_path: Path) -> None:
    write(tmp_path, "pyproject.toml", '[project]\ndependencies = ["requests"]\n')
    write(tmp_path, "src/a.py", "import requests\n")
    res = run(tmp_path)
    assert res.scorecard.get("python", "imports", Resolution.EXTERNAL) >= 1
    assert res.scorecard.get("python", "imports", Resolution.UNRESOLVED) == 0


def test_stdlib_is_external(tmp_path: Path) -> None:
    write(tmp_path, "src/a.py", "import json\nimport pathlib\n")
    res = run(tmp_path)
    assert res.scorecard.get("python", "imports", Resolution.EXTERNAL) == 2


def test_undeclared_unknown_import_is_unresolved_not_external(tmp_path: Path) -> None:
    """The bin that keeps the scorecard honest.

    Counting every failure as external would launder resolver bugs into a
    number that looks like honesty.
    """
    write(tmp_path, "src/a.py", "import totally_unknown_thing\n")
    res = run(tmp_path)
    assert res.scorecard.get("python", "imports", Resolution.UNRESOLVED) == 1
    assert res.scorecard.get("python", "imports", Resolution.EXTERNAL) == 0


def test_unresolved_samples_are_recorded_for_the_report(tmp_path: Path) -> None:
    write(tmp_path, "src/a.py", "import mystery_pkg\n")
    res = run(tmp_path)
    assert "mystery_pkg" in res.scorecard.samples[("python", "imports")]


def test_pinned_rate_excludes_external_from_the_denominator() -> None:
    from svarupa.extract.base import Scorecard

    sc = Scorecard()
    for _ in range(5):
        sc.record("python", EdgeKind.CALLS, Resolution.RESOLVED)
    for _ in range(5):
        sc.record("python", EdgeKind.CALLS, Resolution.UNRESOLVED)
    for _ in range(90):
        sc.record("python", EdgeKind.CALLS, Resolution.EXTERNAL)
    assert sc.pinned_rate("python", "calls") == 50.0, (
        "a framework-heavy repo must not look better than a plain one"
    )


def test_scorecard_is_per_language() -> None:
    from svarupa.extract.base import Scorecard

    sc = Scorecard()
    sc.record("python", EdgeKind.IMPORTS, Resolution.RESOLVED)
    sc.record("typescript", EdgeKind.IMPORTS, Resolution.UNRESOLVED)
    assert sc.pinned_rate("python", "imports") == 100.0
    assert sc.pinned_rate("typescript", "imports") == 0.0


# --------------------------------------------------------------------------
# Resolution
# --------------------------------------------------------------------------


def test_relative_import_resolves_to_a_file(tmp_path: Path) -> None:
    write(tmp_path, "src/__init__.py", "")
    write(tmp_path, "src/a.py", "from .b import helper\n")
    write(tmp_path, "src/b.py", "def helper():\n    pass\n")
    res = run(tmp_path)
    imports = [e for e in res.edges if e.kind is EdgeKind.IMPORTS]
    assert any(e.src == "src/a.py" and e.dst == "src/b.py" for e in imports)


def test_reexport_chain_resolves_to_the_definition_not_the_facade(tmp_path: Path) -> None:
    """The R2-4 finding.

    `from pkg import Thing` resolves at file level to `pkg/__init__.py`, but
    the definition lives in `pkg/core.py`. Stopping at the facade would make
    `impact_of_change` wrong for most of a well-packaged library's public API.
    """
    write(tmp_path, "pkg/__init__.py", "from .core import Thing\n")
    write(tmp_path, "pkg/core.py", "class Thing:\n    pass\n")
    write(tmp_path, "app.py", "from pkg import Thing\n")
    res = run(tmp_path)

    refs = [e for e in res.edges if e.kind is EdgeKind.REFERENCES and e.src == "app.py"]
    assert refs, "expected a symbol-level reference edge"
    assert refs[0].dst == node_id("pkg/core.py", "pkg.core.Thing"), (
        f"resolved to the facade instead of the definition: {refs[0].dst}"
    )


def test_self_method_resolves_through_the_mro(tmp_path: Path) -> None:
    """Measured 90-100% in the spikes; the one call shape that works well."""
    write(
        tmp_path,
        "src/a.py",
        "class Base:\n    def helper(self):\n        pass\n\n\n"
        "class Child(Base):\n    def go(self):\n        return self.helper()\n",
    )
    res = run(tmp_path)
    calls = [e for e in res.edges if e.kind is EdgeKind.CALLS]
    assert any(e.dst.endswith("Base.helper") for e in calls)


def test_inheritance_edges(tmp_path: Path) -> None:
    write(tmp_path, "src/a.py", "class Base:\n    pass\n\n\nclass Child(Base):\n    pass\n")
    res = run(tmp_path)
    inh = [e for e in res.edges if e.kind is EdgeKind.INHERITS]
    assert len(inh) == 1
    assert inh[0].src.endswith("Child") and inh[0].dst.endswith("Base")


def test_external_base_class_is_external_not_unresolved(tmp_path: Path) -> None:
    write(tmp_path, "src/a.py", "class Thing(Exception):\n    pass\n")
    res = run(tmp_path)
    assert res.scorecard.get("python", "inherits", Resolution.EXTERNAL) == 1


# --------------------------------------------------------------------------
# Candidate edges: F3
# --------------------------------------------------------------------------


def test_candidate_edges_carry_call_site_and_definition_site(tmp_path: Path) -> None:
    """A candidate claims 'one of N'. With one location it is uncheckable."""
    write(tmp_path, "src/a.py", "class A:\n    def run(self):\n        pass\n")
    write(tmp_path, "src/b.py", "class B:\n    def run(self):\n        pass\n")
    write(tmp_path, "src/c.py", "def go():\n    return run()\n")
    res = run(tmp_path)
    cands = [e for e in res.edges if e.resolution is Resolution.CANDIDATE]
    for e in cands:
        assert len(e.evidence) >= 2, "candidate needs both sites"
        assert e.arity >= 2


def test_hopeless_ambiguity_is_unresolved_not_a_candidate_swarm(tmp_path: Path) -> None:
    """`.get()` matching 40 methods tells a reader nothing.

    Above the arity ceiling it is counted honestly as unresolved rather than
    swamping the graph with meaningless candidates.
    """
    for i in range(12):
        write(tmp_path, f"src/m{i}.py", f"class C{i}:\n    def common(self):\n        pass\n")
    write(tmp_path, "src/caller.py", "def go():\n    return common()\n")
    res = run(tmp_path)
    assert not [
        e
        for e in res.edges
        if e.resolution is Resolution.CANDIDATE and e.src.startswith("src/caller.py")
    ]


# --------------------------------------------------------------------------
# Determinism
# --------------------------------------------------------------------------


@pytest.mark.determinism
def test_extraction_is_stable_across_runs(tmp_path: Path) -> None:
    write(tmp_path, "src/__init__.py", "")
    write(tmp_path, "src/a.py", "from .b import helper\n\n\ndef go():\n    return helper()\n")
    write(tmp_path, "src/b.py", "def helper():\n    return 1\n")
    runs = [(run(tmp_path).nodes, run(tmp_path).edges) for _ in range(5)]
    assert len(set(runs)) == 1


@pytest.mark.determinism
def test_pass_one_reads_only_its_own_file(tmp_path: Path) -> None:
    """What makes pass 1 cacheable by content hash.

    If parsing consulted another file, a content hash would be an unsound key
    and `--update` could diverge from a full build.
    """
    src = "from .other import thing\n\n\ndef go():\n    return thing()\n"
    a = PythonExtractor().parse("src/a.py", src.encode())
    write(tmp_path, "src/other.py", "def thing():\n    pass\n")
    b = PythonExtractor().parse("src/a.py", src.encode())
    assert a == b, "pass 1 output changed when an unrelated file appeared"


# --------------------------------------------------------------------------
# Graph integrity and source roots
# --------------------------------------------------------------------------


def test_every_edge_endpoint_is_a_real_node(tmp_path: Path) -> None:
    """A dangling endpoint is a missing-node bug wearing a graph-shaped mask.

    Import edges connect file to file and module-level calls are attributed to
    the file, so a Node must exist per file. Without one, `build` would see
    endpoints referencing ids that exist nowhere.
    """
    write(tmp_path, "src/__init__.py", "")
    write(tmp_path, "src/a.py", "def helper():\n    pass\n")
    write(tmp_path, "src/b.py", "from .a import helper\n\nhelper()\n")
    res = run(tmp_path)
    ids = {n.id for n in res.nodes}
    dangling = [
        (e.src, e.dst, e.kind.value) for e in res.edges if e.src not in ids or e.dst not in ids
    ]
    assert not dangling, f"edges reference non-existent nodes: {dangling}"


def test_a_module_node_exists_per_file(tmp_path: Path) -> None:
    from svarupa.model import NodeKind

    write(tmp_path, "src/a.py", "x = 1\n")
    write(tmp_path, "src/b.py", "y = 2\n")
    res = run(tmp_path)
    modules = {n.id for n in res.nodes if n.kind is NodeKind.MODULE}
    assert modules == {"src/a.py", "src/b.py"}


def test_package_under_a_non_standard_root_resolves(tmp_path: Path) -> None:
    """`src`, `lib`, `app` were hardcoded, so `backend/mylib/` resolved nothing.

    Import roots are now derived from where packages actually live, plus any
    workspace roots `detect` discovered.
    """
    write(tmp_path, "backend/mylib/__init__.py", "")
    write(tmp_path, "backend/mylib/core.py", "def go():\n    pass\n")
    write(tmp_path, "backend/mylib/api.py", "from mylib.core import go\n")
    res = run(tmp_path)
    imports = {(e.src, e.dst) for e in res.edges if e.kind is EdgeKind.IMPORTS}
    assert ("backend/mylib/api.py", "backend/mylib/core.py") in imports


def test_source_roots_do_not_swallow_the_standard_library(tmp_path: Path) -> None:
    """Regression for a fix that over-corrected.

    Allowing the resolver to fall back to the root directory itself made every
    import match: `import typing` resolved to `src/pkg/__init__.py` merely
    because that package existed. External dropped to zero and the scorecard
    claimed 100% import resolution while being entirely wrong. Stripping now
    happens only within the dotted specifier, never into the root prefix.
    """
    write(tmp_path, "src/pkg/__init__.py", "")
    write(tmp_path, "src/pkg/thing.py", "import typing\nimport os\nimport collections\n")
    res = run(tmp_path)
    assert not [e for e in res.edges if e.kind is EdgeKind.IMPORTS]
    assert res.scorecard.get("python", "imports", Resolution.EXTERNAL) == 3
    assert res.scorecard.get("python", "imports", Resolution.RESOLVED) == 0


def test_roots_do_not_reach_across_unrelated_trees(tmp_path: Path) -> None:
    """A specifier must not match a same-named module anywhere in the repo."""
    write(tmp_path, "src/pkg/__init__.py", "")
    write(tmp_path, "src/pkg/utils.py", "def real():\n    pass\n")
    write(tmp_path, "examples/demo/__init__.py", "")
    write(tmp_path, "examples/demo/utils.py", "def decoy():\n    pass\n")
    write(tmp_path, "src/pkg/main.py", "from pkg.utils import real\n")
    res = run(tmp_path)
    imports = {(e.src, e.dst) for e in res.edges if e.kind is EdgeKind.IMPORTS}
    assert ("src/pkg/main.py", "src/pkg/utils.py") in imports
    assert ("src/pkg/main.py", "examples/demo/utils.py") not in imports


@pytest.mark.parametrize(
    ("files", "why"),
    [
        (
            {
                "pkg/__init__.py": "",
                "pkg/a.py": "from .b import thing\n",
                "pkg/b.py": "from .a import thing\n",
                "app.py": "from pkg.a import thing\n",
            },
            "circular re-export must terminate",
        ),
        (
            {
                "src/x.py": "class A(B):\n    pass\n\n\nclass B(A):\n    def m(self):\n        return self.m()\n"
            },
            "circular inheritance is invalid Python but parseable",
        ),
    ],
)
def test_cycles_terminate(tmp_path: Path, files: dict[str, str], why: str) -> None:
    for rel, text in files.items():
        write(tmp_path, rel, text)
    res = run(tmp_path)  # must not hang or recurse forever
    assert res.nodes, why


# --------------------------------------------------------------------------
# Decoy fixtures: catching a WRONG resolution, not just a missing one
#
# Review #4's central criticism: every prior test used a repo with exactly one
# plausible target, so first-match-wins, bare-name indexing and length-sorted
# roots were indistinguishable from correct resolution. Each fixture below
# places a decoy so the assertion names the specific expected dst.
# --------------------------------------------------------------------------


def test_bare_call_binds_the_function_not_the_method(tmp_path: Path) -> None:
    """A bare name in Python cannot dispatch to an instance method.

    Lexicographic ordering put `src.mod.A.f` before `src.mod.f`, so the
    resolver emitted a confident RESOLVED edge to a target that is not merely
    arbitrary but impossible.
    """
    write(
        tmp_path,
        "src/mod.py",
        "class A:\n    def f(self):\n        pass\n\n\ndef f():\n    pass\n\n\ndef go():\n    return f()\n",
    )
    res = run(tmp_path)
    calls = [e for e in res.edges if e.kind is EdgeKind.CALLS]
    assert len(calls) == 1
    assert calls[0].dst == node_id("src/mod.py", "src.mod.f")


def test_mro_does_not_bind_a_same_named_class_in_another_file(tmp_path: Path) -> None:
    """Bases were resolved by bare name across the whole repo.

    `Child(Base)` importing the real `Base` from `.a` bound `self.helper()` to
    an unrelated `Base` in `z.py` that happened to define `helper`.
    """
    write(tmp_path, "src/__init__.py", "")
    write(tmp_path, "src/a.py", "class Base:\n    pass\n")
    write(tmp_path, "src/z.py", "class Base:\n    def helper(self):\n        pass\n")
    write(
        tmp_path,
        "src/c.py",
        "from .a import Base\n\n\nclass Child(Base):\n    def go(self):\n        return self.helper()\n",
    )
    res = run(tmp_path)
    wrong = [e for e in res.edges if e.kind is EdgeKind.CALLS and "z.py" in e.dst]
    assert not wrong, f"bound to the decoy class: {[e.dst for e in wrong]}"


def test_import_prefers_a_root_that_is_an_ancestor_of_the_importer(tmp_path: Path) -> None:
    """Roots sorted by name length let `examples` beat `src`.

    The decoy uses the SAME package name as the real one, which is the only
    arrangement where the collision actually fires. An earlier version of this
    test used different names and so could never have caught it.
    """
    write(tmp_path, "src/pkg/__init__.py", "")
    write(tmp_path, "src/pkg/utils.py", "def real():\n    pass\n")
    write(tmp_path, "examples/pkg/__init__.py", "")
    write(tmp_path, "examples/pkg/utils.py", "def decoy():\n    pass\n")
    write(tmp_path, "src/pkg/main.py", "from pkg.utils import real\n")
    res = run(tmp_path)
    targets = {
        e.dst for e in res.edges if e.kind is EdgeKind.IMPORTS and e.src == "src/pkg/main.py"
    }
    assert "src/pkg/utils.py" in targets
    assert "examples/pkg/utils.py" not in targets


def test_diamond_inheritance_is_one_definition_not_a_two_way_ambiguity(tmp_path: Path) -> None:
    """`_mro` deduped cycles but not converging branches.

    `D(B, C)` where both reach `A.m` produced two identical edges claiming
    arity 2, while exactly one definition exists. Arity is a claim shown to a
    reader; an inflated one is an invented fact carried in the evidence.
    """
    write(
        tmp_path,
        "src/d.py",
        "class A:\n    def m(self):\n        pass\n\n\nclass B(A):\n    pass\n\n\n"
        "class C(A):\n    pass\n\n\nclass D(B, C):\n    def go(self):\n        return self.m()\n",
    )
    res = run(tmp_path)
    calls = [e for e in res.edges if e.kind is EdgeKind.CALLS]
    assert len(calls) == 1
    assert calls[0].resolution is Resolution.RESOLVED
    assert calls[0].arity == 1


def test_multi_segment_relative_import_anchors_correctly(tmp_path: Path) -> None:
    """Level was re-derived in pass 2 as `spec.count(".")`.

    `from ..util.mod import helper` has level 2 (leading dots) but three dots
    total, so the recomputation anchored at the wrong directory. Pass 1 already
    computed the right value; pass 2 must carry it rather than re-parse.
    """
    for rel in ("src/__init__.py", "src/util/__init__.py", "src/app/__init__.py"):
        write(tmp_path, rel, "")
    write(tmp_path, "src/util/mod.py", "def helper():\n    pass\n")
    write(
        tmp_path,
        "src/app/main.py",
        "from ..util.mod import helper\n\n\ndef go():\n    return helper()\n",
    )
    res = run(tmp_path)
    calls = [e for e in res.edges if e.kind is EdgeKind.CALLS]
    assert calls, "multi-segment relative import failed to anchor"
    assert calls[0].dst == node_id("src/util/mod.py", "src.util.mod.helper")


# --------------------------------------------------------------------------
# Scorecard honesty regressions
# --------------------------------------------------------------------------


def test_absolute_from_import_does_not_record_a_phantom_name() -> None:
    """`child is module_node` never matched.

    py-tree-sitter returns a fresh wrapper per `child_by_field_name` call, so
    identity comparison always failed and the module path was captured as if
    it were an imported symbol. Every absolute from-import then produced a
    phantom unresolved reference, polluting the headline honesty feature with
    the extractor's own bug.
    """
    from svarupa.extract.python import PythonExtractor

    f = PythonExtractor().parse("src/a.py", b"from mypkg.b import helper\n")
    assert f.imports[0].names == ("helper",)
    assert f.imports[0].specifier == "mypkg.b"


def test_plain_import_is_not_treated_as_a_symbol_reference(tmp_path: Path) -> None:
    """`names` means module paths for `import x.y`, symbols for `from x import y`."""
    write(tmp_path, "pkg/__init__.py", "")
    write(tmp_path, "pkg/b.py", "def thing():\n    pass\n")
    write(tmp_path, "app.py", "import pkg.b\n")
    res = run(tmp_path)
    assert res.scorecard.get("python", "references", Resolution.UNRESOLVED) == 0


@pytest.mark.parametrize(
    ("src", "manifest", "expect_external"),
    [
        ("class T(CompletelyUnknownBase):\n    pass\n", None, False),
        ("class T(Exception):\n    pass\n", None, True),
        (
            "from django.db import Model\n\n\nclass T(Model):\n    pass\n",
            '[project]\ndependencies = ["django"]\n',
            True,
        ),
    ],
)
def test_base_class_external_is_a_claim_not_a_fallback(
    tmp_path: Path, src: str, manifest: str | None, expect_external: bool
) -> None:
    """An unknown base was recorded EXTERNAL unconditionally.

    A resolver bug losing every intra-repo base would then have scored 100%
    external and 0% unresolved, which is precisely the laundering the third
    bin exists to prevent.
    """
    write(tmp_path, "src/a.py", src)
    if manifest:
        write(tmp_path, "pyproject.toml", manifest)
    res = run(tmp_path)
    ext = res.scorecard.get("python", "inherits", Resolution.EXTERNAL)
    unres = res.scorecard.get("python", "inherits", Resolution.UNRESOLVED)
    assert (ext, unres) == ((1, 0) if expect_external else (0, 1))


def test_external_symbol_imports_land_in_a_bin(tmp_path: Path) -> None:
    """These fell through a `continue` and were counted nowhere.

    Every reference must land in exactly one bin, or the denominators are
    fiction.
    """
    write(tmp_path, "pyproject.toml", '[project]\ndependencies = ["requests"]\n')
    write(tmp_path, "src/a.py", "from requests import Session, Response\n")
    res = run(tmp_path)
    assert res.scorecard.get("python", "references", Resolution.EXTERNAL) == 2


def test_dependency_groups_are_declared_dependencies(tmp_path: Path) -> None:
    """PEP 735. Without it the tool cried wolf about its own build."""
    write(tmp_path, "pyproject.toml", '[dependency-groups]\ndev = ["pytest>=8"]\n')
    write(tmp_path, "src/a.py", "import pytest\n")
    res = run(tmp_path)
    assert res.scorecard.get("python", "imports", Resolution.EXTERNAL) == 1
    assert res.scorecard.get("python", "imports", Resolution.UNRESOLVED) == 0


def test_grammar_version_matches_the_installed_pin() -> None:
    """Guards against silent drift when the grammar pin is bumped."""
    from importlib.metadata import version

    from svarupa.extract.python import PythonExtractor

    assert PythonExtractor.grammar_version == version("tree-sitter-python")


def test_conditional_definition_yields_one_node_and_a_diagnostic(tmp_path: Path) -> None:
    """`try/except ImportError` defines the same name twice.

    Two Nodes claiming one id with different evidence is a graph-integrity
    violation. Keeping the first and saying so beats silently collapsing them,
    since a conditional definition is itself a fact worth surfacing.
    """
    write(
        tmp_path,
        "src/mod.py",
        "try:\n    from fast import connect\nexcept ImportError:\n"
        "    def connect():\n        pass\n\n\ndef connect():\n    pass\n",
    )
    res = run(tmp_path)
    ids = [n.id for n in res.nodes]
    assert len(ids) == len(set(ids)), "duplicate node ids"
    assert "SVA-X-002" in {d.code for d in res.diagnostics}


def test_repeated_imports_keep_their_own_evidence(tmp_path: Path) -> None:
    """Two import lines to the same target are two facts, not one.

    They share an (src, dst, kind) key but carry different lines. Deduping
    here would discard evidence; merging is `build`'s job, and it must union
    rather than drop.
    """
    write(tmp_path, "pkg/__init__.py", "")
    write(tmp_path, "pkg/target.py", "def a():\n    pass\n\n\ndef b():\n    pass\n")
    write(tmp_path, "pkg/user.py", "from .target import a\nfrom .target import b\n")
    res = run(tmp_path)
    imports = [e for e in res.edges if e.kind is EdgeKind.IMPORTS and e.src == "pkg/user.py"]
    lines = sorted(ev.start_line for e in imports for ev in e.evidence)
    assert lines == [1, 2], f"lost an import site: {lines}"
