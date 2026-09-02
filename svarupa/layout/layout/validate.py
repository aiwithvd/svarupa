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
    """Every geometric claim the canvas makes. Empty result means drawable."""
    out: list[Diagnostic] = []
    out.extend(_check_ids(canvas))
    out.extend(_check_boxes(canvas, style))
    out.extend(_check_overlap(canvas))
    out.extend(_check_routes(canvas))
    out.extend(_check_bands(canvas))
    return tuple(out)


def _check_ids(canvas: Canvas) -> list[Diagnostic]:
    """Duplicate ids are checked first, and separately.

    The viewer keys boxes by id, so a duplicate silently loses one box. It also
    makes every later message ambiguous, which is why it is its own code rather
    than being folded into the overlap check that would also fire.
    """
    seen: dict[str, int] = {}
    for b in canvas.boxes:
        seen[b.id] = seen.get(b.id, 0) + 1
    return [
        _err("SVA-G-007", bid, f"appears {n} times on one canvas")
        for bid, n in sorted(seen.items())
        if n > 1
    ]


def _check_boxes(canvas: Canvas, style: Style) -> list[Diagnostic]:
    out: list[Diagnostic] = []
    for b in canvas.boxes:
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
    """Membership is tested against a set, never against truthiness.

    An id may legitimately be the empty string: the repository root is a real
    module and its id is `""`. A check written as `if not r.src` would report
    every arrow touching the root box as having a missing endpoint, so the
    empty string is a value here and never a stand-in for absence.
    """
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
