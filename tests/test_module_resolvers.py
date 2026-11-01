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


from svarupa.extract.packs.modules import (  # noqa: E402
    CSharpNamespaces,
    GoModules,
    ModuleContext,
)

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


def _cs(path: str, namespaces: tuple[str, ...], *types: str) -> FileFacts:
    ev = Evidence(file=path, start_line=1, end_line=1)
    syms = tuple(SymbolRef(t.rsplit(".", 1)[-1], t, "class", ev) for t in types)
    return FileFacts(
        path=path,
        lang="csharp",
        namespace=namespaces[0] if namespaces else "",
        namespaces=namespaces,
        symbols=syms,
    )


CSHARP = (
    _cs("Data/OrderRepo.cs", ("Shop.Data",), "Shop.Data.OrderRepo"),
    _cs("Data/Db.cs", ("Shop.Data",), "Shop.Data.Db"),
    _cs("Data/Sql/Raw.cs", ("Shop.Data.Sql",), "Shop.Data.Sql.Raw"),
    _cs("Misc/Two.cs", ("Shop.Misc", "Shop.Data"), "Shop.Misc.A", "Shop.Data.Extra"),
)


def csharp() -> CSharpNamespaces:
    return CSharpNamespaces(
        ModuleContext(files=frozenset(f.path for f in CSHARP), facts=CSHARP, deps=frozenset())
    )


def test_a_namespace_names_every_file_declaring_it() -> None:
    assert csharp().targets("Shop.Data", "x") == (
        "Data/Db.cs",
        "Data/OrderRepo.cs",
        "Misc/Two.cs",
    )


def test_a_file_belongs_to_every_namespace_it_declares() -> None:
    assert csharp().targets("Shop.Misc", "x") == ("Misc/Two.cs",)


def test_a_nested_namespace_is_not_part_of_its_parent() -> None:
    r = csharp()
    assert "Data/Sql/Raw.cs" not in r.targets("Shop.Data", "x")
    assert r.targets("Shop.Data.Sql", "x") == ("Data/Sql/Raw.cs",)


def test_a_type_name_narrows_to_the_files_declaring_it() -> None:
    r = csharp()
    assert r.targets("Shop.Data.Db", "x") == ("Data/Db.cs",)
    assert r.targets("Shop.Data.Extra", "x") == ("Misc/Two.cs",)


def test_an_unknown_member_of_a_known_namespace_is_unresolved() -> None:
    assert csharp().targets("Shop.Data.Gone", "x") == ()


def test_an_undeclared_namespace_resolves_to_nothing() -> None:
    assert csharp().targets("Shop.Nowhere", "x") == ()


def test_csharp_external_is_the_framework_only() -> None:
    r = csharp()
    assert r.is_external("System")
    assert r.is_external("System.Text")
    assert r.is_external("Microsoft.Extensions.Logging")
    assert not r.is_external("Systemic")
    assert not r.is_external("Shop.Nowhere")
    assert not r.is_external("Shop.Data")


def csharp_with(*deps: str) -> CSharpNamespaces:
    return CSharpNamespaces(
        ModuleContext(
            files=frozenset(f.path for f in CSHARP), facts=CSHARP, deps=frozenset(deps)
        )
    )


def test_csharp_nuget_package_namespaces_are_external() -> None:
    r = csharp_with("Newtonsoft.Json", "Dapper", "Ardalis.Specification.EntityFrameworkCore")
    assert r.is_external("Newtonsoft.Json")
    assert r.is_external("Newtonsoft.Json.Linq")
    assert r.is_external("Dapper")
    # Namespace shorter than the package id: shares the first two segments.
    assert r.is_external("Ardalis.Specification")
    assert r.is_external("Ardalis.Specification.Filters")
    assert not r.is_external("Newtonsoft")
    assert not r.is_external("Dappery")
    assert not r.is_external("Ardalis.Result")


def test_csharp_declared_namespace_wins_over_a_package() -> None:
    r = csharp_with("Shop.Data.Tools", "Shop.Misc")
    assert not r.is_external("Shop.Data")
    assert not r.is_external("Shop.Misc")
    assert r.is_external("Shop.Other.Thing") is False


def test_csharp_package_ids_match_case_insensitively() -> None:
    assert csharp_with("xunit").is_external("Xunit.Abstractions")


from svarupa.extract.packs.modules import RustCrates  # noqa: E402

RUST_FILES = frozenset(
    {
        "Cargo.toml",
        "src/lib.rs",
        "src/main.rs",
        "src/db.rs",
        "src/handlers/mod.rs",
        "src/handlers/orders.rs",
        "src/handlers/deep/leaf.rs",
        "src/bin/tool.rs",
        "src/bin/other/main.rs",
        "src/bin/other/util.rs",
        "crates/shop-core/src/lib.rs",
        "crates/shop-core/src/model.rs",
        "crates/shop-api/src/main.rs",
        "tests/it.rs",
        "lone/src/main.rs",
    }
)


RUST_DECLARES = {
    "src/lib.rs": ("Thing", "Kind"),
    "src/db.rs": ("Repo",),
    "src/handlers/mod.rs": ("Thing",),
    "src/handlers/orders.rs": ("make", "Item"),
    "src/bin/other/main.rs": ("Thing",),
    "crates/shop-core/src/lib.rs": ("Thing",),
    "crates/shop-core/src/model.rs": ("Order",),
}


def _rust_facts() -> tuple[FileFacts, ...]:
    return tuple(
        FileFacts(
            path=path,
            lang="rust",
            symbols=tuple(SymbolRef(n, n, "class", ev(path)) for n in names),
        )
        for path, names in RUST_DECLARES.items()
    )


def rust(
    deps: frozenset[str] = frozenset(),
    crates: tuple[tuple[str, str], ...] = (
        ("shop", ""),
        ("shop_core", "crates/shop-core"),
        ("shop_api", "crates/shop-api"),
    ),
) -> RustCrates:
    return RustCrates(
        ModuleContext(files=RUST_FILES, facts=_rust_facts(), deps=deps, rust_crates=crates)
    )


def test_rust_crate_paths_find_the_longest_module_file() -> None:
    r = rust()
    assert r.targets("crate.db", "src/lib.rs") == ("src/db.rs",)
    assert r.targets("crate.db.Repo", "src/handlers/orders.rs") == ("src/db.rs",)
    assert r.targets("crate.handlers.Thing", "src/db.rs") == ("src/handlers/mod.rs",)
    assert r.targets("crate.handlers.orders.make", "src/db.rs") == ("src/handlers/orders.rs",)
    assert r.targets("crate.handlers.deep.leaf", "src/db.rs") == ("src/handlers/deep/leaf.rs",)
    assert r.targets("crate.Thing", "src/db.rs") == ("src/lib.rs",)


def test_rust_self_and_super_are_relative_to_the_callers_module() -> None:
    r = rust()
    assert r.targets("self.orders", "src/handlers/mod.rs") == ("src/handlers/orders.rs",)
    assert r.targets("super.db", "src/handlers/mod.rs") == ("src/db.rs",)
    assert r.targets("super.super.orders.make", "src/handlers/deep/leaf.rs") == (
        "src/handlers/orders.rs",
    )
    assert r.targets("super.super.super.db", "src/handlers/deep/leaf.rs") == ("src/db.rs",)
    assert r.targets("super.x", "src/lib.rs") == ()
    # `mod handlers;` in lib.rs and in a mod.rs
    assert r.targets("self.handlers", "src/lib.rs") == ("src/handlers/mod.rs",)
    assert r.targets("self.db", "src/main.rs") == ("src/db.rs",)
    # `self.Item` is an item of the caller's own module (a file never imports itself)
    assert r.targets("self.Item", "src/handlers/orders.rs") == ("src/handlers/orders.rs",)


def test_rust_a_mod_name_in_scope_resolves_without_a_prefix() -> None:
    r = rust()
    assert r.targets("db.Repo", "src/lib.rs") == ("src/db.rs",)
    assert r.targets("nope.Repo", "src/lib.rs") == ()
    assert r.targets("nope.deeper.Repo", "src/handlers/mod.rs") == ()


def test_rust_binaries_and_the_lib_of_the_same_crate() -> None:
    r = rust()
    # main.rs is a root of its own; the lib is named by the crate name
    assert r.targets("crate", "src/main.rs") == ("src/main.rs",)
    assert r.targets("crate", "src/db.rs") == ("src/lib.rs",)
    assert r.targets("shop.db.Repo", "src/main.rs") == ("src/db.rs",)
    assert r.targets("shop.Thing", "src/main.rs") == ("src/lib.rs",)
    assert r.targets("crate.util", "src/bin/other/main.rs") == ("src/bin/other/util.rs",)
    assert r.targets("crate.Thing", "src/bin/other/main.rs") == ("src/bin/other/main.rs",)
    assert r.targets("shop.db", "src/bin/tool.rs") == ("src/db.rs",)
    assert r.targets("shop.db", "tests/it.rs") == ("src/db.rs",)


def test_rust_a_binary_only_crate_cannot_be_imported() -> None:
    r = rust(crates=(("lone", "lone"),))
    assert r.targets("lone.x", "src/lib.rs") == ()
    assert not r.is_external("lone.x")


def test_rust_a_workspace_member_resolves_into_its_crate() -> None:
    r = rust()
    assert r.targets("shop_core.model.Order", "crates/shop-api/src/main.rs") == (
        "crates/shop-core/src/model.rs",
    )
    assert r.targets("shop_core.Thing", "crates/shop-api/src/main.rs") == (
        "crates/shop-core/src/lib.rs",
    )
    assert r.targets("crate.model", "crates/shop-core/src/lib.rs") == (
        "crates/shop-core/src/model.rs",
    )


def test_rust_external_is_the_std_family_and_declared_dependencies() -> None:
    r = rust(frozenset({"serde", "tokio_util"}))
    for spec in (
        "std.fmt",
        "core.mem",
        "alloc.vec",
        "serde",
        "serde.Serialize",
        "tokio_util.io",
    ):
        assert r.is_external(spec), spec
    for spec in ("shop_core.model", "shop.db", "crate.db", "self.x", "super.x", "nobody.x"):
        assert not r.is_external(spec), spec


def test_rust_a_member_is_never_external_even_if_a_manifest_lists_it() -> None:
    r = rust(frozenset({"shop_core"}))
    assert not r.is_external("shop_core.model")


def test_rust_a_path_falls_back_at_most_one_segment_past_a_module() -> None:
    r = rust()
    for caller in ("src/lib.rs", "src/handlers/orders.rs", "src/handlers/deep/leaf.rs"):
        assert r.targets("crate.nope.X", caller) == (), caller
        assert r.targets("crate.nope", caller) == (), caller  # lib.rs declares no `nope`
        assert r.targets("crate.Kind", caller) == ("src/lib.rs",), caller
        assert r.targets("crate.db.nope.Y", caller) == (), caller
        assert r.targets("crate.handlers.nope.Y.Z", caller) == (), caller
        assert r.targets("crate.handlers.nope", caller) == (), caller
        assert r.targets("crate.handlers.Thing", caller) == ("src/handlers/mod.rs",), caller
        assert r.targets("crate.db.Gone", caller) == (), caller
    assert r.targets("super.nope.X", "src/handlers/orders.rs") == ()
    assert r.targets("super.db.nope.Y", "src/handlers/orders.rs") == ()
    assert r.targets("self.nope.X", "src/handlers/mod.rs") == ()
    assert r.targets("self.orders.nope.Y", "src/handlers/mod.rs") == ()
    assert r.targets("shop_core.nope.X", "crates/shop-api/src/main.rs") == ()
    assert r.targets("shop_core.model.nope.Y", "crates/shop-api/src/main.rs") == ()


def test_rust_a_glob_needs_its_module_to_exist() -> None:
    r = rust()
    assert r.targets("crate.nope.*", "src/lib.rs") == ()
    assert r.targets("crate.db.*", "src/lib.rs") == ("src/db.rs",)
    assert r.targets("crate.*", "src/db.rs") == ("src/lib.rs",)
    assert r.targets("super.nope.*", "src/handlers/orders.rs") == ()
    assert r.targets("super.*", "src/handlers/orders.rs") == ("src/handlers/mod.rs",)
    assert r.targets("shop_core.nope.*", "crates/shop-api/src/main.rs") == ()


def test_rust_a_repo_crate_named_like_std_shadows_it_only_for_its_users() -> None:
    crates = (("core", "libs/core"), ("app", "app"), ("other", "other"))
    files = frozenset(
        {"libs/core/src/lib.rs", "libs/core/src/fmt.rs", "app/src/main.rs", "other/src/lib.rs"}
    )
    r = RustCrates(
        ModuleContext(
            files=files,
            facts=(),
            deps=frozenset({"core"}),
            rust_crates=crates,
            rust_uses=(("app", "core"),),
        )
    )
    assert r.targets("core.fmt", "app/src/main.rs") == ("libs/core/src/fmt.rs",)
    assert r.targets("core.fmt", "libs/core/src/lib.rs") == ("libs/core/src/fmt.rs",)
    assert r.targets("core.fmt.Debug", "other/src/lib.rs") == ()
    assert r.is_external("core.fmt.Debug")


def test_rust_an_item_fallback_needs_the_file_to_declare_it() -> None:
    r = rust()
    # a brace list `use crate::Kind::{A, B}` has the spec `crate.Kind`
    assert r.targets("crate.Kind", "src/db.rs") == ("src/lib.rs",)
    assert r.targets("crate.nope", "src/db.rs") == ()
    assert r.targets("crate.db.Repo", "src/lib.rs") == ("src/db.rs",)
    assert r.targets("crate.db.Order", "src/lib.rs") == ()
    assert r.targets("shop_core.Order", "crates/shop-api/src/main.rs") == ()
    assert r.targets("self.Item", "src/handlers/orders.rs") == ("src/handlers/orders.rs",)
    assert r.targets("self.Other", "src/handlers/orders.rs") == ()
