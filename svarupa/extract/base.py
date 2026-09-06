"""Extraction contract: what every language extractor must produce.

Two passes, and the split matters for correctness, not just speed.

**Pass 1 is per-file and cacheable.** It collects definitions, raw import
specifiers, and raw call sites from one file's syntax tree. Nothing here looks
outside the file, so a content hash is a sound cache key.

**Pass 2 is global and never incremental.** Cross-file resolution means
changing file A can invalidate edges attributed to file B, so resolution always
runs over the whole symbol table. Caching pass 1 while re-running pass 2 is the
only combination that keeps `--update` byte-identical to a full build.

The scorecard has **three bins, not two**. Counting every resolution failure as
"external" would launder resolver bugs into a number that looks like honesty,
and the scorecard is the headline honesty feature.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from svarupa.diagnostics import Diagnostic, Severity
from svarupa.model import Edge, EdgeKind, Evidence, Node, Resolution

__all__ = [
    "MAX_AST_DEPTH",
    "CallSite",
    "DecoratorRef",
    "EntrypointFact",
    "Extractor",
    "FieldType",
    "FileFacts",
    "ImportRef",
    "RouteFact",
    "Scorecard",
    "SymbolRef",
    "TaskFact",
    "depth_capped",
    "node_id",
]


def node_id(file: str, qualified_name: str) -> str:
    """Deterministic, readable node identity.

    Readable rather than hashed: a debugging session, a diagnostic, and a
    diff are all easier to follow when the id says what it is. `#` cannot
    appear in a Python or TypeScript qualified name, so the two halves stay
    unambiguous.
    """
    return f"{file}#{qualified_name}"


class CallShape(str, Enum):
    """How a call was written, which decides which resolver applies.

    Measured resolution rates differ enormously by shape (Spike 0b/0c), so the
    shape is recorded rather than inferred later:

    * ``BARE`` ``foo()`` — 73-94% pinned
    * ``SELF`` ``self.m()`` / ``this.m()`` — 90-100% via MRO
    * ``MEMBER`` ``x.m()`` where x is a local — **5-28%**, the dominant
      failure and the reason function-level sequence diagrams were cut
    * ``QUALIFIED`` ``mod.f()`` where mod is an imported module
    * ``SUPER`` ``super().m()``
    * ``SELF_FIELD`` ``this.svc.m()`` where ``svc`` is a typed constructor
      parameter — 99-100% on DI-heavy TypeScript, because the annotation names
      an intra-repo class. Python's nearest equivalent named framework classes
      and pinned at nearly nothing, which is why the shape is tracked
      separately rather than folded into MEMBER.
    """

    BARE = "bare"
    SELF = "self"
    SELF_FIELD = "self_field"
    MEMBER = "member"
    QUALIFIED = "qualified"
    SUPER = "super"


@dataclass(frozen=True, slots=True)
class SymbolRef:
    """A definition found in one file, before any cross-file work."""

    name: str
    qualified_name: str
    kind: str  # function | class | method | module
    evidence: Evidence
    enclosing_class: str | None = None
    bases: tuple[str, ...] = ()
    exported: bool = True
    decorators: tuple[DecoratorRef, ...] = ()


@dataclass(frozen=True, slots=True)
class DecoratorRef:
    """One decorator on a definition, with its own line.

    The decorator's line is not the definition's line: `@app.get("/items")`
    sits above `def read_items():`, and a route claim cites the decorator,
    because that is where the claim is made.
    """

    name: str  # dotted callee text without arguments, e.g. "app.get"
    arg: str | None  # FIRST argument when it is a literal string, source-spelled
    evidence: Evidence
    # True when the decorator call has arguments but the first is not a
    # literal string: `@Get(PATH)`, `@Controller(['a','b'])`. Distinguishable
    # from `@Get()` (arg is None, arg_dynamic False), whose meaning is "the
    # controller prefix itself". Present-but-unknown must be representable:
    # conflating the two minted `endpoint GET /users` for a route that lives
    # at `/users/:id`, one commit after the same lesson was promoted for
    # Flask's `methods`.
    arg_dynamic: bool = False
    # `methods=[...]` on a Flask `.route`. None means the kwarg is absent
    # (Flask's documented GET default applies); () means it is present but
    # not a literal collection of strings, so the methods are unknown and no
    # fact may be minted. Conflating those two invented `GET` for
    # `methods=("POST",)`.
    methods: tuple[str, ...] | None = None


@dataclass(frozen=True, slots=True)
class ImportRef:
    """A raw import, unresolved.

    ``names`` is kept because symbol-level resolution needs it: resolving only
    to the module means `from flask import Flask` points at the package facade
    rather than the definition, and `impact_of_change` would then be wrong for
    most of a well-packaged library's public API.
    """

    specifier: str
    names: tuple[str, ...]
    alias_of: Mapping[str, str]
    evidence: Evidence
    is_relative: bool = False
    level: int = 0
    type_only: bool = False
    # `names` means different things for the two statement kinds: symbols for
    # `from x import a, b`, module paths for `import x.y`. Running the
    # symbol-reference loop over the latter records a guaranteed phantom miss
    # for every plain intra-repo import, so the resolver has to know which
    # kind it is holding.
    is_from: bool = True
    # `export { x } from './y'` is a re-export, the TypeScript barrel pattern
    # and direct analogue of a package __init__.
    is_reexport: bool = False
    # `export * from './x'` names nothing explicitly; the resolver has to
    # search the target's own symbol table to chase it.
    is_star: bool = False


@dataclass(frozen=True, slots=True)
class FieldType:
    """A class field whose declared type names another class.

    The single highest-yield fact in TypeScript extraction: it is what makes
    `this.svc.method()` resolvable at all.
    """

    owner: str
    field: str
    type_name: str


@dataclass(frozen=True, slots=True)
class CallSite:
    """A call, unresolved."""

    name: str
    shape: CallShape
    receiver: str | None
    evidence: Evidence
    enclosing: str | None
    enclosing_class: str | None
    # The call's first argument when it is a static string literal, else None.
    # A generic pass-1 fact (no framework semantics here); it is what lets
    # semantics read `app.get('/items', h)` as a route without re-parsing.
    first_str_arg: str | None = None
    # Identifier arguments, in position order: `app.use('/api', router)`
    # carries ("router",). Generic; the consumer decides what mounting means.
    ident_args: tuple[str, ...] = ()
    # The first argument when it is a bare identifier, else None. Together
    # with first_str_arg this classifies a first argument as static string /
    # identifier / other, and "other" is what a consumer must treat as dynamic.
    first_arg_ident: str | None = None
    # When the receiver is itself a call chain rooted at an identifier
    # (`app.route('/x').get(h)`), `receiver` holds the base identifier and
    # this holds (innermost method name, its static first string arg or None):
    # here ("route", "/x"). None when the receiver is not such a chain.
    recv_call: tuple[str, str | None] | None = None


@dataclass(frozen=True, slots=True)
class FileFacts:
    """Pass 1 output for a single file. Cacheable by content hash."""

    path: str
    lang: str
    symbols: tuple[SymbolRef, ...] = ()
    imports: tuple[ImportRef, ...] = ()
    calls: tuple[CallSite, ...] = ()
    fields: tuple[FieldType, ...] = ()
    reexports: tuple[str, ...] = ()
    diagnostics: tuple[Diagnostic, ...] = ()
    # `local = callee(...)` bindings, as (local name, callee text). Generic:
    # the consumer decides that `app = express()` makes `app` a route holder.
    ctor_assigns: tuple[tuple[str, str], ...] = ()


def _d_counts() -> dict[tuple[str, str, Resolution], int]:
    return {}


def _d_samples() -> dict[tuple[str, str], list[str]]:
    return {}


@dataclass
class Scorecard:
    """Per-language, per-edge-kind resolution tally.

    Per-language because quality varies materially between them: TypeScript
    resolves imports at 63-96% against Python's 37-47%. One blended number
    would imply a uniformity that does not exist.
    """

    counts: dict[tuple[str, str, Resolution], int] = field(default_factory=_d_counts)
    samples: dict[tuple[str, str], list[str]] = field(default_factory=_d_samples)

    def record(
        self, lang: str, kind: EdgeKind | str, res: Resolution, sample: str | None = None
    ) -> None:
        k = kind.value if isinstance(kind, EdgeKind) else kind
        key = (lang, k, res)
        self.counts[key] = self.counts.get(key, 0) + 1
        if res is Resolution.UNRESOLVED and sample is not None:
            bucket = self.samples.setdefault((lang, k), [])
            if len(bucket) < 10 and sample not in bucket:
                bucket.append(sample)

    def total(self, lang: str, kind: str) -> int:
        return sum(v for (ln, k, _), v in self.counts.items() if ln == lang and k == kind)

    def get(self, lang: str, kind: str, res: Resolution) -> int:
        return self.counts.get((lang, kind, res), 0)

    def pinned_rate(self, lang: str, kind: str) -> float:
        """Fraction of *intra-repo* references we pinned to something.

        Known-external is excluded from the denominator: a third-party import
        is not a resolution failure, and counting it as one would flatter or
        deflate the number depending on how framework-heavy the repo is.
        """
        intra = (
            self.get(lang, kind, Resolution.RESOLVED)
            + self.get(lang, kind, Resolution.CANDIDATE)
            + self.get(lang, kind, Resolution.UNRESOLVED)
        )
        if not intra:
            return 0.0
        pinned = self.get(lang, kind, Resolution.RESOLVED) + self.get(
            lang, kind, Resolution.CANDIDATE
        )
        return pinned / intra * 100.0

    def rows(self) -> list[tuple[str, str, int, int, int, int, float]]:
        keys = sorted({(ln, k) for (ln, k, _) in self.counts})
        out: list[tuple[str, str, int, int, int, int, float]] = []
        for lang, kind in keys:
            out.append(
                (
                    lang,
                    kind,
                    self.get(lang, kind, Resolution.RESOLVED),
                    self.get(lang, kind, Resolution.CANDIDATE),
                    self.get(lang, kind, Resolution.EXTERNAL),
                    self.get(lang, kind, Resolution.UNRESOLVED),
                    self.pinned_rate(lang, kind),
                )
            )
        return out

    def to_json_obj(self) -> dict[str, object]:
        """The scorecard as data, with its own caveat attached.

        The caveat travels with the numbers on purpose. A promoted decision
        records that this measures **pinning, not correctness**: a confidently
        wrong edge counts as resolved. A consumer reading `pinned: 96.4` out of
        a JSON file has no other way to learn that, and a percentage without
        that sentence reads as an accuracy claim it cannot support.
        """
        return {
            "measures": (
                "the share of intra-repository references pinned to a target, "
                "not whether the target is correct; a wrong edge counts as resolved"
            ),
            "rows": [
                {
                    "lang": lang,
                    "kind": kind,
                    "resolved": r,
                    "candidate": c,
                    "external": e,
                    "unresolved": u,
                    "pinned_pct": round(pct, 2),
                }
                for lang, kind, r, c, e, u, pct in self.rows()
            ],
            "unresolved_samples": {
                f"{lang}/{kind}": sorted(v)
                for (lang, kind), v in sorted(self.samples.items())
                if v
            },
        }

    def render(self) -> str:
        lines = [
            f"{'lang':<12}{'kind':<14}{'resolved':>9}{'candidate':>11}"
            f"{'external':>10}{'unresolved':>12}{'pinned':>9}",
            "-" * 78,
        ]
        for lang, kind, r, c, e, u, pct in self.rows():
            lines.append(f"{lang:<12}{kind:<14}{r:>9}{c:>11}{e:>10}{u:>12}{pct:>8.1f}%")
        return "\n".join(lines)


@dataclass(frozen=True, order=True, slots=True)
class RouteFact:
    """A handler declaring an HTTP route, claimed only when the file imports
    the framework the decorator shape belongs to.

    The path is the one the decorator declares. Mount prefixes
    (`include_router(prefix="/api/v1")`, blueprints) are not composed in, so
    this is the route as written at the handler, not necessarily the full URL.
    """

    method: str  # GET/POST/.../WS, as declared
    path: str  # the decorator's literal path argument
    file: str
    handler: str  # qualified name of the decorated definition
    framework: str  # fastapi | flask
    evidence: Evidence  # the decorator's own line


@dataclass(frozen=True, order=True, slots=True)
class TaskFact:
    """A background-task handler (Celery), same import-gated rule as routes."""

    file: str
    handler: str
    framework: str  # celery
    evidence: Evidence


@dataclass(frozen=True, order=True, slots=True)
class EntrypointFact:
    """A declared executable entry: pyproject scripts or package.json bin.

    Evidence is the manifest line that declares it, located by a line scan
    that is verified against the parsed content; a declaration whose line
    cannot be located produces no fact rather than a guessed line.
    """

    name: str
    target: str  # "pkg.mod:func" for python, a file path for javascript
    file: str  # the manifest
    lang: str  # python | javascript
    evidence: Evidence


@dataclass(frozen=True, slots=True)
class ExtractResult:
    nodes: tuple[Node, ...]
    edges: tuple[Edge, ...]
    scorecard: Scorecard
    diagnostics: tuple[Diagnostic, ...] = ()
    routes: tuple[RouteFact, ...] = ()
    tasks: tuple[TaskFact, ...] = ()
    entrypoints: tuple[EntrypointFact, ...] = ()


# A syntax tree deeper than this is walked no further. Minified bundles nest
# thousands of levels: one 16 KB file in a real 10,403-file repository nested
# past CPython's frame limit and raised RecursionError out of the extractor,
# taking the whole analysis down. A cap belongs with the file size and count
# caps design §3 already defines, and it degrades rather than disabling: the
# shallow part of the file still contributes facts and a diagnostic says the
# rest was skipped. Comfortably under the default 1000-frame limit, since a
# visitor burns more than one frame per level.
MAX_AST_DEPTH = 300


def depth_capped(path: str, kind: str = "syntax tree") -> Diagnostic:
    return Diagnostic(
        code="SVA-X-003",
        severity=Severity.WARNING,
        message=(
            f"{kind} nests deeper than {MAX_AST_DEPTH} levels; the deeper part was "
            "not walked, so facts from it are missing"
        ),
        subject=path,
        suggested_fixes=(
            "this is usually a minified or generated bundle; add it to .svarupaignore",
        ),
    )


class Extractor(ABC):
    """One language. Pass 1 only; resolution is the resolver's job."""

    lang: str
    grammar_version: str

    @abstractmethod
    def parse(self, path: str, data: bytes) -> FileFacts:
        """Extract facts from one file. Must not read any other file."""

    @staticmethod
    def evidence(path: str, start_row: int, end_row: int) -> Evidence:
        """tree-sitter rows are 0-indexed; Evidence lines are 1-indexed."""
        return Evidence(path, start_row + 1, max(end_row + 1, start_row + 1))


def sorted_nodes(nodes: Iterable[Node]) -> tuple[Node, ...]:
    return tuple(sorted(nodes, key=lambda n: (n.id, n.kind.value)))


def sorted_edges(edges: Iterable[Edge]) -> tuple[Edge, ...]:
    return tuple(sorted(edges, key=lambda e: (e.src, e.dst, e.kind.value, e.resolution.value)))


def module_of(path: str) -> str:
    """The structural module a file belongs to: its directory.

    Module identity is directory-shaped and comes from declared structure, not
    from clustering. See the design's decision log.
    """
    parent = str(Path(path).parent)
    return "" if parent == "." else parent


def qualified_prefix(path: str, lang: str) -> str:
    """Dotted prefix for qualified names within a file."""
    stem = path.rsplit(".", 1)[0] if "." in Path(path).name else path
    if lang == "python" and stem.endswith("/__init__"):
        stem = stem[: -len("/__init__")]
    return stem.replace("/", ".")


def dedupe_preserving_order(items: Sequence[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    out: list[str] = []
    for x in items:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return tuple(out)
