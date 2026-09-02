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

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from svarupa.derive.base import DiagramEdge, DiagramSpec
from svarupa.diagnostics import Diagnostic, Severity
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


def _inferred_layers_cyclic(spec: DiagramSpec) -> frozenset[str]:
    """The ids Kahn could not drain, which is exactly the cyclic set."""
    ids = [n.id for n in spec.nodes]
    present = set(ids)
    incoming: dict[str, set[str]] = {i: set() for i in ids}
    outgoing: dict[str, set[str]] = {i: set() for i in ids}
    for e in spec.edges:
        if e.src in present and e.dst in present and e.src != e.dst:
            incoming[e.dst].add(e.src)
            outgoing[e.src].add(e.dst)
    drained: set[str] = set()
    ready = sorted(i for i in ids if not incoming[i])
    remaining = {i: set(v) for i, v in incoming.items()}
    while ready:
        nid = ready.pop(0)
        drained.add(nid)
        for nxt in sorted(outgoing[nid]):
            remaining[nxt].discard(nid)
            if not remaining[nxt] and nxt not in drained and nxt not in ready:
                ready.append(nxt)
    return frozenset(set(ids) - drained)


def _levels(spec: DiagramSpec, boxes: Sequence[Box]) -> dict[str, int]:
    """The dependency level of each box: declared if available, else inferred."""
    _ = boxes
    return _declared_layers(spec) or _inferred_layers(spec)


def _cyclic_ids(spec: DiagramSpec) -> frozenset[str]:
    """Ids whose level came from the cycle fallback rather than from an order.

    Needed so a band over them can say so instead of claiming a level number,
    which for a cycle is a placement decision and not a measured fact.
    """
    if _declared_layers(spec) is not None:
        return frozenset()
    return _inferred_layers_cyclic(spec)


def _rows(spec: DiagramSpec, boxes: Sequence[Box], style_gap: int) -> list[list[Box]]:
    """Group boxes into rows, wrapping any row that would exceed MAX_ROW_WIDTH."""
    layers = _levels(spec, boxes)
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


@dataclass(frozen=True, slots=True)
class Placement:
    """The result of placing rows, and everything routing needs to stay legal.

    `rows` and `gaps` are handed back rather than recomputed from coordinates
    because routing's correctness argument depends on them: a horizontal run
    must be inside a gap, and a vertical run must be inside one gap or in the
    side lane. Re-deriving those bands from box positions would be a second
    implementation that can disagree with the first.
    """

    boxes: list[Box]
    width: int
    height: int
    rows: list[range]
    gaps: list[range]
    lane_x: int

    def row_of(self, box_id: str, index: dict[str, int]) -> int:
        return index[box_id]


def _place(rows: list[list[Box]], style: Style) -> Placement:
    """Assign coordinates row by row, each row centred.

    A right-hand lane is reserved on every canvas, wide enough for a vertical
    run beyond every box. Reserved unconditionally rather than only when a
    long edge exists, because a lane that appears and disappears makes the
    canvas width depend on edge topology, and two diagrams of the same
    repository would then differ in width for no reason a reader can see.
    """
    row_widths = [sum(b.w for b in row) + style.gap_x * max(0, len(row) - 1) for row in rows]
    content = max(row_widths, default=0)
    width = content + 2 * style.margin + style.lane_gutter
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

    # One gap band per row, being the empty space below it. The last row's gap
    # is the bottom margin, which is why routing never needs to leave the
    # canvas to get underneath the final row.
    gaps: list[range] = []
    for i, extent in enumerate(extents):
        end = extents[i + 1].start if i + 1 < len(extents) else height
        gaps.append(range(extent.stop, end))
    lane_x = style.margin + content + style.lane_gutter // 2
    return Placement(placed, width, height, extents, gaps, lane_x)


# --------------------------------------------------------------------------
# Routing
# --------------------------------------------------------------------------


def _routes(
    edges: Sequence[DiagramEdge],
    place: Placement,
    diags: list[Diagnostic],
) -> list[Route]:
    """Orthogonal polylines that provably miss every box they do not connect.

    The previous version argued the invariant instead of guaranteeing it: it
    put the horizontal run at the midpoint of the whole vertical span, which
    for any edge skipping a layer lands *inside* an intervening row, and it
    routed same-row edges straight across their own row. Two ordinary shapes,
    a layer-skipping edge and a two-node cycle, drew arrows through box
    interiors while validation returned nothing, because the check the design
    names was omitted on the strength of that argument.

    The rule now is structural, so it holds whatever the rows contain:

    * every **horizontal** run lies inside a row gap, which has no boxes;
    * every **vertical** run lies inside a single gap, or in the side lane
      beyond every box.

    Three cases follow from it. Adjacent rows drop through the one gap between
    them. A longer forward edge goes out into the gap below the source, across
    to the lane, down the lane, and back in along the gap above the target.
    Same-row and backward edges leave and re-enter from below, since an arrow
    entering a box from above would read as a forward dependency.
    """
    index = {b.id: i for i, row in enumerate(place.rows) for b in place.boxes if _in(b, row)}
    by_id = {b.id: b for b in place.boxes}
    degree_out: dict[str, int] = {}
    degree_in: dict[str, int] = {}
    usable: list[DiagramEdge] = []
    for e in sorted(edges, key=lambda e: (e.src, e.dst)):
        if e.src not in by_id or e.dst not in by_id:
            # Diagnosed, never silently skipped. A `continue` here would turn a
            # derive defect into a clean canvas, hiding it from SVA-G-004,
            # which exists to catch exactly this.
            diags.append(
                Diagnostic(
                    code="SVA-G-004",
                    severity=Severity.ERROR,
                    message="edge endpoint is not a node in this spec, so no arrow was drawn",
                    subject=f"{e.src} -> {e.dst}",
                )
            )
            continue
        usable.append(e)
        degree_out[e.src] = degree_out.get(e.src, 0) + 1
        degree_in[e.dst] = degree_in.get(e.dst, 0) + 1

    out: list[Route] = []
    exits: dict[str, int] = {}
    entries: dict[str, int] = {}
    for e in usable:
        a, b = by_id[e.src], by_id[e.dst]
        exits[e.src] = exits.get(e.src, 0) + 1
        entries[e.dst] = entries.get(e.dst, 0) + 1
        ax = _fan(a, exits[e.src], degree_out[e.src])
        bx = _fan(b, entries[e.dst], degree_in[e.dst])
        ra, rb = index[e.src], index[e.dst]
        gap_a = _mid(place.gaps[ra])

        if rb == ra + 1:
            points = ((ax, a.bottom), (ax, gap_a), (bx, gap_a), (bx, b.y))
        elif rb > ra:
            gap_b = _mid(place.gaps[rb - 1])
            points = (
                (ax, a.bottom),
                (ax, gap_a),
                (place.lane_x, gap_a),
                (place.lane_x, gap_b),
                (bx, gap_b),
                (bx, b.y),
            )
        elif rb == ra:
            points = ((ax, a.bottom), (ax, gap_a), (bx, gap_a), (bx, b.bottom))
        else:
            gap_b = _mid(place.gaps[rb])
            points = (
                (ax, a.bottom),
                (ax, gap_a),
                (place.lane_x, gap_a),
                (place.lane_x, gap_b),
                (bx, gap_b),
                (bx, b.bottom),
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


def _in(box: Box, row: range) -> bool:
    return box.y == row.start


def _mid(gap: range) -> int:
    """The middle of a gap band, floored so it stays an integer."""
    return (gap.start + gap.stop) // 2


def _fan(box: Box, nth: int, degree: int) -> int:
    """The nth of `degree` attachment points, spread across the box width.

    Spread over the box's actual degree, not over a fixed number of slots. A
    fixed five collapsed at out-degree six: edges six and seven landed exactly
    on edges one and two, which is verbatim the failure this function exists to
    prevent, and the test that covered it used four edges. Real module
    out-degrees pass five routinely.

    Clamped so a narrow box still yields a point strictly inside itself rather
    than on its corner.
    """
    slots = max(1, degree)
    step = box.w // (slots + 1)
    if step < 1:
        # More edges than the box has pixels. Stacking at the centre is honest
        # about the crowding; inventing points outside the box is not.
        return box.x + box.w // 2
    return box.x + step * (1 + (nth - 1) % slots)


# --------------------------------------------------------------------------
# Engines
# --------------------------------------------------------------------------


def layered(spec: DiagramSpec, style: Style) -> Canvas:
    """Rows by dependency depth. Module deps, class hierarchy."""
    diags: list[Diagnostic] = []
    place = _place(_rows(spec, _boxes(spec, style), style.gap_x), style)
    routes = _routes(spec.edges, place, diags)
    return _canvas(spec, "layered", place, routes, diagnostics=tuple(diags))


def clustered(spec: DiagramSpec, style: Style) -> Canvas:
    """Layered, with each dependency level banded and labelled.

    A deliberate narrowing of design section 6.1, which described the bands as
    communities. Communities cannot appear here: they are chaotically sensitive
    to input perturbation and a promoted decision confines them to `derive`,
    which is also why the boxes on an architecture diagram already *are* the
    groups. Banding by dependency depth is the honest thing left to say about a
    row, and unlike a community label it is stable under a new import.

    One band per **level**, spanning every row that level wrapped into. Banding
    per row instead made the label false as soon as presentation and semantics
    diverged: forty boxes all at depth 0 produced bands labelled level 1
    through level 4, four confident wrong claims about dependency structure,
    and geometric containment held so validation said nothing.

    A level reached only by the cycle fallback is labelled as such rather than
    given a number, since its depth is a placement decision and not a measured
    fact.
    """
    boxes = _boxes(spec, style)
    levels = _levels(spec, boxes)
    rows = _rows(spec, boxes, style.gap_x)
    diags: list[Diagnostic] = []
    place = _place(rows, style)
    routes = _routes(spec.edges, place, diags)

    cyclic = _cyclic_ids(spec)
    by_level: dict[int, list[Box]] = {}
    for b in place.boxes:
        by_level.setdefault(levels.get(b.id, 0), []).append(b)
    bands = tuple(
        Band(
            label=(
                "in a cycle"
                if all(b.id in cyclic for b in members)
                else f"level {sorted(by_level).index(level) + 1}"
            ),
            y=min(b.y for b in members),
            h=max(b.bottom for b in members) - min(b.y for b in members),
            members=tuple(sorted(b.id for b in members)),
        )
        for level, members in sorted(by_level.items())
    )
    return _canvas(spec, "clustered", place, routes, bands, tuple(diags))


def grid(spec: DiagramSpec, style: Style) -> Canvas:
    """Uniform columns, for diagrams whose edges do not imply an order.

    ERD tables and any edgeless spec. Laying those out by depth would put every
    box in one row and imply a hierarchy that the data does not contain.
    """
    boxes = _boxes(spec, style)
    diags: list[Diagnostic] = []
    if not boxes:
        return _canvas(spec, "grid", _place([], style), [])
    widest = max(b.w for b in boxes)
    per_row = max(1, (MAX_ROW_WIDTH + style.gap_x) // (widest + style.gap_x))
    ordered = sorted(boxes, key=lambda b: b.id)
    rows = [ordered[i : i + per_row] for i in range(0, len(ordered), per_row)]
    place = _place(rows, style)
    routes = _routes(spec.edges, place, diags)
    return _canvas(spec, "grid", place, routes, diagnostics=tuple(diags))


def _canvas(
    spec: DiagramSpec,
    engine: str,
    place: Placement,
    routes: Sequence[Route],
    bands: tuple[Band, ...] = (),
    diagnostics: tuple[Diagnostic, ...] = (),
) -> Canvas:
    return Canvas(
        spec_id=spec.id,
        kind=spec.kind,
        engine=engine,
        title=spec.title,
        subtitle=spec.subtitle,
        width=place.width,
        height=place.height,
        boxes=tuple(place.boxes),
        routes=tuple(routes),
        bands=bands,
        parent=spec.parent,
        diagnostics=diagnostics,
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
