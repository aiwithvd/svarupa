"""Java extraction: packages, annotations and Maven/Gradle dependencies."""

from __future__ import annotations

from pathlib import Path

from svarupa.detect import detect
from svarupa.extract import declared_dependencies, extract
from svarupa.extract.base import CallShape
from svarupa.extract.packs import extractor
from svarupa.model import EdgeKind, Resolution


def write(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf8")


def run(root: Path):
    scan = detect(root)
    return extract(scan, declared_dependencies(scan))


POM = (
    '<project xmlns="http://maven.apache.org/POM/4.0.0"><dependencies>'
    "<dependency><groupId>org.springframework.boot</groupId>"
    "<artifactId>spring-boot-starter-web</artifactId></dependency>"
    "</dependencies></project>\n"
)
SRC = "src/main/java/com/acme/shop"


def shop(root: Path) -> None:
    write(root, "pom.xml", POM)
    write(
        root,
        f"{SRC}/OrderController.java",
        "package com.acme.shop;\n\n"
        "import java.util.List;\n"
        "import org.springframework.web.bind.annotation.GetMapping;\n"
        "import com.acme.shop.service.OrderService;\n"
        "import com.acme.shop.model.*;\n"
        "import static com.acme.shop.util.Strings.trim;\n"
        "import com.acme.missing.Thing;\n\n"
        "public class OrderController {\n"
        "    private final OrderService service;\n\n"
        "    public OrderController(OrderService service) {\n"
        "        this.service = service;\n"
        "    }\n\n"
        '    @GetMapping("/orders")\n'
        "    public List<Order> list() {\n"
        '        trim("x");\n'
        "        return this.service.findAll();\n"
        "    }\n"
        "}\n",
    )
    write(
        root,
        f"{SRC}/service/OrderService.java",
        "package com.acme.shop.service;\n\n"
        "public class OrderService extends BaseService {\n"
        "    public Object findAll() {\n"
        "        return audit();\n"
        "    }\n"
        "}\n",
    )
    write(
        root,
        f"{SRC}/service/BaseService.java",
        "package com.acme.shop.service;\n\n"
        "abstract class BaseService {\n"
        "    protected Object audit() {\n"
        "        return null;\n"
        "    }\n"
        "}\n",
    )
    write(
        root,
        f"{SRC}/model/Order.java",
        "package com.acme.shop.model;\n\npublic class Order {}\n",
    )
    write(
        root,
        f"{SRC}/model/Line.java",
        "package com.acme.shop.model;\n\npublic record Line(int qty) {}\n",
    )
    write(
        root,
        f"{SRC}/util/Strings.java",
        "package com.acme.shop.util;\n\n"
        "public final class Strings {\n"
        "    public static String trim(String s) {\n"
        "        return s.trim();\n"
        "    }\n"
        "}\n",
    )


def test_class_wildcard_and_static_imports_resolve(tmp_path: Path) -> None:
    shop(tmp_path)
    imports = sorted(
        (e.src.rsplit("/", 1)[-1], e.dst.rsplit("/", 1)[-1])
        for e in run(tmp_path).edges
        if e.kind is EdgeKind.IMPORTS
    )
    assert imports == [
        ("OrderController.java", "Line.java"),
        ("OrderController.java", "Order.java"),
        ("OrderController.java", "OrderService.java"),
        ("OrderController.java", "Strings.java"),
    ]


def test_java_imports_land_in_the_right_bins(tmp_path: Path) -> None:
    shop(tmp_path)
    card = run(tmp_path).scorecard
    assert card.get("java", "imports", Resolution.RESOLVED) == 3
    assert card.get("java", "imports", Resolution.EXTERNAL) == 2  # java.util, spring
    assert card.get("java", "imports", Resolution.UNRESOLVED) == 1  # com.acme.missing


def test_field_injection_and_inherited_calls_resolve(tmp_path: Path) -> None:
    shop(tmp_path)
    calls = {
        (e.src.rsplit("#", 1)[-1], e.dst.rsplit("#", 1)[-1])
        for e in run(tmp_path).edges
        if e.kind is EdgeKind.CALLS
    }
    base = "src.main.java.com.acme.shop"
    assert (
        f"{base}.OrderController.OrderController.list",
        f"{base}.service.OrderService.OrderService.findAll",
    ) in calls
    assert (
        f"{base}.service.OrderService.OrderService.findAll",
        f"{base}.service.BaseService.BaseService.audit",
    ) in calls
    assert (
        f"{base}.OrderController.OrderController.list",
        f"{base}.util.Strings.Strings.trim",
    ) in calls


def test_annotations_exports_and_namespace_are_recorded() -> None:
    f = extractor("java").parse(
        "A.java",
        b'package com.a;\n@Rest public class A { @Get("/x") public void m() {} void hidden() {} }\n',
    )
    assert f.namespace == "com.a"
    a, m, hidden = f.symbols
    assert ([d.name for d in a.decorators], a.exported) == (["Rest"], True)
    assert ([(d.name, d.arg) for d in m.decorators], m.exported) == ([("Get", "/x")], True)
    assert hidden.exported is False


def test_an_unqualified_call_in_a_class_is_a_call_on_this() -> None:
    f = extractor("java").parse(
        "A.java",
        b"import static x.U.helper;\nclass A { void m() { own(); helper(); } }\n",
    )
    assert [(c.name, c.shape) for c in f.calls] == [
        ("own", CallShape.SELF),
        ("helper", CallShape.BARE),
    ]


def test_maven_gradle_and_catalog_groups_are_declared(tmp_path: Path) -> None:
    write(tmp_path, "pom.xml", POM)
    write(
        tmp_path,
        "app/build.gradle",
        "dependencies {\n  implementation 'com.google.guava:guava:33.0.0-jre'\n"
        '  implementation("io.jsonwebtoken:jjwt-api:0.12.5")\n}\n',
    )
    write(
        tmp_path,
        "gradle/libs.versions.toml",
        '[libraries]\njackson = { module = "com.fasterxml.jackson.core:jackson-databind" }\n'
        'lombok = "org.projectlombok:lombok:1.18.30"\n'
        'okhttp = { group = "com.squareup.okhttp3", name = "okhttp" }\n',
    )
    deps = declared_dependencies(detect(tmp_path))
    assert {
        "org.springframework",
        "com.google",
        "io.jsonwebtoken",
        "com.fasterxml",
        "org.projectlombok",
        "com.squareup",
    } <= deps


def test_a_pom_with_entities_is_refused(tmp_path: Path) -> None:
    write(
        tmp_path,
        "pom.xml",
        '<?xml version="1.0"?><!DOCTYPE p [<!ENTITY g "com.evil">]>'
        "<project><dependencies><dependency><groupId>&g;</groupId>"
        "</dependency></dependencies></project>\n",
    )
    assert "com.evil" not in declared_dependencies(detect(tmp_path))


def test_maven_placeholders_and_broken_xml_add_nothing(tmp_path: Path) -> None:
    write(
        tmp_path,
        "pom.xml",
        "<project><dependencies><dependency><groupId>${project.groupId}</groupId>"
        "</dependency></dependencies></project>\n",
    )
    write(tmp_path, "other/pom.xml", "<project><dependencies>\n")
    deps = declared_dependencies(detect(tmp_path))
    assert not {d for d in deps if "$" in d or d.startswith("project")}


def test_imports_are_not_chased_through_another_files_imports(tmp_path: Path) -> None:
    # Java has no re-exports: `helper` is not in a.Util just because Util
    # imports a package that defines one.
    write(
        tmp_path,
        "src/b/Caller.java",
        "package b;\nimport static a.Util.helper;\nclass Caller { void go() { helper(); } }\n",
    )
    write(tmp_path, "src/a/Util.java", "package a;\nimport c.*;\npublic class Util {}\n")
    write(
        tmp_path,
        "src/c/A.java",
        "package c;\npublic class A { public static void helper() {} }\n",
    )
    edges = [
        (e.src, e.dst)
        for e in run(tmp_path).edges
        if e.kind in (EdgeKind.CALLS, EdgeKind.REFERENCES)
    ]
    assert not [e for e in edges if e[1].startswith("src/c/A.java")], edges


def test_a_class_import_does_not_make_a_same_named_method_call_bare() -> None:
    f = extractor("java").parse(
        "A.java", b"import a.b.Order;\nclass A { void Order() {} void m() { Order(); } }\n"
    )
    assert [(c.name, c.shape) for c in f.calls] == [("Order", CallShape.SELF)]


def test_a_field_call_without_this_resolves_through_the_field_type(tmp_path: Path) -> None:
    write(
        tmp_path,
        "src/a/Api.java",
        "package a;\nimport b.Service;\n"
        "public class Api {\n  private final Service service;\n"
        "  public void list() { service.all(); }\n}\n",
    )
    write(
        tmp_path,
        "src/b/Service.java",
        "package b;\npublic class Service {\n  public void all() {}\n}\n",
    )
    calls = {(e.src, e.dst) for e in run(tmp_path).edges if e.kind is EdgeKind.CALLS}
    assert (
        "src/a/Api.java#src.a.Api.Api.list",
        "src/b/Service.java#src.b.Service.Service.all",
    ) in calls


def test_a_class_method_wins_over_its_interface_declaration(tmp_path: Path) -> None:
    write(tmp_path, "src/a/Repo.java", "package a;\npublic interface Repo { void save(); }\n")
    write(
        tmp_path,
        "src/a/SqlRepo.java",
        "package a;\npublic class SqlRepo implements Repo {\n"
        "  public void save() {}\n  void run() { save(); }\n}\n",
    )
    write(
        tmp_path,
        "src/a/Svc.java",
        "package a;\npublic class Svc {\n  private SqlRepo repo;\n"
        "  void go() { this.repo.save(); }\n}\n",
    )
    calls = [e for e in run(tmp_path).edges if e.kind is EdgeKind.CALLS]
    assert [c.resolution for c in calls] == [Resolution.RESOLVED, Resolution.RESOLVED]
    assert all(c.dst.endswith("SqlRepo.save") for c in calls)


def test_a_field_typed_by_a_plain_import_of_a_dependency_is_external(tmp_path: Path) -> None:
    write(
        tmp_path,
        "pom.xml",
        "<project><dependencies><dependency><groupId>org.acme</groupId>"
        "<artifactId>http</artifactId></dependency></dependencies></project>",
    )
    write(
        tmp_path,
        "src/main/java/app/A.java",
        "package app;\nimport org.acme.Client;\n"
        "class A { Client c; void f() { this.c.get(); } }\n",
    )
    card = run(tmp_path).scorecard
    assert card.get("java", "calls", Resolution.EXTERNAL) == 1


# ----------------------------------------------------------------------
# parameter and local types

REPO_J = "package a;\npublic class Repo {\n  public void save() {}\n}\n"
OTHER_J = "package a;\npublic class Other {\n  public void x() {}\n}\n"


def locals_of(src: str) -> set[tuple[str, str, str]]:
    facts = extractor("java").parse("a/A.java", src.encode("utf8"))
    return {(x.function, x.name, x.type_name) for x in facts.locals}


def typed_calls(tmp_path: Path, body: str) -> set[tuple[str, str]]:
    write(tmp_path, "src/a/Repo.java", REPO_J)
    write(tmp_path, "src/a/Other.java", OTHER_J)
    write(tmp_path, "src/a/A.java", "package a;\n" + body)
    return {(e.src, e.dst) for e in run(tmp_path).edges if e.kind is EdgeKind.CALLS}


def saves(calls: set[tuple[str, str]]) -> bool:
    return any(d.endswith("Repo.save") for _, d in calls)


def test_a_local_shadows_a_field_of_the_same_name(tmp_path: Path) -> None:
    got = typed_calls(tmp_path, "class A { Repo repo; void f(Other repo) { repo.x(); } }\n")
    assert [d for _, d in got if d.endswith(".x")] == ["src/a/Other.java#src.a.Other.Other.x"]
    assert not saves(got)


def test_a_var_local_hides_a_field_of_the_same_name(tmp_path: Path) -> None:
    got = typed_calls(
        tmp_path, "class A { Repo repo; void f() { var repo = make(); repo.save(); } }\n"
    )
    assert not saves(got)


def test_a_typed_parameter_resolves(tmp_path: Path) -> None:
    got = typed_calls(tmp_path, "class A { void f(final Repo r) { r.save(); } }\n")
    assert ("src/a/A.java#src.a.A.A.f", "src/a/Repo.java#src.a.Repo.Repo.save") in got


def test_a_typed_local_resolves(tmp_path: Path) -> None:
    got = typed_calls(tmp_path, "class A { void f() { Repo r = make(); r.save(); } }\n")
    assert saves(got)


def test_a_var_local_is_not_inferred(tmp_path: Path) -> None:
    src = "class A { void f() { var r = new Repo(); r.save(); } }\n"
    assert locals_of(src) == {("a.A.A.f", "r", "")}
    assert not saves(typed_calls(tmp_path, src))


def test_a_generic_parameter_stays_unresolved(tmp_path: Path) -> None:
    src = "class A { <T extends Repo> void f(T t) { t.save(); } }\n"
    assert locals_of(src) == {("a.A.A.f", "t", "")}
    assert not saves(typed_calls(tmp_path, src))


def test_a_class_generic_parameter_stays_unresolved() -> None:
    assert locals_of("class A<T> { void f(T t) {} }\n") == {("a.A.A.f", "t", "")}


def test_generic_and_qualified_types_keep_the_class_name() -> None:
    got = locals_of(
        "class A { void f(Optional<Repo> a, java.util.List<Order> b, Map.Entry<K, V> c) {} }\n"
    )
    assert {(n, t) for _, n, t in got} == {("a", "Optional"), ("b", "List"), ("c", "Entry")}


def test_arrays_and_primitives_are_not_typed() -> None:
    got = locals_of("class A { void f(Repo[] a, int b, Repo... c) { Repo d[] = null; } }\n")
    assert {(n, t) for _, n, t in got} == {("a", ""), ("b", ""), ("c", ""), ("d", "")}


def test_an_enhanced_for_variable_is_typed(tmp_path: Path) -> None:
    got = typed_calls(
        tmp_path,
        "class A { void f(java.util.List<Repo> l) { for (Repo o : l) { o.save(); } } }\n",
    )
    assert saves(got)


def test_a_catch_parameter_is_typed_and_a_multi_catch_is_not() -> None:
    got = locals_of("class A { void f() { try {} catch (Boom e) {} catch (A1 | A2 g) {} } }\n")
    assert {(n, t) for _, n, t in got} == {("e", "Boom"), ("g", "")}


def test_lambda_parameters_typed_and_untyped() -> None:
    got = locals_of(
        "class A { void f() { g((Repo a) -> 1, b -> 2, (c, d) -> 3, (final Other e) -> 4); } }\n"
    )
    assert {(n, t) for _, n, t in got} == {
        ("a", "Repo"),
        ("b", ""),
        ("c", ""),
        ("d", ""),
        ("e", "Other"),
    }


def test_an_instanceof_pattern_variable_is_typed(tmp_path: Path) -> None:
    src = "class A { void f(Object x) { if (x instanceof Repo r) { r.save(); } } }\n"
    assert ("a.A.A.f", "r", "Repo") in locals_of(src)
    assert saves(typed_calls(tmp_path, src))


def test_a_try_with_resources_variable_is_typed() -> None:
    got = locals_of("class A { void f() { try (Repo r = open()) { } } }\n")
    assert got == {("a.A.A.f", "r", "Repo")}


def test_a_receiver_parameter_and_a_constructor() -> None:
    got = locals_of("class A { A(Repo r) {} void m(A this, int x) {} }\n")
    assert {(f, n, t) for f, n, t in got if n == "r"} == {("a.A.A.A", "r", "Repo")}
    assert not [x for x in got if x[1] == "this"]


def test_a_name_typed_two_ways_in_one_method_resolves_nothing(tmp_path: Path) -> None:
    src = "class A { void f() { { Repo r = a(); r.save(); } { Other r = b(); r.x(); } } }\n"
    assert not saves(typed_calls(tmp_path, src))


def test_a_local_field_chain_takes_one_step(tmp_path: Path) -> None:
    write(tmp_path, "src/a/Repo.java", REPO_J)
    write(tmp_path, "src/a/State.java", "package a;\npublic class State { Repo db; }\n")
    write(
        tmp_path, "src/a/A.java", "package a;\nclass A { void f(State s) { s.db.save(); } }\n"
    )
    got = {(e.src, e.dst) for e in run(tmp_path).edges if e.kind is EdgeKind.CALLS}
    assert saves(got)


def test_a_plain_instanceof_does_not_type_its_operand_field(tmp_path: Path) -> None:
    src = "class A { Repo repo; void f() { if (repo instanceof Other) {} repo.save(); } }\n"
    assert locals_of(src) == set()
    assert saves(typed_calls(tmp_path, src))


def test_a_plain_instanceof_does_not_retype_its_operand_param(tmp_path: Path) -> None:
    src = "class A { void f(Repo r) { if (r instanceof Other) {} r.save(); } }\n"
    assert {(n, t) for _, n, t in locals_of(src)} == {("r", "Repo")}
    assert saves(typed_calls(tmp_path, src))


# ----------------------------------------------------------------------
# a binding is in scope only in its block, loop, clause, arm or lambda

REPO_QW = "package a;\npublic class Repo {\n  public void q() {}\n  public void w() {}\n}\n"
OTHER_QW = "package a;\npublic class Other {\n  public void q() {}\n  public void w() {}\n}\n"


def scope_calls(tmp_path: Path, body: str) -> set[str]:
    """What `f` calls, as `Type.method`, in a class with a field `Repo repo`."""
    write(tmp_path, "src/a/Repo.java", REPO_QW)
    write(tmp_path, "src/a/Other.java", OTHER_QW)
    write(
        tmp_path,
        "src/a/A.java",
        "package a;\nclass A {\n  Repo repo;\n  void f(Object o, java.util.List<Other> xs) {\n"
        + body
        + "\n  }\n}\n",
    )
    got = {(e.src, e.dst) for e in run(tmp_path).edges if e.kind is EdgeKind.CALLS}
    return {".".join(d.rsplit(".", 2)[-2:]) for s, d in got if s.endswith(".f")}


def test_a_local_in_a_block_does_not_leak_out_of_it(tmp_path: Path) -> None:
    body = "    {\n      Other repo = make();\n      repo.q();\n    }\n    repo.w();"
    assert scope_calls(tmp_path, body) == {"Other.q", "Repo.w"}


def test_a_lambda_parameter_does_not_leak_out_of_the_lambda(tmp_path: Path) -> None:
    body = "    xs.forEach((Other repo) -> {\n      repo.q();\n    });\n    repo.w();"
    assert scope_calls(tmp_path, body) == {"Other.q", "Repo.w"}


def test_a_one_line_lambda_with_the_name_outside_resolves_to_nothing(tmp_path: Path) -> None:
    body = "    xs.forEach((Other repo) -> repo.q()); repo.w();"
    assert scope_calls(tmp_path, body) == set()


def test_a_one_line_typed_lambda_resolves(tmp_path: Path) -> None:
    body = "    xs.forEach((Other repo) -> repo.q());"
    assert scope_calls(tmp_path, body) == {"Other.q"}


def test_a_for_variable_does_not_leak_out_of_the_loop(tmp_path: Path) -> None:
    body = "    for (Other repo : xs) {\n      repo.q();\n    }\n    repo.w();"
    assert scope_calls(tmp_path, body) == {"Other.q", "Repo.w"}


def test_a_catch_parameter_does_not_leak_out_of_the_clause(tmp_path: Path) -> None:
    body = "    try {\n    } catch (Other repo) {\n      repo.q();\n    }\n    repo.w();"
    assert scope_calls(tmp_path, body) == {"Other.q", "Repo.w"}


def test_a_case_pattern_does_not_leak_out_of_the_arm(tmp_path: Path) -> None:
    body = (
        "    switch (o) {\n      case Other repo -> repo.q();\n      default -> {}\n    }\n"
        "    repo.w();"
    )
    assert scope_calls(tmp_path, body) == {"Other.q", "Repo.w"}


def test_an_instanceof_binding_only_hides_the_field_after_its_statement(
    tmp_path: Path,
) -> None:
    body = "    if (o instanceof Other repo) {\n      repo.q();\n    }\n    repo.w();"
    assert scope_calls(tmp_path, body) == {"Other.q"}


def test_a_negated_instanceof_gives_no_edge_after_the_statement(tmp_path: Path) -> None:
    body = "    if (!(o instanceof Other repo)) {\n      return;\n    }\n    repo.w();"
    assert scope_calls(tmp_path, body) == set()


def test_an_instanceof_binding_not_used_later_leaves_the_field(tmp_path: Path) -> None:
    body = "    if (o instanceof Other other) {\n      other.q();\n    }\n    repo.w();"
    assert scope_calls(tmp_path, body) == {"Other.q", "Repo.w"}


def test_sibling_blocks_each_resolve_their_own_type(tmp_path: Path) -> None:
    body = (
        "    {\n      Repo r = make();\n      r.q();\n    }\n"
        "    {\n      Other r = make();\n      r.w();\n    }"
    )
    assert scope_calls(tmp_path, body) == {"Repo.q", "Other.w"}


def test_a_local_resolves_only_from_its_declaration_on(tmp_path: Path) -> None:
    body = "    repo.q();\n    Other repo = make();\n    repo.w();"
    assert scope_calls(tmp_path, body) == {"Repo.q", "Other.w"}


def test_an_externally_imported_parameter_type_stays_external(tmp_path: Path) -> None:
    pom = (
        '<project xmlns="http://maven.apache.org/POM/4.0.0"><dependencies>'
        "<dependency><groupId>com.squareup.okhttp</groupId>"
        "<artifactId>okhttp</artifactId></dependency></dependencies></project>\n"
    )
    write(tmp_path, "pom.xml", pom)
    write(
        tmp_path,
        "src/a/Request.java",
        "package a;\npublic class Request {\n  public void send() {}\n}\n",
    )
    write(
        tmp_path,
        "src/a/A.java",
        "package a;\nimport com.squareup.okhttp.Request;\n"
        "class A { void f(Request r) { r.send(); } }\n",
    )
    calls = {e.dst for e in run(tmp_path).edges if e.kind is EdgeKind.CALLS}
    assert not any(d.endswith("Request.send") for d in calls)


def test_an_externally_imported_field_type_stays_external(tmp_path: Path) -> None:
    pom = (
        '<project xmlns="http://maven.apache.org/POM/4.0.0"><dependencies>'
        "<dependency><groupId>com.squareup.okhttp</groupId>"
        "<artifactId>okhttp</artifactId></dependency></dependencies></project>\n"
    )
    write(tmp_path, "pom.xml", pom)
    write(
        tmp_path,
        "src/a/Request.java",
        "package a;\npublic class Request {\n  public void send() {}\n}\n",
    )
    write(
        tmp_path,
        "src/a/A.java",
        "package a;\nimport com.squareup.okhttp.Request;\n"
        "class A { Request r; void f() { r.send(); } }\n",
    )
    calls = {e.dst for e in run(tmp_path).edges if e.kind is EdgeKind.CALLS}
    assert not any(d.endswith("Request.send") for d in calls)
