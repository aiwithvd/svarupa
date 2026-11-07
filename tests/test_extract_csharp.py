"""C# extraction: namespaces, using directives, attributes and calls."""

from __future__ import annotations

from pathlib import Path

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


def test_a_file_records_every_namespace_it_declares() -> None:
    facts = parse("namespace A.B { class X {} }\nnamespace C { namespace D { class Y {} } }\n")
    assert facts.namespaces == ("A.B", "C", "C.D")
    assert facts.namespace == "A.B"
    assert parse(FILE_SCOPED).namespaces == ("Shop.Api",)
    assert parse("class A {}\n", "a.cs").namespaces == ()


def test_field_and_property_types_are_recorded() -> None:
    facts = parse(
        "namespace S;\n"
        "class Svc\n{\n"
        "    private readonly IOrderRepo _repo;\n"
        "    public List<Line> Lines { get; set; }\n"
        "    private Db a, b;\n"
        "    public Svc(IOrderRepo repo) { _repo = repo; }\n"
        "}\n"
    )
    assert [(f.owner, f.field, f.type_name) for f in facts.fields] == [
        ("Svc", "_repo", "IOrderRepo"),
        ("Svc", "Lines", "List"),
        ("Svc", "a", "Db"),
        ("Svc", "b", "Db"),
    ]


def test_nuget_package_ids_come_from_csproj_and_central_props(tmp_path: Path) -> None:
    from svarupa.detect import detect
    from svarupa.extract import declared_dependencies

    (tmp_path / "Api").mkdir()
    (tmp_path / "Api" / "Api.csproj").write_text(
        '<Project Sdk="Microsoft.NET.Sdk">\n<ItemGroup>\n'
        '<PackageReference Include="Dapper" Version="2.0.0" />\n'
        '<PackageReference Include="MediatR" />\n'
        "</ItemGroup>\n</Project>\n"
    )
    (tmp_path / "Directory.Packages.props").write_text(
        '<Project><ItemGroup><PackageVersion Include="Ardalis.Result" Version="1" />'
        "</ItemGroup></Project>\n"
    )
    (tmp_path / "Api" / "A.cs").write_text("namespace Api;\nclass A {}\n")
    assert {"Dapper", "MediatR", "Ardalis.Result"} <= declared_dependencies(detect(tmp_path))


def test_a_csproj_with_a_dtd_is_refused(tmp_path: Path) -> None:
    from svarupa.detect import detect
    from svarupa.extract import declared_dependencies

    (tmp_path / "A.csproj").write_text(
        '<!DOCTYPE p [<!ENTITY x "y">]><Project><PackageReference Include="Evil" /></Project>'
    )
    assert "Evil" not in declared_dependencies(detect(tmp_path))


# ----------------------------------------------------------------------
# parameter and local types

REPO_CS = "namespace A;\npublic class Repo\n{\n    public void Save() {}\n}\n"
OTHER_CS = "namespace A;\npublic class Other\n{\n    public void X() {}\n}\n"
IREPO_CS = "namespace A;\npublic interface IRepo\n{\n    void Save();\n}\n"


def locals_of(src: str) -> set[tuple[str, str, str]]:
    facts = parse("namespace a;\n" + src, "a/A.cs")
    return {(x.function, x.name, x.type_name) for x in facts.locals}


def typed_calls(tmp_path: Path, body: str) -> set[tuple[str, str]]:
    from svarupa.detect import detect
    from svarupa.extract import declared_dependencies, extract
    from svarupa.model import EdgeKind

    for name, text in (("Repo", REPO_CS), ("Other", OTHER_CS), ("IRepo", IREPO_CS)):
        (tmp_path / f"{name}.cs").write_text(text)
    (tmp_path / "Use.cs").write_text("namespace A;\n" + body)
    scan = detect(tmp_path)
    graph = extract(scan, declared_dependencies(scan))
    return {(e.src, e.dst) for e in graph.edges if e.kind is EdgeKind.CALLS}


def saves(calls: set[tuple[str, str]]) -> bool:
    return any(d.endswith("Save") for _, d in calls)


def pairs(src: str) -> set[tuple[str, str]]:
    return {(n, t) for _, n, t in locals_of(src)}


def test_a_typed_parameter_resolves(tmp_path: Path) -> None:
    got = typed_calls(tmp_path, "class U { void F(Repo r) { r.Save(); } }\n")
    assert saves(got)


def test_an_interface_parameter_resolves_to_the_interface_method(tmp_path: Path) -> None:
    got = typed_calls(tmp_path, "class U { void F(IRepo repo) { repo.Save(); } }\n")
    assert [d for _, d in got if d.endswith("Save")] == ["IRepo.cs#A.IRepo.Save"]


def test_a_typed_local_resolves(tmp_path: Path) -> None:
    assert saves(typed_calls(tmp_path, "class U { void F() { Repo r = Make(); r.Save(); } }\n"))


def test_var_is_not_an_explicit_type(tmp_path: Path) -> None:
    src = "class U { void F() { var r = new Repo(); r.Save(); } }\n"
    assert locals_of(src) == {("a.U.F", "r", "")}
    assert not saves(typed_calls(tmp_path, src))


def test_a_parameter_shadows_a_field_of_the_same_name(tmp_path: Path) -> None:
    got = typed_calls(tmp_path, "class U { Repo _repo; void F(Other _repo) { _repo.X(); } }\n")
    assert [d for _, d in got if d.endswith(".X")] == ["Other.cs#A.Other.X"]
    assert not saves(got)


def test_a_var_local_hides_a_field_of_the_same_name(tmp_path: Path) -> None:
    src = "class U { Repo _repo; void F() { var _repo = Make(); _repo.Save(); } }\n"
    assert not saves(typed_calls(tmp_path, src))


def test_generic_parameters_are_skipped(tmp_path: Path) -> None:
    src = "class U<T> { void F<V>(T a, V b) { a.Save(); } }\n"
    assert pairs(src) == {("a", ""), ("b", "")}
    assert not saves(typed_calls(tmp_path, src))


def test_parameter_modifiers_and_extension_this() -> None:
    got = pairs(
        "static class U { static void F(this Repo a, ref Other b, out Repo c, in Repo d,"
        " params Repo[] e, Repo f = null) {} }\n"
    )
    assert got == {
        ("a", "Repo"),
        ("b", "Other"),
        ("c", "Repo"),
        ("d", "Repo"),
        ("e", ""),
        ("f", "Repo"),
    }


def test_nullable_unwraps_and_arrays_tuples_primitives_are_untyped() -> None:
    got = pairs(
        "class U { void F(Repo? a, Repo[] b, int c, (int, Repo) d, List<Repo> e) {} }\n"
    )
    assert got == {("a", "Repo"), ("b", ""), ("c", ""), ("d", ""), ("e", "List")}


def test_qualified_types_keep_the_class_name() -> None:
    assert pairs("class U { void F(Shop.Data.Repo a, global::Shop.Repo b) {} }\n") == {
        ("a", "Repo"),
        ("b", "Repo"),
    }


def test_local_declarations_with_several_declarators() -> None:
    got = pairs("class U { void F() { Repo a = Make(), b; Repo[] c; Repo? d; } }\n")
    assert got == {("a", "Repo"), ("b", "Repo"), ("c", ""), ("d", "Repo")}


def test_a_foreach_variable_is_typed(tmp_path: Path) -> None:
    src = "class U { void F(IList<Repo> l) { foreach (Repo o in l) { o.Save(); } } }\n"
    assert saves(typed_calls(tmp_path, src))
    assert pairs("class U { void F() { foreach (var o in l) {} } }\n") == {("o", "")}


def test_catch_using_and_for_bindings() -> None:
    got = pairs(
        "class U { void F() { try {} catch (Boom e) {} catch (Other) {} catch {}"
        " using (Repo r = Open()) {} using Repo s = Open(); using var t = Open();"
        " for (Repo i = null; ;) {} } }\n"
    )
    assert got == {("e", "Boom"), ("r", "Repo"), ("s", "Repo"), ("t", ""), ("i", "Repo")}


def test_pattern_and_out_variables(tmp_path: Path) -> None:
    src = "class U { void F(object x) { if (x is Repo r) { r.Save(); } } }\n"
    assert ("a.U.F", "r", "Repo") in locals_of(src)
    assert saves(typed_calls(tmp_path, src))
    got = pairs(
        "class U { void F(object x) { switch (x) { case Repo c: break; }"
        " var y = x switch { Other o => 1, _ => 2 }; G(out Repo p); G(out var q);"
        " if (x is { } any) {} if (x is var v) {} } }\n"
    )
    assert got >= {
        ("c", "Repo"),
        ("o", "Other"),
        ("p", "Repo"),
        ("q", ""),
        ("any", ""),
        ("v", ""),
    }


def test_lambda_parameters_typed_and_untyped() -> None:
    got = pairs(
        "class U { void F() { G((Repo a) => 1, b => 2, (c, d) => 3,"
        " delegate (Other e) { }); } }\n"
    )
    assert got == {("a", "Repo"), ("b", ""), ("c", ""), ("d", ""), ("e", "Other")}


def test_deconstruction_binds_untyped_names() -> None:
    got = pairs(
        "class U { void F() { var (a, b) = P(); (var c, var d) = P(); Repo (e, _) = P();"
        " foreach (var (k, v) in m) {} } }\n"
    )
    assert got == {(n, "") for n in "abcdekv"}


def test_query_range_variables() -> None:
    got = pairs(
        "class U { void F() { var q = from Repo o in rs let l = o.X join j in js on 1 equals 2"
        " into g select o; } }\n"
    )
    assert got >= {("o", "Repo"), ("l", ""), ("j", ""), ("g", ""), ("q", "")}


def test_a_local_function_has_its_own_parameters() -> None:
    got = locals_of("class U { void F() { void G(Repo a) {} } }\n")
    assert ("a.U.F.G", "a", "Repo") in got


def test_primary_constructor_parameters_are_in_scope_in_every_member(tmp_path: Path) -> None:
    src = "class U(Repo repo) { void F() { repo.Save(); } void G() {} }\n"
    got = locals_of(src)
    assert {("a.U.F", "repo", "Repo"), ("a.U.G", "repo", "Repo")} <= got
    assert saves(typed_calls(tmp_path, src))


def test_a_member_parameter_retyping_a_primary_parameter_resolves_nothing(
    tmp_path: Path,
) -> None:
    src = "class U(Repo repo) { void F(Other repo) { repo.Save(); } }\n"
    assert not saves(typed_calls(tmp_path, src))


def test_a_plain_type_test_binds_nothing(tmp_path: Path) -> None:
    src = (
        "class U { Repo _repo; void F(Other o, object x) {"
        " if (_repo is Other) {} if (o is Repo) {} if (x is Repo) {}"
        " _repo.Save(); o.X(); } }\n"
    )
    assert pairs(src) == {("o", "Other"), ("x", "")}
    got = typed_calls(tmp_path, src)
    assert saves(got)  # _repo stays the field Repo
    assert [d for _, d in got if d.endswith(".X")] == ["Other.cs#A.Other.X"]


def test_a_typed_join_variable_is_not_mistaken_for_its_type() -> None:
    got = pairs(
        "class U { void F() { var q = from a in b join Repo j in js on 1 equals 2 select a; } }\n"
    )
    assert ("j", "Repo") in got
    assert ("Repo", "") not in got
