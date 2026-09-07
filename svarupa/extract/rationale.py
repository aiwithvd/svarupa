"""Rationale: the "why" that lives in comments and docstrings.

Graphify's consumers ask "why does this exist" as often as "what calls this",
and the answer, when the codebase has one, is a docstring or a `# NOTE`,
`# WHY`, `# HACK`, `# TODO`, `# FIXME` comment. Each becomes a node cited at
its own lines, attached by a `rationale_for` edge to the innermost definition
that contains it, or to the module when nothing does.

The parser decides what is a comment and what is a docstring. The first
version was a line scanner, and review #18 fed it a `TODO` inside a URL
string, a `class B:` inside a docstring and a one-line docstring with a
trailing comment, and got a rationale node for each, one of them cited as
spanning fourteen lines into the next class. tree-sitter is already loaded
for both languages; a `comment` node is a comment and a `string` that is the
first statement of a module or class body is a docstring, and nothing else
is either. Runs only over architecture-eligible source, so vendored and
generated text never becomes a rationale claim.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import tree_sitter_python as tsp
import tree_sitter_typescript as tst
from tree_sitter import Language, Node, Parser

from svarupa.model import Evidence

__all__ = ["MARKERS", "RationaleFact", "rationale_facts"]

MARKERS = ("NOTE", "WHY", "HACK", "TODO", "FIXME")
# Anchored at the start of the comment's text, so a marker word later in a
# sentence ("see the TODO list") is not a marker.
_MARKER_RE = re.compile(r"^\s*(?P<kind>" + "|".join(MARKERS) + r")\b[:\s-]*(?P<text>.*)$")
_MAX_TEXT = 200
_PY = Language(tsp.language())
_TS = Language(tst.language_typescript())
_TSX = Language(tst.language_tsx())
_PY_SUFFIXES = (".py",)
_TS_SUFFIXES = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")


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


def _text(data: bytes, node: Node) -> str:
    return data[node.start_byte : node.end_byte].decode("utf8", errors="replace")


def _first_line(body: str) -> str:
    for line in body.splitlines():
        cleaned = _clean(line)
        if cleaned:
            return cleaned
    return ""


def _py_string_body(text: str) -> str:
    """The content of a Python string literal: prefix and quotes removed."""
    i = 0
    while i < len(text) and text[i] in "rRbBuUfF":
        i += 1
    text = text[i:]
    for q in ('"""', "'''", '"', "'"):
        if text.startswith(q) and text.endswith(q) and len(text) >= 2 * len(q):
            return text[len(q) : -len(q)]
    return text


def _docstring_of(body: Node | None, data: bytes) -> tuple[Node, str] | None:
    """The string node that is the first statement of `body`, with its text."""
    if body is None:
        return None
    for child in body.children:
        if child.type == "comment":
            continue
        if child.type == "expression_statement" and child.children:
            s = child.children[0]
            if s.type == "string":
                return s, _first_line(_py_string_body(_text(data, s)))
        return None
    return None


def _comment_fact(file: str, node: Node, data: bytes) -> RationaleFact | None:
    text = _text(data, node)
    if text.startswith("#"):
        text = text[1:]
    elif text.startswith("/*"):
        text = text[2:].removeprefix("*").removesuffix("*/")
    elif text.startswith("//"):
        text = text[2:]
    first = text.strip().splitlines()[0] if text.strip() else ""
    m = _MARKER_RE.match(first)
    if not m or not _clean(m.group("text")):
        return None
    return RationaleFact(
        file,
        node.start_point.row + 1,
        node.end_point.row + 1,
        m.group("kind").lower(),
        _clean(m.group("text")),
    )


def _walk(node: Node):  # type: ignore[no-untyped-def]
    stack = [node]
    while stack:
        n = stack.pop()
        yield n
        stack.extend(reversed(n.children))


def _scan_python(file: str, data: bytes) -> list[RationaleFact]:
    root = Parser(_PY).parse(data).root_node
    out: list[RationaleFact] = []
    found = _docstring_of(root, data)
    if found and found[1]:
        s, text = found
        out.append(
            RationaleFact(file, s.start_point.row + 1, s.end_point.row + 1, "docstring", text)
        )
    for n in _walk(root):
        if n.type == "class_definition":
            found = _docstring_of(n.child_by_field_name("body"), data)
            if found and found[1]:
                s, text = found
                out.append(
                    RationaleFact(
                        file, s.start_point.row + 1, s.end_point.row + 1, "docstring", text
                    )
                )
        elif n.type == "comment":
            fact = _comment_fact(file, n, data)
            if fact:
                out.append(fact)
    return out


def _scan_typescript(file: str, data: bytes) -> list[RationaleFact]:
    root = Parser(_TSX if file.endswith(".tsx") else _TS).parse(data).root_node
    out: list[RationaleFact] = []
    first = next((c for c in root.children), None)
    doc: Node | None = None
    if first is not None and first.type == "comment":
        text = _text(data, first)
        if text.startswith("/**"):
            body = text[3:].removesuffix("*/")
            line = _first_line(body)
            if line:
                doc = first
                out.append(
                    RationaleFact(
                        file,
                        first.start_point.row + 1,
                        first.end_point.row + 1,
                        "docstring",
                        line,
                    )
                )
    # Only the comment consumed as the docstring is exempt: a `// TODO` that
    # happens to be the file's first node is still a marker.
    for n in _walk(root):
        if n.type == "comment" and n is not doc:
            fact = _comment_fact(file, n, data)
            if fact:
                out.append(fact)
    return out


def rationale_facts(root: Path, files: frozenset[str] | set[str]) -> tuple[RationaleFact, ...]:
    """Every rationale in the given repository-relative source files."""
    out: list[RationaleFact] = []
    for rel in sorted(files):
        if not rel.endswith(_PY_SUFFIXES + _TS_SUFFIXES):
            continue
        try:
            data = (root / rel).read_bytes()
        except OSError:
            continue
        try:
            facts = (
                _scan_python(rel, data) if rel.endswith(".py") else _scan_typescript(rel, data)
            )
        except (RecursionError, ValueError):
            continue
        out.extend(sorted(facts, key=lambda f: (f.line, f.kind, f.text)))
    return tuple(out)
