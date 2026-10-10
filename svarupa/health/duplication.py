"""Duplicated blocks: the same 10 or more lines in more than one place.

Lines are compared after collapsing whitespace. Blank lines, comments,
punctuation-only lines and import or package lines are skipped, so shared
import blocks and license headers never count as copy-paste.
"""

from __future__ import annotations

from collections.abc import Mapping

from svarupa.extract.base import module_of
from svarupa.health.catalog import BY_ID
from svarupa.health.model import Violation
from svarupa.model import Evidence

__all__ = ["duplicated_blocks"]

WINDOW = 10
_SKIP_PREFIXES = ("#", "//", "/*", "*", "import ", "from ", "package ", "using ", "require(")


def _lines(text: str) -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    for number, raw in enumerate(text.splitlines(), 1):
        line = " ".join(raw.split())
        if not line or line.startswith(_SKIP_PREFIXES) or not any(ch.isalnum() for ch in line):
            continue
        out.append((number, line))
    return out


def duplicated_blocks(texts: Mapping[str, str]) -> list[Violation]:
    check = BY_ID["duplicated-block"]
    files = {path: _lines(text) for path, text in sorted(texts.items())}
    seen: dict[str, list[tuple[str, int]]] = {}
    for path, lines in files.items():
        for i in range(len(lines) - WINDOW + 1):
            key = "\n".join(text for _, text in lines[i : i + WINDOW])
            seen.setdefault(key, []).append((path, i))
    covered: set[tuple[str, int]] = set()
    out: list[Violation] = []
    for path, lines in files.items():
        for i in range(len(lines) - WINDOW + 1):
            if (path, i) in covered:
                continue
            key = "\n".join(text for _, text in lines[i : i + WINDOW])
            places = [
                p
                for p in seen[key]
                if p == (path, i) or p[0] != path or abs(p[1] - i) >= WINDOW
            ]
            if len(places) < 2:
                continue
            length = WINDOW
            while all(
                p[1] + length < len(files[p[0]])
                and files[p[0]][p[1] + length][1] == lines[i + length][1]
                for p in places
                if i + length < len(lines)
            ) and i + length < len(lines):
                length += 1
            evidence: list[Evidence] = []
            for p_path, p_i in sorted(places):
                block = files[p_path][p_i : p_i + length]
                evidence.append(Evidence(p_path, block[0][0], block[-1][0]))
                covered.update((p_path, p_i + k) for k in range(length))
            out.append(
                Violation(
                    check="duplicated-block",
                    message=f"{length} lines repeated in {len(places)} places",
                    evidence=tuple(evidence),
                    module=module_of(evidence[0].file),
                    value=length,
                    minutes=check.minutes,
                )
            )
    return out
