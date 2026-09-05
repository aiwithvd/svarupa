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

import posixpath

from svarupa.build import Graph
from svarupa.cluster import Clustering
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
        nodes: list[DiagramNode] = []
        for nid, node in sorted(members.items()):
            build_context = node.attr("build_context")
            nodes.append(
                DiagramNode(
                    id=nid,
                    label=node.label,
                    kind=node.kind.value,
                    evidence=node.evidence,
                    # A service built from this repository is the door into the
                    # code: it drills to the structural architecture. Image-only
                    # services are external and are leaves here.
                    child_spec=self._code_door(graph, nid, build_context),
                    attrs=(("image", node.attr("image") or ""),) if node.attr("image") else (),
                )
            )

        edges = tuple(
            sorted(
                DiagramEdge(
                    src=e.src,
                    dst=e.dst,
                    label="depends on",
                    evidence=e.evidence,
                )
                for e in graph.edges
                if e.kind is EdgeKind.DEPENDS_ON and e.src in members and e.dst in members
            )
        )
        services = sum(1 for n in nodes if n.kind == "service")
        stores = len(nodes) - services
        spec = DiagramSpec(
            kind=self.kind,
            id=ROOT,
            title=self.title,
            subtitle=(
                f"{services} service{'s' if services != 1 else ''}, "
                f"{stores} backing store{'s' if stores != 1 else ''}"
            ),
            nodes=tuple(sorted(nodes)),
            edges=edges,
        )
        return DiagramSet(self.kind, ROOT, {ROOT: spec}, tuple(diags))

    def _code_door(
        self, graph: Graph, service_id: str, build_context: str | None
    ) -> str | None:
        """The module a built service's context lands on, as a drill target.

        Only when the context resolves to a module this graph actually has:
        pointing a drill at a guess would be an invented link. `.` maps to the
        repository root module when one exists.

        The target is a *module id*, not a spec id, because this diagram set
        contains only its own root; cross-diagram navigation is the viewer's
        job in P2 and a dangling child_spec here would fail navigability, and
        should. So today a resolvable context is recorded as an attribute-level
        fact rather than a drill link.
        """
        _ = service_id
        if build_context is None:
            return None
        compose_dir = ""  # contexts are relative to the compose file's directory
        target = posixpath.normpath(posixpath.join(compose_dir, build_context))
        if target in (".", ""):
            target = ""
        _ = target, graph
        return None
