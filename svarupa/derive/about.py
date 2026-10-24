"""One plain sentence per diagram box: what it is, what it does, how healthy.

Every clause is built from a fact the graph holds; a clause with no fact is
left out, never guessed. No AI: the sentences are fixed templates.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from svarupa.build import Graph, module_of, modules_shipped
from svarupa.derive.base import DiagramKind, DiagramNode, DiagramSet
from svarupa.health import BY_ID, Health, Violation
from svarupa.health.catalog import SEVERITIES
from svarupa.model import EdgeKind, NodeKind

__all__ = ["About", "about_lines"]

_FRAMEWORKS = {"fastapi": "FastAPI", "flask": "Flask", "express": "Express", "nestjs": "NestJS"}
_STORES = {
    "postgres": "PostgreSQL", "mysql": "MySQL", "mariadb": "MariaDB", "mongo": "MongoDB",
    "redis": "Redis", "elasticsearch": "Elasticsearch", "clickhouse": "ClickHouse",
    "cassandra": "Cassandra", "influxdb": "InfluxDB", "minio": "MinIO", "sqlite": "SQLite",
    "rabbitmq": "RabbitMQ", "kafka": "Kafka", "nats": "NATS", "activemq": "ActiveMQ",
    "mosquitto": "Mosquitto",
}  # fmt: skip
_CATEGORY = {
    "database": "database", "messagebus": "message bus", "cloud": "cloud service",
    "security": "security service", "frontend": "front-end library",
}  # fmt: skip


@dataclass(frozen=True, slots=True)
class About:
    text: str
    worst: str | None = None  # check id of the worst finding, for the passport link


def _n(count: int, word: str) -> str:
    return (
        f"{count} {word}"
        if count == 1
        else f"{count} {word}{'es' if word.endswith('s') else 's'}"
    )


def _and(items: Iterable[str]) -> str:
    xs = list(items)
    return xs[0] if len(xs) == 1 else ", ".join(xs[:-1]) + " and " + xs[-1]


def _module_line(graph: Graph, m: str) -> str:
    arch = graph.architecture_paths
    parts: list[str] = []
    routes = [r for r in graph.routes if r.file in arch and module_of(r.file) == m]
    if routes:
        fw = "/".join(_FRAMEWORKS.get(f, f) for f in sorted({r.framework for r in routes}))
        parts.append(f"serves {_n(len(routes), 'HTTP route')} ({fw})")
    tasks = [t for t in graph.tasks if t.file in arch and module_of(t.file) == m]
    if tasks:
        parts.append(f"runs {_n(len(tasks), 'background task')}")
    if any(e.file in arch and module_of(e.file) == m for e in graph.entrypoints):
        parts.append("starts the program")
    uses = len({d for s, d in graph.module_deps if s == m and d != m})
    used_by = len({s for s, d in graph.module_deps if d == m and s != m})
    if uses or used_by:
        parts.append(f"uses {_n(uses, 'module')} and is used by {used_by}")
    talks = sorted(
        {x.label for x in graph.externals if x.file in arch and module_of(x.file) == m}
    )
    if talks:
        parts.append(f"talks to {_and(talks)}")
    name = m or "."
    return f"Module {name}: {', '.join(parts)}." if parts else f"Module {name}."


def _store_name(image: str) -> str:
    base = image.rsplit("/", 1)[-1].split(":", 1)[0].lower()
    return next((v for k, v in _STORES.items() if base.startswith(k)), base)


def _users(graph: Graph, nid: str) -> list[str]:
    """Compose services that depend on or connect to this one."""
    compose_file, _, name = nid.partition("#service.")
    out: set[str] = set()
    for e in graph.edges:
        if e.kind is EdgeKind.DEPENDS_ON and e.dst == nid and e.src in graph.nodes:
            out.add(graph.nodes[e.src].label)
    for oid, node in graph.nodes.items():
        if oid.startswith(compose_file + "#service.") and name in (
            node.attr("connects") or ""
        ).split("\n"):
            out.add(node.label)
    return sorted(out)


def _compose_line(graph: Graph, nid: str) -> str:
    node = graph.nodes[nid]
    if node.kind in (NodeKind.DATASTORE, NodeKind.QUEUE):
        what = "database" if node.kind is NodeKind.DATASTORE else "queue"
        users = _users(graph, nid)
        used = f", used by {_and(users)}" if users else ""
        return f"{_store_name(node.attr('image') or node.label)} {what} (compose service {node.label}){used}."
    deps = sorted(
        graph.nodes[e.dst].label
        for e in graph.edges
        if e.kind is EdgeKind.DEPENDS_ON and e.src == nid and e.dst in graph.nodes
    )
    built = node.attr("build_context")
    head = (
        f"built from {built}"
        if built
        else (f"runs the image {node.attr('image')}" if node.attr("image") else "")
    )
    parts = [p for p in (head, f"depends on {_and(deps)}" if deps else "") if p]
    return f"Service {node.label}: {', '.join(parts)}." if parts else f"Service {node.label}."


def _class_line(graph: Graph, nid: str) -> str:
    node = graph.nodes[nid]
    file = nid.split("#", 1)[0]
    methods = sum(
        1
        for oid, n in graph.nodes.items()
        if n.kind is NodeKind.METHOD
        and oid.split("#", 1)[0] == file
        and n.attr("class") == node.label
    )
    bases = sorted(
        graph.nodes[e.dst].label
        for e in graph.edges
        if e.kind is EdgeKind.INHERITS and e.src == nid and e.dst in graph.nodes
    )
    parts = [
        p
        for p in (
            f"with {_n(methods, 'method')}" if methods else "",
            f"based on {_and(bases)}" if bases else "",
        )
        if p
    ]
    return f"Class {node.label} {', '.join(parts)}." if parts else f"Class {node.label}."


def _file_line(graph: Graph, path: str) -> str:
    counts: dict[NodeKind, int] = {}
    for oid, n in graph.nodes.items():
        if oid.split("#", 1)[0] == path and "#" in oid:
            counts[n.kind] = counts.get(n.kind, 0) + 1
    parts = [
        _n(counts[k], word)
        for k, word in (
            (NodeKind.CLASS, "class"),
            (NodeKind.INTERFACE, "interface"),
            (NodeKind.FUNCTION, "function"),
        )
        if counts.get(k)
    ]
    return f"File {path}: defines {_and(parts)}." if parts else f"File {path}."


def _describe(graph: Graph, box: DiagramNode) -> str:
    bid = box.id
    members = box.attr("members")
    if members:
        ms = members.split("\n")
        return f"Group of {_n(len(ms), 'module')}: {_and(ms[:3])}{' and others' if len(ms) > 3 else ''}."
    if bid.startswith("ext:"):
        category = box.attr("external") or bid.split(":", 2)[1]
        mods = {
            module_of(x.file)
            for x in graph.externals
            if x.label == box.label and x.file in graph.architecture_paths
        }
        return f"External {_CATEGORY.get(category, category)} ({box.label}), used from {_n(len(mods), 'module')}."
    if bid.startswith(("in:", "req:")):
        m = bid.split(":", 1)[1]
        m = "" if m == "." else m
        routes = sorted(
            (r.method, r.path)
            for r in graph.routes
            if r.file in graph.architecture_paths and module_of(r.file) == m
        )
        sample = ", ".join(f"{a} {b}" for a, b in routes[:3])
        return f"HTTP entry to {m or '.'}: {_n(len(routes), 'route')}" + (
            f", for example {sample}." if sample else "."
        )
    if bid.startswith("env:"):
        return f"Deploy environment {box.label}."
    module = "" if bid == "." else bid
    if module in graph.modules and bid not in graph.nodes:
        return _module_line(graph, module)
    node = graph.nodes.get(bid)
    if node is not None:
        if (
            node.kind in (NodeKind.SERVICE, NodeKind.DATASTORE, NodeKind.QUEUE)
            and "#service." in bid
        ):
            return _compose_line(graph, bid)
        if node.kind is NodeKind.CLASS:
            return _class_line(graph, bid)
        if "#" not in bid:
            return _file_line(graph, bid)
    kind = box.kind.replace("_", " ") if box.kind else "Box"
    return f"{kind[:1].upper()}{kind[1:]} {box.label}."


def _findings(graph: Graph, health: Health, box: DiagramNode) -> list[Violation]:
    bid = box.id
    vs = health.violations
    members = box.attr("members")
    if members:
        ms = {"" if m == "." else m for m in members.split("\n")}
        return [v for v in vs if v.module in ms]
    module = "" if bid == "." else bid
    if module in graph.modules and bid not in graph.nodes:
        return [v for v in vs if v.module == module]
    if bid.startswith(("in:", "req:")):
        m = bid.split(":", 1)[1]
        files = {r.file for r in graph.routes if module_of(r.file) == ("" if m == "." else m)}
        return [v for v in vs if v.evidence[0].file in files]
    node = graph.nodes.get(bid)
    if node is not None and node.kind is NodeKind.SERVICE and node.attr("build_context"):
        shipped = modules_shipped(graph, bid)
        if shipped is not None:
            return [v for v in vs if v.module in shipped[0]]
    if node is not None and node.kind in (NodeKind.DATASTORE, NodeKind.QUEUE) and node.evidence:
        line = (node.evidence[0].file, node.evidence[0].start_line)
        return [v for v in vs if any((e.file, e.start_line) == line for e in v.evidence)]
    if node is not None and "#" in bid:  # a definition: only its own findings
        return [v for v in vs if v.symbol == bid]
    if bid in graph.nodes and "#" not in bid:  # a file box
        return [v for v in vs if v.evidence[0].file == bid]
    files = {e.file for e in box.evidence}
    return [v for v in vs if v.evidence[0].file in files]


def _health_clause(found: list[Violation]) -> tuple[str, str | None]:
    if not found:
        return "No health findings.", None
    worst = min(
        found,
        key=lambda v: (-SEVERITIES.index(BY_ID[v.check].severity), -v.impact, v.check),
    )
    check = BY_ID[worst.check]
    return (
        f"{_n(len(found), 'health finding')}, worst: {check.title} ({check.severity}).",
        worst.check,
    )


def about_lines(
    graph: Graph, health: Health | None, produced: Mapping[DiagramKind, DiagramSet]
) -> dict[str, About]:
    boxes: dict[str, DiagramNode] = {}
    for kind in sorted(produced, key=lambda k: k.value):
        for spec_id in sorted(produced[kind].specs):
            for box in produced[kind].specs[spec_id].nodes:
                boxes.setdefault(box.id, box)
    out: dict[str, About] = {}
    for bid in sorted(boxes):
        text = _describe(graph, boxes[bid])
        if health is None:
            out[bid] = About(text)
            continue
        clause, worst = _health_clause(_findings(graph, health, boxes[bid]))
        out[bid] = About(f"{text} {clause}", worst)
    return out
