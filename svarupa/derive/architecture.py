"""Architecture and module-dependency derivers.

Both read only the graph and the clustering, and both emit ids that exist in
the graph. The clustering decides *grouping*; it never becomes an identity.
"""

from __future__ import annotations

from svarupa.build import Graph
from svarupa.cluster import Clustering
from svarupa.derive.base import (
    MAX_TOP_BOXES,
    ROOT,
    Deriver,
    DiagramEdge,
    DiagramKind,
    DiagramNode,
    DiagramSet,
    DiagramSpec,
    ModulePair,
    group_evidence,
    module_evidence,
    runtime_edges,
)
from svarupa.diagnostics import Diagnostic, Severity
from svarupa.model import Evidence


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
        top_nodes: list[DiagramNode] = []
        for anchor, members in groups:
            child = f"group:{anchor}" if len(members) > 1 else None
            top_nodes.append(
                DiagramNode(
                    id=anchor,
                    label=_label(anchor),
                    kind="group" if len(members) > 1 else "module",
                    evidence=group_evidence(graph, members, anchor),
                    child_spec=child,
                    attrs=(("modules", str(len(members))),),
                )
            )
            if child is not None:
                specs[child] = self._group_spec(graph, anchor, members, pairs)

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
                    DiagramEdge(
                        src=a,
                        dst=b,
                        label=f"{w} import{'s' if w != 1 else ''}",
                        evidence=tuple(sorted(set(ev)))[:6],
                        weight=w,
                    )
                    for (a, b), (w, ev) in top_edges.items()
                )
            ),
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
        for _spill_anchor, members in spill:
            best: str | None = None
            best_score = -1
            for target_anchor, target_members in merged.items():
                score = sum(
                    1
                    for m in members
                    for n in neighbours.get(m, ())
                    if n in set(target_members)
                )
                if score > best_score:
                    best_score, best = score, target_anchor
            merged[best or keep[0][0]].extend(members)

        diags.append(
            Diagnostic(
                code="SVA-R-003",
                severity=Severity.INFO,
                message=(
                    f"{len(groups)} groups exceeded the {MAX_TOP_BOXES}-box top level; "
                    f"{len(spill)} were merged into the groups they connect to most"
                ),
                subject=self.kind.value,
            )
        )
        return sorted((a, tuple(sorted(set(m)))) for a, m in merged.items())

    def _group_spec(
        self,
        graph: Graph,
        anchor: str,
        members: tuple[str, ...],
        pairs: tuple[ModulePair, ...],
    ) -> DiagramSpec:
        inside = set(members)
        nodes = tuple(
            sorted(
                DiagramNode(
                    id=m,
                    label=_label(m),
                    kind="module",
                    evidence=module_evidence(graph, m),
                    attrs=(("files", str(graph.modules[m].file_count)),)
                    if m in graph.modules
                    else (),
                )
                for m in members
                if module_evidence(graph, m)
            )
        )
        edges = tuple(
            sorted(
                DiagramEdge(
                    src=a,
                    dst=b,
                    label=f"{w} import{'s' if w != 1 else ''}",
                    evidence=ev,
                    weight=w,
                )
                for a, b, w, ev in pairs
                if a in inside and b in inside
            )
        )
        return DiagramSpec(
            kind=self.kind,
            id=f"group:{anchor}",
            title=f"{_label(anchor)} internals",
            subtitle=f"{len(nodes)} modules",
            nodes=nodes,
            edges=edges,
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
        nodes = tuple(
            sorted(
                DiagramNode(
                    id=m,
                    label=_label(m),
                    kind="module",
                    evidence=module_evidence(graph, m),
                    attrs=(("layer", str(depth.get(m, 0))),),
                )
                for m in sorted(involved)
                if module_evidence(graph, m)
            )
        )
        known = {n.id for n in nodes}
        edges = tuple(
            sorted(
                DiagramEdge(
                    src=a,
                    dst=b,
                    label=f"{w}",
                    evidence=ev,
                    weight=w,
                )
                for a, b, w, ev in pairs
                if a in known and b in known
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
        spec = DiagramSpec(
            kind=self.kind,
            id=ROOT,
            title=self.title,
            subtitle=f"{len(nodes)} modules, {len(edges)} dependencies",
            nodes=nodes,
            edges=edges,
        )
        return DiagramSet(self.kind, ROOT, {ROOT: spec}, tuple(diags))


def _label(module_id: str) -> str:
    return module_id.rsplit("/", 1)[-1] or module_id or "root"


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
