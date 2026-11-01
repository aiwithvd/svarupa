"""Rust extraction: items, impl blocks, use and mod, attributes and calls."""

from __future__ import annotations

from pathlib import Path

import pytest

from svarupa.detect import detect
from svarupa.extract import declared_dependencies, extract
from svarupa.extract.base import CallShape, FileFacts
from svarupa.extract.packs import extractor
from svarupa.model import EdgeKind, Resolution


def parse(src: str, path: str = "src/shop.rs") -> FileFacts:
    return extractor("rust").parse(path, src.encode("utf8"))


def write(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf8")


def calls_of(root: Path) -> set[tuple[str, str]]:
    scan = detect(root)
    graph = extract(scan, declared_dependencies(scan))
    return {(e.src, e.dst) for e in graph.edges if e.kind is EdgeKind.CALLS}


ITEMS = (
    "pub struct Shop;\n"
    "enum Kind { A }\n"
    "pub trait Store { fn save(&self); }\n"
    "impl Store for Shop { fn save(&self) {} }\n"
)


def test_items_and_the_method_of_an_impl() -> None:
    facts = parse(ITEMS, "src/lib.rs")
    got = [(s.qualified_name, s.kind, s.enclosing_class) for s in facts.symbols]
    assert got == [
        ("Shop", "class", None),
        ("Kind", "class", None),
        ("Store", "interface", None),
        ("Store.save", "method", "Store"),
        ("Shop.save", "method", "Shop"),
    ]


def test_an_impl_trait_is_a_base_of_the_type_for_call_resolution(tmp_path: Path) -> None:
    write(
        tmp_path,
        "src/shop.rs",
        "pub struct Shop;\npub trait Store { fn describe(&self) {} }\n"
        "impl Store for Shop { fn save(&self) { self.describe(); } }\n",
    )
    assert ("src/shop.rs#shop.Shop.save", "src/shop.rs#shop.Store.describe") in calls_of(
        tmp_path
    )


def test_an_impl_in_another_file_still_names_the_method() -> None:
    facts = parse("impl Shop { pub fn save(&self) {} }\n", "src/lib.rs")
    assert [(s.qualified_name, s.enclosing_class) for s in facts.symbols] == [
        ("Shop.save", "Shop")
    ]


def test_an_impl_in_another_file_joins_the_type_and_its_trait(tmp_path: Path) -> None:
    write(
        tmp_path,
        "src/shop.rs",
        "pub struct Shop;\npub trait Store { fn describe(&self) {} }\n",
    )
    write(
        tmp_path,
        "src/imp.rs",
        "impl Store for Shop { fn save(&self) { self.describe(); self.load(); } }\n"
        "impl Shop { fn load(&self) {} }\n",
    )
    found = calls_of(tmp_path)
    assert ("src/imp.rs#imp.Shop.save", "src/shop.rs#shop.Store.describe") in found
    assert ("src/imp.rs#imp.Shop.save", "src/imp.rs#imp.Shop.load") in found


def test_a_field_call_resolves_through_the_struct_in_another_file(tmp_path: Path) -> None:
    write(tmp_path, "src/shop.rs", "pub struct Shop { repo: Repo }\n")
    write(tmp_path, "src/repo.rs", "pub struct Repo;\nimpl Repo { pub fn find(&self) {} }\n")
    write(tmp_path, "src/imp.rs", "impl Shop { fn run(&self) { self.repo.find(1); } }\n")
    assert ("src/imp.rs#imp.Shop.run", "src/repo.rs#repo.Repo.find") in calls_of(tmp_path)


def test_a_braced_use_lists_each_name_and_alias() -> None:
    facts = parse("use crate::db::{Repo, Order as O};\n")
    (imp,) = facts.imports
    assert imp.specifier == "crate.db"
    assert imp.names == ("Repo", "Order")
    assert dict(imp.alias_of) == {"O": "Order"}


def test_a_plain_use_is_the_full_dotted_path() -> None:
    facts = parse("use std::collections::HashMap;\nuse foo;\nuse a::b as c;\nuse a::*;\n")
    assert [i.specifier for i in facts.imports] == [
        "std.collections.HashMap",
        "foo",
        "a",
        "a",
    ]
    assert dict(facts.imports[2].alias_of) == {"c": "b"}
    assert facts.imports[3].is_star


def test_nested_use_lists_and_relative_roots() -> None:
    facts = parse("use a::{self, b::{C, D}};\nuse super::x;\nuse self::y;\n")
    got = [(i.specifier, i.names, i.is_relative) for i in facts.imports]
    assert got == [
        ("a", (), False),
        ("a.b", ("C", "D"), False),
        ("super.x", (), True),
        ("self.y", (), True),
    ]


def test_self_beside_names_still_imports_the_module() -> None:
    facts = parse("use crate::model::base::{self, Repo, Order as O};\n")
    got = [(i.specifier, i.names, dict(i.alias_of)) for i in facts.imports]
    assert got == [
        ("crate.model.base", (), {}),
        ("crate.model.base", ("Repo", "Order"), {"O": "Order"}),
    ]


def test_a_module_imported_with_self_resolves_its_calls(tmp_path: Path) -> None:
    write(tmp_path, "src/lib.rs", "mod base;\nmod user;\n")
    write(tmp_path, "src/base.rs", "pub struct Repo;\npub fn create() {}\n")
    write(
        tmp_path,
        "src/user.rs",
        "use crate::base::{self, Repo};\nfn run(_r: Repo) { base::create(); }\n",
    )
    assert ("src/user.rs#user.run", "src/base.rs#base.create") in calls_of(tmp_path)


def test_a_mod_without_a_body_imports_the_child_module() -> None:
    facts = parse("mod handlers;\n", "src/lib.rs")
    (imp,) = facts.imports
    assert imp.specifier == "self.handlers"
    assert facts.symbols == ()


def test_an_inline_mod_qualifies_its_items() -> None:
    facts = parse("mod api { pub fn f() {} }\nfn g() {}\n", "src/lib.rs")
    assert [s.qualified_name for s in facts.symbols] == ["api.f", "g"]
    facts = parse("mod api { pub fn f() {} }\n", "src/shop.rs")
    assert [s.qualified_name for s in facts.symbols] == ["shop.api.f"]


def test_the_module_path_comes_from_the_file() -> None:
    names = {
        "src/lib.rs": "f",
        "src/main.rs": "f",
        "src/a/b.rs": "a.b.f",
        "src/a/mod.rs": "a.f",
        "crates/shop-core/src/lib.rs": "shop_core.f",
        "crates/shop-core/src/db/mod.rs": "shop_core.db.f",
        "crates/shop-api/src/routes.rs": "shop_api.routes.f",
        "build.rs": "build.f",
    }
    for path, want in names.items():
        assert parse("fn f() {}\n", path).symbols[0].qualified_name == want, path


def test_attributes_become_decorators_on_the_next_item() -> None:
    facts = parse(
        '#[derive(Debug)]\n#[get("/x")]\nstruct S;\n\n#[tokio::main]\nasync fn run() {}\n\nfn plain() {}\n'
    )
    by = {s.name: s.decorators for s in facts.symbols}
    assert [(d.name, d.arg, d.arg_dynamic) for d in by["S"]] == [
        ("derive", None, True),
        ("get", "/x", False),
    ]
    assert [d.name for d in by["run"]] == ["main"]
    assert by["run"][0].evidence.start_line == 5
    assert by["plain"] == ()


def test_attributes_on_methods_and_trait_members() -> None:
    facts = parse(
        "trait T {\n    #[must_use]\n    fn a(&self);\n}\nimpl S {\n    #[inline]\n    fn b(&self) {}\n}\n"
    )
    by = {s.name: [d.name for d in s.decorators] for s in facts.symbols}
    assert by == {"T": [], "a": ["must_use"], "b": ["inline"]}


CALLS = (
    "impl Shop {\n"
    "    fn run(&self) {\n"
    "        self.repo.find(1);\n"
    "        self.save();\n"
    "        Shop::new();\n"
    "        helper();\n"
    "        x.save();\n"
    "        a.b.c();\n"
    '        println!("hi {}", format!("a"));\n'
    "        parse::<u8>();\n"
    "    }\n"
    "}\n"
)


def test_call_shapes_and_evidence() -> None:
    facts = parse(CALLS)
    got = [(c.name, c.shape, c.receiver, c.evidence.start_line) for c in facts.calls]
    assert got == [
        ("find", CallShape.SELF_FIELD, "repo", 3),
        ("save", CallShape.SELF, "self", 4),
        ("new", CallShape.QUALIFIED, "Shop", 5),
        ("helper", CallShape.BARE, None, 6),
        ("save", CallShape.QUALIFIED, "x", 7),
        ("c", CallShape.MEMBER, "a.b", 8),
        ("parse", CallShape.BARE, None, 10),
    ]
    assert {c.enclosing_class for c in facts.calls} == {"Shop"}
    assert {c.enclosing for c in facts.calls} == {"shop.Shop.run"}


def test_macros_and_variant_constructors_are_not_calls() -> None:
    facts = parse('fn f() { println!("a"); let v = Some(1); Ok(()); }\n')
    assert facts.calls == ()


def test_exported_means_pub_and_trait_methods() -> None:
    facts = parse(
        "pub struct A;\nstruct B;\npub fn f() {}\nfn g() {}\n"
        "pub trait T { fn t(&self); }\n"
        "impl A { pub fn m(&self) {} fn p(&self) {} }\n"
        "impl T for A { fn t(&self) {} }\n"
    )
    assert {s.qualified_name: s.exported for s in facts.symbols} == {
        "shop.A": True,
        "shop.B": False,
        "shop.f": True,
        "shop.g": False,
        "shop.T": True,
        "shop.T.t": True,
        "shop.A.m": True,
        "shop.A.p": False,
        "shop.A.t": True,
    }


def test_struct_fields_name_their_type() -> None:
    facts = parse("struct S { repo: Repo, shared: Arc<Mutex<Db>>, r: &'static Cfg, n: u8 }\n")
    got = [(f.owner, f.field, f.type_name) for f in facts.fields]
    assert got == [("S", "repo", "Repo"), ("S", "shared", "Db"), ("S", "r", "Cfg")]


def test_a_syntax_error_is_reported_and_the_rest_is_extracted() -> None:
    facts = parse("fn ok() {}\nfn broken( {\nstruct S;\n")
    assert [d.code for d in facts.diagnostics] == ["SVA-X-001"]
    assert "ok" in {s.name for s in facts.symbols}


def test_types_at_the_crate_root_resolve(tmp_path: Path) -> None:
    for root in ("lib", "main"):
        d = tmp_path / root
        write(
            d,
            f"src/{root}.rs",
            "pub struct Shop { repo: Repo }\n"
            "impl Shop {\n    fn a(&self) {}\n    fn new() -> Shop { Shop { repo: Repo } }\n"
            "    fn run(&self) { self.a(); self.repo.find(); }\n}\n"
            "fn make() { Shop::new(); }\n",
        )
        write(d, "src/repo.rs", "pub struct Repo;\nimpl Repo { pub fn find(&self) {} }\n")
        write(d, "src/imp.rs", "impl Shop { fn more(&self) { self.a(); self.repo.find(); } }\n")
        found = calls_of(d)
        top = f"src/{root}.rs"
        assert (f"{top}#Shop.run", f"{top}#Shop.a") in found, root
        assert (f"{top}#Shop.run", "src/repo.rs#repo.Repo.find") in found, root
        assert (f"{top}#make", f"{top}#Shop.new") in found, root
        assert ("src/imp.rs#imp.Shop.more", f"{top}#Shop.a") in found, root
        assert ("src/imp.rs#imp.Shop.more", "src/repo.rs#repo.Repo.find") in found, root


def test_an_impl_for_a_type_nobody_names_owns_no_methods() -> None:
    facts = parse("impl Store for [Shop] { fn save(&self) {} }\nfn f() {}\n")
    assert [s.qualified_name for s in facts.symbols] == ["shop.f"]


def test_self_as_alias_names_the_module() -> None:
    facts = parse("use a::b::{self as z};\n")
    (imp,) = facts.imports
    assert (imp.specifier, imp.names, dict(imp.alias_of)) == ("a", ("b",), {"z": "b"})


def test_an_impl_for_an_imported_external_type_joins_no_repo_struct(tmp_path: Path) -> None:
    write(
        tmp_path,
        "src/error.rs",
        "pub struct Error;\nimpl Error { fn mine(&self) { self.go(); } }\n",
    )
    write(
        tmp_path, "src/imp.rs", "use anyhow::Error;\nimpl Wrap for Error { fn go(&self) {} }\n"
    )
    write(tmp_path, "Cargo.toml", '[package]\nname = "app"\n[dependencies]\nanyhow = "1"\n')
    scan = detect(tmp_path)
    graph = extract(scan, declared_dependencies(scan))
    calls = {(e.src, e.dst) for e in graph.edges if e.kind is EdgeKind.CALLS}
    assert not [c for c in calls if c[1].endswith("Error.go")]


def run(root: Path):
    scan = detect(root)
    return extract(scan, declared_dependencies(scan))


def imports_of(root: Path) -> set[tuple[str, str]]:
    return {(e.src, e.dst) for e in run(root).edges if e.kind is EdgeKind.IMPORTS}


def test_extern_crate_keeps_its_alias() -> None:
    plain = parse("extern crate foo;\n")
    (imp,) = plain.imports
    assert (imp.specifier, imp.names, dict(imp.alias_of)) == ("foo", (), {})
    aliased = parse("extern crate foo as bar;\n")
    (imp,) = aliased.imports
    assert (imp.specifier, dict(imp.alias_of), imp.evidence.start_line) == (
        "foo",
        {"bar": "foo"},
        1,
    )
    assert not parse("extern crate self as me;\n").imports


def cargo_deps(text: str, tmp_path: Path) -> frozenset[str]:
    write(tmp_path, "Cargo.toml", text)
    return declared_dependencies(detect(tmp_path))


def test_cargo_dependency_tables_are_declared(tmp_path: Path) -> None:
    got = cargo_deps(
        '[package]\nname = "a"\n[dependencies]\nserde-json = "1"\n'
        '[dev-dependencies]\npretty_assertions = "1"\n'
        '[build-dependencies]\ncc = "1"\n'
        '[workspace.dependencies]\ntokio = { version = "1" }\n'
        "[target.'cfg(unix)'.dependencies]\nlibc = \"0\"\n"
        '[dependencies.rand]\nversion = "0.8"\n'
        '[dependencies.old_name]\npackage = "new-name"\nversion = "1"\n',
        tmp_path,
    )
    assert {"serde_json", "pretty_assertions", "cc", "tokio", "libc", "rand"} <= got
    assert {"old_name", "new_name"} <= got
    assert "a" not in got


def test_use_paths_resolve_to_module_files(tmp_path: Path) -> None:
    write(tmp_path, "Cargo.toml", '[package]\nname = "shop"\n[dependencies]\nserde = "1"\n')
    write(tmp_path, "src/lib.rs", "mod db;\nmod handlers;\n")
    write(tmp_path, "src/db.rs", "pub struct Repo;\nimpl Repo { pub fn find(&self) {} }\n")
    write(
        tmp_path, "src/handlers/mod.rs", "mod orders;\nuse super::db::Repo;\npub struct Item;\n"
    )
    write(
        tmp_path,
        "src/handlers/orders.rs",
        "use crate::db::Repo;\nuse serde::Serialize;\nuse ghost::Thing;\nuse self::x::Y;\n"
        "use super::Item;\nuse std::fmt;\n",
    )
    found = imports_of(tmp_path)
    assert ("src/lib.rs", "src/db.rs") in found
    assert ("src/lib.rs", "src/handlers/mod.rs") in found
    assert ("src/handlers/mod.rs", "src/handlers/orders.rs") in found
    assert ("src/handlers/mod.rs", "src/db.rs") in found
    assert ("src/handlers/orders.rs", "src/db.rs") in found
    assert ("src/handlers/orders.rs", "src/handlers/mod.rs") in found
    card = run(tmp_path).scorecard
    assert card.get("rust", "imports", Resolution.EXTERNAL) == 2  # serde, std
    assert card.get("rust", "imports", Resolution.UNRESOLVED) == 2  # ghost, self::x::Y


def test_a_workspace_member_import_goes_to_the_other_crate(tmp_path: Path) -> None:
    write(tmp_path, "Cargo.toml", '[workspace]\nmembers = ["crates/*"]\n')
    write(
        tmp_path,
        "crates/shop-core/Cargo.toml",
        '[package]\nname = "shop-core"\n[dependencies]\nanyhow = "1"\n',
    )
    write(
        tmp_path,
        "crates/shop-api/Cargo.toml",
        '[package]\nname = "shop-api"\n[dependencies]\nshop-core = { path = "../shop-core" }\n'
        'serde = { version = "1", package = "serde" }\n',
    )
    write(tmp_path, "crates/shop-core/src/lib.rs", "pub mod model;\n")
    write(
        tmp_path,
        "crates/shop-core/src/model.rs",
        "pub struct Order;\nimpl Order { pub fn total(&self) {} }\n",
    )
    write(
        tmp_path,
        "crates/shop-api/src/main.rs",
        "use shop_core::model::Order;\nuse serde::Serialize;\n"
        "pub struct Api { order: Order }\n"
        "impl Api { fn go(&self) { self.order.total(); Order::total(); } }\n",
    )
    assert (
        "crates/shop-api/src/main.rs",
        "crates/shop-core/src/model.rs",
    ) in imports_of(tmp_path)
    card = run(tmp_path).scorecard
    assert card.get("rust", "imports", Resolution.EXTERNAL) == 1
    assert card.get("rust", "imports", Resolution.UNRESOLVED) == 0
    assert (
        "crates/shop-api/src/main.rs#shop_api.Api.go",
        "crates/shop-core/src/model.rs#shop_core.model.Order.total",
    ) in calls_of(tmp_path)


def test_a_path_dependency_under_another_name_is_not_external(tmp_path: Path) -> None:
    write(tmp_path, "core/Cargo.toml", '[package]\nname = "core-lib"\n')
    write(tmp_path, "core/src/lib.rs", "pub fn f() {}\n")
    write(
        tmp_path,
        "app/Cargo.toml",
        '[package]\nname = "app"\n[dependencies]\nshared = { path = "../core", package = "core-lib" }\n',
    )
    write(tmp_path, "app/src/main.rs", "use shared::f;\nfn main() { f(); }\n")
    assert ("app/src/main.rs", "core/src/lib.rs") in imports_of(tmp_path)


def test_module_and_type_calls_resolve(tmp_path: Path) -> None:
    write(tmp_path, "src/lib.rs", "mod db;\nmod util;\nmod other;\n")
    write(
        tmp_path,
        "src/db.rs",
        "pub struct Repo;\nimpl Repo { pub fn new() -> Repo { Repo } pub fn find(&self) {} }\n"
        "pub fn load() {}\n",
    )
    write(
        tmp_path,
        "src/other.rs",
        "pub struct Cache;\nimpl Cache { pub fn new() -> Cache { Cache } }\npub fn load() {}\n",
    )
    write(tmp_path, "src/util.rs", "pub fn helper() {}\n")
    write(
        tmp_path,
        "src/app.rs",
        "use crate::db;\nuse crate::db::Repo;\nuse crate::util::helper;\nuse crate::other::*;\n"
        "fn run() { db::load(); Repo::new(); helper(); load(); Cache::new(); }\n",
    )
    found = calls_of(tmp_path)
    assert ("src/app.rs#app.run", "src/db.rs#db.load") in found
    assert ("src/app.rs#app.run", "src/db.rs#db.Repo.new") in found
    assert ("src/app.rs#app.run", "src/util.rs#util.helper") in found
    assert ("src/app.rs#app.run", "src/other.rs#other.load") in found
    assert ("src/app.rs#app.run", "src/other.rs#other.Cache.new") in found
    assert not [
        c for c in found if c[1] == "src/db.rs#db.load" and c[0] != "src/app.rs#app.run"
    ]


def test_a_bare_call_to_an_unimported_name_is_not_guessed(tmp_path: Path) -> None:
    write(tmp_path, "src/util.rs", "pub fn helper() {}\n")
    write(tmp_path, "src/app.rs", "fn run() { helper(); }\n")
    assert not [c for c in calls_of(tmp_path) if c[1].endswith("helper")]


@pytest.mark.parametrize(
    ("use", "expected"),
    [
        ("use crate::nope::X;", set()),
        ("use crate::nope::*;", set()),
        ("use crate::a::nope::Y::Z;", set()),
        ("use crate::db::nope::Y;", set()),
        ("use super::nope::X;", set()),
        ("use super::nope::*;", set()),
        ("use crate::{db::Repo, nope::Q};", {"src/db.rs"}),
        ("use crate::a::Gone;", set()),
        ("use crate::a::Real;", {"src/a/mod.rs"}),
        ("use crate::nope::{X};", set()),
        ("use crate::Kind::{A, B};", {"src/lib.rs"}),
        ("use crate::db::Missing;", set()),
        ("use crate::db::Repo;", {"src/db.rs"}),
        ("use crate::db::*;", {"src/db.rs"}),
        ("use super::Gone;", set()),
        ("use super::Real;", {"src/a/mod.rs"}),
    ],
)
def test_a_bad_path_makes_no_edge(tmp_path: Path, use: str, expected: set[str]) -> None:
    write(tmp_path, "src/lib.rs", "mod a;\nmod db;\npub enum Kind { A, B }\n")
    write(tmp_path, "src/a/mod.rs", "mod b;\npub struct Real;\n")
    write(tmp_path, "src/db.rs", "pub struct Repo;\n")
    write(tmp_path, "src/a/b.rs", use + "\n")
    got = {dst for src, dst in imports_of(tmp_path) if src == "src/a/b.rs"}
    assert got == expected


# ----------------------------------------------------------------------
# final fix pass


def _imports_graph(root: Path):
    scan = detect(root)
    return extract(scan, declared_dependencies(scan))


def _file_edges(root: Path) -> set[tuple[str, str]]:
    graph = _imports_graph(root)
    return {(e.src, e.dst) for e in graph.edges if e.kind is EdgeKind.IMPORTS}


def test_use_super_in_inline_test_mod_is_the_file_itself(tmp_path: Path) -> None:
    write(tmp_path, "Cargo.toml", '[package]\nname = "t"\n')
    write(tmp_path, "src/lib.rs", "mod token;\n")
    write(
        tmp_path,
        "src/token/mod.rs",
        "pub struct Tok;\n\n#[cfg(test)]\nmod tests {\n    use super::*;\n    fn t() {}\n}\n",
    )
    edges = _file_edges(tmp_path)
    assert ("src/token/mod.rs", "src/lib.rs") not in edges
    assert ("src/lib.rs", "src/token/mod.rs") in edges
    graph = _imports_graph(tmp_path)
    assert not [n for n in graph.diagnostics if "cycle" in str(n).lower()]


def test_super_super_from_nested_inline_mod_climbs_one_file(tmp_path: Path) -> None:
    write(tmp_path, "Cargo.toml", '[package]\nname = "t"\n')
    write(tmp_path, "src/lib.rs", "mod a;\npub struct Root;\n")
    write(
        tmp_path,
        "src/a.rs",
        "pub struct A;\nmod outer {\n    mod inner {\n        use super::super::A;\n"
        "        use super::super::super::Root;\n    }\n}\n",
    )
    edges = _file_edges(tmp_path)
    # super::super is a.rs itself (two inline levels); three supers reach lib.rs
    assert ("src/a.rs", "src/lib.rs") in edges
    facts = parse(
        "mod outer { mod inner { use super::super::A; use super::B; use super::super::super::C;"
        " use self::x::Y; } }\n",
        "src/a.rs",
    )
    assert [i.specifier for i in facts.imports] == [
        "self.A",
        "self.outer.B",
        "super.C",
        "self.outer.inner.x.Y",
    ]


def test_self_in_inline_mod_names_the_inline_path() -> None:
    facts = parse("mod m { use self::x::Y; use super::Z; }\n", "src/a.rs")
    assert [i.specifier for i in facts.imports] == ["self.m.x.Y", "self.Z"]


def test_unresolved_member_receiver_is_one_line() -> None:
    src = 'fn f(p: P) {\n    sqlx::query!(r#"\n  SELECT a\n  FROM b\n"#).fetch_one(&p);\n}\n'
    facts = parse(src)
    for c in facts.calls:
        assert c.receiver is None or "\n" not in c.receiver
        assert c.receiver is None or "(" not in c.receiver


# ----------------------------------------------------------------------
# pub use re-exports


def test_only_pub_and_pub_crate_use_are_reexports() -> None:
    src = (
        "pub use a::X;\npub(crate) use b::Y;\nuse c::Z;\npub(super) use d::W;\n"
        "pub use e::{P, Q as R};\npub use f::*;\n"
    )
    got = [(i.specifier, i.is_reexport) for i in parse(src).imports]
    assert got == [
        ("a.X", True),
        ("b.Y", True),
        ("c.Z", False),
        ("d.W", False),
        ("e", True),
        ("f", True),
    ]


def facade(tmp_path: Path, model: str, caller: str, extra: dict[str, str] | None = None) -> set:
    write(tmp_path, "src/lib.rs", "mod model;\nmod app;\n")
    write(tmp_path, "src/model/mod.rs", model)
    write(tmp_path, "src/model/user.rs", "pub fn create() {}\npub fn list() {}\n")
    write(tmp_path, "src/app.rs", caller)
    for rel, text in (extra or {}).items():
        write(tmp_path, rel, text)
    return calls_of(tmp_path)


RUN = "src/app.rs#app.run"
CREATE = "src/model/user.rs#model.user.create"


def test_a_named_facade_resolves_to_the_definition(tmp_path: Path) -> None:
    found = facade(
        tmp_path,
        "mod user;\npub use user::{create, list};\n",
        "use crate::model::create;\nuse crate::model;\nfn run() { create(); model::list(); }\n",
    )
    assert (RUN, CREATE) in found
    assert (RUN, "src/model/user.rs#model.user.list") in found


def test_a_single_path_facade_and_pub_crate(tmp_path: Path) -> None:
    found = facade(
        tmp_path,
        "mod user;\npub(crate) use user::create;\n",
        "use crate::model::create;\nfn run() { create(); }\n",
    )
    assert (RUN, CREATE) in found


def test_a_renamed_facade_resolves_to_the_definition(tmp_path: Path) -> None:
    found = facade(
        tmp_path,
        "mod user;\npub use user::create as make;\n",
        "use crate::model::make;\nfn run() { make(); }\n",
    )
    assert (RUN, CREATE) in found


def test_the_old_name_is_not_exported_by_a_rename(tmp_path: Path) -> None:
    found = facade(
        tmp_path,
        "mod user;\npub use user::create as make;\n",
        "use crate::model::create;\nfn run() { create(); }\n",
    )
    assert not [c for c in found if c[0] == RUN]


def test_a_glob_facade_resolves_to_the_definition(tmp_path: Path) -> None:
    found = facade(
        tmp_path,
        "mod user;\npub use user::*;\n",
        "use crate::model::create;\nuse crate::model::*;\nfn run() { create(); list(); }\n",
    )
    assert (RUN, CREATE) in found
    assert (RUN, "src/model/user.rs#model.user.list") in found


def test_a_two_level_facade_chain_resolves(tmp_path: Path) -> None:
    found = facade(
        tmp_path,
        "mod user;\npub use user::*;\n",
        "use crate::create;\nfn run() { create(); }\n",
        {"src/lib.rs": "mod model;\nmod app;\npub use model::create;\n"},
    )
    assert (RUN, CREATE) in found


def test_an_external_reexport_stays_external(tmp_path: Path) -> None:
    write(tmp_path, "Cargo.toml", '[package]\nname = "shop"\n[dependencies]\nserde = "1"\n')
    found = facade(
        tmp_path,
        "pub use serde::Serialize;\n",
        "use crate::model::Serialize;\nfn run() { Serialize(); }\n",
        {"src/other.rs": "pub fn Serialize() {}\n"},
    )
    assert not [c for c in found if c[0] == RUN]


def test_a_reexport_cycle_terminates_and_still_resolves(tmp_path: Path) -> None:
    found = facade(
        tmp_path,
        "pub use crate::other::*;\n",
        "use crate::model::create;\nuse crate::model::missing;\n"
        "fn run() { create(); missing(); }\n",
        {"src/other.rs": "pub use crate::model::*;\npub use crate::model::user::*;\n"},
    )
    assert not [c for c in found if c[0] == RUN and c[1].endswith("missing")]


def test_a_cycle_between_two_globs_finds_the_real_definition(tmp_path: Path) -> None:
    write(tmp_path, "src/lib.rs", "mod a;\nmod b;\nmod app;\n")
    write(tmp_path, "src/a.rs", "pub use crate::b::*;\n")
    write(tmp_path, "src/b.rs", "pub use crate::a::*;\npub fn real() {}\n")
    write(
        tmp_path,
        "src/app.rs",
        "use crate::a::real;\nuse crate::a::nope;\nfn run() { real(); nope(); }\n",
    )
    found = calls_of(tmp_path)
    assert (RUN, "src/b.rs#b.real") in found
    assert not [c for c in found if c[1].endswith("nope")]


def test_a_plain_use_does_not_reexport(tmp_path: Path) -> None:
    found = facade(
        tmp_path,
        "mod user;\nuse user::create;\npub(super) use user::list;\n",
        "use crate::model::create;\nuse crate::model::list;\nfn run() { create(); list(); }\n",
    )
    assert not [c for c in found if c[0] == RUN]
