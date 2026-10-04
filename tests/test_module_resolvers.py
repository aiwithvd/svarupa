"""Module resolvers: imports that name a package rather than one file."""

from __future__ import annotations

from svarupa.extract.base import CallShape, CallSite, FileFacts, ImportRef, SymbolRef
from svarupa.extract.resolve import Resolver
from svarupa.model import EdgeKind, Evidence, Resolution


class TwoFiles:
    """A package `pkg` made of two files; `std` is provably external."""

    def targets(self, spec: str, _from_file: str, /) -> tuple[str, ...]:
        return ("pkg/a.toy", "pkg/b.toy") if spec == "pkg" else ()

    def is_external(self, spec: str, /) -> bool:
        return spec == "std"


def ev(path: str, line: int = 1) -> Evidence:
    return Evidence(path, line, line)


def _import(spec: str, line: int) -> ImportRef:
    return ImportRef(
        specifier=spec,
        names=(),
        alias_of={spec: spec},
        evidence=ev("app/main.toy", line),
        is_from=True,
    )


def facts() -> list[FileFacts]:
    main = FileFacts(
        path="app/main.toy",
        lang="toy",
        imports=(_import("pkg", 1), _import("std", 2), _import("missing", 3)),
        calls=(
            CallSite("Open", CallShape.QUALIFIED, "pkg", ev("app/main.toy", 4), None, None),
        ),
    )
    a = FileFacts(
        path="pkg/a.toy",
        lang="toy",
        symbols=(SymbolRef("Helper", "pkg.a.Helper", "function", ev("pkg/a.toy")),),
    )
    b = FileFacts(
        path="pkg/b.toy",
        lang="toy",
        symbols=(SymbolRef("Open", "pkg.b.Open", "function", ev("pkg/b.toy")),),
    )
    return [main, a, b]


def run():
    return Resolver(facts(), modules={"toy": TwoFiles()}).run()


def test_a_package_import_points_at_every_file_in_it() -> None:
    edges = sorted((e.src, e.dst) for e in run().edges if e.kind is EdgeKind.IMPORTS)
    assert edges == [("app/main.toy", "pkg/a.toy"), ("app/main.toy", "pkg/b.toy")]


def test_a_package_import_counts_once_in_the_scorecard() -> None:
    card = run().scorecard
    assert card.get("toy", "imports", Resolution.RESOLVED) == 1
    assert card.get("toy", "imports", Resolution.EXTERNAL) == 1
    assert card.get("toy", "imports", Resolution.UNRESOLVED) == 1


def test_a_qualified_call_finds_its_target_in_any_file_of_the_package() -> None:
    calls = [(e.src, e.dst) for e in run().edges if e.kind is EdgeKind.CALLS]
    assert calls == [("app/main.toy", "pkg/b.toy#pkg.b.Open")]


def test_languages_without_a_resolver_are_untouched() -> None:
    r = Resolver(facts(), modules={})
    assert r.resolve_module("pkg", "app/main.toy", 0, "toy") is None
