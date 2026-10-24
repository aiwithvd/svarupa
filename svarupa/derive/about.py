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


class _Index:
    """Every lookup the sentences need, built once: one pass over the nodes,
    the edges and the facts, so a large repository stays linear."""

    def __init__(self, graph: Graph) -> None:
        self.graph = graph
        self.file_kinds: dict[str, dict[NodeKind, int]] = {}
        self.class_methods: dict[tuple[str, str], int] = {}
        self.members_of: dict[str, list[str]] = {}
        self.bases: dict[str, list[str]] = {}
        self.deps_out: dict[str, list[str]] = {}
        self.users: dict[str, set[str]] = {}
        # module -> (method, path, framework)
        self.routes: dict[str, list[tuple[str, str, str]]] = {}
        self.route_files: dict[str, set[str]] = {}
        self.tasks: dict[str, int] = {}
        self.entries: set[str] = set()
        self.talks: dict[str, set[str]] = {}
        self.ext_modules: dict[str, set[str]] = {}
        self.uses: dict[str, set[str]] = {}
        self.used_by: dict[str, set[str]] = {}
        self.by_module: dict[str, list[Violation]] = {}
        self.by_file: dict[str, list[Violation]] = {}
        self.by_symbol: dict[str, list[Violation]] = {}
        self.by_line: dict[tuple[str, int], list[Violation]] = {}


def _index(graph: Graph, health: Health | None) -> _Index:
    ix = _Index(graph)
    arch = graph.architecture_paths
    connects: list[tuple[str, str, str]] = []  # (compose file, service label, target name)
    for oid, n in graph.nodes.items():
        if "#service." in oid:
            for name in (n.attr("connects") or "").split("\n"):
                if name:
                    connects.append((oid.split("#", 1)[0], n.label, name))
        if "#" not in oid:
            continue
        file = oid.split("#", 1)[0]
        kinds = ix.file_kinds.setdefault(file, {})
        kinds[n.kind] = kinds.get(n.kind, 0) + 1
        if n.kind is NodeKind.METHOD and n.attr("class"):
            key = (file, n.attr("class") or "")
            ix.class_methods[key] = ix.class_methods.get(key, 0) + 1
        parent = oid.rsplit(".", 1)[0]
        if parent != oid:
            ix.members_of.setdefault(parent, []).append(oid)
    for file, label, name in connects:
        ix.users.setdefault(f"{file}#service.{name}", set()).add(label)
    for e in graph.edges:
        if e.kind is EdgeKind.INHERITS and e.dst in graph.nodes:
            ix.bases.setdefault(e.src, []).append(graph.nodes[e.dst].label)
        elif e.kind is EdgeKind.DEPENDS_ON and e.dst in graph.nodes and e.src in graph.nodes:
            ix.deps_out.setdefault(e.src, []).append(graph.nodes[e.dst].label)
            ix.users.setdefault(e.dst, set()).add(graph.nodes[e.src].label)
    for r in graph.routes:
        if r.file in arch:
            m = module_of(r.file)
            ix.routes.setdefault(m, []).append((r.method, r.path, r.framework))
            ix.route_files.setdefault(m, set()).add(r.file)
    for t in graph.tasks:
        if t.file in arch:
            ix.tasks[module_of(t.file)] = ix.tasks.get(module_of(t.file), 0) + 1
    ix.entries = {module_of(e.file) for e in graph.entrypoints if e.file in arch}
    for x in graph.externals:
        if x.file in arch:
            ix.talks.setdefault(module_of(x.file), set()).add(x.label)
            ix.ext_modules.setdefault(x.label, set()).add(module_of(x.file))
    for s, d in graph.module_deps:
        if s != d:
            ix.uses.setdefault(s, set()).add(d)
            ix.used_by.setdefault(d, set()).add(s)
    for v in health.violations if health is not None else ():
        ix.by_module.setdefault(v.module, []).append(v)
        ix.by_file.setdefault(v.evidence[0].file, []).append(v)
        if v.symbol:
            ix.by_symbol.setdefault(v.symbol, []).append(v)
        for ev in {(e.file, e.start_line) for e in v.evidence}:
            ix.by_line.setdefault(ev, []).append(v)
    return ix


def _module_line(ix: _Index, m: str) -> str:
    parts: list[str] = []
    routes = ix.routes.get(m, [])
    if routes:
        fw = "/".join(_FRAMEWORKS.get(f, f) for f in sorted({r[2] for r in routes}))
        parts.append(f"serves {_n(len(routes), 'HTTP route')} ({fw})")
    if ix.tasks.get(m):
        parts.append(f"runs {_n(ix.tasks[m], 'background task')}")
    if m in ix.entries:
        parts.append("starts the program")
    uses, used_by = len(ix.uses.get(m, ())), len(ix.used_by.get(m, ()))
    if uses or used_by:
        parts.append(f"uses {_n(uses, 'module')} and is used by {used_by}")
    talks = sorted(ix.talks.get(m, ()))
    if talks:
        parts.append(f"talks to {_and(talks)}")
    name = m or "."
    return f"Module {name}: {', '.join(parts)}." if parts else f"Module {name}."


def _store_name(image: str) -> str:
    base = image.rsplit("/", 1)[-1].split(":", 1)[0].lower()
    return next((v for k, v in _STORES.items() if base.startswith(k)), base)


def _compose_line(ix: _Index, nid: str) -> str:
    node = ix.graph.nodes[nid]
    if node.kind in (NodeKind.DATASTORE, NodeKind.QUEUE):
        what = "database" if node.kind is NodeKind.DATASTORE else "queue"
        users = sorted(ix.users.get(nid, ()))
        used = f", used by {_and(users)}" if users else ""
        name = _store_name(node.attr("image") or node.label)
        return f"{name} {what} (compose service {node.label}){used}."
    deps = sorted(ix.deps_out.get(nid, []))
    built, image = node.attr("build_context"), node.attr("image")
    head = f"built from {built}" if built else (f"runs the image {image}" if image else "")
    parts = [p for p in (head, f"depends on {_and(deps)}" if deps else "") if p]
    return f"Service {node.label}: {', '.join(parts)}." if parts else f"Service {node.label}."


def _class_line(ix: _Index, nid: str) -> str:
    node = ix.graph.nodes[nid]
    methods = ix.class_methods.get((nid.split("#", 1)[0], node.label), 0)
    bases = sorted(ix.bases.get(nid, []))
    parts = [
        p
        for p in (
            f"with {_n(methods, 'method')}" if methods else "",
            f"based on {_and(bases)}" if bases else "",
        )
        if p
    ]
    return f"Class {node.label} {', '.join(parts)}." if parts else f"Class {node.label}."


def _file_line(ix: _Index, path: str) -> str:
    counts = ix.file_kinds.get(path, {})
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


def _entry_module(bid: str) -> str:
    m = bid.split(":", 1)[1]
    return "" if m == "." else m


def _describe_in(ix: _Index, box: DiagramNode) -> str:
    graph, bid = ix.graph, box.id
    members = box.attr("members")
    if members:
        ms = [m or "." for m in members.split("\n")]
        more = f" and {_n(len(ms) - 3, 'other')}" if len(ms) > 3 else ""
        listed = _and(ms[:3]) if not more else ", ".join(ms[:3])
        return f"Group of {_n(len(ms), 'module')}: {listed}{more}."
    if bid.startswith("ext:"):
        category = box.attr("external") or bid.split(":", 2)[1]
        mods = ix.ext_modules.get(box.label, set())
        return (
            f"External {_CATEGORY.get(category, category)} ({box.label}), "
            f"used from {_n(len(mods), 'module')}."
        )
    if bid.startswith(("in:", "req:")):
        m = _entry_module(bid)
        routes = ix.routes.get(m, [])
        sample = ", ".join(f"{a} {b}" for a, b in sorted({(r[0], r[1]) for r in routes})[:3])
        return f"HTTP entry to {m or '.'}: {_n(len(routes), 'route')}" + (
            f", for example {sample}." if sample else "."
        )
    if bid.startswith("env:"):
        return f"Deploy environment {box.label}."
    module = "" if bid == "." else bid
    if module in graph.modules and bid not in graph.nodes:
        return _module_line(ix, module)
    node = graph.nodes.get(bid)
    if node is not None:
        if (
            node.kind in (NodeKind.SERVICE, NodeKind.DATASTORE, NodeKind.QUEUE)
            and "#service." in bid
        ):
            return _compose_line(ix, bid)
        if node.kind is NodeKind.CLASS:
            return _class_line(ix, bid)
        if "#" not in bid:
            return _file_line(ix, bid)
    kind = box.kind.replace("_", " ") if box.kind else "Box"
    return f"{kind[:1].upper()}{kind[1:]} {box.label}."


def _findings_in(ix: _Index, box: DiagramNode) -> list[Violation] | None:
    """The violations a box owns; None when health says nothing about it (an
    external system or an environment is not code this repository holds)."""
    graph, bid = ix.graph, box.id
    members = box.attr("members")
    if members:
        return [v for m in members.split("\n") for v in ix.by_module.get(m, [])]
    if bid.startswith(("ext:", "env:")):
        return None
    module = "" if bid == "." else bid
    if module in graph.modules and bid not in graph.nodes:
        return list(ix.by_module.get(module, []))
    if bid.startswith(("in:", "req:")):
        files = ix.route_files.get(_entry_module(bid), set())
        return [v for f in sorted(files) for v in ix.by_file.get(f, [])]
    node = graph.nodes.get(bid)
    if node is not None and node.kind is NodeKind.SERVICE and node.attr("build_context"):
        shipped = modules_shipped(graph, bid)
        if shipped is not None:
            return [v for m in sorted(shipped[0]) for v in ix.by_module.get(m, [])]
    if node is not None and node.kind in (NodeKind.DATASTORE, NodeKind.QUEUE) and node.evidence:
        return list(ix.by_line.get((node.evidence[0].file, node.evidence[0].start_line), []))
    if node is not None and "#" in bid:  # a definition: its own findings, a class its members'
        ids = [bid, *(ix.members_of.get(bid, []) if node.kind is NodeKind.CLASS else [])]
        return [v for i in ids for v in ix.by_symbol.get(i, [])]
    if node is not None:  # a file box
        return list(ix.by_file.get(bid, []))
    files = sorted({e.file for e in box.evidence})
    return [v for f in files for v in ix.by_file.get(f, [])]


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
    ix = _index(graph, health)
    out: dict[str, About] = {}
    for bid in sorted(boxes):
        text = _describe_in(ix, boxes[bid])
        found = _findings_in(ix, boxes[bid]) if health is not None else None
        if found is None:
            out[bid] = About(text)
            continue
        clause, worst = _health_clause(found)
        out[bid] = About(f"{text} {clause}", worst)
    return out
