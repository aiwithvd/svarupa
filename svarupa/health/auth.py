"""Write endpoints with no auth marker on the handler, its router or the file.

A marker is a known decorator, dependency, guard or middleware name (each
pack lists its own; teams add theirs in design.yaml). A route counts as
protected when a marker appears in its own lines (the route declaration and
handler) or in the file's top-level code (a router built with a dependency,
a guard on the controller class), never in an import line. A unit with no
marker anywhere is not judged: Svarupa cannot tell "no auth" from "auth it
does not know", and says so instead. Auth attached where routers are
mounted (`include_router(..., dependencies=...)`, `app.use(authenticate)`,
a global guard) covers the whole unit.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence

from svarupa.build import Graph
from svarupa.extract.base import RouteFact, module_of
from svarupa.extract.packs import PACKS
from svarupa.health.catalog import BY_ID
from svarupa.health.model import Violation

__all__ = ["auth_checks"]

_WRITES = frozenset({"POST", "PUT", "PATCH", "DELETE", "ALL"})
_PUBLIC = re.compile(
    r"(?i)(?:^|/)(?:login|logout|signin|sign-in|signup|sign-up|register|token|refresh|oauth"
    r"|callback|webhooks?|health|healthz|password-recovery|reset-password|forgot-password"
    r"|verify-email)(?:/|$|-|\{)"
)
_MOUNT = re.compile(
    r"\binclude_router\(|\.use\(|useGlobalGuards\(|APP_GUARD\b|register_blueprint\("
)
_IMPORT = re.compile(
    r"^[ \t]*from[ \t]+[\w.]+[ \t]+import[ \t]*\([^)]*\)"
    r"|^[ \t]*import[ \t]*(?:type[ \t]+)?\{[^}]*\}[^\n]*"
    r"|^[ \t]*import[ \t]*\([^)]*\)"
    r"|^[ \t]*(?:from[ \t]+[\w.]+[ \t]+)?import[ \t][^\n]*"
    r"|^[^\n]*\brequire\([^)]*\)[^\n]*",
    re.MULTILINE,
)


def _defaults() -> frozenset[str]:
    return frozenset(m for p in PACKS if p.risks is not None for m in p.risks.auth_markers)


def _blank_imports(text: str) -> list[str]:
    """The file's lines with every import statement blanked, newlines kept."""
    return _IMPORT.sub(lambda m: "\n" * m.group(0).count("\n"), text).split("\n")


def _unit_of(path: str, units: Sequence[str]) -> str:
    best = ""
    for u in units:
        if u and (path == u or path.startswith(u + "/")) and len(u) > len(best):
            best = u
    return best


def _window(graph: Graph, r: RouteFact) -> tuple[int, int]:
    start, end = r.evidence.start_line, r.evidence.end_line
    node = graph.nodes.get(f"{r.file}#{r.handler}") if r.framework != "express" else None
    if node is not None and node.evidence:
        start = min(start, node.evidence[0].start_line)
        end = max(end, node.evidence[0].end_line)
    return start, end


def auth_checks(
    graph: Graph,
    texts: Mapping[str, str],
    markers: Iterable[str],
    units: Sequence[str] = ("",),
) -> tuple[list[Violation], list[str]]:
    routes = [r for r in graph.routes if r.file in texts]
    if not routes:
        return [], []
    names = sorted(_defaults() | set(markers), key=len, reverse=True)
    rx = re.compile(r"(?<![\w$])(?:" + "|".join(map(re.escape, names)) + r")(?![\w$])")
    lines = {path: _blank_imports(text) for path, text in texts.items()}
    spans: dict[str, list[tuple[int, int]]] = {}
    for fm in graph.functions:
        spans.setdefault(fm.evidence.file, []).append(
            (fm.evidence.start_line, fm.evidence.end_line)
        )
    windows = {r: _window(graph, r) for r in routes}
    by_file: dict[str, list[tuple[int, int]]] = {}
    for r, w in windows.items():
        by_file.setdefault(r.file, []).append(w)

    tops: dict[str, str] = {}

    def top_level(path: str) -> str:
        """The file's lines outside every function and route, built once."""
        if path not in tops:
            covered = [False] * (len(lines[path]) + 2)
            for a, b in spans.get(path, []) + by_file.get(path, []):
                for n in range(a, min(b, len(lines[path])) + 1):
                    covered[n] = True
            tops[path] = "\n".join(
                text for n, text in enumerate(lines[path], start=1) if not covered[n]
            )
        return tops[path]

    check = BY_ID["endpoint-without-auth"]
    out: list[Violation] = []
    notes: list[str] = []
    for unit in sorted({_unit_of(r.file, units) for r in routes}):
        unit_routes = [r for r in routes if _unit_of(r.file, units) == unit]
        files = [p for p in texts if _unit_of(p, units) == unit]
        if not any(rx.search("\n".join(lines[p])) for p in files):
            notes.append(
                f"{unit or '.'}: no auth marker found, so endpoint-without-auth was not "
                "checked; add your markers to design.yaml under auth_markers"
            )
            continue
        # Auth attached where a router is mounted or a guard registered
        # (`include_router(r, dependencies=[...])`, `app.use(authenticate)`,
        # `useGlobalGuards`) covers the unit's routes: Svarupa does not trace
        # which router it names, so it errs toward protected.
        if any(_MOUNT.search(line) and rx.search(line) for p in files for line in lines[p]):
            continue
        for r in sorted(unit_routes):
            if r.method not in _WRITES or _PUBLIC.search(r.path):
                continue
            a, b = windows[r]
            own = "\n".join(lines[r.file][a - 1 : b])
            if rx.search(own) or rx.search(top_level(r.file)):
                continue
            node_id = f"{r.file}#{r.handler}"
            out.append(
                Violation(
                    check="endpoint-without-auth",
                    message=(
                        f"{r.method} {r.path} changes data but has no auth marker on its "
                        "handler, router or middleware (CWE-306)"
                    ),
                    evidence=(r.evidence,),
                    module=module_of(r.file),
                    value=1,
                    minutes=check.minutes,
                    symbol=node_id if node_id in graph.nodes else None,
                )
            )
    return out, notes
