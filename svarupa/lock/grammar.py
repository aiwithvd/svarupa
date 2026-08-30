"""The architecture lockfile: grammar, serializer, parser.

This is the one artifact users commit and diff by hand, so its format is
frozen early and deliberately. Retrofitting escaping after lockfiles exist in
the wild is a schema break for every adopter.

Four properties it must have (design 7.1):

1. **Facts only, no line numbers.** Line numbers are the most volatile data in
   the system; any edit above a record shifts them. Evidence lives in
   ``graph.json``, which is regenerated, not committed.
2. **One fact per line.** ``module api -> auth, billing`` renders an added
   dependency as one removed line plus one added line, forcing the reviewer to
   eyeball-diff a comma list. That breaks the promise the format exists to keep.
3. **Real escaping.** POSIX paths may contain spaces, commas, even ``" -> "``.
   Fields are TAB-separated because tab cannot appear unescaped in a path or an
   endpoint template.
4. **A published evolution policy.** Additive record kinds bump the minor and
   still diff; parsers skip unknown kinds. Only field-shape changes to an
   existing kind bump the major and refuse.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import cast

__all__ = [
    "SCHEMA_MAJOR",
    "SCHEMA_MINOR",
    "Header",
    "Lockfile",
    "Record",
    "SchemaMismatch",
    "escape_field",
    "unescape_field",
]

SCHEMA_MAJOR = 1
SCHEMA_MINOR = 0

_SEP = "\t"
_COMMENT = "#"

# Kinds this build understands. An unknown kind is NOT an error: it is retained
# verbatim so an older tool can still diff a newer lockfile, with the unknown
# lines showing as opaque adds and removes.
KNOWN_KINDS: frozenset[str] = frozenset(
    {
        "module",  # module <id>
        "dep",  # dep <from> <to>
        "endpoint",  # endpoint <method-and-path> <handler-module>   (P2)
        "datastore",  # datastore <name>                              (P2)
        "service",  # service <name>                                (P2)
        "queue",  # queue <name>                                  (P2)
        "surface",  # surface <module> <exported-symbol>            (P2)
    }
)


class SchemaMismatch(Exception):
    """Raised when a lockfile cannot be diffed against this build."""


def escape_field(s: str) -> str:
    """Escape a single field.

    Backslash first, otherwise escaping the others would be re-escaped.
    """
    return (
        s.replace("\\", "\\\\").replace("\t", "\\t").replace("\n", "\\n").replace("\r", "\\r")
    )


def unescape_field(s: str) -> str:
    out: list[str] = []
    i, n = 0, len(s)
    while i < n:
        c = s[i]
        if c == "\\" and i + 1 < n:
            nxt = s[i + 1]
            out.append({"\\": "\\", "t": "\t", "n": "\n", "r": "\r"}.get(nxt, "\\" + nxt))
            i += 2
            continue
        out.append(c)
        i += 1
    return "".join(out)


@dataclass(frozen=True, order=True, slots=True)
class Record:
    """One architectural fact.

    Ordering is by ``(kind, fields)`` on raw codepoints, which is what makes
    serialization canonical and therefore diffable.
    """

    kind: str
    fields: tuple[str, ...]

    def render(self) -> str:
        return _SEP.join([self.kind, *(escape_field(f) for f in self.fields)])

    @staticmethod
    def parse(line: str) -> Record:
        parts = line.split(_SEP)
        return Record(parts[0], tuple(unescape_field(p) for p in parts[1:]))

    @property
    def is_known(self) -> bool:
        return self.kind in KNOWN_KINDS


@dataclass(frozen=True, slots=True)
class Header:
    tool_version: str
    schema_major: int = SCHEMA_MAJOR
    schema_minor: int = SCHEMA_MINOR
    grammars: Mapping[str, str] = field(default_factory=lambda: cast("dict[str, str]", {}))

    def __post_init__(self) -> None:
        # Sorted so the rendered header is canonical: grammar order must not
        # depend on the order extractors happened to register themselves.
        object.__setattr__(self, "grammars", dict(sorted(self.grammars.items())))

    def render(self) -> list[str]:
        g = " ".join(f"{k}@{v}" for k, v in self.grammars.items())
        return [
            f"{_COMMENT} svarupa {self.tool_version}",
            f"{_COMMENT} schema {self.schema_major}.{self.schema_minor}",
            f"{_COMMENT} grammars {g}" if g else f"{_COMMENT} grammars",
        ]


@dataclass(frozen=True, slots=True)
class Lockfile:
    header: Header
    records: tuple[Record, ...]

    @staticmethod
    def build(
        tool_version: str,
        grammars: Mapping[str, str],
        records: Iterable[Record],
    ) -> Lockfile:
        # Deduplicate and canonically sort. Sorting on the dataclass order
        # (kind, then fields) uses raw codepoints, never locale collation.
        uniq = sorted(set(records))
        return Lockfile(Header(tool_version, grammars=dict(grammars)), tuple(uniq))

    def render(self) -> str:
        lines = [*self.header.render(), *(r.render() for r in self.records)]
        return "\n".join(lines) + "\n"

    @staticmethod
    def parse(text: str) -> Lockfile:
        tool_version = "unknown"
        major, minor = SCHEMA_MAJOR, SCHEMA_MINOR
        grammars: dict[str, str] = {}
        records: list[Record] = []

        for raw in text.splitlines():
            if not raw.strip():
                continue
            if raw.startswith(_COMMENT):
                body = raw[1:].strip()
                if body.startswith("svarupa "):
                    tool_version = body.split(" ", 1)[1].strip()
                elif body.startswith("schema "):
                    val = body.split(" ", 1)[1].strip()
                    part = val.split(".")
                    try:
                        major = int(part[0])
                        minor = int(part[1]) if len(part) > 1 else 0
                    except ValueError as exc:
                        raise SchemaMismatch(f"unparseable schema stamp {val!r}") from exc
                elif body.startswith("grammars"):
                    for tok in body.split()[1:]:
                        if "@" in tok:
                            k, v = tok.rsplit("@", 1)
                            grammars[k] = v
                continue
            records.append(Record.parse(raw))

        return Lockfile(Header(tool_version, major, minor, grammars), tuple(records))

    def assert_diffable(self, other: Lockfile) -> None:
        """Refuse only on a MAJOR mismatch.

        A minor bump means new record kinds were added. Those diff as opaque
        adds and removes, which is strictly better than refusing: otherwise
        every release that adds a record kind becomes a flag day for every
        adopter, which is exactly the moment they are lost.
        """
        if self.header.schema_major != other.header.schema_major:
            raise SchemaMismatch(
                f"lockfile schema {self.header.schema_major}.x cannot be diffed "
                f"against {other.header.schema_major}.x. Regenerate the base "
                f"lockfile with this version of svarupa."
            )

    def unknown_kinds(self) -> set[str]:
        return {r.kind for r in self.records if not r.is_known}


def module_record(module_id: str) -> Record:
    return Record("module", (module_id,))


def dep_record(src: str, dst: str) -> Record:
    return Record("dep", (src, dst))


def collision_check(module_ids: Sequence[str]) -> list[tuple[str, list[str]]]:
    """Find ids that collide after normalization or case-folding.

    Two directories distinct on Linux can be one file on a case-insensitive
    macOS filesystem, and NFC normalization can merge two Linux-distinct names.
    Either case must raise a diagnostic rather than silently merging keys,
    because a silently merged module is a silently wrong lockfile.

    Normalization and case-insensitivity are separate axes and both are checked.
    """
    buckets: dict[str, list[str]] = {}
    for mid in module_ids:
        buckets.setdefault(mid.casefold(), []).append(mid)
    return sorted((k, sorted(set(v))) for k, v in buckets.items() if len(set(v)) > 1)
