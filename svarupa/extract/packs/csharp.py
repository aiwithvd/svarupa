"""C# language pack (experimental).

A namespace replaces the path-based prefix: `namespace Shop.Api;` and
`namespace Shop.Api { ... }` qualify their types the same way. Attributes
are `attribute_list` children of a declaration, and `public` is a
`modifier` child. An unqualified call inside a type is a call on `this`.
"""

from __future__ import annotations

from tree_sitter import Node as TSNode

from svarupa.extract.base import CallShape, CallSite, DecoratorRef, FieldType, ImportRef
from svarupa.extract.packs.model import (
    Call,
    Custom,
    Define,
    Field,
    Grammar,
    Import,
    Maturity,
    Pack,
)
from svarupa.extract.packs.modules import CSharpNamespaces
from svarupa.extract.packs.walker import Ctx, Frame

__all__ = ["PACK"]

_NAMES = ("qualified_name", "identifier", "alias_qualified_name")
_SKIP = ("(", ")", ",", "comment")


def _public(_ctx: Ctx, node: TSNode, _name: str) -> bool:
    if any(c.type == "modifier" and c.text == b"public" for c in node.children):
        return True
    body = node.parent  # interface members are public without saying so
    return (
        node.type != "interface_declaration"
        and body is not None
        and body.parent is not None
        and body.parent.type == "interface_declaration"
    )


def _string(ctx: Ctx, node: TSNode) -> str | None:
    """A plain string literal's content, escapes kept in source spelling."""
    if node.type != "string_literal":
        return None
    return "".join(ctx.text(c) for c in node.children if c.type == "string_literal_content")


def _type_name(text: str) -> str:
    """`List<Order>` -> `List`; `a.b.Order` -> `Order`."""
    return text.split("<")[0].split("[")[0].split(".")[-1].strip()


def _first_arg(ctx: Ctx, args: TSNode | None) -> tuple[str | None, bool]:
    """(string value, dynamic) of the first argument of an attribute or call."""
    if args is None:
        return None, False
    for child in args.children:
        if child.type in _SKIP:
            continue
        if child.type == "attribute_argument":
            if child.child_by_field_name("name") is not None:
                return None, False  # `Roles = "A"` is not the first positional value
            inner = child.children[-1]
        else:
            inner = child
        value = _string(ctx, inner)
        return value, value is None
    return None, False


def _first_string(ctx: Ctx, args: TSNode | None) -> str | None:
    return _first_arg(ctx, args)[0]


def _attribute(ctx: Ctx, node: TSNode) -> DecoratorRef | None:
    name = node.child_by_field_name("name")
    if name is None:
        return None
    args = next((c for c in node.children if c.type == "attribute_argument_list"), None)
    arg, dynamic = _first_arg(ctx, args)
    return DecoratorRef(
        name=ctx.text(name).rsplit(".", 1)[-1],
        arg=arg,
        evidence=ctx.node_evidence(node),
        arg_dynamic=dynamic,
    )


def attributes_of(ctx: Ctx, node: TSNode) -> tuple[DecoratorRef, ...]:
    found = (
        _attribute(ctx, attr)
        for lst in node.children
        if lst.type == "attribute_list"
        for attr in lst.children
        if attr.type == "attribute"
    )
    return tuple(d for d in found if d is not None)


def _walk_children(ctx: Ctx, node: TSNode, frame: Frame, depth: int) -> None:
    for child in node.children:
        ctx.visit(child, frame, depth + 1)


def _full_name(ctx: Ctx, node: TSNode, name: TSNode) -> str:
    """A block namespace's name joined with the block namespaces around it."""
    parts = [ctx.text(name)]
    up = node.parent
    while up is not None:
        outer = up.child_by_field_name("name") if up.type == "namespace_declaration" else None
        if outer is not None:
            parts.append(ctx.text(outer))
        up = up.parent
    return ".".join(reversed(parts))


def namespace_declaration(ctx: Ctx, node: TSNode, frame: Frame, depth: int) -> None:
    name = node.child_by_field_name("name")
    if name is None:
        _walk_children(ctx, node, frame, depth)
        return
    outer = ctx.prefix
    block = node.type == "namespace_declaration"
    full = _full_name(ctx, node, name) if block else ctx.text(name)
    ctx.namespace = ctx.namespace or full
    if full not in ctx.namespaces:
        ctx.namespaces.append(full)
    ctx.prefix = full
    if not block:
        return  # file scoped: what follows in the file belongs to it
    body = node.child_by_field_name("body")
    if body is not None:
        _walk_children(ctx, body, frame, depth + 1)
    ctx.prefix = outer


def import_(ctx: Ctx, node: TSNode) -> ImportRef | None:
    # `using Db = A.B;` has a `name` field for the alias; the target is the
    # last name child. `using var x = ...;` statements have no name here.
    path = next((c for c in reversed(node.children) if c.type in _NAMES), None)
    if path is None or node.parent is None or node.parent.type == "block":
        return None
    dotted = ctx.text(path).split("<")[0]
    return ImportRef(
        specifier=dotted,
        names=(),
        alias_of={},
        evidence=ctx.node_evidence(node),
        is_from=True,
    )


def _base_type(node: TSNode) -> TSNode:
    """`Base(x)` in a primary constructor base list names `Base`."""
    if node.type == "primary_constructor_base_type":
        return node.child_by_field_name("type") or node
    return node


def bases(ctx: Ctx, node: TSNode) -> tuple[str, ...]:
    base_list = next((c for c in node.children if c.type == "base_list"), None)
    if base_list is None:
        return ()
    out = (
        _type_name(ctx.text(_base_type(c)))
        for c in base_list.children
        if c.is_named and c.type not in ("comment", "argument_list")
    )
    return tuple(b for b in out if b)


def _target(function: TSNode) -> TSNode | None:
    """The identifier naming the method called, past generic arguments."""
    if function.type == "generic_name":
        return next((c for c in function.children if c.type == "identifier"), None)
    return function


def _member_call(
    ctx: Ctx, target: TSNode, obj: TSNode, frame: Frame, first: str | None
) -> CallSite:
    name = ctx.text(target)
    row = target.start_point[0]
    ev = ctx.evidence(row, row)
    fn, cls = frame.fn, frame.cls
    if obj.type == "this":
        return CallSite(name, CallShape.SELF, "this", ev, fn, cls, first)
    if obj.type == "base":
        return CallSite(name, CallShape.SUPER, "base", ev, fn, cls, first)
    if obj.type == "member_access_expression":
        inner = obj.child_by_field_name("expression")
        field = obj.child_by_field_name("name")
        if inner is not None and inner.type == "this" and field is not None:
            return CallSite(name, CallShape.SELF_FIELD, ctx.text(field), ev, fn, cls, first)
    if obj.type == "identifier":
        return CallSite(name, CallShape.QUALIFIED, ctx.text(obj), ev, fn, cls, first)
    return CallSite(name, CallShape.MEMBER, ctx.text(obj), ev, fn, cls, first)


def _split_function(function: TSNode) -> tuple[TSNode | None, TSNode]:
    """(receiver, name node) of the called expression."""
    if function.type == "member_access_expression":
        return function.child_by_field_name("expression"), (
            function.child_by_field_name("name") or function
        )
    if function.type == "conditional_access_expression":  # `x?.Foo()`
        binding = function.children[-1]
        name = binding.child_by_field_name("name")
        if binding.type == "member_binding_expression" and name is not None:
            return function.child_by_field_name("condition"), name
    return None, function


def invocation(ctx: Ctx, node: TSNode, frame: Frame) -> CallSite | None:
    function = node.child_by_field_name("function")
    if function is None:
        return None
    first = _first_string(ctx, node.child_by_field_name("arguments"))
    obj, named = _split_function(function)
    target = _target(named)
    if target is None or target.type != "identifier":
        return None
    name = ctx.text(target)
    row = target.start_point[0]
    ev = ctx.evidence(row, row)
    if obj is not None:
        return _member_call(ctx, target, obj, frame, first)
    if frame.cls:
        return CallSite(name, CallShape.SELF, "this", ev, frame.fn, frame.cls, first)
    return CallSite(name, CallShape.BARE, None, ev, frame.fn, frame.cls, first)


def creation(ctx: Ctx, node: TSNode, frame: Frame) -> CallSite | None:
    ty = node.child_by_field_name("type")
    if ty is None:
        return None
    name = _type_name(ctx.text(ty))
    row = ty.start_point[0]
    first = _first_string(ctx, node.child_by_field_name("arguments"))
    return CallSite(
        name, CallShape.BARE, None, ctx.evidence(row, row), frame.fn, frame.cls, first
    )


def _type_rule(kind: str) -> Define:
    return Define(
        kind=kind,
        sets_class=True,
        bases=bases,
        exported_by=_public,
        decorators_from=attributes_of,
    )


def _declarator_name(node: TSNode) -> TSNode | None:
    return node.child_by_field_name("name") or next(
        (c for c in node.children if c.type == "identifier"), None
    )


def _typed_names(node: TSNode) -> tuple[TSNode | None, list[TSNode | None]]:
    """(type node, name nodes) of a field or property declaration."""
    if node.type == "property_declaration":
        return node.child_by_field_name("type"), [node.child_by_field_name("name")]
    decl = next((c for c in node.children if c.type == "variable_declaration"), None)
    if decl is None:
        return None, []
    return decl.child_by_field_name("type"), [
        _declarator_name(d) for d in decl.children if d.type == "variable_declarator"
    ]


def declared_field(ctx: Ctx, node: TSNode, cls: str) -> list[FieldType]:
    """`IRepo _repo;` and `List<Line> Lines { get; }` give the member its type."""
    ty, names = _typed_names(node)
    type_name = _type_name(ctx.text(ty)) if ty is not None else ""
    if not type_name:
        return []
    return [FieldType(cls, ctx.text(n), type_name) for n in names if n is not None]


def _initializer(ctx: Ctx, node: TSNode, frame: Frame, name: str) -> None:
    """`C() : base(Init())`: Define walks only the body, so walk this too."""
    init = next((c for c in node.children if c.type == "constructor_initializer"), None)
    if init is not None:
        inner = Frame((*frame.stack, name), frame.cls, ctx.qual((*frame.stack, name)))
        ctx.visit(init, inner, 1)


def _member_rule() -> Define:
    return Define(
        kind="function",
        kind_in_class="method",
        exported_by=_public,
        decorators_from=attributes_of,
        after=_initializer,
    )


PACK = Pack(
    lang="csharp",
    grammar=Grammar(
        distribution="tree-sitter-c-sharp",
        version="0.23.5",
        module="tree_sitter_c_sharp",
        default="language",
    ),
    maturity=Maturity.EXPERIMENTAL,
    rules={
        "using_directive": Import(hook=import_),
        "namespace_declaration": Custom(hook=namespace_declaration),
        "file_scoped_namespace_declaration": Custom(hook=namespace_declaration),
        "class_declaration": _type_rule("class"),
        "struct_declaration": _type_rule("class"),
        "record_declaration": _type_rule("class"),
        "enum_declaration": _type_rule("class"),
        "interface_declaration": _type_rule("interface"),
        "method_declaration": _member_rule(),
        "constructor_declaration": _member_rule(),
        "local_function_statement": _member_rule(),
        "field_declaration": Field(hook=declared_field),
        "property_declaration": Field(hook=declared_field),
        "invocation_expression": Call(hook=invocation),
        "object_creation_expression": Call(hook=creation),
    },
    modules=CSharpNamespaces,
    qualified_prefix=lambda path: path.rsplit(".", 1)[0].replace("/", "."),
)
