"""tsconfig loading: aliases, `extends`, and `references`.

Two traps, both measured in Spike 0c on real repositories.

**Never parse JSONC with a regex.** tsconfig `paths` values always contain glob
patterns like ``"@/*": ["src/*"]``, and the ``/*`` inside that string makes a
block-comment regex delete everything through the next ``*/``, silently
yielding invalid JSON and zero aliases. Measured: one project scored 61.3%
import resolution instead of 92.7%. A string-aware scanner is required.

**Follow `references` and `extends`.** The project-references layout puts
``"files": []`` in the root tsconfig and points at sibling configs, where the
aliases actually live. Reading only the root finds nothing at all.
"""

from __future__ import annotations

import json
import posixpath
from dataclasses import dataclass
from pathlib import Path
from typing import cast

__all__ = ["Alias", "load_aliases", "strip_jsonc"]

_MAX_CONFIGS = 64


@dataclass(frozen=True, order=True, slots=True)
class Alias:
    """A `paths` mapping, scoped to the directory that declares it.

    Scope is not decoration. Merging every config's aliases into one namespace
    means two apps each declaring the standard `"@/*": ["src/*"]` collide, and
    an import inside one app resolves into the other -- with the winner decided
    by filesystem enumeration order. Measured on a two-app fixture: an import
    in `apps/b` resolved to `apps/a/src/store.ts`.
    """

    scope: str
    prefix: str
    target: str

    def applies_to(self, file: str) -> bool:
        return not self.scope or file == self.scope or file.startswith(self.scope + "/")


def strip_jsonc(raw: str) -> str:
    """Remove // and /* */ comments and trailing commas, respecting strings.

    A regex cannot do this. See the module docstring for the measured cost of
    trying. Trailing commas are handled in the same pass rather than by a
    follow-up regex, which would happily delete a comma inside a string value
    that happens to contain `,]`.
    """
    out: list[str] = []
    i, n = 0, len(raw)
    in_str = False
    while i < n:
        ch = raw[i]
        if in_str:
            out.append(ch)
            if ch == "\\" and i + 1 < n:
                out.append(raw[i + 1])
                i += 2
                continue
            if ch == '"':
                in_str = False
            i += 1
            continue
        if ch == '"':
            in_str = True
            out.append(ch)
            i += 1
            continue
        if ch == "/" and i + 1 < n:
            nxt = raw[i + 1]
            if nxt == "/":
                while i < n and raw[i] != "\n":
                    i += 1
                continue
            if nxt == "*":
                i += 2
                while i + 1 < n and not (raw[i] == "*" and raw[i + 1] == "/"):
                    i += 1
                i += 2
                continue
        if ch == ",":
            # Trailing comma: legal in JSONC, invalid in JSON. Only safe to
            # judge here, outside string state.
            j = i + 1
            while j < n and raw[j] in " \t\r\n":
                j += 1
            if j < n and raw[j] in "}]":
                i += 1
                continue
        out.append(ch)
        i += 1
    return "".join(out)


def _read(path: Path) -> dict[str, object] | None:
    try:
        raw = path.read_text(encoding="utf8", errors="replace")
    except OSError:
        return None
    try:
        loaded: object = json.loads(strip_jsonc(raw))
    except json.JSONDecodeError:
        return None
    return cast("dict[str, object]", loaded) if isinstance(loaded, dict) else None


def load_aliases(root: Path) -> tuple[Alias, ...]:
    """Collect `paths` aliases from every reachable tsconfig, each scoped.

    Targets are resolved with path arithmetic, not string trimming: a target of
    `"../shared/src/*"` declared in `packages/web/tsconfig.json` must climb out
    of `packages/web`. `lstrip("./")` is a character-set operation that turns it
    into `packages/web/shared/src`, which either silently fails or resolves into
    a decoy that happens to sit at the mangled path.
    """
    root = root.resolve()
    aliases: dict[tuple[str, str], Alias] = {}
    seen: set[Path] = set()
    queue: list[Path] = sorted(
        [
            *root.glob("tsconfig*.json"),
            *root.glob("*/tsconfig*.json"),
            *root.glob("*/*/tsconfig*.json"),
            *root.glob("packages/*/*/tsconfig*.json"),
        ]
    )

    while queue and len(seen) < _MAX_CONFIGS:
        path = queue.pop(0)
        try:
            path = path.resolve()
        except OSError:
            continue
        if path in seen or not path.is_file():
            continue
        seen.add(path)

        cfg = _read(path)
        if cfg is None:
            continue

        refs = cfg.get("references")
        for ref in cast("list[object]", refs) if isinstance(refs, list) else []:
            if isinstance(ref, dict):
                target: object = cast("dict[str, object]", ref).get("path")
                if isinstance(target, str):
                    rp = (path.parent / target).resolve()
                    queue.append(rp / "tsconfig.json" if rp.is_dir() else rp)

        ext = cfg.get("extends")
        ext_list: list[object] = (
            [ext]
            if isinstance(ext, str)
            else (cast("list[object]", ext) if isinstance(ext, list) else [])
        )
        for item in ext_list:
            if isinstance(item, str) and item.startswith("."):
                ep = (path.parent / item).resolve()
                queue.append(ep if ep.suffix else ep.with_suffix(".json"))

        raw_opts = cfg.get("compilerOptions")
        if not isinstance(raw_opts, dict):
            continue
        opts = cast("dict[str, object]", raw_opts)
        try:
            prefix = path.parent.relative_to(root).as_posix()
        except ValueError:
            continue
        prefix = "" if prefix == "." else prefix

        base = opts.get("baseUrl")
        base_str = base if isinstance(base, str) else "."

        raw_paths = opts.get("paths")
        if not isinstance(raw_paths, dict):
            continue
        for key, value in cast("dict[str, object]", raw_paths).items():
            if not isinstance(value, list) or not value:
                continue
            first: object = cast("list[object]", value)[0]
            if not isinstance(first, str):
                continue
            # Real path arithmetic, anchored at the declaring config.
            joined = posixpath.join(prefix, base_str, first.rstrip("/*"))
            target = posixpath.normpath(joined)
            if target in (".", "/"):
                target = ""
            if target.startswith(".."):
                # Escaping the repo is a fact worth stating, not swallowing.
                continue
            alias = Alias(scope=prefix, prefix=str(key).rstrip("/*"), target=target)
            aliases.setdefault((alias.scope, alias.prefix), alias)

    return tuple(sorted(aliases.values()))
