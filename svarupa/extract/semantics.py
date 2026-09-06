"""Semantic facts: routes, background tasks, declared entrypoints.

Three rules keep this honest:

* **A decorator shape alone claims nothing.** `@app.get("/x")` is a FastAPI
  route only in a file that imports fastapi; the same text in a file that
  defines its own `app` object is somebody's DSL. Every claim here is gated on
  the framework import in the same file, and the claim cites the decorator's
  own line.
* **Declared beats inferred.** Entrypoints come from what the manifest says
  (`[project.scripts]`, package.json `bin`), not from `if __name__ ==
  "__main__"` heuristics.
* **A fact without a locatable line is no fact.** tomllib and json discard
  positions, so the declaration line is found by a scanner over the same
  bytes, cross-checked against the parsed value; when the scan cannot place a
  parsed declaration, the fact is dropped and a diagnostic says so, rather
  than citing a guessed line.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import cast

from svarupa.detect import Scan, load_toml
from svarupa.diagnostics import Diagnostic, Severity
from svarupa.extract.base import DecoratorRef, EntrypointFact, FileFacts, RouteFact, TaskFact
from svarupa.model import Evidence

__all__ = ["Semantics", "semantics"]

# FastAPI/Starlette route-declaring method names. `websocket` is a route in
# every sense a reader cares about; it renders as method WS.
_HTTP_METHODS = frozenset({"get", "post", "put", "delete", "patch", "head", "options"})

_TABLE_RE = re.compile(r"^\s*\[([^\]]+)\]\s*(?:#.*)?$")


def _dig_dict(data: object, *keys: str) -> dict[str, object] | None:
    cur: object = data
    for k in keys:
        if not isinstance(cur, dict):
            return None
        cur = cast("dict[str, object]", cur).get(k)
    return cast("dict[str, object]", cur) if isinstance(cur, dict) else None


@dataclass(frozen=True, slots=True)
class Semantics:
    routes: tuple[RouteFact, ...]
    tasks: tuple[TaskFact, ...]
    entrypoints: tuple[EntrypointFact, ...]
    diagnostics: tuple[Diagnostic, ...]


def _imports_top(f: FileFacts, top: str) -> bool:
    return any(not imp.is_relative and imp.specifier.split(".")[0] == top for imp in f.imports)


def _routes_for(f: FileFacts) -> list[RouteFact]:
    out: list[RouteFact] = []
    fastapi = _imports_top(f, "fastapi")
    flask = _imports_top(f, "flask")
    if not (fastapi or flask):
        return out
    for s in f.symbols:
        for dec in s.decorators:
            tail = dec.name.rsplit(".", 1)[-1]
            if fastapi and tail in _HTTP_METHODS and dec.arg is not None:
                out.append(
                    RouteFact(
                        method=tail.upper(),
                        path=dec.arg,
                        file=f.path,
                        handler=s.qualified_name,
                        framework="fastapi",
                        evidence=dec.evidence,
                    )
                )
            elif fastapi and tail == "websocket" and dec.arg is not None:
                out.append(
                    RouteFact(
                        method="WS",
                        path=dec.arg,
                        file=f.path,
                        handler=s.qualified_name,
                        framework="fastapi",
                        evidence=dec.evidence,
                    )
                )
            elif flask and tail == "route" and dec.arg is not None:
                # Flask's documented default when `methods` is absent is GET.
                for method in dec.methods or ("GET",):
                    out.append(
                        RouteFact(
                            method=method.upper(),
                            path=dec.arg,
                            file=f.path,
                            handler=s.qualified_name,
                            framework="flask",
                            evidence=dec.evidence,
                        )
                    )
    return out


def _is_task(dec: DecoratorRef) -> bool:
    tail = dec.name.rsplit(".", 1)[-1]
    return tail in ("task", "shared_task")


def _tasks_for(f: FileFacts) -> list[TaskFact]:
    if not _imports_top(f, "celery"):
        return []
    return [
        TaskFact(
            file=f.path,
            handler=s.qualified_name,
            framework="celery",
            evidence=dec.evidence,
        )
        for s in f.symbols
        for dec in s.decorators
        if _is_task(dec)
    ]


def _key_line(lines: Sequence[str], table: str, key: str) -> int | None:
    """The 1-based line declaring `key` inside TOML `[table]`.

    A scanner, not a parser: tomllib already established the semantics, this
    only locates them. It tracks the current table header and matches a
    `key =` line (bare or quoted) inside the right one, so a same-named key in
    another table cannot be cited.
    """
    current = ""
    pattern = re.compile(rf'^\s*(?:{re.escape(key)}|"{re.escape(key)}")\s*=')
    for i, line in enumerate(lines, start=1):
        m = _TABLE_RE.match(line)
        if m:
            current = m.group(1).strip()
            continue
        if current == table and pattern.match(line):
            return i
    return None


def _pyproject_entrypoints(
    path: str, text: str, diags: list[Diagnostic]
) -> list[EntrypointFact]:
    doc = load_toml(text)
    if doc is None:
        return []  # a malformed manifest is diagnosed by detect, not here
    lines = text.split("\n")
    out: list[EntrypointFact] = []
    tables = (
        ("project.scripts", _dig_dict(doc, "project", "scripts")),
        ("tool.poetry.scripts", _dig_dict(doc, "tool", "poetry", "scripts")),
    )
    for table, mapping in tables:
        if mapping is None:
            continue
        for name, target in mapping.items():
            if not isinstance(target, str):
                continue
            line = _key_line(lines, table, name)
            if line is None:
                diags.append(_unlocatable(path, f"{table}.{name}"))
                continue
            out.append(
                EntrypointFact(
                    name=name,
                    target=target,
                    file=path,
                    lang="python",
                    evidence=Evidence(path, line, line),
                )
            )
    return out


def _package_json_entrypoints(
    path: str, text: str, diags: list[Diagnostic]
) -> list[EntrypointFact]:
    try:
        doc = json.loads(text)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return []
    if not isinstance(doc, dict):
        return []
    typed = cast("dict[str, object]", doc)
    bin_value = typed.get("bin")
    lines = text.split("\n")
    out: list[EntrypointFact] = []

    def locate(key: str, value: str) -> int | None:
        # The line must contain both the quoted key and the quoted value, so a
        # same-named key elsewhere in the document cannot be cited.
        needle_key, needle_val = f'"{key}"', json.dumps(value)
        for i, line in enumerate(lines, start=1):
            if needle_key in line and needle_val in line:
                return i
        return None

    if isinstance(bin_value, str):
        raw_name = typed.get("name")
        name = raw_name if isinstance(raw_name, str) else "bin"
        line = locate("bin", bin_value)
        if line is None:
            diags.append(_unlocatable(path, "bin"))
        else:
            out.append(
                EntrypointFact(
                    name=name,
                    target=bin_value,
                    file=path,
                    lang="javascript",
                    evidence=Evidence(path, line, line),
                )
            )
    elif isinstance(bin_value, dict):
        for name, target in cast("dict[str, object]", bin_value).items():
            if not isinstance(target, str):
                continue
            line = locate(name, target)
            if line is None:
                diags.append(_unlocatable(path, f"bin.{name}"))
                continue
            out.append(
                EntrypointFact(
                    name=name,
                    target=target,
                    file=path,
                    lang="javascript",
                    evidence=Evidence(path, line, line),
                )
            )
    return out


def _unlocatable(path: str, subject: str) -> Diagnostic:
    return Diagnostic(
        code="SVA-X-007",
        severity=Severity.INFO,
        message=(
            "an entrypoint is declared but its declaration line could not be "
            "located, so no evidence-backed fact was emitted"
        ),
        subject=subject,
        location=path,
    )


def semantics(scan: Scan, facts: Sequence[FileFacts]) -> Semantics:
    routes: list[RouteFact] = []
    tasks: list[TaskFact] = []
    entrypoints: list[EntrypointFact] = []
    diags: list[Diagnostic] = []

    for f in facts:
        routes.extend(_routes_for(f))
        tasks.extend(_tasks_for(f))

    for rec in scan.files:
        name = rec.path.rsplit("/", 1)[-1]
        if name not in ("pyproject.toml", "package.json"):
            continue
        try:
            text = (scan.root / rec.path).read_text(encoding="utf8")
        except (OSError, UnicodeDecodeError):
            continue
        if name == "pyproject.toml":
            entrypoints.extend(_pyproject_entrypoints(rec.path, text, diags))
        else:
            entrypoints.extend(_package_json_entrypoints(rec.path, text, diags))

    return Semantics(
        routes=tuple(sorted(set(routes))),
        tasks=tuple(sorted(set(tasks))),
        entrypoints=tuple(sorted(set(entrypoints))),
        diagnostics=tuple(diags),
    )
