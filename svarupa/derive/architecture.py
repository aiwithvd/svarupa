"""Architecture and module-dependency derivers.

Both read only the graph and the clustering, and both emit ids that exist in
the graph. The clustering decides *grouping*; it never becomes an identity.
"""

from __future__ import annotations

from collections.abc import Mapping

from svarupa.build import (
    Graph,
    build_context_of,
    entrypoint_module,
    module_of,
    module_roles,
    modules_under,
)
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
    """A structural import arrow: no drawn text, the count in the note.

    The word was drawn for one wave (6b5866a) and review #20 measured it: 76
    identical `imports` on descovo's module deps, doubling the ink and saying
    nothing the solid stroke does not. The legend says once what a solid arrow
    is; the passport and tooltip carry the count.
    """
    return DiagramEdge(
        src=a,
        dst=b,
        label="",
        note=f"{w} import{'s' if w != 1 else ''}",
        evidence=ev,
        weight=w,
    )


def show_module(module_id: str) -> str:
    """A module id for a diagnostic: the repository root reads `(repo root)`,
    as its box does, never an empty string a reader cannot see (review #20
    C7 found `'' 1 module(s) omitted` in a report)."""
    return module_id or "(repo root)"


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
    graph: Graph,
    modules: set[str],
    source_of: Mapping[str, str | tuple[str, ...]] | None = None,
) -> tuple[list[DiagramNode], list[DiagramEdge]]:
    """External boxes for what these modules import, plus dashed verb edges.

    `source_of` maps a module to the box, or boxes, that stand for it in this
    spec (its group at the top level; every service built from a context that
    holds it in the System view), so edges attach to what is drawn. One
    external box per (category, label): three modules talking to MongoDB
    share one box and three arrows, which is the picture, not three MongoDBs.
    """
    nodes: list[DiagramNode] = []
    edges: list[DiagramEdge] = []
    for (category, label), holders in sorted(_externals_of(graph, modules).items()):
        nid = f"ext:{category}:{label}"
        packages = sorted(
            {
                x.package
                for x in graph.externals
                if x.label == label and x.file in graph.architecture_paths
            }
        )
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
            stands = (source_of or {}).get(m, m)
            for src in (stands,) if isinstance(stands, str) else stands:
                by_src.setdefault(src, []).append(ev)
        for src, evs in sorted(by_src.items()):
            # Every store arrow carries its verb; the label gate drops the
            # ones that would collide. Drawing it on the first arrow only
            # left both MongoDB arrows on the demo unlabelled when that one
            # was dropped (review #20 C3), and the structural arrows are
            # silent now, so the verbs are the only words on the canvas.
            edges.append(
                DiagramEdge(
                    src=src,
                    dst=nid,
                    label=_EXTERNAL_VERB[category],
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

    `stand_in` maps a module to the box that represents it in this spec (its
    group at the top level). A box stands inside a boundary only if EVERY
    module it represents is under the context: a group holding three `web/*`
    modules next to three `api/*` ones is not inside the `api` service, and
    drawing the boundary around it claimed deployment for code outside the
    build. The root context wraps nothing (a boundary around the whole
    diagram says nothing, and two root-built services would overlap), and a
    context escaping the repository builds nothing in the tree.
    """
    out: list[Region] = []
    for nid, node in sorted(graph.nodes.items()):
        if node.kind is not NodeKind.SERVICE:
            continue
        ctx = build_context_of(graph, nid)
        if not ctx:
            continue
        inside = modules_under(graph, ctx)
        represented: dict[str, set[str]] = {}
        for m in member_ids:
            if m in graph.modules:
                represented.setdefault((stand_in or {}).get(m, m), set()).add(m)
        members = sorted(box for box, mods in represented.items() if mods and mods <= inside)
        if members:
            # The boundary is a claim about the build, so it cites the `build:`
            # line when the extractor recorded one, else the service line.
            build_line = node.attr("build_line")
            evidence = node.evidence
            if build_line and build_line.isdigit():
                ev0 = node.evidence[0]
                evidence = (Evidence(ev0.file, int(build_line), int(build_line)),)
            out.append(
                Region(
                    id=f"svc:{nid}",
                    label=node.label,
                    members=tuple(members),
                    evidence=evidence,
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
                    subject=", ".join(show_module(m) for m in undrawable[:5]),
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

        # Top level: one box per group, edges aggregated between groups. A
        # group is its own box with its own id: it borrowed its anchor's name
        # and id for nineteen reviews, so `src/api -> scripts (16 imports)`
        # cited six lines none of which imported anything under `scripts/`,
        # and the passport's "also in" jumped from the group to the module
        # (review #20 M3). Communities are presentation (decision F2); the box
        # says so in its label and never wears a member's identity.
        member_group = {
            m: group_box_id(anchor, members) for anchor, members in groups for m in members
        }
        top_labels = top_box_labels(groups)
        top_nodes: list[DiagramNode] = []
        roles = module_roles(graph)
        for anchor, members in groups:
            if len(members) > 1:
                child = spec_id(anchor)
                specs[child] = self._group_spec(
                    graph, anchor, members, pairs, specs, diags, top_labels[anchor]
                )
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
                    id=group_box_id(anchor, members),
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
                    )
                    # Only a group lists members; a singleton listing itself
                    # was a chip saying nothing (review #21 N13).
                    + ((("members", "\n".join(members)),) if len(members) > 1 else ()),
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

    def components_for(
        self,
        graph: Graph,
        module_id: str,
        parent: str,
        specs: dict[str, DiagramSpec],
        diags: list[Diagnostic],
    ) -> str | None:
        """Public door to the drill below a module, for other derivers whose
        boxes are modules (data flow, request flow): one definition of the
        component-flow and code levels, not one per diagram type."""
        return self._components(graph, module_id, parent, specs, diags)

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
            # The box the reader clicked is the module's own, so the code
            # view is titled by it (`versions code`), not by the file's raw
            # stem — migration filenames lead with a date and a hash.
            direct = code_spec(graph, files[0], parent, self.kind, _label(module_id))
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
        label: str,
    ) -> DiagramSpec:
        inside = set(members)
        labels = labels_for(list(members))
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
            # Titled by the box the reader opened, not its anchor: `agent`
            # opened "routers internals" (review #21 N7); externals are not
            # modules and are not counted as such.
            title=f"{label} internals",
            subtitle=f"{len(module_nodes)} modules"
            + (f", {len(ext_nodes)} external" if ext_nodes else ""),
            nodes=nodes,
            edges=edges,
            regions=regions,
            parent=ROOT,
        )


class ModuleDepsDeriver(Deriver):
    """Every module and every dependency, layered by depth.

    Complete, and readable at the root. Up to the top-box budget this is
    the flat picture: every module a box, every dependency an arrow. Past it
    (30 boxes and 76 arrows on the second acceptance repo, review #20 M5), the
    picture follows the directory tree the modules already have: a box per
    top-level part, its arrows the dependencies between parts, and a drill
    into a part shows the modules inside it and the dependencies among them.
    Every dependency is drawn at exactly one level, the one where both ends
    are different boxes, so nothing is summarized away; it is placed where a
    reader can see it. No community grouping here: the tree is structural,
    the same identity the lockfile rests on. A flat directory of many sibling
    packages stays dense one level down (descovo's `src/`: 26 boxes); that is
    the repository's shape, drawn rather than invented around.
    """

    kind = DiagramKind.MODULE_DEPS
    title = "Module dependencies"

    def derive(
        self,
        graph: Graph,
        clustering: Clustering,  # noqa: ARG002 - structural by design; communities are the other view
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

        involved = {m for a, b, _w, _e in pairs for m in (a, b)}
        known = {m for m in involved if module_evidence(graph, m)}
        diags: list[Diagnostic] = []
        specs: dict[str, DiagramSpec] = {}
        if len(known) > MAX_TOP_BOXES:
            live = tuple(p for p in pairs if p[0] in known and p[1] in known)
            self._tree(graph, live, known, ROOT, None, specs, diags)
            nodes = specs[ROOT].nodes
            edges = specs[ROOT].edges
        else:
            depth = _layer(pairs, sorted(graph.modules))
            dep_labels = labels_for(sorted(known))
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
                        sublabel=module_sublabel(graph, m),
                    )
                    for m in sorted(known)
                )
            )
            edges = tuple(
                sorted(
                    _import_edge(a, b, w, ev)
                    for a, b, w, ev in pairs
                    if a in known and b in known
                )
            )
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
                    subject=", ".join(show_module(m) for m in dropped_modules[:5])
                    or self.kind.value,
                )
            )
        if ROOT not in specs:
            specs[ROOT] = DiagramSpec(
                kind=self.kind,
                id=ROOT,
                title=self.title,
                subtitle=f"{len(nodes)} modules, {len(edges)} dependencies",
                nodes=nodes,
                edges=edges,
            )
        return DiagramSet(self.kind, ROOT, specs, tuple(diags))

    def _tree(
        self,
        graph: Graph,
        pairs: tuple[ModulePair, ...],
        members: set[str],
        sid: str,
        parent: str | None,
        specs: dict[str, DiagramSpec],
        diags: list[Diagnostic],
    ) -> None:
        """One level of the directory tree over `members`, as a spec.

        A level whose members all share one more path segment is skipped
        (a root holding only `src` would be one box), so every level drawn
        has at least two boxes. A box is a module itself when it is the only
        member under its segment, and a part (`tree:<path>`) holding several
        modules otherwise; a part drills into the level below it.
        """
        # Group by the next path segment under `prefix`; a member that IS the
        # prefix is its own box. While everything falls into one part, descend
        # into it (a root holding only `src` would be one box), so every level
        # drawn has at least two boxes and the walk always terminates.
        prefix = ""
        while True:
            parts: dict[str, list[str]] = {}
            for m in sorted(members):
                if m == prefix:
                    parts.setdefault(m, []).append(m)
                    continue
                rest = m[len(prefix) :].lstrip("/") if prefix else m
                head = rest.split("/", 1)[0]
                parts.setdefault(f"{prefix}/{head}".strip("/"), []).append(m)
            if len(parts) == 1:
                ((key, ms),) = parts.items()
                if ms != [key]:
                    prefix = key
                    continue
            break
        box_of: dict[str, str] = {}
        roles = module_roles(graph)
        nodes: list[DiagramNode] = []
        # A part with one module in it IS that module (`dagster/jobs` alone
        # under `dagster/`): a part box around one module would drill into a
        # one-box level, which review #19 F9 already named as nothing to say.
        leaves = [ms[0] for ms in parts.values() if len(ms) == 1]
        leaf_labels = labels_for(leaves)
        arch = ArchitectureDeriver()
        for _key, ms in sorted(parts.items()):
            key = _key
            if len(ms) == 1:
                key = ms[0]
                box_of[key] = key
                nodes.append(
                    DiagramNode(
                        id=key,
                        label=leaf_labels[key],
                        kind=_role_kind(roles, key),
                        evidence=_evidence_with_roles(graph, key, module_evidence(graph, key)),
                        attrs=((("roles", ",".join(roles[key])),) if key in roles else ()),
                        sublabel=module_sublabel(graph, key),
                        child_spec=arch.components_for(graph, key, sid, specs, diags),
                    )
                )
                continue
            bid = f"tree:{key}"
            for m in ms:
                box_of[m] = bid
            child = spec_id(bid)
            inside = tuple(p for p in pairs if p[0] in ms and p[1] in ms)
            self._tree(graph, inside, set(ms), child, sid, specs, diags)
            nodes.append(
                DiagramNode(
                    id=bid,
                    label=_label(key) if key else "(repo root)",
                    # A part is a group of modules (by directory, not by
                    # community) and wears the group colour and chip.
                    kind="group",
                    evidence=group_evidence(graph, tuple(ms), ms[0]),
                    attrs=(("modules", str(len(ms))), ("members", "\n".join(ms))),
                    sublabel=f"{len(ms)} modules",
                    child_spec=child,
                )
            )
        agg: dict[tuple[str, str], tuple[int, list[Evidence]]] = {}
        for a, b, w, ev in pairs:
            ba, bb = box_of[a], box_of[b]
            if ba == bb:
                continue  # drawn one level down, where the two are distinct boxes
            cw, cev = agg.get((ba, bb), (0, []))
            agg[(ba, bb)] = (cw + w, [*cev, *ev])
        edges = tuple(
            sorted(
                _import_edge(a, b, w, tuple(sorted(set(ev)))[:MAX_EVIDENCE_PER_BOX])
                for (a, b), (w, ev) in agg.items()
            )
        )
        depth = _layer(
            tuple((a, b, w, ()) for (a, b), (w, _e) in agg.items()), sorted(box_of.values())
        )
        nodes = [
            DiagramNode(
                id=n.id,
                label=n.label,
                kind=n.kind,
                evidence=n.evidence,
                attrs=(*n.attrs, ("layer", str(depth.get(n.id, 0)))),
                sublabel=n.sublabel,
                child_spec=n.child_spec,
            )
            for n in nodes
        ]
        within = sum(1 for a, b, _w, _e in pairs if box_of[a] == box_of[b])
        between = len(pairs) - within
        where = _label(prefix) if prefix else "the repository"
        n_parts = sum(1 for n in nodes if n.id.startswith("tree:"))
        specs[sid] = DiagramSpec(
            kind=self.kind,
            id=sid,
            title=self.title if sid == ROOT else f"{_label(prefix)} module dependencies",
            # One unit throughout: module dependencies. An arrow between two
            # parts stands for several of them (review #21 N12).
            subtitle=(
                f"{len(members)} modules in {len(nodes)} boxes"
                + (f", {n_parts} drillable" if n_parts else "")
                + f"; {between + within} dependenc{'y' if between + within == 1 else 'ies'}: {between} between boxes"
                + (f" ({len(edges)} arrow{'s' if len(edges) != 1 else ''})" if edges else "")
                + (f", {within} inside {where if prefix else 'the parts'}" if within else "")
            ),
            nodes=tuple(sorted(nodes)),
            edges=edges,
            parent=parent,
        )


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


def group_box_id(anchor: str, members: tuple[str, ...]) -> str:
    """The id of a top-level box: the module itself for a singleton, a
    distinct `group:` id for a community, so no group is ever mistaken for
    the module it was anchored on."""
    return anchor if len(members) == 1 else f"group:{anchor}"


def _common_dir(members: tuple[str, ...]) -> str:
    parts = [m.split("/") for m in members]
    shared: list[str] = []
    for segs in zip(*parts, strict=False):
        if len(set(segs)) != 1:
            break
        shared.append(segs[0])
    return "/".join(shared)


def top_box_labels(groups: list[tuple[str, tuple[str, ...]]]) -> dict[str, str]:
    """Labels for the top level, keyed by anchor.

    A singleton is its module. A group is named by the one fact all its
    members share, the directory they sit under, when there is one (`agent`
    for `agent, agent/routers, agent/schema`); with no shared directory it is
    named for its anchor with the count of the rest (`pipeline +5`), which
    reads as a set and never as the anchor alone. Labels are then made unique
    the way module labels are.
    """
    singles = labels_for([a for a, m in groups if len(m) == 1])
    out: dict[str, str] = {}
    everyone = [m for _, ms in groups for m in ms]
    for anchor, members in groups:
        if len(members) == 1:
            out[anchor] = singles[anchor]
            continue
        shared = _common_dir(members)
        # The directory name says "everything under it": it is used only
        # when no other box holds a module under that directory. On svarupa
        # itself the emit/layout group was named `svarupa` beside a group
        # that held the `svarupa` module (review #21 N8).
        others_under = any(
            m == shared or m.startswith(shared + "/") for m in everyone if m not in members
        )
        out[anchor] = (
            _label(shared)
            if shared and shared not in ("", ".") and not others_under
            else f"{_label(anchor)} +{len(members) - 1}"
        )
    # Two groups under one directory (`src` holding `src/*` twice over) share
    # a label; the colliding groups fall back to their anchor's leaf and count,
    # then to the anchor's last two segments, and singletons to theirs.
    size = dict(groups)
    for _attempt in range(2):
        seen: dict[str, list[str]] = {}
        for anchor, label in out.items():
            # `svarupa` and `svarupa +6` read as one name; compare the stem.
            seen.setdefault(label.split(" +", 1)[0], []).append(anchor)
        clashes = [a for anchors in seen.values() if len(anchors) > 1 for a in anchors]
        if not clashes:
            break
        for anchor in clashes:
            parts = anchor.split("/")
            n = len(size[anchor])
            if n > 1 and _attempt == 0:
                out[anchor] = f"{_label(anchor)} +{n - 1}"
            else:
                stem = "/".join(parts[-2:]) if len(parts) > 1 else _label(anchor)
                out[anchor] = stem + (f" +{n - 1}" if n > 1 else "")
    return out


def labels_for(module_ids: list[str]) -> dict[str, str]:
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
