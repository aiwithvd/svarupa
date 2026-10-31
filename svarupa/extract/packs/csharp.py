"""C# language pack (experimental).

A namespace replaces the path-based prefix: `namespace Shop.Api;` and
`namespace Shop.Api { ... }` qualify their types the same way. Attributes
are `attribute_list` children of a declaration, and `public` is a
`modifier` child. An unqualified call inside a type is a call on `this`.
"""

from __future__ import annotations

import re

from tree_sitter import Node as TSNode

from svarupa.extract.base import CallShape, CallSite, DecoratorRef, FieldType, ImportRef
from svarupa.extract.packs.model import (
    LAST_NAMED,
    Call,
    Custom,
    Define,
    Field,
    Grammar,
    Import,
    Maturity,
    MetricsSpec,
    Pack,
    RiskSpec,
    Sink,
    Unsafe,
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
    alias = node.child_by_field_name("name")
    last = dotted.rsplit(".", 1)[-1]
    # An alias names a type, so `H.Go()` calls into it. A static using stays
    # `names=()`: its bare `Go()` is a call on `this` and cannot be routed.
    names_type = alias is not None
    return ImportRef(
        specifier=dotted,
        names=(last,) if names_type else (),
        alias_of={ctx.text(alias): last} if alias is not None else {},
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


def _count_params(fn: TSNode) -> int:
    params = fn.child_by_field_name("parameters")
    if params is None:
        return 0
    if params.type == "implicit_parameter":
        return 1  # `x => x`
    return sum(1 for c in params.children if c.type == "parameter")


_MEMBERS = ("property_declaration", "indexer_declaration", "event_declaration")


def _has_body(node: TSNode) -> bool:
    """`get;` has no body; `get { ... }` and `get => x` do. An arrow clause is
    a function only as the body of a property or indexer (`int P => x;`)."""
    if node.type == "accessor_declaration":
        return any(c.type in ("block", "arrow_expression_clause") for c in node.children)
    if node.type == "arrow_expression_clause":
        return node.parent is not None and node.parent.type in _MEMBERS
    return True


def _text(node: TSNode | None) -> str | None:
    return node.text.decode() if node is not None and node.text else None


def _member_name(owner: TSNode | None, word: str | None) -> str | None:
    """`Prop.get`, or `this.get` for an indexer."""
    if owner is None or word is None:
        return None
    if owner.type == "indexer_declaration":
        return f"this.{word}"
    name = _text(owner.child_by_field_name("name"))
    return f"{name}.{word}" if name else None


def _function_name(node: TSNode) -> str | None:
    """A name for the members whose `name` field does not name them."""
    if node.type == "accessor_declaration":
        owner = node.parent.parent if node.parent is not None else None
        return _member_name(owner, _text(node.child_by_field_name("name")))
    if node.type == "arrow_expression_clause":
        return _member_name(node.parent, "get")
    if node.type == "conversion_operator_declaration":
        ty = _text(node.child_by_field_name("type"))
        return f"operator {ty}" if ty else None
    return _text(node.child_by_field_name("operator"))


# A `case` label is its own `switch_section`, so stacked labels count once
# each. A switch expression arm counts like a Java switch rule; `default`
# and the `_ =>` discard arm are not decisions.
METRICS = MetricsSpec(
    function_types=frozenset(
        {
            "method_declaration",
            "constructor_declaration",
            "local_function_statement",
            "lambda_expression",
            "accessor_declaration",
            "arrow_expression_clause",
            "operator_declaration",
            "conversion_operator_declaration",
            "destructor_declaration",
            "anonymous_method_expression",
        }
    ),
    branch_types=frozenset(
        {
            "if_statement",
            "for_statement",
            "foreach_statement",
            "while_statement",
            "do_statement",
            "catch_clause",
            "conditional_expression",
            "switch_section",
            "switch_expression_arm",
        }
    ),
    nesting_types=frozenset(
        {
            "if_statement",
            "for_statement",
            "foreach_statement",
            "while_statement",
            "do_statement",
            "switch_statement",
            "switch_expression",
            "try_statement",
        }
    ),
    count_params=_count_params,
    boolean_ops=(
        ("binary_expression", frozenset({"&&", "||", "??"})),
        ("assignment_expression", frozenset({"??="})),
    ),
    not_branch=(("switch_section", "default"), ("switch_expression_arm", "discard")),
    else_if_parents=frozenset({"if_statement"}),
    is_function=_has_body,
    name_of=_function_name,
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


def _called_name(node: TSNode | None) -> str | None:
    """`Foo` for `Foo` and `Foo<T>`."""
    if node is None:
        return None
    if node.type == "generic_name":
        node = next((c for c in node.children if c.type == "identifier"), node)
    return _text(node)


def risk_callee(ctx: Ctx, node: TSNode) -> str | None:
    """`conn.Query`, `x?.Foo` as `x.Foo`, `new SqlCommand`; None otherwise."""
    if node.type == "object_creation_expression":
        typ = node.child_by_field_name("type")
        return f"new {ctx.text(typ)}" if typ is not None else None
    function = node.child_by_field_name("function")
    if function is None or function.end_byte - function.start_byte > 200:
        return None
    if function.type == "conditional_access_expression":  # `x?.Foo()` reads as `x.Foo`
        return ctx.text(function).replace("?.", ".")
    if function.type == "member_access_expression":
        obj = function.child_by_field_name("expression")
        name = _called_name(function.child_by_field_name("name"))
        return f"{ctx.text(obj)}.{name}".replace("?.", ".") if obj and name else None
    return _called_name(function) if function.type in ("identifier", "generic_name") else None


def _p(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern, re.DOTALL)


_INVOKE = frozenset({"invocation_expression"})
_NEW = frozenset({"object_creation_expression"})
_ASSIGN = frozenset({"assignment_expression"})
_ACCESS = frozenset({"member_access_expression"})
# A database receiver: `conn`, `_db`, `catalogConnection`.
_CONN = r"_?(?:conn|connection|db)|\w*Connection"
# `HttpContext` and a bare `context` are not databases: only a Db-named context.
_STORE = _CONN + r"|_?(?:db|\w*Db)\w*[Cc]ontext|\w*(?:Repository|Repo)"
_VERBS = (
    r"(?:Find\w*|First\w*|Single\w*|Where|ToList\w*|Any\w*|Count\w*"
    r"|SaveChanges\w*|Query\w*|Execute\w*)"
)

# fmt: off
RISKS = RiskSpec(
    call_types=frozenset({"invocation_expression", "object_creation_expression"}),
    callee=risk_callee,
    string_types=frozenset({"string_literal", "verbatim_string_literal", "raw_string_literal", "interpolated_string_expression"}),
    interpolations=(("interpolated_string_expression", "interpolation"),),
    concat=(("binary_expression", frozenset({"+"})),),
    format_callee=re.compile(r"^[Ss]tring\.Format$"),
    assigns=(
        ("variable_declarator", "name", LAST_NAMED),
        ("assignment_expression", "left", "right"),
    ),
    unwrap=frozenset({"parenthesized_expression", "argument", "await_expression"}),
    sinks=(
        # `FromSqlInterpolated` and `ExecuteSqlInterpolated` bind parameters: not sinks.
        Sink("sql", re.compile(r"(?:^|\.)(?:ExecuteSqlRaw|ExecuteSqlRawAsync|FromSqlRaw|SqlQueryRaw)$")),
        Sink("sql", re.compile(r"^new (?:[\w.]+\.)?(?:SqlCommand|NpgsqlCommand|MySqlCommand|SqliteCommand)$")),
        Sink("sql", re.compile(r"(?:^|\.)(?:" + _CONN + r")\.(?:Query|QueryAsync|Execute|ExecuteAsync|QueryFirst\w*|QuerySingle\w*)$")),
        Sink("shell", re.compile(r"(?:^|\.)Process\.Start$|^new (?:[\w.]+\.)?ProcessStartInfo$")),
        Sink("code", re.compile(r"(?:^|\.)CSharpScript\.(?:EvaluateAsync|RunAsync)$")),
    ),
    unsafe=(
        Unsafe("tls-verify-off", "certificate validation off", _ASSIGN, _p(r"(?:\w+\.)*ServerCertificateCustomValidationCallback\s*=.*(?:=>\s*(?:true|\{\s*return true;\s*\})|delegate\s*(?:\([^)]*\))?\s*\{\s*return true;\s*\})")),
        Unsafe("tls-verify-off", "certificate validation off", _ACCESS, _p(r".*DangerousAcceptAnyServerCertificateValidator")),
        Unsafe("unsafe-deserialize", "BinaryFormatter", _NEW, _p(r"new (?:[\w.]+\.)?BinaryFormatter\(.*")),
        Unsafe("unsafe-deserialize", "type names read from the data", _ASSIGN, _p(r"(?:\w+\.)*TypeNameHandling\s*=\s*(?:\w+\.)*TypeNameHandling\.(?:All|Auto|Objects)")),
        Unsafe("weak-hash", "MD5 or SHA-1", _INVOKE, _p(r"(?:\w+\.)*(?:MD5|SHA1)\.Create\(.*")),
        Unsafe("weak-hash", "MD5 or SHA-1", _NEW, _p(r"new (?:[\w.]+\.)?(?:MD5CryptoServiceProvider|SHA1Managed|SHA1CryptoServiceProvider)\(.*")),
        Unsafe("cors-any-origin", "CORS allows any origin", _INVOKE, _p(r".*\.AllowAnyOrigin\(\s*\)")),
    ),
    # No auth_markers: ASP.NET routes are not extracted, and a marker would
    # leak into the shared defaults. Add `Authorize` once routes are extracted.
    loops=(
        ("for_statement", "body"),
        ("foreach_statement", "body"),
        ("while_statement", "body"),
        ("do_statement", "body"),
    ),
    loop_callee=re.compile(r"\.(?:ForEach|Select|SelectMany)$"),
    arg_wrapper="argument",
    datastore_calls=re.compile(r"(?:^|\.)(?:" + _STORE + r")\.(?:\w+\.)?" + _VERBS + "$"),
    async_marker="async",
    async_holder="modifier",
    blocking=re.compile(
        r"(?:^|\.)Thread\.Sleep$|\.Wait$|\.GetAwaiter\(\)\.GetResult$"
        r"|(?:^|\.)File\.(?:ReadAll|WriteAll)(?:Text|Bytes|Lines)$"
    ),
    catch_types=frozenset({"catch_clause"}),
    quiet=frozenset({"comment"}),
)
# fmt: on


PACK = Pack(
    lang="csharp",
    grammar=Grammar(
        distribution="tree-sitter-c-sharp",
        version="0.23.5",
        module="tree_sitter_c_sharp",
        default="language",
    ),
    maturity=Maturity.EXPERIMENTAL,
    metrics=METRICS,
    risks=RISKS,
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
