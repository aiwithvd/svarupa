"""Below the module: the component flow, then the code.

The drill order a reader wants, stated by the user and matching design §5.1's
"derived the same way one level down":

1. architecture: groups and modules;
2. click a module: its **component flow**, the parts inside it and how they
   depend on each other;
3. click a part: the classes and functions defined in that specific part, with
   their call and inheritance edges.

A "part" is a source file, because that is the unit developers actually split
modules into, but it is presented as a component: the box says `resolver`, not
`resolve.py`, and the citation underneath it is where the truth lives. Going
straight from module to a wall of every class in it skipped the level where a
reader orients.

Honesty constraints, inherited from the whole pipeline: only evidenced elements
appear; call edges render at their real resolution, dashed when candidate and
absent when unresolved, because a guessed arrow between two functions is a
wrong claim at the exact zoom where a reader would act on it.
"""

from __future__ import annotations

from svarupa.build import Graph
from svarupa.derive.base import (
    DiagramEdge,
    DiagramKind,
    DiagramNode,
    DiagramSpec,
    spec_id,
)
from svarupa.diagnostics import Diagnostic, Severity
from svarupa.model import EdgeKind, Evidence, NodeKind

__all__ = [
    "COMPONENT_SUFFIX",
    "FLOW_SUFFIX",
    "MAX_COMPONENT_BOXES",
    "code_spec",
    "flow_spec",
]

# Suffixes appended to a spec id. `//` cannot survive path normalization, so no
# module- or file-derived id can contain it, and these namespaces are disjoint
# from the group namespace by construction.
FLOW_SUFFIX = "//flow"
COMPONENT_SUFFIX = "//code"

# Above this a code view is a wall, not a picture. The most connected
# components are kept, connectivity being what a reader drills in to see, and
# the count of what was left out is stated rather than hidden.
MAX_COMPONENT_BOXES = 24

_CODE_KINDS = (NodeKind.CLASS, NodeKind.FUNCTION, NodeKind.INTERFACE)
_CODE_EDGES = (EdgeKind.CALLS, EdgeKind.INHERITS, EdgeKind.IMPLEMENTS)


def _module_of(node_id: str) -> str:
    path = node_id.split("#", 1)[0]
    return path.rsplit("/", 1)[0] if "/" in path else ""


def _component_label(path: str) -> str:
    """A file presented as a component: the stem, not the filename.

    `resolver`, not `resolve.py`. The path stays one click away in the
    citation, which is also the only place it is exact rather than pretty.
    """
    leaf = path.rsplit("/", 1)[-1]
    stem = leaf.rsplit(".", 1)[0]
    return stem or leaf


def flow_spec(
    graph: Graph,
    module_id: str,
    parent: str,
    kind: DiagramKind,
) -> tuple[dict[str, DiagramSpec], tuple[Diagnostic, ...]] | None:
    """A module's component flow, plus a code view per component.

    Returns every spec this level introduces, keyed by id, or None when the
    module has fewer than two components: a one-box flow restates the parent,
    which design §5.1 names as the case where no child link is emitted. In
    that case the caller may still hang the single component's code view
    directly off the module.
    """
    files = sorted(
        path
        for path in graph.architecture_paths
        if _module_of(path) == module_id and path in graph.nodes
    )
    if len(files) < 2:
        return None

    inside = set(files)
    specs: dict[str, DiagramSpec] = {}
    diags: list[Diagnostic] = []

    # Flow edges: imports between this module's files, plus calls whose
    # endpoints are symbols defined in two different files here, aggregated to
    # the file pair. Each pair's weight is its count of distinct citations,
    # for the same reason module weights are.
    flows: dict[tuple[str, str], set[Evidence]] = {}
    for e in graph.edges:
        if e.kind is EdgeKind.IMPORTS or e.kind in _CODE_EDGES:
            a, b = e.src.split("#", 1)[0], e.dst.split("#", 1)[0]
        else:
            continue
        if a == b or a not in inside or b not in inside:
            continue
        flows.setdefault((a, b), set()).update(e.evidence)

    nodes: list[DiagramNode] = []
    for path in files:
        node = graph.nodes[path]
        child = code_spec(graph, path, spec_id(module_id) + FLOW_SUFFIX, kind)
        child_id: str | None = None
        if child is not None:
            spec, extra = child
            specs[spec.id] = spec
            diags.extend(extra)
            child_id = spec.id
        nodes.append(
            DiagramNode(
                id=path,
                label=_component_label(path),
                kind="component",
                evidence=node.evidence,
                child_spec=child_id,
            )
        )

    flow_id = spec_id(module_id) + FLOW_SUFFIX
    specs[flow_id] = DiagramSpec(
        kind=kind,
        id=flow_id,
        title=f"{module_id or '.'} component flow",
        subtitle=f"{len(nodes)} components",
        nodes=tuple(sorted(nodes)),
        edges=tuple(
            sorted(
                # The label's count and the clickable citations must agree or
                # say why they do not: "8 uses" over six links reads as two of
                # them lost. The cap is stated in the label when it applies.
                DiagramEdge(
                    src=a,
                    dst=b,
                    label=(
                        f"{len(ev)} uses, first 6 cited"
                        if len(ev) > 6
                        else f"{len(ev)} use{'s' if len(ev) != 1 else ''}"
                    ),
                    evidence=tuple(sorted(ev))[:6],
                    weight=len(ev),
                )
                for (a, b), ev in flows.items()
            )
        ),
        parent=parent,
    )
    return specs, tuple(diags)


def code_spec(
    graph: Graph,
    file_path: str,
    parent: str,
    kind: DiagramKind,
) -> tuple[DiagramSpec, tuple[Diagnostic, ...]] | None:
    """The classes and functions defined in one component, with their edges.

    Scoped to a single file on purpose: this is the pinpoint level, reached
    after the reader has chosen a part, so it shows that part completely
    rather than a whole module's symbols at once.
    """
    members = {
        nid: node
        for nid, node in graph.nodes.items()
        if node.kind in _CODE_KINDS and nid.split("#", 1)[0] == file_path
    }
    if len(members) < 2:
        return None

    method_counts: dict[str, int] = {}
    for nid, node in graph.nodes.items():
        if node.kind is not NodeKind.METHOD or nid.split("#", 1)[0] != file_path:
            continue
        owner = f"{file_path}#{node.qualified_name.rsplit('.', 1)[0]}"
        method_counts[owner] = method_counts.get(owner, 0) + 1

    edges = [
        e
        for e in graph.edges
        if e.kind in _CODE_EDGES and e.src in members and e.dst in members
    ]

    degree: dict[str, int] = dict.fromkeys(members, 0)
    for e in edges:
        degree[e.src] += 1
        degree[e.dst] += 1

    diags: list[Diagnostic] = []
    kept = set(members)
    if len(members) > MAX_COMPONENT_BOXES:
        ranked = sorted(members, key=lambda nid: (-degree[nid], nid))
        kept = set(ranked[:MAX_COMPONENT_BOXES])
        diags.append(
            Diagnostic(
                code="SVA-R-006",
                severity=Severity.INFO,
                message=(
                    f"{len(members) - len(kept)} less-connected component(s) not "
                    f"drawn; the {MAX_COMPONENT_BOXES} most connected are shown. "
                    "All of them are in graph.json"
                ),
                subject=file_path,
            )
        )

    nodes = tuple(
        sorted(
            DiagramNode(
                id=nid,
                label=node.label,
                kind=node.kind.value,
                evidence=node.evidence,
                attrs=((("methods", str(method_counts[nid])),) if nid in method_counts else ()),
            )
            for nid, node in members.items()
            if nid in kept
        )
    )
    spec_edges = tuple(
        sorted(
            DiagramEdge(
                src=e.src,
                dst=e.dst,
                label=e.kind.value,
                evidence=e.evidence,
                resolution=e.resolution,
            )
            for e in edges
            if e.src in kept and e.dst in kept
        )
    )
    # Counted per kind, never by subtraction: `len - classes` counted three
    # interfaces as three functions, and a subtitle is a claim.
    tally = {"class": 0, "function": 0, "interface": 0}
    for n in nodes:
        tally[n.kind] = tally.get(n.kind, 0) + 1
    parts = [
        f"{count} {kind}{'es' if kind == 'class' and count != 1 else 's' if count != 1 else ''}"
        for kind, count in tally.items()
        if count
    ]
    return (
        DiagramSpec(
            kind=kind,
            id=spec_id(file_path) + COMPONENT_SUFFIX,
            title=f"{_component_label(file_path)} code",
            subtitle=" and ".join(parts),
            nodes=nodes,
            edges=spec_edges,
            parent=parent,
        ),
        tuple(diags),
    )
