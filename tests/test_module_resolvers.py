"""Module resolvers: imports that name a package rather than one file."""

from __future__ import annotations

from svarupa.extract.base import CallShape, CallSite, FileFacts, ImportRef, SymbolRef
from svarupa.extract.resolve import Resolver
from svarupa.model import EdgeKind, Evidence, Resolution


class TwoFiles:
    """A package `pkg` made of two files; `std` is provably external."""

    top_level_only = True
    package_scoped_bare = False
    fields_without_this = False

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


from svarupa.extract.packs.modules import GoModules, ModuleContext  # noqa: E402

GO_FILES = frozenset(
    {
        "go.mod",
        "cmd/api/main.go",
        "internal/orders/service.go",
        "internal/orders/repo.go",
        "internal/orders/service_test.go",
        "tools/gen/gen.go",
    }
)


def go(modules: tuple[tuple[str, str], ...], deps: frozenset[str] = frozenset()) -> GoModules:
    return GoModules(ModuleContext(files=GO_FILES, facts=(), deps=deps, go_modules=modules))


def test_package_imports_skip_test_files() -> None:
    r = go((("github.com/acme/shop", ""),))
    assert r.targets("github.com/acme/shop/internal/orders", "cmd/api/main.go") == (
        "internal/orders/repo.go",
        "internal/orders/service.go",
    )


def test_the_longest_module_path_wins() -> None:
    r = go((("github.com/acme/shop", ""), ("github.com/acme/shop/tools", "tools")))
    assert r.targets("github.com/acme/shop/tools/gen", "x.go") == ("tools/gen/gen.go",)


def test_standard_library_and_required_modules_are_external() -> None:
    r = go((("github.com/acme/shop", ""),), frozenset({"github.com/gin-gonic/gin"}))
    assert r.is_external("net/http")
    assert r.is_external("github.com/gin-gonic/gin")
    assert r.is_external("github.com/gin-gonic/gin/binding")
    assert not r.is_external("github.com/gin-gonic/ginx")
    assert not r.is_external("github.com/other/lib")


def test_a_dotless_module_path_is_never_standard_library() -> None:
    r = go((("shop", ""),))
    assert r.targets("shop/nowhere", "cmd/api/main.go") == ()
    assert not r.is_external("shop/nowhere")
    assert r.is_external("fmt")


from svarupa.extract.packs.modules import JvmPackages  # noqa: E402


def _jfacts(path: str, ns: str) -> FileFacts:
    return FileFacts(path=path, lang="java", namespace=ns)


JAVA = (
    _jfacts("src/main/java/com/acme/shop/Api.java", "com.acme.shop"),
    _jfacts("src/main/java/com/acme/shop/model/Order.java", "com.acme.shop.model"),
    _jfacts("src/main/java/com/acme/shop/model/Line.java", "com.acme.shop.model"),
    _jfacts("src/main/java/com/acme/shop/util/Strings.java", "com.acme.shop.util"),
)


def jvm(deps: frozenset[str] = frozenset()) -> JvmPackages:
    return JvmPackages(
        ModuleContext(files=frozenset(f.path for f in JAVA), facts=JAVA, deps=deps)
    )


def test_a_class_import_names_its_file() -> None:
    assert jvm().targets("com.acme.shop.model.Order", "x") == (
        "src/main/java/com/acme/shop/model/Order.java",
    )


def test_a_package_import_names_every_class_in_it() -> None:
    assert jvm().targets("com.acme.shop.model", "x") == (
        "src/main/java/com/acme/shop/model/Line.java",
        "src/main/java/com/acme/shop/model/Order.java",
    )


def test_a_static_or_nested_import_names_the_class_file() -> None:
    assert jvm().targets("com.acme.shop.util.Strings.trim", "x") == (
        "src/main/java/com/acme/shop/util/Strings.java",
    )


def test_jvm_external_needs_the_jdk_or_a_declared_group() -> None:
    r = jvm(frozenset({"org.springframework"}))
    assert r.is_external("java.util.List")
    assert r.is_external("org.springframework.web.bind.annotation.GetMapping")
    assert not r.is_external("org.springframeworkx.Thing")
    assert not r.is_external("com.acme.missing.Thing")
    assert not r.is_external("com.acme.shop.model")
