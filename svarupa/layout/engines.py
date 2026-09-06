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
from dataclasses import dataclass, replace
from itertools import pairwise

from svarupa.derive.base import DiagramEdge, DiagramSpec
from svarupa.diagnostics import Diagnostic, Severity
from svarupa.layout.geometry import Band, Box, Canvas, RegionBox, Route, Style
from svarupa.layout.sugiyama import DUMMY_WIDTH, Chain, layer_out
from svarupa.layout.text import advance, sanitize, truncate

__all__ = ["ENGINES", "clustered", "flow", "grid", "lay_out", "layered"]

# Rows wrap rather than growing without bound. A 4000px-wide row is not a
# diagram, it is a horizontal scroll bar.
MAX_ROW_WIDTH = 1280


# --------------------------------------------------------------------------
# Boxes
# --------------------------------------------------------------------------


def _boxes(
    spec: DiagramSpec,
    style: Style,
    sizes: Mapping[str, tuple[int, int]] | None = None,
) -> list[Box]:
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
        sub = truncate(sanitize(n.sublabel), style.sublabel_font_size, style.text_budget)
        width = advance(label, style.font_size) + 2 * style.box_pad_x
        if sub:
            width = max(width, advance(sub, style.sublabel_font_size) + 2 * style.box_pad_x)
        width = max(style.box_min_width, min(style.box_max_width, width))
        # Two lines when there is a sublabel, one when there is not.
        height = style.box_height_tall if sub else style.box_height
        if sizes and n.id in sizes:
            # An expansion needs this box big enough to hold a whole child
            # diagram. The override may only grow a box: shrinking one below
            # its label is the truncated-to-nothing failure again.
            width = max(width, sizes[n.id][0])
            height = max(height, sizes[n.id][1])
        out.append(
            Box(
                id=n.id,
                label=label,
                full_label=full,
                kind=n.kind,
                x=0,
                y=0,
                w=width,
                h=height,
                evidence=n.evidence,
                child_spec=n.child_spec,
                attrs=n.attrs,
                sublabel=sub,
            )
        )
    return out


def _region_rank(spec: DiagramSpec) -> dict[str, int]:
    """Region index per member, so rows can keep a region's members adjacent."""
    rank: dict[str, int] = {}
    for i, region in enumerate(spec.regions):
        for m in region.members:
            rank.setdefault(m, i)
    return rank


def _group_rows_by_region(rows: list[list[Box]], spec: DiagramSpec) -> list[list[Box]]:
    """Stable-sort each row so a region's members sit together.

    Presentation only: the barycentric order is kept within a region and
    among the unwrapped boxes, so crossings may rise a little where a
    boundary demands it. A boundary that cannot be drawn without enclosing a
    non-member is skipped and reported, so adjacency here is what makes
    boundaries drawable at all.
    """
    if not spec.regions:
        return rows
    rank = _region_rank(spec)
    last = len(spec.regions)
    return [sorted(row, key=lambda b: rank.get(b.id, last)) for row in rows]


def _regions(
    spec: DiagramSpec,
    boxes: Sequence[Box],
    style: Style,
    diags: list[Diagnostic],
    waypoints: frozenset[str] = frozenset(),
) -> tuple[RegionBox, ...]:
    """Boundary rectangles around their members, drawn only when honest.

    The rectangle is the members' bounding box padded by Archify's 30/30/30
    plus 20 at the bottom, with room for the label above the top row. If it
    would also enclose a non-member, or intersect another boundary, it is
    not drawn and a diagnostic says so: a boundary that visually claims a
    module it does not own is a false statement about deployment.
    """
    by_id = {b.id: b for b in boxes}
    out: list[RegionBox] = []
    for region in spec.regions:
        members = [by_id[m] for m in region.members if m in by_id]
        if not members:
            continue
        top_pad = style.region_pad + style.region_label_height
        x = min(b.x for b in members) - style.region_pad
        y = min(b.y for b in members) - top_pad
        right = max(b.right for b in members) + style.region_pad
        bottom = max(b.bottom for b in members) + style.region_pad + style.region_extra_bottom
        member_ids = set(region.members)
        # A waypoint is a bend in a line, not a box, and cannot intrude: a
        # skipping import inside a service once dropped the service's
        # boundary and printed the dummy's NUL-delimited id to the user.
        intruders = sorted(
            b.id
            for b in boxes
            if b.id not in member_ids
            and b.id not in waypoints
            and b.x < right
            and b.right > x
            and b.y < bottom
            and b.bottom > y
        )
        overlapping = [
            r.label
            for r in out
            if r.x < right and r.right > x and r.y < bottom and r.bottom > y
        ]
        if intruders or overlapping:
            what = (
                f"would enclose non-member(s) {intruders[:3]}"
                if intruders
                else f"would overlap boundary {overlapping[0]!r}"
            )
            diags.append(
                Diagnostic(
                    code="SVA-G-014",
                    severity=Severity.INFO,
                    message=(
                        f"a boundary was not drawn in this layout because it {what}; "
                        "its members are still shown, the wrapping is not"
                    ),
                    subject=region.label,
                    location=spec.id,
                )
            )
            continue
        out.append(
            RegionBox(
                id=region.id,
                label=sanitize(region.label),
                kind=region.kind,
                x=x,
                y=y,
                w=right - x,
                h=bottom - y,
                members=tuple(m for m in region.members if m in by_id),
                evidence=region.evidence,
            )
        )
    return tuple(out)


def _region_pad(spec: DiagramSpec, style: Style) -> int:
    """Extra margin a canvas needs so boundaries fit inside it."""
    if not spec.regions:
        return 0
    return style.region_pad + style.region_label_height + style.region_extra_bottom


def _settle_labels(
    routes: list[Route], boxes: Sequence[Box], style: Style, waypoints: frozenset[str]
) -> list[Route]:
    """Place each route label where it collides with nothing, or drop it.

    First choice is the midpoint of the longest segment; if that mask would
    cover a box or another label, the other segments are tried longest-first;
    if none is clear the label is dropped (the verb stays in the tooltip and
    the panel). Two opposite edges between the same pair share a segment and
    would otherwise stamp their verbs on top of each other, which is the
    `7122.26.62.5nimports` failure with words. Deterministic: routes are
    settled in (src, dst) order.
    """
    h = style.label_font_size + style.label_pad
    gap = style.label_gap
    solid = [b for b in boxes if b.id not in waypoints]
    placed: list[tuple[int, int, int, int]] = []

    def clear(cx: int, cy: int, w: int, own: Route) -> bool:
        x, y = cx - w // 2, cy - h // 2
        # Not over another route either: a mask on a bundle of lines hides
        # which line the verb belongs to.
        for other in routes:
            if other is not own and _segments_cross_rect(other.points, x, y, w, h):
                return False
        for b in solid:
            if x < b.right and x + w > b.x and y < b.bottom and y + h > b.y:
                return False
        for px, py, pw, ph in placed:
            if (
                x < px + pw + gap
                and x + w + gap > px
                and y < py + ph + gap
                and y + h + gap > py
            ):
                return False
        return True

    out: list[Route] = []
    for r in sorted(routes, key=lambda r: (r.src, r.dst)):
        if r.label_at is None or not r.label:
            out.append(r)
            continue
        segments = sorted(
            range(len(r.points) - 1),
            key=lambda i: (
                -(
                    abs(r.points[i + 1][0] - r.points[i][0])
                    + abs(r.points[i + 1][1] - r.points[i][1])
                )
            ),
        )
        chosen: tuple[int, int] | None = None
        for i in segments:
            (x0, y0), (x1, y1) = r.points[i], r.points[i + 1]
            cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
            if clear(cx, cy, r.label_w, r):
                chosen = (cx, cy)
                break
        if chosen is None:
            out.append(replace(r, label_at=None, label_w=0))
            continue
        placed.append((chosen[0] - r.label_w // 2, chosen[1] - h // 2, r.label_w, h))
        out.append(replace(r, label_at=chosen))
    return out


def _segments_cross_rect(
    points: Sequence[tuple[int, int]], x: int, y: int, w: int, h: int
) -> bool:
    """Whether any orthogonal segment of a polyline passes through a rectangle."""
    for (x0, y0), (x1, y1) in pairwise(points):
        lo_x, hi_x = min(x0, x1), max(x0, x1)
        lo_y, hi_y = min(y0, y1), max(y0, y1)
        if lo_x < x + w and hi_x > x and lo_y < y + h and hi_y > y:
            return True
    return False


def _label_anchor(
    points: Sequence[tuple[int, int]], label: str, style: Style
) -> tuple[tuple[int, int] | None, int]:
    """Where a route's label mask sits: the midpoint of its longest segment.

    Archify's rule; the mask is `label width + padding` wide and the validator
    checks it against boxes and other labels, so a label that would collide is
    a diagnostic rather than a smear.
    """
    if not label or len(points) < 2:
        return None, 0
    best = max(
        range(len(points) - 1),
        key=lambda i: (
            abs(points[i + 1][0] - points[i][0]) + abs(points[i + 1][1] - points[i][1])
        ),
    )
    (x0, y0), (x1, y1) = points[best], points[best + 1]
    width = advance(label, style.label_font_size) + 10
    return ((x0 + x1) // 2, (y0 + y1) // 2), width


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


def _levels(
    spec: DiagramSpec, boxes: Sequence[Box], sink_externals: bool = True
) -> dict[str, int]:
    """The dependency level of each box: declared if available, else inferred.

    External boxes (stores, buses, cloud APIs a module talks to) sink to the
    bottom layer whatever the inference said: they only ever receive arrows,
    and Archify puts what a system talks to below what talks. Left to the
    inference, a store used from inside a dependency cycle landed in the
    cycle's own row among the modules.
    """
    _ = boxes
    levels = dict(_declared_layers(spec) or _inferred_layers(spec))
    external = {n.id for n in spec.nodes if any(k == "external" for k, _ in n.attrs)}
    if sink_externals and external and len(external) < len(levels):
        bottom = max(v for k, v in levels.items() if k not in external) + 1
        for nid in external:
            levels[nid] = bottom
    return levels


def _cyclic_ids(spec: DiagramSpec) -> frozenset[str]:
    """Ids whose level came from the cycle fallback rather than from an order.

    Needed so a band over them can say so instead of claiming a level number,
    which for a cycle is a placement decision and not a measured fact.
    """
    if _declared_layers(spec) is not None:
        return frozenset()
    return _inferred_layers_cyclic(spec)


def _dummy_box(nid: str, style: Style) -> Box:
    """A waypoint that occupies a slot in a row.

    Evidence is not required of it and it carries none: it is not a claim about
    the codebase, it is a bend in a line. The renderer skips it, and `validate`
    is told which ids are waypoints so it does not demand a citation for a
    corner.
    """
    return Box(
        id=nid,
        label="",
        full_label="",
        kind="waypoint",
        x=0,
        y=0,
        w=DUMMY_WIDTH,
        h=style.box_height,
        evidence=(),
    )


def _ordered_rows(
    spec: DiagramSpec, boxes: Sequence[Box], style: Style
) -> tuple[list[list[Box]], list[Chain], frozenset[str], list[tuple[str, str]]]:
    """Rows ordered to reduce crossings, with waypoints for long edges.

    Replaces sorting each row by id, which is deterministic and close to the
    worst possible ordering: it ignores where a node's neighbours are, so edges
    cross maximally. The canonical id order is kept as the seed the sweep
    refines, so the result is still the same everywhere.
    """
    levels = _levels(spec, boxes)
    ids = [b.id for b in sorted(boxes, key=lambda b: b.id)]
    pairs = [(e.src, e.dst) for e in spec.edges]
    layered_out = layer_out(ids, pairs, levels)

    by_id = {b.id: b for b in boxes}
    rows: list[list[Box]] = []
    for row in layered_out.rows:
        rows.append([by_id[nid] if nid in by_id else _dummy_box(nid, style) for nid in row])
    # Edges the layering dropped because an endpoint is not a node here. They
    # travel back out rather than vanishing: the layering discards them
    # silently, and a producer-side guard that skips input the validator would
    # reject converts a failure into a pass.
    dropped = sorted(
        (e.src, e.dst) for e in spec.edges if e.src not in by_id or e.dst not in by_id
    )
    return rows, list(layered_out.chains), layered_out.dummies, dropped


def _dangling(dropped: Sequence[tuple[str, str]]) -> list[Diagnostic]:
    return [
        Diagnostic(
            code="SVA-G-004",
            severity=Severity.ERROR,
            message="edge endpoint is not a node in this spec, so no arrow was drawn",
            subject=f"{src} -> {dst}",
        )
        for src, dst in dropped
    ]


def _wrap(rows: list[list[Box]], style: Style) -> list[list[Box]]:
    """Split any row that would exceed the width bound.

    **Only for `grid`.** Wrapping is incompatible with layered routing: a long
    edge owns one waypoint per *layer*, and wrapping turns one layer into
    several rows, so a hop that was between adjacent rows suddenly spans three
    of them and runs straight through whatever is in between. Measured: a
    200-edge fixture produced 1,900 route-through-box violations.

    A layered diagram is therefore as wide as its widest layer, which is what
    every layered drawing tool does. The answer to a very wide layer is
    hierarchy, not folding, and that is the drill-down the architecture view
    already has and module-deps still needs.
    """
    out: list[list[Box]] = []
    for row in rows:
        current: list[Box] = []
        width = 0
        for b in row:
            step = b.w + (style.gap_x if current else 0)
            if current and width + step > MAX_ROW_WIDTH:
                out.append(current)
                current, width = [], 0
                step = b.w
            current.append(b)
            width += step
        out.append(current)
    return out


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


def _place(
    rows: list[list[Box]],
    style: Style,
    demand: Sequence[int] = (),
    pad: int = 0,
    align_left: bool = False,
) -> Placement:
    """Assign coordinates row by row, each row centred.

    The row *order* is decided before this runs. Placing is arithmetic; which
    box sits where is the part that decides whether the picture is readable.

    `demand[i]` is how many horizontal tracks the gap below row `i` must hold.
    The gap grows to fit them: twenty same-row edges squeezed into a fixed
    56px gap sat two pixels apart and read as one smear, which is the same
    failure as the shared rail at a smaller scale. Space is made for the lines
    the diagram actually has.
    """
    row_widths = [sum(b.w for b in row) + style.gap_x * max(0, len(row) - 1) for row in rows]
    content = max(row_widths, default=0)
    # `pad` is room for boundaries drawn around members: a region rectangle
    # extends past its members on every side, and the canvas must hold it.
    margin = style.margin + pad
    width = content + 2 * margin
    placed: list[Box] = []
    extents: list[range] = []
    y = margin
    for i, (row, row_width) in enumerate(zip(rows, row_widths, strict=True)):
        # Rows centre by default. With boundaries, rows align left: a region's
        # members lead every row, so left alignment stacks them into one
        # column a rectangle can wrap, while centring shifts a narrow row's
        # members under a wide row's outsiders and the boundary would have to
        # enclose them.
        x = margin if align_left else margin + (content - row_width) // 2
        # Rows are as tall as their tallest box. Uniform height was fine while
        # every box was a card; an expanded container is a diagram inside a
        # box, and squeezing it to card height would be scaling by another
        # name. Boxes centre vertically in their row so a row of ordinary
        # cards next to one container still reads as one row.
        row_h = max((b.h for b in row), default=style.box_height)
        for b in row:
            # `replace`, never a field-by-field copy. The copy listed every
            # field by hand, and when a field was added it silently dropped
            # it, with nothing failing because the empty value was legal. A
            # copy that enumerates fields is wrong the day the dataclass grows.
            placed.append(replace(b, x=x, y=y + (row_h - b.h) // 2))
            x += b.w + style.gap_x
        extents.append(range(y, y + row_h))
        tracks = demand[i] if i < len(demand) else 0
        gap = max(style.gap_y, 12 + 10 * tracks)
        y += row_h + gap
    # A full gap below the last row, not just the margin: same-row edges route
    # through the gap beneath their own row, and the bottom row has one too.
    height = max(y + margin - style.gap_y // 2, 2 * margin + style.box_height)

    gaps: list[range] = []
    for i, extent in enumerate(extents):
        end_y = extents[i + 1].start if i + 1 < len(extents) else height
        gaps.append(range(extent.stop, end_y))
    return Placement(placed, width, height, extents, gaps, width - style.margin // 2)


# --------------------------------------------------------------------------
# Routing
# --------------------------------------------------------------------------


def _routes(
    chains: Sequence[Chain],
    place: Placement,
    edges: Mapping[tuple[str, str], DiagramEdge],
    diags: list[Diagnostic],
    style: Style,
) -> list[Route]:
    """One polyline per edge, following the slots its chain passes through.

    Because a long edge owns a dummy slot in every row it crosses, each hop is
    between adjacent rows and runs in the empty gap between them. No shared
    side lane, so no rail, and the invariant that mattered still holds: every
    horizontal run is inside a gap and every vertical run is inside one gap.

    Attachment points are spread across a box's width by the edge's rank among
    that box's connections, so parallel edges leave from visibly different
    places instead of stacking into one thick line.
    """
    by_id = {b.id: b for b in place.boxes}
    rows = place.rows

    exits: dict[str, list[str]] = {}
    entries: dict[str, list[str]] = {}
    for c in sorted(chains, key=lambda c: (c.src, c.dst)):
        head, tail = c.nodes[0], c.nodes[-1]
        if head in by_id and tail in by_id:
            exits.setdefault(head, []).append(tail)
            entries.setdefault(tail, []).append(head)

    def row_index(box: Box) -> int:
        return _row_of(box, rows)

    # Every edge that runs horizontally through a gap gets its own y inside
    # it. Sharing one track is what turned a cycle-heavy diagram into a row of
    # overlapping stubs: the routes were individually correct and collectively
    # one thick line.
    tracks: dict[int, dict[tuple[str, str], int]] = {}
    for c in sorted(chains, key=lambda c: (c.src, c.dst)):
        head, tail = c.nodes[0], c.nodes[-1]
        if head not in by_id or tail not in by_id:
            continue
        gap = _row_of(by_id[head], rows)
        tracks.setdefault(gap, {})[(c.src, c.dst)] = len(tracks.get(gap, {}))

    out: list[Route] = []
    for c in sorted(chains, key=lambda c: (c.src, c.dst)):
        edge = edges.get((c.src, c.dst))
        if edge is None or c.src not in by_id or c.dst not in by_id:
            diags.append(
                Diagnostic(
                    code="SVA-G-004",
                    severity=Severity.ERROR,
                    message="edge endpoint is not a node in this spec, so no arrow was drawn",
                    subject=f"{c.src} -> {c.dst}",
                )
            )
            continue

        # `nodes` is in layer order, so the polyline is built from its ends
        # rather than from src/dst, which for a reversed edge point the other
        # way. The Route still records the true src and dst.
        head, tail = c.nodes[0], c.nodes[-1]
        if head not in by_id or tail not in by_id:
            continue
        a, b = by_id[head], by_id[tail]
        ax = _fan(a, exits[head].index(tail) + 1, len(exits[head]))
        bx = _fan(b, entries[tail].index(head) + 1, len(entries[tail]))
        ra, rb = row_index(a), row_index(b)

        points: list[tuple[int, int]] = []
        if rb == ra:
            # Same row: down into the gap below, across, back up. The gap is
            # empty of boxes by construction, and each such edge gets its own
            # track inside it so they do not stack.
            mid = _track(place.gaps[ra], tracks[ra][(c.src, c.dst)], len(tracks[ra]))
            points = [(ax, a.bottom), (ax, mid), (bx, mid), (bx, b.bottom)]
        elif rb > ra and not [n for n in c.nodes[1:-1] if n in by_id] and rb > ra + 1:
            # No waypoints and not adjacent: `grid` builds rows that are not
            # layers, so nothing reserved space in between. Go round the side,
            # which is ugly and correct, rather than through.
            gap_a = _mid(place.gaps[ra])
            gap_b = _mid(place.gaps[rb - 1])
            lane = place.lane_x
            points = [
                (ax, a.bottom),
                (ax, gap_a),
                (lane, gap_a),
                (lane, gap_b),
                (bx, gap_b),
                (bx, b.y),
            ]
        elif rb > ra:
            waypoints = [by_id[n] for n in c.nodes[1:-1] if n in by_id]
            points = [(ax, a.bottom)]
            previous = a
            for w in waypoints:
                gap = _mid(place.gaps[row_index(previous)])
                wx = w.x + w.w // 2
                points += [(points[-1][0], gap), (wx, gap), (wx, w.y), (wx, w.bottom)]
                previous = w
            gap = _mid(place.gaps[row_index(previous)])
            points += [(points[-1][0], gap), (bx, gap), (bx, b.y)]
        else:
            # A reversed edge: it was flipped for layering, so it runs upward
            # here. Enter the target from below so the arrowhead still points
            # at the real dependency.
            gap_a = _mid(place.gaps[ra])
            gap_b = _mid(place.gaps[rb])
            points = [(ax, a.bottom), (ax, gap_a), (bx, gap_a), (bx, gap_b), (bx, b.bottom)]

        # A reversed chain was laid out downward for the layering, so its
        # polyline runs from the layering source. Flipping the point order
        # makes it run from the real source instead: the same pixels, but the
        # arrowhead now lands on the module that is actually depended upon.
        # Without this a dependency cycle draws both of its arrows backwards,
        # which is a wrong claim rather than an ugly one.
        if c.reversed_:
            points.reverse()

        label = sanitize(edge.label)
        anchor, label_w = _label_anchor(points, label, style)
        out.append(
            Route(
                src=c.src,
                dst=c.dst,
                label=label,
                points=tuple(points),
                evidence=edge.evidence,
                resolution=edge.resolution,
                weight=edge.weight,
                variant=edge.variant,
                note=edge.note,
                label_at=anchor,
                label_w=label_w,
            )
        )
    return out


def _row_of(box: Box, rows: Sequence[range]) -> int:
    """The row whose vertical extent contains this box.

    Containment, not `y == extent.start`: boxes centre vertically inside a
    variable-height row, so only the tallest box in a row still starts at the
    row's top edge.
    """
    for i, extent in enumerate(rows):
        if extent.start <= box.y < extent.stop:
            return i
    return 0


def _mid(gap: range) -> int:
    """The middle of a gap band, floored so it stays an integer."""
    return (gap.start + gap.stop) // 2


def _track(gap: range, nth: int, total: int) -> int:
    """The nth of `total` horizontal tracks inside a gap.

    Kept clear of both edges so a track never grazes the row above or below,
    which the crossing check treats as touching rather than crossing but which
    reads as a line stuck to a box.
    """
    usable = max(1, (gap.stop - gap.start) - 12)
    step = max(1, usable // max(1, total + 1))
    return gap.start + 6 + step * (nth % max(1, total) + 1)


def _fan(box: Box, nth: int, degree: int) -> int:
    """The nth of `degree` attachment points, spread across the box width.

    Spread over the actual degree, not a fixed slot count: a fixed five
    collapsed at out-degree six, landing edges six and seven exactly on one and
    two, which is the failure this exists to prevent.
    """
    slots = max(1, degree)
    step = box.w // (slots + 1)
    if step < 1:
        return box.x + box.w // 2
    return box.x + step * (1 + (nth - 1) % slots)


# --------------------------------------------------------------------------
# Engines
# --------------------------------------------------------------------------


def layered(
    spec: DiagramSpec,
    style: Style,
    sizes: Mapping[str, tuple[int, int]] | None = None,
) -> Canvas:
    """Rows by dependency depth. Module deps, class hierarchy."""
    diags: list[Diagnostic] = []
    rows, chains, dummies, dropped = _ordered_rows(spec, _boxes(spec, style, sizes), style)
    rows = _group_rows_by_region(rows, spec)
    diags.extend(_dangling(dropped))
    place = _place(
        rows, style, _gap_demand(rows, chains), _region_pad(spec, style), bool(spec.regions)
    )
    routes = _settle_labels(
        _routes(chains, place, _edge_map(spec), diags, style), place.boxes, style, dummies
    )
    regions = _regions(spec, place.boxes, style, diags, dummies)
    return _canvas(
        spec,
        "layered",
        place,
        routes,
        diagnostics=tuple(diags),
        waypoints=dummies,
        regions=regions,
    )


def _edge_map(spec: DiagramSpec) -> dict[tuple[str, str], DiagramEdge]:
    return {(e.src, e.dst): e for e in spec.edges}


def _gap_demand(rows: Sequence[Sequence[Box]], chains: Sequence[Chain]) -> list[int]:
    """How many horizontal tracks each row's lower gap needs.

    Same-row edges take one track each in the gap below their row; a hop of a
    multi-row chain takes one in the gap it crosses. Counted here so placement
    can size the gaps before any route exists.
    """
    row_of = {b.id: i for i, row in enumerate(rows) for b in row}
    demand = [0] * len(rows)
    for c in chains:
        hops = list(zip(c.nodes, c.nodes[1:], strict=False))
        for a, b in hops:
            ra, rb = row_of.get(a), row_of.get(b)
            if ra is None or rb is None:
                continue
            demand[min(ra, rb) if ra != rb else ra] += 1
    return demand


def clustered(
    spec: DiagramSpec,
    style: Style,
    sizes: Mapping[str, tuple[int, int]] | None = None,
) -> Canvas:
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
    boxes = _boxes(spec, style, sizes)
    levels = _levels(spec, boxes)
    rows, chains, dummies, dropped = _ordered_rows(spec, boxes, style)
    rows = _group_rows_by_region(rows, spec)
    diags: list[Diagnostic] = list(_dangling(dropped))
    place = _place(
        rows, style, _gap_demand(rows, chains), _region_pad(spec, style), bool(spec.regions)
    )
    routes = _settle_labels(
        _routes(chains, place, _edge_map(spec), diags, style), place.boxes, style, dummies
    )
    regions = _regions(spec, place.boxes, style, diags, dummies)

    cyclic = _cyclic_ids(spec)
    by_level: dict[int, list[Box]] = {}
    for b in place.boxes:
        if b.id in dummies:
            continue
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
    return _canvas(
        spec,
        "clustered",
        place,
        routes,
        bands,
        tuple(diags),
        waypoints=dummies,
        regions=regions,
    )


def grid(
    spec: DiagramSpec,
    style: Style,
    sizes: Mapping[str, tuple[int, int]] | None = None,
) -> Canvas:
    """Uniform columns, for diagrams whose edges do not imply an order.

    ERD tables and any edgeless spec. Laying those out by depth would put every
    box in one row and imply a hierarchy that the data does not contain.
    """
    boxes = _boxes(spec, style, sizes)
    diags: list[Diagnostic] = []
    if not boxes:
        return _canvas(spec, "grid", _place([], style), [])
    widest = max(b.w for b in boxes)
    per_row = max(1, (MAX_ROW_WIDTH + style.gap_x) // (widest + style.gap_x))
    ordered = sorted(boxes, key=lambda b: b.id)
    rows = _wrap([ordered[i : i + per_row] for i in range(0, len(ordered), per_row)], style)
    place = _place(rows, style)
    chains = [Chain(e.src, e.dst, (e.src, e.dst)) for e in spec.edges]
    routes = _settle_labels(
        _routes(chains, place, _edge_map(spec), diags, style), place.boxes, style, frozenset()
    )
    return _canvas(spec, "grid", place, routes, diagnostics=tuple(diags))


def _canvas(
    spec: DiagramSpec,
    engine: str,
    place: Placement,
    routes: Sequence[Route],
    bands: tuple[Band, ...] = (),
    diagnostics: tuple[Diagnostic, ...] = (),
    waypoints: frozenset[str] = frozenset(),
    regions: tuple[RegionBox, ...] = (),
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
        regions=regions,
        parent=spec.parent,
        diagnostics=diagnostics,
        waypoints=waypoints,
    )


# --------------------------------------------------------------------------
# Flow: left to right, for the system view
# --------------------------------------------------------------------------


def flow(
    spec: DiagramSpec,
    style: Style,
    sizes: Mapping[str, tuple[int, int]] | None = None,
) -> Canvas:
    """Columns read left to right: sources on the left, sinks on the right.

    An architecture story runs "requests come in here, data ends up there", and
    top-to-bottom layering does not read that way. Small by nature, a system
    view holds services and stores rather than every module, so the router is
    simpler than the layered one: adjacent columns route through the gap
    between them, and an edge that skips columns runs through a reserved
    corridor above every box, which is box-free by construction and checked by
    the same crossing validation as everything else.
    """
    boxes = _boxes(spec, style, sizes)
    # No external sinking here: in columns, one trailing column stacks the
    # externals and a corridor drop into a lower one crosses the ones above
    # it, which withheld the System view on the acceptance repo.
    levels = _levels(spec, boxes, sink_externals=False)
    diags: list[Diagnostic] = list(
        _dangling(
            sorted(
                (e.src, e.dst)
                for e in spec.edges
                if e.src not in {b.id for b in boxes} or e.dst not in {b.id for b in boxes}
            )
        )
    )

    columns: dict[int, list[Box]] = {}
    for b in sorted(boxes, key=lambda b: b.id):
        columns.setdefault(levels.get(b.id, 0), []).append(b)

    # The corridor above the boxes, sized by how many skipping edges need it.
    ids = {b.id for b in boxes}

    # Everything that is not a simple forward hop takes the corridor: skips,
    # same-column edges, and backward edges. The first version collected skips
    # with abs(diff) != 1 but routed with `diff == 1`, so a backward edge
    # between adjacent columns fell between the two definitions and crashed on
    # a list lookup. One predicate, used by both sides.
    def takes_corridor(src: str, dst: str) -> bool:
        return levels.get(dst, 0) - levels.get(src, 0) != 1

    skips = sorted(
        (e.src, e.dst)
        for e in spec.edges
        if e.src in ids and e.dst in ids and takes_corridor(e.src, e.dst)
    )
    corridor_h = 12 + 12 * len(skips)
    # Stage frames (regions) need room on every side and between columns:
    # two adjacent frames each pad 30px, so the column gap grows to hold both.
    pad = _region_pad(spec, style)
    col_gap = (
        max(style.gap_x * 3, 2 * style.region_pad + style.gap_x)
        if spec.regions
        else style.gap_x * 3
    )
    top = style.margin + pad + corridor_h

    placed: dict[str, Box] = {}
    gaps: list[tuple[int, int]] = []  # x-extent of the gap after each column
    x = style.margin + pad
    tallest = 0
    for level in sorted(columns):
        col = columns[level]
        col_w = max(b.w for b in col)
        height = sum(b.h for b in col) + style.gap_y * (len(col) - 1)
        tallest = max(tallest, height)
        y = top
        for b in col:
            placed[b.id] = replace(b, x=x + (col_w - b.w) // 2, y=y)
            y += b.h + style.gap_y
        gaps.append((x + col_w, x + col_w + col_gap))
        x += col_w + col_gap

    # Centre every column vertically against the tallest.
    for level in sorted(columns):
        col = columns[level]
        height = sum(b.h for b in col) + style.gap_y * (len(col) - 1)
        shift = (tallest - height) // 2
        for b in col:
            placed[b.id] = replace(placed[b.id], y=placed[b.id].y + shift)

    width = x - col_gap + style.margin + pad + style.lane_gutter
    height = top + tallest + style.margin + pad

    # Routes. Adjacent columns cross their shared gap on a per-edge track;
    # skipping edges climb into the corridor.
    routes: list[Route] = []
    # Tracks are budgeted per gap, not per diagram. Dividing by the whole
    # spec's edge count skewed every track toward the gap's left edge and let
    # two edges through one gap land on the same x, which is the smear failure
    # the layered rewrite was named for, at smaller scale.
    gap_budget: dict[int, int] = {}
    edge_map = _edge_map(spec)
    for (src, dst), _e in sorted(edge_map.items()):
        if src in placed and dst in placed and not takes_corridor(src, dst):
            gap_budget[levels.get(src, 0)] = gap_budget.get(levels.get(src, 0), 0) + 1
    track_use: dict[int, int] = {}
    exits: dict[str, list[str]] = {}
    entries: dict[str, list[str]] = {}
    for e in sorted(spec.edges, key=lambda e: (e.src, e.dst)):
        if e.src in placed and e.dst in placed:
            exits.setdefault(e.src, []).append(e.dst)
            entries.setdefault(e.dst, []).append(e.src)

    def fan_y(b: Box, nth: int, total: int) -> int:
        slots = max(1, total)
        step = b.h // (slots + 1)
        return b.y + max(1, step) * (1 + (nth - 1) % slots)

    # A column's horizontal extent: a corridor edge climbs and drops BESIDE
    # the whole column, never at its own box's edge. Dropping at `b.x - 10`
    # ran through every wider box stacked above the target in that column,
    # which withheld the request-flow views on the acceptance repo.
    col_left = {
        level: min(placed[b.id].x for b in col) for level, col in columns.items() if col
    }
    col_right = {
        level: max(placed[b.id].right for b in col) for level, col in columns.items() if col
    }

    for i, ((src, dst), edge) in enumerate(sorted(edge_map.items())):
        if src not in placed or dst not in placed:
            continue
        a, b = placed[src], placed[dst]
        ay = fan_y(a, exits[src].index(dst) + 1, len(exits[src]))
        by = fan_y(b, entries[dst].index(src) + 1, len(entries[dst]))
        la, lb = levels.get(src, 0), levels.get(dst, 0)
        if not takes_corridor(src, dst):
            gx0, gx1 = gaps[sorted(columns).index(la)]
            used = track_use.get(la, 0)
            track_use[la] = used + 1
            span = max(1, gx1 - gx0 - 16)
            tx = gx0 + 8 + (used * span) // max(1, gap_budget.get(la, 1))
            points = ((a.right, ay), (tx, ay), (tx, by), (b.x, by))
        else:
            # Through the corridor above everything, one lane per edge.
            lane_y = style.margin + 6 + 12 * skips.index((src, dst))
            out_x = col_right[la] + 10 + 4 * (exits[src].index(dst))
            backward = lb <= la
            # A backward edge enters its target from the right, so the
            # arrowhead points against the flow, which is what a backward
            # dependency is.
            in_edge = b.right if backward else b.x
            in_x = (
                (col_right[lb] + 10 + 4 * entries[dst].index(src))
                if backward
                else (col_left[lb] - 10 - 4 * entries[dst].index(src))
            )
            points = (
                (a.right, ay),
                (out_x, ay),
                (out_x, lane_y),
                (in_x, lane_y),
                (in_x, by),
                (in_edge, by),
            )
        flabel = sanitize(edge.label)
        anchor, label_w = _label_anchor(points, flabel, style)
        routes.append(
            Route(
                src=src,
                dst=dst,
                label=flabel,
                points=points,
                evidence=edge.evidence,
                resolution=edge.resolution,
                weight=edge.weight,
                variant=edge.variant,
                note=edge.note,
                label_at=anchor,
                label_w=label_w,
            )
        )
        _ = i

    routes = _settle_labels(routes, list(placed.values()), style, frozenset())
    regions = _regions(spec, list(placed.values()), style, diags)
    return Canvas(
        spec_id=spec.id,
        kind=spec.kind,
        engine="flow",
        title=spec.title,
        subtitle=spec.subtitle,
        width=width,
        height=height,
        boxes=tuple(placed[b.id] for b in sorted(boxes, key=lambda b: b.id)),
        routes=tuple(routes),
        regions=regions,
        parent=spec.parent,
        diagnostics=tuple(diags),
    )


ENGINES: dict[
    str, Callable[[DiagramSpec, Style, Mapping[str, tuple[int, int]] | None], Canvas]
] = {
    "layered": layered,
    "clustered": clustered,
    "grid": grid,
    "flow": flow,
}


def lay_out(
    spec: DiagramSpec,
    style: Style,
    engine: str,
    sizes: Mapping[str, tuple[int, int]] | None = None,
) -> Canvas:
    """Run a named engine.

    An unknown name raises rather than falling back. A silent substitution
    would produce a diagram in the wrong shape while the run still looked
    successful, which is the same refusal `cluster` makes about backends.
    """
    if engine not in ENGINES:
        raise KeyError(f"unknown layout engine {engine!r}; expected one of {sorted(ENGINES)}")
    return ENGINES[engine](spec, style, sizes)
