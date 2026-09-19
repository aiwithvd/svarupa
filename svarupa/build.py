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
import posixpath
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import cast

import networkx as nx

from svarupa.detect import Scan, load_toml
from svarupa.diagnostics import Diagnostic, DiagnosticError, Severity
from svarupa.extract.base import (
    EntrypointFact,
    EnvironmentFact,
    ExternalFact,
    ExtractResult,
    RouteFact,
    Scorecard,
    TaskFact,
)
from svarupa.model import Edge, EdgeKind, Evidence, MissingEvidenceError, Node, NodeKind

__all__ = [
    "Graph",
    "Module",
    "build",
    "build_context_of",
    "entrypoint_module",
    "module_of",
    "module_roles",
    "modules_under",
    "workspace_members",
]


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
    # Files eligible to shape the architecture picture. The graph deliberately
    # contains test, generated and vendored code so a user can still ask about
    # it, which means every consumer that draws architecture has to know which
    # files may stand for a module. Without this, a box for `src/gateway` cited
    # `access-control.test.ts` simply because it sorted first.
    architecture_paths: frozenset[str] = frozenset()
    # What the scanner saw, per language. Carried because a deriver otherwise
    # cannot tell "this repository has no SQL" from "nothing extracts SQL yet":
    # with no extractor, no nodes exist either way, and those are different
    # facts -- only one of them is about the user's codebase.
    file_languages: tuple[tuple[str, int], ...] = ()
    # Semantic facts (routes, tasks, declared entrypoints, environments),
    # evidence re-verified on the way in like every node and edge.
    routes: tuple[RouteFact, ...] = ()
    tasks: tuple[TaskFact, ...] = ()
    entrypoints: tuple[EntrypointFact, ...] = ()
    externals: tuple[ExternalFact, ...] = ()
    environments: tuple[EnvironmentFact, ...] = ()

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
    el: Node | Edge,
    subject: str,
    lines: Mapping[str, int] | None = None,
    strict: bool = True,
    sink: list[Diagnostic] | None = None,
) -> bool:
    """Returns True if the element is usable.

    In non-strict mode a problem is appended to `sink` and the element is
    dropped, rather than aborting the run. One malformed extractor emission
    should not disable an entire report.
    """
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

    def fail(diag: Diagnostic) -> bool:
        if strict:
            raise DiagnosticError(diag)
        if sink is not None:
            sink.append(diag)
        return False

    if not el.evidence:
        if strict:
            raise MissingEvidenceError(type(el).__name__, subject, el.producer)
        return fail(
            Diagnostic(
                code="SVA-B-007",
                severity=Severity.ERROR,
                message="element has no evidence",
                subject=subject,
                location=el.producer,
            )
        )
    for ev in el.evidence:
        whole_file = ev.start_line == 0 and ev.end_line == 0
        if whole_file and (lines is None or lines.get(ev.file) == 0):
            continue  # an empty file, cited as itself
        if not ev.file or ev.start_line < 1 or ev.end_line < ev.start_line:
            return fail(
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
            return fail(
                Diagnostic(
                    code="SVA-B-005",
                    severity=Severity.ERROR,
                    message="evidence names a file that was never scanned",
                    subject=subject,
                    location=str(ev),
                )
            )
        if ev.end_line > known:
            return fail(
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
    return True


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
        # Deterministic base. Taking the first matching edge made the merged
        # attrs, producer and confidence depend on extractor emission order,
        # which is an implementation detail feeding a byte-identical lockfile.
        candidates = sorted(
            (e for e in group if e.resolution is chosen),
            key=lambda e: (e.producer or "", e.confidence.value, e.attrs),
        )
        base = candidates[0]

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

        # Attributes whose meaning is per-relationship need an explicit rule,
        # not a copy from whichever edge happened to be base. `type_only` is
        # the sharp case: one runtime site makes the whole relationship a
        # runtime dependency, and mislabelling it would make impact analysis
        # silently drop a real edge.
        attrs: dict[str, str] = {}
        for key in sorted({k for e in group for k, _ in e.attrs}):
            values = {e.attr(key) for e in group}
            values.discard(None)
            if key == "type_only":
                runtime = any(e.attr("type_only") != "true" for e in group)
                if not runtime:
                    attrs[key] = "true"
            elif len(values) == 1:
                attrs[key] = next(iter(values))  # type: ignore[arg-type]
            else:
                attrs[key] = ",".join(sorted(v for v in values if v))
        attrs["sites"] = str(len(evidence))
        try:
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
        except (MissingEvidenceError, ValueError) as exc:
            # Fail-closed means the element is not emitted. It does not mean
            # the run dies: a `MissingEvidenceError` escaping `build` cost an
            # entire 10,403-file analysis over one edge in one minified
            # bundle, which is the disabling that "partial failure must
            # degrade" exists to prevent.
            #
            # The contract itself is right and is not relaxed. What triggers
            # it here is a self-call: a candidate edge whose call site and
            # definition site are the *same* line, so deduplicating evidence
            # into a set leaves one citation where the contract wants two.
            # Dropping is the honest outcome, because a candidate a reader
            # cannot see two sides of is exactly what the contract forbids,
            # and an intra-file edge is discarded by module aggregation
            # anyway.
            diags.append(
                Diagnostic(
                    code="SVA-B-008",
                    severity=Severity.WARNING,
                    message=(
                        f"merged edge violated its own contract and was dropped "
                        f"({type(exc).__name__}); "
                        f"{len(group)} site(s) collapsed to {len(evidence)} citation(s)"
                    ),
                    subject=f"{key[0]} -> {key[1]} ({key[2]})",
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
        return _go_work_uses(text)

    return []


def _go_work_uses(text: str) -> list[str]:
    """Parse `use` directives, single-line and block form.

    Two bugs lived here. `lstrip("./")` is a character-set operation applied to
    a path, so `use ../shared` was re-anchored onto any in-repo directory named
    `shared` -- the exact class already promoted to the decision log after the
    TypeScript alias targets, reintroduced one component later. And only
    single-line `use ./x` was read, so the standard multi-module block form
    returned nothing at all.

    Targets that escape the repository are dropped rather than re-anchored: a
    workspace member is structural module identity, and a wrong module boundary
    is worse than a missing one.
    """
    out: list[str] = []
    in_block = False
    for raw in text.splitlines():
        line = raw.split("//")[0].strip()
        if not line:
            continue
        if in_block:
            if line.startswith(")"):
                in_block = False
                continue
            out.append(line)
            continue
        if line == "use (" or line.startswith("use ("):
            in_block = True
            rest = line[len("use (") :].strip()
            if rest and rest != ")":
                out.append(rest)
            continue
        if line.startswith("use "):
            out.append(line[4:].strip())

    cleaned: list[str] = []
    for target in out:
        norm = posixpath.normpath(target.strip().strip('"'))
        if norm.startswith("..") or norm.startswith("/"):
            continue  # escapes the repo; never re-anchor onto a same-named decoy
        if norm in (".", ""):
            continue
        cleaned.append(norm)
    return cleaned


def _module_of(path: str) -> str:
    parent = str(Path(path).parent)
    return "" if parent == "." else parent


# Public alias: the lockfile builder and derivers map fact files to the same
# structural module identity this stage commits to, and a re-implementation
# there would be a second definition of the most important id in the system.
module_of = _module_of


def entrypoint_module(graph: Graph, target: str, lang: str, manifest: str) -> str | None:
    """The structural module an entrypoint target lands in, or None.

    Resolved against the graph's own module nodes (file paths), never by
    trusting the string: `pkg.mod:fn` names a module only if `pkg/mod.py` or
    `pkg/mod/__init__.py` was actually extracted, and a `bin` path only if the
    file it points at exists in the graph. An unresolvable target produces no
    claim, and the consumer diagnoses it.
    """
    folder = manifest.rsplit("/", 1)[0] if "/" in manifest else ""
    if lang == "python":
        # Anchored at the declaring manifest's directory, never at the repo
        # root: `packages/a/pyproject.toml` declaring `pkg.cli:main` names
        # `packages/a/pkg/cli.py`, and resolving from the root let a decoy
        # `pkg/` at the top level capture the record silently. `src/` is the
        # one extra anchor, because the src layout puts the package one level
        # below the manifest that declares it.
        base = target.split(":", 1)[0].strip().replace(".", "/")
        candidates = [
            posixpath.normpath(posixpath.join(folder, rel))
            for rel in (
                f"{base}.py",
                f"{base}/__init__.py",
                f"src/{base}.py",
                f"src/{base}/__init__.py",
            )
        ]
    else:
        candidates = [posixpath.normpath(posixpath.join(folder, target))]
    for cand in candidates:
        if cand in graph.nodes:
            return _module_of(cand)
    return None


def build_context_of(graph: Graph, service_id: str) -> str | None:
    """The repository-relative directory a compose service builds, or None.

    Anchored at the compose file's own directory and normalised with path
    operations, never string-stripped: `lstrip("./")` turned `./.web` into
    `web` and `../api` into `api`, and a compose file in `deploy/` with
    `context: ./api` means `deploy/api`, not the root's `api`. A context that
    escapes the repository (`../api`) is None: nothing in the tree is what it
    builds. The root context is "".
    """
    node = graph.nodes.get(service_id)
    if node is None:
        return None
    raw = node.attr("build_context")
    if not raw:
        return None
    compose_file = service_id.split("#", 1)[0]
    folder = posixpath.dirname(compose_file)
    joined = posixpath.normpath(posixpath.join(folder, raw.replace("\\", "/")))
    if joined.startswith("..") or posixpath.isabs(joined):
        return None
    return "" if joined == "." else joined


def modules_under(graph: Graph, context: str) -> set[str]:
    """Structural modules inside a build context ("" is every module)."""
    return {
        m for m in graph.modules if context == "" or m == context or m.startswith(context + "/")
    }


def modules_shipped(
    graph: Graph, service_id: str
) -> tuple[set[str], dict[str, Evidence]] | None:
    """The modules a built service's image holds, each with the Dockerfile
    COPY/ADD line that puts it there; None for a service that builds nothing
    in the tree.

    From the Dockerfile's sources when the extractor read one (review #21 N1:
    the build context says where a build may read, the Dockerfile says what
    the image holds), else every module under the context. A copied file
    stands for its module only when it is a source file the graph knows.
    """
    ctx = build_context_of(graph, service_id)
    if ctx is None:
        return None
    node = graph.nodes.get(service_id)
    ships = node.attr("ships") if node is not None else None
    dockerfile = node.attr("dockerfile") if node is not None else None
    if ships is None or dockerfile is None:
        return modules_under(graph, ctx), {}
    out: set[str] = set()
    where: dict[str, Evidence] = {}
    for entry in ships.split("\n"):
        if not entry:
            continue
        src, _, line = entry.rpartition(":")
        ev = Evidence(file=dockerfile, start_line=int(line), end_line=int(line))
        if src in graph.nodes and graph.nodes[src].kind is NodeKind.MODULE:
            mods = {module_of(src)}
        else:
            mods = modules_under(graph, src)
        for m in sorted(mods):
            out.add(m)
            where.setdefault(m, ev)
    return out, where


def module_roles(graph: Graph) -> dict[str, tuple[str, ...]]:
    """Evidence-backed roles per structural module, sorted both ways.

    `api` from routes, `worker` from tasks, `cli` from a declared entrypoint
    whose target resolves into the module, `auth` and `frontend` from imports
    the vocabulary knows. Gated on architecture eligibility,
    so a route declared in a test file assigns nothing. One definition, used
    by both the lockfile and the diagrams, because two implementations of
    "what is this module's role" would eventually disagree.
    """
    roles: dict[str, set[str]] = {}
    for r in graph.routes:
        if r.file in graph.architecture_paths:
            roles.setdefault(_module_of(r.file), set()).add("api")
    for t in graph.tasks:
        if t.file in graph.architecture_paths:
            roles.setdefault(_module_of(t.file), set()).add("worker")
    for e in graph.entrypoints:
        target = entrypoint_module(graph, e.target, e.lang, e.file)
        if target is not None:
            roles.setdefault(target, set()).add("cli")
    # Roles an import gives away: a module importing an auth library takes
    # `auth`, one importing a UI framework takes `frontend`. Stores, buses and
    # cloud APIs are not roles of the module; they become external boxes.
    for x in graph.externals:
        if x.file not in graph.architecture_paths:
            continue
        if x.category == "security":
            roles.setdefault(_module_of(x.file), set()).add("auth")
        elif x.category == "frontend":
            roles.setdefault(_module_of(x.file), set()).add("frontend")
    return {m: tuple(sorted(rs)) for m, rs in sorted(roles.items())}


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
        # The extractor cites a file's line 1 for the file's own node; the
        # build knows the file's length, and an empty file has no line 1. It
        # is cited as itself (0, 0), which the passport shows as the path
        # alone and links without a line (review #23 F3: 259 citations to a
        # line that did not exist, `api/__init__.py:1` first in the README).
        if (
            node.kind is NodeKind.MODULE
            and lines.get(node.id) == 0
            and node.evidence == (Evidence(node.id, 1, 1),)
        ):
            node = replace(node, evidence=(Evidence(node.id, 0, 0),))
        if not _verify_evidence(node, node.id, lines, strict, acc.diagnostics):
            continue
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
        if not _verify_evidence(
            edge, f"{edge.src} -> {edge.dst}", lines, strict, acc.diagnostics
        ):
            continue
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

    # --- semantic facts: the same independent evidence re-check ------------
    def fact_ok(ev: Evidence, subject: str) -> bool:
        count = lines.get(ev.file)
        if (
            count is None
            or not (1 <= ev.start_line <= count)
            or not (ev.start_line <= ev.end_line <= count)
        ):
            acc.diagnostics.append(
                Diagnostic(
                    code="SVA-B-002",
                    severity=Severity.WARNING,
                    message=(
                        "a semantic fact cites an invalid source location and was dropped"
                    ),
                    subject=subject,
                    location=str(ev),
                )
            )
            return False
        return True

    routes = tuple(r for r in extracted.routes if fact_ok(r.evidence, r.handler))
    tasks = tuple(t for t in extracted.tasks if fact_ok(t.evidence, t.handler))
    entrypoints = tuple(e for e in extracted.entrypoints if fact_ok(e.evidence, e.name))
    externals = tuple(x for x in extracted.externals if fact_ok(x.evidence, x.label))

    # Environment facts cite files stage 1 never records (`values-prod.yaml`,
    # `Dockerfile`, `eas.json`), so the scan's line table cannot vouch for
    # them; the line count is read from disk instead, cached per file. `(0,0)`
    # is legal here too: an empty `config/prod.yaml` is cited as itself.
    disk_lines: dict[str, int] = {}

    def env_fact_ok(ev: Evidence, subject: str) -> bool:
        count = lines.get(ev.file)
        if count is None:
            count = disk_lines.get(ev.file)
        if count is None:
            try:
                data = (scan.root / ev.file).read_bytes()
            except OSError:
                count = -1
            else:
                count = 0 if not data else data.count(b"\n") + (
                    0 if data.endswith(b"\n") else 1
                )
            disk_lines[ev.file] = count
        whole_file = ev.start_line == 0 and ev.end_line == 0
        ok = (whole_file and count == 0) or (
            not whole_file and 1 <= ev.start_line <= ev.end_line <= count
        )
        if not ok:
            acc.diagnostics.append(
                Diagnostic(
                    code="SVA-B-002",
                    severity=Severity.WARNING,
                    message=(
                        "a semantic fact cites an invalid source location and was dropped"
                    ),
                    subject=subject,
                    location=str(ev),
                )
            )
        return ok

    environments = tuple(
        f
        for f in extracted.environments
        if all(env_fact_ok(ev, f"{f.source}:{f.name}") for ev in f.evidence)
    )

    return Graph(
        nodes=dict(sorted(acc.nodes.items())),
        edges=merged,
        modules=modules,
        module_deps=tuple(sorted(deps)),
        scorecard=extracted.scorecard,
        diagnostics=tuple(acc.diagnostics) + extracted.diagnostics,
        architecture_paths=frozenset(eligible),
        file_languages=scan.languages(),
        routes=routes,
        tasks=tasks,
        entrypoints=entrypoints,
        externals=externals,
        environments=environments,
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
