"""Rust extraction: items, impl blocks, use and mod, attributes and calls."""

from __future__ import annotations

from pathlib import Path

from svarupa.detect import detect
from svarupa.extract import declared_dependencies, extract
from svarupa.extract.base import CallShape, FileFacts
from svarupa.extract.packs import extractor
from svarupa.model import EdgeKind


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
