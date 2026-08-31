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
    CallShape,
    CallSite,
    Extractor,
    FieldType,
    FileFacts,
    ImportRef,
    SymbolRef,
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

        def visit(node: TSNode, stack: list[str], cls: str | None, fn: str | None) -> None:
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
                    names = self._named_exports(data, node)
                    imports.append(
                        ImportRef(
                            specifier=spec,
                            names=names,
                            alias_of={},
                            evidence=self.evidence(
                                path, node.start_point[0], node.end_point[0]
                            ),
                            is_relative=spec.startswith("."),
                            level=0,
                            is_from=True,
                            is_reexport=True,
                        )
                    )
                    reexports.extend(names)
                    return
                for child in node.children:
                    visit(child, stack, cls, fn)
                return

            if t in ("class_declaration", "abstract_class_declaration"):
                name_node = node.child_by_field_name("name")
                if name_node is None:
                    return
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
                    )
                )
                body = node.child_by_field_name("body")
                if body is not None:
                    for child in body.children:
                        visit(child, [*stack, name], name, fn)
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
                    )
                )
                if name == "constructor" and cls:
                    params = node.child_by_field_name("parameters")
                    if params is not None:
                        fields.extend(self._injected_fields(data, cls, params))
                body = node.child_by_field_name("body")
                if body is not None:
                    for child in body.children:
                        visit(child, [*stack, name], cls, q)
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
                        )
                    )
                    body = node.child_by_field_name("body")
                    if body is not None:
                        for child in body.children:
                            visit(child, [*stack, name], cls, q)
                    return

            if t == "variable_declarator":
                name_node = node.child_by_field_name("name")
                value = node.child_by_field_name("value")
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
                        )
                    )
                    for child in value.children:
                        visit(child, [*stack, name], cls, q)
                    return

            if (
                t == "call_expression"
                and (site := self._call(path, data, node, fn, cls)) is not None
            ):
                calls.append(site)

            for child in node.children:
                visit(child, stack, cls, fn)

        visit(tree.root_node, [], None, None)

        return FileFacts(
            path=path,
            lang="typescript",
            symbols=tuple(symbols),
            imports=tuple(imports),
            calls=tuple(calls),
            fields=tuple(fields),
            reexports=tuple(sorted(set(reexports))),
            diagnostics=tuple(diags),
        )

    # ------------------------------------------------------------------

    def _named_exports(self, src: bytes, node: TSNode) -> tuple[str, ...]:
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
        return tuple(out)

    def _import(self, path: str, src: bytes, node: TSNode) -> ImportRef | None:
        source = node.child_by_field_name("source")
        if source is None:
            return None
        spec = _text(src, source).strip("'\"`")
        names: list[str] = []
        alias: dict[str, str] = {}
        type_only = "import type" in _text(src, node)[:20]

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

        This is the highest-yield fact in TypeScript extraction: it is what
        makes `this.svc.method()` resolvable, and it pinned at 99-100% on
        DI-heavy code.
        """
        out: list[FieldType] = []
        for param in params.children:
            if param.type not in ("required_parameter", "optional_parameter"):
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

    def _call(
        self, path: str, src: bytes, node: TSNode, fn: str | None, cls: str | None
    ) -> CallSite | None:
        func = node.child_by_field_name("function")
        if func is None:
            return None
        ev = self.evidence(path, node.start_point[0], node.start_point[0])

        if func.type == "identifier":
            name = _text(src, func)
            return (
                None
                if name in _GLOBAL_CALLS
                else CallSite(name, CallShape.BARE, None, ev, fn, cls)
            )

        if func.type == "member_expression":
            obj = func.child_by_field_name("object")
            prop = func.child_by_field_name("property")
            if prop is None or obj is None:
                return None
            name = _text(src, prop)
            otext = _text(src, obj)

            if obj.type == "this":
                return CallSite(name, CallShape.SELF, "this", ev, fn, cls)
            if obj.type == "super":
                return CallSite(name, CallShape.SUPER, "super", ev, fn, cls)
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
                )
            if obj.type == "identifier":
                if otext in _GLOBAL_OBJECTS:
                    return None
                return CallSite(name, CallShape.QUALIFIED, otext, ev, fn, cls)
            return CallSite(name, CallShape.MEMBER, otext, ev, fn, cls)

        return None
