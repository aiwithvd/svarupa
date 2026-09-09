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

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from svarupa.diagnostics import Diagnostic, DiagnosticError, Severity

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
# Minor 1: `service`, `datastore` and `queue` records are now emitted. Their
# kinds and arities were published in KNOWN_KINDS from the start, so a 1.0
# parser reads a 1.1 file and diffs the new lines as opaque adds, which is the
# additive path the evolution policy promises.
# Minor 2: `endpoint` (pre-published) is emitted, and `entrypoint` and `role`
# are added and emitted. An older parser diffs all three as opaque adds,
# tested the same way as the minor-1 step.
# Minor 3: Express/NestJS route coverage. Same record kinds, new producers:
# the minor tracks WHAT THIS BUILD CAN EMIT, not only the kind list, because
# the delta's upgrade attribution (SVA-L-013) keys on it. Without this bump,
# every adopter with a TS service had six new endpoint lines blamed on
# whatever PR happened to bump svarupa.
# Minor 4: Express route() chains and same-file mount composition. New facts
# are emitted and existing facts are SPELLED differently (a mounted `/things`
# becomes `/api/things`), which reads as one removal and one addition; the
# minor is what lets SVA-L-013 say that pair is the upgrade, not the PR.
# Minor 5: `role` gains `auth` and `frontend` (from imports the vocabulary
# knows); same kind, new values this build emits.
# Minor 6: files under a top-level hidden directory (`.claude/`, `.agent/`,
# `.github/`) are tooling and leave every record. Fewer facts for the same
# code is still a change in what the build emits, and SVA-L-013 must say the
# upgrade removed them, not the PR that bumped svarupa.
SCHEMA_MINOR = 6

_SEP = "\t"
_COMMENT = "#"

# Kinds this build understands, mapped to their required field count.
#
# A *well-formed* unknown kind is not an error: it is retained verbatim so an
# older tool can still diff a newer lockfile, with the unknown lines showing as
# opaque adds and removes. That tolerance exists for forward compatibility with
# future records, not as an amnesty for garbage, which is why the kind must
# still match the published production and known kinds must have right arity.
KNOWN_KINDS: dict[str, int] = {
    "module": 1,  # module <id>
    "dep": 2,  # dep <from> <to>
    "endpoint": 2,  # endpoint <method-and-path> <handler-module>   (P2)
    "datastore": 1,  # datastore <name>                             (P2)
    "service": 1,  # service <name>                                (P2)
    "queue": 1,  # queue <name>                                   (P2)
    "surface": 2,  # surface <module> <exported-symbol>            (P2)
    "entrypoint": 2,  # entrypoint <name> <module>                 (1.2)
    "role": 2,  # role <module> <api|worker|cli|auth|frontend>     (1.2, 1.5)
}

# The grammar publishes `kind := [a-z_]+`. Enforcing it is what stops an
# unresolved git conflict marker ("<<<<<<< HEAD", "=======") from parsing as an
# architecture fact and flowing through the diff engine as real change. This
# file is committed, merged by humans, and diffed by CI on every PR, so
# malformed input is the expected case, not the exotic one.
_KIND_RE = re.compile(r"^[a-z_]+$")

# NEWLINE in the grammar means LF and nothing else. str.splitlines() also
# breaks on VT (U+000B), FF (U+000C), NEL (U+0085), U+2028 and U+2029 -- all
# legal in POSIX filenames -- which would silently split one record into two
# and invent a phantom fact in the artifact users commit.
_LF = "\n"


# Accepts either form for ergonomics, normalized to the tuple on construction.
GrammarSpec = Mapping[str, str] | tuple[tuple[str, str], ...]


class SchemaMismatch(DiagnosticError):
    """A refusal, not a crash.

    Subclasses `DiagnosticError` so the tool's one refusal path handles it.
    It was a bare `Exception`, which meant the deliberate refusal this format
    is designed around reached a user as a raw traceback: the message told them
    to regenerate, wrapped in a stack telling them about our call frames.
    """

    def __init__(self, message: str, subject: str | None = None) -> None:
        super().__init__(
            Diagnostic(
                code="SVA-L-007",
                severity=Severity.ERROR,
                message=message,
                subject=subject,
                suggested_fixes=(
                    "Regenerate the lockfile with a matching major schema version.",
                ),
            )
        )


def escape_field(s: str) -> str:
    """Escape a single field.

    Backslash first, otherwise escaping the others would be re-escaped.
    """
    return (
        s.replace("\\", "\\\\").replace("\t", "\\t").replace("\n", "\\n").replace("\r", "\\r")
    )


_ESCAPES = {"\\": "\\", "t": "\t", "n": "\n", "r": "\r"}


def unescape_field(s: str) -> str:
    """Reverse `escape_field`. An unrecognized escape is an error.

    Tolerating `\\q` would make parse->render non-idempotent: it would decode to
    a literal backslash-q and re-encode as `\\\\q`, changing bytes with no
    diagnostic. Since this format is machine-written, any unknown escape means
    hand-editing or corruption, and refusing loudly beats canonicalizing
    silently.
    """
    out: list[str] = []
    i, n = 0, len(s)
    while i < n:
        c = s[i]
        if c == "\\":
            nxt = s[i + 1] if i + 1 < n else ""
            if nxt not in _ESCAPES:
                raise DiagnosticError(
                    Diagnostic(
                        code="SVA-L-003",
                        severity=Severity.ERROR,
                        message=(
                            f"unknown escape sequence '\\{nxt}'. "
                            "Valid escapes are \\\\, \\t, \\n, \\r."
                        ),
                        subject=s,
                        suggested_fixes=(
                            "Regenerate the lockfile rather than hand-editing it.",
                        ),
                    )
                )
            out.append(_ESCAPES[nxt])
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
    def parse(line: str, lineno: int | None = None) -> Record:
        parts = line.split(_SEP)
        kind = parts[0]
        loc = f"line {lineno}" if lineno else None

        if not _KIND_RE.match(kind):
            raise DiagnosticError(
                Diagnostic(
                    code="SVA-L-001",
                    severity=Severity.ERROR,
                    message=(
                        "record kind must match [a-z_]+. This usually means an "
                        "unresolved merge conflict or hand-editing."
                    ),
                    subject=line[:120],
                    location=loc,
                    suggested_fixes=(
                        "Resolve any conflict markers, then regenerate: svarupa --lock",
                    ),
                )
            )

        fields = tuple(unescape_field(x) for x in parts[1:])
        expected = KNOWN_KINDS.get(kind)
        if expected is not None and len(fields) != expected:
            raise DiagnosticError(
                Diagnostic(
                    code="SVA-L-002",
                    severity=Severity.ERROR,
                    message=(
                        f"record kind {kind!r} takes {expected} field(s), "
                        f"got {len(fields)}. A truncated line would otherwise "
                        "parse as a different fact."
                    ),
                    subject=line[:120],
                    location=loc,
                    suggested_fixes=("Regenerate the lockfile: svarupa --lock",),
                )
            )
        return Record(kind, fields)

    @property
    def is_known(self) -> bool:
        return self.kind in KNOWN_KINDS


@dataclass(frozen=True, slots=True)
class Header:
    tool_version: str
    schema_major: int = SCHEMA_MAJOR
    schema_minor: int = SCHEMA_MINOR
    # Stored as a sorted tuple, not a dict, for the same reason Node.attrs is:
    # a dict field makes the frozen dataclass unhashable and mutable after
    # construction, and post-init mutation would break header canonicality.
    grammars: GrammarSpec = ()
    stamped: bool = True

    def __post_init__(self) -> None:
        g: GrammarSpec = self.grammars
        items: Iterable[tuple[str, str]] = g.items() if isinstance(g, Mapping) else g
        object.__setattr__(self, "grammars", tuple(sorted(items)))

    @property
    def grammars_tuple(self) -> tuple[tuple[str, str], ...]:
        g = self.grammars
        return g if isinstance(g, tuple) else tuple(sorted(g.items()))

    @property
    def grammars_dict(self) -> dict[str, str]:
        return dict(self.grammars_tuple)

    def render(self) -> list[str]:
        g = " ".join(f"{k}@{v}" for k, v in self.grammars_tuple)
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
        stamped = False
        grammars: dict[str, str] = {}
        records: list[Record] = []

        # LF only. str.splitlines() would also break on VT, FF, NEL, U+2028
        # and U+2029, all legal in POSIX filenames, silently turning one
        # record into two and inventing a phantom fact.
        lines = text.split(_LF)
        for lineno, raw0 in enumerate(lines, start=1):
            raw = raw0[:-1] if raw0.endswith("\r") else raw0  # tolerate CRLF
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
                        stamped = True
                    except ValueError as exc:
                        raise SchemaMismatch(f"unparseable schema stamp {val!r}") from exc
                elif body.startswith("grammars"):
                    for tok in body.split()[1:]:
                        if "@" in tok:
                            k, v = tok.rsplit("@", 1)
                            grammars[k] = v
                continue
            records.append(Record.parse(raw, lineno))

        return Lockfile(
            Header(tool_version, major, minor, tuple(sorted(grammars.items())), stamped),
            tuple(records),
        )

    def assert_diffable(self, other: Lockfile) -> None:
        """Refuse only on a MAJOR mismatch.

        A minor bump means new record kinds were added. Those diff as opaque
        adds and removes, which is strictly better than refusing: otherwise
        every release that adds a record kind becomes a flag day for every
        adopter, which is exactly the moment they are lost.
        """
        for name, lf in (("base", self), ("head", other)):
            if not lf.header.stamped:
                raise SchemaMismatch(
                    f"the {name} lockfile carries no '# schema' stamp, so its "
                    "format cannot be established. A missing stamp is less "
                    "trustworthy than a mismatched one: refusing rather than "
                    "assuming the current schema. Regenerate it with "
                    "this version of svarupa."
                )
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
