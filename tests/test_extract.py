"""`extract` tests.

The invariant that matters most: **nothing enters the graph without evidence,
and nothing is invented to fill a gap.** A resolution we cannot make produces
no edge and increments a bin the report surfaces.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from svarupa.detect import detect
from svarupa.extract import _EXTRACTORS, declared_dependencies, extract
from svarupa.extract.base import CallShape, node_id
from svarupa.extract.packs import extractor
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
    return extractor("python").parse(path, src.encode())


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
    a = extractor("python").parse("src/a.py", src.encode())
    write(tmp_path, "src/other.py", "def thing():\n    pass\n")
    b = extractor("python").parse("src/a.py", src.encode())
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
    f = extractor("python").parse("src/a.py", b"from mypkg.b import helper\n")
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

    assert extractor("python").grammar_version == version("tree-sitter-python")


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


# --------------------------------------------------------------------------
# Degrade, do not disable. Both cases found by running on a real repository.
# --------------------------------------------------------------------------


def test_a_deeply_nested_file_is_capped_rather_than_crashing(tmp_path: Path) -> None:
    """One 16 KB minified bundle raised RecursionError out of the extractor and
    took a whole 10,403-file analysis down with it.

    The nesting here is generated rather than pasted, so the fixture actually
    exceeds the cap instead of merely looking deep.
    """
    from svarupa.extract.base import MAX_AST_DEPTH

    depth = MAX_AST_DEPTH + 200
    write(tmp_path, "bundle.js", "const x = " + "(" * depth + "1" + ")" * depth + ";\n")
    write(tmp_path, "real.ts", "export function keep(): number {\n  return 1;\n}\n")

    scan = detect(tmp_path)
    result = extract(scan)  # must not raise

    assert any(d.code == "SVA-X-003" for d in result.diagnostics), [
        d.code for d in result.diagnostics
    ]
    # And the rest of the repository still produced facts.
    assert any(n.id.endswith(".keep") for n in result.nodes), "capping one file lost the others"


def test_an_extractor_that_raises_costs_only_its_own_file(tmp_path: Path) -> None:
    """The `try` used to cover only `read_bytes`, so any parser failure escaped.

    Broad on purpose: the guarantee is that no extractor bug, present or
    future, can cost more than the file it happened on.
    """
    import svarupa.extract as extract_mod

    write(tmp_path, "boom.py", "x = 1\n")
    write(tmp_path, "fine.py", "def keep():\n    pass\n")
    scan = detect(tmp_path)

    real = extract_mod._EXTRACTORS["python"]

    class Exploding:
        lang = real.lang
        grammar_version = real.grammar_version

        def parse(self, path: str, data: bytes) -> object:
            if path == "boom.py":
                raise RuntimeError("kaboom")
            return real.parse(path, data)

    original = dict(extract_mod._EXTRACTORS)
    extract_mod._EXTRACTORS["python"] = Exploding()  # type: ignore[assignment]
    try:
        result = extract_mod.extract(scan)
    finally:
        extract_mod._EXTRACTORS.clear()
        extract_mod._EXTRACTORS.update(original)

    crashes = [d for d in result.diagnostics if d.code == "SVA-X-004"]
    assert [d.subject for d in crashes] == ["boom.py"]
    assert "RuntimeError" in crashes[0].message
    assert any(n.id.endswith(".keep") for n in result.nodes), (
        "one exploding file lost the others"
    )


def test_namespace_package_submodule_import_resolves(tmp_path: Path) -> None:
    """PEP 420: no `__init__.py` anywhere, which is how real service repos are
    laid out. `from agent.schema import schema` must resolve to
    `agent/schema/schema.py`. Measured before the fix on a real two-service
    FastAPI repo: every absolute self-package import was unresolved, and an
    added cross-module import diffed the lockfile by zero lines."""
    write(tmp_path, "agent/config.py", "from agent.schema import schema\n")
    write(tmp_path, "agent/schema/schema.py", "x = 1\n")
    res = run(tmp_path)
    imports = [
        (e.src, e.dst)
        for e in res.edges
        if e.kind is EdgeKind.IMPORTS and e.resolution is Resolution.RESOLVED
    ]
    assert ("agent/config.py", "agent/schema/schema.py") in imports


def test_namespace_package_import_of_missing_submodule_stays_unresolved(
    tmp_path: Path,
) -> None:
    """The fallback must not invent an edge for a name that is not there."""
    write(tmp_path, "agent/config.py", "from agent.schema import nothing\n")
    write(tmp_path, "agent/schema/schema.py", "x = 1\n")
    res = run(tmp_path)
    assert not [e for e in res.edges if e.kind is EdgeKind.IMPORTS and "nothing" in e.dst]
    assert res.scorecard.get("python", "references", Resolution.UNRESOLVED) >= 1


def test_a_decorated_method_keeps_kind_and_decorator() -> None:
    f = facts("class A:\n    @staticmethod\n    def build():\n        pass\n")
    build = next(s for s in f.symbols if s.name == "build")
    assert build.kind == "method"
    assert build.enclosing_class == "A"
    assert [d.name for d in build.decorators] == ["staticmethod"]


def test_languages_without_a_pack_are_reported_once_each(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Every detected language has a pack now, so take one away.
    monkeypatch.delitem(_EXTRACTORS, "rust")
    write(tmp_path, "lib/a.rs", "fn a() {}\n")
    write(tmp_path, "lib/b.rs", "fn b() {}\n")
    write(tmp_path, "app.py", "def keep():\n    pass\n")
    result = run(tmp_path)
    x012 = [d for d in result.diagnostics if d.code == "SVA-X-012"]
    assert [d.subject for d in x012] == ["rust"]
    assert "2 rust files" in x012[0].message
    assert any(n.id.endswith(".keep") for n in result.nodes)


def test_a_repository_of_packed_languages_gets_no_x012(tmp_path: Path) -> None:
    write(tmp_path, "app.py", "def keep():\n    pass\n")
    write(tmp_path, "web/a.ts", "export const a = 1;\n")
    write(tmp_path, "db/schema.sql", "create table t (id int);\n")
    result = run(tmp_path)
    assert not [d for d in result.diagnostics if d.code == "SVA-X-012"]


@pytest.mark.parametrize("stmt", ["from app import crud", "from . import crud"])
def test_a_call_through_an_imported_submodule_resolves(tmp_path: Path, stmt: str) -> None:
    write(tmp_path, "app/__init__.py", "")
    write(tmp_path, "app/crud.py", "def create_user():\n    return 1\n")
    write(
        tmp_path, "app/routes.py", f"{stmt}\n\n\ndef make():\n    return crud.create_user()\n"
    )
    calls = {(e.src, e.dst) for e in run(tmp_path).edges if e.kind is EdgeKind.CALLS}
    assert ("app/routes.py#app.routes.make", "app/crud.py#app.crud.create_user") in calls


def test_an_override_wins_over_the_method_it_overrides(tmp_path: Path) -> None:
    """The nearest class in the MRO that defines the name is the one called.

    Collecting every same-named method along the chain made an override and
    the method it hides a two-way CANDIDATE, though only one can run.
    """
    write(
        tmp_path,
        "src/a.py",
        "class Base:\n    def m(self):\n        pass\n\n\n"
        "class Child(Base):\n    def m(self):\n        pass\n\n"
        "    def go(self):\n        return self.m()\n",
    )
    calls = [e for e in run(tmp_path).edges if e.kind is EdgeKind.CALLS]
    assert len(calls) == 1
    assert calls[0].resolution is Resolution.RESOLVED
    assert calls[0].dst.endswith("Child.m")


# --------------------------------------------------------------------------
# Parameter and local types
# --------------------------------------------------------------------------

REPO_PY = "class Repo:\n    def save(self):\n        pass\n"
OTHER_PY = "class Other:\n    def save(self):\n        pass\n    def x(self):\n        pass\n"
PY_IMPORTS = "from repo import Repo\nfrom other import Other\n"


def locals_of(src: str, path: str = "src/a.py") -> set[tuple[str, str, str]]:
    return {(x.function, x.name, x.type_name) for x in facts(src, path).locals}


def scoped_locals(src: str) -> set[tuple[str, str, str, int, int]]:
    f = facts(src, "src/a.py")
    return {(x.function, x.name, x.type_name, x.start_line, x.end_line) for x in f.locals}


def typed_calls(tmp_path: Path, body: str) -> set[str]:
    write(tmp_path, "repo.py", REPO_PY)
    write(tmp_path, "other.py", OTHER_PY)
    write(tmp_path, "a.py", PY_IMPORTS + body)
    return {e.dst for e in run(tmp_path).edges if e.kind is EdgeKind.CALLS}


def saves_repo(calls: set[str]) -> bool:
    return any(d.endswith("Repo.save") for d in calls)


def test_a_typed_parameter_resolves(tmp_path: Path) -> None:
    assert saves_repo(typed_calls(tmp_path, "def f(r: Repo):\n    r.save()\n"))


def test_a_default_parameter_and_a_fastapi_depends_resolve(tmp_path: Path) -> None:
    body = "def f(r: Repo = Depends(get_repo), s: Other = None):\n    r.save()\n"
    assert saves_repo(typed_calls(tmp_path, body))
    assert locals_of(body) == {("src.a.f", "r", "Repo"), ("src.a.f", "s", "Other")}


def test_a_method_parameter_resolves_and_self_is_not_a_local(tmp_path: Path) -> None:
    body = "class S:\n    def f(self, r: Repo, cls=None):\n        r.save()\n"
    assert saves_repo(typed_calls(tmp_path, body))
    assert locals_of(body) == {("src.a.S.f", "r", "Repo"), ("src.a.S.f", "cls", "")}
    assert locals_of("class S:\n    @classmethod\n    def g(cls, r: Repo): pass") == {
        ("src.a.S.g", "r", "Repo")
    }


def test_wrappers_are_unwrapped() -> None:
    src = (
        "from typing import Annotated, Optional, Union\n"
        "def f(a: Optional[Repo], b: Repo | None, c: Union[Repo, None], d: Annotated[Repo, 1],\n"
        '      e: "Repo", f: "Optional[Repo]", g: Optional["Repo"], h: None | Repo,\n'
        "      i: typing.Optional[Repo], j: Annotated[Optional[Repo], Depends(x, y)]): pass"
    )
    got = {n: t for (_, n, t) in locals_of(src)}
    assert got == dict.fromkeys("abcdefghij", "Repo")


def test_unions_containers_and_typevars_give_no_type() -> None:
    src = (
        "from typing import Any, Callable, TypeVar, Union\n"
        'T = TypeVar("T")\n'
        "def f(a: A | B, b: Union[A, B], c: T, d: Any, e: list[Repo], f: Callable[[A], B],\n"
        "      g: type[Repo], h: Optional[A | B], i: Optional[T], j: models.Repo, k: dict[str, Repo],\n"
        "      l: 'A | B', m: None, n: Optional[list[Repo]]): pass\n"
        "def g[S](x: S, y: Repo): pass\n"
    )
    got = {(f, n): t for (f, n, t) in locals_of(src)}
    for name in "abcdefghijklmn":
        assert got[("src.a.f", name)] == "", name
    assert got[("src.a.g", "x")] == "" and got[("src.a.g", "y")] == "Repo"


def test_untyped_and_splat_parameters_hide_the_name() -> None:
    src = "def f(a, b=1, *args: Repo, **kw): pass"
    assert locals_of(src) == {
        ("src.a.f", "a", ""),
        ("src.a.f", "b", ""),
        ("src.a.f", "args", ""),
        ("src.a.f", "kw", ""),
    }


def test_an_annotated_assignment_resolves(tmp_path: Path) -> None:
    assert saves_repo(typed_calls(tmp_path, "def f():\n    r: Repo = make()\n    r.save()\n"))
    assert saves_repo(typed_calls(tmp_path, "def f():\n    r: Optional[Repo]\n    r.save()\n"))
    assert locals_of("def f():\n    self.r: Repo = 1") == set()


def test_an_untyped_assignment_is_not_inferred_and_hides_the_name(tmp_path: Path) -> None:
    body = "def f():\n    r = Repo()\n    r.save()\n"
    assert locals_of(body) == {("src.a.f", "r", "")}
    assert not saves_repo(typed_calls(tmp_path, body))
    two_ways = "def f(r: Repo):\n    r = other()\n    r.save()\n"
    assert not saves_repo(typed_calls(tmp_path, two_ways))


def test_every_binding_form_is_recorded_untyped() -> None:
    src = (
        "def f(xs):\n"
        "    a, (b, *c) = xs\n"
        "    d = e = 1\n"
        "    for g, h in xs: pass\n"
        "    with open() as i, j as (k, m): pass\n"
        "    if (n := 3): pass\n"
        "    try: pass\n"
        "    except (A, B) as o: pass\n"
        "    def p(): pass\n"
        "    class Q: pass\n"
        "    match xs:\n"
        "        case Repo(x=r, y=[s, *t]) as u: pass\n"
        "        case {'k': v, **w}: pass\n"
        "        case y: pass\n"
    )
    got = {n: t for (fn, n, t) in locals_of(src) if fn == "src.a.f"}
    names = "xs a b c d e g h i k m n o p Q r s t u v w y"
    assert got == dict.fromkeys(names.split(), "")


def test_an_except_name_is_typed_only_for_a_single_plain_name() -> None:
    src = (
        "def f():\n    try: pass\n    except Boom as e: pass\n"
        "def g():\n    try: pass\n    except (A, B) as e: pass\n"
        "def h():\n    try: pass\n    except mod.Boom as e: pass\n"
    )
    assert locals_of(src) == {
        ("src.a.f", "e", "Boom"),
        ("src.a.g", "e", ""),
        ("src.a.h", "e", ""),
    }


def test_a_class_pattern_does_not_type_its_subject(tmp_path: Path) -> None:
    body = "def f(x):\n    match x:\n        case Repo():\n            x.save()\n"
    assert locals_of(body) == {("src.a.f", "x", "")}
    assert not saves_repo(typed_calls(tmp_path, body))


def test_isinstance_is_not_a_binding(tmp_path: Path) -> None:
    body = "def f(x):\n    if isinstance(x, Repo):\n        x.save()\n"
    assert locals_of(body) == {("src.a.f", "x", "")}
    assert not saves_repo(typed_calls(tmp_path, body))


def test_global_and_nonlocal_names_are_not_locals() -> None:
    src = (
        "def f():\n    global a\n    a = 1\n    b = 2\n"
        "def outer(r: Repo):\n    n = 1\n    def inner():\n        nonlocal n\n        n = 2\n"
    )
    got = locals_of(src)
    assert ("src.a.f", "a", "") not in got and ("src.a.f", "b", "") in got
    # `n` in inner is outer's variable: recorded once, over outer's lines
    mine = [x for x in scoped_locals(src) if x[0] == "src.a.outer.inner" and x[1] == "n"]
    assert mine == [("src.a.outer.inner", "n", "", 5, 9)]


def test_comprehension_variables_are_scoped_to_the_comprehension() -> None:
    src = "def f(r: Repo, xs):\n    ys = [r.save() for r in xs]\n    r.save()\n"
    got = scoped_locals(src)
    assert ("src.a.f", "r", "", 2, 2) in got
    assert ("src.a.f", "r", "Repo", 1, 3) in got


def test_a_comprehension_variable_hides_a_typed_parameter(tmp_path: Path) -> None:
    body = "def f(r: Repo, xs):\n    return [r.save() for r in xs]\n"
    assert not saves_repo(typed_calls(tmp_path, body))
    after = "def f(r: Repo, xs):\n    ys = [1 for r in xs]\n    r.save()\n"
    assert saves_repo(typed_calls(tmp_path, after))


def test_a_lambda_parameter_hides_the_name_inside_the_lambda_only(tmp_path: Path) -> None:
    body = "def f(r: Repo):\n    g = lambda r: r.save()\n    r.save()\n"
    assert not saves_repo(
        typed_calls(tmp_path, "def f(r: Repo):\n    return lambda r: r.save()\n")
    )
    assert saves_repo(typed_calls(tmp_path, body.replace("g = lambda r: r.save()", "g = 1")))
    assert ("src.a.f", "r", "", 2, 2) in scoped_locals(body)


def test_a_walrus_binds_untyped(tmp_path: Path) -> None:
    body = "def f(r: Repo):\n    if (r := other()):\n        r.save()\n"
    assert not saves_repo(typed_calls(tmp_path, body))


def test_a_nested_function_sees_the_outer_locals(tmp_path: Path) -> None:
    body = "def f(r: Repo):\n    def g():\n        r.save()\n    g()\n"
    assert saves_repo(typed_calls(tmp_path, body))
    own = "def f(r: Repo):\n    def g(r):\n        r.save()\n    g(1)\n"
    assert not saves_repo(typed_calls(tmp_path, own))
    assigned = "def f(r: Repo):\n    def g():\n        r = make()\n        r.save()\n"
    assert not saves_repo(typed_calls(tmp_path, assigned))
    deep = "def f(r: Repo):\n    def g():\n        def h():\n            r.save()\n"
    assert saves_repo(typed_calls(tmp_path, deep))
    method = "def f(r: Repo):\n    class C:\n        def m(self):\n            r.save()\n"
    assert saves_repo(typed_calls(tmp_path, method))


def test_a_global_in_a_nested_function_does_not_inherit_the_outer_local(tmp_path: Path) -> None:
    body = "def f(r: Repo):\n    def g():\n        global r\n        r.save()\n"
    assert not saves_repo(typed_calls(tmp_path, body))


def test_a_nested_def_or_class_name_hides_the_outer_name(tmp_path: Path) -> None:
    body = "def f(r: Repo):\n    def r():\n        pass\n    r.save()\n"
    assert not saves_repo(typed_calls(tmp_path, body))
    body = "def f(r: Repo):\n    class r:\n        pass\n    r.save()\n"
    assert not saves_repo(typed_calls(tmp_path, body))


def test_a_local_hides_an_import_of_the_same_name_only_in_its_scope(tmp_path: Path) -> None:
    write(tmp_path, "util.py", "def go():\n    pass\n")
    write(tmp_path, "repo.py", REPO_PY)
    write(
        tmp_path,
        "a.py",
        "import util\nfrom repo import Repo\n\n"
        "def hidden(util: Repo):\n    util.go()\n\n"
        "def shown():\n    util.go()\n",
    )
    edges = {(e.src, e.dst) for e in run(tmp_path).edges if e.kind is EdgeKind.CALLS}
    assert not any(s.endswith("a.hidden") and d.endswith("util.go") for s, d in edges)
    assert any(s.endswith("a.shown") and d.endswith("util.go") for s, d in edges)


def test_a_local_shadows_an_import_without_a_type(tmp_path: Path) -> None:
    write(tmp_path, "util.py", "def go():\n    pass\n")
    write(tmp_path, "a.py", "import util\n\ndef f():\n    util = make()\n    util.go()\n")
    assert not any(
        e.dst.endswith("util.go") for e in run(tmp_path).edges if e.kind is EdgeKind.CALLS
    )


def test_module_level_bindings_are_recorded_under_the_empty_function() -> None:
    src = "r: Repo = make()\nfor x in xs: pass\n"
    assert locals_of(src) == {("", "r", "Repo"), ("", "x", "")}


def test_a_decorated_and_async_function_are_read() -> None:
    src = "@app.get('/')\nasync def f(r: Repo = Depends(g)): pass"
    assert locals_of(src) == {("src.a.f", "r", "Repo")}


def test_an_externally_imported_parameter_type_stays_external(tmp_path: Path) -> None:
    write(tmp_path, "pyproject.toml", '[project]\ndependencies = ["requests"]\n')
    write(tmp_path, "models.py", "class Session:\n    def get(self):\n        pass\n")
    write(
        tmp_path,
        "a.py",
        "from requests import Session\n\ndef f(s: Session):\n    s.get()\n",
    )
    calls = [e for e in run(tmp_path).edges if e.kind is EdgeKind.CALLS and e.src.endswith("f")]
    assert not any(e.dst.endswith("Session.get") for e in calls)


def test_nonlocal_rebinding_hides_the_outer_typed_name(tmp_path: Path) -> None:
    body = (
        "def outer(r: Repo):\n"
        "    def inner():\n"
        "        nonlocal r\n"
        "        r = make()\n"
        "    inner()\n"
        "    r.save()\n"
    )
    assert not saves_repo(typed_calls(tmp_path, body))


@pytest.mark.parametrize(
    "stmt",
    [
        "import json as r",
        "import r",
        "import r.sub",
        "from other import Other as r",
        "from other import r",
    ],
)
def test_an_import_in_a_function_binds_its_name_untyped(tmp_path: Path, stmt: str) -> None:
    for body in (
        f"def f(r: Repo):\n    {stmt}\n    r.save()\n",
        f"def f(r: Repo):\n    def g():\n        {stmt}\n        r.save()\n",
        f"def f(r: Repo):\n    try:\n        {stmt}\n    except ImportError:\n        pass\n    r.save()\n",
    ):
        assert not saves_repo(typed_calls(tmp_path, body))


def test_a_class_body_assignment_hides_the_outer_typed_name(tmp_path: Path) -> None:
    body = "def f(r: Repo):\n    class C:\n        r = make()\n        r.save()\n"
    assert not saves_repo(typed_calls(tmp_path, body))
    assert not saves_repo(
        typed_calls(tmp_path, "r: Repo = Repo()\nclass C:\n    r = make()\n    r.save()\n")
    )


def test_a_class_body_assignment_does_not_hide_the_name_outside_the_class(
    tmp_path: Path,
) -> None:
    body = "def f(r: Repo):\n    class C:\n        r = make()\n    r.save()\n"
    assert saves_repo(typed_calls(tmp_path, body))


def test_a_type_alias_statement_binds_its_name_untyped(tmp_path: Path) -> None:
    body = "def f(r: Repo):\n    type r = int\n    r.save()\n"
    assert not saves_repo(typed_calls(tmp_path, body))
