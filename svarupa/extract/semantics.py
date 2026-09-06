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
from svarupa.extract.base import (
    CallSite,
    DecoratorRef,
    EntrypointFact,
    ExternalFact,
    FileFacts,
    RouteFact,
    TaskFact,
)
from svarupa.extract.vocabulary import classify_import, package_root
from svarupa.model import Evidence

__all__ = ["SEMANTIC_FRAMEWORKS", "SEMANTIC_LANGS", "Semantics", "semantics"]

# What route/task extraction actually covers, published so the report can
# state the boundary. Framework-level because the language alone overclaims:
# a Django or Koa service is "python"/"javascript" and still invisible here.
SEMANTIC_LANGS: tuple[str, ...] = ("python", "typescript", "javascript")
SEMANTIC_FRAMEWORKS: tuple[str, ...] = ("fastapi", "flask", "celery", "express", "nestjs")

# FastAPI/Starlette route-declaring method names. `websocket` is a route in
# every sense a reader cares about; it renders as method WS.
_HTTP_METHODS = frozenset({"get", "post", "put", "delete", "patch", "head", "options"})

_TABLE_RE = re.compile(r"^\s*\[([^\]]+)\]\s*(?:#.*)?$")

# Express route-declaring method names on an app/router object. `all` is a
# real Express method covering every verb; recorded as ALL, never expanded.
_EXPRESS_METHODS = frozenset(
    {"get", "post", "put", "delete", "patch", "head", "options", "all"}
)

# NestJS route decorators to HTTP methods.
_NEST_DECORATORS = {
    "Get": "GET",
    "Post": "POST",
    "Put": "PUT",
    "Delete": "DELETE",
    "Patch": "PATCH",
    "Options": "OPTIONS",
    "Head": "HEAD",
    "All": "ALL",
}


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
    externals: tuple[ExternalFact, ...] = ()


def _externals_for(f: FileFacts) -> list[ExternalFact]:
    """One fact per (file, package) the vocabulary knows, citing the import.

    Relative imports are the codebase's own modules and never external; a
    file importing `redis` three times yields one fact, at the first line.
    """
    out: dict[str, ExternalFact] = {}
    for imp in sorted(f.imports, key=lambda i: (i.evidence.start_line, i.specifier)):
        if imp.is_relative or imp.specifier.startswith("."):
            continue
        hit = classify_import(imp.specifier, f.lang)
        if hit is None:
            continue
        category, label = hit
        root = package_root(imp.specifier, f.lang)
        if root not in out:
            out[root] = ExternalFact(
                file=f.path,
                category=category,
                label=label,
                package=root,
                evidence=imp.evidence,
            )
    return list(out.values())


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
            # A route path starts with `/` (or is empty, FastAPI's idiom for
            # "the router's own prefix"). `@cache.get("user:profile")` in a
            # file that happens to import fastapi is somebody's cache, and
            # both frameworks reject such a string as a path anyway.
            if dec.arg is not None and dec.arg != "" and not dec.arg.startswith("/"):
                continue
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
                # None: no `methods` kwarg, so Flask's documented default is
                # GET, a fact. (): the kwarg is present but dynamic, so the
                # methods are unknown, the loop runs zero times, and no fact
                # is minted; `dec.methods or ("GET",)` here recorded GET for
                # `methods=("POST",)`, an invented method in a committed file.
                for method in dec.methods if dec.methods is not None else ("GET",):
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


def _express_ctor_names(f: FileFacts) -> frozenset[str]:
    """Callees that construct an Express app or router in this file: any name
    imported from `express` when called, plus `.Router` on any of them."""
    names: set[str] = set()
    for imp in f.imports:
        if imp.specifier == "express":
            names.update(imp.names)
    return frozenset(names | {f"{n}.Router" for n in names})


def _express_receivers(f: FileFacts) -> frozenset[str]:
    """Locals that hold an Express app or router, receiver-scoped.

    `import fastapi` plus a homemade `.get()` was review #13 S6; the same
    trap for Express is `axios.get('/users')` in a file that also imports
    express. A receiver counts only if it was assigned from the imported
    `express()` default, `express.Router()`, or an imported `Router` (alias
    respected), so the import gate is on the OBJECT, not merely the file.
    """
    # Exact-specifier match inside `_express_ctor_names`: a relative
    # `./express` spells its specifier with the leading `./`, so equality
    # alone excludes it. Any express-imported name is a route-holder
    # constructor when called (`express()`, `Router()`, an alias of either),
    # and so is `.Router` on any of them: one rule, because a Router/default
    # split whose branches then union is a distinction the data structure
    # cannot express.
    ctors = _express_ctor_names(f)
    if not ctors:
        return frozenset()
    held = {name for name, callee in f.ctor_assigns if callee in ctors}
    # The gate is on the object, and a name is not an object: a same-file
    # `const app = makeCache()` inside a helper shares the top-level `app`'s
    # name, and claiming its `.get('/cache-key')` is a wrong committed edge.
    # A name bound by ANY non-express constructor in the file leaves the set;
    # losing a true route to a name collision is the cheap direction.
    poisoned = {name for name, callee in f.ctor_assigns if callee not in ctors}
    return frozenset(held - poisoned)


def _join_paths(prefix: str, sub: str) -> str:
    """`/api` + `/things/` -> `/api/things/`: the sub-path keeps its own
    spelling, trailing slash included, so a mounted route is spelled the same
    way an unmounted one is. Stripping both ends made `/things/` and `/things`
    collapse onto one lock key only when mounted (#15 C1)."""
    head = prefix.rstrip("/")
    if not sub or sub == "/":
        return head or "/"
    tail = sub if sub.startswith("/") else "/" + sub
    return (head + tail) or "/"


def _mount_prefixes(
    f: FileFacts,
    receivers: frozenset[str],
    shadowed: frozenset[str],
    diags: list[Diagnostic],
) -> dict[str, list[tuple[str, tuple[Evidence, ...]]] | None]:
    """Same-file `host.use('/prefix', router)` mounts, resolved to full prefixes
    with the mount lines that produced each one.

    Only mounts where both host and router are receivers in THIS file are
    composed: a router imported from elsewhere, or a middleware argument that
    is not a router, is not a mount here. A router mounted twice exists at
    both prefixes and yields both. Any dynamic mount path anywhere in a
    router's chain poisons that router (value None): its routes stay
    handler-relative, the documented boundary, rather than composed wrong.

    A name bound by an express constructor more than once in the file
    (`shadowed`) is not one object, so it neither mounts nor is mounted: a
    factory's inner `const router = Router()` sharing the top-level name
    minted `GET /users/healthz` for a route served at `/healthz` (#15 F2).

    Cycles are detected on the resolution path and poison every router on it,
    with a diagnostic; there is no depth cap, because a cap poisoned routers
    depending on which mount statement was met first (#15 S1).
    """
    mounts: dict[str, list[tuple[str, str | None, Evidence]]] = {}
    for call in f.calls:
        if call.name != "use" or call.receiver not in receivers:
            continue
        routers = [i for i in call.ident_args if i in receivers]
        if not routers:
            continue
        # Three first-argument shapes: a static string is the prefix; the
        # router itself (`app.use(router)`) mounts at the host's own prefix;
        # anything else (`app.use(PFX, router)`, `api.use(auth, users)`) is
        # a dynamic prefix, which must poison rather than read as "".
        if call.first_str_arg is not None:
            prefix: str | None = call.first_str_arg
        elif call.first_arg_ident is not None and call.first_arg_ident in receivers:
            prefix = ""
        else:
            prefix = None
        if call.receiver in shadowed:
            prefix = None
        for router in routers:
            if router in shadowed:
                continue
            mounts.setdefault(router, []).append((call.receiver, prefix, call.evidence))

    resolved: dict[str, list[tuple[str, tuple[Evidence, ...]]] | None] = {}
    reported: set[str] = set()

    def prefixes(
        router: str, path: tuple[str, ...]
    ) -> list[tuple[str, tuple[Evidence, ...]]] | None:
        if router in resolved:
            return resolved[router]
        if router in path:
            cycle = " -> ".join((*path[path.index(router) :], router))
            if cycle not in reported:
                reported.add(cycle)
                diags.append(
                    Diagnostic(
                        code="SVA-X-009",
                        severity=Severity.INFO,
                        message=(
                            "an express router mount forms a cycle or mounts itself, so "
                            "its routes keep their declared paths"
                        ),
                        subject=cycle,
                        location=f.path,
                    )
                )
            return None
        hosts = mounts.get(router)
        if not hosts:
            return [("", ())]
        out: list[tuple[str, tuple[Evidence, ...]]] = []
        for host, pfx, ev in sorted(hosts, key=lambda h: (h[0], h[1] or "", h[2])):
            if pfx is None or (pfx and not pfx.startswith("/")):
                resolved[router] = None
                return None
            above = prefixes(host, (*path, router))
            if above is None:
                resolved[router] = None
                return None
            for a, via in above:
                out.append((_join_paths(a, pfx) if (a or pfx) else "", (*via, ev)))
        resolved[router] = sorted(set(out))
        return resolved[router]

    for router in sorted(mounts):
        prefixes(router, ())
    return resolved


def _express_routes(f: FileFacts, diags: list[Diagnostic]) -> list[RouteFact]:
    receivers = _express_receivers(f)
    if not receivers:
        return []
    express_ctors = _express_ctor_names(f)
    binds: dict[str, int] = {}
    for name, callee in f.ctor_assigns:
        if callee in express_ctors:
            binds[name] = binds.get(name, 0) + 1
    shadowed = frozenset(n for n, k in binds.items() if k > 1)

    declared: list[tuple[str, str, str, CallSite]] = []  # receiver, method, path, site
    for call in f.calls:
        if call.receiver not in receivers or call.name not in _EXPRESS_METHODS:
            continue
        method = "ALL" if call.name == "all" else call.name.upper()
        if call.recv_call is not None:
            # `app.route('/x').get(h)`: the path lives on the chain's root.
            root_method, root_path = call.recv_call
            if root_method == "route" and root_path is not None and root_path.startswith("/"):
                declared.append((call.receiver, method, root_path, call))
            continue
        if call.first_str_arg is not None and call.first_str_arg.startswith("/"):
            declared.append((call.receiver, method, call.first_str_arg, call))

    mounted = _mount_prefixes(f, receivers, shadowed, diags)
    out: list[RouteFact] = []
    for receiver, method, path, call in declared:
        prefixes = None if receiver in shadowed else mounted.get(receiver)
        # Not mounted here (an app, or a router mounted elsewhere): the
        # declared path. Poisoned or shadowed: the declared path, the
        # documented boundary. Mounted statically: one fact per full prefix,
        # carrying the mount lines as `via` so every part is cited.
        composed = (
            [(path, ())]
            if not prefixes
            else [(_join_paths(p, path), via) for p, via in prefixes]
        )
        for full, via in composed:
            out.append(
                RouteFact(
                    method=method,
                    path=full,
                    file=f.path,
                    handler=call.enclosing or f.path,
                    framework="express",
                    evidence=call.evidence,
                    via=via,
                )
            )
    return out


def _nest_path(prefix: str | None, sub: str | None) -> str:
    segments = [s.strip("/") for s in (prefix, sub) if s and s.strip("/")]
    return "/" + "/".join(segments) if segments else "/"


def _nest_routes(f: FileFacts) -> list[RouteFact]:
    """NestJS: `@Controller('users')` on the class, `@Get(':id')` on the
    method, composed into `GET /users/:id`.

    Same-file composition only, which is why it is safe: both decorators are
    in front of the reader at the cited lines. The claim cites the method
    decorator, where the route is declared.
    """
    if not any(
        not imp.is_relative and imp.specifier.startswith("@nestjs/") for imp in f.imports
    ):
        return []
    # A controller whose prefix is present-but-dynamic (`@Controller(PREFIX)`,
    # `@Controller(['a','b'])`) has an unknown prefix, so every route in it is
    # unknown: the class is excluded rather than composed wrong.
    controllers = {
        s.name: next(
            (d.arg for d in s.decorators if d.name.rsplit(".", 1)[-1] == "Controller"),
            None,
        )
        for s in f.symbols
        if s.kind == "class"
        and any(d.name.rsplit(".", 1)[-1] == "Controller" for d in s.decorators)
        and not any(
            d.arg_dynamic for d in s.decorators if d.name.rsplit(".", 1)[-1] == "Controller"
        )
    }
    out: list[RouteFact] = []
    for s in f.symbols:
        if s.kind != "method" or s.enclosing_class not in controllers:
            continue
        for dec in s.decorators:
            method = _NEST_DECORATORS.get(dec.name.rsplit(".", 1)[-1])
            if method is None:
                continue
            if dec.arg_dynamic:
                # `@Get(PATH)`: the route exists and its path is unknown.
                # Composing None as "" would commit `GET /users` for a route
                # that lives at `/users/<something>`.
                continue
            out.append(
                RouteFact(
                    method=method,
                    path=_nest_path(controllers[s.enclosing_class], dec.arg),
                    file=f.path,
                    handler=s.qualified_name,
                    framework="nestjs",
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


def _key_line(lines: Sequence[str], table: str, key: str, value: str) -> int | None:
    """The 1-based line declaring `key = value` inside TOML `[table]`.

    A scanner, not a parser: tomllib already established the semantics, this
    only locates them. It tracks the current table header, matches a `key =`
    line (bare or quoted) inside the right one, and requires the parsed value
    on the same line. The value check is what stops a line *inside a
    multiline string* that happens to read `key = ...` from being cited: the
    scanner has no string state, so agreement with the parser is the guard.
    """
    current = ""
    pattern = re.compile(rf'^\s*(?:{re.escape(key)}|"{re.escape(key)}")\s*=')
    for i, line in enumerate(lines, start=1):
        m = _TABLE_RE.match(line)
        if m:
            current = m.group(1).strip()
            continue
        if (
            current == table
            and pattern.match(line)
            and (f'"{value}"' in line or f"'{value}'" in line)
        ):
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
            line = _key_line(lines, table, name, target)
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

    def bin_range() -> tuple[int, int]:
        """1-based inclusive line range of the `bin` object, brace-tracked.

        Scanning the whole document cited `"config": {"serve": "./x.js"}`
        for the identically-spelled `bin` entry below it; mirrored keys
        across config/scripts/bin are ordinary in real manifests.
        """
        start = next((i for i, ln in enumerate(lines, start=1) if '"bin"' in ln), None)
        if start is None:
            return (0, -1)
        depth = 0
        for i in range(start, len(lines) + 1):
            seg = lines[i - 1]
            if i == start:
                seg = seg[seg.index('"bin"') :]
            depth += seg.count("{") - seg.count("}")
            if depth <= 0:
                return (start, i)
        return (start, len(lines))

    lo, hi = bin_range()

    def locate(key: str, value: str) -> int | None:
        # Both the quoted key and the parsed value, inside the bin object's
        # own line range, so a same-named key elsewhere cannot be cited.
        needle_key, needle_val = f'"{key}"', json.dumps(value)
        for i in range(lo, hi + 1):
            if needle_key in lines[i - 1] and needle_val in lines[i - 1]:
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
    externals: list[ExternalFact] = []
    diags: list[Diagnostic] = []

    for f in facts:
        externals.extend(_externals_for(f))
        if f.lang == "python":
            routes.extend(_routes_for(f))
            tasks.extend(_tasks_for(f))
        elif f.lang in ("typescript", "javascript"):
            routes.extend(_express_routes(f, diags))
            routes.extend(_nest_routes(f))

    for rec in scan.files:
        name = rec.path.rsplit("/", 1)[-1]
        if name not in ("pyproject.toml", "package.json"):
            continue
        # The same gate routes and tasks already have: a manifest in a test
        # fixture, vendored tree or generated directory declares nothing
        # about the production architecture. Without this, a
        # tests/fixtures/pyproject.toml minted a committed entrypoint record
        # and coloured a real module `cli`.
        if not rec.role.in_architecture:
            continue
        try:
            text = (scan.root / rec.path).read_text(encoding="utf8")
        except (OSError, UnicodeDecodeError) as exc:
            # A new loading channel is part of the parser's boundary: degrade
            # AND say so, never skip silently.
            diags.append(
                Diagnostic(
                    code="SVA-X-008",
                    severity=Severity.WARNING,
                    message=(
                        f"a manifest could not be read for semantic facts "
                        f"({type(exc).__name__}), so its entrypoints are missing"
                    ),
                    subject=rec.path,
                )
            )
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
        externals=tuple(sorted(set(externals))),
    )
