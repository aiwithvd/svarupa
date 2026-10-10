"""The one place a syntax tree becomes facts.

Every pack runs through here, so the rules that make facts trustworthy are
written once: evidence comes from the node's own rows, a tree deeper than
MAX_AST_DEPTH degrades with SVA-X-003 instead of crashing, a file with
syntax errors says so with SVA-X-001, and facts come out in document
order. Several resolver steps are first-seen-wins, so that order is part of
the contract.

The generic path costs one interpreter frame per tree level (children are
walked in `visit`'s own loop), so the depth cap trips long before Python's
recursion limit does.
"""

from __future__ import annotations

import importlib
from collections.abc import Callable
from dataclasses import dataclass, replace
from functools import cache
from typing import cast

from tree_sitter import Language, Parser, Tree
from tree_sitter import Node as TSNode

from svarupa.diagnostics import Diagnostic, Severity
from svarupa.extract.base import (
    MAX_AST_DEPTH,
    CallSite,
    DecoratorRef,
    Extractor,
    FieldType,
    FileFacts,
    FunctionMetrics,
    ImportRef,
    SymbolRef,
    depth_capped,
)
from svarupa.extract.packs.model import (
    Call,
    Carry,
    Custom,
    Decorated,
    Define,
    Field,
    Grammar,
    Import,
    MetricsSpec,
    Pack,
)
from svarupa.model import Evidence

__all__ = ["Ctx", "Frame", "PackExtractor", "language_for"]


@dataclass(frozen=True, slots=True)
class Frame:
    """Where the walk is: scope names, enclosing class and function, and the
    two things a parent hands to exactly one child (exported, decorators)."""

    stack: tuple[str, ...] = ()
    cls: str | None = None
    fn: str | None = None
    exported: bool = False
    decorators: tuple[DecoratorRef, ...] = ()

    def plain(self) -> Frame:
        """The frame a generic child gets: same scope, nothing handed down."""
        return Frame(self.stack, self.cls, self.fn)


class Ctx:
    """One file's walk: the source, the pack, and the facts so far."""

    def __init__(self, pack: Pack, path: str, data: bytes) -> None:
        self.pack = pack
        self.path = path
        self.data = data
        self.prefix = pack.qualified_prefix(path)
        self.symbols: list[SymbolRef] = []
        self.imports: list[ImportRef] = []
        self.calls: list[CallSite] = []
        self.fields: list[FieldType] = []
        self.ctor_assigns: list[tuple[str, str]] = []
        self.reexports: list[str] = []
        self.namespace = ""
        self.too_deep = False

    def text(self, node: TSNode) -> str:
        return self.data[node.start_byte : node.end_byte].decode("utf8", "replace")

    def evidence(self, start_row: int, end_row: int) -> Evidence:
        return Extractor.evidence(self.path, start_row, end_row)

    def node_evidence(self, node: TSNode) -> Evidence:
        return self.evidence(node.start_point[0], node.end_point[0])

    def qual(self, names: tuple[str, ...]) -> str:
        return ".".join((self.prefix, *names)) if names else self.prefix

    def decorator(self, node: TSNode) -> DecoratorRef | None:
        hook = self.pack.decorator
        return hook(self, node) if hook is not None else None

    def visit(self, node: TSNode, frame: Frame, depth: int) -> None:
        if depth > MAX_AST_DEPTH:
            self.too_deep = True
            return
        rule = self.pack.rules.get(node.type)
        if isinstance(rule, Define):
            self._define(node, rule, frame, depth)
            return
        if isinstance(rule, Decorated):
            decs = tuple(
                d
                for child in node.children
                if child.type == rule.decorator_type
                if (d := self.decorator(child)) is not None
            )
            definition = node.child_by_field_name(rule.definition_field)
            if definition is not None:
                self.visit(definition, replace(frame, decorators=decs), depth + 1)
            return
        if isinstance(rule, Custom):
            rule.hook(self, node, frame, depth)
            return
        if isinstance(rule, Carry):
            carried = Frame(frame.stack, frame.cls, frame.fn, exported=frame.exported)
            for child in node.children:
                self.visit(child, carried, depth + 1)
            return
        if isinstance(rule, Import):
            ref = rule.hook(self, node)
            if ref is not None:
                self.imports.append(ref)
                return
            if not rule.recurse_on_none:
                return
        elif isinstance(rule, Call):
            site = rule.hook(self, node, frame)
            if site is not None:
                self.calls.append(site)
        elif isinstance(rule, Field) and frame.cls:
            self.fields.extend(rule.hook(self, node, frame.cls))
        plain = frame.plain()
        for child in node.children:
            self.visit(child, plain, depth + 1)

    def _define(self, node: TSNode, rule: Define, frame: Frame, depth: int) -> None:
        name_node = node.child_by_field_name(rule.name_field)
        if name_node is None:
            if rule.recurse_without_name:
                plain = frame.plain()
                for child in node.children:
                    self.visit(child, plain, depth + 1)
            return
        decorators = frame.decorators if rule.keeps_decorators else ()
        if rule.own_decorators is not None:
            decorators += tuple(
                d
                for child in node.children
                if child.type == rule.own_decorators
                if (d := self.decorator(child)) is not None
            )
        if rule.decorators_from is not None:
            decorators += rule.decorators_from(self, node)
        name = self.text(name_node)
        qualified = self.qual((*frame.stack, name))
        self.symbols.append(
            SymbolRef(
                name=name,
                qualified_name=qualified,
                kind=rule.kind_in_class if rule.kind_in_class and frame.cls else rule.kind,
                evidence=self.node_evidence(node),
                enclosing_class=frame.cls if rule.records_class else None,
                bases=rule.bases(self, node) if rule.bases is not None else (),
                exported=(
                    rule.exported_by(self, node, name)
                    if rule.exported_by is not None
                    else frame.exported
                    if rule.inherit_exported
                    else not name.startswith("_")
                ),
                decorators=decorators,
            )
        )
        if rule.after is not None:
            rule.after(self, node, frame, name)
        if rule.body_field is None:
            return
        body = node.child_by_field_name(rule.body_field)
        if body is None:
            return
        scope = (*frame.stack, name)
        inner = (
            Frame(scope, name, frame.fn)
            if rule.sets_class
            else Frame(scope, frame.cls, qualified)
        )
        if rule.member_decorators is None:
            for child in body.children:
                self.visit(child, inner, depth + 1)
            return
        # Member decorators are siblings that precede the member they decorate.
        pending: list[DecoratorRef] = []
        for child in body.children:
            if child.type == rule.member_decorators:
                if (d := self.decorator(child)) is not None:
                    pending.append(d)
                continue
            self.visit(child, replace(inner, decorators=tuple(pending)), depth + 1)
            pending = []

    def measure(self, root: TSNode) -> list[FunctionMetrics]:
        """Every function in document order. Iterative, so a deep file costs
        no recursion; skipped when the tree already blew the depth cap."""
        spec = self.pack.metrics
        if spec is None or self.too_deep:
            return []
        out: list[FunctionMetrics] = []
        stack = [root]
        while stack:
            node = stack.pop()
            # Named nodes only: Python's `lambda` keyword token shares the
            # node's type name and would be measured as a second function.
            if node.is_named and node.type in spec.function_types:
                out.append(self._measure(node, spec))
            stack.extend(reversed(node.children))
        return out

    def _measure(self, fn: TSNode, spec: MetricsSpec) -> FunctionMetrics:
        ops = dict(spec.boolean_ops)
        excluded = set(spec.not_branch)
        complexity, deepest = 1, 0
        work = [(c, 0, fn.type) for c in reversed(fn.children)]
        while work:
            node, depth, parent = work.pop()
            kind = node.type
            if kind in spec.function_types and node.is_named:
                continue  # measured on its own
            if kind in spec.branch_types:
                if not any((kind, c.type) in excluded for c in node.children):
                    complexity += 1
            elif kind in ops and any(c.type in ops[kind] for c in node.children):
                complexity += 1
            nests = kind in spec.nesting_types and not (
                kind == "if_statement" and parent in spec.else_if_parents
            )
            here = depth + 1 if nests else depth
            deepest = max(deepest, here)
            work.extend((c, here, kind) for c in reversed(node.children))
        start, end = fn.start_point[0], fn.end_point[0]
        return FunctionMetrics(
            name=self._function_name(fn),
            evidence=self.evidence(start, end),
            complexity=complexity,
            params=spec.count_params(fn),
            nesting=deepest,
            lines=end - start + 1,
        )

    def _function_name(self, fn: TSNode) -> str:
        name = fn.child_by_field_name("name")
        if name is None and fn.parent is not None and fn.parent.type == "variable_declarator":
            name = fn.parent.child_by_field_name("name")
        return self.text(name) if name is not None else "(anonymous)"

    def run(self, tree: Tree) -> FileFacts:
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
                    subject=self.path,
                )
            )
        self.visit(tree.root_node, Frame(), 0)
        functions = self.measure(tree.root_node)
        if self.too_deep:
            diags.append(depth_capped(self.path))
        return FileFacts(
            path=self.path,
            lang=self.pack.lang,
            symbols=tuple(self.symbols),
            imports=tuple(self.imports),
            calls=tuple(self.calls),
            fields=tuple(self.fields),
            reexports=tuple(sorted(set(self.reexports))),
            diagnostics=tuple(diags),
            ctor_assigns=tuple(self.ctor_assigns),
            namespace=self.namespace,
            functions=tuple(functions),
        )


@cache
def _load(module: str, function: str) -> Language:
    factory = cast("Callable[[], object]", getattr(importlib.import_module(module), function))
    return Language(factory())


def language_for(grammar: Grammar, path: str) -> Language:
    for suffix, function in grammar.by_suffix:
        if path.endswith(suffix):
            return _load(grammar.module, function)
    return _load(grammar.module, grammar.default)


class PackExtractor(Extractor):
    """The Extractor contract, served by a pack."""

    def __init__(self, pack: Pack) -> None:
        self.pack = pack
        self.lang = pack.lang
        self.grammar_version = pack.grammar.version

    def available(self) -> str | None:
        """None when every grammar function loads, else why it does not."""
        g = self.pack.grammar
        try:
            for function in (g.default, *(f for _, f in g.by_suffix)):
                _load(g.module, function)
        except (ImportError, AttributeError, ValueError, TypeError) as exc:
            return f"the {g.distribution} grammar could not be loaded ({type(exc).__name__})"
        return None

    def parse(self, path: str, data: bytes) -> FileFacts:
        parser = Parser(language_for(self.pack.grammar, path))
        return Ctx(self.pack, path, data).run(parser.parse(data))
