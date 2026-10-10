"""Go language pack.

Go names a package by import path and compiles it as one unit, so imports
resolve to directories (see modules.GoModules). Methods are declared at the
top level with a receiver (`func (s *Server) Run()`), so the receiver type,
not lexical nesting, makes a function a method. Exported means a capital
first letter: that is the language rule, not a convention.
"""

from __future__ import annotations

import re

from tree_sitter import Node as TSNode

from svarupa.extract.base import CallShape, CallSite, ImportRef, SymbolRef
from svarupa.extract.packs.model import (
    Call,
    Custom,
    Define,
    Grammar,
    Import,
    Maturity,
    MetricsSpec,
    Pack,
)
from svarupa.extract.packs.modules import GoModules
from svarupa.extract.packs.walker import Ctx, Frame

__all__ = ["BUILTINS", "PACK"]

# Builtin functions and conversions: calls to them are not intra-repo calls.
# fmt: off
BUILTINS = frozenset(
    {
        "append", "cap", "clear", "close", "complex", "copy", "delete", "imag",
        "len", "make", "max", "min", "new", "panic", "print", "println", "real",
        "recover", "any", "bool", "byte", "complex64", "complex128", "error",
        "float32", "float64", "int", "int8", "int16", "int32", "int64", "rune",
        "string", "uint", "uint8", "uint16", "uint32", "uint64", "uintptr",
    }
)
# fmt: on

_MAJOR = re.compile(r"^v[0-9]+$")


def _exported(name: str) -> bool:
    return name[:1].isupper()


def _package_name(path: str) -> str:
    """The name a package is used by when the import gives none.

    The real name is the target's `package` clause, which pass 1 cannot read
    from here; the last path element is the toolchain's convention, minus a
    major-version suffix (`.../v2`, `gopkg.in/yaml.v3`). A wrong guess only
    leaves a call unresolved: the name still has to match a definition.
    """
    parts = path.split("/")
    last = parts[-1]
    if _MAJOR.match(last) and len(parts) > 1:
        last = parts[-2]
    return re.sub(r"\.v[0-9]+$", "", last)


def _literal(ctx: Ctx, node: TSNode) -> str | None:
    if node.type == "interpreted_string_literal":
        return "".join(
            ctx.text(c) for c in node.children if c.type == "interpreted_string_literal_content"
        )
    if node.type == "raw_string_literal":
        return ctx.text(node).strip("`")
    return None


def import_(ctx: Ctx, node: TSNode) -> ImportRef | None:
    path_node = node.child_by_field_name("path")
    if path_node is None:
        return None
    spec = _literal(ctx, path_node)
    if not spec:
        return None
    alias_node = node.child_by_field_name("name")
    local = ctx.text(alias_node) if alias_node is not None else _package_name(spec)
    # `_` imports for side effects, `.` merges names into the file: neither
    # gives a name to call through.
    alias_of = {} if local in ("_", ".") else {local: spec}
    return ImportRef(
        specifier=spec,
        names=(),
        alias_of=alias_of,
        evidence=ctx.node_evidence(node),
        is_relative=spec.startswith("."),
        is_from=True,
    )


def type_spec(ctx: Ctx, node: TSNode, frame: Frame, _depth: int) -> None:
    name_node = node.child_by_field_name("name")
    ty = node.child_by_field_name("type")
    if name_node is None or ty is None:
        return
    kind = {"struct_type": "class", "interface_type": "interface"}.get(ty.type)
    if kind is None:
        return  # aliases and named basic types are not architecture
    name = ctx.text(name_node)
    ctx.symbols.append(
        SymbolRef(
            name=name,
            qualified_name=ctx.qual((*frame.stack, name)),
            kind=kind,
            evidence=ctx.node_evidence(node),
            exported=_exported(name),
        )
    )


def _receiver_type(ctx: Ctx, params: TSNode) -> str | None:
    for param in params.children:
        if param.type == "parameter_declaration":
            ty = param.child_by_field_name("type")
            if ty is None:
                return None
            name = ctx.text(ty).lstrip("*").split("[")[0].strip()
            return name or None
    return None


def method_declaration(ctx: Ctx, node: TSNode, frame: Frame, depth: int) -> None:
    name_node = node.child_by_field_name("name")
    if name_node is None:
        return
    receiver = node.child_by_field_name("receiver")
    owner = _receiver_type(ctx, receiver) if receiver is not None else None
    name = ctx.text(name_node)
    scope = (*frame.stack, owner, name) if owner else (*frame.stack, name)
    qualified = ctx.qual(scope)
    ctx.symbols.append(
        SymbolRef(
            name=name,
            qualified_name=qualified,
            kind="method" if owner else "function",
            evidence=ctx.node_evidence(node),
            enclosing_class=owner,
            exported=_exported(name),
        )
    )
    body = node.child_by_field_name("body")
    if body is None:
        return
    inner = Frame(scope, owner, qualified)
    for child in body.children:
        ctx.visit(child, inner, depth + 1)


def _first_str_arg(ctx: Ctx, node: TSNode) -> str | None:
    args = node.child_by_field_name("arguments")
    if args is None:
        return None
    for child in args.children:
        if child.type in ("(", ")", ",", "comment"):
            continue
        return _literal(ctx, child)
    return None


def call(ctx: Ctx, node: TSNode, frame: Frame) -> CallSite | None:
    func = node.child_by_field_name("function")
    if func is None:
        return None
    fn, cls = frame.fn, frame.cls
    first = _first_str_arg(ctx, node)
    if func.type == "identifier":
        name = ctx.text(func)
        if name in BUILTINS:
            return None
        row = node.start_point[0]
        return CallSite(name, CallShape.BARE, None, ctx.evidence(row, row), fn, cls, first)
    if func.type == "selector_expression":
        operand = func.child_by_field_name("operand")
        field = func.child_by_field_name("field")
        if operand is None or field is None:
            return None
        row = field.start_point[0]
        ev = ctx.evidence(row, row)
        if operand.type == "identifier":
            # A package (`orders.New()`) or a local value (`svc.Place()`);
            # the resolver decides, because only it knows the imports.
            return CallSite(
                ctx.text(field), CallShape.QUALIFIED, ctx.text(operand), ev, fn, cls, first
            )
        return CallSite(
            ctx.text(field), CallShape.MEMBER, ctx.text(operand), ev, fn, cls, first
        )
    return None


def _count_params(fn: TSNode) -> int:
    params = fn.child_by_field_name("parameters")
    if params is None:
        return 0
    count = 0
    for c in params.children:
        if c.type == "parameter_declaration":
            # `a, b int` declares two; an unnamed `int` declares one.
            count += max(1, sum(1 for x in c.children if x.type == "identifier"))
        elif c.type == "variadic_parameter_declaration":
            count += 1
    return count


METRICS = MetricsSpec(
    function_types=frozenset({"function_declaration", "method_declaration", "func_literal"}),
    branch_types=frozenset(
        {"if_statement", "for_statement", "expression_case", "type_case", "communication_case"}
    ),
    nesting_types=frozenset(
        {
            "if_statement",
            "for_statement",
            "expression_switch_statement",
            "type_switch_statement",
            "select_statement",
        }
    ),
    count_params=_count_params,
    boolean_ops=(("binary_expression", frozenset({"&&", "||"})),),
    else_if_parents=frozenset({"if_statement"}),
)


PACK = Pack(
    lang="go",
    grammar=Grammar(
        distribution="tree-sitter-go",
        version="0.25.0",
        module="tree_sitter_go",
        default="language",
    ),
    maturity=Maturity.STABLE,
    metrics=METRICS,
    rules={
        "import_spec": Import(hook=import_),
        "type_spec": Custom(hook=type_spec),
        "function_declaration": Define(
            kind="function",
            records_class=False,
            keeps_decorators=False,
            exported_by=lambda _ctx, _node, name: _exported(name),
        ),
        "method_declaration": Custom(hook=method_declaration),
        "call_expression": Call(hook=call),
    },
    qualified_prefix=lambda path: path.rsplit(".", 1)[0].replace("/", "."),
    modules=GoModules,
)
