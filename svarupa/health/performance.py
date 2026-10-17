"""Performance checks that read the graph: datastores shared by services and
hubs that every request path goes through."""

from __future__ import annotations

from svarupa.build import Graph, build_context_of
from svarupa.extract.base import module_of
from svarupa.health.catalog import BY_ID
from svarupa.health.model import Violation
from svarupa.model import EdgeKind, NodeKind

__all__ = ["shared_datastores"]


def _app(graph: Graph, nid: str) -> bool:
    """A compose service built from this repository (not an image like adminer)."""
    node = graph.nodes.get(nid)
    return (
        node is not None and node.kind is NodeKind.SERVICE and bool(node.attr("build_context"))
    )


def _datastore(graph: Graph, nid: str) -> bool:
    node = graph.nodes.get(nid)
    return node is not None and node.kind is NodeKind.DATASTORE


def shared_datastores(graph: Graph) -> list[Violation]:
    """A datastore that services from two or more codebases use."""
    check = BY_ID["shared-datastore"]
    users: dict[str, set[str]] = {}
    for e in graph.edges:
        if e.kind is EdgeKind.DEPENDS_ON and _app(graph, e.src) and _datastore(graph, e.dst):
            users.setdefault(e.dst, set()).add(e.src)
    for nid in sorted(graph.nodes):
        if not _app(graph, nid):
            continue
        compose_file = nid.split("#", 1)[0]
        for name in (graph.nodes[nid].attr("connects") or "").split("\n"):
            dst = f"{compose_file}#service.{name}"
            if name and _datastore(graph, dst):
                users.setdefault(dst, set()).add(nid)
    out: list[Violation] = []
    for dst, apps in sorted(users.items()):
        codebases = {
            build_context_of(graph, a) or graph.nodes[a].attr("build_context") for a in apps
        }
        if len(codebases) <= check.threshold:
            continue
        store = graph.nodes[dst]
        names = sorted(graph.nodes[a].label for a in apps)
        ordered = sorted(apps, key=lambda a: graph.nodes[a].evidence[0].start_line)
        out.append(
            Violation(
                check="shared-datastore",
                message=(
                    f"{store.label} is used by {len(names)} services ({', '.join(names)}); "
                    "each service should own its data so one cannot slow or break another "
                    "(Newman, Building Microservices)"
                ),
                evidence=(store.evidence[0], *(graph.nodes[a].evidence[0] for a in ordered)),
                module=module_of(dst.split("#", 1)[0]),
                value=len(codebases),
                minutes=check.minutes,
            )
        )
    return out
