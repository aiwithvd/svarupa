"""Editing files a user may own, without touching what they wrote.

A Markdown rules file gets one block between two markers: appended once,
replaced in place after that, and nothing outside the markers changes. An
MCP config gets one `svarupa` server merged in next to the user's own. A
file that cannot be edited safely is refused, never rewritten.
"""

from __future__ import annotations

import json
import re
from typing import Any, cast

__all__ = ["BEGIN", "END", "MergeError", "merge_block", "merge_mcp"]

BEGIN = "<!-- svarupa:begin -->"
END = "<!-- svarupa:end -->"


class MergeError(ValueError):
    """A file that cannot be edited without guessing."""


def _newline(existing: str) -> str:
    return "\r\n" if "\r\n" in existing else "\n"


def _block(body: str, nl: str = "\n") -> str:
    text = body if body.endswith("\n") else body + "\n"
    return f"{BEGIN}\n{text}{END}\n".replace("\n", nl)


def _marker_lines(text: str, marker: str) -> list[tuple[int, int]]:
    """(start, end) of each line that is exactly `marker`, newline excluded.

    A marker only counts as the whole line (a trailing "\\r" allowed): prose
    that mentions the marker mid-sentence is the user's text, not ours.
    """
    found: list[tuple[int, int]] = []
    pos = 0
    for line in text.splitlines(keepends=True):
        if line.rstrip("\r\n") == marker:
            found.append((pos, pos + len(marker)))
        pos += len(line)
    return found


def merge_block(existing: str | None, body: str) -> str:
    """`existing` with the Svarupa block added or replaced.

    The file's line endings are kept: a CRLF file gets a CRLF block."""
    if not existing:
        return _block(body)
    nl = _newline(existing)
    block = _block(body, nl)
    begins = _marker_lines(existing, BEGIN)
    if len(begins) > 1:
        raise MergeError("has more than one svarupa begin marker; remove the extra one")
    if not begins:
        sep = "" if existing.endswith("\n") else nl
        return f"{existing}{sep}{nl}{block}"
    start = begins[0][0]
    ends = [e for e in _marker_lines(existing, END) if e[0] > start]
    if not ends:
        raise MergeError("has a svarupa begin marker but no end marker; add the end marker")
    stop = ends[0][1]
    if existing.startswith("\r\n", stop):
        stop += 2
    elif existing.startswith("\n", stop):
        stop += 1
    return existing[:start] + block + existing[stop:]


def _no_constant(_name: str) -> Any:
    raise MergeError("has NaN or Infinity, which is not valid JSON; fix or remove it")


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in pairs:
        if k in out:
            raise MergeError(f"has a duplicate key {k!r}; fix or remove it")
        out[k] = v
    return out


def _indent(existing: str) -> str | int:
    m = re.search(r"^([ \t]+)\S", existing, re.MULTILINE)
    if m is None:
        return 2
    lead = m.group(1)
    return "\t" if lead.startswith("\t") else len(lead)


def _parse_object(existing: str | None) -> dict[str, Any]:
    if not existing or not existing.strip():
        return {}
    try:
        data: Any = json.loads(
            existing, parse_constant=_no_constant, object_pairs_hook=_no_duplicates
        )
    except MergeError:
        raise
    except ValueError as exc:
        raise MergeError(f"is not a JSON object ({exc}); fix or remove it") from exc
    if not isinstance(data, dict):
        raise MergeError("is not a JSON object; fix or remove it")
    return cast("dict[str, Any]", data)


def _server_map(doc: dict[str, Any], key: str) -> dict[str, Any]:
    servers: Any = doc.setdefault(key, {})
    if not isinstance(servers, dict):
        raise MergeError(f"has a {key} value that is not a JSON object")
    return cast("dict[str, Any]", servers)


def _dump(doc: dict[str, Any], existing: str) -> str:
    try:
        text = json.dumps(doc, indent=_indent(existing), ensure_ascii=False, allow_nan=False)
    except ValueError as exc:
        raise MergeError(
            f"has a number that is not valid JSON ({exc}); fix or remove it"
        ) from exc
    return (text + "\n").replace("\n", _newline(existing))


def merge_mcp(existing: str | None, key: str, entry: dict[str, object]) -> str:
    """`existing` MCP JSON with `key.svarupa` set to `entry`, others kept.

    If the entry is already there the file is returned byte for byte. A real
    merge keeps the file's indentation (tabs or N spaces, 2 for a new file)
    and line endings, but re-serializes the JSON, so other formatting may
    change: for example the number 1.50 is written back as 1.5."""
    doc = _parse_object(existing)
    servers = _server_map(doc, key)
    if existing and servers.get("svarupa") == entry:
        return existing
    servers["svarupa"] = entry
    return _dump(doc, existing or "")
