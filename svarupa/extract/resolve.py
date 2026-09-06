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

from svarupa.diagnostics import Diagnostic, Severity
from svarupa.extract.base import (
    CallShape,
    ExtractResult,
    FileFacts,
    ImportRef,
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
from svarupa.tsconfig import Alias

__all__ = ["Resolver", "resolve"]

_STDLIB = frozenset(sys.stdlib_module_names)
_SUFFIXES = (".py", "/__init__.py", ".pyi")

# Bundler-handled assets. Importing a PNG or a stylesheet is a real dependency
# but not a code one, so it belongs in the external bin rather than being
# reported as a resolution failure the user is expected to act on.
_ASSET_SUFFIXES = (
    ".css",
    ".scss",
    ".sass",
    ".less",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".svg",
    ".webp",
    ".avif",
    ".ico",
    ".woff",
    ".woff2",
    ".ttf",
    ".otf",
    ".mp4",
    ".webm",
    ".wasm",
    ".txt",
    ".md",
    ".html",
    ".graphql",
    ".gql",
    ".yaml",
    ".yml",
)
_NODE_BUILTINS = frozenset(
    {
        "assert",
        "async_hooks",
        "buffer",
        "child_process",
        "cluster",
        "console",
        "constants",
        "crypto",
        "dgram",
        "diagnostics_channel",
        "dns",
        "domain",
        "events",
        "fs",
        "http",
        "http2",
        "https",
        "inspector",
        "module",
        "net",
        "os",
        "path",
        "perf_hooks",
        "process",
        "punycode",
        "querystring",
        "readline",
        "repl",
        "stream",
        "string_decoder",
        "sys",
        "timers",
        "tls",
        "trace_events",
        "tty",
        "url",
        "util",
        "v8",
        "vm",
        "wasi",
        "worker_threads",
        "zlib",
    }
)

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

# Bases that are genuinely external without needing an import statement.
_BUILTIN_TYPES = frozenset(
    {
        "object",
        "Exception",
        "BaseException",
        "ValueError",
        "TypeError",
        "KeyError",
        "IndexError",
        "RuntimeError",
        "NotImplementedError",
        "AttributeError",
        "OSError",
        "IOError",
        "StopIteration",
        "dict",
        "list",
        "set",
        "tuple",
        "str",
        "int",
        "float",
        "bytes",
        "type",
        "Enum",
        "IntEnum",
        "StrEnum",
        "Protocol",
        "ABC",
        "NamedTuple",
        "TypedDict",
        "Generic",
    }
)


def _dedupe(items: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Order-preserving dedupe, applied before the resolved/candidate split.

    Diamond inheritance converges: `D(B, C)` where both reach `A.m` yields the
    same definition twice, which was then reported as CANDIDATE with arity 2.
    Arity is a claim shown to a reader ("one of N"); an inflated N is an
    invented fact carried in the evidence itself.
    """
    seen: set[tuple[str, str]] = set()
    out: list[tuple[str, str]] = []
    for x in items:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


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


def _d_any() -> dict[tuple[str, str], object]:
    return {}


def _d_meth() -> dict[tuple[str, str], list[tuple[str, str]]]:
    return {}


def _d_fields() -> dict[tuple[str, str, str], str]:
    return {}


@dataclass
class _Index:
    by_file: dict[str, FileFacts] = field(default_factory=_d_facts)
    by_qualname: dict[str, tuple[str, str]] = field(default_factory=_d_loc)
    by_name: dict[str, list[tuple[str, str]]] = field(default_factory=_d_locs)
    methods_by_name: dict[str, list[tuple[str, str]]] = field(default_factory=_d_locs)
    classes: dict[str, tuple[str, str]] = field(default_factory=_d_loc)
    class_by_id: dict[tuple[str, str], object] = field(default_factory=_d_any)
    methods_of: dict[tuple[str, str], list[tuple[str, str]]] = field(default_factory=_d_meth)
    bases_of: dict[str, tuple[str, ...]] = field(default_factory=_d_bases)
    field_types: dict[tuple[str, str, str], str] = field(default_factory=_d_fields)
    files: set[str] = field(default_factory=_s_str)


class Resolver:
    def __init__(
        self,
        facts: Sequence[FileFacts],
        declared_deps: frozenset[str] = frozenset(),
        source_roots: Sequence[str] = (),
        ts_aliases: Sequence[Alias] = (),
        ts_packages: Sequence[tuple[str, str]] = (),
    ) -> None:
        self.facts = list(facts)
        self.deps = declared_deps
        # Longest alias first, so `@/store/x` prefers `@/store` over `@`.
        self.ts_aliases = tuple(ts_aliases)
        self.ts_packages = tuple(sorted(ts_packages, key=lambda kv: -len(kv[0])))
        self.scorecard = Scorecard()
        self.diagnostics: list[Diagnostic] = []
        self.idx = self._build_index()
        self.roots = self._source_roots(source_roots)

    def _source_roots(self, declared: Sequence[str]) -> tuple[str, ...]:
        """Every directory an absolute import could be rooted at.

        Hardcoding `src`, `lib`, `app` silently failed on any other layout: a
        package at `backend/mylib/` resolved nothing at all. Instead, derive
        the candidates from the tree itself, since any ancestor directory of a
        file is a possible import root, and let the caller add roots that
        `detect` discovered from workspace manifests.

        Sorted longest-first so the most specific root wins, then by name so
        the order never depends on set iteration.
        """
        roots: set[str] = {""}
        roots.update(r.strip("/") for r in declared if r.strip("/"))

        # A root is the *parent of a top-level package*, not every ancestor
        # directory. Treating every ancestor as a root lets a specifier match
        # a same-named module anywhere in the tree, including examples/ and
        # docs/, which is over-eager in exactly the direction that invents
        # edges. The parent of any `__init__.py` package is precise and small.
        for path in self.idx.files:
            if path.endswith("/__init__.py"):
                pkg_dir = path[: -len("/__init__.py")]
                parent = "/".join(pkg_dir.split("/")[:-1])
                roots.add(parent)
            elif "/" not in path:
                roots.add("")

        # Longest first so the most specific root wins; then by name so the
        # order never depends on set iteration.
        return tuple(sorted(roots, key=lambda r: (-r.count("/"), -len(r), r)))

    # ------------------------------------------------------------------

    def _build_index(self) -> _Index:
        idx = _Index()
        for f in self.facts:
            idx.by_file[f.path] = f
            idx.files.add(f.path)
            for fld in f.fields:
                idx.field_types[(f.path, fld.owner, fld.field)] = fld.type_name
            for s in f.symbols:
                key = (f.path, s.qualified_name)
                idx.by_qualname.setdefault(s.qualified_name, key)
                idx.by_name.setdefault(s.name, []).append(key)
                if s.kind == "method":
                    idx.methods_by_name.setdefault(s.name, []).append(key)
                if s.kind == "class":
                    # Keyed by identity, not bare name. Keeping `classes`
                    # first-seen while `bases_of` kept last-seen silently fused
                    # one class's identity with another class's bases.
                    idx.classes.setdefault(s.name, key)
                    idx.class_by_id[key] = s
                    idx.bases_of.setdefault(s.name, s.bases)
                    idx.methods_of.setdefault(key, [])
                if s.kind == "interface":
                    idx.classes.setdefault(s.name, key)
                    idx.class_by_id[key] = s
                    idx.bases_of.setdefault(s.name, s.bases)
                    idx.methods_of.setdefault(key, [])
                if s.kind == "method" and s.enclosing_class:
                    owner = (f.path, s.qualified_name.rsplit(".", 1)[0])
                    idx.methods_of.setdefault(owner, []).append(key)
        for lst in idx.by_name.values():
            lst.sort()
        for lst in idx.methods_by_name.values():
            lst.sort()
        return idx

    # ------------------------------------------------------------------

    @staticmethod
    def _package_root(spec: str, lang: str) -> str:
        """The distribution name a specifier belongs to.

        Language-specific, and getting it wrong is silent: splitting a
        TypeScript specifier on "." (Python's rule) turns
        `typeorm/common/DeepPartial` into itself, which matches no declared
        dependency, so a perfectly ordinary framework import lands in the
        unresolved bin and the scorecard cries wolf.
        """
        if lang in ("typescript", "javascript"):
            bare = spec.removeprefix("node:")
            if bare.lower().endswith(_ASSET_SUFFIXES):
                return bare
            parts = bare.split("/")
            if bare.startswith("@") and len(parts) >= 2:
                return "/".join(parts[:2])
            return parts[0]
        return spec.lstrip(".").split(".")[0]

    def _is_external(self, top: str, lang: str = "python") -> bool:
        if lang in ("typescript", "javascript"):
            if top.startswith("node:") or top in _NODE_BUILTINS:
                return True
            if top.lower().endswith(_ASSET_SUFFIXES):
                return True
            return top in self.deps
        return top in _STDLIB or top in self.deps

    def _mro_ids(
        self, cls_id: tuple[str, str], seen: frozenset[tuple[str, str]] = frozenset()
    ) -> list[tuple[str, str]]:
        """Linearize by class identity, resolving base names in the defining file.

        Resolving bases by bare name across the whole repo made `self.helper()`
        bind to an unrelated same-named class in another file. A base name is
        looked up first among classes defined in the same file, then through
        that file's own import table, and only then repo-wide -- and a repo-wide
        hit that is ambiguous is not used at all.
        """
        if cls_id in seen:
            return []
        out = [cls_id]
        sym = self.idx.class_by_id.get(cls_id)
        bases = getattr(sym, "bases", ()) if sym is not None else ()
        for base in bases:
            resolved = self._resolve_class_name(base, cls_id[0])
            if resolved is not None:
                out.extend(self._mro_ids(resolved, seen | {cls_id}))
        return out

    def _type_is_external(self, f: FileFacts, name: str) -> bool:
        for imp in f.imports:
            if name not in imp.names and name not in imp.alias_of:
                continue
            if imp.is_relative:
                return False
            return self._is_external(self._package_root(imp.specifier, f.lang), f.lang)
        return False

    def _resolve_class_name_x(
        self, name: str, from_file: str
    ) -> tuple[tuple[str, str] | None, bool]:
        """Resolve, and say whether a None means absent or ambiguous."""
        repo_wide = [cid for cid in self.idx.class_by_id if cid[1].endswith(f".{name}")]
        hit = self._resolve_class_name(name, from_file)
        return hit, hit is None and len(repo_wide) > 1

    def _resolve_class_name(self, name: str, from_file: str) -> tuple[str, str] | None:
        same_file = [
            cid
            for cid in self.idx.class_by_id
            if cid[0] == from_file and cid[1].endswith(f".{name}")
        ]
        if len(same_file) == 1:
            return same_file[0]

        facts = self.idx.by_file.get(from_file)
        if facts is not None:
            for imp in facts.imports:
                if not imp.is_from or name not in imp.names:
                    continue
                target = self._resolve_module(imp.specifier, from_file, imp.level, facts.lang)
                if target is None:
                    continue
                hit = [
                    cid
                    for cid in self.idx.class_by_id
                    if cid[0] == target and cid[1].endswith(f".{name}")
                ]
                if len(hit) == 1:
                    return hit[0]

        repo_wide = [cid for cid in self.idx.class_by_id if cid[1].endswith(f".{name}")]
        return repo_wide[0] if len(repo_wide) == 1 else None

    def _resolve_module(
        self, spec: str, from_file: str, level: int, lang: str = "python"
    ) -> str | None:
        if lang == "typescript":
            return self._resolve_ts(spec, from_file)
        return self._resolve_python(spec, from_file, level)

    def _resolve_python(self, spec: str, from_file: str, level: int) -> str | None:
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

        # Strip only within the dotted specifier, never into the root prefix.
        # Falling back to the root directory itself made every import match:
        # `import typing` resolved to src/flask/__init__.py simply because that
        # package existed, which drove flask's external count to zero and made
        # the scorecard claim 100% import resolution while being entirely wrong.
        parts = [p for p in spec.split(".") if p]
        if not parts:
            return None
        # An ancestor of the importing file wins over any other root. Sorting
        # roots by name length let `examples/pkg/utils.py` beat
        # `src/pkg/utils.py` for an import made from inside `src/pkg`, purely
        # because "examples" is longer than "src".
        ancestors = [
            r for r in self.roots if r and (from_file == r or from_file.startswith(r + "/"))
        ]
        for root in [*ancestors, *self.roots]:
            for n in range(len(parts), 0, -1):
                cand = "/".join(parts[:n])
                full = f"{root}/{cand}" if root else cand
                if (hit := self._hit(full)) is not None:
                    return hit
        return None

    def _hit(self, base: str) -> str | None:
        for suffix in _SUFFIXES:
            if (c := base + suffix) in self.idx.files:
                return c
        return base if base in self.idx.files else None

    def _resolve_ts(self, spec: str, from_file: str) -> str | None:
        """TypeScript module resolution.

        Three traps, all measured in Spike 0c before any of this was written:

        1. **ESM writes `./foo.js` for a file that is `foo.ts` on disk.**
           Without remapping, zod scored 0.0% import resolution; with it,
           96.3%. Modern ESM TypeScript does this universally.
        2. **tsconfig `paths` aliases** live behind `references`/`extends` in
           the project-references layout, and the aliases are what make
           `@/store` resolvable at all.
        3. Relative specifiers must not fall back to a package search, or an
           unresolvable relative import silently matches something unrelated.
        """
        if spec.startswith("."):
            cur = from_file.split("/")[:-1]
            for part in spec.split("/"):
                if part in ("", "."):
                    continue
                if part == "..":
                    cur = cur[:-1]
                else:
                    cur.append(part)
            return self._hit_ts("/".join(cur))

        # A monorepo publishes `packages/zod` as the package `zod`, so
        # `zod/v4` is an intra-repo import that no tsconfig alias covers.
        for pkg_name, pkg_dir in self.ts_packages:
            if spec == pkg_name or spec.startswith(pkg_name + "/"):
                rest = spec[len(pkg_name) :].lstrip("/")
                cand = f"{pkg_dir}/{rest}" if rest else pkg_dir
                for probe in (cand, f"{pkg_dir}/src/{rest}" if rest else f"{pkg_dir}/src"):
                    if (hit := self._hit_ts(probe)) is not None:
                        return hit

        # Only aliases whose declaring config governs this file, most specific
        # scope first. A global namespace lets one app's `@/` hijack another's.
        applicable = [a for a in self.ts_aliases if a.applies_to(from_file)]
        applicable.sort(key=lambda a: (-len(a.scope), -len(a.prefix)))
        for alias in applicable:
            if spec == alias.prefix or spec.startswith(alias.prefix + "/"):
                rest = spec[len(alias.prefix) :].lstrip("/")
                cand = f"{alias.target}/{rest}" if rest else alias.target
                if (hit := self._hit_ts(cand.lstrip("/"))) is not None:
                    return hit
        return None

    def _hit_ts(self, base: str) -> str | None:
        # `./foo.js` usually names `foo.ts` on disk (the ESM convention), but in
        # a plain JavaScript project it names `foo.js`. Try the file as written
        # first, then the source-extension remap.
        if base in self.idx.files:
            return base
        for ext in (".js", ".mjs", ".cjs", ".jsx"):
            if base.endswith(ext):
                base = base[: -len(ext)]
                break
        for suffix in (
            ".ts",
            ".tsx",
            ".mts",
            ".cts",
            ".js",
            ".jsx",
            ".mjs",
            ".cjs",
            "/index.ts",
            "/index.tsx",
            "/index.js",
            "/index.jsx",
            "",
        ):
            if (c := base + suffix) in self.idx.files:
                return c
        return None

    @staticmethod
    def _real_name(imp: ImportRef, local: str) -> str:
        """`import { Real as Alias }` must be looked up as `Real`.

        The alias map was captured in pass 1 and never consulted, so every
        aliased import produced a false UNRESOLVED in two bins, using data the
        resolver already held.
        """
        return imp.alias_of.get(local, local)

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
            # F8: `export * from './x'` names nothing, so the only way to chase
            # it is to search the target's own symbols. The depth cap bounds it.
            if imp.is_star:
                target = self._resolve_module(imp.specifier, file, imp.level, facts.lang)
                if (
                    target
                    and target != file
                    and (found := self._follow_reexport(target, name, depth + 1)) is not None
                ):
                    return found
                continue
            if name not in imp.names:
                continue
            target = self._resolve_module(imp.specifier, file, imp.level, facts.lang)
            real = self._real_name(imp, name)
            if (
                target
                and target != file
                and (found := self._follow_reexport(target, real, depth + 1)) is not None
            ):
                return found
        return None

    # ------------------------------------------------------------------

    def run(self) -> ExtractResult:
        nodes: list[Node] = []
        edges: list[Edge] = []

        for f in self.facts:
            self.diagnostics.extend(f.diagnostics)
            nodes.append(self._module_node(f))
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

    def _module_node(self, f: FileFacts) -> Node:
        """One node per file.

        Import edges connect file to file, and module-level calls are attributed
        to the file. Without this node those edges referenced an id that existed
        nowhere in the graph, which would surface in `build` as a dangling
        endpoint rather than as the missing-node bug it actually is.

        Evidence is line 1: the file's own existence is the claim.
        """
        return Node(
            id=f.path,
            kind=NodeKind.MODULE,
            label=f.path.rsplit("/", 1)[-1],
            qualified_name=f.path,
            evidence=(Evidence(f.path, 1, 1),),
            lang=f.lang,
            producer=f"{f.lang}.module",
        )

    def _nodes_for(self, f: FileFacts) -> list[Node]:
        out: list[Node] = []
        # A `try/except ImportError` fallback defines the same name twice, which
        # emitted two Nodes claiming one id with different evidence. Node ids
        # must be unique -- build re-validates that independently -- and a
        # conditional definition is a fact worth surfacing rather than
        # silently collapsing.
        seen_ids: set[str] = set()
        for s in f.symbols:
            nid = node_id(f.path, s.qualified_name)
            if nid in seen_ids:
                self.diagnostics.append(
                    Diagnostic(
                        code="SVA-X-002",
                        severity=Severity.INFO,
                        message=(
                            "defined more than once in this file (a conditional "
                            "or fallback definition); keeping the first"
                        ),
                        subject=s.qualified_name,
                        location=str(s.evidence),
                    )
                )
                continue
            seen_ids.add(nid)
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
            top = self._package_root(imp.specifier, f.lang)
            target = self._resolve_module(imp.specifier, f.path, imp.level, f.lang)

            if target is None and f.lang == "python" and imp.is_from:
                # A PEP 420 namespace package has no `__init__.py`, so the
                # specifier itself names no file, but the imported names can
                # still be its submodules: `from agent.schema import schema`
                # in a repo where `agent/` and `agent/schema/` are plain
                # directories must resolve to `agent/schema/schema.py`.
                # Measured on a real two-service FastAPI repo: every absolute
                # self-package import was unresolved, and an added
                # cross-module import diffed the lockfile by zero lines.
                sib_edges: list[Edge] = []
                leftover: list[str] = []
                for name in imp.names:
                    lookup = self._real_name(imp, name)
                    sibling = self._resolve_module(
                        f"{imp.specifier}.{lookup}", f.path, imp.level, f.lang
                    )
                    if sibling is not None and sibling != f.path:
                        sib_edges.append(
                            Edge(
                                src=f.path,
                                dst=sibling,
                                kind=EdgeKind.IMPORTS,
                                evidence=(imp.evidence,),
                                confidence=Confidence.RESOLVED,
                                resolution=Resolution.RESOLVED,
                                producer=f"{f.lang}.imports",
                            )
                        )
                    else:
                        leftover.append(name)
                if sib_edges:
                    out.extend(sib_edges)
                    self.scorecard.record(f.lang, EdgeKind.IMPORTS, Resolution.RESOLVED)
                    for _ in sib_edges:
                        self.scorecard.record(f.lang, "references", Resolution.RESOLVED)
                    for name in leftover:
                        self.scorecard.record(
                            f.lang,
                            "references",
                            Resolution.UNRESOLVED,
                            f"{imp.specifier}.{name}",
                        )
                    continue

            if target is None:
                # An asset import is external whether or not it is relative:
                # `./logo.png` is a real dependency the bundler handles, not
                # something the user should be told we failed to resolve.
                is_asset = f.lang in (
                    "typescript",
                    "javascript",
                ) and imp.specifier.lower().endswith(_ASSET_SUFFIXES)
                external = is_asset or (not imp.is_relative and self._is_external(top, f.lang))
                self.scorecard.record(
                    f.lang,
                    EdgeKind.IMPORTS,
                    Resolution.EXTERNAL if external else Resolution.UNRESOLVED,
                    None if external else imp.specifier,
                )
                # Every reference must land in exactly one bin. These used to
                # fall through the `continue` and be counted nowhere, so the
                # references denominator silently excluded every symbol
                # imported from a framework.
                if imp.is_from:
                    for _ in imp.names:
                        self.scorecard.record(
                            f.lang,
                            "references",
                            Resolution.EXTERNAL if external else Resolution.UNRESOLVED,
                            None if external else f"{imp.specifier}",
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

            # `names` are module paths for a plain `import x.y`, not symbols.
            # Running the reference loop over them recorded a guaranteed
            # phantom miss for every intra-repo plain import.
            if not imp.is_from:
                continue

            # Symbol-level references, chasing re-export chains.
            for name in imp.names:
                lookup = self._real_name(imp, name)
                # `from . import sub` names a submodule. Treating it as a
                # symbol lookup inside __init__.py misses the real dependency.
                sibling = self._resolve_module(
                    f"{imp.specifier}.{name}" if imp.specifier.strip(".") else f".{name}",
                    f.path,
                    imp.level,
                )
                if sibling is not None and sibling != target and sibling != f.path:
                    self.scorecard.record(f.lang, "references", Resolution.RESOLVED)
                    out.append(
                        Edge(
                            src=f.path,
                            dst=sibling,
                            kind=EdgeKind.IMPORTS,
                            evidence=(imp.evidence,),
                            confidence=Confidence.RESOLVED,
                            resolution=Resolution.RESOLVED,
                            producer=f"{f.lang}.imports",
                        )
                    )
                    continue
                found = self._follow_reexport(target, lookup)
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
        # Carry the level pass 1 computed. Re-deriving it as `spec.count(".")`
        # is a second parser that disagrees: `from ..pkg.mod import x` has
        # level 2 (leading dots) but three dots total, so the recomputation
        # anchored at the wrong directory -- a wrong-edge generator, not just
        # recall loss.
        import_names: dict[str, tuple[str, int]] = {}
        alias_targets: dict[str, str] = {}
        for imp in f.imports:
            alias_targets.update(imp.alias_of)
            for alias, real in imp.alias_of.items():
                import_names[alias] = (imp.specifier or real, imp.level)
            for n in imp.names:
                if imp.is_from:
                    import_names[n] = (imp.specifier or n, imp.level)
            if not imp.is_from and imp.specifier:
                import_names[imp.specifier.split(".")[0]] = (imp.specifier, 0)

        for call in f.calls:
            src_id = node_id(f.path, call.enclosing) if call.enclosing else f.path
            targets = self._call_targets(f, call, import_names, alias_targets)

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
        self,
        f: FileFacts,
        call: object,
        import_names: Mapping[str, tuple[str, int]],
        alias_targets: Mapping[str, str] | None = None,
    ) -> list[tuple[str, str]] | None:
        """Return targets, [] for unresolved, or None for known-external."""
        from svarupa.extract.base import CallSite

        assert isinstance(call, CallSite)
        alias_targets = alias_targets or {}
        name = call.name

        if call.shape is CallShape.SELF_FIELD:
            # `this.svc.method()`. The constructor's type annotation names the
            # class, which is why this shape pins at 99-100% on DI-heavy
            # TypeScript while Python's nearest equivalent pins at nearly
            # nothing: the annotation there names a framework class.
            if not call.enclosing_class or not call.receiver:
                return []
            type_name = self.idx.field_types.get((f.path, call.enclosing_class, call.receiver))
            if type_name is None:
                return []
            owner, ambiguous = self._resolve_class_name_x(type_name, f.path)
            if owner is None:
                # `_resolve_class_name` returns None both for "not in the repo"
                # and for "in the repo twice". Collapsing those into EXTERNAL is
                # the unconditioned-external shape review #4 promoted to the
                # decision log; an ambiguous intra-repo type is unresolved.
                if ambiguous:
                    return []
                return None if self._type_is_external(f, type_name) else []
            return _dedupe(
                [
                    m
                    for cls_id in self._mro_ids(owner)
                    for m in self.idx.methods_of.get(cls_id, [])
                    if m[1].rsplit(".", 1)[-1] == name
                ]
            )[:MAX_CANDIDATE_ARITY]

        if call.shape in (CallShape.SELF, CallShape.SUPER):
            if not call.enclosing_class:
                return []
            own = self._resolve_class_name(call.enclosing_class, f.path)
            if own is None:
                return []
            chain = self._mro_ids(own)
            if call.shape is CallShape.SUPER:
                chain = chain[1:]
            hits = _dedupe(
                [
                    m
                    for cls_id in chain
                    for m in self.idx.methods_of.get(cls_id, [])
                    if m[1].rsplit(".", 1)[-1] == name
                ]
            )
            return hits[:MAX_CANDIDATE_ARITY] if len(hits) <= MAX_CANDIDATE_ARITY else []

        if call.shape is CallShape.BARE:
            # A bare name in Python can never dispatch to an instance method,
            # so a method hit is not merely arbitrary, it is impossible.
            # Lexicographic order was picking `A.f` over module-level `f`.
            methods = set(self.idx.methods_by_name.get(name, []))
            local = _dedupe(
                [
                    (p, q)
                    for p, q in self.idx.by_name.get(name, [])
                    if p == f.path and (p, q) not in methods
                ]
            )
            if len(local) == 1:
                return local
            if local:
                return local[:MAX_CANDIDATE_ARITY]
            if name in import_names:
                spec, level = import_names[name]
                top = self._package_root(spec, f.lang)
                target = self._resolve_module(spec, f.path, level, f.lang)
                if target is None:
                    return None if self._is_external(top, f.lang) else []
                found = self._follow_reexport(target, name)
                return [found] if found else []
            hits = _dedupe([h for h in self.idx.by_name.get(name, []) if h not in methods])
            if len(hits) == 1:
                return hits
            return hits[:MAX_CANDIDATE_ARITY] if 1 < len(hits) <= MAX_CANDIDATE_ARITY else []

        if call.shape is CallShape.QUALIFIED:
            recv = call.receiver or ""
            if recv in import_names:
                spec, level = import_names[recv]
                top = self._package_root(spec, f.lang)
                target = self._resolve_module(spec, f.path, level, f.lang)
                if target is None:
                    return None if self._is_external(top, f.lang) else []
                found = self._follow_reexport(target, name)
                return [found] if found else []
            owner = self._resolve_class_name(recv, f.path)
            if owner is not None:
                hits = _dedupe(
                    [
                        m
                        for cls_id in self._mro_ids(owner)
                        for m in self.idx.methods_of.get(cls_id, [])
                        if m[1].rsplit(".", 1)[-1] == name
                    ]
                )
                if hits:
                    return hits[:MAX_CANDIDATE_ARITY]
            # Receiver is a local variable of unknown type. This is the
            # dominant shape in service code and resolves at ~5%; treating it
            # as anything but unresolved would be invention.
            return []

        # MEMBER: chained receiver, type unknowable without inference.
        return []

    def _base_is_external(self, f: FileFacts, base: str) -> bool:
        """A base counts external only if we can point at why.

        Either it is a Python builtin exception/type, or the file imports it
        from something we classified external. Anything else we simply failed
        to resolve, and saying so is the whole point of the third bin.
        """
        if base in _BUILTIN_TYPES:
            return True
        for imp in f.imports:
            if base not in imp.names and base not in imp.alias_of:
                continue
            if imp.is_relative:
                return False
            top = self._package_root(imp.specifier, f.lang)
            return self._is_external(top, f.lang)
        return False

    def _inherit_edges(self, f: FileFacts) -> list[Edge]:
        out: list[Edge] = []
        for s in f.symbols:
            if s.kind != "class":
                continue
            for base in s.bases:
                hit = self._resolve_class_name(base, f.path)
                if hit is None:
                    # Recording EXTERNAL unconditionally re-collapses the
                    # scorecard to two bins: a resolver bug that lost every
                    # intra-repo base would have scored 100% external and 0%
                    # unresolved, which is exactly the laundering the third
                    # bin exists to prevent.
                    if self._base_is_external(f, base):
                        self.scorecard.record(f.lang, EdgeKind.INHERITS, Resolution.EXTERNAL)
                    else:
                        self.scorecard.record(
                            f.lang, EdgeKind.INHERITS, Resolution.UNRESOLVED, base
                        )
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
    facts: Sequence[FileFacts],
    declared_deps: frozenset[str] = frozenset(),
    source_roots: Sequence[str] = (),
    ts_aliases: Sequence[Alias] = (),
    ts_packages: Sequence[tuple[str, str]] = (),
) -> ExtractResult:
    return Resolver(facts, declared_deps, source_roots, ts_aliases, ts_packages).run()
