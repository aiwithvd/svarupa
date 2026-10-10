"""The checks that read the graph and the function metrics."""

from __future__ import annotations

from collections.abc import Mapping

import networkx as nx

from svarupa.build import Graph
from svarupa.extract.base import module_of
from svarupa.health.catalog import BY_ID
from svarupa.health.model import Violation
from svarupa.model import EdgeKind, Evidence

__all__ = ["class_checks", "file_checks", "function_checks", "module_checks"]


def _symbol_ids(graph: Graph) -> dict[tuple[str, int, str], str]:
    """(file, start line, label) -> node id, to link a measured function to
    the definition the diagrams draw."""
    out: dict[tuple[str, int, str], str] = {}
    for nid, node in graph.nodes.items():
        if "#" in nid and node.evidence:
            ev = node.evidence[0]
            out.setdefault((ev.file, ev.start_line, node.label), nid)
    return out


def function_checks(graph: Graph) -> list[Violation]:
    ids = _symbol_ids(graph)
    out: list[Violation] = []
    for fm in graph.functions:
        ev = fm.evidence
        symbol = ids.get((ev.file, ev.start_line, fm.name))
        for check_id, value, what in (
            ("complex-function", fm.complexity, "cyclomatic complexity"),
            ("long-function", fm.lines, "lines"),
            ("many-parameters", fm.params, "parameters"),
            ("deep-nesting", fm.nesting, "nesting levels"),
        ):
            check = BY_ID[check_id]
            if value <= check.threshold:
                continue
            out.append(
                Violation(
                    check=check_id,
                    message=f"{fm.name} has {value} {what} (limit {check.threshold})",
                    evidence=(ev,),
                    module=module_of(ev.file),
                    value=value,
                    minutes=check.minutes + check.per_unit * (value - check.threshold),
                    symbol=symbol,
                )
            )
    return out


def file_checks(texts: Mapping[str, str]) -> list[Violation]:
    check = BY_ID["large-file"]
    out: list[Violation] = []
    for path, text in sorted(texts.items()):
        lines = len(text.splitlines())
        if lines > check.threshold:
            out.append(
                Violation(
                    check="large-file",
                    message=f"{path} has {lines} lines (limit {check.threshold})",
                    evidence=(Evidence(path, 1, lines),),
                    module=module_of(path),
                    value=lines,
                    minutes=check.minutes,
                )
            )
    return out


def class_checks(graph: Graph) -> list[Violation]:
    """WMC: the sum of the complexity of a class's methods."""
    check = BY_ID["large-class"]
    owners: dict[tuple[str, str], int] = {}
    ids = _symbol_ids(graph)
    for fm in graph.functions:
        nid = ids.get((fm.evidence.file, fm.evidence.start_line, fm.name))
        node = graph.nodes.get(nid) if nid else None
        cls = dict(node.attrs).get("class") if node is not None else None
        if cls:
            key = (fm.evidence.file, cls)
            owners[key] = owners.get(key, 0) + fm.complexity
    classes = {
        (node.evidence[0].file, node.label): (nid, node.evidence[0])
        for nid, node in graph.nodes.items()
        if node.kind.value == "class" and node.evidence
    }
    out: list[Violation] = []
    for (file, cls), wmc in sorted(owners.items()):
        if wmc <= check.threshold or (file, cls) not in classes:
            continue
        nid, ev = classes[(file, cls)]
        out.append(
            Violation(
                check="large-class",
                message=f"{cls} has methods with total complexity {wmc} (limit {check.threshold})",
                evidence=(ev,),
                module=module_of(file),
                value=wmc,
                minutes=check.minutes,
                symbol=nid,
            )
        )
    return out


def _line_order(ev: Evidence) -> tuple[str, int, int]:
    return (ev.file, ev.start_line, ev.end_line)


def module_checks(graph: Graph) -> list[Violation]:
    out: list[Violation] = []
    g: nx.DiGraph[str] = nx.DiGraph()
    g.add_edges_from(graph.module_deps)
    imports = sorted(
        (e for e in graph.edges if e.kind is EdgeKind.IMPORTS and e.evidence),
        key=lambda e: (e.src, e.dst),
    )
    cycle = BY_ID["module-cycle"]
    for scc in sorted(sorted(c) for c in nx.strongly_connected_components(g) if len(c) > 1):
        members = set(scc)
        # Earliest import first, file by file: the line a reader starts from.
        cited = sorted(
            {
                e.evidence[0]
                for e in imports
                if module_of(e.src) in members
                and module_of(e.dst) in members
                and module_of(e.src) != module_of(e.dst)
            },
            key=_line_order,
        )
        if not cited:
            continue
        out.append(
            Violation(
                check="module-cycle",
                message="modules import each other in a cycle: " + " -> ".join(scc),
                evidence=tuple(cited[:5]),
                module=scc[0],
                value=len(scc),
                minutes=cycle.minutes,
            )
        )
    hub = BY_ID["hub-module"]
    for module in sorted(g.nodes):
        fan_in, fan_out = g.in_degree(module), g.out_degree(module)
        if fan_in > hub.threshold and fan_out > hub.threshold:
            cited = sorted(
                {e.evidence[0] for e in imports if module_of(e.src) == module}, key=_line_order
            )[:5]
            if cited:
                out.append(
                    Violation(
                        check="hub-module",
                        message=(
                            f"{module or '.'} is used by {fan_in} modules and uses {fan_out}"
                        ),
                        evidence=tuple(cited),
                        module=module,
                        value=fan_in + fan_out,
                        minutes=hub.minutes,
                    )
                )
    return out
