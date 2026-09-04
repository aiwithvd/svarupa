"""Layered graph drawing: ordering and long-edge routing.

The first layered engine had two defects that made real diagrams unreadable,
and both were invisible to the geometry validator because it only ever checked
boxes against boxes.

**Rows were ordered alphabetically.** Sorting a layer by id is deterministic and
close to the worst possible ordering: it ignores where a node's neighbours sit,
so edges cross maximally. The fix is the standard barycentric sweep, which is
also deterministic when the initial order and the tie-break are.

**Every long edge was sent to one shared side lane.** That was the correct
answer to routes crossing boxes, and it produced a rail: 74 edges leaving their
rows, running to the same x, and coming back, collapsing into a single thick
line with stubs. The standard answer is older and better: give a long edge a
**dummy node** in every layer it spans. The edge is then a chain of
adjacent-layer hops, each with its own space, and the dummies take part in
ordering so the long edges spread out on their own.

Everything here is deterministic: a fixed sweep count, a canonical initial
order, and index-based tie-breaking, so the same graph always draws the same
way on any machine.
"""

from __future__ import annotations

import itertools
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

__all__ = ["DUMMY_WIDTH", "Chain", "Layered", "layer_out"]

# A dummy occupies a real slot so edges through a row cannot pile up, but it is
# a waypoint rather than a box, so it is narrow.
DUMMY_WIDTH = 10

# Enough sweeps to settle, few enough to stay fast and predictable. Measured on
# real repositories: crossings stop improving after three or four.
SWEEPS = 4


@dataclass(frozen=True, slots=True)
class Chain:
    """One edge as the sequence of slots it passes through.

    `reversed_` records that the edge pointed against the layering and was
    flipped to make the graph acyclic. The renderer must draw the arrowhead at
    the original target, or a dependency cycle would be drawn pointing the
    wrong way, which is a wrong claim rather than an ugly one.
    """

    src: str
    dst: str
    nodes: tuple[str, ...]
    reversed_: bool = False

    @property
    def identity(self) -> tuple[str, str]:
        """The edge this chain draws, always in its original orientation.

        Distinct from `nodes`, which runs in layer order and therefore
        backwards for a reversed edge. Conflating the two made every reversed
        edge fail its lookup in the caller's edge map and be reported as a
        dangling endpoint, which took a whole diagram down.
        """
        return (self.src, self.dst)


@dataclass(frozen=True, slots=True)
class Layered:
    """Rows of slot ids, and the chain each edge follows through them."""

    rows: tuple[tuple[str, ...], ...]
    chains: tuple[Chain, ...]
    dummies: frozenset[str]

    def row_of(self) -> dict[str, int]:
        return {nid: i for i, row in enumerate(self.rows) for nid in row}

    def index_in_row(self) -> dict[str, int]:
        return {nid: j for row in self.rows for j, nid in enumerate(row)}


def _acyclic(
    ids: Sequence[str], edges: Sequence[tuple[str, str]], layer: Mapping[str, int]
) -> list[tuple[str, str, str, str]]:
    """Orient every edge downward, keeping its original direction alongside.

    Layers come from `derive` or from a topological pass, so an edge pointing
    upward means a cycle. Flipping it is how layered drawing handles cycles at
    all; remembering the flip is how the arrowhead stays truthful.

    Each entry is `(layer_src, layer_dst, real_src, real_dst)`. Carrying the
    original pair is not bookkeeping: in a two-node cycle `a -> b` and
    `b -> a` both orient to the same forward pair, so anything keyed on the
    forward pair alone collides. Measured before this: two distinct edges
    generated identical waypoint ids and the whole diagram was withheld for
    duplicate box ids.
    """
    out: list[tuple[str, str, str, str]] = []
    present = set(ids)
    for src, dst in edges:
        if src not in present or dst not in present or src == dst:
            continue
        if layer[dst] > layer[src]:
            out.append((src, dst, src, dst))
        else:
            out.append((dst, src, src, dst))
    return out


def _with_dummies(
    oriented: Sequence[tuple[str, str, str, str]], layer: Mapping[str, int]
) -> tuple[dict[int, list[str]], list[Chain], set[str], list[tuple[str, str]]]:
    """Expand every edge spanning more than one layer into a chain.

    A same-layer edge keeps a two-node chain and is routed in the gap below by
    the caller; there is no meaningful dummy for it.
    """
    rows: dict[int, list[str]] = {}
    chains: list[Chain] = []
    dummies: set[str] = set()
    links: list[tuple[str, str]] = []

    for src, dst, real_src, real_dst in sorted(oriented):
        flipped = (src, dst) != (real_src, real_dst)
        a, b = layer[src], layer[dst]
        if b - a <= 1:
            chains.append(Chain(real_src, real_dst, (src, dst), reversed_=flipped))
            if b - a == 1:
                links.append((src, dst))
            continue
        path = [src]
        for depth in range(a + 1, b):
            # Keyed on the *original* edge, so a cycle's two directions cannot
            # produce the same id, and stable across runs so the drawing is.
            nid = f"\x00dummy\x00{real_src}\x00{real_dst}\x00{depth}"
            dummies.add(nid)
            rows.setdefault(depth, []).append(nid)
            path.append(nid)
        path.append(dst)
        chains.append(Chain(real_src, real_dst, tuple(path), reversed_=flipped))
        links.extend(itertools.pairwise(path))
    return rows, chains, dummies, links


def _order(rows: list[list[str]], links: Sequence[tuple[str, str]]) -> list[list[str]]:
    """Barycentric sweep, down then up, a fixed number of times.

    A node moves to the average position of its neighbours in the layer just
    traversed. Ties keep the previous order, so the result depends only on the
    initial order, which the caller makes canonical.
    """
    below: dict[str, list[str]] = {}
    above: dict[str, list[str]] = {}
    for src, dst in links:
        below.setdefault(src, []).append(dst)
        above.setdefault(dst, []).append(src)

    def sweep(order: list[list[str]], neighbours: dict[str, list[str]], down: bool) -> None:
        span = range(1, len(order)) if down else range(len(order) - 2, -1, -1)
        for i in span:
            reference = {nid: j for j, nid in enumerate(order[i - 1 if down else i + 1])}
            current = {nid: j for j, nid in enumerate(order[i])}

            def key(nid: str, ref: dict[str, int] = reference, cur: dict[str, int] = current):
                seen = [ref[n] for n in neighbours.get(nid, ()) if n in ref]
                # A node with no neighbour in the reference layer stays put,
                # rather than being pulled to position zero.
                return (sum(seen) / len(seen) if seen else cur[nid], cur[nid])

            order[i] = sorted(order[i], key=key)

    for _ in range(SWEEPS):
        sweep(rows, above, down=True)
        sweep(rows, below, down=False)
    return rows


def layer_out(
    ids: Sequence[str],
    edges: Sequence[tuple[str, str]],
    layer: Mapping[str, int],
) -> Layered:
    """Produce ordered rows plus one chain per edge.

    `ids` must be canonically ordered by the caller; that ordering is the seed
    the sweep refines and the reason the result is deterministic.
    """
    oriented = _acyclic(ids, edges, layer)
    dummy_rows, chains, dummies, links = _with_dummies(oriented, layer)

    depth = max(layer.values(), default=0)
    rows: list[list[str]] = []
    for level in range(depth + 1):
        real = [nid for nid in ids if layer[nid] == level]
        rows.append([*real, *sorted(dummy_rows.get(level, ()))])

    rows = _order(rows, links)
    return Layered(tuple(tuple(r) for r in rows), tuple(chains), frozenset(dummies))
