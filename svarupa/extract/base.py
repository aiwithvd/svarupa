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

from svarupa.diagnostics import Diagnostic
from svarupa.model import Edge, EdgeKind, Evidence, Node, Resolution

__all__ = [
    "CallSite",
    "Extractor",
    "FileFacts",
    "ImportRef",
    "Scorecard",
    "SymbolRef",
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
    """

    BARE = "bare"
    SELF = "self"
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


@dataclass(frozen=True, slots=True)
class CallSite:
    """A call, unresolved."""

    name: str
    shape: CallShape
    receiver: str | None
    evidence: Evidence
    enclosing: str | None
    enclosing_class: str | None


@dataclass(frozen=True, slots=True)
class FileFacts:
    """Pass 1 output for a single file. Cacheable by content hash."""

    path: str
    lang: str
    symbols: tuple[SymbolRef, ...] = ()
    imports: tuple[ImportRef, ...] = ()
    calls: tuple[CallSite, ...] = ()
    reexports: tuple[str, ...] = ()
    diagnostics: tuple[Diagnostic, ...] = ()


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

    def render(self) -> str:
        lines = [
            f"{'lang':<12}{'kind':<14}{'resolved':>9}{'candidate':>11}"
            f"{'external':>10}{'unresolved':>12}{'pinned':>9}",
            "-" * 78,
        ]
        for lang, kind, r, c, e, u, pct in self.rows():
            lines.append(f"{lang:<12}{kind:<14}{r:>9}{c:>11}{e:>10}{u:>12}{pct:>8.1f}%")
        return "\n".join(lines)


@dataclass(frozen=True, slots=True)
class ExtractResult:
    nodes: tuple[Node, ...]
    edges: tuple[Edge, ...]
    scorecard: Scorecard
    diagnostics: tuple[Diagnostic, ...] = ()


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
