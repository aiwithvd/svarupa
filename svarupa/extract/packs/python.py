"""Python language pack.

The rules are data. The hooks are the parts of Python data cannot say: how
`from ..x import y as z` spells a relative import, which bare names are
builtins, how a decorator carries a route path. Each hook is a direct port
of the hand-written extractor it replaced; tests/golden/python pins that
nothing moved.
"""

from __future__ import annotations

import re
import weakref

from tree_sitter import Node as TSNode

from svarupa.extract.base import (
    CallShape,
    CallSite,
    DecoratorRef,
    ImportRef,
    LocalType,
    qualified_prefix,
)
from svarupa.extract.packs.model import (
    Call,
    Custom,
    Decorated,
    Define,
    Grammar,
    Import,
    Maturity,
    MetricsSpec,
    Pack,
    RiskSpec,
    Sink,
    Unsafe,
)
from svarupa.extract.packs.walker import Ctx, Frame

__all__ = ["BUILTIN_CALLS", "PACK"]

# Builtins that would otherwise look like unresolved intra-repo calls. Kept
# small and explicit rather than pulling in the whole `builtins` namespace,
# because a project is free to define its own `list` or `filter` and we want
# the local definition to win.
# fmt: off
BUILTIN_CALLS = frozenset(
    {
        "print", "len", "range", "enumerate", "zip", "map", "filter", "sorted",
        "isinstance", "issubclass", "hasattr", "getattr", "setattr", "delattr",
        "int", "str", "float", "bool", "list", "dict", "set", "tuple", "bytes",
        "type", "super", "open", "iter", "next", "repr", "format", "abs", "min",
        "max", "sum", "any", "all", "round", "id", "hash", "vars", "dir",
        "callable", "reversed", "slice", "frozenset", "bytearray", "complex",
        "divmod", "pow", "ord", "chr", "hex", "oct", "bin", "input", "eval",
        "exec", "compile", "globals", "locals", "staticmethod", "classmethod",
        "property", "object", "Exception", "ValueError", "TypeError", "KeyError",
        "IndexError", "RuntimeError", "StopIteration", "NotImplementedError",
        "AttributeError", "OSError", "IOError", "ZeroDivisionError",
    }
)
# fmt: on


def _string_literal(ctx: Ctx, node: TSNode) -> str | None:
    """The content of a plain string literal, or None if it is not one.

    An f-string with interpolation is dynamic: returning its static parts
    would invent a route path that is not the real one.
    """
    if any(c.type == "interpolation" for c in node.children):
        return None
    parts = [c for c in node.children if c.type == "string_content"]
    if not parts:
        return ""  # an empty string literal has no content node
    return "".join(ctx.text(c) for c in parts)


def decorator(ctx: Ctx, node: TSNode) -> DecoratorRef | None:
    """One decorator, with the callee text, its first literal string
    argument, and any literal `methods=[...]` keyword.

    Anything dynamic (an f-string path, a computed methods list) is left
    out rather than guessed: a missing `arg` means "no literal path", not
    an empty path.
    """
    expr = next((c for c in node.children if c.type not in ("@", "comment")), None)
    if expr is None:
        return None
    ev = ctx.node_evidence(node)
    if expr.type != "call":
        name = ctx.text(expr)
        return DecoratorRef(name=name, arg=None, evidence=ev) if name else None
    callee = expr.child_by_field_name("function")
    if callee is None:
        return None
    arg: str | None = None
    # None: no `methods` kwarg at all. (): the kwarg is present but not a
    # literal collection of strings, so the methods are unknown. The two
    # must stay distinguishable: Flask's documented default applies only
    # to the first, and applying it to the second invents a method.
    methods: tuple[str, ...] | None = None
    args = expr.child_by_field_name("arguments")
    if args is not None:
        for child in args.children:
            if arg is None and child.type == "string":
                arg = _string_literal(ctx, child)
            if child.type == "keyword_argument":
                key = child.child_by_field_name("name")
                value = child.child_by_field_name("value")
                if key is None or ctx.text(key) != "methods" or value is None:
                    continue
                methods = ()
                if value.type in ("list", "tuple"):
                    items = [
                        _string_literal(ctx, c) for c in value.children if c.type == "string"
                    ]
                    entries = sum(
                        1 for c in value.children if c.type not in ("[", "]", "(", ")", ",")
                    )
                    if items and len(items) == entries and all(i is not None for i in items):
                        methods = tuple(i for i in items if i is not None)
    return DecoratorRef(name=ctx.text(callee), arg=arg, evidence=ev, methods=methods)


def _is_package_init(path: str) -> bool:
    return path.endswith("/__init__.py") or path == "__init__.py"


def import_(ctx: Ctx, node: TSNode) -> ImportRef | None:
    ref = _import(ctx, node)
    # In a package __init__, `from .x import Y` is the re-export chain that
    # makes `from pkg import Y` work. Without following it, resolution lands
    # on the facade rather than the definition.
    if ref is not None and ref.is_relative and _is_package_init(ctx.path):
        ctx.reexports.extend(ref.names)
    return ref


def _import(ctx: Ctx, node: TSNode) -> ImportRef | None:
    ev = ctx.node_evidence(node)

    if node.type == "import_statement":
        names: list[str] = []
        alias: dict[str, str] = {}
        for child in node.children:
            if child.type == "dotted_name":
                names.append(ctx.text(child))
            elif child.type == "aliased_import":
                target = child.child_by_field_name("name")
                as_name = child.child_by_field_name("alias")
                if target is not None:
                    dotted = ctx.text(target)
                    names.append(dotted)
                    if as_name is not None:
                        alias[ctx.text(as_name)] = dotted
        if not names:
            return None
        return ImportRef(
            specifier=names[0],
            names=tuple(names),
            alias_of=alias,
            evidence=ev,
            is_relative=False,
            level=0,
            is_from=False,
        )

    # import_from_statement
    module_node = node.child_by_field_name("module_name")
    spec = ctx.text(module_node) if module_node is not None else ""
    level = len(spec) - len(spec.lstrip("."))
    names_out: list[str] = []
    alias_out: dict[str, str] = {}

    for child in node.children:
        # `is` is wrong here: py-tree-sitter hands back a fresh wrapper object
        # per `child_by_field_name` call, so identity never matches and the
        # module name was captured as if it were an imported symbol.
        if module_node is not None and child.id == module_node.id:
            continue
        if child.type == "dotted_name":
            names_out.append(ctx.text(child))
        elif child.type == "aliased_import":
            target = child.child_by_field_name("name")
            as_name = child.child_by_field_name("alias")
            if target is not None:
                real = ctx.text(target)
                names_out.append(real)
                if as_name is not None:
                    alias_out[ctx.text(as_name)] = real

    # `from x import *` binds an unknowable set; the module edge is kept and
    # the names are left empty rather than guessed.
    return ImportRef(
        specifier=spec,
        names=tuple(names_out),
        alias_of=alias_out,
        evidence=ev,
        is_relative=level > 0,
        level=level,
    )


def call(ctx: Ctx, node: TSNode, frame: Frame) -> CallSite | None:
    func = node.child_by_field_name("function")
    if func is None:
        return None
    row = node.start_point[0]
    ev = ctx.evidence(row, row)
    fn, cls = frame.fn, frame.cls

    if func.type == "identifier":
        name = ctx.text(func)
        if name in BUILTIN_CALLS:
            return None
        return CallSite(name, CallShape.BARE, None, ev, fn, cls)

    if func.type == "attribute":
        obj = func.child_by_field_name("object")
        attr = func.child_by_field_name("attribute")
        if attr is None or obj is None:
            return None
        name = ctx.text(attr)
        recv = ctx.text(obj)
        if recv == "self":
            return CallSite(name, CallShape.SELF, "self", ev, fn, cls)
        if obj.type == "call" and recv.startswith("super("):
            return CallSite(name, CallShape.SUPER, "super", ev, fn, cls)
        if obj.type == "identifier":
            # Ambiguous here: `recv` may be an imported module or a local.
            # The resolver decides, because only it knows the import table.
            return CallSite(name, CallShape.QUALIFIED, recv, ev, fn, cls)
        return CallSite(name, CallShape.MEMBER, recv, ev, fn, cls)

    return None


def bases(ctx: Ctx, node: TSNode) -> tuple[str, ...]:
    supers = node.child_by_field_name("superclasses")
    if supers is None:
        return ()
    return tuple(
        ctx.text(child).rsplit(".", 1)[-1]
        for child in supers.children
        if child.type in ("identifier", "attribute")
    )


# --------------------------------------------------------------------------
# parameter and local types
#
# Python scope is the function, not the block. A name bound anywhere in a
# function is local to all of it, so its bindings cover the whole function.
# The exceptions get their own lines: a comprehension, a lambda, and a nested
# `def`. A nested function reads its enclosing functions' locals, so those
# are recorded under it too, over the enclosing function's lines; its own
# bindings span fewer lines and win. `global` and `nonlocal` names are not
# locals of the function that declares them.
#
# Only an annotation types a name. Every other binding records "", so a local
# hides an import or a field of the same name inside its scope and nowhere
# else.

# Annotations that name no single class.
_NOT_TYPES = frozenset(
    {
        "Any", "Optional", "Union", "Callable", "Type", "type", "Self", "Literal",
        "NoReturn", "Never", "None", "object", "LiteralString", "Final", "ClassVar",
        "list", "dict", "set", "frozenset", "tuple", "List", "Dict", "Set", "FrozenSet",
        "Tuple", "Sequence", "Iterable", "Iterator", "Mapping", "Awaitable",
    }
)  # fmt: skip
_TYPEVAR = re.compile(
    rb"^[ \t]*(\w+)[ \t]*(?::[^=\n]*)?=[ \t]*(?:\w+\.)?(?:TypeVar|ParamSpec|TypeVarTuple)[ \t]*\(",
    re.MULTILINE,
)
_SUBSCRIPT = re.compile(r"([\w.]+)\s*\[(.*)\]")
_NAME = re.compile(r"[^\W\d]\w*")


def _split(text: str, sep: str) -> list[str]:
    """Split on `sep` outside brackets and quotes."""
    out: list[str] = []
    depth, quote, start = 0, "", 0
    for i, ch in enumerate(text):
        if quote:
            quote = "" if ch == quote else quote
        elif ch in "\"'":
            quote = ch
        elif ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif ch == sep and depth == 0:
            out.append(text[start:i])
            start = i + 1
    out.append(text[start:])
    return [p.strip() for p in out]


def _unquote(text: str) -> str | None:
    quoted = len(text) > 1 and text[0] in "\"'" and text[-1] == text[0]
    return text[1:-1] if quoted and text[0] not in text[1:-1] else None


def _one_of(args: list[str], skip: frozenset[str]) -> str:
    """The type of the only argument that is not None, else no type."""
    real = [a for a in args if a != "None"]
    return annotation_type(real[0], skip) if len(real) == 1 else ""


def annotation_type(text: str, skip: frozenset[str] = frozenset()) -> str:
    """The one class an annotation names, or "". `Optional[T]`, `T | None`,
    `Union[T, None]`, `Annotated[T, ...]` and a quoted `"T"` are T. Two real
    types, a TypeVar, a container, a dotted name and anything else are no type:
    a union could be either, and a dotted name could be a module's class that
    shares a bare name with one of ours."""
    text = " ".join(text.split())
    inner = _unquote(text)
    if inner is not None:
        return annotation_type(inner, skip)
    parts = _split(text, "|")
    if len(parts) > 1:
        return _one_of(parts, skip)
    sub = _SUBSCRIPT.fullmatch(text)
    if sub is not None:
        head, args = sub[1].rsplit(".", 1)[-1], _split(sub[2], ",")
        if head == "Annotated":
            return annotation_type(args[0], skip)
        return _one_of(args, skip) if head in ("Optional", "Union") else ""
    ok = _NAME.fullmatch(text) and text not in _NOT_TYPES and text not in skip
    return text if ok else ""


class _Scope:
    """What one function (or the module) binds."""

    def __init__(self) -> None:
        self.wide: dict[tuple[str, str], None] = {}  # (name, type), function-wide
        self.inner: list[tuple[str, TSNode]] = []  # name, the lambda or comprehension
        self.declared_global: set[str] = set()
        self.declared_nonlocal: set[str] = set()
        self.imported: set[str] = set()  # names an import statement binds here

    def visible(self) -> list[tuple[str, str]]:
        """The function-wide bindings that are this scope's own."""
        hidden = self.declared_global | self.declared_nonlocal
        return [(n, t) for n, t in self.wide if n not in hidden]


class _State:
    def __init__(self, data: bytes) -> None:
        self.typevars = frozenset(
            m[1].decode("utf8", "replace") for m in _TYPEVAR.finditer(data)
        )
        self.scopes: dict[int, _Scope] = {}


_STATES: weakref.WeakKeyDictionary[Ctx, _State] = weakref.WeakKeyDictionary()


def _state(ctx: Ctx) -> _State:
    state = _STATES.get(ctx)
    if state is None:
        state = _STATES[ctx] = _State(ctx.data)
    return state


def _first_identifier(node: TSNode) -> TSNode | None:
    if node.type == "identifier":
        return node
    return next((i for c in node.children if (i := _first_identifier(c)) is not None), None)


def _generic_names(ctx: Ctx, node: TSNode) -> frozenset[str]:
    """PEP 695 type parameters of this definition and the ones around it."""
    names: set[str] = set()
    cur: TSNode | None = node
    while cur is not None:
        tp = cur.child_by_field_name("type_parameters") if cur.type in _DEFS else None
        if tp is not None:
            names.update(
                ctx.text(i)
                for c in tp.named_children
                if (i := _first_identifier(c)) is not None
            )
        cur = cur.parent
    return frozenset(names)


_DEFS = frozenset({"function_definition", "class_definition"})
_ASSIGN_TARGETS = frozenset(
    {
        "pattern_list",
        "tuple_pattern",
        "list_pattern",
        "list_splat_pattern",
        "parenthesized_pattern",
        "as_pattern_target",
        "tuple",
        "list",
        "parenthesized_expression",
    }
)
_COMPREHENSIONS = frozenset(
    {
        "list_comprehension",
        "set_comprehension",
        "dictionary_comprehension",
        "generator_expression",
    }
)
_PATTERN_PARTS = frozenset(
    {"case_pattern", "list_pattern", "tuple_pattern", "union_pattern", "dict_pattern"}
)


def _targets(ctx: Ctx, node: TSNode | None) -> list[str]:
    """The names an assignment target binds: `a`, `a, (b, *c)`. An attribute or
    a subscript binds nothing."""
    if node is None:
        return []
    if node.type == "identifier":
        return [ctx.text(node)]
    if node.type in _ASSIGN_TARGETS:
        return [n for c in node.named_children for n in _targets(ctx, c)]
    return []


def _all_captures(ctx: Ctx, nodes: list[TSNode]) -> list[str]:
    return [n for c in nodes for n in _captures(ctx, c)]


def _capture_name(ctx: Ctx, node: TSNode) -> list[str]:
    return [ctx.text(node)] if node.named_child_count == 1 and node.text != b"_" else []


def _capture_splat(ctx: Ctx, node: TSNode) -> list[str]:
    return [ctx.text(c) for c in node.named_children if c.type == "identifier"]


def _capture_keyword(ctx: Ctx, node: TSNode) -> list[str]:
    return _captures(ctx, node.named_children[-1]) if node.named_child_count > 1 else []


def _capture_class(ctx: Ctx, node: TSNode) -> list[str]:
    # The first child is the class, not a capture.
    return _all_captures(ctx, node.named_children[1:])


def _capture_as(ctx: Ctx, node: TSNode) -> list[str]:
    kids = node.named_children
    inner = _all_captures(ctx, [c for c in kids if c.type == "case_pattern"])
    return inner + [ctx.text(c) for c in kids[-1:] if c.type == "identifier"]


def _capture_dict(ctx: Ctx, node: TSNode) -> list[str]:
    values = [
        c
        for i, c in enumerate(node.children)
        if c.is_named and node.field_name_for_child(i) != "key"
    ]
    return _all_captures(ctx, values)


def _capture_parts(ctx: Ctx, node: TSNode) -> list[str]:
    return _all_captures(ctx, node.named_children)


_CAPTURES = {
    "dotted_name": _capture_name,  # a dotted name is a value; a lone name captures
    "splat_pattern": _capture_splat,
    "keyword_pattern": _capture_keyword,
    "class_pattern": _capture_class,
    "as_pattern": _capture_as,
    "dict_pattern": _capture_dict,
    "case_pattern": _capture_parts,
    "list_pattern": _capture_parts,
    "tuple_pattern": _capture_parts,
    "union_pattern": _capture_parts,
}


def _captures(ctx: Ctx, node: TSNode) -> list[str]:
    """The names a `case` pattern captures."""
    hook = _CAPTURES.get(node.type)
    return hook(ctx, node) if hook is not None else []


def _annotated(ctx: Ctx, node: TSNode, skip: frozenset[str]) -> str:
    ann = node.child_by_field_name("type")
    return annotation_type(ctx.text(ann), skip) if ann is not None else ""


def _param_default(ctx: Ctx, node: TSNode, skip: frozenset[str]) -> tuple[str, str]:
    name = node.child_by_field_name("name")
    return (ctx.text(name) if name is not None else "", _annotated(ctx, node, skip))


def _param_typed(ctx: Ctx, node: TSNode, skip: frozenset[str]) -> tuple[str, str]:
    kids = node.named_children
    if not kids:
        return "", ""
    if kids[0].type == "identifier":
        return ctx.text(kids[0]), _annotated(ctx, node, skip)
    return _param_splat(ctx, kids[0], skip)  # `*args: T` is a tuple of T


def _param_splat(ctx: Ctx, node: TSNode, _skip: frozenset[str]) -> tuple[str, str]:
    names = _targets(ctx, node.named_children[0]) if node.named_children else []
    return (names[0] if names else ""), ""


def _param_plain(ctx: Ctx, node: TSNode, _skip: frozenset[str]) -> tuple[str, str]:
    return ctx.text(node), ""


_PARAMS = {
    "identifier": _param_plain,
    "default_parameter": _param_default,
    "typed_default_parameter": _param_default,
    "typed_parameter": _param_typed,
    "list_splat_pattern": _param_splat,
    "dictionary_splat_pattern": _param_splat,
}


def _parameters(
    ctx: Ctx, node: TSNode | None, skip: frozenset[str], receiver: bool
) -> list[tuple[str, str]]:
    """Every parameter. Only `name: T` and `name: T = default` are typed.
    `self` and `cls` first are the receiver, not a local."""
    out: list[tuple[str, str]] = []
    for i, child in enumerate(node.named_children if node is not None else []):
        hook = _PARAMS.get(child.type)
        name, ty = hook(ctx, child, skip) if hook is not None else ("", "")
        if name and not (receiver and i == 0 and name in ("self", "cls")):
            out.append((name, ty))
    return out


def _imported_names(ctx: Ctx, node: TSNode) -> list[str]:
    """The names an import statement binds: `import a.b as c` binds `c`,
    `import a.b` binds `a`, `from m import X as c` binds `c`."""
    out: list[str] = []
    for item in node.children_by_field_name("name"):
        if item.type == "aliased_import":
            item = item.child_by_field_name("alias") or item
        first = item.named_children[0] if item.type == "dotted_name" else item
        if first.type == "identifier":
            out.append(ctx.text(first))
    return out


def _caught_type(ctx: Ctx, value: TSNode, skip: frozenset[str]) -> str:
    """The type `except E as e` gives `e`: `E` when it is one plain name."""
    caught = value.named_children[0]
    return annotation_type(ctx.text(caught), skip) if caught.type == "identifier" else ""


class _Walk:
    """One scope's walk. A step takes a node, whether its bindings are the
    scope's own (a class body's are not), and the lambda around it; it says
    what to visit next."""

    def __init__(self, ctx: Ctx, root: TSNode, sc: _Scope, skip: frozenset[str]) -> None:
        self.ctx, self.sc, self.skip = ctx, sc, skip
        self.in_function = root.type == "function_definition"

    def bind(self, names: list[str], ty: str, binds: bool, around: TSNode | None) -> None:
        for name in names:
            if around is not None:
                self.sc.inner.append((name, around))
            elif binds:
                self.sc.wide[(name, ty)] = None

    def definition(self, node: TSNode, binds: bool, around: TSNode | None) -> _Next:
        name = node.child_by_field_name("name")
        body = node.child_by_field_name("body")
        if self.in_function and name is not None:
            self.bind([self.ctx.text(name)], "", binds, around)
        if node.type == "function_definition" or body is None:
            return [], binds, around
        # A class body binds its own names, not the scope's; a lambda or
        # comprehension in it still shadows.
        return [body], False, around if around is not None else body

    def lambda_(self, node: TSNode, binds: bool, _around: TSNode | None) -> _Next:
        params = node.child_by_field_name("parameters")
        for name, _ in _parameters(self.ctx, params, self.skip, False):
            self.sc.inner.append((name, node))
        return list(node.children), binds, node

    def comprehension(self, node: TSNode, binds: bool, around: TSNode | None) -> _Next:
        for clause in node.children:
            if clause.type == "for_in_clause":
                for name in _targets(self.ctx, clause.child_by_field_name("left")):
                    self.sc.inner.append((name, node))
        return list(node.children), binds, around

    def assignment(self, node: TSNode, binds: bool, around: TSNode | None) -> _Next:
        left = node.child_by_field_name("left")
        if node.child_by_field_name("type") is None:
            self.bind(_targets(self.ctx, left), "", binds, around)
        elif left is not None and left.type == "identifier":
            ty = _annotated(self.ctx, node, self.skip)
            self.bind([self.ctx.text(left)], ty, binds, around)
        return list(node.children), binds, around

    def for_(self, node: TSNode, binds: bool, around: TSNode | None) -> _Next:
        self.bind(_targets(self.ctx, node.child_by_field_name("left")), "", binds, around)
        return list(node.children), binds, around

    def with_item(self, node: TSNode, binds: bool, around: TSNode | None) -> _Next:
        value = node.child_by_field_name("value")
        if value is not None and value.type == "as_pattern":
            alias = value.child_by_field_name("alias")
            self.bind(_targets(self.ctx, alias), "", binds, around)
        return list(node.children), binds, around

    def except_(self, node: TSNode, binds: bool, around: TSNode | None) -> _Next:
        """`except E as e` types `e` when `E` is one plain name."""
        value = node.child_by_field_name("value")
        if value is not None and value.type == "as_pattern" and value.named_child_count:
            ty = _caught_type(self.ctx, value, self.skip)
            self.bind(_targets(self.ctx, value.child_by_field_name("alias")), ty, binds, around)
        return list(node.children), binds, around

    def walrus(self, node: TSNode, binds: bool, around: TSNode | None) -> _Next:
        self.bind(_targets(self.ctx, node.child_by_field_name("name")), "", binds, around)
        return list(node.children), binds, around

    def case(self, node: TSNode, binds: bool, around: TSNode | None) -> _Next:
        for pattern in node.children:
            if pattern.type == "case_pattern":
                self.bind(_captures(self.ctx, pattern), "", binds, around)
        return list(node.children), binds, around

    def import_(self, node: TSNode, binds: bool, around: TSNode | None) -> _Next:
        """Only noted: an import name hides a typed name of the same spelling
        (see `function_locals`), and otherwise the import still resolves."""
        self.sc.imported.update(_imported_names(self.ctx, node))
        return [], binds, around

    def type_alias(self, node: TSNode, binds: bool, around: TSNode | None) -> _Next:
        left = node.named_children[0] if node.named_children else None
        ident = _first_identifier(left) if left is not None else None
        if ident is not None:
            self.bind([self.ctx.text(ident)], "", binds, around)
        return [], binds, around

    def global_(self, node: TSNode, binds: bool, around: TSNode | None) -> _Next:
        self.sc.declared_global.update(self.ctx.text(c) for c in node.named_children)
        return [], binds, around

    def nonlocal_(self, node: TSNode, binds: bool, around: TSNode | None) -> _Next:
        self.sc.declared_nonlocal.update(self.ctx.text(c) for c in node.named_children)
        return [], binds, around

    def run(self, start: TSNode) -> None:
        todo: list[tuple[TSNode, bool, TSNode | None]] = [(start, True, None)]
        while todo:
            node, binds, around = todo.pop()
            method = _STEPS.get(node.type)
            step = getattr(self, method) if method else None
            kids, binds, around = (
                step(node, binds, around) if step else (list(node.children), binds, around)
            )
            todo.extend((c, binds, around) for c in reversed(kids))


_Next = tuple[list[TSNode], bool, "TSNode | None"]

# Node type to the `_Walk` method that handles it.
_STEPS = {
    "function_definition": "definition",
    "class_definition": "definition",
    "lambda": "lambda_",
    "list_comprehension": "comprehension",
    "set_comprehension": "comprehension",
    "dictionary_comprehension": "comprehension",
    "generator_expression": "comprehension",
    "assignment": "assignment",
    "for_statement": "for_",
    "with_item": "with_item",
    "except_clause": "except_",
    "named_expression": "walrus",
    "case_clause": "case",
    "global_statement": "global_",
    "nonlocal_statement": "nonlocal_",
    "import_statement": "import_",
    "import_from_statement": "import_",
    "type_alias_statement": "type_alias",
}


def _scope(ctx: Ctx, root: TSNode) -> _Scope:
    """What `root` (a function or the module) binds, not looking into a nested
    function. Cached: a nested function asks for every function around it."""
    state = _state(ctx)
    cached = state.scopes.get(root.id)
    if cached is not None:
        return cached
    sc = state.scopes[root.id] = _Scope()
    skip = state.typevars | _generic_names(ctx, root)
    walk = _Walk(ctx, root, sc, skip)
    if root.type != "function_definition":
        walk.run(root)
        return sc
    for name, ty in _parameters(ctx, root.child_by_field_name("parameters"), skip, True):
        sc.wide[(name, ty)] = None
    body = root.child_by_field_name("body")
    walk.run(body if body is not None else root)
    _rebound_by_nested(ctx, root, sc)
    return sc


def _rebound_by_nested(ctx: Ctx, root: TSNode, sc: _Scope) -> None:
    """A nested function that says `nonlocal x` may rebind the `x` of this
    function, so `x` is also untyped here: the conflict hides it."""
    names = {n for n, _ in sc.wide}
    todo = list(root.children)
    while todo and names:
        node = todo.pop()
        if node.type == "function_definition":
            for name in _scope(ctx, node).declared_nonlocal & names:
                sc.wide[(name, "")] = None
        todo.extend(node.children)


def _own(fn: str, sc: _Scope) -> list[LocalType]:
    """A scope's own bindings: function-wide, then each lambda and comprehension
    over its lines."""
    out = [LocalType(fn, n, t) for n, t in sc.visible()]
    seen: set[tuple[str, int]] = set()
    for name, node in sc.inner:
        if (name, node.id) not in seen:
            seen.add((name, node.id))
            out.append(LocalType(fn, name, "", node.start_point[0] + 1, node.end_point[0] + 1))
    return out


def function_locals(ctx: Ctx, node: TSNode, qualified: str) -> list[LocalType]:
    """A function's own bindings, then those of the functions around it."""
    own = _scope(ctx, node)
    out = _own(qualified, own)
    blocked = set(own.declared_global)
    typed = {n for n, t in own.visible() if t}
    outer = node.parent
    while outer is not None:
        if outer.type == "function_definition":
            sc = _scope(ctx, outer)
            typed |= {n for n, t in sc.visible() if t}
            start, end = outer.start_point[0] + 1, outer.end_point[0] + 1
            out += [
                LocalType(qualified, n, t, start, end)
                for n, t in sc.visible()
                if n not in blocked
            ]
            blocked |= sc.declared_global
        outer = outer.parent
    # An import that rebinds a typed name hides it; any other import is left to
    # the import resolution.
    out += [LocalType(qualified, n, "") for n in sorted(own.imported & typed)]
    return out


def module(ctx: Ctx, node: TSNode, frame: Frame, depth: int) -> None:
    """Module-level bindings are the locals of the function "", as for a call
    outside any function."""
    ctx.locals.extend(_own("", _scope(ctx, node)))
    plain = frame.plain()
    for child in node.children:
        ctx.visit(child, plain, depth + 1)


_PARAM_KINDS = frozenset(
    {
        "identifier",
        "typed_parameter",
        "default_parameter",
        "typed_default_parameter",
        "list_splat_pattern",
        "dictionary_splat_pattern",
    }
)


def _count_params(fn: TSNode) -> int:
    params = fn.child_by_field_name("parameters")
    if params is None:
        return 0
    names = [c for c in params.children if c.type in _PARAM_KINDS]
    # `self` and `cls` are the receiver, not an argument a caller passes.
    if names and names[0].type == "identifier" and names[0].text in (b"self", b"cls"):
        names = names[1:]
    return len(names)


METRICS = MetricsSpec(
    function_types=frozenset({"function_definition", "lambda"}),
    branch_types=frozenset(
        {
            "if_statement",
            "elif_clause",
            "for_statement",
            "while_statement",
            "except_clause",
            "conditional_expression",
            "boolean_operator",
            "for_in_clause",
            "if_clause",
            "case_clause",
        }
    ),
    nesting_types=frozenset(
        {
            "if_statement",
            "for_statement",
            "while_statement",
            "try_statement",
            "with_statement",
            "match_statement",
        }
    ),
    count_params=_count_params,
)


def risk_callee(ctx: Ctx, node: TSNode) -> str | None:
    fn = node.child_by_field_name("function")
    if fn is None or fn.end_byte - fn.start_byte > 200:
        return None
    return ctx.text(fn)


_KW = frozenset({"keyword_argument"})
_CALL = frozenset({"call"})
_ASSIGN = frozenset({"assignment"})


def _p(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern, re.DOTALL)


# fmt: off
RISKS = RiskSpec(
    call_types=frozenset({"call"}),
    callee=risk_callee,
    string_types=frozenset({"string", "concatenated_string"}),
    interpolations=(("string", "interpolation"),),
    concat=(("binary_operator", frozenset({"+", "%"})),),
    format_callee=re.compile(r"\.format$"),
    assigns=(("assignment", "left", "right"), ("augmented_assignment", "left", "right")),
    unwrap=frozenset({"parenthesized_expression", "keyword_argument", "await"}),
    sinks=(
        Sink("sql", re.compile(r"(?:^|\.)(?:execute|executemany|executescript)$|\.objects\.(?:raw|extra)$|^(?:sqlalchemy\.|sa\.)?text$")),
        Sink("shell", re.compile(r"^os\.(?:system|popen)$|^subprocess\.(?:getoutput|getstatusoutput)$")),
        Sink(
            "shell",
            re.compile(r"^subprocess\.(?:run|call|check_call|check_output|Popen)$"),
            re.compile(r"shell\s*=\s*True"),
        ),
        Sink("code", re.compile(r"^(?:eval|exec)$")),
    ),
    unsafe=(
        Unsafe("tls-verify-off", "verify=False", _KW, _p(r"verify\s*=\s*False")),
        Unsafe("tls-verify-off", "unverified SSL context", _CALL, _p(r"ssl\._create_unverified_context\(.*")),
        Unsafe("unsafe-deserialize", "pickle load", _CALL, _p(r"(?:c?[pP]ickle|dill|marshal|shelve|jsonpickle)\.(?:loads?|open|decode)\(.*")),
        Unsafe("unsafe-deserialize", "yaml.load without a safe loader", _CALL, _p(r"yaml\.(?:unsafe_)?load(?:_all)?\((?!.*Safe).*")),
        Unsafe("weak-hash", "MD5 or SHA-1", _CALL, _p(r"hashlib\.(?:md5|sha1)\((?!.*usedforsecurity\s*=\s*False).*")),
        Unsafe("weak-hash", "MD5 or SHA-1", _CALL, _p(r"hashlib\.new\(\s*[\"'](?:md5|sha1)[\"'](?!.*usedforsecurity\s*=\s*False).*")),
        Unsafe("debug-enabled", "DEBUG = True", _ASSIGN, _p(r"DEBUG\s*=\s*True")),
        Unsafe("debug-enabled", "debug=True", _KW, _p(r"debug\s*=\s*True")),
        Unsafe("cors-any-origin", "CORS allows every origin", _KW, _p(r"(?:allow_origins|origins)\s*=\s*(?:\[\s*)?[\"']\*[\"']\s*\]?")),
        Unsafe("cors-any-origin", "CORS allows every origin", _ASSIGN, _p(r"CORS_(?:ORIGIN_ALLOW_ALL|ALLOW_ALL_ORIGINS)\s*=\s*True")),
        Unsafe("cors-any-origin", "CORS with default origins (*)", _CALL, _p(r"CORS\(\s*\w+\s*\)")),
        Unsafe("no-timeout", "requests call without timeout", _CALL, _p(r"requests\.(?:get|post|put|patch|delete|head|options|request)\((?!.*\btimeout\s*=).*")),
        Unsafe("no-timeout", "urlopen without timeout", _CALL, _p(r"(?:urllib\.request\.)?urlopen\((?!.*\btimeout\s*=).*")),
        Unsafe("no-retry", "pika connection tries once", _CALL, _p(r"pika\.(?:ConnectionParameters|URLParameters)\((?!.*connection_attempts).*")),
        Unsafe("no-retry", "aio_pika.connect does not reconnect (use connect_robust)", _CALL, _p(r"aio_pika\.connect\(.*")),
    ),
    auth_markers=frozenset({
        "login_required", "permission_required", "user_passes_test", "jwt_required",
        "auth_required", "requires_auth", "token_required", "fresh_jwt_required",
        "get_current_user", "get_current_active_user", "get_current_active_superuser",
        "CurrentUser", "OAuth2PasswordBearer", "HTTPBearer", "APIKeyHeader",
        "Security", "permission_classes", "IsAuthenticated", "LoginRequiredMixin",
        "PermissionRequiredMixin", "verify_token",
    }),
    loops=(
        ("for_statement", "body"),
        ("while_statement", "body"),
        ("list_comprehension", "body"),
        ("set_comprehension", "body"),
        ("dictionary_comprehension", "body"),
        ("generator_expression", "body"),
    ),
    datastore_calls=re.compile(
        r"(?:^|\.)objects\.\w+$"
        r"|(?:^|\.)[A-Z]\w*\.query\.(?:get|get_or_404|filter|filter_by|first|all|one|count)$"
        r"|(?:^|\.)(?:db\.session|cursor|cur|collection)\.(?:query|execute|executemany|get|scalar|scalars|scalar_one|first|find|find_one|insert_one|update_one|delete_one|aggregate|fetch|fetchrow|fetchval)$"
        r"|(?:^|\.)(?:session|db|conn|connection|engine)\.(?:query|execute|executemany|scalar|scalars|scalar_one|fetch|fetchrow|fetchval)$"
    ),
    async_marker="async",
    blocking=re.compile(
        r"^(?:requests\.(?:get|post|put|patch|delete|head|options|request)|time\.sleep"
        r"|urllib\.request\.urlopen|urlopen|subprocess\.(?:run|call|check_call|check_output)"
        r"|os\.system|psycopg2\.connect|sqlite3\.connect)$"
    ),
    catch_types=frozenset({"except_clause"}),
    quiet=frozenset({"pass_statement", "comment"}),
)
# fmt: on


PACK = Pack(
    lang="python",
    grammar=Grammar(
        distribution="tree-sitter-python",
        version="0.25.0",
        module="tree_sitter_python",
        default="language",
    ),
    maturity=Maturity.STABLE,
    metrics=METRICS,
    risks=RISKS,
    qualified_prefix=lambda path: qualified_prefix(path, "python"),
    decorator=decorator,
    rules={
        "decorated_definition": Decorated(),
        "import_statement": Import(hook=import_),
        "import_from_statement": Import(hook=import_),
        "module": Custom(hook=module),
        "class_definition": Define(kind="class", sets_class=True, bases=bases),
        "function_definition": Define(
            kind="function", kind_in_class="method", locals=function_locals
        ),
        "call": Call(hook=call),
    },
)
