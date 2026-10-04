"""Python language pack.

The rules are data. The hooks are the parts of Python data cannot say: how
`from ..x import y as z` spells a relative import, which bare names are
builtins, how a decorator carries a route path. Each hook is a direct port
of the hand-written extractor it replaced; tests/golden/python pins that
nothing moved.
"""

from __future__ import annotations

from tree_sitter import Node as TSNode

from svarupa.extract.base import (
    CallShape,
    CallSite,
    DecoratorRef,
    ImportRef,
    qualified_prefix,
)
from svarupa.extract.packs.model import (
    Call,
    Decorated,
    Define,
    Grammar,
    Import,
    Maturity,
    Pack,
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


PACK = Pack(
    lang="python",
    grammar=Grammar(
        distribution="tree-sitter-python",
        version="0.25.0",
        module="tree_sitter_python",
        default="language",
    ),
    maturity=Maturity.STABLE,
    qualified_prefix=lambda path: qualified_prefix(path, "python"),
    decorator=decorator,
    rules={
        "decorated_definition": Decorated(),
        "import_statement": Import(hook=import_),
        "import_from_statement": Import(hook=import_),
        "class_definition": Define(kind="class", sets_class=True, bases=bases),
        "function_definition": Define(kind="function", kind_in_class="method"),
        "call": Call(hook=call),
    },
)
