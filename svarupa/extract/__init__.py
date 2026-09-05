"""Stage 2: extract facts from source, then resolve them across files."""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from dataclasses import replace
from pathlib import Path
from typing import cast

from svarupa.detect import Scan, load_toml
from svarupa.diagnostics import Diagnostic, Severity
from svarupa.extract.base import (
    CallShape,
    CallSite,
    Extractor,
    ExtractResult,
    FileFacts,
    ImportRef,
    Scorecard,
    SymbolRef,
    node_id,
)
from svarupa.extract.compose import extract_compose
from svarupa.extract.python import PythonExtractor
from svarupa.extract.resolve import Resolver, resolve
from svarupa.extract.typescript import TypeScriptExtractor
from svarupa.tsconfig import load_aliases

__all__ = [
    "CallShape",
    "CallSite",
    "ExtractResult",
    "Extractor",
    "FileFacts",
    "ImportRef",
    "PythonExtractor",
    "Resolver",
    "Scorecard",
    "SymbolRef",
    "extract",
    "node_id",
    "resolve",
]

_EXTRACTORS: dict[str, Extractor] = {
    "python": PythonExtractor(),
    "typescript": TypeScriptExtractor(),
    # .js/.jsx parse fine with the TypeScript grammar, which is a superset.
    "javascript": TypeScriptExtractor(),
}

GRAMMAR_VERSIONS: dict[str, str] = {
    lang: ex.grammar_version for lang, ex in _EXTRACTORS.items()
}


def extract(scan: Scan, declared_deps: frozenset[str] = frozenset()) -> ExtractResult:
    """Run pass 1 per file, then pass 2 globally.

    Pass 2 is never incremental: changing one file invalidates edges attributed
    to another, so resolution always sees the whole symbol table.
    """
    facts: list[FileFacts] = []
    crashes: list[Diagnostic] = []
    for rec in scan.files:
        ex = _EXTRACTORS.get(rec.lang or "")
        if ex is None:
            continue
        try:
            data = (scan.root / rec.path).read_bytes()
        except OSError:
            continue
        try:
            facts.append(ex.parse(rec.path, data))
        except Exception as exc:
            # The `try` used to cover only `read_bytes`, so any parser failure
            # escaped and took the whole analysis with it. Demonstrated on a
            # real 10,403-file repository: one 16 KB minified bundle raised
            # RecursionError and nothing else got analyzed.
            #
            # This is the stage with the most hostile input in the system, so
            # it is also where "partial failure must degrade, not disable"
            # matters most. Broad on purpose: the point is that no extractor
            # bug, present or future, can cost more than its own file.
            crashes.append(
                Diagnostic(
                    code="SVA-X-004",
                    severity=Severity.WARNING,
                    message=(
                        f"extractor raised {type(exc).__name__}, so this file "
                        "contributed no facts"
                    ),
                    subject=rec.path,
                    location=rec.path,
                )
            )

    # Workspace roots discovered by `detect` are import roots. Passing them
    # through is the integration that was missing: the resolver otherwise
    # guesses at layout from the tree alone.
    roots = [w.root for w in scan.workspaces if w.root]
    result = resolve(
        facts, declared_deps, roots, load_aliases(scan.root), workspace_packages(scan)
    )

    # Configuration is architecture too. Compose services, datastores and
    # queues join the same graph as code, with the same evidence rule: every
    # node cites the line in the file that declares it.
    compose = extract_compose(scan)
    return replace(
        result,
        nodes=result.nodes + compose.nodes,
        edges=result.edges + compose.edges,
        diagnostics=result.diagnostics + compose.diagnostics + tuple(crashes),
    )


def workspace_packages(scan: Scan) -> tuple[tuple[str, str], ...]:
    """Map each package.json `name` to the directory that declares it.

    A monorepo publishes `packages/zod` as the package `zod`, so `zod/v4` is an
    intra-repo import that no tsconfig alias covers. Without this it lands in
    the unresolved bin despite being right there in the tree.
    """
    from svarupa.build import workspace_members

    # Only declared members may claim a package name. Every package.json in the
    # tree used to qualify, so an `examples/fake/package.json` naming itself
    # "express" captured a real `import express` as an intra-repo edge.
    # A package.json may claim a name only if it is a declared workspace member
    # or the repository's own root manifest. Any nested manifest used to
    # qualify, so an `examples/fake/package.json` naming itself "express"
    # captured a real `import express` as an intra-repo edge.
    #
    # No `if members:` escape hatch: a workspace that declares members which do
    # not exist yet should claim nothing, not everything.
    allowed = set(workspace_members(scan)) | {""}
    out: dict[str, str] = {}
    for rec in scan.files:
        if Path(rec.path).name != "package.json":
            continue
        holder = str(Path(rec.path).parent)
        holder = "" if holder == "." else holder
        if holder not in allowed:
            continue
        try:
            loaded: object = json.loads(
                (scan.root / rec.path).read_text(encoding="utf8", errors="replace")
            )
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(loaded, dict):
            continue
        name = cast("dict[str, object]", loaded).get("name")
        if isinstance(name, str) and name:
            out.setdefault(name, holder)
    return tuple(sorted(out.items()))


def _norm_dep(raw: str) -> str | None:
    """PEP 508 requirement string -> importable top-level name, roughly.

    Approximate by design: the distribution name and the import name differ
    often enough (`PyYAML` imports as `yaml`) that this can never be exact
    without installing the package. Being slightly over-inclusive is the safe
    direction here, since a false "external" only understates the scorecard's
    unresolved bin, whereas a false "unresolved" would cry wolf.
    """
    token = raw.strip()
    for sep in (";", "[", " ", "=", ">", "<", "!", "~", "@"):
        token = token.split(sep)[0]
    token = token.strip().replace("-", "_")
    return token if token.isidentifier() else None


def declared_dependencies(scan: Scan) -> frozenset[str]:
    """Third-party names the project declares.

    Used to tell a genuine external import from a resolver failure. Without it
    the scorecard has only two bins and launders its own bugs.

    Manifests are **parsed**, never grepped. A line-based reader on
    `dependencies = ["requests"]` extracts `dependencies`, which is exactly the
    class of error the decision log records for workspace detection.
    `requirements.txt` is the one genuine line format, so it is read as one.
    """
    names: set[str] = set()

    for rec in scan.files:
        try:
            text = (scan.root / rec.path).read_text(encoding="utf8", errors="replace")
        except OSError:
            continue
        name = Path(rec.path).name

        if name == "requirements.txt":
            for line in text.splitlines():
                line = line.split("#")[0].strip()
                if line and not line.startswith("-") and (n := _norm_dep(line)):
                    names.add(n)

        elif name == "pyproject.toml":
            data = load_toml(text)
            if data is None:
                continue
            for raw in _iter_pep508(data):
                if (n := _norm_dep(raw)) is not None:
                    names.add(n)

        elif name == "package.json":
            try:
                loaded: object = json.loads(text)
            except json.JSONDecodeError:
                continue
            if not isinstance(loaded, dict):
                continue
            pkg = cast("dict[str, object]", loaded)
            for key in ("dependencies", "devDependencies", "peerDependencies"):
                block: object = pkg.get(key)
                if isinstance(block, dict):
                    names.update(str(k) for k in cast("dict[str, object]", block))

    return frozenset(names)


def _iter_pep508(data: Mapping[str, object]) -> Iterator[str]:
    """Yield requirement strings from every place a Python project puts them."""
    project = data.get("project")
    if isinstance(project, dict):
        proj = cast("dict[str, object]", project)
        deps = proj.get("dependencies")
        if isinstance(deps, list):
            yield from (str(d) for d in cast("list[object]", deps))
        optional = proj.get("optional-dependencies")
        if isinstance(optional, dict):
            for group in cast("dict[str, object]", optional).values():
                if isinstance(group, list):
                    yield from (str(d) for d in cast("list[object]", group))

    # PEP 735. Without this the tool cries wolf about its own build: svarupa's
    # `pytest` is declared right here and still landed in the unresolved bin.
    groups = data.get("dependency-groups")
    if isinstance(groups, dict):
        for group in cast("dict[str, object]", groups).values():
            if isinstance(group, list):
                for item in cast("list[object]", group):
                    if isinstance(item, str):
                        yield item

    tool = data.get("tool")
    if not isinstance(tool, dict):
        return
    poetry = cast("dict[str, object]", tool).get("poetry")
    if isinstance(poetry, dict):
        pdeps = cast("dict[str, object]", poetry).get("dependencies")
        if isinstance(pdeps, dict):
            yield from (str(k) for k in cast("dict[str, object]", pdeps) if k != "python")
