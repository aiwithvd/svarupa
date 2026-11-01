"""Rust language pack (experimental).

A file is a module, so the qualified prefix is the module path from the
crate root (`src/a/b.rs` is `a.b`), and an inline `mod x { }` extends it.
Methods are declared in `impl` blocks, not inside the type, so the type an
impl names (not lexical nesting) owns its functions, the same as a Go
receiver. `impl Trait for Type` records `Trait` as a base on each method it
declares, and the resolver treats that as a base of `Type` (see
resolve._Index.extra_bases), which works when the impl and the struct live in
different files. Macros are not calls. `pub` is the export rule, and the
methods of a trait or of a trait impl are public without saying so.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

from tree_sitter import Node as TSNode

from svarupa.extract.base import (
    CallShape,
    CallSite,
    DecoratorRef,
    FieldType,
    ImportRef,
)
from svarupa.extract.packs.model import (
    Call,
    Custom,
    Define,
    Field,
    Grammar,
    Maturity,
    Pack,
)
from svarupa.extract.packs.walker import Ctx, Frame
from svarupa.model import Evidence

__all__ = ["BUILTINS", "PACK"]

# Enum constructors of the prelude: calls to them are not intra-repo calls.
BUILTINS = frozenset({"Some", "Ok", "Err", "drop"})

# Pointer and cell types that hold a value: `Arc<Repo>` is a field of `Repo`.
_HOLDERS = frozenset({"Arc", "Rc", "Box", "Option", "Mutex", "RwLock", "RefCell", "Cell"})
_COMMENTS = ("line_comment", "block_comment")
_PUNCT = ("(", ")", ",", *_COMMENTS)


def _text(node: TSNode) -> str:
    return node.text.decode("utf8", "replace") if node.text else ""


def _dotted(path: str) -> str:
    return ".".join(part.strip() for part in path.split("::") if part.strip())


def _join(base: str, tail: str) -> str:
    return f"{base}.{tail}" if base and tail else base or tail


def qualified_prefix(path: str) -> str:
    """The module path of a file: `src/a/mod.rs` is `a`, the crate root is ``.

    A file in a workspace member (`crates/shop-core/src/db.rs`) is prefixed
    with the crate directory, `-` written as `_` as Rust spells the crate.
    """
    parts = path.removesuffix(".rs").split("/")
    crate: list[str] = []
    if "src" in parts:
        at = parts.index("src")
        crate = [parts[at - 1].replace("-", "_")] if at > 0 else []
        parts = parts[at + 1 :]
        if parts in (["lib"], ["main"]):
            parts = []
    if parts and parts[-1] == "mod":
        parts = parts[:-1]
    return ".".join([*crate, *parts])


# ----------------------------------------------------------------------
# types


def _first_type_arg(generic: TSNode) -> TSNode | None:
    args = generic.child_by_field_name("type_arguments")
    if args is None:
        return None
    return next((c for c in args.named_children if c.type != "lifetime"), None)


def _type_name(ty: TSNode | None, unwrap: bool) -> str | None:
    """`Shop`, `crate::a::Shop`, `&Shop` and `Shop<T>` name `Shop`. With
    `unwrap`, `Arc<Mutex<Shop>>` does too: that is a field, not an impl."""
    if ty is None:
        return None
    if ty.type == "type_identifier":
        return _text(ty)
    if ty.type == "scoped_type_identifier":
        return _type_name(ty.child_by_field_name("name"), unwrap)
    if ty.type == "reference_type":
        return _type_name(ty.child_by_field_name("type"), unwrap)
    if ty.type == "dynamic_type":
        return _type_name(ty.child_by_field_name("trait"), unwrap)
    if ty.type == "generic_type":
        name = _type_name(ty.child_by_field_name("type"), unwrap)
        if unwrap and name in _HOLDERS:
            return _type_name(_first_type_arg(ty), unwrap)
        return name
    return None


def _has_visibility(node: TSNode) -> bool:
    return any(c.type == "visibility_modifier" for c in node.children)


def _in_trait(node: TSNode) -> bool:
    """A member of a trait, or of an `impl Trait for Type`: public by the trait."""
    body = node.parent
    owner = body.parent if body is not None else None
    if owner is None:
        return False
    return owner.type == "trait_item" or (
        owner.type == "impl_item" and owner.child_by_field_name("trait") is not None
    )


def exported(_ctx: Ctx, node: TSNode, _name: str) -> bool:
    return _has_visibility(node) or _in_trait(node)


def impl_trait(_ctx: Ctx, node: TSNode) -> tuple[str, ...]:
    """The trait of the `impl Trait for Type` a method sits in."""
    body = node.parent
    impl = body.parent if body is not None else None
    if impl is None or impl.type != "impl_item":
        return ()
    name = _type_name(impl.child_by_field_name("trait"), False)
    return (name,) if name else ()


def field_types(_ctx: Ctx, node: TSNode, cls: str) -> list[FieldType]:
    name = node.child_by_field_name("name")
    group = node.parent.parent if node.parent is not None else None
    if name is None or group is None or group.type not in ("struct_item", "union_item"):
        return []
    type_name = _type_name(node.child_by_field_name("type"), True)
    return [FieldType(cls, _text(name), type_name)] if type_name else []


# ----------------------------------------------------------------------
# attributes


def _string(node: TSNode) -> str | None:
    """A plain string literal's content, escapes kept in source spelling."""
    if node.type != "string_literal":
        return None
    return "".join(_text(c) for c in node.children if c.type == "string_content")


def _attribute_arg(args: TSNode | None) -> tuple[str | None, bool]:
    """(first string argument, whether an argument exists that is not one)."""
    if args is None:
        return None, False
    first = next((c for c in args.children if c.type not in _PUNCT), None)
    if first is None:
        return None, False
    value = _string(first)
    return value, value is None


def decorator(ctx: Ctx, node: TSNode) -> DecoratorRef | None:
    attr = next((c for c in node.children if c.type == "attribute"), None)
    named = attr.named_children[0] if attr is not None and attr.named_children else None
    if attr is None or named is None:
        return None
    arg, dynamic = _attribute_arg(attr.child_by_field_name("arguments"))
    return DecoratorRef(
        name=_text(named).rsplit("::", 1)[-1],
        arg=arg,
        evidence=ctx.node_evidence(node),
        arg_dynamic=dynamic,
    )


def visit_items(ctx: Ctx, children: list[TSNode], frame: Frame, depth: int) -> None:
    """Walk items; an `attribute_item` decorates the item that follows it."""
    pending: list[DecoratorRef] = []
    for child in children:
        if child.type == "attribute_item":
            if (d := ctx.decorator(child)) is not None:
                pending.append(d)
            continue
        ctx.visit(child, replace(frame, decorators=tuple(pending)), depth + 1)
        pending = []


def source_file(ctx: Ctx, node: TSNode, frame: Frame, depth: int) -> None:
    visit_items(ctx, node.children, frame, depth)


# ----------------------------------------------------------------------
# impl and mod


def impl_item(ctx: Ctx, node: TSNode, frame: Frame, depth: int) -> None:
    body = node.child_by_field_name("body")
    if body is None:
        return
    owner = _type_name(node.child_by_field_name("type"), False)
    stack = (*frame.stack, owner) if owner else frame.stack
    visit_items(ctx, body.children, Frame(stack, owner, frame.fn), depth)


def mod_item(ctx: Ctx, node: TSNode, frame: Frame, depth: int) -> None:
    name = node.child_by_field_name("name")
    if name is None:
        return
    body = node.child_by_field_name("body")
    if body is None:  # `mod x;` names the child module file
        ctx.imports.append(_ref(f"self.{_text(name)}", (), {}, ctx.node_evidence(node)))
        return
    outer = ctx.prefix
    ctx.prefix = _join(outer, _text(name))
    visit_items(ctx, body.children, frame.plain(), depth)
    ctx.prefix = outer


# ----------------------------------------------------------------------
# use


def _ref(
    spec: str,
    names: tuple[str, ...],
    alias_of: dict[str, str],
    evidence: Evidence,
    star: bool = False,
) -> ImportRef:
    parts = spec.split(".")
    supers = 0
    for part in parts:
        if part != "super":
            break
        supers += 1
    return ImportRef(
        specifier=spec,
        names=names,
        alias_of=alias_of,
        evidence=evidence,
        is_relative=parts[0] in ("self", "super"),
        level=supers,
        is_from=True,
        is_star=star,
    )


def _aliased(node: TSNode, base: str, ev: Evidence) -> ImportRef | None:
    """`a::b as c` imports `b` from `a` under the name `c`."""
    path, alias = node.child_by_field_name("path"), node.child_by_field_name("alias")
    if path is None or alias is None:
        return None
    head, _, last = _dotted(_text(path)).rpartition(".")
    renamed = {} if _text(alias) == "_" else {_text(alias): last}
    if not head and not base:
        return _ref(last, (), renamed, ev)
    return _ref(_join(base, head), (last,), renamed, ev)


def _list_item(item: TSNode, full: str, ev: Evidence) -> list[ImportRef]:
    if item.type == "identifier":
        return [_ref(full, (_text(item),), {}, ev)]
    if item.type == "self":
        return [_ref(full, (), {}, ev)]
    return _refs(item, full, ev)


def _merged(full: str, same: list[ImportRef], ev: Evidence) -> ImportRef:
    """One import of `full` naming everything its list items name."""
    names = tuple(n for r in same for n in r.names)
    return _ref(full, names, {k: v for r in same for k, v in r.alias_of.items()}, ev)


def _group(node: TSNode, base: str, ev: Evidence) -> list[ImportRef]:
    """`a::{b, c as d, e::f}`: one import of `a` naming b and c, one of `a.e.f`."""
    path = node.child_by_field_name("path")
    lst = node.child_by_field_name("list")
    full = _join(base, _dotted(_text(path))) if path is not None else base
    refs: list[ImportRef] = []
    for item in lst.named_children if lst is not None else ():
        refs.extend(_list_item(item, full, ev))
    return _head_first(full, refs, ev)


def _head_first(full: str, refs: list[ImportRef], ev: Evidence) -> list[ImportRef]:
    own = [r for r in refs if r.specifier == full and not r.is_star]
    rest = [r for r in refs if r not in own]
    return rest if rest and not own else [_merged(full, own, ev), *rest]


def _refs(node: TSNode, base: str, ev: Evidence) -> list[ImportRef]:
    if node.type in _COMMENTS:
        return []
    if node.type == "scoped_use_list":
        return _group(node, base, ev)
    if node.type == "use_as_clause":
        ref = _aliased(node, base, ev)
        return [ref] if ref is not None else []
    if node.type == "use_wildcard":
        path = _dotted(_text(node).removesuffix("*").rstrip(":"))
        return [_ref(_join(base, path), (), {}, ev, star=True)]
    return [_ref(_join(base, _dotted(_text(node))), (), {}, ev)]


def use_declaration(ctx: Ctx, node: TSNode, _frame: Frame, _depth: int) -> None:
    arg = node.child_by_field_name("argument")
    if arg is not None:
        ctx.imports.extend(_refs(arg, "", ctx.node_evidence(node)))


# ----------------------------------------------------------------------
# calls


def _first_str_arg(args: TSNode | None) -> str | None:
    if args is None:
        return None
    first = next((c for c in args.children if c.type not in _PUNCT), None)
    return _string(first) if first is not None else None


def _scoped_receiver(path: TSNode) -> str:
    """`Shop::new` is on `Shop`; `a::b::f` is on `b`."""
    if path.type == "scoped_identifier":
        name = path.child_by_field_name("name")
        return _text(name) if name is not None else _text(path)
    if path.type == "generic_type":
        inner = path.child_by_field_name("type")
        return _text(inner) if inner is not None else _text(path)
    return _text(path)


_Site = Callable[[TSNode, CallShape, str | None], CallSite]


def _method_site(
    func: TSNode, site: Callable[[TSNode, CallShape, str | None], CallSite]
) -> CallSite | None:
    value, field = func.child_by_field_name("value"), func.child_by_field_name("field")
    if value is None or field is None:
        return None
    if value.type == "self":
        return site(field, CallShape.SELF, "self")
    inner = value.child_by_field_name("value") if value.type == "field_expression" else None
    if inner is not None and inner.type == "self":
        owned = value.child_by_field_name("field")
        return site(field, CallShape.SELF_FIELD, _text(owned) if owned is not None else None)
    if value.type == "identifier":
        return site(field, CallShape.QUALIFIED, _text(value))
    return site(field, CallShape.MEMBER, _text(value))


def _callee(node: TSNode) -> TSNode | None:
    func = node.child_by_field_name("function")
    if func is not None and func.type == "generic_function":
        return func.child_by_field_name("function")  # `parse::<u8>()`
    return func


def _path_site(func: TSNode, site: _Site) -> CallSite | None:
    name, path = func.child_by_field_name("name"), func.child_by_field_name("path")
    if name is None or path is None:
        return None
    receiver = _scoped_receiver(path)
    return site(name, CallShape.SELF if receiver == "Self" else CallShape.QUALIFIED, receiver)


def call(ctx: Ctx, node: TSNode, frame: Frame) -> CallSite | None:
    func = _callee(node)
    if func is None:
        return None
    first = _first_str_arg(node.child_by_field_name("arguments"))

    def site(target: TSNode, shape: CallShape, receiver: str | None) -> CallSite:
        row = target.start_point[0]
        return CallSite(
            _text(target), shape, receiver, ctx.evidence(row, row), frame.fn, frame.cls, first
        )

    if func.type == "identifier":
        return None if _text(func) in BUILTINS else site(func, CallShape.BARE, None)
    if func.type == "field_expression":
        return _method_site(func, site)
    return _path_site(func, site) if func.type == "scoped_identifier" else None


_CLASS = Define(kind="class", sets_class=True, exported_by=exported, member_decorators=None)
_METHOD = Define(
    kind="function",
    kind_in_class="method",
    exported_by=exported,
    bases=impl_trait,
)

PACK = Pack(
    lang="rust",
    grammar=Grammar(
        distribution="tree-sitter-rust",
        version="0.24.2",
        module="tree_sitter_rust",
        default="language",
    ),
    maturity=Maturity.EXPERIMENTAL,
    qualified_prefix=qualified_prefix,
    decorator=decorator,
    rules={
        "source_file": Custom(hook=source_file),
        "use_declaration": Custom(hook=use_declaration),
        "mod_item": Custom(hook=mod_item),
        "impl_item": Custom(hook=impl_item),
        "struct_item": _CLASS,
        "enum_item": _CLASS,
        "union_item": _CLASS,
        "trait_item": Define(
            kind="interface",
            sets_class=True,
            exported_by=exported,
            member_decorators="attribute_item",
        ),
        "function_item": _METHOD,
        "function_signature_item": _METHOD,
        "field_declaration": Field(hook=field_types),
        "call_expression": Call(hook=call),
    },
)
