"""TypeScript extraction, pass 1.

Same contract as Python: read one file, emit facts, never look outside.

TypeScript resolves substantially better than Python (Spike 0c measured imports
at 63-96% against 37-47%, and service calls at ~49% against ~20%), for two
reasons this extractor leans on:

* module specifiers are explicit, so there is no package-search guesswork;
* constructor injection is **typed**, so ``this.userService.findOne()`` names
  an intra-repo class. That shape pinned at 99-100% on DI-heavy codebases,
  where Python's equivalent (``Depends()``) named framework classes and pinned
  at nearly nothing. Recording constructor parameter types is therefore cheap
  and unusually high-yield.
"""

from __future__ import annotations

import tree_sitter_typescript as tst
from tree_sitter import Language, Parser
from tree_sitter import Node as TSNode

from svarupa.diagnostics import Diagnostic, Severity
from svarupa.extract.base import (
    MAX_AST_DEPTH,
    CallShape,
    CallSite,
    DecoratorRef,
    Extractor,
    FieldType,
    FileFacts,
    ImportRef,
    SymbolRef,
    depth_capped,
)

_TS = Language(tst.language_typescript())
_TSX = Language(tst.language_tsx())

# Globals that are not intra-repo calls. `require` is included because a
# CommonJS call is a module reference, handled as an import, not a function.
_GLOBAL_CALLS = frozenset(
    {
        "require",
        "parseInt",
        "parseFloat",
        "isNaN",
        "isFinite",
        "String",
        "Number",
        "Boolean",
        "Array",
        "Object",
        "Symbol",
        "BigInt",
        "Promise",
        "setTimeout",
        "setInterval",
        "clearTimeout",
        "clearInterval",
        "fetch",
        "encodeURIComponent",
        "decodeURIComponent",
        "structuredClone",
        "queueMicrotask",
        "atob",
        "btoa",
        "alert",
        "confirm",
        "prompt",
    }
)
_GLOBAL_OBJECTS = frozenset(
    {
        "console",
        "JSON",
        "Math",
        "Object",
        "Array",
        "String",
        "Number",
        "Promise",
        "Date",
        "RegExp",
        "Map",
        "Set",
        "WeakMap",
        "WeakSet",
        "Symbol",
        "Reflect",
        "Proxy",
        "process",
        "Buffer",
        "globalThis",
        "window",
        "document",
        "localStorage",
        "sessionStorage",
        "navigator",
        "crypto",
        "performance",
        "URL",
        "Error",
        "Intl",
    }
)


def _text(src: bytes, node: TSNode) -> str:
    return src[node.start_byte : node.end_byte].decode("utf8", "replace")


def _ts_string(src: bytes, node: TSNode) -> str | None:
    """A static string value from a `string` or substitution-free
    `template_string` node; None for anything dynamic.

    Escape sequences are kept in their source spelling rather than dropped:
    joining only the fragments turned `'/a\\'b'` into `/ab`, a corrupted
    value in a committed file, and let two distinct routes collide onto one
    lock key.
    """
    if node.type == "template_string" and any(
        c.type == "template_substitution" for c in node.children
    ):
        return None
    if node.type in ("string", "template_string"):
        return "".join(
            _text(src, c)
            for c in node.children
            if c.type in ("string_fragment", "escape_sequence")
        )
    return None


def _bare_type(annotation: str) -> str:
    """`Promise<UserService | null>` -> `UserService`.

    Deliberately crude. A wrong type name yields a failed lookup, which is an
    honest unresolved; it cannot manufacture an edge, because the name still
    has to match a class that actually exists.
    """
    t = annotation.lstrip(":").strip()
    for cut in ("|", "&", "<", "["):
        t = t.split(cut)[0]
    return t.strip().strip("()").split(".")[-1].strip()


class TypeScriptExtractor(Extractor):
    lang = "typescript"
    grammar_version = "0.23.2"

    def parse(self, path: str, data: bytes) -> FileFacts:
        parser = Parser(_TSX if path.endswith(".tsx") else _TS)
        tree = parser.parse(data)

        symbols: list[SymbolRef] = []
        imports: list[ImportRef] = []
        calls: list[CallSite] = []
        fields: list[FieldType] = []
        ctor_assigns: list[tuple[str, str]] = []
        reexports: list[str] = []
        diags: list[Diagnostic] = []

        if tree.root_node.has_error:
            diags.append(
                Diagnostic(
                    code="SVA-X-001",
                    severity=Severity.WARNING,
                    message=(
                        "file has syntax errors; extraction continues over the "
                        "parseable regions and may be incomplete"
                    ),
                    subject=path,
                )
            )

        prefix = path.rsplit(".", 1)[0].replace("/", ".")

        def qual(stack: list[str]) -> str:
            return ".".join([prefix, *stack]) if stack else prefix

        too_deep = False

        def visit(
            node: TSNode,
            stack: list[str],
            cls: str | None,
            fn: str | None,
            exported: bool = False,
            depth: int = 0,
            decorators: tuple[DecoratorRef, ...] = (),
        ) -> None:
            nonlocal too_deep
            if depth > MAX_AST_DEPTH:
                # Stop, do not raise. The shallow part of the file has already
                # contributed real facts and losing them, plus every other
                # file in the run, is a far worse outcome than losing the
                # inside of a minified bundle.
                too_deep = True
                return

            t = node.type

            if t == "import_statement" and (ref := self._import(path, data, node)) is not None:
                imports.append(ref)
                return

            if t == "export_statement":
                # `export { x } from './y'` and `export * from './y'` are the
                # barrel pattern: the TS analogue of a package __init__, and
                # the reason a naive resolver reports the barrel as the
                # dependency target instead of the definition.
                src_node = node.child_by_field_name("source")
                if src_node is not None:
                    spec = _text(data, src_node).strip("'\"`")
                    export_aliases: dict[str, str] = {}
                    names = self._named_exports(data, node, export_aliases)
                    imports.append(
                        ImportRef(
                            specifier=spec,
                            names=names,
                            alias_of=export_aliases,
                            evidence=self.evidence(
                                path, node.start_point[0], node.end_point[0]
                            ),
                            is_relative=spec.startswith("."),
                            level=0,
                            is_from=True,
                            is_reexport=True,
                            is_star=not names,
                        )
                    )
                    reexports.extend(names)
                    return
                # A bare `export` wrapper: everything inside it is exported.
                # Decorators are siblings preceding the declaration they
                # decorate (`@Controller('users')` before `export class ...`
                # parses that way), so they are paired here rather than lost
                # to the generic recursion.
                pending: list[DecoratorRef] = []
                for child in node.children:
                    if child.type == "decorator":
                        if (d := self._decorator(path, data, child)) is not None:
                            pending.append(d)
                        continue
                    if child.type in ("export", "default", ";"):
                        # Keyword tokens sit between the decorators and the
                        # declaration; they must not consume the pending list.
                        continue
                    visit(
                        child,
                        stack,
                        cls,
                        fn,
                        exported=True,
                        depth=depth + 1,
                        decorators=tuple(pending),
                    )
                    pending = []
                return

            if t in ("class_declaration", "abstract_class_declaration"):
                name_node = node.child_by_field_name("name")
                if name_node is None:
                    return
                # A non-exported decorated class carries its decorators as its
                # own children rather than as export-statement siblings.
                own_decs: list[DecoratorRef] = []
                for child in node.children:
                    if child.type == "decorator" and (
                        (d := self._decorator(path, data, child)) is not None
                    ):
                        own_decs.append(d)
                decorators = decorators + tuple(own_decs)
                name = _text(data, name_node)
                bases: list[str] = []
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
                                        bases.append(_bare_type(_text(data, ref)))
                symbols.append(
                    SymbolRef(
                        name=name,
                        qualified_name=qual([*stack, name]),
                        kind="class",
                        evidence=self.evidence(path, node.start_point[0], node.end_point[0]),
                        enclosing_class=cls,
                        bases=tuple(b for b in bases if b),
                        exported=exported,
                        decorators=decorators,
                    )
                )
                body = node.child_by_field_name("body")
                if body is not None:
                    # Member decorators are siblings preceding the member.
                    member_decs: list[DecoratorRef] = []
                    for child in body.children:
                        if child.type == "decorator":
                            if (d := self._decorator(path, data, child)) is not None:
                                member_decs.append(d)
                            continue
                        visit(
                            child,
                            [*stack, name],
                            name,
                            fn,
                            depth=depth + 1,
                            decorators=tuple(member_decs),
                        )
                        member_decs = []
                return

            if t == "interface_declaration":
                name_node = node.child_by_field_name("name")
                if name_node is not None:
                    name = _text(data, name_node)
                    symbols.append(
                        SymbolRef(
                            name=name,
                            qualified_name=qual([*stack, name]),
                            kind="interface",
                            evidence=self.evidence(
                                path, node.start_point[0], node.end_point[0]
                            ),
                            exported=exported,
                        )
                    )
                return

            if t == "method_definition":
                name_node = node.child_by_field_name("name")
                if name_node is None:
                    return
                name = _text(data, name_node)
                q = qual([*stack, name])
                symbols.append(
                    SymbolRef(
                        name=name,
                        qualified_name=q,
                        kind="method",
                        evidence=self.evidence(path, node.start_point[0], node.end_point[0]),
                        enclosing_class=cls,
                        exported=not name.startswith("_"),
                        decorators=decorators,
                    )
                )
                if name == "constructor" and cls:
                    params = node.child_by_field_name("parameters")
                    if params is not None:
                        fields.extend(self._injected_fields(data, cls, params))
                body = node.child_by_field_name("body")
                if body is not None:
                    for child in body.children:
                        visit(child, [*stack, name], cls, q, depth=depth + 1)
                return

            if t in ("public_field_definition", "property_signature") and cls:
                fields.extend(self._declared_field(data, cls, node))

            if t == "function_declaration":
                name_node = node.child_by_field_name("name")
                if name_node is not None:
                    name = _text(data, name_node)
                    q = qual([*stack, name])
                    symbols.append(
                        SymbolRef(
                            name=name,
                            qualified_name=q,
                            kind="function",
                            evidence=self.evidence(
                                path, node.start_point[0], node.end_point[0]
                            ),
                            exported=exported,
                        )
                    )
                    body = node.child_by_field_name("body")
                    if body is not None:
                        for child in body.children:
                            visit(child, [*stack, name], cls, q, depth=depth + 1)
                    return

            if t in ("lexical_declaration", "variable_declaration"):
                # Not handled directly, but `exported` must survive the hop:
                # `export const f = () => {}` reached its declarator through
                # the generic recursion, which dropped the flag.
                for child in node.children:
                    visit(child, stack, cls, fn, exported=exported, depth=depth + 1)
                return

            if t == "variable_declarator":
                name_node = node.child_by_field_name("name")
                value = node.child_by_field_name("value")
                if (
                    name_node is not None
                    and value is not None
                    and value.type == "call_expression"
                ):
                    callee = value.child_by_field_name("function")
                    # `const x = require('spec')` is the CommonJS import, the
                    # dominant dialect of real Express codebases; without it
                    # the report claimed express coverage the extractor did
                    # not have. `const { A, B } = require('spec')` maps the
                    # destructured names like named imports.
                    if callee is not None and _text(data, callee) == "require":
                        spec = None
                        req_args = value.child_by_field_name("arguments")
                        if req_args is not None:
                            for c in req_args.children:
                                if c.type in ("(", ")", ","):
                                    continue
                                if c.type in ("string", "template_string"):
                                    spec = _ts_string(data, c)
                                break
                        req_names: list[str] = []
                        if name_node.type == "identifier":
                            req_names = [_text(data, name_node)]
                        elif name_node.type == "object_pattern":
                            req_names = [
                                _text(data, c)
                                for c in name_node.children
                                if c.type == "shorthand_property_identifier_pattern"
                            ]
                        if spec and req_names:
                            imports.append(
                                ImportRef(
                                    specifier=spec,
                                    names=tuple(req_names),
                                    alias_of={},
                                    evidence=self.evidence(
                                        path, node.start_point[0], node.start_point[0]
                                    ),
                                    is_relative=spec.startswith("."),
                                    is_from=True,
                                )
                            )
                    if callee is not None and name_node.type == "identifier":
                        ctor_assigns.append((_text(data, name_node), _text(data, callee)))
                if (
                    name_node is not None
                    and value is not None
                    and value.type in ("arrow_function", "function_expression")
                ):
                    name = _text(data, name_node)
                    q = qual([*stack, name])
                    symbols.append(
                        SymbolRef(
                            name=name,
                            qualified_name=q,
                            kind="function",
                            evidence=self.evidence(
                                path, node.start_point[0], node.end_point[0]
                            ),
                            exported=exported,
                        )
                    )
                    for child in value.children:
                        visit(child, [*stack, name], cls, q, depth=depth + 1)
                    return

            if (
                t == "call_expression"
                and (site := self._call(path, data, node, fn, cls)) is not None
            ):
                calls.append(site)

            for child in node.children:
                visit(child, stack, cls, fn, depth=depth + 1)

        visit(tree.root_node, [], None, None)
        if too_deep:
            diags.append(depth_capped(path))

        return FileFacts(
            path=path,
            lang="typescript",
            symbols=tuple(symbols),
            imports=tuple(imports),
            calls=tuple(calls),
            fields=tuple(fields),
            reexports=tuple(sorted(set(reexports))),
            diagnostics=tuple(diags),
            ctor_assigns=tuple(ctor_assigns),
        )

    # ------------------------------------------------------------------

    def _named_exports(
        self, src: bytes, node: TSNode, aliases: dict[str, str] | None = None
    ) -> tuple[str, ...]:
        aliases = aliases if aliases is not None else {}
        out: list[str] = []
        for child in node.children:
            if child.type == "export_clause":
                for spec in child.children:
                    if spec.type == "export_specifier":
                        alias = spec.child_by_field_name("alias")
                        name = spec.child_by_field_name("name")
                        target = alias or name
                        if target is not None:
                            out.append(_text(src, target))
                        if alias is not None and name is not None:
                            aliases[_text(src, alias)] = _text(src, name)
        return tuple(out)

    def _import(self, path: str, src: bytes, node: TSNode) -> ImportRef | None:
        source = node.child_by_field_name("source")
        if source is None:
            return None
        spec = _text(src, source).strip("'\"`")
        names: list[str] = []
        alias: dict[str, str] = {}
        # From the tree, never a substring: `import typeA from './x'` matched
        # "import type" and stamped a false type-only attribute on a runtime
        # import. `import typeDefs from ...` is a common GraphQL idiom.
        type_only = any(
            c.type == "type" or (c.type == "identifier" and False) for c in node.children
        ) or any(
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
                    names.append(_text(src, part))
                elif part.type == "named_imports":
                    for spec_node in part.children:
                        if spec_node.type != "import_specifier":
                            continue
                        real = spec_node.child_by_field_name("name")
                        as_name = spec_node.child_by_field_name("alias")
                        if real is None:
                            continue
                        if as_name is not None:
                            alias[_text(src, as_name)] = _text(src, real)
                            names.append(_text(src, as_name))
                        else:
                            names.append(_text(src, real))
                elif part.type == "namespace_import":
                    for ident in part.children:
                        if ident.type == "identifier":
                            names.append(_text(src, ident))

        return ImportRef(
            specifier=spec,
            names=tuple(names),
            alias_of=alias,
            evidence=self.evidence(path, node.start_point[0], node.end_point[0]),
            is_relative=spec.startswith("."),
            level=0,
            type_only=type_only,
        )

    def _injected_fields(self, src: bytes, cls: str, params: TSNode) -> list[FieldType]:
        """`constructor(private readonly svc: UserService)` declares a field.

        Only *parameter properties* count. A plain `constructor(config: T)`
        declares no field at all, and recording it as one produced a wrong
        edge: a class with a declared `config: DeclaredType` field resolved
        `this.config.run()` to the constructor parameter's type instead.

        This is still the highest-yield fact in TypeScript extraction, pinning
        at 99-100% on DI-heavy code, which is exactly why it has to be right.
        """
        out: list[FieldType] = []
        for param in params.children:
            if param.type not in ("required_parameter", "optional_parameter"):
                continue
            text = _text(src, param)
            # The modifier is what turns a parameter into a field declaration.
            if not any(
                text.lstrip().startswith(mod)
                for mod in ("private", "public", "protected", "readonly")
            ):
                continue
            name: str | None = None
            ty: str | None = None
            for child in param.children:
                if child.type in ("identifier", "property_identifier") and name is None:
                    name = _text(src, child)
                elif child.type == "type_annotation":
                    ty = _bare_type(_text(src, child))
            if name and ty:
                out.append(FieldType(cls, name, ty))
        return out

    def _declared_field(self, src: bytes, cls: str, node: TSNode) -> list[FieldType]:
        name: str | None = None
        ty: str | None = None
        for child in node.children:
            if child.type in ("property_identifier", "identifier") and name is None:
                name = _text(src, child)
            elif child.type == "type_annotation":
                ty = _bare_type(_text(src, child))
        return [FieldType(cls, name, ty)] if name and ty else []

    def _decorator(self, path: str, src: bytes, node: TSNode) -> DecoratorRef | None:
        """`@Get(':id')` or `@Controller('users')`, with its own line."""
        expr = next((c for c in node.children if c.type not in ("@", "comment")), None)
        if expr is None:
            return None
        ev = self.evidence(path, node.start_point[0], node.end_point[0])
        if expr.type != "call_expression":
            name = _text(src, expr)
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
                # First argument only: a string in a later position is not
                # the path. A first argument that is anything but a static
                # string makes the decorator present-but-dynamic, which must
                # stay distinguishable from "no arguments": conflating them
                # minted `endpoint GET /users` for `@Get(PATH)` one commit
                # after the same three-state lesson was promoted for Flask.
                if child.type in ("string", "template_string"):
                    arg = _ts_string(src, child)
                    dynamic = arg is None
                else:
                    dynamic = True
                break
        return DecoratorRef(name=_text(src, callee), arg=arg, evidence=ev, arg_dynamic=dynamic)

    @staticmethod
    def _first_str_arg(src: bytes, node: TSNode) -> str | None:
        args = node.child_by_field_name("arguments")
        if args is None:
            return None
        for child in args.children:
            if child.type in ("(", ")", ","):
                continue
            # Only when the string is literally the FIRST argument: a string
            # later in the list (a log message, a header value) is not a path.
            if child.type in ("string", "template_string"):
                return _ts_string(src, child)
            return None
        return None

    @staticmethod
    def _first_arg_ident(src: bytes, node: TSNode) -> str | None:
        args = node.child_by_field_name("arguments")
        if args is None:
            return None
        for child in args.children:
            if child.type in ("(", ")", ",", "comment"):
                continue
            return _text(src, child) if child.type == "identifier" else None
        return None

    @staticmethod
    def _ident_args(src: bytes, node: TSNode) -> tuple[str, ...]:
        args = node.child_by_field_name("arguments")
        if args is None:
            return ()
        return tuple(_text(src, c) for c in args.children if c.type == "identifier")

    def _chain_base(self, src: bytes, obj: TSNode) -> tuple[str, str, str | None] | None:
        """Unwind `base.m1(...).m2(...)` to (base identifier, m1, m1's static
        first string arg), or None if the chain is not rooted at an identifier.

        `app.route('/x').get(h).post(h)`: the `.post` receiver unwinds to
        ("app", "route", "/x") whatever the intermediate hops are; the
        consumer decides that only a `route` root with a static path means
        anything. Depth-capped: a chain longer than any real fluent API is
        left as opaque text rather than walked forever.
        """
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
        return (_text(src, cur), _text(src, prop), self._first_str_arg(src, innermost))

    def _call(
        self, path: str, src: bytes, node: TSNode, fn: str | None, cls: str | None
    ) -> CallSite | None:
        func = node.child_by_field_name("function")
        if func is None:
            return None
        ev = self.evidence(path, node.start_point[0], node.start_point[0])
        first_arg = self._first_str_arg(src, node)
        idents = self._ident_args(src, node)
        first_ident = self._first_arg_ident(src, node)

        if func.type == "identifier":
            name = _text(src, func)
            return (
                None
                if name in _GLOBAL_CALLS
                else CallSite(name, CallShape.BARE, None, ev, fn, cls, first_arg)
            )

        if func.type == "member_expression":
            obj = func.child_by_field_name("object")
            prop = func.child_by_field_name("property")
            if prop is None or obj is None:
                return None
            name = _text(src, prop)
            otext = _text(src, obj)

            if obj.type == "this":
                return CallSite(name, CallShape.SELF, "this", ev, fn, cls, first_arg)
            if obj.type == "super":
                return CallSite(name, CallShape.SUPER, "super", ev, fn, cls, first_arg)
            if obj.type == "member_expression" and otext.startswith("this."):
                # `this.svc.method()` -- the field name is the receiver, and
                # the constructor's type annotation gives us its class.
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
                if otext in _GLOBAL_OBJECTS:
                    return None
                return CallSite(
                    name,
                    CallShape.QUALIFIED,
                    otext,
                    ev,
                    fn,
                    cls,
                    first_arg,
                    idents,
                    first_ident,
                )
            if obj.type == "call_expression" and (chain := self._chain_base(src, obj)):
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
