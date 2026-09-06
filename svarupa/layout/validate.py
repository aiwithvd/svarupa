"""Geometry validation: the last check that does not need a browser.

Once coordinates are integers in a file, every claim a diagram makes about
itself is arithmetic. Boxes overlap or they do not; a label fits or it does
not; an arrow ends on a box that exists or it does not. Checking here is the
difference between a rendering bug found by a test and one found by a reader
who then stops trusting the artifact.

**A violation fails the diagram, not the run.** Same shape as `derive_all`:
one broken canvas does not lose the others, and the CLI exits non-zero so
nothing bad is emitted silently. That is not the "partial failure must degrade"
decision being stretched to excuse a bug, though. A geometry violation is
always *our* defect rather than hostile input, so it is reported at ERROR and
the artifact records that the diagram was withheld, with the reason.
"""

from __future__ import annotations

from svarupa.diagnostics import Diagnostic, Severity
from svarupa.layout.geometry import Canvas, Style
from svarupa.layout.text import advance, sanitize

__all__ = ["MIN_GAP", "validate"]

# Boxes must clear each other by this much. Zero would permit shared edges,
# which read as one merged box and hide a real overlap bug behind a pixel.
MIN_GAP = 4


def _err(code: str, subject: str, message: str) -> Diagnostic:
    return Diagnostic(code=code, severity=Severity.ERROR, message=message, subject=subject)


def validate(canvas: Canvas, style: Style) -> tuple[Diagnostic, ...]:
    """Every geometric claim the canvas makes. Empty result means drawable.

    **Waypoints are exempt from three checks, and the exemption is about what
    they are rather than about making the checks pass.** A waypoint is a slot a
    long edge owns in a row it crosses, so it has somewhere of its own to run.
    It is never drawn, so it cannot be crossed and it has no label to fit; it is
    not a claim about the codebase, so it has no source location to cite.

    It is *not* exempt from overlap or bounds. Occupying real space is the
    entire reason it exists, and a waypoint sitting on top of a box would put
    two things in one place.
    """
    out: list[Diagnostic] = []
    out.extend(_check_integers(canvas))
    out.extend(_check_evidence(canvas, canvas.waypoints))
    out.extend(_check_ids(canvas))
    out.extend(_check_boxes(canvas, style, canvas.waypoints))
    out.extend(_check_overlap(canvas))
    out.extend(_check_routes(canvas))
    out.extend(_check_crossings(canvas, canvas.waypoints))
    out.extend(_check_bands(canvas))
    out.extend(_check_regions(canvas))
    out.extend(_check_labels(canvas, style))
    return tuple(out)


def _check_integers(canvas: Canvas) -> list[Diagnostic]:
    """Every coordinate is an `int`, checked by type and not by range.

    This is the check that catches non-finite values, which the plan names and
    which range comparisons cannot see: `nan < 0`, `nan > width` and every
    overlap comparison are all `False`, so a box at `(nan, nan)` passed every
    other check on this canvas. A check against a pathological value has to be
    a type or identity test, never an ordering test.

    It also moves the integer promise from a property of the engines to a
    property of the gate. Byte-identity across platforms rests on it, and a
    float that survives to the artifact differs in its last bits.
    """
    out: list[Diagnostic] = []

    def check(subject: str, **values: object) -> None:
        for name, v in values.items():
            if type(v) is not int:
                out.append(
                    _err(
                        "SVA-G-010",
                        subject,
                        f"{name} is {v!r} ({type(v).__name__}), not an int",
                    )
                )

    check(canvas.spec_id, width=canvas.width, height=canvas.height)
    for b in canvas.boxes:
        check(b.id, x=b.x, y=b.y, w=b.w, h=b.h)
    for r in canvas.routes:
        for i, (px, py) in enumerate(r.points):
            check(f"{r.src} -> {r.dst}", **{f"points[{i}].x": px, f"points[{i}].y": py})
    for band in canvas.bands:
        check(band.label, y=band.y, h=band.h)
    return out


def _check_evidence(canvas: Canvas, waypoints: frozenset[str]) -> list[Diagnostic]:
    """No box, no arrow, without a source location.

    `Box` and `Route` deliberately have no constructor check, so this is the
    only place the product's central promise is enforced on the drawn form. It
    is enforced here for the same reason `build` re-validates evidence rather
    than trusting `Node.__post_init__`: constructor validation is a
    convenience, the pipeline gate is the contract. Layout is the last gate
    before a browser.
    """
    out: list[Diagnostic] = []
    out.extend(
        _err("SVA-G-009", b.id, "box carries no evidence, so it cannot be clicked through")
        for b in canvas.boxes
        if not b.evidence and b.id not in waypoints
    )
    out.extend(
        _err("SVA-G-009", f"{r.src} -> {r.dst}", "route carries no evidence")
        for r in canvas.routes
        if not r.evidence
    )
    return out


def _check_crossings(canvas: Canvas, waypoints: frozenset[str]) -> list[Diagnostic]:
    """No route segment may pass through a box it does not connect.

    Design section 6.1 names this invariant. It was previously argued away in a
    routing docstring, which claimed vertical segments stay in the row gaps so
    the check was unnecessary. Both halves were false: two ordinary shapes, an
    edge skipping a layer and a two-node cycle, drew arrows straight through
    box interiors while validation returned nothing.

    Polylines are orthogonal and coordinates are integers, so this is interval
    arithmetic. The box interior is open: a segment running exactly along a box
    edge is touching, not crossing, which is how a route legitimately leaves
    the box it starts on.
    """
    out: list[Diagnostic] = []
    for r in canvas.routes:
        endpoints = {r.src, r.dst}
        for (x1, y1), (x2, y2) in zip(r.points, r.points[1:], strict=False):
            lo_x, hi_x = min(x1, x2), max(x1, x2)
            lo_y, hi_y = min(y1, y2), max(y1, y2)
            for b in canvas.boxes:
                if b.id in endpoints or b.id in waypoints:
                    continue
                if lo_x < b.right and hi_x > b.x and lo_y < b.bottom and hi_y > b.y:
                    out.append(
                        _err(
                            "SVA-G-011",
                            f"{r.src} -> {r.dst}",
                            f"segment ({x1},{y1})-({x2},{y2}) crosses box {b.id!r} "
                            f"at ({b.x},{b.y})+{b.w}x{b.h}",
                        )
                    )
    return out


def _check_ids(canvas: Canvas) -> list[Diagnostic]:
    """Duplicate ids are checked first, and separately.

    The viewer keys boxes by id, so a duplicate silently loses one box. It also
    makes every later message ambiguous, which is why it is its own code rather
    than being folded into the overlap check that would also fire.

    Note ids may legitimately be the empty string: the repository root is a
    real module whose id is `""`. Any check written as `if not box.id` would
    therefore reject a valid diagram, so emptiness is never treated as absence.
    """
    seen: dict[str, int] = {}
    for b in canvas.boxes:
        seen[b.id] = seen.get(b.id, 0) + 1
    return [
        _err("SVA-G-007", bid, f"appears {n} times on one canvas")
        for bid, n in sorted(seen.items())
        if n > 1
    ]


def _check_boxes(canvas: Canvas, style: Style, waypoints: frozenset[str]) -> list[Diagnostic]:
    out: list[Diagnostic] = []
    for b in canvas.boxes:
        if b.id in waypoints:
            # Still bounds-checked below via the shared branch; a waypoint has
            # no text, so only the label checks are skipped.
            if b.x < 0 or b.y < 0 or b.right > canvas.width or b.bottom > canvas.height:
                out.append(_err("SVA-G-002", b.id, "waypoint falls outside the canvas"))
            continue
        if b.w <= 0 or b.h <= 0:
            out.append(_err("SVA-G-002", b.id, f"has non-positive size {b.w}x{b.h}"))
            continue
        if b.x < 0 or b.y < 0 or b.right > canvas.width or b.bottom > canvas.height:
            out.append(
                _err(
                    "SVA-G-002",
                    b.id,
                    f"at ({b.x},{b.y})+{b.w}x{b.h} falls outside the "
                    f"{canvas.width}x{canvas.height} canvas",
                )
            )
        if sanitize(b.label) != b.label:
            out.append(
                _err(
                    "SVA-G-008",
                    b.id,
                    "label was not sanitized, so its drawn width is not what "
                    "was measured and its text may not read as stored",
                )
            )
        budget = b.w - 2 * style.box_pad_x
        # A box truncated down to nothing passes a width check trivially, and
        # is worse than an overflowing one: a reader cannot tell what it is.
        # Found by a test that expected an impossible style to be rejected and
        # watched it pass instead, because every label had been emptied.
        if b.full_label and not b.label:
            out.append(
                _err(
                    "SVA-G-003",
                    b.id,
                    f"has no room for any of {b.full_label!r} in {budget}px; "
                    "an unlabelled box cannot be identified",
                )
            )
            continue
        drawn = advance(b.label, style.font_size)
        if drawn > budget:
            out.append(
                _err(
                    "SVA-G-003",
                    b.id,
                    f"label {b.label!r} needs {drawn}px but the box allows {budget}px",
                )
            )
    return out


def _check_overlap(canvas: Canvas) -> list[Diagnostic]:
    out: list[Diagnostic] = []
    boxes = sorted(canvas.boxes, key=lambda b: (b.y, b.x, b.id))
    for i, a in enumerate(boxes):
        for b in boxes[i + 1 :]:
            # Rows are laid out top to bottom, so once a later box starts below
            # this one's bottom edge plus the gap, nothing after it can touch.
            if b.y >= a.bottom + MIN_GAP:
                break
            if a.overlaps(b, MIN_GAP):
                out.append(
                    _err(
                        "SVA-G-001",
                        f"{a.id} / {b.id}",
                        f"overlap or sit closer than {MIN_GAP}px: "
                        f"({a.x},{a.y})+{a.w}x{a.h} against ({b.x},{b.y})+{b.w}x{b.h}",
                    )
                )
    return out


def _check_routes(canvas: Canvas) -> list[Diagnostic]:
    out: list[Diagnostic] = []
    ids = {b.id for b in canvas.boxes}
    for r in canvas.routes:
        missing = [end for end in (r.src, r.dst) if end not in ids]
        if missing:
            out.append(
                _err(
                    "SVA-G-004",
                    f"{r.src} -> {r.dst}",
                    f"endpoint(s) {missing} are not boxes on this canvas",
                )
            )
            continue
        if len(r.points) < 2:
            out.append(
                _err(
                    "SVA-G-005",
                    f"{r.src} -> {r.dst}",
                    f"has {len(r.points)} point(s); a polyline needs at least 2",
                )
            )
            continue
        outside = [
            p
            for p in r.points
            if not (0 <= p[0] <= canvas.width and 0 <= p[1] <= canvas.height)
        ]
        if outside:
            out.append(
                _err(
                    "SVA-G-005",
                    f"{r.src} -> {r.dst}",
                    f"leaves the canvas at {outside[:3]}",
                )
            )
        # An arrow must start and end on the boxes it claims to connect.
        # Without this the polyline could be geometrically fine and still point
        # at the wrong pair, which is a wrong claim rather than an ugly one.
        for label, end, box_id in (
            ("starts", r.points[0], r.src),
            ("ends", r.points[-1], r.dst),
        ):
            box = canvas.box(box_id)
            assert box is not None  # membership checked above
            on_x = box.x - MIN_GAP <= end[0] <= box.right + MIN_GAP
            on_y = box.y - MIN_GAP <= end[1] <= box.bottom + MIN_GAP
            if not (on_x and on_y):
                out.append(
                    _err(
                        "SVA-G-005",
                        f"{r.src} -> {r.dst}",
                        f"{label} at {end}, which is not on box {box_id!r} "
                        f"at ({box.x},{box.y})+{box.w}x{box.h}",
                    )
                )
    return out


def _check_bands(canvas: Canvas) -> list[Diagnostic]:
    """A band label is a claim about its members, so containment is checked.

    A band labelled "layer 3" that visually holds a layer-5 box tells a reader
    something false about the dependency structure. The label and the geometry
    have to agree for the same reason an edge label and its evidence do.
    """
    out: list[Diagnostic] = []
    ids = {b.id for b in canvas.boxes}
    for band in canvas.bands:
        missing = sorted(m for m in band.members if m not in ids)
        if missing:
            out.append(
                _err("SVA-G-006", band.label, f"claims boxes not on this canvas: {missing}")
            )
        for member in band.members:
            box = canvas.box(member)
            if box is None:
                continue
            if box.y < band.y or box.bottom > band.y + band.h:
                out.append(
                    _err(
                        "SVA-G-006",
                        band.label,
                        f"spans y {band.y}..{band.y + band.h} but member {member!r} "
                        f"spans {box.y}..{box.bottom}",
                    )
                )
    return out


def _check_regions(canvas: Canvas) -> list[Diagnostic]:
    """A boundary is a membership claim; every member must be inside it, and
    the rectangle must be on the canvas."""
    out: list[Diagnostic] = []
    ids = {b.id for b in canvas.boxes}
    for region in canvas.regions:
        if (
            region.x < 0
            or region.y < 0
            or region.right > canvas.width
            or region.bottom > canvas.height
        ):
            out.append(_err("SVA-G-012", region.label, "boundary falls outside the canvas"))
        missing = sorted(m for m in region.members if m not in ids)
        if missing:
            out.append(
                _err(
                    "SVA-G-012",
                    region.label,
                    f"boundary claims boxes not on this canvas: {missing}",
                )
            )
        for member in region.members:
            box = canvas.box(member)
            if box is None:
                continue
            if (
                box.x < region.x
                or box.right > region.right
                or box.y < region.y
                or box.bottom > region.bottom
            ):
                out.append(
                    _err(
                        "SVA-G-012",
                        region.label,
                        f"boundary does not contain its member {member!r}",
                    )
                )
    return out


def _check_labels(canvas: Canvas, style: Style) -> list[Diagnostic]:
    """A route label sits on a mask; the mask must not cover a box or another
    label, or the reader gets `7122.26.62.5nimports` again.

    The mask is Archify's label rule: the measured text width plus padding,
    a fixed height, and a clear gap around it.
    """
    out: list[Diagnostic] = []
    height = style.label_font_size + 6
    masks: list[tuple[str, int, int, int, int]] = []
    for r in canvas.routes:
        if r.label_at is None or not r.label:
            continue
        cx, cy = r.label_at
        masks.append(
            (f"{r.src} -> {r.dst}", cx - r.label_w // 2, cy - height // 2, r.label_w, height)
        )
    for who, x, y, w, h in masks:
        for b in canvas.boxes:
            if b.id in canvas.waypoints:
                continue
            if x < b.right and x + w > b.x and y < b.bottom and y + h > b.y:
                out.append(_err("SVA-G-013", who, f"route label overlaps box {b.id!r}"))
                break
    for i, (who, x, y, w, h) in enumerate(masks):
        for who2, x2, y2, w2, h2 in masks[i + 1 :]:
            gap = 8
            if (
                x < x2 + w2 + gap
                and x + w + gap > x2
                and y < y2 + h2 + gap
                and y + h + gap > y2
            ):
                out.append(
                    _err("SVA-G-013", who, f"route label collides with the label of {who2}")
                )
    return out
