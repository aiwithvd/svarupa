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
    spec_id,
)
from svarupa.diagnostics import Diagnostic, Severity
from svarupa.model import EdgeKind, Evidence

__all__ = ["MAX_STAGE_BOXES", "DataFlowDeriver", "RequestFlowDeriver"]

# How far a handler's imports are followed into the domain. Two hops is where
# import chains on services stop carrying meaning about the request (Spike
# 0b measured import depth 3-10 on libraries, far less on services); deeper
# hops become the whole codebase.
DOMAIN_DEPTH = 2

# Above this a stage frame is a wall, not a picture. The most-connected
# modules of the stage are kept, connectivity being what a reader drills in
# to see, and the remainder is stated in the subtitle rather than hidden —
# the same answer as MAX_TOP_BOXES at the top level and MAX_COMPONENT_BOXES
# in a code view, one stage down. Twelve is the top-level budget. Omitted
# modules are not gone: they stay in graph.json and one drill away in the
# architecture view.
MAX_STAGE_BOXES = 12

_STAGES = ("Ingress", "Handlers", "Domain", "Storage / External")

# module -> [(imported module, citations, pass-through modules on the way)]
Imports = dict[str, list[tuple[str, tuple[Evidence, ...], tuple[str, ...]]]]


def _imports_of(graph: Graph) -> Imports:
    """Runtime import pairs between architecture modules, passing THROUGH
    code that is not one.

    Generated, vendored and test code is in the graph but is not a module
    (design section 3 exclusions). A handler that imports a generated schema
    that imports the store still reaches the store, and following only
    module-to-module pairs cut the request short there and then reported the
    store as reachable from no handler (review #17 F6). A pass-through module
    is not drawn and not a hop; the edge cites both import lines and names
    the module it went through.
    """
    modules = set(graph.modules)
    raw: dict[str, dict[str, set[Evidence]]] = {}
    for e in graph.edges:
        if e.kind is not EdgeKind.IMPORTS or e.attr("type_only") == "true":
            continue
        a, b = module_of(e.src), module_of(e.dst)
        if a != b:
            raw.setdefault(a, {}).setdefault(b, set()).update(e.evidence)

    out: Imports = {}
    for a in sorted(raw):
        if a not in modules:
            continue
        found: dict[str, tuple[set[Evidence], tuple[str, ...]]] = {}
        _walk(raw, modules, a, a, set(), (), frozenset({a}), found)
        out[a] = [
            (c, tuple(sorted(ev))[:MAX_EVIDENCE_PER_BOX], via)
            for c, (ev, via) in sorted(found.items())
        ]
    return out


def _walk(
    raw: dict[str, dict[str, set[Evidence]]],
    modules: set[str],
    origin: str,
    node: str,
    carried: set[Evidence],
    via: tuple[str, ...],
    seen: frozenset[str],
    found: dict[str, tuple[set[Evidence], tuple[str, ...]]],
) -> None:
    """Follow `node`'s imports; a target that is a module is recorded, one
    that is not is walked through (once) with its import lines carried."""
    for c in sorted(raw.get(node, {})):
        ev = carried | raw[node][c]
        if c in modules:
            if c == origin:
                continue
            prev = found.get(c)
            # A direct import outranks any pass-through path, and the
            # shortest pass-through outranks a longer one.
            if prev is None or len(via) < len(prev[1]):
                found[c] = (set(ev), via)
            elif len(via) == len(prev[1]):
                prev[0].update(ev)
        elif c not in seen:
            _walk(raw, modules, origin, c, ev, (*via, c), seen | {c}, found)


def _reach(
    start: set[str], imports: Imports, depth: int
) -> tuple[dict[str, int], dict[str, tuple[Evidence, ...]]]:
    """Modules reachable by import from `start`: their hop count and the
    import that first brought each one in (what a stage frame cites)."""
    hops = dict.fromkeys(start, 0)
    entry: dict[str, tuple[Evidence, ...]] = {}
    frontier = set(start)
    for hop in range(1, depth + 1):
        nxt: set[str] = set()
        for m in sorted(frontier):
            for target, ev, _via in imports.get(m, []):
                if target not in hops:
                    hops[target] = hop
                    entry[target] = ev
                    nxt.add(target)
        frontier = nxt
    return hops, entry


# What fits a box at the label font without the layout cutting it again from
# the head (review #21 N10: `…able-rules, /batch, /check …`, elided both ends).
_INGRESS_CHARS = 28


def _cut(paths: list[str], limit: int = _INGRESS_CHARS) -> str:
    """Route paths joined, cut at a path boundary rather than mid-segment:
    `/api/v1/organisations/{org_id}/billin…` says nothing a reader can use."""
    shown = ", ".join(paths[:3]) + (" …" if len(paths) > 3 else "")
    if len(shown) <= limit:
        return shown
    keep = shown[: limit - 1]
    at_comma = keep.rfind(", ")
    cut = max(at_comma, keep.rfind("/", 1))
    if cut <= 0:
        return keep + "…"
    stem = keep[:cut]
    # A cut that drops whole routes ends ` …` like the untruncated list; a
    # cut inside a route ends `…` on the segment boundary.
    if cut == at_comma or stem.rstrip().endswith(","):
        return stem.rstrip(" ,") + " …"
    return stem + "…"


def _ingress_node(graph: Graph, module: str) -> DiagramNode | None:
    routes = sorted(
        r
        for r in graph.routes
        if r.file in graph.architecture_paths and module_of(r.file) == module
    )
    if not routes:
        return None
    # A route declared with path "" (handler-relative; its router prefix is
    # composed elsewhere or not at all) counts but is not shown: joined, it
    # was a leading comma in 51 places on the acceptance repo (review #20 S4).
    paths = sorted({r.path for r in routes if r.path})
    return DiagramNode(
        id=f"in:{module}",
        label=_cut(paths),
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


def _degree(imports: Imports) -> dict[str, int]:
    """Distinct import connections per module, in both directions.

    The selection weight when a stage must be capped: connectivity is what a
    reader drills in to see (components.py ranks code views the same way).
    """
    degree: dict[str, int] = {}
    for a, outs in imports.items():
        degree[a] = degree.get(a, 0) + len(outs)
        for b, _ev, _via in outs:
            degree[b] = degree.get(b, 0) + 1
    return degree


def _cap(
    members: list[str],
    degree: dict[str, int],
    stage: str,
    diags: list[Diagnostic],
) -> tuple[list[str], int]:
    """The stage's most-connected MAX_STAGE_BOXES members, and how many rest.

    A cap that dropped silently would make the picture claim the stage is
    smaller than it is; the omitted count goes to the subtitle and an SVA-R-006
    diagnostic, and every omitted module remains in graph.json and one drill
    away in the architecture view. Below the cap nothing changes, so a small
    repository's diagram is byte-identical.
    """
    if len(members) <= MAX_STAGE_BOXES:
        return members, 0
    ranked = sorted(members, key=lambda m: (-degree.get(m, 0), m))
    kept = ranked[:MAX_STAGE_BOXES]
    omitted = len(members) - len(kept)
    diags.append(
        Diagnostic(
            code="SVA-R-006",
            severity=Severity.INFO,
            message=(
                f"{omitted} less-connected {stage} module(s) not drawn; the "
                f"{MAX_STAGE_BOXES} most connected are shown. Drill from the "
                "architecture view; all of them are in graph.json"
            ),
            subject=", ".join(m or "(repo root)" for m in ranked[MAX_STAGE_BOXES:][:5]),
        )
    )
    return kept, omitted


def _remainder(clauses: list[str]) -> str:
    """The stated remainder of capped stages, e.g.
    `; + 253 more domain modules — drill from the architecture view`."""
    if not clauses:
        return ""
    return "; + " + ", + ".join(clauses) + " — drill from the architecture view"


def _not_drawn_note(lateral: int, upstream: int) -> str:
    """Imports between drawn modules that are not arrows, by kind. One
    number called "against the flow" counted a handler importing another
    handler, which is sideways, not backwards (review #17 F5)."""
    parts: list[str] = []
    if lateral:
        parts.append(f"{lateral} lateral import{'s' if lateral != 1 else ''}")
    if upstream:
        parts.append(f"{upstream} upstream import{'s' if upstream != 1 else ''}")
    return f"; {' and '.join(parts)} not drawn" if parts else ""


def _import_edge(a: str, b: str, ev: tuple[Evidence, ...], via: tuple[str, ...]) -> DiagramEdge:
    note = f"{len(ev)} import{'s' if len(ev) != 1 else ''}"
    if via:
        note += " via " + ", ".join(via)
    # Silent, like every structural arrow (review #21 N9: 79 `imports` labels
    # stayed in the flow views after the architecture ones went quiet).
    return DiagramEdge(
        src=a,
        dst=b,
        label="",
        note=note,
        evidence=ev[:MAX_EVIDENCE_PER_BOX],
        weight=len(ev),
    )


def _stage_regions(
    nodes: list[DiagramNode], cite: dict[str, tuple[Evidence, ...]]
) -> tuple[Region, ...]:
    """One frame per stage that has members, labelled `01 / Ingress` like
    Archify's stage headers. The frame's citations are the lines that put
    each member in the stage: route lines for ingress and handlers, the
    import that reached a domain module, the classified import for a store.
    "The first member's first line" cited a file's line 1 for a stage claim
    (review #17 F8)."""
    out: list[Region] = []
    for i, stage in enumerate(_STAGES):
        members = tuple(sorted(n.id for n in nodes if n.attr("stage") == stage))
        if members:
            cited: list[Evidence] = []
            for m in members:
                cited.extend(cite.get(m, ())[:1])
            out.append(
                Region(
                    id=f"stage:{i}",
                    label=f"0{i + 1} / {stage}",
                    members=members,
                    evidence=tuple(sorted(set(cited)))[:MAX_EVIDENCE_PER_BOX],
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
        degree = _degree(imports)
        diags: list[Diagnostic] = []
        # A stage past the cap is a wall, not a picture: keep the most
        # connected, state the rest. The uncapped sets still drive what the
        # SVA-R-007 note counts, or capped-away modules would read as
        # "reachable from no handler", which is false.
        all_handlers = handlers
        handlers, omitted_handlers = _cap(handlers, degree, "handler", diags)
        # Reach is computed from ALL handlers, capped or not: a domain module
        # reachable only through an omitted handler is still reachable, and
        # counting it as "reachable from no route handler" (SVA-R-007) is a
        # false claim about a real module. The domain cap below states its own
        # remainder, so nothing is hidden either way.
        hops, entry = _reach(set(all_handlers), imports, DOMAIN_DEPTH)
        domain = sorted(m for m, h in hops.items() if h > 0 and m in graph.modules)
        domain_all = domain
        domain, omitted_domain = _cap(domain, degree, "domain", diags)
        names = labels_for([*handlers, *domain])
        nodes: list[DiagramNode] = []
        cite: dict[str, tuple[Evidence, ...]] = {}
        specs: dict[str, DiagramSpec] = {}
        arch = ArchitectureDeriver()
        for m in handlers:
            ingress = _ingress_node(graph, m)
            handler = _module_node(graph, m, names[m], 1, "Handlers", roles)
            if ingress is None or handler is None:
                continue
            nodes.append(ingress)
            cite[ingress.id] = ingress.evidence
            cite[m] = ingress.evidence
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
        # Two ingress boxes with the same paths (every service has `/health`)
        # say which handler they belong to.
        seen_labels: dict[str, int] = {}
        for n in nodes:
            if n.kind == "endpoint":
                seen_labels[n.label] = seen_labels.get(n.label, 0) + 1
        for i, n in enumerate(nodes):
            if n.kind == "endpoint" and seen_labels[n.label] > 1:
                nodes[i] = DiagramNode(
                    id=n.id,
                    label=n.label,
                    kind=n.kind,
                    evidence=n.evidence,
                    attrs=n.attrs,
                    sublabel=n.sublabel + " · " + names[n.id[len("in:") :]],
                )
        # One column per hop, so a store two imports away sits right of the
        # module that reaches it instead of stacked in the same column.
        for m in domain:
            node = _module_node(graph, m, names[m], 1 + hops[m], "Domain", roles)
            if node is not None:
                cite[m] = entry.get(m, ())
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
        # through the corridor. Externals belong to DRAWN modules only.
        ext_layer = 2 + max((hops[m] for m in domain if m in drawn), default=0)
        ext_nodes, ext_edges = external_nodes_and_edges(graph, drawn & set(graph.modules))
        # Externals are not modules, so the import degree does not cover
        # them; their connectivity here is how many drawn modules talk to them.
        ext_degree: dict[str, int] = {}
        for e in ext_edges:
            ext_degree[e.dst] = ext_degree.get(e.dst, 0) + 1
        kept_ext, omitted_ext = _cap(
            sorted(n.id for n in ext_nodes), ext_degree, "external", diags
        )
        omitted_ext_ids = {n.id for n in ext_nodes} - set(kept_ext)
        if omitted_ext_ids:
            ext_nodes = [n for n in ext_nodes if n.id not in omitted_ext_ids]
            ext_edges = [e for e in ext_edges if e.dst not in omitted_ext_ids]
        for n in ext_nodes:
            cite[n.id] = n.evidence
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
        # An import between drawn modules that does not go downstream is not
        # an arrow here, and its count is stated so the picture is not read
        # as "no such import": lateral (same hop) and upstream (back toward
        # the handlers) separately, because they mean different things.
        lateral = upstream = 0
        for a in sorted(drawn & set(graph.modules)):
            for b, ev, via in imports.get(a, []):
                if b not in drawn:
                    continue
                if hops.get(b, 0) == hops.get(a, 0):
                    lateral += 1
                elif hops.get(b, 0) < hops.get(a, 0):
                    upstream += 1
                else:
                    edges.append(_import_edge(a, b, ev, via))
        edges.extend(e for e in ext_edges if e.src in drawn)

        unstaged = sorted(set(graph.modules) - set(all_handlers) - set(domain_all))
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
        clauses: list[str] = []
        if omitted_handlers:
            clauses.append(
                f"{omitted_handlers} more handler module{'s' if omitted_handlers != 1 else ''}"
            )
        if omitted_domain:
            clauses.append(
                f"{omitted_domain} more domain module{'s' if omitted_domain != 1 else ''}"
            )
        if omitted_ext:
            clauses.append(f"{omitted_ext} more external{'s' if omitted_ext != 1 else ''}")
        specs[ROOT] = DiagramSpec(
            kind=self.kind,
            id=ROOT,
            title=self.title,
            subtitle=(
                f"{len(handlers)} ingress module{'s' if len(handlers) != 1 else ''}, "
                f"{len(domain)} domain module{'s' if len(domain) != 1 else ''}, "
                f"{len(ext_nodes)} external" + _not_drawn_note(lateral, upstream)
                + _remainder(clauses)
            ),
            nodes=tuple(sorted(nodes)),
            edges=tuple(sorted(edges)),
            regions=_stage_regions(nodes, cite),
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
        if len(top) == 1 and top[0].child_spec in specs:
            # One story is the diagram: a root holding a single box that must
            # be drilled is a menu with one item (review #20 C10).
            only = specs[top[0].child_spec]
            specs[only.id] = DiagramSpec(
                kind=only.kind,
                id=only.id,
                title=only.title,
                subtitle=only.subtitle,
                nodes=only.nodes,
                edges=only.edges,
                parent=None,
                regions=only.regions,
            )
            return DiagramSet(self.kind, only.id, specs, tuple(diags))
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
        imports: Imports,
        roles: dict[str, tuple[str, ...]],
        specs: dict[str, DiagramSpec],
        diags: list[Diagnostic],
        arch: ArchitectureDeriver,
    ) -> str:
        sid = spec_id(f"req:{handler}")
        hops, entry = _reach({handler}, imports, DOMAIN_DEPTH)
        degree = _degree(imports)
        # A story whose hops explode stops being a story: cap each hop at the
        # stage budget, most-connected first, and state the remainder. The
        # caps are PROGRESSIVE: a hop-N box is eligible only when a drawn
        # hop-(N-1) box imports it, because the alternative is a box labelled
        # "hop N" with no visible path from the handler — a path claim the
        # picture does not make. Every module dropped this way is still
        # counted in the remainder.
        parents: dict[str, set[str]] = {}
        for a, outs in imports.items():
            for b, _ev, _via in outs:
                parents.setdefault(b, set()).add(a)
        kept: set[str] = {handler}
        clauses: list[str] = []
        for hop in range(1, DOMAIN_DEPTH + 1):
            members = sorted(m for m, h in hops.items() if h == hop and m in graph.modules)
            eligible = [m for m in members if parents.get(m, set()) & kept]
            kept_hop, _ = _cap(eligible, degree, f"hop {hop}", diags)
            omitted = len(members) - len(kept_hop)
            kept.update(kept_hop)
            if omitted:
                clauses.append(f"{omitted} more at hop {hop}")
        hops = {m: h for m, h in hops.items() if m in kept}
        names = labels_for(sorted(m for m in hops if m in graph.modules))
        nodes: list[DiagramNode] = []
        cite: dict[str, tuple[Evidence, ...]] = {}
        ingress = _ingress_node(graph, handler)
        for m, hop in sorted(hops.items(), key=lambda kv: (kv[1], kv[0])):
            if m not in graph.modules:
                continue
            stage = "Handlers" if hop == 0 else "Domain"
            node = _module_node(graph, m, names[m], hop, stage, roles)
            if node is None:
                continue
            cite[m] = ingress.evidence if (hop == 0 and ingress) else entry.get(m, ())
            nodes.append(
                DiagramNode(
                    id=node.id,
                    label=node.label,
                    kind=node.kind,
                    evidence=node.evidence,
                    # The module's roles and stage stay with it: the passport
                    # of a box in a story showed no role while the same box in
                    # data flow showed `api,auth` (review #17 F9).
                    attrs=(*node.attrs, ("hop", str(hop))),
                    sublabel=(f"hop {hop} · " if hop else "handler · ") + node.sublabel,
                    child_spec=arch.components_for(graph, m, sid, specs, diags),
                )
            )
        drawn = {n.id for n in nodes}
        last = max((hops[m] for m in drawn), default=0) + 1
        ext_nodes, ext_edges = external_nodes_and_edges(graph, drawn)
        ext_degree: dict[str, int] = {}
        for e in ext_edges:
            ext_degree[e.dst] = ext_degree.get(e.dst, 0) + 1
        kept_ext, omitted_ext = _cap(
            sorted(n.id for n in ext_nodes), ext_degree, "external", diags
        )
        omitted_ext_ids = {n.id for n in ext_nodes} - set(kept_ext)
        if omitted_ext_ids:
            ext_nodes = [n for n in ext_nodes if n.id not in omitted_ext_ids]
            ext_edges = [e for e in ext_edges if e.dst not in omitted_ext_ids]
            clauses.append(f"{omitted_ext} more external{'s' if omitted_ext != 1 else ''}")
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
        lateral = upstream = 0
        for a in sorted(drawn):
            for b, ev, via in imports.get(a, []):
                if b not in drawn:
                    continue
                if hops.get(b, 0) == hops.get(a, 0):
                    lateral += 1
                elif hops.get(b, 0) < hops.get(a, 0):
                    upstream += 1
                else:
                    edges.append(_import_edge(a, b, ev, via))
        edges.extend(ext_edges)
        regions: list[Region] = []
        for hop in range(0, last):
            members = tuple(sorted(n.id for n in nodes if n.attr("hop") == str(hop)))
            if members:
                cited: list[Evidence] = []
                for m in members:
                    cited.extend(cite.get(m, ())[:1])
                regions.append(
                    Region(
                        id=f"hop:{hop}",
                        label="handler" if hop == 0 else f"hop {hop}",
                        members=members,
                        evidence=tuple(sorted(set(cited)))[:MAX_EVIDENCE_PER_BOX],
                        kind="stage",
                    )
                )
        specs[sid] = DiagramSpec(
            kind=self.kind,
            id=sid,
            title=f"{handler or '(repo root)'} request flow",
            subtitle=(
                f"{len(hops)} module{'s' if len(hops) != 1 else ''} within {DOMAIN_DEPTH} import "
                f"hops; reachability, not call order" + _not_drawn_note(lateral, upstream)
                + _remainder(clauses)
            ),
            nodes=tuple(sorted(nodes)),
            edges=tuple(sorted(edges)),
            parent=ROOT,
            regions=tuple(regions),
        )
        return sid
