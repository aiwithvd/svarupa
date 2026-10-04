"""Stage 2: extract facts from source, then resolve them across files."""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from collections.abc import Iterator, Mapping
from dataclasses import replace
from pathlib import Path
from typing import cast

from svarupa.detect import Scan, load_toml, read_bytes, read_text
from svarupa.diagnostics import Diagnostic, Severity
from svarupa.extract.base import (
    CallShape,
    CallSite,
    EnvironmentFact,
    Extractor,
    ExtractResult,
    FileFacts,
    ImportRef,
    Scorecard,
    SymbolRef,
    node_id,
)
from svarupa.extract.compose import extract_compose
from svarupa.extract.environments import extract_environments
from svarupa.extract.packs import ANALYZED_ELSEWHERE, BY_DETECTED, load_extractors
from svarupa.extract.packs.modules import ModuleContext, ModuleResolver
from svarupa.extract.resolve import Resolver, resolve
from svarupa.extract.semantics import semantics
from svarupa.tsconfig import load_aliases

__all__ = [
    "CallShape",
    "CallSite",
    "EnvironmentFact",
    "ExtractResult",
    "Extractor",
    "FileFacts",
    "ImportRef",
    "Resolver",
    "Scorecard",
    "SymbolRef",
    "extract",
    "node_id",
    "resolve",
]

_PACK_EXTRACTORS, _UNAVAILABLE = load_extractors()

_EXTRACTORS: dict[str, Extractor] = dict(_PACK_EXTRACTORS)

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
    unanalyzed: dict[str, int] = {}
    for rec in scan.files:
        ex = _EXTRACTORS.get(rec.lang or "")
        if ex is None:
            if rec.lang and rec.lang not in ANALYZED_ELSEWHERE:
                unanalyzed[rec.lang] = unanalyzed.get(rec.lang, 0) + 1
            continue
        try:
            data = read_bytes(scan.root, rec.path)
        except OSError as exc:
            # Never a silent skip: this `continue` alone is how Linux lost
            # every NFD-named file while the run reported success.
            crashes.append(
                Diagnostic(
                    code="SVA-X-011",
                    severity=Severity.WARNING,
                    message=(
                        f"a scanned file could not be read for extraction "
                        f"({type(exc).__name__}), so it contributed no facts"
                    ),
                    subject=rec.path,
                    location=rec.path,
                )
            )
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

    # One line per language, never one per file: a Go service with 900 files
    # is one fact ("Go is not analyzed"), and 900 warnings would bury it.
    for lang, count in sorted(unanalyzed.items()):
        reason = _UNAVAILABLE.get(lang, f"no language pack exists for {lang} yet")
        crashes.append(
            Diagnostic(
                code="SVA-X-012",
                severity=Severity.WARNING,
                message=(
                    f"{count} {lang} files were detected but not analyzed: {reason}; "
                    "they appear in no diagram"
                ),
                subject=lang,
            )
        )

    # Workspace roots discovered by `detect` are import roots. Passing them
    # through is the integration that was missing: the resolver otherwise
    # guesses at layout from the tree alone.
    roots = [w.root for w in scan.workspaces if w.root]
    context = ModuleContext(
        files=frozenset(f.path for f in facts),
        facts=tuple(facts),
        deps=declared_deps,
        go_modules=go_modules(scan),
    )
    modules: dict[str, ModuleResolver] = {
        lang: pack.modules(context)
        for lang, pack in sorted(BY_DETECTED.items())
        if pack.modules is not None and lang in _EXTRACTORS
    }
    resolver = Resolver(
        facts,
        declared_deps,
        roots,
        load_aliases(scan.root),
        workspace_packages(scan),
        modules,
    )
    result = resolver.run()

    # Configuration is architecture too. Compose services, datastores and
    # queues join the same graph as code, with the same evidence rule: every
    # node cites the line in the file that declares it.
    compose = extract_compose(scan)

    # Semantic facts: routes, tasks, declared entrypoints. Import-gated and
    # line-cited, the same rule as everything above.
    sem = semantics(scan, facts, resolver.resolve_module)

    # Environments: declared deploy targets from config filenames, profile
    # manifests, CI workflows and Dockerfiles. No evidence, no environment.
    env = extract_environments(scan)
    return replace(
        result,
        nodes=result.nodes + compose.nodes,
        edges=result.edges + compose.edges,
        diagnostics=(
            result.diagnostics
            + compose.diagnostics
            + sem.diagnostics
            + env.diagnostics
            + tuple(crashes)
        ),
        routes=sem.routes,
        tasks=sem.tasks,
        entrypoints=sem.entrypoints,
        externals=sem.externals,
        environments=env.facts,
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
            loaded: object = json.loads(read_text(scan.root, rec.path))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(loaded, dict):
            continue
        name = cast("dict[str, object]", loaded).get("name")
        if isinstance(name, str) and name:
            out.setdefault(name, holder)
    return tuple(sorted(out.items()))


def _go_mod(text: str) -> tuple[str | None, list[str]]:
    """The `module` path and the `require`d module paths of a go.mod.

    go.mod is a line format by specification (like requirements.txt), so it
    is read line by line; `//` starts a comment.
    """
    module: str | None = None
    required: list[str] = []
    in_block = False
    for raw in text.splitlines():
        line = raw.split("//", 1)[0].strip()
        if not line:
            continue
        if in_block:
            if line == ")":
                in_block = False
            else:
                required.append(line.split()[0])
            continue
        words = line.split()
        if words[0] == "module" and len(words) > 1:
            module = words[1].strip('"')
        elif words[0] == "require" and len(words) > 1:
            if words[1] == "(":
                in_block = True
            else:
                required.append(words[1])
    return module, required


def _jvm_group(group: str) -> str | None:
    """A Maven groupId, trimmed to its first two segments.

    `org.springframework.boot` publishes packages under `org.springframework`;
    the trim is over-inclusive on purpose, the safe direction for the same
    reason `_norm_dep` gives for Python distribution names. Placeholders like
    `${project.groupId}` name nothing.
    """
    group = group.strip()
    if not group or "$" in group or "{" in group:
        return None
    return ".".join(group.split(".")[:2])


def _maven_groups(text: str) -> set[str]:
    """`dependency/groupId` values from a pom.xml.

    Parsed, never grepped. A pom never needs a DTD or entities, so a document
    that declares one is refused before parsing: that closes entity-expansion
    and external-entity attacks without a new dependency.
    """
    if "<!DOCTYPE" in text or "<!ENTITY" in text:
        return set()
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return set()
    out: set[str] = set()
    for el in root.iter():
        if el.tag.rsplit("}", 1)[-1] != "dependency":
            continue
        for child in el:
            if (
                child.tag.rsplit("}", 1)[-1] == "groupId"
                and child.text
                and (g := _jvm_group(child.text)) is not None
            ):
                out.add(g)
    return out


# `group:artifact` or `group:artifact:version` inside a quoted string.
_GRADLE_COORD = re.compile(r"""["']([A-Za-z0-9_.\-]+):([A-Za-z0-9_.\-]+)(?::[^"']*)?["']""")


def _gradle_groups(text: str) -> set[str]:
    """Coordinate strings from a build.gradle(.kts).

    Gradle build files are programs, not data, so only literal coordinate
    strings are read. Anything computed is missed, which leaves an import
    unresolved rather than inventing an external one.
    """
    return {g for m in _GRADLE_COORD.finditer(text) if (g := _jvm_group(m.group(1)))}


def _catalog_groups(text: str) -> set[str]:
    """`[libraries]` entries of a Gradle version catalog (libs.versions.toml)."""
    data = load_toml(text)
    libraries = data.get("libraries") if data else None
    if not isinstance(libraries, dict):
        return set()
    out: set[str] = set()
    for value in cast("dict[str, object]", libraries).values():
        coordinate: object = value
        if isinstance(value, dict):
            entry = cast("dict[str, object]", value)
            coordinate = entry.get("module") or entry.get("group")
        if isinstance(coordinate, str) and (g := _jvm_group(coordinate.split(":")[0])):
            out.add(g)
    return out


def go_modules(scan: Scan) -> tuple[tuple[str, str], ...]:
    """Map each go.mod `module` path to the directory that declares it."""
    out: dict[str, str] = {}
    for rec in scan.files:
        if Path(rec.path).name != "go.mod":
            continue
        try:
            module, _ = _go_mod(read_text(scan.root, rec.path))
        except OSError:
            continue
        if module:
            holder = str(Path(rec.path).parent)
            out.setdefault(module, "" if holder == "." else holder)
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
            text = read_text(scan.root, rec.path)
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

        elif name == "go.mod":
            names.update(_go_mod(text)[1])
        elif name == "pom.xml":
            names.update(_maven_groups(text))
        elif name in ("build.gradle", "build.gradle.kts"):
            names.update(_gradle_groups(text))
        elif name == "libs.versions.toml":
            names.update(_catalog_groups(text))

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
