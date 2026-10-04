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
