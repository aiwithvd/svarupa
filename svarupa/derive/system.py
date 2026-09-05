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
        # Two compose files can both define a `web`. Ids stay distinct, and the
        # human-facing label gains the file's directory when names collide,
        # because two identical boxes force the reader to click to tell them
        # apart, which is the label failing at its one job.
        by_label: dict[str, int] = {}
        for node in members.values():
            by_label[node.label] = by_label.get(node.label, 0) + 1

        nodes: list[DiagramNode] = []
        for nid, node in sorted(members.items()):
            build_context = node.attr("build_context")
            label = node.label
            if by_label[label] > 1:
                where = nid.split("#", 1)[0].rsplit("/", 1)[0] or "."
                label = f"{label} ({where})"
            nodes.append(
                DiagramNode(
                    id=nid,
                    label=label,
                    kind=node.kind.value,
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
        tally = {"service": 0, "datastore": 0, "queue": 0}
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
