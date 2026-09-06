"""Data flow and request flow: where data enters, is handled, and lands.

Archify's `dataflow` type reads left to right through named stages. Ours is
derived from facts already extracted, and every stage is a claim with a line
behind it:

* **Ingress**: the routes, grouped by the module that declares them (the
  decorator or route-call lines).
* **Handlers**: the api modules themselves.
* **Domain**: the modules the handlers import, transitively to a small depth
  (the import lines).
* **Storage / External**: the stores, buses and cloud APIs any of those
  modules talk to (the import lines the vocabulary classified).

A module reachable from no handler is in no stage and is not drawn; the
diagnostic says how many. Stage frames are `Region`s, so the same geometry
check that guards service boundaries guards them.

**Request flow** is the same story for ONE endpoint group: the handler
module, what it reaches by import, hop by hop, and what those reach outside.
It is drawn in this notation, not as a sequence diagram: import depth is
reachability, not temporal order (design section 5, Spike 0b), and sequence
notation would imply an ordering the evidence does not carry.
"""

from __future__ import annotations

from svarupa.build import Graph, module_of, module_roles
from svarupa.cluster import Clustering
from svarupa.derive.architecture import (
    ArchitectureDeriver,
    external_nodes_and_edges,
    labels_for,
    module_sublabel,
)
from svarupa.derive.base import (
    MAX_EVIDENCE_PER_BOX,
    ROOT,
    Deriver,
    DiagramEdge,
    DiagramKind,
    DiagramNode,
    DiagramSet,
    DiagramSpec,
    Region,
    module_evidence,
    runtime_edges,
    spec_id,
)
from svarupa.diagnostics import Diagnostic, Severity
from svarupa.model import Evidence

__all__ = ["DataFlowDeriver", "RequestFlowDeriver"]

# How far a handler's imports are followed into the domain. Two hops is where
# import chains on services stop carrying meaning about the request (Spike
# 0b measured import depth 3-10 on libraries, far less on services); deeper
# hops become the whole codebase.
DOMAIN_DEPTH = 2

_STAGES = ("Ingress", "Handlers", "Domain", "Storage / External")


def _imports_of(graph: Graph) -> dict[str, list[tuple[str, tuple[Evidence, ...]]]]:
    """module -> [(imported module, evidence)] from the runtime import pairs."""
    out: dict[str, list[tuple[str, tuple[Evidence, ...]]]] = {}
    pairs, _ = runtime_edges(graph)
    for a, b, _w, ev in pairs:
        out.setdefault(a, []).append((b, tuple(ev)))
    return out


def _reach(
    start: set[str], imports: dict[str, list[tuple[str, tuple[Evidence, ...]]]], depth: int
) -> dict[str, int]:
    """Modules reachable by import from `start`, with their hop count."""
    hops = dict.fromkeys(start, 0)
    frontier = set(start)
    for hop in range(1, depth + 1):
        nxt: set[str] = set()
        for m in sorted(frontier):
            for target, _ev in imports.get(m, []):
                if target not in hops:
                    hops[target] = hop
                    nxt.add(target)
        frontier = nxt
    return hops


def _ingress_node(graph: Graph, module: str) -> DiagramNode | None:
    routes = sorted(
        r
        for r in graph.routes
        if r.file in graph.architecture_paths and module_of(r.file) == module
    )
    if not routes:
        return None
    paths = sorted({r.path for r in routes})
    shown = ", ".join(paths[:3]) + (" …" if len(paths) > 3 else "")
    return DiagramNode(
        id=f"in:{module}",
        label=shown if len(shown) <= 40 else shown[:37] + "…",
        kind="endpoint",
        evidence=tuple(sorted({r.evidence for r in routes}))[:MAX_EVIDENCE_PER_BOX],
        attrs=(("layer", "0"), ("stage", "Ingress")),
        sublabel=f"{len(routes)} route{'s' if len(routes) != 1 else ''}",
    )


def _module_node(
    graph: Graph,
    module: str,
    label: str,
    layer: int,
    stage: str,
    roles: dict[str, tuple[str, ...]],
) -> DiagramNode | None:
    ev = module_evidence(graph, module)
    if not ev:
        return None
    held = roles.get(module, ())
    kind = (
        "frontend"
        if "frontend" in held
        else "backend"
        if {"api", "worker", "cli"} & set(held)
        else "security"
        if "auth" in held
        else "module"
    )
    return DiagramNode(
        id=module,
        label=label,
        kind=kind,
        evidence=ev,
        attrs=(("layer", str(layer)), ("stage", stage))
        + ((("roles", ",".join(held)),) if held else ()),
        sublabel=module_sublabel(graph, module),
    )


def _against_note(count: int) -> str:
    if not count:
        return ""
    return f"; {count} import{'s' if count != 1 else ''} against the flow not drawn"


def _stage_regions(nodes: list[DiagramNode], stage_ev: Evidence) -> tuple[Region, ...]:
    """One frame per stage that has members, labelled `01 / Ingress` like
    Archify's stage headers. Evidence is the first member's first line: the
    frame claims only that these boxes are in this stage."""
    out: list[Region] = []
    for i, stage in enumerate(_STAGES):
        members = tuple(sorted(n.id for n in nodes if n.attr("stage") == stage))
        if members:
            first = next(n for n in nodes if n.id == members[0])
            out.append(
                Region(
                    id=f"stage:{i}",
                    label=f"0{i + 1} / {stage}",
                    members=members,
                    evidence=first.evidence[:1] or (stage_ev,),
                    kind="stage",
                )
            )
    return tuple(out)


class DataFlowDeriver(Deriver):
    kind = DiagramKind.DATA_FLOW
    title = "Data flow"

    def derive(self, graph: Graph, clustering: Clustering) -> DiagramSet | None:
        _ = clustering
        roles = module_roles(graph)
        handlers = sorted(m for m, rs in roles.items() if "api" in rs and m in graph.modules)
        if not handlers:
            return DiagramSet(
                self.kind,
                ROOT,
                {},
                (
                    self.unavailable(
                        "no module declares routes, so there is no ingress to follow data from",
                        fixes=(
                            "Routes come from FastAPI, Flask, Express or NestJS declarations.",
                        ),
                    ),
                ),
            )
        imports = _imports_of(graph)
        hops = _reach(set(handlers), imports, DOMAIN_DEPTH)
        domain = sorted(m for m, h in hops.items() if h > 0 and m in graph.modules)
        names = labels_for([*handlers, *domain])
        nodes: list[DiagramNode] = []
        diags: list[Diagnostic] = []
        specs: dict[str, DiagramSpec] = {}
        arch = ArchitectureDeriver()
        for m in handlers:
            ingress = _ingress_node(graph, m)
            handler = _module_node(graph, m, names[m], 1, "Handlers", roles)
            if ingress is None or handler is None:
                continue
            nodes.append(ingress)
            nodes.append(
                DiagramNode(
                    id=handler.id,
                    label=handler.label,
                    kind=handler.kind,
                    evidence=handler.evidence,
                    attrs=handler.attrs,
                    sublabel=handler.sublabel,
                    child_spec=arch.components_for(graph, m, ROOT, specs, diags),
                )
            )
        # One column per hop, so a store two imports away sits right of the
        # module that reaches it instead of stacked in the same column.
        for m in domain:
            node = _module_node(graph, m, names[m], 1 + hops[m], "Domain", roles)
            if node is not None:
                nodes.append(
                    DiagramNode(
                        id=node.id,
                        label=node.label,
                        kind=node.kind,
                        evidence=node.evidence,
                        attrs=(*node.attrs, ("hop", str(hops[m]))),
                        sublabel=node.sublabel,
                        child_spec=arch.components_for(graph, m, ROOT, specs, diags),
                    )
                )
        drawn = {n.id for n in nodes}
        # The column after the deepest hop actually drawn; a fixed column
        # left an empty one before it and forced every external edge
        # through the corridor.
        ext_layer = 2 + max((hops[m] for m in domain if m in drawn), default=0)
        ext_nodes, ext_edges = external_nodes_and_edges(graph, set(handlers) | set(domain))
        for n in ext_nodes:
            nodes.append(
                DiagramNode(
                    id=n.id,
                    label=n.label,
                    kind=n.kind,
                    evidence=n.evidence,
                    attrs=(
                        *n.attrs,
                        ("layer", str(ext_layer)),
                        ("stage", "Storage / External"),
                    ),
                    sublabel=n.sublabel,
                )
            )
        edges: list[DiagramEdge] = []
        for m in handlers:
            if f"in:{m}" in drawn and m in drawn:
                ingress = next(n for n in nodes if n.id == f"in:{m}")
                edges.append(
                    DiagramEdge(
                        src=f"in:{m}",
                        dst=m,
                        label="handles",
                        evidence=ingress.evidence,
                        variant="emphasis",
                    )
                )
        # An import between drawn modules that does not go downstream (same
        # hop, or back toward the handlers) is not an arrow here, and its
        # count is stated so the picture is not read as "no such import".
        against = 0
        for a in sorted(set(handlers) | set(domain)):
            for b, ev in imports.get(a, []):
                if b not in drawn or a not in drawn:
                    continue
                if hops.get(b, 0) <= hops.get(a, 0):
                    against += 1
                else:
                    edges.append(
                        DiagramEdge(
                            src=a,
                            dst=b,
                            label="",
                            note=f"{len(ev)} import{'s' if len(ev) != 1 else ''}",
                            evidence=ev[:MAX_EVIDENCE_PER_BOX],
                            weight=len(ev),
                        )
                    )
        edges.extend(e for e in ext_edges if e.src in drawn)

        unstaged = sorted(set(graph.modules) - set(handlers) - set(domain))
        if unstaged:
            diags.append(
                Diagnostic(
                    code="SVA-R-007",
                    severity=Severity.INFO,
                    message=(
                        f"{len(unstaged)} module(s) are reachable from no route handler "
                        f"within {DOMAIN_DEPTH} import hops and fall in no data-flow stage, "
                        "so they are not drawn here"
                    ),
                    subject=", ".join(m or "(repo root)" for m in unstaged[:5]),
                )
            )
        anchor_ev = nodes[0].evidence[0]
        specs[ROOT] = DiagramSpec(
            kind=self.kind,
            id=ROOT,
            title=self.title,
            subtitle=(
                f"{len(handlers)} ingress module{'s' if len(handlers) != 1 else ''}, "
                f"{len(domain)} domain module{'s' if len(domain) != 1 else ''}, "
                f"{len(ext_nodes)} external" + _against_note(against)
            ),
            nodes=tuple(sorted(nodes)),
            edges=tuple(sorted(edges)),
            regions=_stage_regions(nodes, anchor_ev),
        )
        return DiagramSet(self.kind, ROOT, specs, tuple(diags))


class RequestFlowDeriver(Deriver):
    """One story per endpoint group, in hops of import reachability."""

    kind = DiagramKind.REQUEST_FLOW
    title = "Request flow"

    def derive(self, graph: Graph, clustering: Clustering) -> DiagramSet | None:
        _ = clustering
        roles = module_roles(graph)
        handlers = sorted(m for m, rs in roles.items() if "api" in rs and m in graph.modules)
        if not handlers:
            return DiagramSet(
                self.kind,
                ROOT,
                {},
                (
                    self.unavailable(
                        "no module declares routes, so there is no request to follow"
                    ),
                ),
            )
        imports = _imports_of(graph)
        specs: dict[str, DiagramSpec] = {}
        diags: list[Diagnostic] = []
        arch = ArchitectureDeriver()
        names = labels_for(handlers)
        top: list[DiagramNode] = []
        for m in handlers:
            ingress = _ingress_node(graph, m)
            if ingress is None:
                continue
            child = self._story(graph, m, imports, roles, specs, diags, arch)
            top.append(
                DiagramNode(
                    id=f"req:{m}",
                    label=names[m],
                    kind="endpoint",
                    evidence=ingress.evidence,
                    child_spec=child,
                    attrs=(("layer", "0"),),
                    sublabel=ingress.sublabel + " · " + ingress.label,
                )
            )
        specs[ROOT] = DiagramSpec(
            kind=self.kind,
            id=ROOT,
            title=self.title,
            subtitle=(
                f"{len(top)} endpoint group{'s' if len(top) != 1 else ''}; drill into one. "
                "Hops are import reachability, not call order"
            ),
            nodes=tuple(sorted(top)),
            edges=(),
        )
        return DiagramSet(self.kind, ROOT, specs, tuple(diags))

    def _story(
        self,
        graph: Graph,
        handler: str,
        imports: dict[str, list[tuple[str, tuple[Evidence, ...]]]],
        roles: dict[str, tuple[str, ...]],
        specs: dict[str, DiagramSpec],
        diags: list[Diagnostic],
        arch: ArchitectureDeriver,
    ) -> str:
        sid = spec_id(f"req:{handler}")
        hops = _reach({handler}, imports, DOMAIN_DEPTH)
        names = labels_for(sorted(m for m in hops if m in graph.modules))
        nodes: list[DiagramNode] = []
        for m, hop in sorted(hops.items(), key=lambda kv: (kv[1], kv[0])):
            if m not in graph.modules:
                continue
            stage = "Handlers" if hop == 0 else "Domain"
            node = _module_node(graph, m, names[m], hop, stage, roles)
            if node is None:
                continue
            nodes.append(
                DiagramNode(
                    id=node.id,
                    label=node.label,
                    kind=node.kind,
                    evidence=node.evidence,
                    attrs=(("layer", str(hop)), ("hop", str(hop))),
                    sublabel=(f"hop {hop} · " if hop else "handler · ") + node.sublabel,
                    child_spec=arch.components_for(graph, m, sid, specs, diags),
                )
            )
        drawn = {n.id for n in nodes}
        last = max((hops[m] for m in drawn), default=0) + 1
        ext_nodes, ext_edges = external_nodes_and_edges(graph, drawn)
        for n in ext_nodes:
            nodes.append(
                DiagramNode(
                    id=n.id,
                    label=n.label,
                    kind=n.kind,
                    evidence=n.evidence,
                    attrs=(*n.attrs, ("layer", str(last))),
                    sublabel=n.sublabel,
                )
            )
        edges: list[DiagramEdge] = []
        against = 0
        for a in sorted(drawn):
            for b, ev in imports.get(a, []):
                if b not in drawn:
                    continue
                if hops.get(b, 0) != hops.get(a, 0) + 1:
                    against += 1
                else:
                    edges.append(
                        DiagramEdge(
                            src=a,
                            dst=b,
                            label="",
                            note=f"{len(ev)} import{'s' if len(ev) != 1 else ''}",
                            evidence=ev[:MAX_EVIDENCE_PER_BOX],
                            weight=len(ev),
                        )
                    )
        edges.extend(ext_edges)
        regions: list[Region] = []
        for hop in range(0, last):
            members = tuple(sorted(n.id for n in nodes if n.attr("hop") == str(hop)))
            if members:
                first = next(n for n in nodes if n.id == members[0])
                regions.append(
                    Region(
                        id=f"hop:{hop}",
                        label="handler" if hop == 0 else f"hop {hop}",
                        members=members,
                        evidence=first.evidence[:1],
                        kind="stage",
                    )
                )
        specs[sid] = DiagramSpec(
            kind=self.kind,
            id=sid,
            title=f"{handler or '(repo root)'} request flow",
            subtitle=(
                f"{len(hops)} module{'s' if len(hops) != 1 else ''} within {DOMAIN_DEPTH} import "
                f"hops; reachability, not call order" + _against_note(against)
            ),
            nodes=tuple(sorted(nodes)),
            edges=tuple(sorted(edges)),
            parent=ROOT,
            regions=tuple(regions),
        )
        return sid
