"""System-level styles, labelled from units, compose services and queues."""

from __future__ import annotations

from pathlib import Path

from svarupa.build import Graph
from svarupa.design.model import Unit
from svarupa.detect import Scan
from svarupa.model import NodeKind

__all__ = ["classify_system"]


def classify_system(
    scan: Scan, graph: Graph, units: list[Unit]
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    backends = [u for u in units if u.level == "backend"]
    frontends = [u for u in units if u.level == "frontend"]
    services = sorted(n.label for n in graph.nodes.values() if n.kind is NodeKind.SERVICE)
    queues = sorted(n.label for n in graph.nodes.values() if n.kind is NodeKind.QUEUE)
    buses = sorted({x.file for x in graph.externals if x.category == "messagebus"})
    files = {Path(r.path).name for r in scan.files}
    styles: list[str] = []
    why: list[str] = []
    if len(backends) >= 2 and len(services) >= 2:
        styles.append("microservices")
        why.append(f"{len(backends)} backend units and compose services {', '.join(services)}")
    if queues or len(buses) >= 2:
        styles.append("event-driven")
        why.append(
            "message queues " + ", ".join(queues)
            if queues
            else f"message bus clients in {len(buses)} files"
        )
    if files & {"serverless.yml", "serverless.yaml"}:
        styles.append("serverless")
        why.append("serverless.yml declares functions")
    if len(frontends) >= 2:
        styles.append("micro-frontends")
        why.append(f"{len(frontends)} frontend units")
    if frontends and len(backends) >= 2 and len(frontends) >= len(backends) - 1:
        styles.append("bff")
        why.append(f"{len(frontends)} frontend units served by {len(backends)} backend units")
    if not styles:
        deployable = backends or units
        if (
            len(deployable) == 1
            and len(
                {
                    m.split("/")[len(deployable[0].id.split("/")) if deployable[0].id else 0]
                    for m in deployable[0].modules
                    if m
                }
            )
            >= 3
        ):
            styles.append("modular-monolith")
            why.append(f"one deployable unit with {len(deployable[0].modules)} modules")
        else:
            styles.append("monolith")
            why.append(f"{len(units)} unit(s), one deployable")
    return tuple(styles), tuple(why)
