"""Derivation contract: graph facts to diagram specs.

A graph has thousands of nodes. A diagram has about twelve boxes. This stage
makes that reduction, and every element it emits carries the same evidence
guarantee as the graph it came from: **no box, no arrow, without a source
location.** A diagram whose boxes cannot be clicked through is the thing this
product exists to replace.

Three rules, each from a promoted decision:

* **A deriver may return nothing.** An empty diagram fabricated to fill a tab
  is a wrong claim about a codebase. When the inputs are absent, the deriver
  says so and the report explains the gap.
* **Community identity never leaves this stage.** A deriver may read a
  clustering to decide grouping and nesting, but every id it emits is a module
  id or a node id from the graph. Communities are chaotically sensitive to
  input perturbation, so anything keyed on them would churn on every PR.
* **Type-only edges are not runtime dependencies.** They are carried with an
  attribute and excluded from flow.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum

from svarupa.build import Graph
from svarupa.cluster import Clustering
from svarupa.diagnostics import Diagnostic, Severity
from svarupa.model import Evidence, MissingEvidenceError, NodeKind, Resolution

__all__ = [
    "MAX_TOP_BOXES",
    "ROOT",
    "Deriver",
    "DiagramEdge",
    "DiagramKind",
    "DiagramNode",
    "DiagramSet",
    "DiagramSpec",
    "ModulePair",
]

# The top view is always about this many boxes, at any repository size. This is
# the whole answer to the 50,000-node monorepo: the UX is identical at 500
# nodes and 500,000 because the top level never grows, you drill instead.
MAX_TOP_BOXES = 12
ROOT = "root"

# Evidence per box is capped. A community of forty modules does not need forty
# citations to be checkable; it needs enough to land a reader somewhere real.
_MAX_EVIDENCE_PER_BOX = 6

# One aggregated module-to-module dependency: source, target, weight, evidence.
ModulePair = tuple[str, str, int, tuple[Evidence, ...]]


class DiagramKind(str, Enum):
    ARCHITECTURE = "architecture"
    MODULE_DEPS = "module-deps"
    ERD = "erd"
    API_SURFACE = "api-surface"
    DEPLOY_TOPOLOGY = "deploy-topology"
    REQUEST_FLOW = "request-flow"
    CLASS_HIERARCHY = "class-hierarchy"


@dataclass(frozen=True, order=True, slots=True)
class DiagramNode:
    id: str
    label: str
    kind: str
    evidence: tuple[Evidence, ...]
    child_spec: str | None = None
    attrs: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if not self.evidence:
            raise MissingEvidenceError("DiagramNode", self.id)

    def attr(self, key: str, default: str | None = None) -> str | None:
        return next((v for k, v in self.attrs if k == key), default)

    @property
    def is_drillable(self) -> bool:
        return self.child_spec is not None


@dataclass(frozen=True, order=True, slots=True)
class DiagramEdge:
    src: str
    dst: str
    label: str
    evidence: tuple[Evidence, ...]
    resolution: Resolution = Resolution.RESOLVED
    weight: int = 1

    def __post_init__(self) -> None:
        if not self.evidence:
            raise MissingEvidenceError("DiagramEdge", f"{self.src} -> {self.dst}")


@dataclass(frozen=True, slots=True)
class DiagramSpec:
    kind: DiagramKind
    id: str
    title: str
    nodes: tuple[DiagramNode, ...]
    edges: tuple[DiagramEdge, ...]
    parent: str | None = None
    subtitle: str = ""

    def node(self, node_id: str) -> DiagramNode | None:
        return next((n for n in self.nodes if n.id == node_id), None)


@dataclass(frozen=True, slots=True)
class DiagramSet:
    """A root spec plus the sub-diagrams reachable by drilling into it."""

    kind: DiagramKind
    root: str
    specs: Mapping[str, DiagramSpec]
    diagnostics: tuple[Diagnostic, ...] = ()

    @property
    def root_spec(self) -> DiagramSpec:
        return self.specs[self.root]

    def depth(self) -> int:
        """Deepest drill-down chain, for reporting."""

        def walk(spec_id: str, seen: frozenset[str]) -> int:
            if spec_id in seen or spec_id not in self.specs:
                return 0
            children = [
                n.child_spec for n in self.specs[spec_id].nodes if n.child_spec is not None
            ]
            if not children:
                return 1
            return 1 + max(walk(c, seen | {spec_id}) for c in children)

        return walk(self.root, frozenset())

    def total_nodes(self) -> int:
        return sum(len(s.nodes) for s in self.specs.values())


class Deriver(ABC):
    """One diagram kind.

    `derive` returning None is a first-class answer, not a failure: it means
    this repository has nothing of that shape to show, and the report says why
    rather than presenting an empty picture.
    """

    kind: DiagramKind
    title: str

    @abstractmethod
    def derive(self, graph: Graph, clustering: Clustering) -> DiagramSet | None: ...

    def unavailable(self, reason: str, fixes: Sequence[str] = ()) -> Diagnostic:
        """Say why a diagram is absent.

        An unexplained missing diagram reads as a bug. An explained one is
        information: the repository has no SQL, or no detected entry points.
        """
        return Diagnostic(
            code="SVA-R-001",
            severity=Severity.INFO,
            message=reason,
            subject=self.kind.value,
            suggested_fixes=tuple(fixes),
        )


# --------------------------------------------------------------------------
# Shared helpers
# --------------------------------------------------------------------------


def module_of(node_id: str) -> str:
    """The module a node id belongs to.

    Both id shapes appear: a file node is a bare path, a symbol node is
    `path#qualname`. Only the path part decides the module, and the split is
    done with string slicing on a known separator rather than path trimming.
    """
    path = node_id.split("#", 1)[0]
    return path.rsplit("/", 1)[0] if "/" in path else ""


def module_evidence(graph: Graph, module_id: str) -> tuple[Evidence, ...]:
    """Evidence for a box that stands for a whole module.

    A module is a directory, and a directory has no source location of its own,
    so the claim is carried by the files inside it. Capped, sorted, and never
    empty for a module that has files -- which is what makes the box clickable.
    """
    eligible = graph.architecture_paths
    hits: list[Evidence] = []
    for nid in sorted(graph.nodes):
        node = graph.nodes[nid]
        if node.kind is not NodeKind.MODULE:
            continue
        if module_of(nid) != module_id:
            continue
        # Only architecture-eligible files may stand for a module. The graph
        # keeps test and generated code on purpose, but a box citing
        # `access-control.test.ts` as the evidence for `src/gateway` sends a
        # reader to the wrong place.
        #
        # No `if eligible and ...` escape: an empty allow-list allows nothing.
        # That is both the safe default and the correct one, since a repository
        # with no architecture-eligible files has nothing to draw and the
        # deriver already declines.
        if nid not in eligible:
            continue
        hits.extend(node.evidence)
        if len(hits) >= _MAX_EVIDENCE_PER_BOX:
            break
    return tuple(sorted(set(hits))[:_MAX_EVIDENCE_PER_BOX])


def group_evidence(
    graph: Graph, module_ids: Iterable[str], anchor: str | None = None
) -> tuple[Evidence, ...]:
    """Evidence for a box that stands for several modules.

    The anchor leads. A box labelled "flask" must cite flask: iterating members
    alphabetically made a box named after `src/flask` cite
    `examples/javascript/js_example` instead, because `examples` sorts first.
    The label and the evidence disagreeing is precisely the kind of small lie
    that makes a reader stop trusting the whole diagram.
    """
    members = list(module_ids)
    ordered = ([anchor] if anchor in members else []) + sorted(
        m for m in members if m != anchor
    )
    out: list[Evidence] = []
    for mid in ordered:
        out.extend(module_evidence(graph, mid))
        if len(out) >= _MAX_EVIDENCE_PER_BOX:
            break
    return tuple(out[:_MAX_EVIDENCE_PER_BOX])


def runtime_edges(graph: Graph) -> tuple[tuple[ModulePair, ...], int]:
    """Module-level import pairs with weights, excluding type-only edges.

    Type-only imports are a real dependency for a compiler and not one at
    runtime, so including them in a flow or impact picture would overstate
    coupling. Returns the pairs plus how many were excluded, because a silently
    smaller number is indistinguishable from a smaller codebase.
    """
    from svarupa.model import EdgeKind

    modules = set(graph.modules)
    acc: dict[tuple[str, str], tuple[int, list[Evidence]]] = {}
    skipped = 0
    for e in graph.edges:
        if e.kind is not EdgeKind.IMPORTS:
            continue
        if e.attr("type_only") == "true":
            skipped += 1
            continue
        a, b = module_of(e.src), module_of(e.dst)
        if a == b or a not in modules or b not in modules:
            continue
        weight, evidence = acc.get((a, b), (0, []))
        acc[(a, b)] = (weight + max(1, len(e.evidence)), [*evidence, *e.evidence])
    pairs: tuple[ModulePair, ...] = tuple(
        (a, b, w, tuple(sorted(set(ev))[:_MAX_EVIDENCE_PER_BOX]))
        for (a, b), (w, ev) in sorted(acc.items())
    )
    return pairs, skipped
