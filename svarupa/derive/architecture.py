"""Architecture and module-dependency derivers.

Both read only the graph and the clustering, and both emit ids that exist in
the graph. The clustering decides *grouping*; it never becomes an identity.
"""

from __future__ import annotations

from svarupa.build import Graph, entrypoint_module, module_of, module_roles
from svarupa.cluster import Clustering
from svarupa.derive.base import (
    MAX_EVIDENCE_PER_BOX,
    MAX_TOP_BOXES,
    ROOT,
    Deriver,
    DiagramEdge,
    DiagramKind,
    DiagramNode,
    DiagramSet,
    DiagramSpec,
    ModulePair,
    Region,
    group_evidence,
    module_evidence,
    runtime_edges,
    spec_id,
)
from svarupa.derive.components import FLOW_SUFFIX, code_spec, flow_spec
from svarupa.diagnostics import Diagnostic, Severity
from svarupa.model import Evidence, NodeKind

# Role priority when a module holds several: a UI module is a frontend
# whatever else it does; serving HTTP says more than also having a task; a
# CLI door is the weakest claim. The full set stays readable in `roles`.
_ROLE_PRIORITY = ("frontend", "api", "worker", "auth", "cli")

# Archify's component vocabulary, which is what makes a box read as a kind of
# thing rather than a folder. `module` stays for code with no evidenced role.
_ARCHETYPE = {
    "frontend": "frontend",
    "api": "backend",
    "worker": "backend",
    "cli": "backend",
    "auth": "security",
}

# The verb an arrow to an external carries, per category.
_EXTERNAL_VERB = {"database": "reads/writes", "messagebus": "publishes", "cloud": "calls"}


def _import_edge(a: str, b: str, w: int, ev: tuple[Evidence, ...]) -> DiagramEdge:
    """A structural import arrow: no drawn text (the word is noise when every
    arrow in a view is an import), the count in the note for the tooltip."""
    return DiagramEdge(
        src=a,
        dst=b,
        label="",
        note=f"{w} import{'s' if w != 1 else ''}",
        evidence=ev,
        weight=w,
    )


def _role_kind(roles: dict[str, tuple[str, ...]], module: str) -> str:
    held = roles.get(module, ())
    role = next((r for r in _ROLE_PRIORITY if r in held), None)
    return _ARCHETYPE[role] if role else "module"


def module_sublabel(graph: Graph, module: str) -> str:
    """The semantic second line: what a module is, never where it lives.

    Built from evidenced facts only: the framework and route count, the task
    count, the entrypoint name, the UI framework, the auth library; failing
    all of those, how many files it holds.
    """
    parts: list[str] = []
    routes = [
        r
        for r in graph.routes
        if r.file in graph.architecture_paths and module_of(r.file) == module
    ]
    if routes:
        frameworks = sorted({r.framework for r in routes})
        names = {
            "fastapi": "FastAPI",
            "flask": "Flask",
            "express": "Express",
            "nestjs": "NestJS",
        }
        fw = "/".join(names.get(f, f) for f in frameworks)
        parts.append(f"{fw} · {len(routes)} route{'s' if len(routes) != 1 else ''}")
    tasks = [
        t
        for t in graph.tasks
        if t.file in graph.architecture_paths and module_of(t.file) == module
    ]
    if tasks:
        parts.append(f"Celery · {len(tasks)} task{'s' if len(tasks) != 1 else ''}")
    entries = sorted(
        e.name
        for e in graph.entrypoints
        if entrypoint_module(graph, e.target, e.lang, e.file) == module
    )
    if entries:
        parts.append("cli · " + ", ".join(entries[:2]))
    labels = sorted(
        {
            x.label
            for x in graph.externals
            if x.file in graph.architecture_paths
            and module_of(x.file) == module
            and x.category in ("frontend", "security")
        }
    )
    parts.extend(labels[:2])
    if not parts:
        mod = graph.modules.get(module)
        if mod is not None:
            parts.append(f"{mod.file_count} file{'s' if mod.file_count != 1 else ''}")
    return " · ".join(parts[:3])


def _externals_of(
    graph: Graph, modules: set[str]
) -> dict[tuple[str, str], list[tuple[str, Evidence]]]:
    """(category, label) -> [(module, import line)] for stores, buses and
    cloud APIs the given modules talk to. Security and frontend imports are
    roles, not boxes, and are left out."""
    out: dict[tuple[str, str], list[tuple[str, Evidence]]] = {}
    for x in sorted(graph.externals):
        if x.file not in graph.architecture_paths or x.category not in _EXTERNAL_VERB:
            continue
        m = module_of(x.file)
        if m in modules:
            out.setdefault((x.category, x.label), []).append((m, x.evidence))
    return out


def external_nodes_and_edges(
    graph: Graph, modules: set[str], source_of: dict[str, str] | None = None
) -> tuple[list[DiagramNode], list[DiagramEdge]]:
    """External boxes for what these modules import, plus dashed verb edges.

    `source_of` maps a module to the box that stands for it in this spec (its
    group at the top level), so edges attach to what is drawn. One external
    box per (category, label): three modules talking to MongoDB share one
    box and three arrows, which is the picture, not three MongoDBs.
    """
    nodes: list[DiagramNode] = []
    edges: list[DiagramEdge] = []
    for (category, label), holders in sorted(_externals_of(graph, modules).items()):
        nid = f"ext:{category}:{label}"
        packages = sorted({x.package for x in graph.externals if x.label == label})
        nodes.append(
            DiagramNode(
                id=nid,
                label=label,
                kind=category,
                evidence=tuple(sorted({ev for _, ev in holders}))[:MAX_EVIDENCE_PER_BOX],
                sublabel="via " + ", ".join(packages[:2]),
                attrs=(("external", category),),
            )
        )
        by_src: dict[str, list[Evidence]] = {}
        for m, ev in holders:
            src = (source_of or {}).get(m, m)
            by_src.setdefault(src, []).append(ev)
        for k, (src, evs) in enumerate(sorted(by_src.items())):
            # The verb is drawn once per external box (on the first arrow in
            # canonical order); twelve `reads/writes` fanning into one store
            # said one thing twelve times. Every arrow keeps the verb in its
            # note, so the tooltip and the passport still say it.
            edges.append(
                DiagramEdge(
                    src=src,
                    dst=nid,
                    label=_EXTERNAL_VERB[category] if k == 0 else "",
                    note=_EXTERNAL_VERB[category],
                    evidence=tuple(sorted(set(evs)))[:MAX_EVIDENCE_PER_BOX],
                    weight=len(evs),
                    variant="dashed",
                )
            )
    return nodes, edges


def service_regions(
    graph: Graph, member_ids: set[str], stand_in: dict[str, str] | None = None
) -> tuple[Region, ...]:
    """Compose services wrapping the modules under their build context.

    `stand_in` maps a module to the box that represents it in this spec; a
    region lists the boxes actually drawn. A service with no member here is
    not a region here.
    """
    out: list[Region] = []
    for nid, node in sorted(graph.nodes.items()):
        if node.kind is not NodeKind.SERVICE:
            continue
        ctx = node.attr("build_context")
        if not ctx:
            continue
        ctx = ctx.replace("\\", "/").lstrip("./").rstrip("/") if ctx not in (".", "./") else ""
        members: set[str] = set()
        for m in member_ids:
            target = m
            if stand_in and m in stand_in:
                target = stand_in[m]
            inside = m == ctx or (ctx == "" or m.startswith(ctx + "/"))
            if inside and (m in graph.modules):
                members.add(target)
        if ctx == "":
            # A root build context wraps every module in the repository, and
            # a boundary around the whole diagram says nothing a reader can
            # use. Two services built from the root (one compose file, two
            # Dockerfiles) also both wrap everything and overlap each other.
            continue
        if members:
            out.append(
                Region(
                    id=f"svc:{nid}",
                    label=node.label,
                    members=tuple(sorted(members)),
                    evidence=node.evidence,
                )
            )
    return tuple(out)


def _evidence_with_roles(
    graph: Graph, module: str, base: tuple[Evidence, ...]
) -> tuple[Evidence, ...]:
    """Module evidence plus role citations, capped as one list.

    Appending after the cap quietly exceeded `MAX_EVIDENCE_PER_BOX` (an
    11-file api module rendered 7 entries against a cap of 6). Role evidence
    is reserved first, since the role colour is the claim a reader will
    question; module evidence fills the rest.
    """
    roles = _role_evidence(graph, module)
    keep = MAX_EVIDENCE_PER_BOX - len(roles)
    trimmed = tuple(ev for ev in base if ev not in roles)[: max(0, keep)]
    return trimmed + roles


def _role_evidence(graph: Graph, module: str) -> tuple[Evidence, ...]:
    """One citation per role a module holds: the first route, task, and
    resolved entrypoint declaration inside it, in that order."""
    out: list[Evidence] = []
    route = next(
        (
            r.evidence
            for r in sorted(graph.routes)
            if r.file in graph.architecture_paths and module_of(r.file) == module
        ),
        None,
    )
    task = next(
        (
            t.evidence
            for t in sorted(graph.tasks)
            if t.file in graph.architecture_paths and module_of(t.file) == module
        ),
        None,
    )
    entry = next(
        (
            e.evidence
            for e in sorted(graph.entrypoints)
            if entrypoint_module(graph, e.target, e.lang, e.file) == module
        ),
        None,
    )
    for ev in (route, task, entry):
        if ev is not None:
            out.append(ev)
    return tuple(out)


class ArchitectureDeriver(Deriver):
    """Top view: about twelve boxes, drill in for detail.

    The top level is always roughly `MAX_TOP_BOXES` regardless of repository
    size, which is the whole answer to a fifty-thousand-node monorepo. You do
    not zoom out, you drill in, and the code path is identical at 500 nodes and
    500,000.
    """

    kind = DiagramKind.ARCHITECTURE
    title = "Architecture"

    def derive(self, graph: Graph, clustering: Clustering) -> DiagramSet | None:
        if not graph.modules:
            return DiagramSet(
                self.kind,
                ROOT,
                {},
                (self.unavailable("no modules were found, so there is nothing to draw"),),
            )

        diags: list[Diagnostic] = []
        pairs, type_only_skipped = runtime_edges(graph)
        if type_only_skipped:
            diags.append(
                Diagnostic(
                    code="SVA-R-002",
                    severity=Severity.INFO,
                    message=(
                        f"{type_only_skipped} type-only import(s) excluded: they bind a "
                        "compiler, not a running system"
                    ),
                    subject=self.kind.value,
                )
            )

        # A module can exist in `graph.modules` while having no nodes at all:
        # `modules` counts architecture-eligible files including config, but
        # only files with a language extractor produce nodes. `.github/workflows`
        # is the common case. Fail-closed means such a box cannot be drawn, so
        # it is excluded and counted rather than fabricated or crashed on.
        drawable = {m for m in graph.modules if module_evidence(graph, m)}
        undrawable = sorted(set(graph.modules) - drawable)
        if undrawable:
            diags.append(
                Diagnostic(
                    code="SVA-R-004",
                    severity=Severity.INFO,
                    message=(
                        f"{len(undrawable)} module(s) omitted for lack of any "
                        "extractable source; no evidence means no box"
                    ),
                    subject=", ".join(undrawable[:5]),
                )
            )
        if not drawable:
            return DiagramSet(
                self.kind,
                ROOT,
                {},
                (
                    *diags,
                    self.unavailable(
                        "no module contains source we can extract, so every box "
                        "would be unevidenced"
                    ),
                ),
            )

        groups = self._top_groups(clustering, graph, diags, drawable)
        specs: dict[str, DiagramSpec] = {}

        # Top level: one box per group, edges aggregated between groups.
        member_group = {m: anchor for anchor, members in groups for m in members}
        top_labels = _labels_for([anchor for anchor, _ in groups])
        top_nodes: list[DiagramNode] = []
        roles = module_roles(graph)
        for anchor, members in groups:
            if len(members) > 1:
                child = spec_id(anchor)
                specs[child] = self._group_spec(graph, anchor, members, pairs, specs, diags)
            else:
                # A singleton at the top level drills straight into its own
                # components, the same way a leaf inside a group does. A leaf
                # with nothing inside stays a leaf.
                child = self._components(graph, anchor, ROOT, specs, diags)
            # A singleton wears its module's role, the same as it would inside
            # a group; a multi-module group stays a group, since a single role
            # colour over several modules would be a claim about all of them.
            singleton_kind = _role_kind(roles, members[0]) if len(members) == 1 else "group"
            top_nodes.append(
                DiagramNode(
                    id=anchor,
                    label=top_labels[anchor],
                    kind="group" if len(members) > 1 else singleton_kind,
                    evidence=(
                        _evidence_with_roles(
                            graph, members[0], group_evidence(graph, members, anchor)
                        )
                        if len(members) == 1
                        else group_evidence(graph, members, anchor)
                    ),
                    child_spec=child,
                    attrs=(("modules", str(len(members))),)
                    + (
                        (("roles", ",".join(roles[members[0]])),)
                        if len(members) == 1 and members[0] in roles
                        else ()
                    ),
                    sublabel=(
                        module_sublabel(graph, members[0])
                        if len(members) == 1
                        else f"{len(members)} modules"
                    ),
                )
            )
        # What the whole system talks to, attached to the group that does.
        ext_nodes, ext_edges = external_nodes_and_edges(graph, set(member_group), member_group)
        top_nodes.extend(ext_nodes)
        top_regions = service_regions(graph, set(member_group), member_group)

        top_edges: dict[tuple[str, str], tuple[int, list[Evidence]]] = {}
        for a, b, weight, evidence in pairs:
            ga, gb = member_group.get(a), member_group.get(b)
            if ga is None or gb is None or ga == gb:
                continue
            w, ev = top_edges.get((ga, gb), (0, []))
            top_edges[(ga, gb)] = (w + weight, [*ev, *evidence])

        specs[ROOT] = DiagramSpec(
            kind=self.kind,
            id=ROOT,
            title=self.title,
            subtitle=f"{len(graph.modules)} modules in {len(groups)} groups",
            nodes=tuple(sorted(top_nodes)),
            edges=tuple(
                sorted(
                    [
                        _import_edge(a, b, w, tuple(sorted(set(ev)))[:MAX_EVIDENCE_PER_BOX])
                        for (a, b), (w, ev) in top_edges.items()
                    ]
                    + ext_edges
                )
            ),
            regions=top_regions,
        )
        return DiagramSet(self.kind, ROOT, specs, tuple(diags))

    def _top_groups(
        self,
        clustering: Clustering,
        graph: Graph,
        diags: list[Diagnostic],
        drawable: set[str],
    ) -> list[tuple[str, tuple[str, ...]]]:
        """Communities, capped at the top-box budget.

        Clustering does not bound the count: a stubborn community is diagnosed
        rather than split, so capping is this stage's job. When there are too
        many groups, the smallest are merged into the largest they connect to
        rather than into an "other" bucket -- a box labelled "other" is not an
        architectural fact.
        """
        groups = [
            (c.anchor, tuple(m for m in c.members if m in drawable))
            for c in clustering.communities
        ]
        groups = [(a, m) for a, m in groups if m]
        # An anchor that is itself undrawable cannot label the box, so the
        # group is re-anchored on a member that can be pointed at.
        groups = [(a if a in drawable else m[0], m) for a, m in groups]
        if not groups:
            groups = [(m, (m,)) for m in sorted(drawable)]

        if len(groups) <= MAX_TOP_BOXES:
            return sorted(groups)

        groups.sort(key=lambda g: (-len(g[1]), g[0]))
        keep, spill = groups[:MAX_TOP_BOXES], groups[MAX_TOP_BOXES:]
        pairs, _ = runtime_edges(graph)
        neighbours: dict[str, set[str]] = {}
        for a, b, _w, _ev in pairs:
            neighbours.setdefault(a, set()).add(b)
            neighbours.setdefault(b, set()).add(a)

        merged: dict[str, list[str]] = {anchor: list(members) for anchor, members in keep}
        by_connection = 0
        by_proximity = 0
        stranded: list[tuple[str, tuple[str, ...]]] = []

        for spill_anchor, members in spill:
            member_set = set(members)

            # 1. A real dependency is a real reason to group.
            #
            # `best_score` starts at 0, not -1. Starting below zero meant a
            # group connected to nothing still "won" against the first
            # candidate, so unrelated modules were absorbed into an arbitrary
            # neighbour and the diagnostic then reported them as connected.
            # That is an invented architectural claim with a misleading label
            # attached.
            best_anchor: str | None = None
            best_score = 0
            for target_anchor in sorted(merged):
                target = set(merged[target_anchor])
                score = sum(1 for m in member_set for n in neighbours.get(m, ()) if n in target)
                if score > best_score:
                    best_anchor, best_score = target_anchor, score

            if best_anchor is not None:
                merged[best_anchor].extend(members)
                by_connection += 1
                continue

            # 2. No dependency, so fall back to structural proximity. Sharing a
            #    parent directory is a genuine fact about the repository -- the
            #    same principle module identity itself rests on -- whereas an
            #    invented dependency is not.
            best_anchor, best_shared = None, 0
            for target_anchor in sorted(merged):
                shared = max(
                    (_shared_prefix(m, t) for m in member_set for t in merged[target_anchor]),
                    default=0,
                )
                if shared > best_shared:
                    best_anchor, best_shared = target_anchor, shared

            if best_anchor is not None:
                merged[best_anchor].extend(members)
                by_proximity += 1
                continue

            # 3. Neither connected nor adjacent. Going over budget is more
            #    honest than asserting a relationship that does not exist.
            stranded.append((spill_anchor, members))

        for anchor, members in stranded:
            merged.setdefault(anchor, []).extend(members)

        parts: list[str] = []
        if by_connection:
            parts.append(f"{by_connection} merged into a group they depend on")
        if by_proximity:
            parts.append(f"{by_proximity} merged by shared directory")
        if stranded:
            parts.append(f"{len(stranded)} kept separate, being neither connected nor adjacent")
        diags.append(
            Diagnostic(
                code="SVA-R-003",
                severity=Severity.WARNING if stranded else Severity.INFO,
                message=(
                    f"{len(groups)} groups exceeded the {MAX_TOP_BOXES}-box top level: "
                    + "; ".join(parts)
                ),
                subject=self.kind.value,
            )
        )
        return sorted((a, tuple(sorted(set(m)))) for a, m in merged.items())

    def _components(
        self,
        graph: Graph,
        module_id: str,
        parent: str,
        specs: dict[str, DiagramSpec],
        diags: list[Diagnostic],
    ) -> str | None:
        """Attach the levels below a leaf module, when it earns them.

        A module with several components drills into its **component flow**,
        and each component drills into its **code**. A module that is really
        one component skips the flow level and drills straight into the code,
        because a one-box flow restates the parent, which design §5.1 names as
        the case where no child link is emitted.
        """
        made = flow_spec(graph, module_id, parent, self.kind)
        if made is not None:
            new_specs, extra = made
            specs.update(new_specs)
            diags.extend(extra)
            return spec_id(module_id) + FLOW_SUFFIX

        files = sorted(
            path
            for path in graph.architecture_paths
            if _file_module(path) == module_id and path in graph.nodes
        )
        if len(files) == 1:
            direct = code_spec(graph, files[0], parent, self.kind)
            if direct is not None:
                spec, extra = direct
                specs[spec.id] = spec
                diags.extend(extra)
                return spec.id
        return None

    def _group_spec(
        self,
        graph: Graph,
        anchor: str,
        members: tuple[str, ...],
        pairs: tuple[ModulePair, ...],
        specs: dict[str, DiagramSpec],
        diags: list[Diagnostic],
    ) -> DiagramSpec:
        inside = set(members)
        labels = _labels_for(list(members))
        roles = module_roles(graph)
        module_nodes = [
            DiagramNode(
                id=m,
                label=labels[m],
                # A module with an evidence-backed role is drawn as that
                # role. The supporting decorator/manifest line joins the
                # box's evidence, so the colour is a claim a reader can
                # click, not a style.
                kind=_role_kind(roles, m),
                evidence=_evidence_with_roles(graph, m, module_evidence(graph, m)),
                child_spec=self._components(graph, m, spec_id(anchor), specs, diags),
                attrs=(
                    (("files", str(graph.modules[m].file_count)),) if m in graph.modules else ()
                )
                + ((("roles", ",".join(roles[m])),) if m in roles else ()),
                sublabel=module_sublabel(graph, m),
            )
            for m in members
            if module_evidence(graph, m)
        ]
        drawn = {n.id for n in module_nodes}
        ext_nodes, ext_edges = external_nodes_and_edges(graph, drawn)
        nodes = tuple(sorted(module_nodes + ext_nodes))
        regions = service_regions(graph, drawn)
        edges = tuple(
            sorted(
                [
                    _import_edge(a, b, w, ev)
                    for a, b, w, ev in pairs
                    if a in inside and b in inside
                ]
                + ext_edges
            )
        )
        return DiagramSpec(
            kind=self.kind,
            id=spec_id(anchor),
            title=f"{_label(anchor)} internals",
            subtitle=f"{len(nodes)} modules",
            nodes=nodes,
            edges=edges,
            regions=regions,
            parent=ROOT,
        )


class ModuleDepsDeriver(Deriver):
    """Every module and every dependency, layered by depth.

    No grouping and no drill-down: this is the flat, complete picture, and its
    value is precisely that nothing was summarized away.
    """

    kind = DiagramKind.MODULE_DEPS
    title = "Module dependencies"

    def derive(
        self,
        graph: Graph,
        clustering: Clustering,  # noqa: ARG002 - flat by design; grouping is the point of the other view
    ) -> DiagramSet | None:
        pairs, skipped = runtime_edges(graph)
        if not pairs:
            return DiagramSet(
                self.kind,
                ROOT,
                {},
                (
                    self.unavailable(
                        "no module-to-module dependencies resolved, so a dependency "
                        "diagram would be a page of disconnected boxes",
                        ("Check the resolution scorecard for unresolved imports.",),
                    ),
                ),
            )

        depth = _layer(pairs, sorted(graph.modules))
        involved = {m for a, b, _w, _e in pairs for m in (a, b)}
        dep_labels = _labels_for(sorted(involved))
        roles = module_roles(graph)
        nodes = tuple(
            sorted(
                DiagramNode(
                    id=m,
                    label=dep_labels[m],
                    kind=_role_kind(roles, m),
                    evidence=_evidence_with_roles(graph, m, module_evidence(graph, m)),
                    attrs=(("layer", str(depth.get(m, 0))),)
                    + ((("roles", ",".join(roles[m])),) if m in roles else ()),
                )
                for m in sorted(involved)
                if module_evidence(graph, m)
            )
        )
        known = {n.id for n in nodes}
        edges = tuple(
            sorted(
                _import_edge(a, b, w, ev) for a, b, w, ev in pairs if a in known and b in known
            )
        )
        diags: list[Diagnostic] = []
        if skipped:
            diags.append(
                Diagnostic(
                    code="SVA-R-002",
                    severity=Severity.INFO,
                    message=f"{skipped} type-only import(s) excluded",
                    subject=self.kind.value,
                )
            )
        # This deriver's whole claim is that nothing was summarized away, so
        # anything it does drop has to be said out loud. A dependency into a
        # config-only module was vanishing with no note at all, while the
        # architecture deriver reported the identical situation.
        dropped_modules = sorted(involved - known)
        dropped_edges = sum(1 for a, b, _w, _e in pairs if a not in known or b not in known)
        if dropped_modules or dropped_edges:
            diags.append(
                Diagnostic(
                    code="SVA-R-004",
                    severity=Severity.INFO,
                    message=(
                        f"{len(dropped_modules)} module(s) and {dropped_edges} "
                        "dependency(ies) omitted for lack of extractable source; "
                        "no evidence means no box"
                    ),
                    subject=", ".join(dropped_modules[:5]) or self.kind.value,
                )
            )
        spec = DiagramSpec(
            kind=self.kind,
            id=ROOT,
            title=self.title,
            subtitle=f"{len(nodes)} modules, {len(edges)} dependencies",
            nodes=nodes,
            edges=edges,
        )
        return DiagramSet(self.kind, ROOT, {ROOT: spec}, tuple(diags))


def _file_module(path: str) -> str:
    return path.rsplit("/", 1)[0] if "/" in path else ""


def _shared_prefix(a: str, b: str) -> int:
    """How many leading path segments two modules share.

    Structural proximity is a real fact about the repository, which is why it
    is an acceptable second choice when no dependency exists. It is the same
    principle module identity rests on: what the developers laid out.
    """
    shared = 0
    for x, y in zip(a.split("/"), b.split("/"), strict=False):
        if x != y:
            break
        shared += 1
    return shared


def _label(module_id: str) -> str:
    """Human-facing name for a module.

    The repository root is `""`, and calling it "root" collided in a reader's
    head with the root *spec*. `(repo root)` cannot be mistaken for a
    directory name.
    """
    if module_id == "":
        return "(repo root)"
    return module_id.rsplit("/", 1)[-1] or module_id


def _labels_for(module_ids: list[str]) -> dict[str, str]:
    """Labels that are unique within one diagram.

    Two modules can share a leaf name -- `src/api/routes` and
    `src/worker/routes` both label "routes" -- and ids disambiguate while
    human-facing text does not. Where leaves collide, enough of the path is
    added to tell them apart.
    """
    leaves: dict[str, list[str]] = {}
    for mid in module_ids:
        leaves.setdefault(_label(mid), []).append(mid)
    out: dict[str, str] = {}
    for leaf, owners in leaves.items():
        if len(owners) == 1:
            out[owners[0]] = leaf
            continue
        for mid in owners:
            parts = mid.split("/")
            out[mid] = "/".join(parts[-2:]) if len(parts) > 1 else leaf
    return out


def _layer(pairs: tuple[ModulePair, ...], modules: list[str]) -> dict[str, int]:
    """Longest-path depth, cycle-tolerant.

    Real dependency graphs contain cycles, and refusing to lay one out is
    worse than laying it out imperfectly: a cycle is exactly the thing a
    reader wants to see. Nodes already on the current path are skipped, so a
    cycle gets a finite depth instead of a stack overflow.
    """
    out: dict[str, set[str]] = {m: set() for m in modules}
    for a, b, _w, _e in pairs:
        out.setdefault(a, set()).add(b)

    depth: dict[str, int] = {}

    def visit(node: str, path: frozenset[str]) -> int:
        if node in depth:
            return depth[node]
        if node in path:
            return 0
        children = [c for c in sorted(out.get(node, ())) if c not in path]
        value = 1 + max((visit(c, path | {node}) for c in children), default=-1)
        depth[node] = value
        return value

    for m in sorted(out):
        visit(m, frozenset())
    return depth
