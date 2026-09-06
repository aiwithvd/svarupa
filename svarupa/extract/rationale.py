"""Rationale: the "why" that lives in comments and docstrings.

Graphify's consumers ask "why does this exist" as often as "what calls this",
and the answer, when the codebase has one, is a docstring or a `# NOTE`,
`# WHY`, `# HACK`, `# TODO` comment. Each becomes a node cited at its own
line, attached by a `rationale_for` edge to the innermost definition that
contains it, or to the module when nothing does.

This is a line scanner, not a parser: a docstring is the first string
statement of a file or the one right under a `class` line, a marker comment
is a line whose comment starts with one of the markers. It runs only over
architecture-eligible source, so vendored and generated text never becomes a
rationale claim.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from svarupa.model import Evidence

__all__ = ["MARKERS", "RationaleFact", "rationale_facts"]

MARKERS = ("NOTE", "WHY", "HACK", "TODO", "FIXME")
_MARKER_RE = re.compile(
    r"(?:#|//)\s*(?P<kind>" + "|".join(MARKERS) + r")\b[:\s-]*(?P<text>.*)$"
)
_CLASS_RE = re.compile(r"^\s*class\s+\w+.*:\s*(#.*)?$")
_TS_LEAD = re.compile(r"^\s*/\*\*")
_MAX_TEXT = 200
_SOURCE_SUFFIXES = (".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")


@dataclass(frozen=True, slots=True)
class RationaleFact:
    file: str
    line: int
    end_line: int
    kind: str  # docstring | note | why | hack | todo | fixme
    text: str

    @property
    def evidence(self) -> Evidence:
        return Evidence(file=self.file, start_line=self.line, end_line=self.end_line)


def _clean(text: str) -> str:
    text = text.strip().strip("*").strip()
    return text[: _MAX_TEXT - 1] + "…" if len(text) > _MAX_TEXT else text


def _docstring_at(lines: list[str], i: int) -> tuple[str, int] | None:
    """The first content line of a triple-quoted string starting at `i`, and
    the index of its closing line; None when `i` does not open one."""
    stripped = lines[i].strip()
    for q in ('"""', "'''"):
        if stripped.startswith(q):
            body = stripped[len(q) :]
            if body.endswith(q) and len(body) >= len(q):
                return _clean(body[: -len(q)]), i
            first = body.strip()
            for j in range(i + 1, min(len(lines), i + 200)):
                seg = lines[j]
                if q in seg:
                    if not first:
                        first = seg.split(q, 1)[0].strip()
                    return _clean(first), j
                if not first:
                    first = seg.strip()
            return None
    return None


def _ts_block_at(lines: list[str], i: int) -> tuple[str, int] | None:
    if not _TS_LEAD.match(lines[i]):
        return None
    first = lines[i].split("/**", 1)[1].split("*/", 1)[0].strip()
    for j in range(i, min(len(lines), i + 200)):
        seg = lines[j]
        if j > i and not first:
            first = seg.strip().lstrip("*").strip()
        if "*/" in seg:
            return (_clean(first), j) if first else None
    return None


def _scan(file: str, lines: list[str]) -> list[RationaleFact]:
    out: list[RationaleFact] = []
    is_py = file.endswith(".py")
    # Module docstring: the first non-blank, non-comment line.
    for i, raw in enumerate(lines):
        s = raw.strip()
        if not s or s.startswith("#") or (not is_py and s.startswith("//")):
            continue
        found = _docstring_at(lines, i) if is_py else _ts_block_at(lines, i)
        if found and found[0]:
            out.append(RationaleFact(file, i + 1, found[1] + 1, "docstring", found[0]))
        break
    # Class docstrings (Python): the string right under a class line.
    if is_py:
        for i, raw in enumerate(lines[:-1]):
            if _CLASS_RE.match(raw):
                found = _docstring_at(lines, i + 1)
                if found and found[0]:
                    out.append(RationaleFact(file, i + 2, found[1] + 1, "docstring", found[0]))
    # Marker comments.
    for i, raw in enumerate(lines):
        m = _MARKER_RE.search(raw)
        if m and _clean(m.group("text")):
            out.append(
                RationaleFact(
                    file, i + 1, i + 1, m.group("kind").lower(), _clean(m.group("text"))
                )
            )
    return sorted(out, key=lambda f: (f.line, f.kind))


def rationale_facts(root: Path, files: frozenset[str] | set[str]) -> tuple[RationaleFact, ...]:
    """Every rationale in the given repository-relative source files."""
    out: list[RationaleFact] = []
    for rel in sorted(files):
        if not rel.endswith(_SOURCE_SUFFIXES):
            continue
        try:
            text = (root / rel).read_text(encoding="utf8", errors="replace")
        except OSError:
            continue
        out.extend(_scan(rel, text.splitlines()))
    return tuple(out)
