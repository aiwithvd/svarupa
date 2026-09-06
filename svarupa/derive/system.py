"""The system view: services, datastores, queues, and who depends on whom.

This is the diagram people mean when they say "architecture diagram": not
folders and imports but the deployed shape of the system, read left to right
as a story. Archify draws this beautifully from boxes an agent typed; here the
boxes come out of docker-compose with the line that declares each one, which
is the product's promise applied to the level where architecture is actually
written down.

The bridge to the code is a service's `build.context`: a service built from
this repository drills into the structural architecture of the subtree it
builds, so the story view and the evidence view are one navigation.
"""

from __future__ import annotations

from svarupa.build import Graph, module_of, module_roles
from svarupa.cluster import Clustering
from svarupa.derive.architecture import external_nodes_and_edges
from svarupa.derive.base import (
    ROOT,
    Deriver,
    DiagramEdge,
    DiagramKind,
    DiagramNode,
    DiagramSet,
    DiagramSpec,
)
from svarupa.diagnostics import Diagnostic
from svarupa.model import EdgeKind, NodeKind

__all__ = ["SystemDeriver"]

_SYSTEM_KINDS = (NodeKind.SERVICE, NodeKind.DATASTORE, NodeKind.QUEUE)


class SystemDeriver(Deriver):
    """One box per deployed thing, one arrow per declared dependency."""

    kind = DiagramKind.DEPLOY_TOPOLOGY
    title = "System"

    def derive(self, graph: Graph, clustering: Clustering) -> DiagramSet | None:
        _ = clustering
        members = {nid: node for nid, node in graph.nodes.items() if node.kind in _SYSTEM_KINDS}
        if not members:
            return DiagramSet(
                self.kind,
                ROOT,
                {},
                (
                    self.unavailable(
                        "no deployment configuration was found; a docker-compose "
                        "file is what names the services, datastores and queues",
                        fixes=(
                            "Add a compose file, or run against the repository that has one.",
                        ),
                    ),
                ),
            )

        diags: list[Diagnostic] = []
        # Two compose files can both define a `web`. Ids stay distinct, and the
        # human-facing label gains the file's directory when names collide,
        # because two identical boxes force the reader to click to tell them
        # apart, which is the label failing at its one job.
        by_label: dict[str, int] = {}
        for node in members.values():
            by_label[node.label] = by_label.get(node.label, 0) + 1

        roles = module_roles(graph)
        # Archify's vocabulary for the deployed things: a service is a
        # frontend if the code it builds has a frontend role, else a backend;
        # a datastore is a database; a queue is a message bus.
        archetype = {"datastore": "database", "queue": "messagebus", "service": "backend"}
        nodes: list[DiagramNode] = []
        modules_of_service: dict[str, set[str]] = {}
        for nid, node in sorted(members.items()):
            build_context = node.attr("build_context")
            label = node.label
            if by_label[label] > 1:
                where = nid.split("#", 1)[0].rsplit("/", 1)[0] or "."
                label = f"{label} ({where})"
            kind = archetype.get(node.kind.value, node.kind.value)
            sublabel = node.attr("image") or ""
            if build_context:
                ctx = (
                    ""
                    if build_context in (".", "./")
                    else build_context.lstrip("./").rstrip("/")
                )
                mods = {
                    m for m in graph.modules if ctx == "" or m == ctx or m.startswith(ctx + "/")
                }
                modules_of_service[nid] = mods
                held = {r for m in mods for r in roles.get(m, ())}
                if "frontend" in held:
                    kind = "frontend"
                elif "auth" in held and not ({"api", "worker"} & held):
                    kind = "security"
                what: list[str] = []
                # A root build context means "everything": two services built
                # from `.` would each claim the whole repository's routes,
                # which is a wrong per-service number. Counts are claimed only
                # for a context that names a subtree.
                if "api" in held and ctx:
                    n_routes = sum(
                        1
                        for r in graph.routes
                        if r.file in graph.architecture_paths and module_of(r.file) in mods
                    )
                    what.append(f"{n_routes} route{'s' if n_routes != 1 else ''}")
                if "worker" in held and ctx:
                    what.append("workers")
                sublabel = ("built from " + (ctx or ".") + ("/" if ctx else "")) + (
                    " · " + ", ".join(what) if what else ""
                )
            nodes.append(
                DiagramNode(
                    id=nid,
                    label=label,
                    kind=kind,
                    sublabel=sublabel,
                    evidence=node.evidence,
                    # No drill link yet: this diagram set holds only its own
                    # root, and a child_spec into another set would rightly
                    # fail navigability. The build context is recorded as a
                    # fact instead, so the panel can say which code a built
                    # service comes from; cross-diagram navigation is P2.
                    attrs=tuple(
                        (k, v)
                        for k, v in (
                            ("image", node.attr("image") or ""),
                            ("build_context", build_context or ""),
                        )
                        if v
                    ),
                )
            )

        # What each built service talks to outside the codebase, from the
        # imports of the code it builds: cloud APIs and stores compose does
        # not declare. Attached to the service that does the talking.
        stand_in: dict[str, str] = {}
        for svc, mods in modules_of_service.items():
            for m in mods:
                stand_in.setdefault(m, svc)
        ext_nodes, ext_edges = external_nodes_and_edges(graph, set(stand_in), stand_in)
        compose_labels = {n.label for n in nodes}
        ext_nodes = [n for n in ext_nodes if n.label not in compose_labels]
        keep = {n.id for n in ext_nodes}
        ext_edges = [e for e in ext_edges if e.dst in keep]
        nodes.extend(ext_nodes)
        edges = tuple(
            sorted(
                [
                    DiagramEdge(
                        src=e.src,
                        dst=e.dst,
                        label="depends on",
                        evidence=e.evidence,
                        variant="emphasis",
                    )
                    for e in graph.edges
                    if e.kind is EdgeKind.DEPENDS_ON and e.src in members and e.dst in members
                ]
                + ext_edges
            )
        )
        tally: dict[str, int] = {}
        for n in nodes:
            tally[n.kind] = tally.get(n.kind, 0) + 1
        subtitle = ", ".join(
            f"{count} {kind}{'s' if count != 1 else ''}"
            for kind, count in tally.items()
            if count
        )
        spec = DiagramSpec(
            kind=self.kind,
            id=ROOT,
            title=self.title,
            subtitle=subtitle,
            nodes=tuple(sorted(nodes)),
            edges=edges,
        )
        return DiagramSet(self.kind, ROOT, {ROOT: spec}, tuple(diags))
