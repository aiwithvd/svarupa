"""Java language pack.

Every file declares its package, so imports resolve through those
declarations (modules.JvmPackages) and source roots never need guessing.
Annotations live inside `modifiers`; `public` there is what exported means.
An unqualified call inside a class is a call on `this`, unless the file
statically imported that name.
"""

from __future__ import annotations

import re
from dataclasses import replace

from tree_sitter import Node as TSNode
from tree_sitter import Point

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
    Import,
    Local,
    Maturity,
    MetricsSpec,
    Pack,
    RiskSpec,
    Sink,
    Unsafe,
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
        # Only static imports bring a bare method name into scope; a class
        # import (`import a.b.Order`) names a type, never a method to call.
        static = {
            n for imp in ctx.imports for n in imp.names if not imp.specifier.endswith("." + n)
        }
        if cls and name not in static:
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


# ----------------------------------------------------------------------
# parameter and local types

_CLASS_TYPES = ("type_identifier", "generic_type", "scoped_type_identifier")
_GENERIC_OWNERS = (
    "method_declaration",
    "constructor_declaration",
    "class_declaration",
    "interface_declaration",
    "record_declaration",
    "enum_declaration",
)


def _generic_names(ctx: Ctx, node: TSNode) -> set[str]:
    """The type parameters in scope: `T` in `<T extends Repo>` on the method or
    on any class around it."""
    names: set[str] = set()
    cur: TSNode | None = node
    while cur is not None:
        if cur.type in _GENERIC_OWNERS:
            params = cur.child_by_field_name("type_parameters")
            for p in params.named_children if params is not None else ():
                ident = next((c for c in p.children if c.type == "type_identifier"), None)
                if ident is not None:
                    names.add(ctx.text(ident))
        cur = cur.parent
    return names


def _declared(ctx: Ctx, ty: TSNode | None, generics: set[str]) -> str:
    """The class a declared type names; "" for `var`, a generic parameter, an
    array or a primitive. `Optional<T>` stays `Optional`."""
    if ty is None or ty.type not in _CLASS_TYPES:
        return ""
    name = _type_name(ctx.text(ty))
    return "" if name == "var" or name in generics else name


def _binding(
    ctx: Ctx, fn: str, ty: TSNode | None, name: TSNode | None, generics: set[str]
) -> list[LocalType]:
    """A name always shadows a field, so an untyped one is recorded too, with
    no type. It has no scope yet: a parameter is in scope in its function."""
    if name is None or name.type != "identifier":
        return []
    return [LocalType(fn, ctx.text(name), _declared(ctx, ty, generics))]


def _has_dims(node: TSNode) -> bool:
    return any(c.type == "dimensions" for c in node.children)


def _sized(node: TSNode, ty: TSNode | None) -> TSNode | None:
    """The declared type, or None when the name adds array dimensions."""
    return None if _has_dims(node) else ty


def _formal(ctx: Ctx, fn: str, p: TSNode, generics: set[str]) -> list[LocalType]:
    """A `formal_parameter` or `spread_parameter`. `Repo... r` is an array of
    Repo, so a call on r is not a call on Repo."""
    if p.type == "formal_parameter":
        ty = _sized(p, p.child_by_field_name("type"))
        return _binding(ctx, fn, ty, p.child_by_field_name("name"), generics)
    if p.type == "spread_parameter":
        d = next((c for c in p.children if c.type == "variable_declarator"), None)
        return _binding(ctx, fn, None, d.child_by_field_name("name") if d else None, generics)
    return []


def _own_parameters(ctx: Ctx, node: TSNode, fn: str) -> list[LocalType]:
    params = node.child_by_field_name("parameters")
    if params is None:
        return []
    generics = _generic_names(ctx, node)
    out: list[LocalType] = []
    for p in params.named_children:
        out += _formal(ctx, fn, p, generics)
    return out


def parameter_locals(ctx: Ctx, node: TSNode, qualified: str) -> list[LocalType]:
    return _own_parameters(ctx, node, qualified) + _enclosing_scope(ctx, node, qualified)


_METHODS = ("method_declaration", "constructor_declaration")
# Where a class is a member or a top-level type, not a local class.
_TYPE_HOMES = ("program", "class_body", "interface_body", "enum_body_declarations")
_TYPE_DECLARATIONS = (
    "class_declaration",
    "interface_declaration",
    "enum_declaration",
    "record_declaration",
)


def _enclosing_scope(ctx: Ctx, node: TSNode, fn: str) -> list[LocalType]:
    """A method of an anonymous or local class sees the parameters and locals
    of the method around the class. Each binding is recorded again under this
    method's name, with its own scope, so the method's own bindings are the
    inner ones. A field of the class, its own or inherited, hides the outer
    name, so a name the class declares is left out; when a supertype is not
    in this file its fields are unknown, so the names bind untyped."""
    owner, supers = _inner_class(ctx, node)
    outer = owner.parent if owner is not None else None
    while outer is not None and outer.type not in _METHODS:
        outer = outer.parent
    if owner is None or outer is None:
        return []
    found = ctx.whole(_own_parameters(ctx, outer, fn), outer)
    found += _scan_scope(ctx, outer, owner, fn)
    found += _enclosing_scope(ctx, outer, fn)
    fields, known = _class_fields(ctx, owner, supers, set())
    if not known:
        found = [replace(loc, type_name="") for loc in found]
    return [loc for loc in found if loc.name not in fields]


def _inner_class(ctx: Ctx, method: TSNode) -> tuple[TSNode | None, list[str]]:
    """The anonymous or local class a method is declared in, with its
    supertypes; None for a member or top-level class."""
    body = method.parent
    owner = body.parent if body is not None and body.type == "class_body" else None
    if owner is None:
        return None, []
    if owner.type == "object_creation_expression":
        ty = owner.child_by_field_name("type")
        return owner, [_type_name(ctx.text(ty))] if ty is not None else []
    home = owner.parent.type if owner.parent is not None else "program"
    if owner.type in _TYPE_DECLARATIONS and home not in _TYPE_HOMES:
        return owner, list(bases(ctx, owner))
    return None, []


def _scan_scope(ctx: Ctx, outer: TSNode, skip: TSNode, fn: str) -> list[LocalType]:
    """The bindings made in `outer`, outside the class `skip`."""
    out: list[LocalType] = []
    stack = [c for c in outer.children if c.type == "block"]
    while stack:
        n = stack.pop()
        if n.id == skip.id:
            continue
        hook = _SCOPE_HOOKS.get(n.type)
        if hook is not None:
            out += hook(ctx, n, fn)
        stack.extend(n.children)
    return out


def _class_fields(
    ctx: Ctx, owner: TSNode, supers: list[str], seen: set[str]
) -> tuple[set[str], bool]:
    """The field names a class declares and inherits from supertypes in this
    file, and whether every supertype was found here."""
    names = _declared_fields(ctx, owner)
    known = True
    for name in supers:
        decl = None if name in seen else _type_in_file(ctx, owner, name)
        if decl is None:
            known = known and name in seen
            continue
        inherited, ok = _class_fields(ctx, decl, list(bases(ctx, decl)), seen | {name})
        names |= inherited
        known = known and ok
    return names, known


def _declared_fields(ctx: Ctx, owner: TSNode) -> set[str]:
    body = owner.child_by_field_name("body") or next(
        (c for c in owner.children if c.type == "class_body"), None
    )
    names: set[str] = set()
    for c in body.named_children if body is not None else ():
        if c.type in ("field_declaration", "constant_declaration"):
            names |= _variable_names(ctx, c)
    return names


def _variable_names(ctx: Ctx, declaration: TSNode) -> set[str]:
    declarators = [d for d in declaration.named_children if d.type == "variable_declarator"]
    return {
        ctx.text(n) for d in declarators if (n := d.child_by_field_name("name")) is not None
    }


def _type_in_file(ctx: Ctx, node: TSNode, name: str) -> TSNode | None:
    root = node
    while root.parent is not None:
        root = root.parent
    stack = [root]
    while stack:
        n = stack.pop()
        ident = n.child_by_field_name("name") if n.type in _TYPE_DECLARATIONS else None
        if ident is not None and ctx.text(ident) == name:
            return n
        stack.extend(n.named_children)
    return None


def _rest_of_block(node: TSNode) -> tuple[Point, Point]:
    """A local is in scope from its declaration to the end of its block; a
    `switch` case group shares one block with the groups after it."""
    block = node.parent or node
    if block.type == "switch_block_statement_group" and block.parent is not None:
        block = block.parent
    return node.start_point, block.end_point


def local_variable_locals(ctx: Ctx, node: TSNode, fn: str) -> list[LocalType]:
    generics = _generic_names(ctx, node)
    ty = node.child_by_field_name("type")
    start, end = _rest_of_block(node)
    out: list[LocalType] = []
    for d in node.children:
        if d.type == "variable_declarator":
            name = d.child_by_field_name("name")
            found = _binding(ctx, fn, _sized(d, ty), name, generics)
            out += ctx.rescoped(found, start, end, name)
    return out


def _typed_name(ctx: Ctx, node: TSNode, fn: str) -> list[LocalType]:
    ty = _sized(node, node.child_by_field_name("type"))
    return _binding(ctx, fn, ty, node.child_by_field_name("name"), _generic_names(ctx, node))


def for_locals(ctx: Ctx, node: TSNode, fn: str) -> list[LocalType]:
    """`for (T x : xs)` binds in the loop body, not in `xs`."""
    body = node.child_by_field_name("body") or node
    name = node.child_by_field_name("name")
    return ctx.rescoped(_typed_name(ctx, node, fn), body.start_point, body.end_point, name)


def resource_locals(ctx: Ctx, node: TSNode, fn: str) -> list[LocalType]:
    """A resource `T x = open()` is in scope to the end of the `try` block."""
    statement = node.parent.parent if node.parent is not None else None
    body = statement.child_by_field_name("body") if statement is not None else None
    end = (body or node).end_point
    return ctx.rescoped(_typed_name(ctx, node, fn), node.start_point, end)


_BLOCKS = ("block", "constructor_body", "switch_block_statement_group")
_ARMS = ("switch_rule", "lambda_expression")


def _pattern_scope(node: TSNode) -> tuple[TSNode, TSNode | None]:
    """(scope, block) of a pattern binding: a `case` arm, a lambda, or else the
    statement holding it. After `if (!(o instanceof T x)) return;` the name is
    still bound in the rest of the block, so the statement's block comes too."""
    cur = node
    while cur.parent is not None:
        if cur.type in _ARMS:
            return cur, None
        if cur.type == "switch_label":
            return cur.parent, None
        if cur.parent.type in _BLOCKS:
            return cur, cur.parent
        cur = cur.parent
    return cur, None


def _pattern_binding(
    ctx: Ctx, fn: str, node: TSNode, ty: TSNode | None, name: TSNode | None
) -> list[LocalType]:
    """Typed in its arm or statement. Past the statement, where the rest of
    the block uses the name, it only hides a field (flow scoping is not
    followed)."""
    scope, block = _pattern_scope(node)
    found = _binding(ctx, fn, ty, name, _generic_names(ctx, node))
    out = ctx.over(found, scope)
    if block is None or name is None:
        return out
    if ctx.mentions(ctx.text(name), scope.end_byte, block.end_byte):
        hidden = [LocalType(fn, ctx.text(name), "")]
        out += ctx.rescoped(hidden, scope.end_point, block.end_point)
    return out


def pattern_locals(ctx: Ctx, node: TSNode, fn: str) -> list[LocalType]:
    """`case T x` and a record component `T x`: a type child, then the name."""
    ty = next((c for c in node.children if c.type in _CLASS_TYPES), None)
    name = next((c for c in node.children if c.type == "identifier"), None)
    return _pattern_binding(ctx, fn, node, ty, name)


def instanceof_locals(ctx: Ctx, node: TSNode, fn: str) -> list[LocalType]:
    """Only `x instanceof T r` binds; a plain `x instanceof T` binds nothing."""
    name = node.child_by_field_name("name")
    if name is None:
        return []
    ty = node.child_by_field_name("right")
    return _pattern_binding(ctx, fn, node, _sized(node, ty), name)


def _catch_type(node: TSNode) -> TSNode | None:
    """The one type a catch names; None for a union `A | B`."""
    catch_type = next((c for c in node.children if c.type == "catch_type"), None)
    types = [c for c in catch_type.children if c.type in _CLASS_TYPES] if catch_type else []
    return types[0] if len(types) == 1 else None


def catch_locals(ctx: Ctx, node: TSNode, fn: str) -> list[LocalType]:
    """`catch (A | B e)` is a union, so e has no single type. It is bound in
    its catch clause."""
    name = node.child_by_field_name("name") or next(
        (c for c in node.children if c.type == "identifier"), None
    )
    found = _binding(ctx, fn, _catch_type(node), name, _generic_names(ctx, node))
    return ctx.over(found, node.parent or node)


def _lambda_bindings(ctx: Ctx, params: TSNode, fn: str) -> list[LocalType]:
    if params.type == "identifier":
        return [LocalType(fn, ctx.text(params), "")]
    generics = _generic_names(ctx, params)
    out: list[LocalType] = []
    for p in params.named_children:
        if p.type == "identifier":
            out.append(LocalType(fn, ctx.text(p), ""))
        else:
            out += _formal(ctx, fn, p, generics)
    return out


def lambda_locals(ctx: Ctx, node: TSNode, fn: str) -> list[LocalType]:
    """`(Order o) -> ..` is typed, `o -> ..` and `(a, b) -> ..` rebind untyped,
    in the lambda only."""
    params = node.child_by_field_name("parameters")
    if params is None:
        return []
    return ctx.over(_lambda_bindings(ctx, params, fn), node)


_SCOPE_HOOKS = {
    "local_variable_declaration": local_variable_locals,
    "enhanced_for_statement": for_locals,
    "resource": resource_locals,
    "type_pattern": pattern_locals,
    "instanceof_expression": instanceof_locals,
    "record_pattern_component": pattern_locals,
    "catch_formal_parameter": catch_locals,
    "lambda_expression": lambda_locals,
}


def _type_rule(kind: str) -> Define:
    return Define(
        kind=kind,
        sets_class=True,
        bases=bases,
        exported_by=_public,
        decorators_from=annotations_of,
    )


def _count_params(fn: TSNode) -> int:
    params = fn.child_by_field_name("parameters")
    if params is None:
        return 0
    if params.type == "identifier":
        return 1  # `x -> x`
    return sum(
        1
        for c in params.children
        if c.type in ("formal_parameter", "spread_parameter", "identifier")
    )


METRICS = MetricsSpec(
    function_types=frozenset(
        {"method_declaration", "constructor_declaration", "lambda_expression"}
    ),
    branch_types=frozenset(
        {
            "if_statement",
            "for_statement",
            "enhanced_for_statement",
            "while_statement",
            "do_statement",
            "catch_clause",
            "ternary_expression",
            "switch_label",
        }
    ),
    nesting_types=frozenset(
        {
            "if_statement",
            "for_statement",
            "enhanced_for_statement",
            "while_statement",
            "do_statement",
            "switch_expression",
            "try_statement",
        }
    ),
    count_params=_count_params,
    boolean_ops=(("binary_expression", frozenset({"&&", "||"})),),
    not_branch=(("switch_label", "default"),),
    else_if_parents=frozenset({"if_statement"}),
)


def risk_callee(ctx: Ctx, node: TSNode) -> str | None:
    if node.type == "object_creation_expression":
        typ = node.child_by_field_name("type")
        return f"new {ctx.text(typ)}" if typ is not None else None
    name = node.child_by_field_name("name")
    if name is None:
        return None
    obj = node.child_by_field_name("object")
    if obj is not None and obj.end_byte - obj.start_byte > 200:
        return None
    return f"{ctx.text(obj)}.{ctx.text(name)}" if obj is not None else ctx.text(name)


def _p(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern, re.DOTALL)


_INVOKE = frozenset({"method_invocation"})
_NEW = frozenset({"object_creation_expression"})

# fmt: off
RISKS = RiskSpec(
    call_types=frozenset({"method_invocation", "object_creation_expression"}),
    callee=risk_callee,
    string_types=frozenset({"string_literal"}),
    interpolations=(),
    concat=(("binary_expression", frozenset({"+"})),),
    format_callee=re.compile(r"^String\.format$|\.formatted$"),
    assigns=(
        ("variable_declarator", "name", "value"),
        ("assignment_expression", "left", "right"),
    ),
    unwrap=frozenset({"parenthesized_expression"}),
    sinks=(
        Sink("sql", re.compile(r"(?:^|\.)(?:executeQuery|executeUpdate|executeLargeUpdate|addBatch|prepareStatement|prepareCall|createQuery|createNativeQuery|createSQLQuery|queryForList|queryForObject|queryForMap|queryForRowSet|batchUpdate)$")),
        # `execute`, `query` and `update` are SQL only on a database receiver.
        Sink("sql", re.compile(r"(?i)(?:^|\.)\w*(?:jdbc|template|stmt|statement|conn|connection|session|entitymanager|em|db|database)\.(?:execute|query|update)$")),
        Sink("shell", re.compile(r"getRuntime\(\)\.exec$|^new ProcessBuilder$")),
        Sink("code", re.compile(r"(?:^|\.)(?:eval|parseExpression)$")),
    ),
    unsafe=(
        Unsafe("unsafe-deserialize", "ObjectInputStream", _NEW, _p(r"new (?:ObjectInputStream|XMLDecoder)\(.*")),
        Unsafe("weak-hash", "MD5 or SHA-1", _INVOKE, _p(r"MessageDigest\.getInstance\(\s*\"(?:MD5|MD2|SHA-?1)\".*")),
        Unsafe("tls-verify-off", "hostname verification off", _NEW, _p(r"new (?:NoopHostnameVerifier|TrustAllStrategy)\(.*")),
        Unsafe("cors-any-origin", "CORS origin '*'", _INVOKE, _p(r".*\.(?:allowedOrigins|setAllowedOrigins|addAllowedOrigin)\(\s*(?:(?:Arrays\.)?asList\(|List\.of\()?\s*\"\*\"\s*\)?\s*\)")),
        Unsafe("cors-any-origin", "@CrossOrigin allows every origin", frozenset({"marker_annotation"}), _p(r"@CrossOrigin")),
        Unsafe("cors-any-origin", "@CrossOrigin allows every origin", frozenset({"annotation"}), _p(r"@CrossOrigin\(\s*(?:origins\s*=\s*)?\"\*\"\s*\)")),
        Unsafe("no-timeout", "HttpClient without connect timeout", _INVOKE, _p(r"HttpClient\.newHttpClient\(\s*\)")),
        Unsafe("no-timeout", "RestTemplate without timeouts", _NEW, _p(r"new RestTemplate\(\s*\)")),
        Unsafe("no-retry", "RabbitMQ automatic recovery turned off", _INVOKE, _p(r".*\.setAutomaticRecoveryEnabled\(\s*false\s*\)")),
    ),
    auth_markers=frozenset({"PreAuthorize", "Secured", "RolesAllowed", "PostAuthorize"}),
    loops=(
        ("for_statement", "body"),
        ("enhanced_for_statement", "body"),
        ("while_statement", "body"),
        ("do_statement", "body"),
    ),
    # `Optional.map` runs once: only a stream maps per item.
    loop_callee=re.compile(r"\.stream\(\).*\.(?:map|flatMap|forEach)$|\.forEach$"),
    datastore_calls=re.compile(
        r"(?:^|\.)\w*(?:[Rr]epository|[Rr]epo|[Dd]ao|[Mm]apper)\.(?:find\w*|get\w*|save\w*|select\w*|insert\w*|update\w*|delete\w*|count\w*|exists\w*)$"
        r"|(?:^|\.)(?:\w*(?:[Jj]dbc|[Mm]ongo)Template|jdbc|em|entityManager|\w*EntityManager|session|\w*Session)\.(?:query\w*|find|get|createQuery|createNativeQuery|update|execute|persist|merge)$"
        r"|(?:^|\.)(?:executeQuery|executeUpdate)$"
    ),
    catch_types=frozenset({"catch_clause"}),
    quiet=frozenset({"line_comment", "block_comment"}),
)
# fmt: on


PACK = Pack(
    lang="java",
    grammar=Grammar(
        distribution="tree-sitter-java",
        version="0.23.5",
        module="tree_sitter_java",
        default="language",
    ),
    maturity=Maturity.STABLE,
    metrics=METRICS,
    risks=RISKS,
    rules={
        "package_declaration": Custom(hook=package_declaration),
        "import_declaration": Import(hook=import_),
        "class_declaration": _type_rule("class"),
        "enum_declaration": _type_rule("class"),
        "record_declaration": _type_rule("class"),
        "interface_declaration": _type_rule("interface"),
        "method_declaration": Define(
            kind="method",
            exported_by=_public,
            decorators_from=annotations_of,
            locals=parameter_locals,
        ),
        "constructor_declaration": Define(
            kind="method",
            exported_by=_public,
            decorators_from=annotations_of,
            locals=parameter_locals,
        ),
        **{kind: Local(hook=hook) for kind, hook in _SCOPE_HOOKS.items()},
        # A lambda in a field initializer binds its parameters there too.
        "lambda_expression": Local(hook=lambda_locals, top_level=True),
        "field_declaration": Field(hook=declared_field),
        "method_invocation": Call(hook=call),
    },
    qualified_prefix=lambda path: path.rsplit(".", 1)[0].replace("/", "."),
    modules=JvmPackages,
)
