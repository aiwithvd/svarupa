"""Stage 3: assemble extracted facts into a validated graph.

This stage is where the product's central promise stops being a convention and
becomes an enforced invariant. Four contracts, each inherited from a review
finding rather than invented here:

1. **Evidence is re-validated independently.** The model checks it at
   construction, but `object.__new__` plus `__setattr__` can build an
   evidence-free node that survives pickling, and a subclass can override
   `__post_init__`. Python cannot prevent that, so constructor validation is a
   convenience and *this* is the enforcement point.
2. **Node ids are unique.** Two nodes claiming one id with different evidence
   is a graph-integrity violation, and downstream stages index by id.
3. **Merging unions evidence, never drops it.** Repeated `(src, dst, kind)`
   keys carry *different* lines: on one real repository, 0 of 135 repeated keys
   had identical evidence across copies. Deduping by key would silently discard
   provenance.
4. **Module identity is structural.** Directories, packages, and workspace
   members, never communities. Community detection is chaotically sensitive to
   input perturbation, and a pull request changes the input.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

import networkx as nx

from svarupa.detect import Scan, load_toml
from svarupa.diagnostics import Diagnostic, DiagnosticError, Severity
from svarupa.extract.base import ExtractResult, Scorecard
from svarupa.model import Edge, EdgeKind, MissingEvidenceError, Node

__all__ = ["Graph", "Module", "build", "workspace_members"]


@dataclass(frozen=True, slots=True)
class Module:
    """A structural module: the unit the lockfile is keyed on.

    Derived from what developers declare, so it changes only when a file
    actually moves. Never from clustering.
    """

    id: str
    package: str | None
    file_count: int

    @property
    def is_root(self) -> bool:
        return self.id == ""


@dataclass(frozen=True, slots=True)
class Graph:
    nodes: Mapping[str, Node]
    edges: tuple[Edge, ...]
    modules: Mapping[str, Module]
    module_deps: tuple[tuple[str, str], ...]
    scorecard: Scorecard
    diagnostics: tuple[Diagnostic, ...] = ()

    def nx(self, directed: bool = True) -> nx.DiGraph[str] | nx.Graph[str]:
        """A NetworkX view for the algorithms later stages need.

        Built on demand rather than stored: the typed view above is the source
        of truth, and keeping two mutable copies in sync is a bug factory.
        Insertion is in sorted order so any algorithm that happens to be
        order-sensitive still sees the same graph everywhere.
        """
        g: nx.DiGraph[str] | nx.Graph[str] = nx.DiGraph() if directed else nx.Graph()
        for nid in sorted(self.nodes):
            node = self.nodes[nid]
            g.add_node(nid, kind=node.kind.value, label=node.label, lang=node.lang)
        for e in self.edges:
            g.add_edge(e.src, e.dst, kind=e.kind.value, resolution=e.resolution.value)
        return g

    @property
    def errors(self) -> tuple[Diagnostic, ...]:
        return tuple(d for d in self.diagnostics if d.severity is Severity.ERROR)


def _verify_evidence(
    el: Node | Edge, subject: str, lines: Mapping[str, int] | None = None
) -> None:
    """The independent re-check.

    Deliberately duplicates the constructor's work. Trusting the model here
    would mean the invariant holds only for elements that went through
    `__init__`, which is not something Python can guarantee.

    When line counts are available it also verifies the range **exists in the
    file**. A well-formed range is not the same as a real one: evidence
    pointing at line 9999 of a ten-line file passes every structural check and
    still sends a reader nowhere, which is precisely the failure the product
    defines itself against.
    """
    if not el.evidence:
        raise MissingEvidenceError(type(el).__name__, subject, el.producer)
    for ev in el.evidence:
        if not ev.file or ev.start_line < 1 or ev.end_line < ev.start_line:
            raise DiagnosticError(
                Diagnostic(
                    code="SVA-B-002",
                    severity=Severity.ERROR,
                    message="evidence range is not a valid source location",
                    subject=subject,
                    location=str(ev),
                )
            )
        if lines is None:
            continue
        known = lines.get(ev.file)
        if known is None:
            raise DiagnosticError(
                Diagnostic(
                    code="SVA-B-005",
                    severity=Severity.ERROR,
                    message="evidence names a file that was never scanned",
                    subject=subject,
                    location=str(ev),
                )
            )
        if ev.end_line > known:
            raise DiagnosticError(
                Diagnostic(
                    code="SVA-B-006",
                    severity=Severity.ERROR,
                    message=(
                        f"evidence points past the end of the file "
                        f"({known} line{'s' if known != 1 else ''}); a reader "
                        "clicking this would land nowhere"
                    ),
                    subject=subject,
                    location=str(ev),
                    suggested_fixes=("This is an extractor bug; please report it.",),
                )
            )


def _merge_edges(edges: Iterable[Edge]) -> tuple[tuple[Edge, ...], list[Diagnostic]]:
    """Collapse repeated keys, unioning evidence.

    Two import lines to the same target are two facts sharing a key. Keeping
    one would discard a real source location, so the merged edge carries both.

    A key that arrives with conflicting *resolution* is a genuine conflict, not
    a merge: the more honest classification wins, because claiming RESOLVED
    when one producer said CANDIDATE would overstate certainty.
    """
    grouped: dict[tuple[str, str, str], list[Edge]] = {}
    for e in edges:
        grouped.setdefault(e.key, []).append(e)

    out: list[Edge] = []
    diags: list[Diagnostic] = []
    for key in sorted(grouped):
        group = grouped[key]
        if len(group) == 1:
            out.append(group[0])
            continue

        evidence = tuple(sorted({ev for e in group for ev in e.evidence}))
        resolutions = {e.resolution for e in group}
        # Prefer the least confident classification present.
        order = ["unresolved", "external", "candidate", "resolved"]
        chosen = min(resolutions, key=lambda r: order.index(r.value))
        base = next(e for e in group if e.resolution is chosen)

        if len(resolutions) > 1:
            diags.append(
                Diagnostic(
                    code="SVA-B-003",
                    severity=Severity.INFO,
                    message=(
                        "the same relationship was classified differently by "
                        f"different sites; keeping the least confident ({chosen.value})"
                    ),
                    subject=f"{key[0]} -> {key[1]} ({key[2]})",
                )
            )

        attrs = dict(base.attrs)
        attrs["sites"] = str(len(evidence))
        out.append(
            Edge(
                src=base.src,
                dst=base.dst,
                kind=base.kind,
                evidence=evidence,
                confidence=base.confidence,
                resolution=chosen,
                arity=max(e.arity for e in group),
                attrs=tuple(sorted(attrs.items())),
                producer=base.producer,
            )
        )
    return tuple(out), diags


def workspace_members(scan: Scan) -> tuple[str, ...]:
    """Expand workspace member globs against the real file set.

    `detect` finds the workspace *roots* because that only needs the manifest
    filenames. Members need the full file list to expand `packages/*`, which is
    why the design puts this here.

    The member, not the root, is the useful grouping: a root-level workspace
    declaration contains everything and says nothing.
    """
    # Every ancestor directory, not just immediate parents. A member glob
    # `packages/*` must match `packages/web` even when the only file under it
    # is `packages/web/src/a.ts`.
    dirs: set[str] = {""}
    for rec in scan.files:
        parts = rec.path.split("/")[:-1]
        for i in range(len(parts)):
            dirs.add("/".join(parts[: i + 1]))
    members: set[str] = set()

    for ws in scan.workspaces:
        base = ws.root
        for pattern in _member_patterns(scan, ws.manifest, ws.kind):
            prefix = f"{base}/{pattern}" if base else pattern
            prefix = prefix.rstrip("/")
            if prefix.endswith("/*"):
                stem = prefix[:-2]
                members.update(
                    d
                    for d in dirs
                    if d.startswith(stem + "/") and "/" not in d[len(stem) + 1 :]
                )
            elif prefix.endswith("/**"):
                stem = prefix[:-3]
                members.update(d for d in dirs if d.startswith(stem + "/"))
            elif prefix in dirs:
                members.add(prefix)
    return tuple(sorted(m for m in members if m))


def _member_patterns(scan: Scan, manifest: str, kind: str) -> list[str]:
    try:
        text = (scan.root / manifest).read_text(encoding="utf8", errors="replace")
    except OSError:
        return []

    if kind == "pnpm":
        try:
            import yaml

            loaded: object = yaml.safe_load(text)
        except Exception:
            return []
        if isinstance(loaded, dict):
            pkgs = cast("dict[str, object]", loaded).get("packages")
            if isinstance(pkgs, list):
                return [str(x) for x in cast("list[object]", pkgs)]
        return []

    if kind == "npm":
        try:
            data: object = json.loads(text)
        except json.JSONDecodeError:
            return []
        if not isinstance(data, dict):
            return []
        ws: object = cast("dict[str, object]", data).get("workspaces")
        if isinstance(ws, list):
            return [str(x) for x in cast("list[object]", ws)]
        if isinstance(ws, dict):
            pkgs = cast("dict[str, object]", ws).get("packages")
            if isinstance(pkgs, list):
                return [str(x) for x in cast("list[object]", pkgs)]
        return []

    if kind == "cargo":
        toml = load_toml(text)
        section = toml.get("workspace") if toml else None
        if isinstance(section, dict):
            mem = cast("dict[str, object]", section).get("members")
            if isinstance(mem, list):
                return [str(x) for x in cast("list[object]", mem)]
        return []

    if kind == "uv":
        toml = load_toml(text)
        section = toml.get("tool") if toml else None
        if isinstance(section, dict):
            uv = cast("dict[str, object]", section).get("uv")
            if isinstance(uv, dict):
                wsx = cast("dict[str, object]", uv).get("workspace")
                if isinstance(wsx, dict):
                    mem = cast("dict[str, object]", wsx).get("members")
                    if isinstance(mem, list):
                        return [str(x) for x in cast("list[object]", mem)]
        return []

    if kind == "go":
        out: list[str] = []
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("use "):
                out.append(stripped[4:].strip().strip("()").lstrip("./"))
        return [x for x in out if x]

    return []


def _module_of(path: str) -> str:
    parent = str(Path(path).parent)
    return "" if parent == "." else parent


def _package_of(path: str, packages: Sequence[str]) -> str | None:
    """The longest workspace member that contains this file."""
    best: str | None = None
    for pkg in packages:
        contains = pkg and (path == pkg or path.startswith(pkg + "/"))
        if contains and (best is None or len(pkg) > len(best)):
            best = pkg
    return best


def _d_nodes() -> dict[str, Node]:
    return {}


def _l_diags() -> list[Diagnostic]:
    return []


@dataclass
class _Acc:
    nodes: dict[str, Node] = field(default_factory=_d_nodes)
    diagnostics: list[Diagnostic] = field(default_factory=_l_diags)


def build(scan: Scan, extracted: ExtractResult, strict: bool = True) -> Graph:
    """Assemble and validate.

    `strict` raises on the first integrity error. Set it False to collect every
    problem in one pass, which is what a report wants and what a build gate
    does not.
    """
    acc = _Acc()
    lines = {rec.path: rec.line_count for rec in scan.files}

    # --- nodes: unique ids, evidence re-verified -------------------------
    for node in extracted.nodes:
        _verify_evidence(node, node.id, lines)
        existing = acc.nodes.get(node.id)
        if existing is None:
            acc.nodes[node.id] = node
            continue
        if existing == node:
            continue
        diag = Diagnostic(
            code="SVA-B-001",
            severity=Severity.ERROR,
            message=(
                "two different nodes claim the same id; downstream stages index "
                "by id, so one would silently shadow the other"
            ),
            subject=node.id,
            location=f"{existing.evidence[0]} and {node.evidence[0]}",
            suggested_fixes=("This is an extractor bug; please report it.",),
        )
        if strict:
            raise DiagnosticError(diag)
        acc.diagnostics.append(diag)

    # --- edges: endpoints must exist, evidence re-verified ---------------
    kept: list[Edge] = []
    for edge in extracted.edges:
        _verify_evidence(edge, f"{edge.src} -> {edge.dst}", lines)
        missing = [end for end in (edge.src, edge.dst) if end not in acc.nodes]
        if missing:
            diag = Diagnostic(
                code="SVA-B-004",
                severity=Severity.ERROR,
                message=(
                    "edge endpoint does not exist in the graph; a dangling "
                    "endpoint is a missing-node bug wearing a graph-shaped mask"
                ),
                subject=f"{edge.src} -> {edge.dst}",
                location=", ".join(missing),
            )
            if strict:
                raise DiagnosticError(diag)
            acc.diagnostics.append(diag)
            continue
        kept.append(edge)

    merged, merge_diags = _merge_edges(kept)
    acc.diagnostics.extend(merge_diags)

    # --- structural modules ----------------------------------------------
    packages = list(workspace_members(scan))
    counts: dict[str, int] = {}
    pkg_of: dict[str, str | None] = {}
    for rec in scan.architecture_files:
        mid = _module_of(rec.path)
        counts[mid] = counts.get(mid, 0) + 1
        pkg_of.setdefault(mid, _package_of(rec.path, packages))

    modules = {
        mid: Module(id=mid, package=pkg_of.get(mid), file_count=n)
        for mid, n in sorted(counts.items())
    }

    # --- module-level aggregation ----------------------------------------
    # Only architecture-eligible files contribute. Test, generated and vendored
    # code is 30-50% of a typical repo and imports everything, which would bury
    # the real structure it is meant to reveal.
    eligible = {rec.path for rec in scan.architecture_files}
    deps: set[tuple[str, str]] = set()
    for e in merged:
        if e.kind is not EdgeKind.IMPORTS:
            continue
        if e.src not in eligible or e.dst not in eligible:
            continue
        a, b = _module_of(e.src), _module_of(e.dst)
        if a != b:
            deps.add((a, b))

    return Graph(
        nodes=dict(sorted(acc.nodes.items())),
        edges=merged,
        modules=modules,
        module_deps=tuple(sorted(deps)),
        scorecard=extracted.scorecard,
        diagnostics=tuple(acc.diagnostics) + extracted.diagnostics,
    )


def module_records(graph: Graph) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """Modules and their dependencies, in lockfile order."""
    out: dict[str, set[str]] = {mid: set() for mid in graph.modules}
    for a, b in graph.module_deps:
        out.setdefault(a, set()).add(b)
    return tuple((mid, tuple(sorted(deps))) for mid, deps in sorted(out.items()))


def unresolved_summary(graph: Graph) -> str:
    """What the report surfaces: the honest gaps, not the flattering total."""
    lines = [graph.scorecard.render()]
    for (lang, kind), samples in sorted(graph.scorecard.samples.items()):
        if samples:
            lines.append(f"  {lang}/{kind} unresolved e.g. " + ", ".join(samples[:5]))
    return "\n".join(lines)
