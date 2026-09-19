"""Core data model.

The whole product rests on one invariant: **every node and every edge carries
`file:line` evidence, or it does not exist.** That is enforced here, at
construction, so no downstream stage has to remember to check.

See design 3 (Data model) and 3.4 (Fail-closed enforcement).
"""

from __future__ import annotations

import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum

__all__ = [
    "Attrs",
    "Confidence",
    "Edge",
    "EdgeKind",
    "Evidence",
    "MissingEvidenceError",
    "Node",
    "NodeKind",
    "Resolution",
    "norm_path",
]


class MissingEvidenceError(ValueError):
    """Raised when an element would enter the graph without provenance.

    Carries the producing extractor so the diagnostic names a culprit rather
    than a symptom.
    """

    def __init__(self, what: str, subject: str, producer: str | None = None) -> None:
        self.what = what
        self.subject = subject
        self.producer = producer
        src = f" (produced by {producer})" if producer else ""
        super().__init__(
            f"{what} {subject!r} has no evidence{src}. "
            "Elements without a source location are not emitted; "
            "see the resolution scorecard for what was abandoned."
        )


def norm_path(p: str) -> str:
    """Normalize a repo-relative path to the canonical on-the-wire form.

    NFC normalization is not cosmetic. APFS *preserves* whatever bytes it is
    given (unlike HFS+, which normalized to NFD on write) and is merely
    normalization-insensitive on lookup, so the byte form of a path depends on
    whichever tool created the file, what git stored, and git's own
    `core.precomposeunicode`. Two developers can hold byte-different paths for
    the same logical filename, which would break lockfile byte-identity.

    Recording the real mechanism matters: a guard justified by a wrong
    mechanism gets deleted by whoever later discovers APFS does not normalize.
    """
    return unicodedata.normalize("NFC", p).replace("\\", "/")


class NodeKind(str, Enum):
    MODULE = "module"
    FUNCTION = "function"
    CLASS = "class"
    METHOD = "method"
    INTERFACE = "interface"
    ENDPOINT = "endpoint"
    TABLE = "table"
    COLUMN = "column"
    SERVICE = "service"
    DATASTORE = "datastore"
    QUEUE = "queue"
    RESOURCE = "resource"
    JOB = "job"


class EdgeKind(str, Enum):
    CALLS = "calls"
    IMPORTS = "imports"
    INHERITS = "inherits"
    IMPLEMENTS = "implements"
    CONTAINS = "contains"
    READS = "reads"
    WRITES = "writes"
    EXPOSES = "exposes"
    REFERENCES = "references"
    DEPENDS_ON = "depends_on"
    PUBLISHES = "publishes"
    CONSUMES = "consumes"
    DEPLOYS = "deploys"


class Confidence(str, Enum):
    """How the edge was established.

    There is deliberately no ``INFERRED`` tier. An edge nobody can point at
    does not exist. ``RESOLVED`` still carries evidence; it names the call or
    import site that motivated the cross-file link.
    """

    EXTRACTED = "EXTRACTED"
    RESOLVED = "RESOLVED"


class Resolution(str, Enum):
    """Scorecard bin. Three bins, not two.

    Counting every resolution failure as ``EXTERNAL`` launders resolver bugs
    into a number that looks like honesty. ``UNRESOLVED`` is the figure the
    report surfaces.
    """

    RESOLVED = "resolved"
    CANDIDATE = "candidate"
    EXTERNAL = "external"
    UNRESOLVED = "unresolved"


def evidence_order(e: Evidence) -> tuple[bool, Evidence]:
    """The one order citations are shown in: real lines first, then empty
    files cited as themselves (review #24 N3: the viewer reordered while the
    diagram JSON and the CLI kept `api/__init__.py:0` first)."""
    return (e.start_line == 0, e)


@dataclass(frozen=True, slots=True)
class Evidence:
    """A pointer at real source. Lines are 1-indexed and inclusive.

    `start_line == end_line == 0` means the file itself, and is legal only for
    a file with no lines: an empty `__init__.py` is real evidence that a
    package exists, and `api/__init__.py:1` pointed at a line that did not
    (review #23 F3, 259 such citations on the acceptance repositories).

    Ordering is defined explicitly rather than via ``order=True``. A generated
    comparison would compare ``rev`` fields directly, and ``rev`` is
    ``str | None`` by design (a git blob sha "when available"). Mixing pinned
    and unpinned evidence on one element is the normal case, not an edge case,
    and a tuple comparison of ``None`` against ``str`` raises ``TypeError``.
    That would crash at Node construction, where evidence is sorted.
    """

    file: str
    start_line: int
    end_line: int
    rev: str | None = None

    @property
    def sort_key(self) -> tuple[str, int, int, str]:
        """Total order over all Evidence, including unpinned ones."""
        return (self.file, self.start_line, self.end_line, self.rev or "")

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, Evidence):
            return NotImplemented
        return self.sort_key < other.sort_key

    def __le__(self, other: object) -> bool:
        if not isinstance(other, Evidence):
            return NotImplemented
        return self.sort_key <= other.sort_key

    def __gt__(self, other: object) -> bool:
        if not isinstance(other, Evidence):
            return NotImplemented
        return self.sort_key > other.sort_key

    def __ge__(self, other: object) -> bool:
        if not isinstance(other, Evidence):
            return NotImplemented
        return self.sort_key >= other.sort_key

    def __post_init__(self) -> None:
        if not self.file:
            raise ValueError("Evidence.file must be non-empty")
        # (0, 0) is a whole empty file, see the class docstring.
        if self.start_line < 1 and not (self.start_line == 0 and self.end_line == 0):
            raise ValueError(f"Evidence.start_line must be >= 1, got {self.start_line}")
        if self.end_line < self.start_line:
            raise ValueError(
                f"Evidence.end_line ({self.end_line}) precedes "
                f"start_line ({self.start_line}) in {self.file}"
            )
        object.__setattr__(self, "file", norm_path(self.file))

    def __str__(self) -> str:
        span = (
            f"{self.start_line}"
            if self.start_line == self.end_line
            else f"{self.start_line}-{self.end_line}"
        )
        return f"{self.file}:{span}"


Attrs = Mapping[str, str] | Sequence[tuple[str, str]]


def _freeze_attrs(a: Attrs) -> tuple[tuple[str, str], ...]:
    """Canonicalize attrs to a sorted tuple of pairs.

    Stored as a tuple rather than a dict so elements stay hashable. Nodes and
    edges live in sets during dedup and graph assembly, so an unhashable
    element would fail late and confusingly. Sorting here also removes one more
    source of input-order dependence from the determinism contract.
    """
    items = a.items() if isinstance(a, Mapping) else a
    return tuple(sorted((str(k), str(v)) for k, v in items))


@dataclass(frozen=True, slots=True)
class Node:
    id: str
    kind: NodeKind
    label: str
    qualified_name: str
    evidence: tuple[Evidence, ...]
    lang: str | None = None
    attrs: tuple[tuple[str, str], ...] = ()
    producer: str | None = None

    def __post_init__(self) -> None:
        if not self.evidence:
            raise MissingEvidenceError("Node", self.id, self.producer)
        object.__setattr__(self, "evidence", tuple(sorted(self.evidence)))
        object.__setattr__(self, "attrs", _freeze_attrs(self.attrs))

    def attr(self, key: str, default: str | None = None) -> str | None:
        return next((v for k, v in self.attrs if k == key), default)

    @property
    def attrs_dict(self) -> dict[str, str]:
        return dict(self.attrs)


@dataclass(frozen=True, slots=True)
class Edge:
    src: str
    dst: str
    kind: EdgeKind
    evidence: tuple[Evidence, ...]
    confidence: Confidence = Confidence.EXTRACTED
    resolution: Resolution = Resolution.RESOLVED
    arity: int = 1
    attrs: tuple[tuple[str, str], ...] = ()
    producer: str | None = None

    def __post_init__(self) -> None:
        if not self.evidence:
            raise MissingEvidenceError("Edge", f"{self.src} -> {self.dst}", self.producer)
        if self.resolution is Resolution.CANDIDATE:
            # A candidate edge claims "one of N implementations". It must show
            # both the call site and the candidate definition, or a reader
            # cannot check the claim, and the fail-closed promise is hollow.
            if len(self.evidence) < 2:
                raise MissingEvidenceError(
                    "Candidate edge",
                    f"{self.src} -> {self.dst} (needs call-site and definition-site "
                    "evidence, got 1)",
                    self.producer,
                )
            if self.arity < 2:
                raise ValueError(
                    f"Candidate edge {self.src} -> {self.dst} declares arity "
                    f"{self.arity}; a candidate implies 2 or more possible targets"
                )
        object.__setattr__(self, "evidence", tuple(sorted(self.evidence)))
        object.__setattr__(self, "attrs", _freeze_attrs(self.attrs))

    def attr(self, key: str, default: str | None = None) -> str | None:
        return next((v for k, v in self.attrs if k == key), default)

    @property
    def attrs_dict(self) -> dict[str, str]:
        return dict(self.attrs)

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.src, self.dst, self.kind.value)
