"""App units: one per directory holding an app manifest."""

from __future__ import annotations

from pathlib import Path

from svarupa.build import Graph
from svarupa.design.model import Unit
from svarupa.detect import Scan
from svarupa.extract.base import module_of

__all__ = ["find_units", "signals"]

_MANIFESTS = frozenset(
    {"pyproject.toml", "requirements.txt", "package.json", "go.mod", "pom.xml",
     "build.gradle", "build.gradle.kts"}
)  # fmt: skip
_CODE_SUFFIXES = frozenset(
    {
        ".py",
        ".pyi",
        ".ts",
        ".tsx",
        ".mts",
        ".cts",
        ".js",
        ".jsx",
        ".mjs",
        ".cjs",
        ".go",
        ".java",
    }
)
_DATA_DIRS = frozenset(
    {"dags", "pipelines", "etl", "marts", "staging", "bronze", "silver", "gold"}
)


def signals(graph: Graph) -> dict[str, frozenset[str]]:
    """Evidence per module: routes, datastore, messagebus, cloud, ui."""
    out: dict[str, set[str]] = {}
    for r in graph.routes:
        out.setdefault(module_of(r.file), set()).add("routes")
    names = {
        "database": "datastore",
        "messagebus": "messagebus",
        "cloud": "cloud",
        "frontend": "ui",
    }
    for x in graph.externals:
        if x.category in names:
            out.setdefault(module_of(x.file), set()).add(names[x.category])
    return {m: frozenset(s) for m, s in out.items()}


def _level(modules: tuple[str, ...], files: list[str], sig: dict[str, frozenset[str]]) -> str:
    found: set[str] = set()
    for m in modules:
        found |= sig.get(m, frozenset())
    if "ui" in found or any(f.endswith((".tsx", ".jsx")) for f in files):
        return "frontend"
    if found & {"routes", "datastore", "messagebus"}:
        return "backend"
    if any(part in _DATA_DIRS for m in modules for part in m.split("/")):
        return "data"
    if any(Path(f).name == "AndroidManifest.xml" for f in files):
        return "mobile"
    return "library"


def find_units(scan: Scan, graph: Graph) -> list[Unit]:
    manifests: dict[str, str] = {}
    for rec in scan.files:
        name = Path(rec.path).name
        if name in _MANIFESTS and rec.role.value == "config":
            parent = str(Path(rec.path).parent)
            manifests.setdefault("" if parent == "." else parent, rec.path)
    dirs = sorted(manifests, key=lambda d: (-d.count("/"), -len(d), d))

    def owner(path: str) -> str:
        for d in dirs:
            if not d or path == d or path.startswith(d + "/"):
                return d
        return ""

    # Only directories holding architecture source: the root of a repository
    # whose code lives in subdirectories is not a module of its own.
    with_code = {
        module_of(p) for p in graph.architecture_paths if Path(p).suffix in _CODE_SUFFIXES
    }
    members: dict[str, list[str]] = {}
    for module in sorted(m for m in graph.modules if m in with_code):
        members.setdefault(owner(module), []).append(module)
    files: dict[str, list[str]] = {}
    for rec in scan.files:
        files.setdefault(owner(rec.path), []).append(rec.path)
    sig = signals(graph)
    return [
        Unit(
            d,
            _level(tuple(members[d]), files.get(d, []), sig),
            manifests.get(d, ""),
            tuple(members[d]),
        )
        for d in sorted(members)
        if members[d]
    ]
