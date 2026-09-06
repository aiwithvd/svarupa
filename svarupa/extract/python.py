"""Python extraction, pass 1.

Collects definitions, raw imports, and raw call sites from one syntax tree.
Deliberately looks at nothing outside the file: that is what makes the result
cacheable by content hash, and what keeps cross-file resolution in one place
where it can run globally.

Every emitted fact carries the source line it came from. A construct we cannot
place produces nothing rather than a guess, and is counted in the scorecard so
the gap is visible rather than papered over.
"""

from __future__ import annotations

import tree_sitter_python as tsp
from tree_sitter import Language, Parser
from tree_sitter import Node as TSNode

from svarupa.diagnostics import Diagnostic, Severity
from svarupa.extract.base import (
    MAX_AST_DEPTH,
    CallShape,
    CallSite,
    DecoratorRef,
    Extractor,
    FileFacts,
    ImportRef,
    SymbolRef,
    depth_capped,
    qualified_prefix,
)

_LANG = Language(tsp.language())

# Builtins that would otherwise look like unresolved intra-repo calls. Kept
# small and explicit rather than pulling in the whole `builtins` namespace,
# because a project is free to define its own `list` or `filter` and we want
# the local definition to win.
_BUILTIN_CALLS = frozenset(
    {
        "print",
        "len",
        "range",
        "enumerate",
        "zip",
        "map",
        "filter",
        "sorted",
        "isinstance",
        "issubclass",
        "hasattr",
        "getattr",
        "setattr",
        "delattr",
        "int",
        "str",
        "float",
        "bool",
        "list",
        "dict",
        "set",
        "tuple",
        "bytes",
        "type",
        "super",
        "open",
        "iter",
        "next",
        "repr",
        "format",
        "abs",
        "min",
        "max",
        "sum",
        "any",
        "all",
        "round",
        "id",
        "hash",
        "vars",
        "dir",
        "callable",
        "reversed",
        "slice",
        "frozenset",
        "bytearray",
        "complex",
        "divmod",
        "pow",
        "ord",
        "chr",
        "hex",
        "oct",
        "bin",
        "input",
        "eval",
        "exec",
        "compile",
        "globals",
        "locals",
        "staticmethod",
        "classmethod",
        "property",
        "object",
        "Exception",
        "ValueError",
        "TypeError",
        "KeyError",
        "IndexError",
        "RuntimeError",
        "StopIteration",
        "NotImplementedError",
        "AttributeError",
        "OSError",
        "IOError",
        "ZeroDivisionError",
    }
)


def _text(src: bytes, node: TSNode) -> str:
    return src[node.start_byte : node.end_byte].decode("utf8", "replace")


def _string_literal(src: bytes, node: TSNode) -> str | None:
    """The content of a plain string literal, or None if it is not one.

    An f-string with interpolation is dynamic: returning its static parts
    would invent a route path that is not the real one.
    """
    if any(c.type == "interpolation" for c in node.children):
        return None
    parts = [c for c in node.children if c.type == "string_content"]
    if not parts:
        return ""  # an empty string literal has no content node
    return "".join(_text(src, c) for c in parts)


class PythonExtractor(Extractor):
    lang = "python"
    grammar_version = "0.25.0"

    def parse(self, path: str, data: bytes) -> FileFacts:
        parser = Parser(_LANG)
        tree = parser.parse(data)

        symbols: list[SymbolRef] = []
        imports: list[ImportRef] = []
        calls: list[CallSite] = []
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

        prefix = qualified_prefix(path, "python")
        is_package_init = path.endswith("/__init__.py") or path == "__init__.py"

        def qual(stack: list[str]) -> str:
            return ".".join([prefix, *stack]) if stack else prefix

        too_deep = False

        def visit(
            node: TSNode,
            stack: list[str],
            cls: str | None,
            fn: str | None,
            depth: int = 0,
            decorators: tuple[DecoratorRef, ...] = (),
        ) -> None:
            nonlocal too_deep
            if depth > MAX_AST_DEPTH:
                too_deep = True
                return

            t = node.type

            if t == "decorated_definition":
                decs = tuple(
                    d
                    for child in node.children
                    if child.type == "decorator"
                    if (d := self._decorator(path, data, child)) is not None
                )
                definition = node.child_by_field_name("definition")
                if definition is not None:
                    visit(definition, stack, cls, fn, depth=depth + 1, decorators=decs)
                return

            if t in ("import_statement", "import_from_statement"):
                ref = self._import(path, data, node)
                if ref is not None:
                    imports.append(ref)
                    # In a package __init__, `from .x import Y` is the
                    # re-export chain that makes `from pkg import Y` work.
                    # Without following it, resolution lands on the facade
                    # rather than the definition.
                    if is_package_init and ref.is_relative:
                        reexports.extend(ref.names)
                return

            if t == "class_definition":
                name_node = node.child_by_field_name("name")
                if name_node is None:
                    return
                name = _text(data, name_node)
                bases: list[str] = []
                supers = node.child_by_field_name("superclasses")
                if supers is not None:
                    for child in supers.children:
                        if child.type in ("identifier", "attribute"):
                            bases.append(_text(data, child).rsplit(".", 1)[-1])
                symbols.append(
                    SymbolRef(
                        name=name,
                        qualified_name=qual([*stack, name]),
                        kind="class",
                        evidence=self.evidence(path, node.start_point[0], node.end_point[0]),
                        enclosing_class=cls,
                        bases=tuple(bases),
                        exported=not name.startswith("_"),
                        decorators=decorators,
                    )
                )
                body = node.child_by_field_name("body")
                if body is not None:
                    for child in body.children:
                        visit(child, [*stack, name], name, fn, depth=depth + 1)
                return

            if t == "function_definition":
                name_node = node.child_by_field_name("name")
                if name_node is None:
                    return
                name = _text(data, name_node)
                q = qual([*stack, name])
                symbols.append(
                    SymbolRef(
                        name=name,
                        qualified_name=q,
                        kind="method" if cls else "function",
                        evidence=self.evidence(path, node.start_point[0], node.end_point[0]),
                        enclosing_class=cls,
                        exported=not name.startswith("_"),
                        decorators=decorators,
                    )
                )
                body = node.child_by_field_name("body")
                if body is not None:
                    for child in body.children:
                        visit(child, [*stack, name], cls, q, depth=depth + 1)
                return

            if t == "call":
                site = self._call(path, data, node, fn, cls)
                if site is not None:
                    calls.append(site)

            for child in node.children:
                visit(child, stack, cls, fn, depth=depth + 1)

        visit(tree.root_node, [], None, None)
        if too_deep:
            diags.append(depth_capped(path))

        return FileFacts(
            path=path,
            lang="python",
            symbols=tuple(symbols),
            imports=tuple(imports),
            calls=tuple(calls),
            reexports=tuple(sorted(set(reexports))),
            diagnostics=tuple(diags),
        )

    # ------------------------------------------------------------------
    # imports
    # ------------------------------------------------------------------

    def _decorator(self, path: str, src: bytes, node: TSNode) -> DecoratorRef | None:
        """One decorator, with the callee text, its first literal string
        argument, and any literal `methods=[...]` keyword.

        Anything dynamic (an f-string path, a computed methods list) is left
        out rather than guessed: a missing `arg` means "no literal path", not
        an empty path.
        """
        expr = next((c for c in node.children if c.type not in ("@", "comment")), None)
        if expr is None:
            return None
        ev = self.evidence(path, node.start_point[0], node.end_point[0])
        if expr.type != "call":
            name = _text(src, expr)
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
                    arg = _string_literal(src, child)
                if child.type == "keyword_argument":
                    key = child.child_by_field_name("name")
                    value = child.child_by_field_name("value")
                    if key is None or _text(src, key) != "methods" or value is None:
                        continue
                    methods = ()
                    if value.type in ("list", "tuple"):
                        items = [
                            _string_literal(src, c)
                            for c in value.children
                            if c.type == "string"
                        ]
                        entries = sum(
                            1 for c in value.children if c.type not in ("[", "]", "(", ")", ",")
                        )
                        if (
                            items
                            and len(items) == entries
                            and all(i is not None for i in items)
                        ):
                            methods = tuple(i for i in items if i is not None)
        return DecoratorRef(name=_text(src, callee), arg=arg, evidence=ev, methods=methods)

    def _import(self, path: str, src: bytes, node: TSNode) -> ImportRef | None:
        ev = self.evidence(path, node.start_point[0], node.end_point[0])

        if node.type == "import_statement":
            names: list[str] = []
            alias: dict[str, str] = {}
            for child in node.children:
                if child.type == "dotted_name":
                    names.append(_text(src, child))
                elif child.type == "aliased_import":
                    target = child.child_by_field_name("name")
                    as_name = child.child_by_field_name("alias")
                    if target is not None:
                        dotted = _text(src, target)
                        names.append(dotted)
                        if as_name is not None:
                            alias[_text(src, as_name)] = dotted
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
        spec = _text(src, module_node) if module_node is not None else ""
        level = len(spec) - len(spec.lstrip("."))
        names_out: list[str] = []
        alias_out: dict[str, str] = {}
        wildcard = False

        for child in node.children:
            # `is` is wrong here: py-tree-sitter hands back a fresh wrapper
            # object per `child_by_field_name` call, so identity never matches
            # and the module name was captured as if it were an imported
            # symbol. Every absolute `from x.y import z` then produced a
            # phantom reference that dominated the unresolved bin.
            if module_node is not None and child.id == module_node.id:
                continue
            if child.type == "wildcard_import":
                wildcard = True
            elif child.type == "dotted_name":
                names_out.append(_text(src, child))
            elif child.type == "aliased_import":
                target = child.child_by_field_name("name")
                as_name = child.child_by_field_name("alias")
                if target is not None:
                    real = _text(src, target)
                    names_out.append(real)
                    if as_name is not None:
                        alias_out[_text(src, as_name)] = real

        if wildcard and not names_out:
            # `from x import *` binds an unknowable set. Emitting a guess would
            # be exactly the invention the fail-closed rule forbids, so the
            # module edge is kept and the names are left empty.
            names_out = []

        return ImportRef(
            specifier=spec,
            names=tuple(names_out),
            alias_of=alias_out,
            evidence=ev,
            is_relative=level > 0,
            level=level,
        )

    # ------------------------------------------------------------------
    # calls
    # ------------------------------------------------------------------

    def _call(
        self,
        path: str,
        src: bytes,
        node: TSNode,
        fn: str | None,
        cls: str | None,
    ) -> CallSite | None:
        func = node.child_by_field_name("function")
        if func is None:
            return None
        ev = self.evidence(path, node.start_point[0], node.start_point[0])

        if func.type == "identifier":
            name = _text(src, func)
            if name in _BUILTIN_CALLS:
                return None
            return CallSite(name, CallShape.BARE, None, ev, fn, cls)

        if func.type == "attribute":
            obj = func.child_by_field_name("object")
            attr = func.child_by_field_name("attribute")
            if attr is None:
                return None
            name = _text(src, attr)
            if obj is None:
                return None
            recv = _text(src, obj)

            if recv == "self":
                return CallSite(name, CallShape.SELF, "self", ev, fn, cls)
            if obj.type == "call" and recv.startswith("super("):
                return CallSite(name, CallShape.SUPER, "super", ev, fn, cls)
            if obj.type == "identifier":
                # Ambiguous at this stage: `recv` may be an imported module or
                # a local variable. The resolver decides, because only it knows
                # the import table.
                return CallSite(name, CallShape.QUALIFIED, recv, ev, fn, cls)
            return CallSite(name, CallShape.MEMBER, recv, ev, fn, cls)

        return None
