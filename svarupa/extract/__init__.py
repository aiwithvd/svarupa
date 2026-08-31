"""Stage 2: extract facts from source, then resolve them across files."""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import cast

from svarupa.detect import Scan, load_toml
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
from svarupa.extract.python import PythonExtractor
from svarupa.extract.resolve import Resolver, resolve

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

_EXTRACTORS: dict[str, Extractor] = {"python": PythonExtractor()}

GRAMMAR_VERSIONS: dict[str, str] = {
    lang: ex.grammar_version for lang, ex in _EXTRACTORS.items()
}


def extract(scan: Scan, declared_deps: frozenset[str] = frozenset()) -> ExtractResult:
    """Run pass 1 per file, then pass 2 globally.

    Pass 2 is never incremental: changing one file invalidates edges attributed
    to another, so resolution always sees the whole symbol table.
    """
    facts: list[FileFacts] = []
    for rec in scan.files:
        ex = _EXTRACTORS.get(rec.lang or "")
        if ex is None:
            continue
        try:
            data = (scan.root / rec.path).read_bytes()
        except OSError:
            continue
        facts.append(ex.parse(rec.path, data))

    # Workspace roots discovered by `detect` are import roots. Passing them
    # through is the integration that was missing: the resolver otherwise
    # guesses at layout from the tree alone.
    roots = [w.root for w in scan.workspaces if w.root]
    return resolve(facts, declared_deps, roots)


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

    tool = data.get("tool")
    if not isinstance(tool, dict):
        return
    poetry = cast("dict[str, object]", tool).get("poetry")
    if isinstance(poetry, dict):
        pdeps = cast("dict[str, object]", poetry).get("dependencies")
        if isinstance(pdeps, dict):
            yield from (str(k) for k in cast("dict[str, object]", pdeps) if k != "python")
