"""The walker's own contract, on tiny packs over the Python grammar.

The real packs are tested by the golden facts. These tests pin what every
pack inherits: fact order, scope and qualified names, which regions are
walked, the depth cap and the syntax-error diagnostic.
"""

from __future__ import annotations

from tree_sitter import Node as TSNode

from svarupa.extract.base import MAX_AST_DEPTH, CallShape, CallSite, DecoratorRef
from svarupa.extract.packs.model import (
    Call,
    Decorated,
    Define,
    Grammar,
    Maturity,
    Pack,
)
from svarupa.extract.packs.walker import Ctx, Frame, PackExtractor

GRAMMAR = Grammar(
    distribution="tree-sitter-python",
    version="0.25.0",
    module="tree_sitter_python",
    default="language",
)


def _bare_call(ctx: Ctx, node: TSNode, frame: Frame) -> CallSite | None:
    func = node.child_by_field_name("function")
    if func is None or func.type != "identifier":
        return None
    row = node.start_point[0]
    return CallSite(
        ctx.text(func), CallShape.BARE, None, ctx.evidence(row, row), frame.fn, frame.cls
    )


def _decorator(ctx: Ctx, node: TSNode) -> DecoratorRef | None:
    text = ctx.text(node).lstrip("@").split("(")[0]
    return DecoratorRef(name=text, arg=None, evidence=ctx.node_evidence(node))


PACK = Pack(
    lang="toy",
    grammar=GRAMMAR,
    maturity=Maturity.EXPERIMENTAL,
    qualified_prefix=lambda path: path.rsplit(".", 1)[0].replace("/", "."),
    decorator=_decorator,
    rules={
        "decorated_definition": Decorated(),
        "class_definition": Define(kind="class", sets_class=True),
        "function_definition": Define(kind="function", kind_in_class="method"),
        "call": Call(hook=_bare_call),
    },
)


def parse(src: str, path: str = "pkg/mod.py"):
    return PackExtractor(PACK).parse(path, src.encode())


def test_definitions_get_scope_kind_and_qualified_names() -> None:
    f = parse("class A:\n    def m(self):\n        def inner():\n            pass\n")
    got = [(s.qualified_name, s.kind, s.enclosing_class) for s in f.symbols]
    assert got == [
        ("pkg.mod.A", "class", None),
        ("pkg.mod.A.m", "method", "A"),
        ("pkg.mod.A.m.inner", "method", "A"),
    ]


def test_calls_keep_document_order_and_enclosing_function() -> None:
    f = parse("def go():\n    b()\n    a()\n\nc()\n")
    assert [(c.name, c.enclosing) for c in f.calls] == [
        ("b", "pkg.mod.go"),
        ("a", "pkg.mod.go"),
        ("c", None),
    ]


def test_skipped_regions_stay_skipped() -> None:
    # Default arguments, base lists and decorator arguments are not walked:
    # a Define walks only its body, a Decorated only its definition.
    f = parse(
        "@deco(setup())\nclass A(base()):\n    def m(self, x=default()):\n        body()\n"
    )
    assert [c.name for c in f.calls] == ["body"]


def test_decorators_reach_the_definition_they_wrap() -> None:
    f = parse("class A:\n    @staticmethod\n    def m():\n        pass\n")
    m = next(s for s in f.symbols if s.name == "m")
    assert m.kind == "method"
    assert [d.name for d in m.decorators] == ["staticmethod"]


def test_exported_defaults_to_the_name_rule() -> None:
    f = parse("def public():\n    pass\n\ndef _private():\n    pass\n")
    assert [(s.name, s.exported) for s in f.symbols] == [("public", True), ("_private", False)]


def test_evidence_lines_are_one_based_and_span_the_node() -> None:
    f = parse("\n\ndef go():\n    pass\n")
    ev = f.symbols[0].evidence
    assert (ev.file, ev.start_line, ev.end_line) == ("pkg/mod.py", 3, 4)


def test_syntax_errors_are_reported_once() -> None:
    f = parse("def broken(:\n    pass\n\ndef fine():\n    pass\n")
    assert [d.code for d in f.diagnostics] == ["SVA-X-001"]


def test_depth_cap_degrades_without_recursion_error() -> None:
    depth = MAX_AST_DEPTH * 3
    f = parse("x = " + "(" * depth + "f()" + ")" * depth + "\n\ndef keep():\n    pass\n")
    assert [d.code for d in f.diagnostics] == ["SVA-X-003"]
    assert [s.name for s in f.symbols] == ["keep"]
    # The call sits below the cap, so the walk must not have reached it.
    assert f.calls == ()


def test_missing_grammar_is_reported_not_raised() -> None:
    from dataclasses import replace

    broken = replace(PACK, grammar=replace(GRAMMAR, module="tree_sitter_does_not_exist"))
    reason = PackExtractor(broken).available()
    assert reason is not None
    assert "tree-sitter-python" in reason


def test_working_grammar_is_available() -> None:
    assert PackExtractor(PACK).available() is None


def test_an_export_hook_overrides_the_name_rule() -> None:
    from dataclasses import replace

    capital = Define(kind="function", exported_by=lambda _ctx, _node, name: name[:1].isupper())
    pack = replace(PACK, rules={**PACK.rules, "function_definition": capital})
    f = PackExtractor(pack).parse("m.py", b"def Up():\n    pass\n\ndef down():\n    pass\n")
    assert [(s.name, s.exported) for s in f.symbols] == [("Up", True), ("down", False)]


def test_a_decorators_hook_adds_decorators() -> None:
    from dataclasses import replace

    def tagged(ctx: Ctx, node: TSNode) -> tuple[DecoratorRef, ...]:
        return (DecoratorRef(name="tag", arg=None, evidence=ctx.node_evidence(node)),)

    rule = Define(kind="class", sets_class=True, decorators_from=tagged)
    pack = replace(PACK, rules={**PACK.rules, "class_definition": rule})
    f = PackExtractor(pack).parse("m.py", b"class A:\n    pass\n")
    assert [d.name for d in f.symbols[0].decorators] == ["tag"]


def test_a_hook_can_record_the_file_namespace() -> None:
    from dataclasses import replace

    from svarupa.extract.packs.model import Custom

    def remember(ctx: Ctx, node: TSNode, _frame: Frame, _depth: int) -> None:
        ctx.namespace = ctx.text(node).split()[1]

    pack = replace(PACK, rules={**PACK.rules, "import_statement": Custom(hook=remember)})
    f = PackExtractor(pack).parse("m.py", b"import shop.core\n")
    assert f.namespace == "shop.core"


def test_namespace_defaults_to_empty() -> None:
    assert parse("x = 1\n").namespace == ""
