"""TypeScript language pack.

TypeScript resolves far better than Python (Spike 0c: imports 63-96%
against 37-47%, service calls ~49% against ~20%) because module specifiers
are explicit and constructor injection is typed, so `this.userService.x()`
names an intra-repo class. The hooks below record exactly those facts. Each
is a direct port of the hand-written extractor it replaced;
tests/golden/typescript pins that nothing moved.
"""

from __future__ import annotations

from tree_sitter import Node as TSNode

from svarupa.extract.base import (
    CallShape,
    CallSite,
    DecoratorRef,
    FieldType,
    ImportRef,
    SymbolRef,
)
from svarupa.extract.packs.model import (
    Call,
    Carry,
    Custom,
    Define,
    Field,
    Grammar,
    Import,
    Maturity,
    MetricsSpec,
    Pack,
)
from svarupa.extract.packs.walker import Ctx, Frame

__all__ = ["GLOBAL_CALLS", "GLOBAL_OBJECTS", "PACK"]

# Globals that are not intra-repo calls. `require` is included because a
# CommonJS call is a module reference, handled as an import, not a function.
# fmt: off
GLOBAL_CALLS = frozenset(
    {
        "require", "parseInt", "parseFloat", "isNaN", "isFinite", "String",
        "Number", "Boolean", "Array", "Object", "Symbol", "BigInt", "Promise",
        "setTimeout", "setInterval", "clearTimeout", "clearInterval", "fetch",
        "encodeURIComponent", "decodeURIComponent", "structuredClone",
        "queueMicrotask", "atob", "btoa", "alert", "confirm", "prompt",
    }
)
GLOBAL_OBJECTS = frozenset(
    {
        "console", "JSON", "Math", "Object", "Array", "String", "Number",
        "Promise", "Date", "RegExp", "Map", "Set", "WeakMap", "WeakSet",
        "Symbol", "Reflect", "Proxy", "process", "Buffer", "globalThis",
        "window", "document", "localStorage", "sessionStorage", "navigator",
        "crypto", "performance", "URL", "Error", "Intl",
    }
)
# fmt: on


def _ts_string(ctx: Ctx, node: TSNode) -> str | None:
    """A static string from a `string` or substitution-free `template_string`
    node; None for anything dynamic.

    Escape sequences keep their source spelling: joining only the fragments
    turned `'/a\\'b'` into `/ab` and let two distinct routes collide.
    """
    if node.type == "template_string" and any(
        c.type == "template_substitution" for c in node.children
    ):
        return None
    if node.type in ("string", "template_string"):
        return "".join(
            ctx.text(c)
            for c in node.children
            if c.type in ("string_fragment", "escape_sequence")
        )
    return None


def _bare_type(annotation: str) -> str:
    """`Promise<UserService | null>` -> `Promise`; `: UserService` -> `UserService`.

    Deliberately crude. A wrong type name yields a failed lookup, an honest
    unresolved; it cannot manufacture an edge, because the name still has to
    match a class that exists.
    """
    t = annotation.lstrip(":").strip()
    for cut in ("|", "&", "<", "["):
        t = t.split(cut)[0]
    return t.strip().strip("()").split(".")[-1].strip()


def _named_exports(ctx: Ctx, node: TSNode, aliases: dict[str, str]) -> tuple[str, ...]:
    out: list[str] = []
    for child in node.children:
        if child.type == "export_clause":
            for spec in child.children:
                if spec.type == "export_specifier":
                    alias = spec.child_by_field_name("alias")
                    name = spec.child_by_field_name("name")
                    target = alias or name
                    if target is not None:
                        out.append(ctx.text(target))
                    if alias is not None and name is not None:
                        aliases[ctx.text(alias)] = ctx.text(name)
    return tuple(out)


def import_(ctx: Ctx, node: TSNode) -> ImportRef | None:
    source = node.child_by_field_name("source")
    if source is None:
        return None
    spec = ctx.text(source).strip("'\"`")
    names: list[str] = []
    alias: dict[str, str] = {}
    # From the tree, never a substring: `import typeA from './x'` matched
    # "import type" and stamped a false type-only flag on a runtime import.
    type_only = any(c.type == "type" for c in node.children) or any(
        ch.type == "type"
        for c in node.children
        if c.type == "import_clause"
        for ch in c.children
    )
    for child in node.children:
        if child.type != "import_clause":
            continue
        for part in child.children:
            if part.type == "identifier":  # default import
                names.append(ctx.text(part))
            elif part.type == "named_imports":
                for spec_node in part.children:
                    if spec_node.type != "import_specifier":
                        continue
                    real = spec_node.child_by_field_name("name")
                    as_name = spec_node.child_by_field_name("alias")
                    if real is None:
                        continue
                    if as_name is not None:
                        alias[ctx.text(as_name)] = ctx.text(real)
                        names.append(ctx.text(as_name))
                    else:
                        names.append(ctx.text(real))
            elif part.type == "namespace_import":
                for ident in part.children:
                    if ident.type == "identifier":
                        names.append(ctx.text(ident))
    return ImportRef(
        specifier=spec,
        names=tuple(names),
        alias_of=alias,
        evidence=ctx.node_evidence(node),
        is_relative=spec.startswith("."),
        level=0,
        type_only=type_only,
    )


def export_statement(ctx: Ctx, node: TSNode, frame: Frame, depth: int) -> None:
    # `export { x } from './y'` and `export * from './y'` are the barrel
    # pattern, the TypeScript analogue of a package __init__.
    src_node = node.child_by_field_name("source")
    if src_node is not None:
        spec = ctx.text(src_node).strip("'\"`")
        aliases: dict[str, str] = {}
        names = _named_exports(ctx, node, aliases)
        ctx.imports.append(
            ImportRef(
                specifier=spec,
                names=names,
                alias_of=aliases,
                evidence=ctx.node_evidence(node),
                is_relative=spec.startswith("."),
                level=0,
                is_from=True,
                is_reexport=True,
                is_star=not names,
            )
        )
        ctx.reexports.extend(names)
        return
    # A bare `export` wrapper: everything inside is exported. Decorators are
    # siblings preceding the declaration they decorate, so they pair here.
    pending: list[DecoratorRef] = []
    for child in node.children:
        if child.type == "decorator":
            if (d := decorator(ctx, child)) is not None:
                pending.append(d)
            continue
        if child.type in ("export", "default", ";"):
            # Keyword tokens sit between decorators and the declaration; they
            # must not consume the pending list.
            continue
        ctx.visit(
            child,
            Frame(frame.stack, frame.cls, frame.fn, exported=True, decorators=tuple(pending)),
            depth + 1,
        )
        pending = []


def variable_declarator(ctx: Ctx, node: TSNode, frame: Frame, depth: int) -> None:
    name_node = node.child_by_field_name("name")
    value = node.child_by_field_name("value")
    if name_node is not None and value is not None and value.type == "call_expression":
        callee = value.child_by_field_name("function")
        # `const x = require('spec')` is the CommonJS import, the dominant
        # dialect of real Express code; `const { A, B } = require('spec')`
        # maps the destructured names like named imports.
        if callee is not None and ctx.text(callee) == "require":
            spec = None
            req_args = value.child_by_field_name("arguments")
            if req_args is not None:
                for c in req_args.children:
                    if c.type in ("(", ")", ","):
                        continue
                    if c.type in ("string", "template_string"):
                        spec = _ts_string(ctx, c)
                    break
            req_names: list[str] = []
            if name_node.type == "identifier":
                req_names = [ctx.text(name_node)]
            elif name_node.type == "object_pattern":
                req_names = [
                    ctx.text(c)
                    for c in name_node.children
                    if c.type == "shorthand_property_identifier_pattern"
                ]
            if spec and req_names:
                row = node.start_point[0]
                ctx.imports.append(
                    ImportRef(
                        specifier=spec,
                        names=tuple(req_names),
                        alias_of={},
                        evidence=ctx.evidence(row, row),
                        is_relative=spec.startswith("."),
                        is_from=True,
                    )
                )
        if callee is not None and name_node.type == "identifier":
            ctx.ctor_assigns.append((ctx.text(name_node), ctx.text(callee)))
    if (
        name_node is not None
        and value is not None
        and value.type in ("arrow_function", "function_expression")
    ):
        name = ctx.text(name_node)
        qualified = ctx.qual((*frame.stack, name))
        ctx.symbols.append(
            SymbolRef(
                name=name,
                qualified_name=qualified,
                kind="function",
                evidence=ctx.node_evidence(node),
                exported=frame.exported,
            )
        )
        inner = Frame((*frame.stack, name), frame.cls, qualified)
        for child in value.children:
            ctx.visit(child, inner, depth + 1)
        return
    plain = frame.plain()
    for child in node.children:
        ctx.visit(child, plain, depth + 1)


def decorator(ctx: Ctx, node: TSNode) -> DecoratorRef | None:
    """`@Get(':id')` or `@Controller('users')`, with its own line."""
    expr = next((c for c in node.children if c.type not in ("@", "comment")), None)
    if expr is None:
        return None
    ev = ctx.node_evidence(node)
    if expr.type != "call_expression":
        name = ctx.text(expr)
        return DecoratorRef(name=name, arg=None, evidence=ev) if name else None
    callee = expr.child_by_field_name("function")
    if callee is None:
        return None
    arg: str | None = None
    dynamic = False
    args = expr.child_by_field_name("arguments")
    if args is not None:
        for child in args.children:
            if child.type in ("(", ")", ",", "comment"):
                continue
            # First argument only. Anything but a static string makes the
            # decorator present-but-dynamic, which must stay distinguishable
            # from "no arguments".
            if child.type in ("string", "template_string"):
                arg = _ts_string(ctx, child)
                dynamic = arg is None
            else:
                dynamic = True
            break
    return DecoratorRef(name=ctx.text(callee), arg=arg, evidence=ev, arg_dynamic=dynamic)


def bases(ctx: Ctx, node: TSNode) -> tuple[str, ...]:
    out: list[str] = []
    for child in node.children:
        if child.type == "class_heritage":
            for clause in child.children:
                if clause.type in ("extends_clause", "implements_clause"):
                    for ref in clause.children:
                        if ref.type in (
                            "identifier",
                            "type_identifier",
                            "generic_type",
                            "member_expression",
                        ):
                            out.append(_bare_type(ctx.text(ref)))
    return tuple(b for b in out if b)


def _field_of(ctx: Ctx, node: TSNode) -> tuple[str | None, str | None]:
    name: str | None = None
    ty: str | None = None
    for child in node.children:
        if child.type in ("identifier", "property_identifier") and name is None:
            name = ctx.text(child)
        elif child.type == "type_annotation":
            ty = _bare_type(ctx.text(child))
    return name, ty


def constructor_fields(ctx: Ctx, node: TSNode, frame: Frame, name: str) -> None:
    """`constructor(private readonly svc: UserService)` declares a field.

    Only parameter properties count: a plain `constructor(config: T)`
    declares no field, and recording one resolved `this.config.run()` to the
    parameter's type instead of the declared field's.
    """
    if name != "constructor" or not frame.cls:
        return
    params = node.child_by_field_name("parameters")
    if params is None:
        return
    for param in params.children:
        if param.type not in ("required_parameter", "optional_parameter"):
            continue
        text = ctx.text(param)
        if not any(
            text.lstrip().startswith(mod)
            for mod in ("private", "public", "protected", "readonly")
        ):
            continue
        field_name, ty = _field_of(ctx, param)
        if field_name and ty:
            ctx.fields.append(FieldType(frame.cls, field_name, ty))


def declared_field(ctx: Ctx, node: TSNode, cls: str) -> list[FieldType]:
    name, ty = _field_of(ctx, node)
    return [FieldType(cls, name, ty)] if name and ty else []


def _first_str_arg(ctx: Ctx, node: TSNode) -> str | None:
    args = node.child_by_field_name("arguments")
    if args is None:
        return None
    for child in args.children:
        if child.type in ("(", ")", ","):
            continue
        # Only when the string is literally the first argument.
        if child.type in ("string", "template_string"):
            return _ts_string(ctx, child)
        return None
    return None


def _first_arg_ident(ctx: Ctx, node: TSNode) -> str | None:
    args = node.child_by_field_name("arguments")
    if args is None:
        return None
    for child in args.children:
        if child.type in ("(", ")", ",", "comment"):
            continue
        return ctx.text(child) if child.type == "identifier" else None
    return None


def _ident_args(ctx: Ctx, node: TSNode) -> tuple[str, ...]:
    args = node.child_by_field_name("arguments")
    if args is None:
        return ()
    return tuple(ctx.text(c) for c in args.children if c.type == "identifier")


def _chain_base(ctx: Ctx, obj: TSNode) -> tuple[str, str, str | None] | None:
    """Unwind `base.m1(...).m2(...)` to (base, m1, m1's static first string),
    or None when the chain is not rooted at an identifier. Capped at 16 hops:
    a longer chain stays opaque text rather than being walked forever."""
    cur = obj
    innermost: TSNode | None = None
    for _ in range(16):
        if cur.type != "call_expression":
            break
        innermost = cur
        fn = cur.child_by_field_name("function")
        if fn is None or fn.type != "member_expression":
            return None
        nxt = fn.child_by_field_name("object")
        if nxt is None:
            return None
        cur = nxt
    if innermost is None or cur.type != "identifier":
        return None
    inner_fn = innermost.child_by_field_name("function")
    prop = inner_fn.child_by_field_name("property") if inner_fn is not None else None
    if prop is None:
        return None
    return (ctx.text(cur), ctx.text(prop), _first_str_arg(ctx, innermost))


def call(ctx: Ctx, node: TSNode, frame: Frame) -> CallSite | None:
    func = node.child_by_field_name("function")
    if func is None:
        return None
    fn, cls = frame.fn, frame.cls
    row = node.start_point[0]
    ev = ctx.evidence(row, row)
    first_arg = _first_str_arg(ctx, node)
    idents = _ident_args(ctx, node)
    first_ident = _first_arg_ident(ctx, node)

    if func.type == "identifier":
        name = ctx.text(func)
        if name in GLOBAL_CALLS:
            return None
        return CallSite(name, CallShape.BARE, None, ev, fn, cls, first_arg)

    if func.type == "member_expression":
        obj = func.child_by_field_name("object")
        prop = func.child_by_field_name("property")
        if prop is None or obj is None:
            return None
        name = ctx.text(prop)
        otext = ctx.text(obj)
        # A member call cites the line of the method name: in a multi-line
        # chain every verb used to cite the chain's first line.
        prow = prop.start_point[0]
        ev = ctx.evidence(prow, prow)
        if obj.type == "this":
            return CallSite(name, CallShape.SELF, "this", ev, fn, cls, first_arg)
        if obj.type == "super":
            return CallSite(name, CallShape.SUPER, "super", ev, fn, cls, first_arg)
        if obj.type == "member_expression" and otext.startswith("this."):
            parts = otext.split(".")
            return CallSite(
                name,
                CallShape.SELF_FIELD,
                parts[1] if len(parts) > 1 else None,
                ev,
                fn,
                cls,
                first_arg,
            )
        if obj.type == "identifier":
            if otext in GLOBAL_OBJECTS:
                return None
            return CallSite(
                name, CallShape.QUALIFIED, otext, ev, fn, cls, first_arg, idents, first_ident
            )
        if obj.type == "call_expression" and (chain := _chain_base(ctx, obj)):
            base, root_method, root_arg = chain
            return CallSite(
                name,
                CallShape.MEMBER,
                base,
                ev,
                fn,
                cls,
                first_arg,
                idents,
                first_ident,
                recv_call=(root_method, root_arg),
            )
        return CallSite(
            name, CallShape.MEMBER, otext, ev, fn, cls, first_arg, idents, first_ident
        )

    return None


_CLASS = Define(
    kind="class",
    inherit_exported=True,
    sets_class=True,
    own_decorators="decorator",
    member_decorators="decorator",
    bases=bases,
)

RULES = {
    "import_statement": Import(hook=import_, recurse_on_none=True),
    "export_statement": Custom(hook=export_statement),
    "class_declaration": _CLASS,
    "abstract_class_declaration": _CLASS,
    "interface_declaration": Define(
        kind="interface",
        inherit_exported=True,
        body_field=None,
        records_class=False,
        keeps_decorators=False,
    ),
    "method_definition": Define(kind="method", after=constructor_fields),
    "public_field_definition": Field(hook=declared_field),
    "property_signature": Field(hook=declared_field),
    "function_declaration": Define(
        kind="function",
        inherit_exported=True,
        records_class=False,
        keeps_decorators=False,
        recurse_without_name=True,
    ),
    "lexical_declaration": Carry(),
    "variable_declaration": Carry(),
    "variable_declarator": Custom(hook=variable_declarator),
    "call_expression": Call(hook=call),
}


def _count_params(fn: TSNode) -> int:
    if fn.child_by_field_name("parameter") is not None:
        return 1  # `x => x`
    params = fn.child_by_field_name("parameters")
    if params is None:
        return 0
    return sum(
        1 for c in params.children if c.type in ("required_parameter", "optional_parameter")
    )


METRICS = MetricsSpec(
    function_types=frozenset(
        {
            "function_declaration",
            "generator_function_declaration",
            "method_definition",
            "arrow_function",
            "function_expression",
        }
    ),
    branch_types=frozenset(
        {
            "if_statement",
            "for_statement",
            "for_in_statement",
            "while_statement",
            "do_statement",
            "switch_case",
            "catch_clause",
            "ternary_expression",
        }
    ),
    nesting_types=frozenset(
        {
            "if_statement",
            "for_statement",
            "for_in_statement",
            "while_statement",
            "do_statement",
            "switch_statement",
            "try_statement",
        }
    ),
    count_params=_count_params,
    boolean_ops=(("binary_expression", frozenset({"&&", "||", "??"})),),
    else_if_parents=frozenset({"else_clause"}),
)


PACK = Pack(
    lang="typescript",
    grammar=Grammar(
        distribution="tree-sitter-typescript",
        version="0.23.2",
        module="tree_sitter_typescript",
        default="language_typescript",
        by_suffix=((".tsx", "language_tsx"),),
    ),
    maturity=Maturity.STABLE,
    metrics=METRICS,
    qualified_prefix=lambda path: path.rsplit(".", 1)[0].replace("/", "."),
    decorator=decorator,
    rules=RULES,
)
