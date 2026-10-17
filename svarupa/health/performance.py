"""Performance checks that read the graph: datastores shared by services and
hubs that every request path goes through."""

from __future__ import annotations

import networkx as nx

from svarupa.build import Graph, build_context_of
from svarupa.extract.base import module_of
from svarupa.health.catalog import BY_ID
from svarupa.health.model import Violation
from svarupa.model import EdgeKind, Evidence, NodeKind

__all__ = ["hub_on_request_path", "shared_datastores"]


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


def _first_imports(graph: Graph, module: str) -> tuple[Evidence, ...]:
    """The module's own import lines, in file and line order, at most five."""
    lines = {
        e.evidence[0]
        for e in graph.edges
        if e.kind is EdgeKind.IMPORTS and e.evidence and module_of(e.src) == module
    }
    return tuple(sorted(lines, key=lambda ev: (ev.file, ev.start_line, ev.end_line))[:5])


def _reach(uses: dict[str, set[str]], start: str) -> set[str]:
    """Every module `start` imports, directly or through others, and itself."""
    seen, stack = {start}, [start]
    while stack:
        for dst in uses.get(stack.pop(), ()):
            if dst not in seen:
                seen.add(dst)
                stack.append(dst)
    return seen


def hub_on_request_path(graph: Graph) -> list[Violation]:
    """A hub module that every route module reaches through its imports."""
    check, hub = BY_ID["hub-on-request-path"], BY_ID["hub-module"]
    route_modules = sorted({module_of(r.file) for r in graph.routes})
    if len(route_modules) <= check.threshold:
        return []
    g: nx.DiGraph[str] = nx.DiGraph()
    g.add_nodes_from(route_modules)
    g.add_edges_from(graph.module_deps)
    uses: dict[str, set[str]] = {}
    for src, dst in graph.module_deps:
        uses.setdefault(src, set()).add(dst)
    common = _reach(uses, route_modules[0])
    for m in route_modules[1:]:
        common &= _reach(uses, m)
    out: list[Violation] = []
    for module in sorted(common):
        fan_in, fan_out = g.in_degree(module), g.out_degree(module)
        if fan_in <= hub.threshold or fan_out <= hub.threshold:
            continue
        cited = _first_imports(graph, module)
        if not cited:
            continue
        out.append(
            Violation(
                check="hub-on-request-path",
                message=(
                    f"{module or '.'} is a hub (used by {fan_in} modules, uses {fan_out}) "
                    f"on every request path, from {len(route_modules)} route modules; "
                    "a slow or broken change there reaches every endpoint"
                ),
                evidence=cited,
                module=module,
                value=len(route_modules),
                minutes=check.minutes,
            )
        )
    return out
