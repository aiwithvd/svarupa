"""Stage 4: group modules for visual presentation.

**Nothing here may reach the lockfile.** Community detection is chaotically
sensitive to input perturbation: measured on real repositories, a single added
import flips 25-38% of community assignments. Determinism means same input,
same output, and a pull request changes the input. So communities decide how
boxes are *grouped on a diagram* and never what a committed artifact is keyed
on. A test asserts no community identifier appears in a rendered lockfile.

That constraint is also what makes the algorithm choice low-stakes. Clustering
defaults to `networkx.louvain_communities`: BSD, already a dependency, and
measured at ARI 1.000 against planted truth. `leidenalg` is available as an
opt-in extra but is GPL, which would foreclose the license revisit the design
tracks as an open item.

If a backend is explicitly requested and unavailable, this **fails** rather
than silently substituting a different algorithm. A silent substitution would
change the output while the run still looks successful.
"""

from __future__ import annotations

import hashlib
import importlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, cast

import networkx as nx

from svarupa.build import Graph
from svarupa.diagnostics import Diagnostic, DiagnosticError, Severity

__all__ = ["Clustering", "Community", "cluster"]

DEFAULT_SEED = 1729
DEFAULT_RESOLUTION = 1.0

# Above this share of the graph a community explains nothing, so it is split
# again. Below this cohesion it is not really one thing.
_OVERSIZED_SHARE = 0.25
_MIN_SPLIT_MEMBERS = 8
_LOW_COHESION = 0.05
# Above this, a group is genuinely one thing and is never split for size.
_COHESIVE_ENOUGH = 0.6
# A resplit that is mostly singletons has shattered rather than divided.
_MAX_SINGLETON_SHARE = 0.5


@dataclass(frozen=True, order=True, slots=True)
class Community:
    """A visual grouping of modules.

    `anchor` is the identity a refinement can attach a human name to. It is the
    highest-degree member rather than a hash of the whole membership, because a
    membership hash changes the moment one module joins or leaves, which is
    exactly the churn that makes anchors useless. Naming the community after
    its most-connected member means a group that keeps its core keeps its name.
    """

    anchor: str
    members: tuple[str, ...]
    cohesion: float

    @property
    def label(self) -> str:
        return self.anchor.rsplit("/", 1)[-1] or self.anchor or "root"

    @property
    def size(self) -> int:
        return len(self.members)

    def fingerprint(self) -> str:
        """For detecting that a community's membership changed at all."""
        joined = "\n".join(self.members)
        return hashlib.sha256(joined.encode()).hexdigest()[:12]


@dataclass(frozen=True, slots=True)
class Clustering:
    communities: tuple[Community, ...]
    backend: str
    seed: int
    resolution: float
    diagnostics: tuple[Diagnostic, ...] = ()

    def of(self, module_id: str) -> Community | None:
        return next((c for c in self.communities if module_id in c.members), None)

    @property
    def assignment(self) -> Mapping[str, str]:
        return {m: c.anchor for c in self.communities for m in c.members}


def _weighted_module_graph(graph: Graph) -> nx.Graph[str]:
    """Undirected module graph with edge weights.

    Weights matter: a module pair with forty imports between them is not the
    same relationship as a pair with one, and discarding that flattens exactly
    the signal clustering is meant to find.

    Built in canonical order so that any tie-breaking inside the algorithm sees
    the same input everywhere.
    """
    # Counted from the file-level edges, NOT from `module_deps`.
    #
    # `module_deps` is a deduplicated set of pairs, so counting it yields 1 for
    # every relationship and 2 only for a reciprocal one. The weighting this
    # function exists to provide was therefore dead: twenty imports between two
    # modules scored exactly the same as one. Verified before fixing.
    from svarupa.model import EdgeKind

    modules = set(graph.modules)
    weights: dict[tuple[str, str], int] = {}
    for e in graph.edges:
        if e.kind is not EdgeKind.IMPORTS:
            continue
        a, b = _module_of(e.src), _module_of(e.dst)
        if a == b or a not in modules or b not in modules:
            continue
        key = (a, b) if a <= b else (b, a)
        # Each distinct source site is a separate act of depending.
        weights[key] = weights.get(key, 0) + max(1, len(e.evidence))

    # A declared dependency with no surviving file edge still connects.
    for a, b in graph.module_deps:
        key = (a, b) if a <= b else (b, a)
        weights.setdefault(key, 1)

    g: nx.Graph[str] = nx.Graph()
    for mid in sorted(graph.modules):
        g.add_node(mid)
    for (a, b), w in sorted(weights.items()):
        g.add_edge(a, b, weight=w)
    return g


def _module_of(node_id: str) -> str:
    """The module a node id belongs to.

    Both shapes appear: a file node is a bare path, a symbol node is
    `path#qualname`. Only the path part decides the module.
    """
    path = node_id.split("#", 1)[0]
    parent = path.rsplit("/", 1)[0] if "/" in path else ""
    return parent


def _cohesion(g: nx.Graph[str], members: Sequence[str]) -> float:
    """Actual over possible intra-group edges."""
    n = len(members)
    if n < 2:
        return 1.0
    inside = set(members)
    actual = sum(1 for a, b in g.edges(members) if a in inside and b in inside)
    possible = n * (n - 1) / 2
    return actual / possible if possible else 1.0


def _anchor(g: nx.Graph[str], members: Sequence[str]) -> str:
    """Highest-degree member, ties broken lexicographically.

    Lexicographic tie-breaking is not cosmetic: without it the anchor would
    depend on iteration order and the same graph would name itself differently
    on two machines.
    """
    return min(members, key=lambda m: (-g.degree(m), m))


def _louvain(g: nx.Graph[str], seed: int, resolution: float) -> list[list[str]]:
    # networkx's dispatchable decorator erases the signature under strict
    # typing, so the call goes through an explicitly-Any handle.
    community = importlib.import_module("networkx.algorithms.community")
    louvain: Any = community.louvain_communities
    found = cast(
        "list[set[str]]",
        louvain(g, seed=seed, resolution=resolution, weight="weight"),
    )
    return [sorted(c) for c in found]


def _leiden(g: nx.Graph[str], seed: int, resolution: float) -> list[list[str]]:
    """Opt-in backend. Neither igraph nor leidenalg ships type stubs, so the
    boundary is explicitly Any and narrowed at the one place it is consumed.

    `RBConfigurationVertexPartition` rather than plain modularity: modularity
    has a resolution limit and no tuning knob, and the design needs to control
    group size to keep a top-level diagram readable.
    """
    igraph = cast("Any", importlib.import_module("igraph"))
    leidenalg = cast("Any", importlib.import_module("leidenalg"))

    nodes = sorted(g.nodes)
    index = {n: i for i, n in enumerate(nodes)}
    edges = sorted((index[a], index[b]) for a, b in g.edges)
    weights = [int(g[nodes[a]][nodes[b]].get("weight", 1)) for a, b in edges]

    h = igraph.Graph(n=len(nodes), edges=edges)
    part = leidenalg.find_partition(
        h,
        leidenalg.RBConfigurationVertexPartition,
        seed=seed,
        resolution_parameter=resolution,
        weights=weights,
    )
    membership = cast("list[int]", list(part.membership))
    groups: dict[int, list[str]] = {}
    for i, cid in enumerate(membership):
        groups.setdefault(int(cid), []).append(nodes[i])
    return [sorted(v) for v in groups.values()]


_BACKENDS = {"louvain": _louvain, "leiden": _leiden}


def cluster(
    graph: Graph,
    backend: str = "louvain",
    seed: int = DEFAULT_SEED,
    resolution: float = DEFAULT_RESOLUTION,
) -> Clustering:
    if backend not in _BACKENDS:
        raise DiagnosticError(
            Diagnostic(
                code="SVA-C-001",
                severity=Severity.ERROR,
                message=f"unknown clustering backend; expected one of {sorted(_BACKENDS)}",
                subject=backend,
            )
        )

    g = _weighted_module_graph(graph)
    diags: list[Diagnostic] = []

    if not g.number_of_nodes():
        return Clustering((), backend, seed, resolution)

    try:
        groups = _BACKENDS[backend](g, seed, resolution)
    except ImportError as exc:
        # Explicitly requested and unavailable. Substituting a different
        # algorithm would change the output while the run still looked
        # successful, which is worse than refusing.
        raise DiagnosticError(
            Diagnostic(
                code="SVA-C-002",
                severity=Severity.ERROR,
                message=(
                    f"clustering backend {backend!r} is not installed; refusing to "
                    "substitute a different algorithm silently"
                ),
                subject=str(exc),
                suggested_fixes=(
                    f"pip install 'svarupa[{backend}]'",
                    "or use --backend louvain",
                ),
            )
        ) from exc

    groups = _split_oversized(g, groups, seed, resolution, backend, diags)

    communities = tuple(
        sorted(
            Community(
                anchor=_anchor(g, members),
                members=tuple(members),
                cohesion=round(_cohesion(g, members), 4),
            )
            for members in groups
            if members
        )
    )
    return Clustering(communities, backend, seed, resolution, tuple(diags))


def _split_oversized(
    g: nx.Graph[str],
    groups: list[list[str]],
    seed: int,
    resolution: float,
    backend: str,
    diags: list[Diagnostic],
) -> list[list[str]]:
    """Re-split a community that swallowed the graph, or that is barely one thing.

    A single community holding most of a repository explains nothing, and a
    community with near-zero cohesion is a bag rather than a group. Both are
    re-run at a higher resolution over the induced subgraph. One pass only:
    recursing until every group is small would just re-implement the algorithm
    badly.
    """
    total = g.number_of_nodes()
    out: list[list[str]] = []
    for members in groups:
        cohesion = _cohesion(g, members)
        # A tightly-connected group explains itself, however large. Splitting a
        # clique produces N singletons and turns a three-box diagram into
        # twenty-two, which is worse than the crowding it was meant to fix.
        cohesive = cohesion >= _COHESIVE_ENOUGH
        oversized = not cohesive and len(members) > max(
            _MIN_SPLIT_MEMBERS, total * _OVERSIZED_SHARE
        )
        weak = len(members) >= _MIN_SPLIT_MEMBERS and cohesion < _LOW_COHESION
        if not (oversized or weak):
            out.append(members)
            continue

        sub = g.subgraph(members).copy()
        try:
            resplit = _BACKENDS[backend](sub, seed, resolution * 2.0)
        except ImportError:  # pragma: no cover - the caller already proved it imports
            out.append(members)
            continue

        singletons = sum(1 for r in resplit if len(r) == 1)
        degenerate = len(resplit) <= 1 or singletons > len(resplit) * _MAX_SINGLETON_SHARE
        if degenerate:
            # Keeping a crowded group beats replacing it with rubble, but the
            # derivation stage has to know the top level will be crowded.
            diags.append(
                Diagnostic(
                    code="SVA-C-004",
                    severity=Severity.WARNING,
                    message=(
                        f"community of {len(members)} modules could not be split "
                        f"usefully ({len(resplit)} groups, {singletons} singletons); "
                        "the top-level diagram will be crowded"
                    ),
                    subject=_anchor(g, members),
                )
            )
            out.append(members)
            continue

        diags.append(
            Diagnostic(
                code="SVA-C-003",
                severity=Severity.INFO,
                message=(
                    f"community of {len(members)} modules was "
                    f"{'oversized' if oversized else 'low-cohesion'}; "
                    f"re-split into {len(resplit)} at resolution {resolution * 2.0}"
                ),
                subject=_anchor(g, members),
            )
        )
        out.extend(resplit)
    return out
