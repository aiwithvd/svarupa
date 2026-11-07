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
from tree_sitter import Point

from svarupa.extract.base import CallShape, CallSite, ImportRef, LocalType, SymbolRef
from svarupa.extract.packs.model import (
    Call,
    Custom,
    Define,
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
    ctx.locals.extend(ctx.whole(signature_locals(ctx, node, qualified), node))
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


# ----------------------------------------------------------------------
# parameter and local types
#
# A closure has no name of its own: its calls carry the enclosing function's
# `enclosing`, so its parameters and locals are recorded under that function,
# each in scope in the closure only. An outer local is visible in a closure;
# a closure's local is not visible outside it.

_TYPE_NAMES = BUILTINS | {"nil"}


def _generic_names(ctx: Ctx, node: TSNode) -> set[str]:
    """The type parameters in scope: `[T any]` on a function, or the `T` of a
    receiver such as `*Stack[T]`, on this node or any function around it."""
    names: set[str] = set()
    cur: TSNode | None = node
    while cur is not None:
        for decl in _named(cur.child_by_field_name("type_parameters")):
            names.update(ctx.text(n) for n in decl.children_by_field_name("name"))
        recv = cur.child_by_field_name("receiver")
        if cur.type == "method_declaration" and recv is not None:
            stack = list(recv.children)
            while stack:
                n = stack.pop()
                if n.type == "type_arguments":
                    names.update(ctx.text(i) for i in _descendants(n, "type_identifier"))
                else:
                    stack.extend(n.children)
        cur = cur.parent
    return names


def _named(node: TSNode | None) -> list[TSNode]:
    return list(node.named_children) if node is not None else []


def _descendants(node: TSNode, kind: str) -> list[TSNode]:
    out: list[TSNode] = []
    stack = [node]
    while stack:
        n = stack.pop()
        if n.type == kind:
            out.append(n)
        stack.extend(n.children)
    return out


def _unwrapped(ty: TSNode | None) -> TSNode | None:
    """Strip parentheses, one pointer and a generic instantiation. A pointer to
    a pointer has no methods, so it gives None."""
    pointer = False
    while ty is not None and ty.type in ("parenthesized_type", "pointer_type", "generic_type"):
        if ty.type == "generic_type":
            ty = ty.child_by_field_name("type")
            continue
        if ty.type == "pointer_type":
            if pointer:
                return None
            pointer = True
        ty = next(iter(ty.named_children), None)
    return ty


def _type_of(ctx: Ctx, ty: TSNode | None, generics: set[str]) -> str:
    """The named type a declared type denotes; "" for a builtin, a generic
    parameter, a slice, array, map, channel, func, struct or interface literal,
    or a pointer to a pointer. `*Repo` and `Repo[int]` are `Repo`, `pkg.Repo` is
    kept qualified so the resolver can look it up in that package."""
    ty = _unwrapped(ty)
    if ty is None:
        return ""
    if ty.type == "qualified_type":
        return _qualified(ctx, ty)
    name = ctx.text(ty)
    if ty.type != "type_identifier" or name in generics or name in _TYPE_NAMES:
        return ""
    return name


def _qualified(ctx: Ctx, ty: TSNode) -> str:
    pkg = ty.child_by_field_name("package")
    name = ty.child_by_field_name("name")
    return f"{ctx.text(pkg)}.{ctx.text(name)}" if pkg is not None and name is not None else ""


def _untyped(ctx: Ctx, fn: str, names: list[TSNode]) -> list[LocalType]:
    return [LocalType(fn, t, "") for n in names if (t := ctx.text(n)) != "_"]


def _identifiers(node: TSNode | None) -> list[TSNode]:
    """The plain names on the left of `a, b := ...`."""
    if node is None:
        return []
    return [n for n in node.named_children if n.type == "identifier"]


def _parameter_list(
    ctx: Ctx, fn: str, params: TSNode | None, generics: set[str]
) -> list[LocalType]:
    """`a, b *Repo` binds both names to `Repo`; `xs ...Repo` is a slice, so untyped."""
    out: list[LocalType] = []
    for p in _named(params):
        names = [ctx.text(n) for n in p.children_by_field_name("name")]
        if p.type == "parameter_declaration":
            ty = _type_of(ctx, p.child_by_field_name("type"), generics)
        elif p.type == "variadic_parameter_declaration":
            ty = ""
        else:
            continue
        out += [LocalType(fn, n, ty) for n in names if n != "_"]
    return out


def signature_locals(ctx: Ctx, node: TSNode, fn: str) -> list[LocalType]:
    """Parameters and named results of a function, method or literal. The
    receiver is not one: it is the method's own receiver, handled as SELF."""
    generics = _generic_names(ctx, node)
    return _parameter_list(
        ctx, fn, node.child_by_field_name("parameters"), generics
    ) + _parameter_list(ctx, fn, node.child_by_field_name("result"), generics)


def _end(node: TSNode) -> Point:
    """Where a node's last named token ends. A case clause ends after the
    newline that closes its last statement, on the next clause's line."""
    while node.named_children:
        node = node.named_children[-1]
    return node.end_point


def _clause(ctx: Ctx, fn: str, name: str, type_name: str, clause: TSNode) -> list[LocalType]:
    return ctx.scoped(LocalType(fn, name, type_name), clause.start_point, _end(clause))


def literal_locals(ctx: Ctx, node: TSNode, fn: str) -> list[LocalType]:
    """A func literal's parameters and results, in scope in the literal."""
    return ctx.over(signature_locals(ctx, node, fn), node)


_SCOPES = frozenset(
    {
        "block",
        "expression_case",
        "type_case",
        "default_case",
        "communication_case",
        "if_statement",
        "for_statement",
        "expression_switch_statement",
        "type_switch_statement",
        "func_literal",
        "source_file",
    }
)


def _after(
    ctx: Ctx, node: TSNode, found: list[LocalType], binder: TSNode | None = None
) -> list[LocalType]:
    """In scope from the end of the declaration (`x := x.Next()` reads the outer
    `x`) to the end of the block, case clause or statement that holds it."""
    scope = node.parent
    while scope is not None and scope.type not in _SCOPES:
        scope = scope.parent
    return ctx.rescoped(found, node.end_point, _end(scope or node), binder)


def spec_locals(ctx: Ctx, node: TSNode, fn: str) -> list[LocalType]:
    """`var x T` and `const c T` are typed; `var x = ..` and `const c = ..` are not."""
    ty = _type_of(ctx, node.child_by_field_name("type"), _generic_names(ctx, node))
    out: list[LocalType] = []
    for n in node.children_by_field_name("name"):
        if (t := ctx.text(n)) != "_":
            out += _after(ctx, node, [LocalType(fn, t, ty)], n)
    return out


def _declared_here(ctx: Ctx, node: TSNode, fn: str, name: str) -> bool:
    """Whether `name` is already declared in the scope that holds this `:=`. Then
    the `:=` only assigns it and its declared type stays. A parameter counts for
    the function's own top block."""
    scope = node.parent
    while scope is not None and scope.type not in _SCOPES:
        scope = scope.parent
    if scope is None:
        return False
    end = _end(scope)[0] + 1
    line = node.start_point[0] + 1
    owner = scope.parent if scope.type == "block" else None
    top = owner is not None and owner.type in (
        "function_declaration",
        "method_declaration",
        "func_literal",
    )
    for loc in ctx.locals:
        if loc.function != fn or loc.name != name or loc.start_line > line:
            continue
        # A parameter spans its whole function; a declaration, the rest of its block.
        if loc.end_line == end or (
            top and owner is not None and loc.end_line > owner.end_point[0]
        ):
            return True
    return False


def short_var_locals(ctx: Ctx, node: TSNode, fn: str) -> list[LocalType]:
    """`x := ..` binds untyped, except `y := x.(Repo)` and `y, ok := x.(Repo)`:
    the assertion names the type outright, and it is the static type of `y`
    whether or not the assertion held, so `y` is a `Repo`."""
    names = _identifiers(node.child_by_field_name("left"))
    out = _untyped(ctx, fn, names)
    right = _named(node.child_by_field_name("right"))
    if len(right) == 1 and right[0].type == "type_assertion_expression" and names:
        first = ctx.text(names[0])
        ty = _type_of(ctx, right[0].child_by_field_name("type"), _generic_names(ctx, node))
        if first != "_" and ty and not _declared_here(ctx, node, fn, first):
            typed = _after(ctx, node, [LocalType(fn, first, ty)], names[0])
            return typed + _after(ctx, node, out[1:])
    return _after(ctx, node, out)


def _is_define(node: TSNode) -> bool:
    return any(c.type == ":=" for c in node.children)


def range_locals(ctx: Ctx, node: TSNode, fn: str) -> list[LocalType]:
    """`for k, v := range xs` binds in the loop body; `for k = range xs` only
    assigns."""
    if not _is_define(node):
        return []
    loop = node.parent
    body = loop.child_by_field_name("body") if loop is not None else None
    found = _untyped(ctx, fn, _identifiers(node.child_by_field_name("left")))
    return ctx.over(found, body or node)


def type_switch_locals(ctx: Ctx, node: TSNode, fn: str) -> list[LocalType]:
    """`switch v := x.(type)` gives `v` the clause's type, in that clause only.
    A clause with one plain type records it; a clause with several types,
    `nil` or a default clause records untyped (there `v` is the switched
    value)."""
    alias = _identifiers(node.child_by_field_name("alias"))
    if not alias:
        return []
    name = ctx.text(alias[0])
    if name == "_":
        return []
    generics = _generic_names(ctx, node)
    out: list[LocalType] = []
    for clause in node.named_children:
        if clause.type == "type_case":
            types = [t for t in clause.children_by_field_name("type") if t.is_named]
            ty = _type_of(ctx, types[0], generics) if len(types) == 1 else ""
            out += _clause(ctx, fn, name, ty, clause)
        elif clause.type == "default_case":
            out += _clause(ctx, fn, name, "", clause)
    return out


def receive_locals(ctx: Ctx, node: TSNode, fn: str) -> list[LocalType]:
    """`case v, ok := <-ch` in a select, bound in its case."""
    if not _is_define(node):
        return []
    found = _untyped(ctx, fn, _identifiers(node.child_by_field_name("left")))
    case = node.parent or node
    return ctx.rescoped(found, case.start_point, _end(case))


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


def risk_callee(ctx: Ctx, node: TSNode) -> str | None:
    fn = node.child_by_field_name("function")
    if fn is None or fn.end_byte - fn.start_byte > 200:
        return None
    return ctx.text(fn)


def _p(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern, re.DOTALL)


_KEYED = frozenset({"keyed_element"})
_CALL = frozenset({"call_expression"})

# Go has no route extraction yet, so no auth markers.
# fmt: off
RISKS = RiskSpec(
    call_types=frozenset({"call_expression"}),
    callee=risk_callee,
    string_types=frozenset({"interpreted_string_literal", "raw_string_literal"}),
    interpolations=(),
    concat=(("binary_expression", frozenset({"+"})),),
    format_callee=re.compile(r"^fmt\.Sprintf$"),
    assigns=(
        ("short_var_declaration", "left", "right"),
        ("assignment_statement", "left", "right"),
        ("var_spec", "name", "value"),
    ),
    unwrap=frozenset({"parenthesized_expression", "expression_list"}),
    sinks=(
        Sink("sql", re.compile(r"\.(?:Query|QueryRow|QueryContext|QueryRowContext|Exec|ExecContext|Raw|Prepare|PrepareContext)$")),
        Sink("shell", re.compile(r"^exec\.Command(?:Context)?$"), re.compile(r"\"(?:/bin/)?(?:sh|bash|zsh)\"\s*,\s*\"-c\"")),
    ),
    unsafe=(
        Unsafe("tls-verify-off", "InsecureSkipVerify: true", _KEYED, _p(r"InsecureSkipVerify\s*:\s*true")),
        Unsafe("weak-hash", "MD5 or SHA-1", _CALL, _p(r"(?:md5|sha1)\.(?:New|Sum)\(.*")),
        Unsafe("debug-enabled", "gin debug mode", _CALL, _p(r"gin\.SetMode\(\s*gin\.DebugMode\s*\)")),
        Unsafe("cors-any-origin", "CORS allows every origin", _KEYED, _p(r"AllowAllOrigins\s*:\s*true|Allow(?:ed)?Origins\s*:\s*\[\]string\{\s*\"\*\"\s*\}")),
        Unsafe("no-timeout", "http default client has no timeout", _CALL, _p(r"http\.(?:Get|Post|Head|PostForm)\(.*|http\.DefaultClient\.\w+\(.*")),
        Unsafe("no-timeout", "http.Client without Timeout", frozenset({"composite_literal"}), _p(r"http\.Client\{(?!.*\bTimeout\s*:).*\}")),
        Unsafe("no-retry", "Redis retries turned off", _KEYED, _p(r"MaxRetries\s*:\s*-1")),
    ),
    loops=(("for_statement", "body"),),
    datastore_calls=re.compile(
        r"(?:^|\.)(?:\w*(?:db|DB|Db)|tx|\w*Tx|conn|\w*Conn|pool|\w*Pool|stmt|\w*Stmt)\.(?:Query|QueryContext|QueryRow|QueryRowContext|Exec|ExecContext)$"
        r"|(?:^|\.)(?:\w*(?:db|DB|Db)|tx|\w*Tx|\w*[Rr]epo|\w*[Rr]epository)\.(?:Get|Select|First|Find|Take|Create|Save|Delete|Updates?|Where)$"
    ),
    catch_types=frozenset({"if_statement"}),
    catch_body="consequence",
    catch_guard=re.compile(r"\w*[eE]rr\w*\s*!=\s*nil"),
    quiet=frozenset({"comment"}),
)
# fmt: on


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
    risks=RISKS,
    rules={
        "import_spec": Import(hook=import_),
        "type_spec": Custom(hook=type_spec),
        "function_declaration": Define(
            kind="function",
            records_class=False,
            keeps_decorators=False,
            exported_by=lambda _ctx, _node, name: _exported(name),
            locals=signature_locals,
        ),
        # A package-level literal too: its calls have no enclosing function.
        "func_literal": Local(hook=literal_locals, top_level=True),
        "var_spec": Local(hook=spec_locals),
        "const_spec": Local(hook=spec_locals),
        "short_var_declaration": Local(hook=short_var_locals),
        "range_clause": Local(hook=range_locals),
        "type_switch_statement": Local(hook=type_switch_locals),
        "receive_statement": Local(hook=receive_locals),
        "method_declaration": Custom(hook=method_declaration),
        "call_expression": Call(hook=call),
    },
    qualified_prefix=lambda path: path.rsplit(".", 1)[0].replace("/", "."),
    modules=GoModules,
)
