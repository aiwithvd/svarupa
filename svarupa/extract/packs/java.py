"""Java language pack.

Every file declares its package, so imports resolve through those
declarations (modules.JvmPackages) and source roots never need guessing.
Annotations live inside `modifiers`; `public` there is what exported means.
An unqualified call inside a class is a call on `this`, unless the file
statically imported that name.
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
from svarupa.extract.packs.modules import JvmPackages
from svarupa.extract.packs.walker import Ctx, Frame

__all__ = ["PACK"]


def _modifiers(node: TSNode) -> TSNode | None:
    return next((c for c in node.children if c.type == "modifiers"), None)


def _public(_ctx: Ctx, node: TSNode, _name: str) -> bool:
    mods = _modifiers(node)
    return mods is not None and any(c.type == "public" for c in mods.children)


def _string(ctx: Ctx, node: TSNode) -> str | None:
    """A static string literal, escapes kept in source spelling (as in TS)."""
    if node.type != "string_literal":
        return None
    return "".join(
        ctx.text(c) for c in node.children if c.type in ("string_fragment", "escape_sequence")
    )


def _type_name(text: str) -> str:
    """`List<Order>` -> `List`; `a.b.Order` -> `Order`."""
    return text.split("<")[0].split("[")[0].split(".")[-1].strip()


def annotations_of(ctx: Ctx, node: TSNode) -> tuple[DecoratorRef, ...]:
    mods = _modifiers(node)
    if mods is None:
        return ()
    out: list[DecoratorRef] = []
    for child in mods.children:
        if child.type not in ("annotation", "marker_annotation"):
            continue
        name_node = child.child_by_field_name("name")
        if name_node is None:
            continue
        arg: str | None = None
        dynamic = False
        args = child.child_by_field_name("arguments")
        if args is not None:
            for a in args.children:
                if a.type in ("(", ")", ",", "comment"):
                    continue
                arg = _string(ctx, a)
                dynamic = arg is None
                break
        out.append(
            DecoratorRef(
                name=ctx.text(name_node),
                arg=arg,
                evidence=ctx.node_evidence(child),
                arg_dynamic=dynamic,
            )
        )
    return tuple(out)


def package_declaration(ctx: Ctx, node: TSNode, _frame: Frame, _depth: int) -> None:
    name = next(
        (c for c in node.children if c.type in ("scoped_identifier", "identifier")), None
    )
    if name is not None:
        ctx.namespace = ctx.text(name)


def import_(ctx: Ctx, node: TSNode) -> ImportRef | None:
    path = next(
        (c for c in node.children if c.type in ("scoped_identifier", "identifier")), None
    )
    if path is None:
        return None
    dotted = ctx.text(path)
    static = any(c.type == "static" for c in node.children)
    if any(c.type == "asterisk" for c in node.children):
        return ImportRef(
            specifier=dotted,
            names=(),
            alias_of={},
            evidence=ctx.node_evidence(node),
            is_from=True,
            is_star=True,
        )
    head, _, last = dotted.rpartition(".")
    return ImportRef(
        # `import a.b.C` names the class; `import static a.b.C.m` names a
        # member of C, so the module is the class and the name is the member.
        specifier=head if static and head else dotted,
        names=(last,),
        alias_of={},
        evidence=ctx.node_evidence(node),
        is_from=True,
    )


def bases(ctx: Ctx, node: TSNode) -> tuple[str, ...]:
    out: list[str] = []
    for child in node.children:
        if child.type in ("superclass", "super_interfaces", "extends_interfaces"):
            for t in _type_nodes(child):
                out.append(_type_name(ctx.text(t)))
    return tuple(b for b in out if b)


def _type_nodes(node: TSNode) -> list[TSNode]:
    types = ("type_identifier", "generic_type", "scoped_type_identifier")
    found: list[TSNode] = []
    for child in node.children:
        if child.type in types:
            found.append(child)
        elif child.type == "type_list":
            found.extend(c for c in child.children if c.type in types)
    return found


def declared_field(ctx: Ctx, node: TSNode, cls: str) -> list[FieldType]:
    ty = node.child_by_field_name("type")
    declarator = node.child_by_field_name("declarator")
    name = declarator.child_by_field_name("name") if declarator is not None else None
    if ty is None or name is None:
        return []
    type_name = _type_name(ctx.text(ty))
    return [FieldType(cls, ctx.text(name), type_name)] if type_name else []


def _first_str_arg(ctx: Ctx, node: TSNode) -> str | None:
    args = node.child_by_field_name("arguments")
    if args is None:
        return None
    for child in args.children:
        if child.type in ("(", ")", ",", "comment"):
            continue
        return _string(ctx, child)
    return None


def call(ctx: Ctx, node: TSNode, frame: Frame) -> CallSite | None:
    name_node = node.child_by_field_name("name")
    if name_node is None:
        return None
    name = ctx.text(name_node)
    row = name_node.start_point[0]
    ev = ctx.evidence(row, row)
    fn, cls = frame.fn, frame.cls
    first = _first_str_arg(ctx, node)
    obj = node.child_by_field_name("object")
    if obj is None:
        imported = {n for imp in ctx.imports for n in imp.names}
        if cls and name not in imported:
            return CallSite(name, CallShape.SELF, "this", ev, fn, cls, first)
        return CallSite(name, CallShape.BARE, None, ev, fn, cls, first)
    if obj.type == "this":
        return CallSite(name, CallShape.SELF, "this", ev, fn, cls, first)
    if obj.type == "super":
        return CallSite(name, CallShape.SUPER, "super", ev, fn, cls, first)
    if obj.type == "field_access":
        target = obj.child_by_field_name("object")
        field = obj.child_by_field_name("field")
        if target is not None and target.type == "this" and field is not None:
            return CallSite(name, CallShape.SELF_FIELD, ctx.text(field), ev, fn, cls, first)
    if obj.type == "identifier":
        return CallSite(name, CallShape.QUALIFIED, ctx.text(obj), ev, fn, cls, first)
    return CallSite(name, CallShape.MEMBER, ctx.text(obj), ev, fn, cls, first)


def _type_rule(kind: str) -> Define:
    return Define(
        kind=kind,
        sets_class=True,
        bases=bases,
        exported_by=_public,
        decorators_from=annotations_of,
    )


PACK = Pack(
    lang="java",
    grammar=Grammar(
        distribution="tree-sitter-java",
        version="0.23.5",
        module="tree_sitter_java",
        default="language",
    ),
    maturity=Maturity.EXPERIMENTAL,
    rules={
        "package_declaration": Custom(hook=package_declaration),
        "import_declaration": Import(hook=import_),
        "class_declaration": _type_rule("class"),
        "enum_declaration": _type_rule("class"),
        "record_declaration": _type_rule("class"),
        "interface_declaration": _type_rule("interface"),
        "method_declaration": Define(
            kind="method", exported_by=_public, decorators_from=annotations_of
        ),
        "constructor_declaration": Define(
            kind="method", exported_by=_public, decorators_from=annotations_of
        ),
        "field_declaration": Field(hook=declared_field),
        "method_invocation": Call(hook=call),
    },
    qualified_prefix=lambda path: path.rsplit(".", 1)[0].replace("/", "."),
    modules=JvmPackages,
)
