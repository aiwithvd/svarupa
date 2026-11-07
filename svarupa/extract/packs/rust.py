"""Rust language pack.

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

import re
from collections.abc import Callable
from dataclasses import replace

from tree_sitter import Node as TSNode

from svarupa.extract.base import (
    CallShape,
    CallSite,
    DecoratorRef,
    FieldType,
    ImportRef,
    LocalType,
)
from svarupa.extract.packs.model import (
    Call,
    Custom,
    Define,
    Field,
    Grammar,
    Local,
    Maturity,
    MetricsSpec,
    Pack,
    RiskSpec,
    Sink,
    Unsafe,
)
from svarupa.extract.packs.modules import RustCrates
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
# parameter and local types

# Wrappers that hand out the value they hold: `Arc<AppState>` is used as an
# `AppState`, so a call on it resolves on `AppState`.
_LOCAL_WRAPPERS = frozenset(
    {"Arc", "Rc", "Box", "Data", "State", "Extension", "Json", "Path", "Query", "Form"}
)


def _generic_names(owner: TSNode | None) -> set[str]:
    """The type parameters a function, impl or trait declares: `S` in `<S: Store>`."""
    params = owner.child_by_field_name("type_parameters") if owner is not None else None
    names: set[str] = set()
    for p in params.named_children if params is not None else ():
        if p.type == "type_parameter":
            name = p.child_by_field_name("name")
        elif p.type == "constrained_type_parameter":
            name = p.child_by_field_name("left")
        else:
            continue
        if name is not None:
            names.add(_text(name))
    return names


def _enclosing_fn(node: TSNode) -> TSNode | None:
    cur = node.parent
    while cur is not None and cur.type != "function_item":
        cur = cur.parent
    return cur


def _in_generics(fn: TSNode) -> set[str]:
    body = fn.parent
    owner = body.parent if body is not None and body.type == "declaration_list" else None
    return _generic_names(fn) | _generic_names(owner)


def _local_type(ty: TSNode | None, generics: set[str]) -> str | None:
    """The class a declared type names, seen through `&`, `Arc<..>` and the
    like; None for a generic parameter, a tuple, `impl Trait` or a bare path."""
    while ty is not None and ty.type == "generic_type":
        inner = _type_name(ty.child_by_field_name("type"), False)
        if inner not in _LOCAL_WRAPPERS:
            break
        ty = _first_type_arg(ty)
        while ty is not None and ty.type == "reference_type":
            ty = ty.child_by_field_name("type")
    name = _type_name(ty, False)
    return None if name is None or name in generics else name


def _bound_name(pattern: TSNode | None) -> str | None:
    return _text(pattern) if pattern is not None and pattern.type == "identifier" else None


_BINDERS = frozenset({"identifier", "shorthand_field_identifier"})


def _bound_names(pattern: TSNode | None) -> list[str]:
    """Every name a pattern may bind. An enum or struct name inside it is
    included too: that only hides a name, never invents an edge."""
    if pattern is None:
        return []
    names = [_text(pattern)] if pattern.type in _BINDERS else []
    for child in pattern.children:
        names += _bound_names(child)
    return names


def _is_known(ctx: Ctx, fn: str, name: str) -> bool:
    return any(x.function == fn and x.name == name and x.type_name for x in ctx.locals)


def _typed(
    fn: str, pattern: TSNode | None, ty: TSNode | None, generics: set[str]
) -> list[LocalType]:
    name = _bound_name(pattern)
    type_name = _local_type(ty, generics)
    return [LocalType(fn, name, type_name)] if name and type_name else []


def parameter_locals(_ctx: Ctx, node: TSNode, qualified: str) -> list[LocalType]:
    params = node.child_by_field_name("parameters")
    if params is None:
        return []
    generics = _in_generics(node)
    out: list[LocalType] = []
    for p in params.named_children:
        if p.type == "parameter":
            out += _typed(
                qualified,
                p.child_by_field_name("pattern"),
                p.child_by_field_name("type"),
                generics,
            )
    return out


def _rebinds(ctx: Ctx, fn: str, pattern: TSNode | None) -> list[LocalType]:
    """Names a pattern binds that were typed before: the type no longer holds."""
    return [LocalType(fn, n, "") for n in _bound_names(pattern) if _is_known(ctx, fn, n)]


def let_locals(ctx: Ctx, node: TSNode, fn: str) -> list[LocalType]:
    enclosing = _enclosing_fn(node)
    generics: set[str] = set() if enclosing is None else _in_generics(enclosing)
    pattern, ty = node.child_by_field_name("pattern"), node.child_by_field_name("type")
    return _typed(fn, pattern, ty, generics) or _rebinds(ctx, fn, pattern)


def pattern_locals(ctx: Ctx, node: TSNode, fn: str) -> list[LocalType]:
    """`if let`, `while let`, `for` and `match` arms bind through a pattern."""
    if node.type == "match_pattern":
        return _rebinds(ctx, fn, node)
    return _rebinds(ctx, fn, node.child_by_field_name("pattern"))


def closure_locals(ctx: Ctx, node: TSNode, fn: str) -> list[LocalType]:
    params = node.child_by_field_name("parameters")
    out: list[LocalType] = []
    for p in params.named_children if params is not None else ():
        pattern = p.child_by_field_name("pattern") if p.type == "parameter" else p
        out += _rebinds(ctx, fn, pattern)
    return out


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
    if owner is None:
        return  # `impl Tr for [T]`: no type of ours owns these methods
    visit_items(ctx, body.children, Frame((*frame.stack, owner), owner, frame.fn), depth)


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
    full = _dotted(_text(path))
    if full == "self":  # `b::{self as z}` renames `b` itself
        full = base
        base = ""
    head, _, last = full.rpartition(".")
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
    if rest and not own:
        return rest
    # `{self, X}` imports the module itself as well as X from it
    return [*_own_imports(full, own, ev), *rest]


def _own_imports(full: str, own: list[ImportRef], ev: Evidence) -> list[ImportRef]:
    itself = [r for r in own if not r.names]
    named = [r for r in own if r.names]
    if itself and named:
        return [_merged(full, itself, ev), _merged(full, named, ev)]
    return [_merged(full, own, ev)]


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


def _inline_mods(ctx: Ctx) -> list[str]:
    """The inline `mod` names that enclose the current item, outermost first.
    `mod_item` extends `ctx.prefix` past the file's own module path."""
    own = qualified_prefix(ctx.path)
    return ctx.prefix[len(own) :].strip(".").split(".") if ctx.prefix != own else []


def _in_module(ref: ImportRef, inline: list[str]) -> ImportRef:
    """Read `self` and `super` from the inline module, not from the file.

    Each leading `super` first leaves one inline module. Only the ones left
    over climb file modules. `self::x` is `x` inside the inline module."""
    parts = ref.specifier.split(".")
    if not inline or parts[0] not in ("self", "super"):
        return ref
    if parts[0] == "self":
        spec = ".".join(["self", *inline, *parts[1:]])
    else:
        supers = ref.level
        used = min(supers, len(inline))
        rest = parts[supers:]
        if supers > used:
            spec = ".".join(["super"] * (supers - used) + rest)
        else:
            spec = ".".join(["self", *inline[: len(inline) - used], *rest])
    return _ref(spec, ref.names, dict(ref.alias_of), ref.evidence, ref.is_star)


def _reexports(node: TSNode) -> bool:
    """`pub use` and `pub(crate) use` put the names in the module's own scope
    for its callers. A plain `use` and `pub(super)` or `pub(in ..)` do not."""
    vis = next((c for c in node.children if c.type == "visibility_modifier"), None)
    return vis is not None and "".join(_text(vis).split()) in ("pub", "pub(crate)")


def use_declaration(ctx: Ctx, node: TSNode, _frame: Frame, _depth: int) -> None:
    arg = node.child_by_field_name("argument")
    if arg is not None:
        inline = _inline_mods(ctx)
        refs = (_in_module(r, inline) for r in _refs(arg, "", ctx.node_evidence(node)))
        if _reexports(node) and not inline:
            refs = (replace(r, is_reexport=True) for r in refs)
        ctx.imports.extend(refs)


def extern_crate(ctx: Ctx, node: TSNode, _frame: Frame, _depth: int) -> None:
    """`extern crate foo as bar;` imports the crate `foo` under the name `bar`."""
    name, alias = node.child_by_field_name("name"), node.child_by_field_name("alias")
    if name is None or _text(name) == "self":
        return
    real = _text(name)
    renamed = {_text(alias): real} if alias is not None and _text(alias) != "_" else {}
    ctx.imports.append(_ref(real, (), renamed, ctx.node_evidence(node)))


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


def _receiver_head(value: TSNode) -> str | None:
    """A chain's receiver can span lines (an SQL body): keep the head. A cut
    call keeps `()`, so `a.b()` is not mistaken for the field path `a.b`."""
    text = _text(value)
    head = re.split(r"[(\n]", text, maxsplit=1)[0]
    if head != text and "." in head:
        head += "()"
    return head or None


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
    return site(field, CallShape.MEMBER, _receiver_head(value))


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


def _fn_owner(fn: TSNode) -> str | None:
    """The type or trait a function is declared in, or None for a free one."""
    body = fn.parent
    block = body.parent if body is not None and body.type == "declaration_list" else None
    if block is None:
        return None
    if block.type == "impl_item":
        return _type_name(block.child_by_field_name("type"), False)
    if block.type == "trait_item":
        name = block.child_by_field_name("name")
        return _text(name) if name is not None else None
    return None


def _function_name(fn: TSNode) -> str | None:
    name = fn.child_by_field_name("name")
    if fn.type != "function_item" or name is None:
        return None
    owner = _fn_owner(fn)
    return f"{owner}.{_text(name)}" if owner else None


def _count_params(fn: TSNode) -> int:
    params = fn.child_by_field_name("parameters")
    if params is None:
        return 0
    if params.type == "closure_parameters":
        return sum(1 for c in params.named_children if c.type not in _COMMENTS)
    # `self`, `&mut self` and `self: Box<Self>` (a `parameter` whose pattern
    # is the `self` keyword) are the receiver, not a parameter.
    return sum(
        1
        for c in params.named_children
        if c.type == "parameter" and (c.children[0].type != "self" if c.children else True)
    )


def _wildcard_arm(node: TSNode) -> bool:
    """`_ => ...`: the default arm. `_ if guard => ...` is still a choice."""
    pattern = next((c for c in node.children if c.type == "match_pattern"), None)
    return pattern is not None and [c.type for c in pattern.children] == ["_"]


_TEST_ATTR = re.compile(r"^#\[\s*(?:\w+::)*(?:test|rstest)\b")
_CFG_TEST = re.compile(r"^#\[\s*cfg\(\s*test\s*\)\s*\]")


def _attributes(node: TSNode) -> list[str]:
    """The text of the attributes written before an item."""
    out: list[str] = []
    prev = node.prev_named_sibling
    while prev is not None and (prev.type == "attribute_item" or prev.type in _COMMENTS):
        if prev.type == "attribute_item":
            out.append(_text(prev))
        prev = prev.prev_named_sibling
    return out


def _is_test(node: TSNode) -> bool:
    """A `#[cfg(test)]` item, or a function marked `#[test]`, `#[tokio::test]`
    or `#[rstest]`: unit tests live in the source file, not in a test file."""
    if node.type not in ("function_item", "mod_item", "impl_item"):
        return False
    attrs = _attributes(node)
    if any(_CFG_TEST.match(a) for a in attrs):
        return True
    return node.type == "function_item" and any(_TEST_ATTR.match(a) for a in attrs)


# `?` is error propagation with no branch the reader writes, so it is not
# counted. `if let` and `while let` count once, as the `if` or `while`. An
# else-if is an `if_expression` under an `else_clause`: no extra nesting.
# Trait and extern signatures have no body and are not function_types.
METRICS = MetricsSpec(
    function_types=frozenset({"function_item", "closure_expression"}),
    branch_types=frozenset(
        {"if_expression", "while_expression", "loop_expression", "for_expression", "match_arm"}
    ),
    nesting_types=frozenset(
        {
            "if_expression",
            "match_expression",
            "while_expression",
            "loop_expression",
            "for_expression",
        }
    ),
    count_params=_count_params,
    boolean_ops=(("binary_expression", frozenset({"&&", "||"})),),
    else_if_parents=frozenset({"else_clause"}),
    if_types=frozenset({"if_expression"}),
    skips_branch=_wildcard_arm,
    name_of=_function_name,
    is_test=_is_test,
)

_METHOD = Define(
    kind="function",
    kind_in_class="method",
    exported_by=exported,
    bases=impl_trait,
    locals=parameter_locals,
)

_SHELLS = frozenset({"sh", "bash", "cmd"})
_MAX_CHAIN = 40


def _first_arg(call: TSNode) -> TSNode | None:
    args = call.child_by_field_name("arguments")
    return args.named_children[0] if args is not None and args.named_children else None


def _literal_text(node: TSNode | None) -> str | None:
    if node is None or node.type != "string_literal":
        return None
    return "".join(_text(c) for c in node.named_children if c.type == "string_content")


_NAMES = frozenset({"identifier", "self", "scoped_identifier", "field_identifier"})
_PASS_THROUGH = frozenset(
    {"await_expression", "try_expression", "parenthesized_expression", "reference_expression"}
)


def _call_root(ctx: Ctx, node: TSNode, depth: int) -> str | None:
    """A call in the middle of a chain is rooted in its own callee."""
    callee = _callee_at(ctx, node, depth)
    is_command = callee is not None and re.search(r"(?:^|::)Command::new$", callee)
    if is_command and _literal_text(_first_arg(node)) in _SHELLS:
        return "ShellCommand"
    return callee


def _receiver(ctx: Ctx, node: TSNode, depth: int) -> str | None:
    """The text a method chain is rooted in: `sqlx::query`, `self.pool`.

    A call is its own callee, so `sqlx::query(q).bind(x)` is rooted in
    `sqlx::query.bind`. `Command::new("sh")` is rooted in `ShellCommand`: the
    program is a literal shell, which makes every later `.arg` a shell string.
    """
    t = node.type
    if depth > _MAX_CHAIN:
        return None
    if t in _NAMES:
        return _text(node)
    if t == "call_expression":
        return _call_root(ctx, node, depth + 1)
    if t == "macro_invocation":
        return _macro_name(node)
    if t in _PASS_THROUGH and node.named_children:
        return _receiver(ctx, node.named_children[-1], depth + 1)
    return _field_root(ctx, node, depth + 1) if t == "field_expression" else None


def _field_root(ctx: Ctx, node: TSNode, depth: int) -> str | None:
    value = node.child_by_field_name("value")
    field = node.child_by_field_name("field")
    base = _receiver(ctx, value, depth) if value is not None else None
    return f"{base}.{_text(field)}" if base and field is not None else None


def _macro_name(node: TSNode) -> str | None:
    name = node.child_by_field_name("macro")
    return f"{_text(name)}!" if name is not None else None


def _callee_at(ctx: Ctx, node: TSNode, depth: int) -> str | None:
    if node.type == "macro_invocation":
        return _macro_name(node)
    function = node.child_by_field_name("function")
    if function is not None and function.type == "generic_function":
        function = function.child_by_field_name("function")  # `query_as::<_, U>`
    if function is None:
        return None
    if function.type in ("identifier", "scoped_identifier"):
        return _text(function)
    if function.type == "field_expression":
        field = function.child_by_field_name("field")
        value = function.child_by_field_name("value")
        if field is None or value is None:
            return None
        base = _receiver(ctx, value, depth)
        return f"{base}.{_text(field)}" if base else f".{_text(field)}"
    return None


def risk_callee(ctx: Ctx, node: TSNode) -> str | None:
    """`sqlx::query`, `conn.execute`, `sqlx::query.bind.fetch_all` for a chain
    rooted in a call, `format!` for a macro; None for anything else."""
    return _callee_at(ctx, node, 0)


def _p(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern, re.DOTALL)


_CALL = frozenset({"call_expression"})
_TYPE = frozenset({"generic_type"})
# A database receiver: `conn`, `self.db`, `read_conn`.
_CONN = r"(?:conn|connection|client|tx|db|pool|\w*_conn)"
_SQLX_NAME = r"(?:(?:\w+::)*sqlx::)?(?:query|query_as|query_scalar)"
_SQLX = _SQLX_NAME + "!?"  # the macro form roots a chain
# A diesel chain starts at a table or `sql_query`: `users::table.filter.load`.
_DIESEL = r"(?:(?:\w+::)*table|(?:\w+::)*sql_query)"

# fmt: off
RISKS = RiskSpec(
    call_types=frozenset({"call_expression", "macro_invocation"}),
    callee=risk_callee,
    string_types=frozenset({"string_literal", "raw_string_literal"}),
    interpolations=(),
    concat=(("binary_expression", frozenset({"+"})),),
    # Rust has no `"x".format(..)`; `format!` is handled by `format_macros`.
    format_callee=re.compile(r"(?!)"),
    format_macros=frozenset({"format!", "std::format!"}),
    string_ctors=re.compile(r"^String::from$|\.(?:to_string|to_owned)$"),
    appends=re.compile(r"\.push_str$"),
    same_value=re.compile(r"\.(?:as_str|clone|to_owned|as_ref)$"),
    view_types=frozenset({"index_expression"}),
    path_types=frozenset({"field_expression"}),
    list_types=frozenset({"array_expression"}),
    await_types=frozenset({"await_expression"}),
    assigns=(
        ("let_declaration", "pattern", "value"),
        ("assignment_expression", "left", "right"),
        ("compound_assignment_expr", "left", "right"),
    ),
    unwrap=frozenset({"parenthesized_expression", "reference_expression", "await_expression", "try_expression"}),
    sinks=(
        # `sqlx::query!` and `query_as!` are macros checked at compile time. A
        # macro is never a sink because it has no `arguments` field, so the
        # walker never reads its argument. Excluding `!` in `_SQLX_NAME` is a
        # second guard.
        Sink("sql", re.compile(r"^" + _SQLX_NAME + r"$")),
        Sink("sql", re.compile(r"(?:^|::)sql_query$")),
        Sink("sql", re.compile(r"(?:^|\.)" + _CONN + r"\.(?:execute|query|query_row|prepare|batch_execute)$")),
        # The program is built, or a built `.arg` follows a literal shell.
        Sink("shell", re.compile(r"(?:^|::)Command::new$")),
        Sink("shell", re.compile(r"^ShellCommand(?:\.\w+)*\.args?$")),
    ),
    unsafe=(
        Unsafe("tls-verify-off", "certificate validation off", _CALL, _p(r".*danger_accept_invalid_(?:certs|hostnames)\(\s*true\s*\)")),
        Unsafe("weak-hash", "MD5 or SHA-1", _CALL, _p(r"(?:[\w:]*::)?md5::compute\(.*")),
        Unsafe("weak-hash", "MD5 or SHA-1", _CALL, _p(r"(?:[\w:]*::)?(?:Md5|Sha1)::(?:new|digest|default)\(.*")),
        Unsafe("weak-hash", "MD5 or SHA-1", _TYPE, _p(r"(?:[\w:]*::)?Hmac<\s*(?:[\w:]*::)?(?:Md5|Sha1)\s*>")),
        Unsafe("cors-any-origin", "CORS allows any origin", _CALL, _p(r"(?:[\w:]*::)?(?:CorsLayer::(?:very_)?permissive|Cors::permissive)\(\s*\)")),
        Unsafe("cors-any-origin", "CORS allows any origin", _CALL, _p(r".*\.allow_origin\(\s*(?:(?:[\w:]*::)?Any|(?:[\w:]*::)?AllowOrigin::any\(\s*\))\s*\)")),
        Unsafe("cors-any-origin", "CORS allows any origin", _CALL, _p(r".*\.allow_any_origin\(\s*\)")),
    ),
    # No auth_markers: routes are not extracted for Rust.
    loops=(
        ("for_expression", "body"),
        ("while_expression", "body"),
        ("loop_expression", "body"),
    ),
    loop_callee=re.compile(r"\.(?:for_each|map|try_for_each)$"),
    datastore_calls=re.compile(
        r"^" + _SQLX + r"(?:\.\w+)*\.(?:fetch_one|fetch_all|fetch_optional|fetch|execute)$"
        r"|(?:^|\.)" + _DIESEL + r"(?:\.\w+)*\.(?:load|get_result|get_results|first|execute)$"
        r"|(?:^|\.)" + _CONN + r"\.(?:execute|query|query_row|fetch_one|fetch_all|fetch_optional|fetch)$"
    ),
    async_marker="async",
    async_holder="function_modifiers",
    blocking=re.compile(
        r"^(?:std::)?thread::sleep$"
        r"|^(?:std::)?fs::(?:read|read_to_string|write|read_dir|copy|remove_file)$"
        r"|\.blocking_(?:send|recv|lock|read|write)$"
        r"|reqwest::blocking::"
    ),
    # No catch rule: Rust has no catch.
    quiet=frozenset({"line_comment", "block_comment"}),
)
# fmt: on


PACK = Pack(
    lang="rust",
    grammar=Grammar(
        distribution="tree-sitter-rust",
        version="0.24.2",
        module="tree_sitter_rust",
        default="language",
    ),
    maturity=Maturity.STABLE,
    metrics=METRICS,
    risks=RISKS,
    qualified_prefix=qualified_prefix,
    modules=RustCrates,
    decorator=decorator,
    rules={
        "source_file": Custom(hook=source_file),
        "use_declaration": Custom(hook=use_declaration),
        "extern_crate_declaration": Custom(hook=extern_crate),
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
        "let_declaration": Local(hook=let_locals),
        "closure_expression": Local(hook=closure_locals),
        "let_condition": Local(hook=pattern_locals),
        "match_pattern": Local(hook=pattern_locals),
        "for_expression": Local(hook=pattern_locals),
        "call_expression": Call(hook=call),
    },
)
