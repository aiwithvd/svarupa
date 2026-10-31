"""C# extraction: namespaces, using directives, attributes and calls."""

from __future__ import annotations

from svarupa.extract.base import CallShape, FileFacts
from svarupa.extract.packs import extractor

FILE_SCOPED = (
    "namespace Shop.Api;\n\npublic class OrdersController\n{\n    public void Create() {}\n}\n"
)
BLOCK = (
    "namespace Shop.Api\n{\n"
    "    public class OrdersController\n    {\n"
    "        public void Create() {}\n"
    "    }\n"
    "}\n"
)


def parse(src: str, path: str = "Api/OrdersController.cs") -> FileFacts:
    return extractor("csharp").parse(path, src.encode("utf8"))


def test_file_scoped_and_block_namespaces_qualify_the_same() -> None:
    for src in (FILE_SCOPED, BLOCK):
        facts = parse(src)
        names = {s.qualified_name: s for s in facts.symbols}
        assert set(names) == {"Shop.Api.OrdersController", "Shop.Api.OrdersController.Create"}
        assert names["Shop.Api.OrdersController.Create"].kind == "method"
        assert names["Shop.Api.OrdersController"].kind == "class"
        assert facts.namespace == "Shop.Api"


def test_without_a_namespace_the_path_prefix_is_used() -> None:
    facts = parse("class A { void M() {} }\n", "src/A.cs")
    assert [s.qualified_name for s in facts.symbols] == ["src.A.A", "src.A.A.M"]


def test_a_block_namespace_does_not_leak_into_what_follows() -> None:
    facts = parse("namespace X { class A {} }\nclass B {}\n", "b.cs")
    assert [s.qualified_name for s in facts.symbols] == ["X.A", "b.B"]


def test_using_directives() -> None:
    facts = parse(
        "using Shop.Data;\nusing static Shop.Util.Math;\nusing Db = Shop.Data.Db;\nusing System;\n"
    )
    assert [i.specifier for i in facts.imports] == [
        "Shop.Data",
        "Shop.Util.Math",
        "Shop.Data.Db",
        "System",
    ]
    assert facts.imports[0].evidence.start_line == 1


def test_type_kinds_and_bases() -> None:
    facts = parse(
        "class C : ControllerBase, IThing<int> {}\n"
        "interface I : J {}\n"
        "record R(int A);\n"
        "struct S {}\n"
        "enum E { A }\n"
    )
    by = {s.name: s for s in facts.symbols}
    assert {n: s.kind for n, s in by.items()} == {
        "C": "class",
        "I": "interface",
        "R": "class",
        "S": "class",
        "E": "class",
    }
    assert by["C"].bases == ("ControllerBase", "IThing")
    assert by["I"].bases == ("J",)


def test_attributes_become_decorators() -> None:
    facts = parse(
        "[ApiController]\npublic class C\n{\n"
        '    [HttpPost, Authorize("admin")]\n'
        "    public void M() {}\n"
        "}\n"
    )
    by = {s.name: s for s in facts.symbols}
    assert [d.name for d in by["C"].decorators] == ["ApiController"]
    assert [(d.name, d.arg) for d in by["M"].decorators] == [
        ("HttpPost", None),
        ("Authorize", "admin"),
    ]
    assert by["C"].exported and by["M"].exported


def test_calls_in_document_order_with_own_line_evidence() -> None:
    facts = parse(
        "class C\n{\n"
        "    void M(int id)\n    {\n"
        "        _repo.FindAsync(id);\n"
        "        var h = new Helper();\n"
        "        Local();\n"
        "        this.Other();\n"
        "        base.Close();\n"
        "        Util.Run();\n"
        "    }\n}\n"
    )
    got = [(c.name, c.shape, c.receiver, c.evidence.start_line) for c in facts.calls]
    assert got == [
        ("FindAsync", CallShape.QUALIFIED, "_repo", 5),
        ("Helper", CallShape.BARE, None, 6),
        ("Local", CallShape.SELF, "this", 7),
        ("Other", CallShape.SELF, "this", 8),
        ("Close", CallShape.SUPER, "base", 9),
        ("Run", CallShape.QUALIFIED, "Util", 10),
    ]
    assert all(c.enclosing_class == "C" for c in facts.calls)


def test_a_local_function_is_a_function_outside_a_class() -> None:
    facts = parse("void Top() {}\n", "p.cs")
    assert [(s.name, s.kind) for s in facts.symbols] == [("Top", "function")]


def test_a_syntax_error_keeps_the_parseable_part() -> None:
    facts = parse("namespace N;\nclass Good { void M() {} }\nclass Bad { void ( }\n")
    assert [d.code for d in facts.diagnostics] == ["SVA-X-001"]
    assert "N.Good" in {s.qualified_name for s in facts.symbols}


def test_nested_block_namespaces_compose_even_when_equal_to_the_path_prefix() -> None:
    facts = parse("namespace A { namespace B { class X {} } }\n", "A.cs")
    assert [s.qualified_name for s in facts.symbols] == ["A.B.X"]


def test_the_file_namespace_is_the_first_one_declared() -> None:
    facts = parse("namespace A.B { class X {} }\nnamespace C { class Y {} }\n", "f.cs")
    assert facts.namespace == "A.B"
    assert [s.qualified_name for s in facts.symbols] == ["A.B.X", "C.Y"]


def test_primary_constructor_bases_lose_their_arguments() -> None:
    facts = parse("record Dto(int Id) : BaseDto(Id), IFoo;\nclass C(int x) : Base(x) {}\n")
    assert [s.bases for s in facts.symbols] == [("BaseDto", "IFoo"), ("Base",)]


def test_null_conditional_calls_are_kept() -> None:
    facts = parse(
        "class C\n{\n    void M()\n    {\n        _log?.Info(1);\n        a.B()?.C();\n    }\n}\n"
    )
    got = [(c.name, c.shape, c.receiver) for c in facts.calls]
    assert ("Info", CallShape.QUALIFIED, "_log") in got
    assert [n for n, _, _ in got if n in ("B", "C")] == ["C", "B"]


def test_constructor_initializer_calls_are_kept() -> None:
    facts = parse("class C\n{\n    C() : base(Init()) {}\n}\n")
    assert [c.name for c in facts.calls] == ["Init"]


def test_alias_with_generics_drops_the_type_arguments() -> None:
    facts = parse("using L = System.Collections.Generic.List<Shop.Order>;\n")
    assert [i.specifier for i in facts.imports] == ["System.Collections.Generic.List"]


def test_named_attribute_arguments_are_not_the_first_argument() -> None:
    facts = parse('class C\n{\n    [Authorize(Roles = "A")]\n    void M() {}\n}\n')
    (dec,) = next(s for s in facts.symbols if s.name == "M").decorators
    assert dec.arg is None


def test_qualified_attribute_names_use_the_last_segment() -> None:
    facts = parse('[Microsoft.AspNetCore.Mvc.Route("x")]\nclass C {}\n')
    (dec,) = facts.symbols[0].decorators
    assert (dec.name, dec.arg) == ("Route", "x")


def test_interface_members_are_exported() -> None:
    facts = parse("interface IFoo\n{\n    void M();\n    void D() {}\n}\n")
    assert {s.name: s.exported for s in facts.symbols} == {"IFoo": False, "M": True, "D": True}


def test_global_using_and_generic_calls() -> None:
    facts = parse("global using Shop.Data;\nclass C\n{\n    void M() { Run<int>(); }\n}\n")
    assert [i.specifier for i in facts.imports] == ["Shop.Data"]
    assert [c.name for c in facts.calls] == ["Run"]
