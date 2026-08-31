"""Pass 2: cross-file resolution.

Always runs over the whole symbol table, never incrementally. Changing file A
invalidates edges attributed to file B, so per-file caching is sound for pass 1
only. That is the single combination under which `--update` stays byte-identical
to a full build.

Three rules govern what gets emitted:

1. **Nothing without evidence.** An unresolvable target produces no edge, and
   increments the ``UNRESOLVED`` bin.
2. **Candidates carry two locations.** When a name resolves to N definitions,
   each edge shows the reference site *and* the candidate definition site, so a
   reader can check the claim. One location would make the fail-closed promise
   hollow.
3. **External is a claim, not a fallback.** A target only counts as external if
   it matches the standard library or a declared dependency. Everything else we
   fail to resolve is ``UNRESOLVED``, which is the number the report surfaces.
"""

from __future__ import annotations

import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from svarupa.diagnostics import Diagnostic
from svarupa.extract.base import (
    CallShape,
    ExtractResult,
    FileFacts,
    Scorecard,
    node_id,
    sorted_edges,
    sorted_nodes,
)
from svarupa.model import (
    Confidence,
    Edge,
    EdgeKind,
    Evidence,
    Node,
    NodeKind,
    Resolution,
)

__all__ = ["Resolver", "resolve"]

_STDLIB = frozenset(sys.stdlib_module_names)

_KIND_MAP = {
    "function": NodeKind.FUNCTION,
    "class": NodeKind.CLASS,
    "method": NodeKind.METHOD,
    "module": NodeKind.MODULE,
    "interface": NodeKind.INTERFACE,
}

# Above this many same-named definitions, a candidate edge stops being
# informative. `.get()` or `.run()` matching 40 methods tells a reader nothing
# and would swamp the diagram, so it is honestly counted as unresolved instead.
MAX_CANDIDATE_ARITY = 8


# pyright --strict cannot infer through a bare `dict`/`set` default_factory,
# so each gets a typed nullary factory. Verbose, but it keeps the index fully
# typed rather than degrading to Unknown at the first lookup.
def _d_facts() -> dict[str, FileFacts]:
    return {}


def _d_loc() -> dict[str, tuple[str, str]]:
    return {}


def _d_locs() -> dict[str, list[tuple[str, str]]]:
    return {}


def _d_bases() -> dict[str, tuple[str, ...]]:
    return {}


def _s_str() -> set[str]:
    return set()


@dataclass
class _Index:
    by_file: dict[str, FileFacts] = field(default_factory=_d_facts)
    by_qualname: dict[str, tuple[str, str]] = field(default_factory=_d_loc)
    by_name: dict[str, list[tuple[str, str]]] = field(default_factory=_d_locs)
    methods_by_name: dict[str, list[tuple[str, str]]] = field(default_factory=_d_locs)
    classes: dict[str, tuple[str, str]] = field(default_factory=_d_loc)
    bases_of: dict[str, tuple[str, ...]] = field(default_factory=_d_bases)
    files: set[str] = field(default_factory=_s_str)


class Resolver:
    def __init__(
        self,
        facts: Sequence[FileFacts],
        declared_deps: frozenset[str] = frozenset(),
    ) -> None:
        self.facts = list(facts)
        self.deps = declared_deps
        self.scorecard = Scorecard()
        self.diagnostics: list[Diagnostic] = []
        self.idx = self._build_index()

    # ------------------------------------------------------------------

    def _build_index(self) -> _Index:
        idx = _Index()
        for f in self.facts:
            idx.by_file[f.path] = f
            idx.files.add(f.path)
            for s in f.symbols:
                key = (f.path, s.qualified_name)
                idx.by_qualname.setdefault(s.qualified_name, key)
                idx.by_name.setdefault(s.name, []).append(key)
                if s.kind == "method":
                    idx.methods_by_name.setdefault(s.name, []).append(key)
                if s.kind == "class":
                    idx.classes.setdefault(s.name, key)
                    idx.bases_of[s.name] = s.bases
        for lst in idx.by_name.values():
            lst.sort()
        for lst in idx.methods_by_name.values():
            lst.sort()
        return idx

    # ------------------------------------------------------------------

    def _is_external(self, top: str) -> bool:
        return top in _STDLIB or top in self.deps

    def _mro(self, cls: str, seen: frozenset[str] = frozenset()) -> list[str]:
        if cls in seen:
            return []
        out = [cls]
        for base in self.idx.bases_of.get(cls, ()):
            out.extend(self._mro(base, seen | {cls}))
        return out

    def _resolve_module(self, spec: str, from_file: str, level: int) -> str | None:
        """Map an import specifier to a file in the repo.

        Tries `x.py`, then `x/__init__.py`, then walks up. A specifier that
        names nothing in the repo is not an error here; the caller decides
        whether it is external or unresolved.
        """
        if level > 0:
            parts = from_file.split("/")[:-1]
            up = level - 1
            anchor = parts[: len(parts) - up] if up else parts
            rest = spec.lstrip(".").replace(".", "/")
            base = "/".join([*anchor, rest]) if rest else "/".join(anchor)
            return self._hit(base)

        dotted = spec.replace(".", "/")
        candidates = [dotted]
        for root in ("src", "lib", "app"):
            candidates.append(f"{root}/{dotted}")
        for cand in candidates:
            cur = cand
            while cur:
                if (hit := self._hit(cur)) is not None:
                    return hit
                cur = "/".join(cur.split("/")[:-1])
        return None

    def _hit(self, base: str) -> str | None:
        for suffix in (".py", "/__init__.py", ".pyi"):
            if (c := base + suffix) in self.idx.files:
                return c
        return base if base in self.idx.files else None

    def _follow_reexport(self, file: str, name: str, depth: int = 0) -> tuple[str, str] | None:
        """Chase `__init__.py` re-export chains to the real definition.

        `from flask import Flask` resolves at file level to the package
        `__init__.py`, but the definition lives in `app.py`. Without this, the
        dependency target is the package facade for most of a well-packaged
        library's public API, and `impact_of_change` is wrong for exactly the
        symbols people ask about.
        """
        if depth > 4:
            return None
        facts = self.idx.by_file.get(file)
        if facts is None:
            return None
        for s in facts.symbols:
            if s.name == name:
                return (file, s.qualified_name)
        for imp in facts.imports:
            if name not in imp.names:
                continue
            target = self._resolve_module(imp.specifier, file, imp.level)
            if (
                target
                and target != file
                and (found := self._follow_reexport(target, name, depth + 1)) is not None
            ):
                return found
        return None

    # ------------------------------------------------------------------

    def run(self) -> ExtractResult:
        nodes: list[Node] = []
        edges: list[Edge] = []

        for f in self.facts:
            self.diagnostics.extend(f.diagnostics)
            nodes.extend(self._nodes_for(f))
            edges.extend(self._import_edges(f))
            edges.extend(self._call_edges(f))
            edges.extend(self._inherit_edges(f))

        return ExtractResult(
            nodes=sorted_nodes(nodes),
            edges=sorted_edges(edges),
            scorecard=self.scorecard,
            diagnostics=tuple(self.diagnostics),
        )

    def _nodes_for(self, f: FileFacts) -> list[Node]:
        out: list[Node] = []
        for s in f.symbols:
            attrs: list[tuple[str, str]] = [("exported", "true" if s.exported else "false")]
            if s.enclosing_class:
                attrs.append(("class", s.enclosing_class))
            out.append(
                Node(
                    id=node_id(f.path, s.qualified_name),
                    kind=_KIND_MAP.get(s.kind, NodeKind.FUNCTION),
                    label=s.name,
                    qualified_name=s.qualified_name,
                    evidence=(s.evidence,),
                    lang=f.lang,
                    attrs=tuple(attrs),
                    producer=f"{f.lang}.symbols",
                )
            )
        return out

    def _import_edges(self, f: FileFacts) -> list[Edge]:
        out: list[Edge] = []
        for imp in f.imports:
            top = imp.specifier.lstrip(".").split(".")[0]
            target = self._resolve_module(imp.specifier, f.path, imp.level)

            if target is None:
                if not imp.is_relative and self._is_external(top):
                    self.scorecard.record(f.lang, EdgeKind.IMPORTS, Resolution.EXTERNAL)
                else:
                    self.scorecard.record(
                        f.lang, EdgeKind.IMPORTS, Resolution.UNRESOLVED, imp.specifier
                    )
                continue

            if target == f.path:
                continue

            self.scorecard.record(f.lang, EdgeKind.IMPORTS, Resolution.RESOLVED)
            out.append(
                Edge(
                    src=f.path,
                    dst=target,
                    kind=EdgeKind.IMPORTS,
                    evidence=(imp.evidence,),
                    confidence=Confidence.RESOLVED,
                    resolution=Resolution.RESOLVED,
                    attrs=(("type_only", "true"),) if imp.type_only else (),
                    producer=f"{f.lang}.imports",
                )
            )

            # Symbol-level references, chasing re-export chains.
            for name in imp.names:
                found = self._follow_reexport(target, name)
                if found is None:
                    self.scorecard.record(
                        f.lang, "references", Resolution.UNRESOLVED, f"{imp.specifier}.{name}"
                    )
                    continue
                self.scorecard.record(f.lang, "references", Resolution.RESOLVED)
                dst_file, dst_qual = found
                out.append(
                    Edge(
                        src=f.path,
                        dst=node_id(dst_file, dst_qual),
                        kind=EdgeKind.REFERENCES,
                        evidence=(imp.evidence,),
                        confidence=Confidence.RESOLVED,
                        resolution=Resolution.RESOLVED,
                        producer=f"{f.lang}.imports",
                    )
                )
        return out

    def _call_edges(self, f: FileFacts) -> list[Edge]:
        out: list[Edge] = []
        import_names: dict[str, str] = {}
        for imp in f.imports:
            for alias, real in imp.alias_of.items():
                import_names[alias] = imp.specifier or real
            for n in imp.names:
                import_names[n] = imp.specifier or n
            if not imp.names and imp.specifier:
                import_names[imp.specifier.split(".")[0]] = imp.specifier

        for call in f.calls:
            src_id = node_id(f.path, call.enclosing) if call.enclosing else f.path
            targets = self._call_targets(f, call, import_names)

            if targets is None:  # external
                self.scorecard.record(f.lang, EdgeKind.CALLS, Resolution.EXTERNAL)
                continue
            if not targets:
                self.scorecard.record(
                    f.lang,
                    EdgeKind.CALLS,
                    Resolution.UNRESOLVED,
                    f"{call.shape.value}:{call.receiver or ''}.{call.name}",
                )
                continue

            if len(targets) == 1:
                dst_file, dst_qual = targets[0]
                self.scorecard.record(f.lang, EdgeKind.CALLS, Resolution.RESOLVED)
                out.append(
                    Edge(
                        src=src_id,
                        dst=node_id(dst_file, dst_qual),
                        kind=EdgeKind.CALLS,
                        evidence=(call.evidence,),
                        confidence=Confidence.RESOLVED,
                        resolution=Resolution.RESOLVED,
                        attrs=(("shape", call.shape.value),),
                        producer=f"{f.lang}.calls",
                    )
                )
                continue

            # Candidate: one of N. Each edge shows the call site AND the
            # candidate definition, so the claim stays checkable.
            self.scorecard.record(f.lang, EdgeKind.CALLS, Resolution.CANDIDATE)
            for dst_file, dst_qual in targets:
                def_ev = self._definition_evidence(dst_file, dst_qual)
                if def_ev is None:
                    continue
                out.append(
                    Edge(
                        src=src_id,
                        dst=node_id(dst_file, dst_qual),
                        kind=EdgeKind.CALLS,
                        evidence=(call.evidence, def_ev),
                        confidence=Confidence.RESOLVED,
                        resolution=Resolution.CANDIDATE,
                        arity=len(targets),
                        attrs=(("shape", call.shape.value),),
                        producer=f"{f.lang}.calls",
                    )
                )
        return out

    def _definition_evidence(self, file: str, qualname: str) -> Evidence | None:
        facts = self.idx.by_file.get(file)
        if facts is None:
            return None
        for s in facts.symbols:
            if s.qualified_name == qualname:
                return s.evidence
        return None

    def _call_targets(
        self, f: FileFacts, call: object, import_names: Mapping[str, str]
    ) -> list[tuple[str, str]] | None:
        """Return targets, [] for unresolved, or None for known-external."""
        from svarupa.extract.base import CallSite

        assert isinstance(call, CallSite)
        name = call.name

        if call.shape in (CallShape.SELF, CallShape.SUPER):
            if not call.enclosing_class:
                return []
            chain = self._mro(call.enclosing_class)
            if call.shape is CallShape.SUPER:
                chain = chain[1:]
            hits = [
                (path, qual)
                for cls in chain
                for path, qual in self.idx.methods_by_name.get(name, [])
                if qual.rsplit(".", 2)[-2:-1] == [cls]
            ]
            return hits[:MAX_CANDIDATE_ARITY] if len(hits) <= MAX_CANDIDATE_ARITY else []

        if call.shape is CallShape.BARE:
            local = [(p, q) for p, q in self.idx.by_name.get(name, []) if p == f.path]
            if local:
                return local[:1]
            if name in import_names:
                spec = import_names[name]
                top = spec.lstrip(".").split(".")[0]
                target = self._resolve_module(
                    spec, f.path, spec.count(".") if spec.startswith(".") else 0
                )
                if target is None:
                    return None if self._is_external(top) else []
                found = self._follow_reexport(target, name)
                return [found] if found else []
            hits = self.idx.by_name.get(name, [])
            if len(hits) == 1:
                return list(hits)
            return (
                list(hits[:MAX_CANDIDATE_ARITY]) if 1 < len(hits) <= MAX_CANDIDATE_ARITY else []
            )

        if call.shape is CallShape.QUALIFIED:
            recv = call.receiver or ""
            if recv in import_names:
                spec = import_names[recv]
                top = spec.lstrip(".").split(".")[0]
                target = self._resolve_module(
                    spec, f.path, spec.count(".") if spec.startswith(".") else 0
                )
                if target is None:
                    return None if self._is_external(top) else []
                found = self._follow_reexport(target, name)
                return [found] if found else []
            if recv in self.idx.classes:
                chain = self._mro(recv)
                hits = [
                    (p, q)
                    for cls in chain
                    for p, q in self.idx.methods_by_name.get(name, [])
                    if q.rsplit(".", 2)[-2:-1] == [cls]
                ]
                if hits:
                    return hits[:MAX_CANDIDATE_ARITY]
            # Receiver is a local variable of unknown type. This is the
            # dominant shape in service code and resolves at ~5%; treating it
            # as anything but unresolved would be invention.
            return []

        # MEMBER: chained receiver, type unknowable without inference.
        return []

    def _inherit_edges(self, f: FileFacts) -> list[Edge]:
        out: list[Edge] = []
        for s in f.symbols:
            if s.kind != "class":
                continue
            for base in s.bases:
                hit = self.idx.classes.get(base)
                if hit is None:
                    self.scorecard.record(f.lang, EdgeKind.INHERITS, Resolution.EXTERNAL, base)
                    continue
                self.scorecard.record(f.lang, EdgeKind.INHERITS, Resolution.RESOLVED)
                out.append(
                    Edge(
                        src=node_id(f.path, s.qualified_name),
                        dst=node_id(hit[0], hit[1]),
                        kind=EdgeKind.INHERITS,
                        evidence=(s.evidence,),
                        confidence=Confidence.RESOLVED,
                        resolution=Resolution.RESOLVED,
                        producer=f"{f.lang}.inherits",
                    )
                )
        return out


def resolve(
    facts: Sequence[FileFacts], declared_deps: frozenset[str] = frozenset()
) -> ExtractResult:
    return Resolver(facts, declared_deps).run()
