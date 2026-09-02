"""Layout engines. One per diagram shape, because one generic engine is worse.

A columnar flow laid out by a force algorithm does not read as a flow, and an
ERD laid out that way ignores foreign-key locality. Each engine here is small
and purpose-built, and each reads only a `DiagramSpec`: no graph access, no
re-parsing, so the evidence chain stays intact and every engine is testable
from a literal spec.

Every coordinate is an integer, computed by integer arithmetic rather than
rounded at the end. Floats would differ in their last bits across platforms
and the artifact is promised byte-identical on Linux and macOS.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence

from svarupa.derive.base import DiagramEdge, DiagramSpec
from svarupa.layout.geometry import Band, Box, Canvas, Route, Style
from svarupa.layout.text import advance, sanitize, truncate

__all__ = ["ENGINES", "clustered", "grid", "lay_out", "layered"]

# Rows wrap rather than growing without bound. A 4000px-wide row is not a
# diagram, it is a horizontal scroll bar.
MAX_ROW_WIDTH = 1280


# --------------------------------------------------------------------------
# Boxes
# --------------------------------------------------------------------------


def _boxes(spec: DiagramSpec, style: Style) -> list[Box]:
    """One unpositioned box per node, sized to its label.

    Sanitize first, then measure, then truncate, then size. That order is the
    whole point: measuring before sanitizing would size the box for text that
    is not what gets drawn, and truncating after sizing would leave a box
    wider than its content for no reason.
    """
    out: list[Box] = []
    for n in spec.nodes:
        full = sanitize(n.label)
        label = truncate(full, style.font_size, style.text_budget)
        width = advance(label, style.font_size) + 2 * style.box_pad_x
        width = max(style.box_min_width, min(style.box_max_width, width))
        out.append(
            Box(
                id=n.id,
                label=label,
                full_label=full,
                kind=n.kind,
                x=0,
                y=0,
                w=width,
                h=style.box_height,
                evidence=n.evidence,
                child_spec=n.child_spec,
                attrs=n.attrs,
            )
        )
    return out


# --------------------------------------------------------------------------
# Rows
# --------------------------------------------------------------------------


def _declared_layers(spec: DiagramSpec) -> dict[str, int] | None:
    """The `layer` attribute, if every node carries a usable one.

    Read rather than recomputed. `derive` already ran a topological pass and a
    second one here could disagree with the first, which is the shape a
    promoted decision forbids: pass 2 must never re-derive a pass 1 fact.
    All-or-nothing, because a mix of declared and inferred layers would be two
    incomparable scales on one axis.
    """
    out: dict[str, int] = {}
    for n in spec.nodes:
        raw = n.attr("layer")
        if raw is None:
            return None
        try:
            out[n.id] = int(raw)
        except ValueError:
            return None
    return out


def _inferred_layers(spec: DiagramSpec) -> dict[str, int]:
    """Longest-path depth over the spec's own edges, tolerating cycles.

    Cycles are not hypothetical: a module dependency cycle is common and one
    already has a regression test. Kahn's algorithm drains what it can, then
    whatever remains is a cycle and is placed one row below the deepest
    resolved node, in id order. Refusing to lay out a cyclic graph would mean
    refusing to draw the repositories that most need the picture.
    """
    ids = [n.id for n in spec.nodes]
    present = set(ids)
    incoming: dict[str, set[str]] = {i: set() for i in ids}
    outgoing: dict[str, set[str]] = {i: set() for i in ids}
    for e in spec.edges:
        if e.src in present and e.dst in present and e.src != e.dst:
            incoming[e.dst].add(e.src)
            outgoing[e.src].add(e.dst)

    depth: dict[str, int] = {}
    ready = sorted(i for i in ids if not incoming[i])
    remaining = {i: set(v) for i, v in incoming.items()}
    while ready:
        nid = ready.pop(0)
        depth[nid] = max((depth[p] + 1 for p in incoming[nid] if p in depth), default=0)
        for nxt in sorted(outgoing[nid]):
            remaining[nxt].discard(nid)
            if not remaining[nxt] and nxt not in depth and nxt not in ready:
                ready.append(nxt)
        ready.sort(key=lambda i: (len(remaining[i]), i))

    if len(depth) < len(ids):
        floor = max(depth.values(), default=-1) + 1
        for nid in sorted(set(ids) - set(depth)):
            depth[nid] = floor
    return depth


def _rows(spec: DiagramSpec, boxes: Sequence[Box], style_gap: int) -> list[list[Box]]:
    """Group boxes into rows, wrapping any row that would exceed MAX_ROW_WIDTH."""
    layers = _declared_layers(spec) or _inferred_layers(spec)
    by_layer: dict[int, list[Box]] = {}
    for b in boxes:
        by_layer.setdefault(layers.get(b.id, 0), []).append(b)

    rows: list[list[Box]] = []
    for layer in sorted(by_layer):
        current: list[Box] = []
        width = 0
        for b in sorted(by_layer[layer], key=lambda b: b.id):
            # The gap counts. Measuring only box widths made the wrap threshold
            # a different number from the width it produced: 26 boxes wrapped
            # into a 1515px row against a stated bound of 1280.
            step = b.w + (style_gap if current else 0)
            if current and width + step > MAX_ROW_WIDTH:
                rows.append(current)
                current, width = [], 0
                step = b.w
            current.append(b)
            width += step
        if current:
            rows.append(current)
    return rows


def _place(rows: list[list[Box]], style: Style) -> tuple[list[Box], int, int, list[range]]:
    """Assign coordinates row by row, each row centred.

    Returns the placed boxes, the canvas size, and each row's vertical extent,
    which the banded engine needs and which is cheaper to hand back than to
    recompute from the coordinates.
    """
    row_widths = [sum(b.w for b in row) + style.gap_x * max(0, len(row) - 1) for row in rows]
    content = max(row_widths, default=0)
    width = content + 2 * style.margin
    placed: list[Box] = []
    extents: list[range] = []
    y = style.margin
    for row, row_width in zip(rows, row_widths, strict=True):
        x = style.margin + (content - row_width) // 2
        for b in row:
            placed.append(
                Box(
                    id=b.id,
                    label=b.label,
                    full_label=b.full_label,
                    kind=b.kind,
                    x=x,
                    y=y,
                    w=b.w,
                    h=b.h,
                    evidence=b.evidence,
                    child_spec=b.child_spec,
                    attrs=b.attrs,
                )
            )
            x += b.w + style.gap_x
        extents.append(range(y, y + style.box_height))
        y += style.box_height + style.gap_y
    height = max(y - style.gap_y + style.margin, 2 * style.margin + style.box_height)
    return placed, width, height, extents


# --------------------------------------------------------------------------
# Routing
# --------------------------------------------------------------------------


def _routes(
    edges: Sequence[DiagramEdge], placed: Mapping[str, Box], style: Style
) -> list[Route]:
    """Elbow polylines that stay inside the row gaps.

    Vertical segments run in the empty band between two rows, which is why the
    geometry check for a segment crossing a box interior can pass rather than
    being quietly omitted. A forward edge leaves the bottom of its source and
    enters the top of its target. A backward or same-row edge leaves and
    re-enters from the side, since entering a box from below would draw an
    arrowhead pointing the wrong way up a dependency.

    Fan-out is spread: several edges leaving one box would otherwise share a
    single exit point and render as one thick line, hiding how many there are.
    """
    out: list[Route] = []
    exits: dict[str, int] = {}
    entries: dict[str, int] = {}
    for e in sorted(edges, key=lambda e: (e.src, e.dst)):
        a, b = placed.get(e.src), placed.get(e.dst)
        if a is None or b is None:
            continue
        exits[e.src] = exits.get(e.src, 0) + 1
        entries[e.dst] = entries.get(e.dst, 0) + 1
        ax = _fan(a, exits[e.src])
        bx = _fan(b, entries[e.dst])

        if b.y > a.bottom:
            mid = (a.bottom + b.y) // 2
            points = ((ax, a.bottom), (ax, mid), (bx, mid), (bx, b.y))
        else:
            # Around the right-hand side, in the gutter beyond both boxes.
            lane = max(a.right, b.right) + style.gap_x // 2
            points = (
                (a.right, a.y + a.h // 2),
                (lane, a.y + a.h // 2),
                (lane, b.y + b.h // 2),
                (b.right, b.y + b.h // 2),
            )
        out.append(
            Route(
                src=e.src,
                dst=e.dst,
                label=sanitize(e.label),
                points=points,
                evidence=e.evidence,
                resolution=e.resolution,
                weight=e.weight,
            )
        )
    return out


def _fan(box: Box, nth: int) -> int:
    """The nth attachment x for a box, spread across its width.

    Deterministic in `nth` so the same spec always fans the same way.
    """
    slots = 5
    step = box.w // (slots + 1)
    return box.x + step * (1 + (nth - 1) % slots)


# --------------------------------------------------------------------------
# Engines
# --------------------------------------------------------------------------


def layered(spec: DiagramSpec, style: Style) -> Canvas:
    """Rows by dependency depth. Module deps, class hierarchy."""
    placed, width, height, _ = _place(_rows(spec, _boxes(spec, style), style.gap_x), style)
    index = {b.id: b for b in placed}
    return _canvas(spec, "layered", placed, _routes(spec.edges, index, style), width, height)


def clustered(spec: DiagramSpec, style: Style) -> Canvas:
    """Layered, with each row banded and labelled. Architecture, topology.

    A deliberate narrowing of design section 6.1, which described the bands as
    communities. Communities cannot appear here: they are chaotically sensitive
    to input perturbation and a promoted decision confines them to `derive`,
    which is also why the boxes on an architecture diagram already *are* the
    groups. Banding by dependency depth is the honest thing left to say about
    a row, and unlike a community label it is stable under a new import.
    """
    boxes = _boxes(spec, style)
    rows = _rows(spec, boxes, style.gap_x)
    placed, width, height, extents = _place(rows, style)
    index = {b.id: b for b in placed}
    bands = tuple(
        Band(
            label=f"level {i + 1}",
            y=extent.start,
            h=extent.stop - extent.start,
            members=tuple(sorted(b.id for b in row)),
        )
        for i, (row, extent) in enumerate(zip(rows, extents, strict=True))
    )
    return _canvas(
        spec, "clustered", placed, _routes(spec.edges, index, style), width, height, bands
    )


def grid(spec: DiagramSpec, style: Style) -> Canvas:
    """Uniform columns, for diagrams whose edges do not imply an order.

    ERD tables and any edgeless spec. Laying those out by depth would put every
    box in one row and imply a hierarchy that the data does not contain.
    """
    boxes = _boxes(spec, style)
    if not boxes:
        placed, width, height, _ = _place([], style)
        return _canvas(spec, "grid", [], [], width, height)
    widest = max(b.w for b in boxes)
    per_row = max(1, (MAX_ROW_WIDTH + style.gap_x) // (widest + style.gap_x))
    ordered = sorted(boxes, key=lambda b: b.id)
    rows = [ordered[i : i + per_row] for i in range(0, len(ordered), per_row)]
    placed, width, height, _ = _place(rows, style)
    index = {b.id: b for b in placed}
    return _canvas(spec, "grid", placed, _routes(spec.edges, index, style), width, height)


def _canvas(
    spec: DiagramSpec,
    engine: str,
    boxes: Sequence[Box],
    routes: Sequence[Route],
    width: int,
    height: int,
    bands: tuple[Band, ...] = (),
) -> Canvas:
    return Canvas(
        spec_id=spec.id,
        kind=spec.kind,
        engine=engine,
        title=spec.title,
        subtitle=spec.subtitle,
        width=width,
        height=height,
        boxes=tuple(boxes),
        routes=tuple(routes),
        bands=bands,
        parent=spec.parent,
    )


ENGINES: dict[str, Callable[[DiagramSpec, Style], Canvas]] = {
    "layered": layered,
    "clustered": clustered,
    "grid": grid,
}


def lay_out(spec: DiagramSpec, style: Style, engine: str) -> Canvas:
    """Run a named engine.

    An unknown name raises rather than falling back. A silent substitution
    would produce a diagram in the wrong shape while the run still looked
    successful, which is the same refusal `cluster` makes about backends.
    """
    if engine not in ENGINES:
        raise KeyError(f"unknown layout engine {engine!r}; expected one of {sorted(ENGINES)}")
    return ENGINES[engine](spec, style)
