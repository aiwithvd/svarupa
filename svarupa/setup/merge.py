"""Editing files a user may own, without touching what they wrote.

A Markdown rules file gets one block between two markers: appended once,
replaced in place after that, and nothing outside the markers changes. An
MCP config gets one `svarupa` server merged in next to the user's own. A
file that cannot be edited safely is refused, never rewritten.
"""

from __future__ import annotations

import json
from typing import Any, cast

__all__ = ["BEGIN", "END", "MergeError", "merge_block", "merge_mcp"]

BEGIN = "<!-- svarupa:begin -->"
END = "<!-- svarupa:end -->"


class MergeError(ValueError):
    """A file that cannot be edited without guessing."""


def _block(body: str) -> str:
    return f"{BEGIN}\n{body if body.endswith(chr(10)) else body + chr(10)}{END}\n"


def merge_block(existing: str | None, body: str) -> str:
    """`existing` with the Svarupa block added or replaced."""
    block = _block(body)
    if not existing:
        return block
    count = existing.count(BEGIN)
    if count > 1:
        raise MergeError("has more than one svarupa begin marker; remove the extra one")
    if count == 0:
        sep = "" if existing.endswith("\n") else "\n"
        return f"{existing}{sep}\n{block}"
    start = existing.index(BEGIN)
    end = existing.find(END, start)
    if end == -1:
        raise MergeError("has a svarupa begin marker but no end marker; add the end marker")
    stop = end + len(END)
    if existing.startswith("\n", stop):
        stop += 1
    elif existing.startswith("\r\n", stop):
        stop += 2
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


def merge_mcp(existing: str | None, key: str, entry: dict[str, object]) -> str:
    """`existing` MCP JSON with `key.svarupa` set to `entry`, others kept."""
    data: Any = {}
    if existing and existing.strip():
        try:
            data = json.loads(
                existing, parse_constant=_no_constant, object_pairs_hook=_no_duplicates
            )
        except MergeError:
            raise
        except ValueError as exc:
            raise MergeError(f"is not a JSON object ({exc}); fix or remove it") from exc
    if not isinstance(data, dict):
        raise MergeError("is not a JSON object; fix or remove it")
    doc = cast("dict[str, Any]", data)
    servers: Any = doc.setdefault(key, {})
    if not isinstance(servers, dict):
        raise MergeError(f"has a {key} value that is not a JSON object")
    cast("dict[str, Any]", servers)["svarupa"] = entry
    try:
        text = json.dumps(doc, indent=2, ensure_ascii=False, allow_nan=False)
    except ValueError as exc:
        raise MergeError(
            f"has a number that is not valid JSON ({exc}); fix or remove it"
        ) from exc
    return text + "\n"
