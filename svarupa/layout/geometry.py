"""Stage 6a: the positioned form of a diagram.

`derive` says what the boxes are; this says where they are. The split matters
because layout is the last stage that can still be checked without a browser:
once coordinates are numbers in a JSON file, "do these boxes overlap" is
arithmetic rather than a screenshot.

**Every coordinate is an integer.** Not cosmetic. A float coordinate is
computed differently on different platforms in the last bits, and the lockfile
promise is byte-identity across Linux and macOS. Rounding once, here, is what
keeps that promise cheap to hold.

**The layout owns the drawn text.** `Box.label` is what appears on screen,
already sanitized and already truncated to fit, and `Box.full_label` keeps the
original for the tooltip. Truncating in the browser instead would move the
fits-in-the-box question somewhere no test can reach.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from svarupa.derive.base import DiagramKind
from svarupa.diagnostics import Diagnostic
from svarupa.model import Evidence, Resolution

__all__ = [
    "Band",
    "Box",
    "Canvas",
    "RegionBox",
    "Route",
    "Style",
]


@dataclass(frozen=True, slots=True)
class Style:
    """Sizes the engines and the viewer must agree on.

    One frozen object rather than module constants, so a test can lay a diagram
    out at an absurd size and check the engine actually reads the value instead
    of having a number baked into it.
    """

    font_size: int = 13
    label_font_size: int = 11
    sublabel_font_size: int = 9
    box_height: int = 44
    # A box with a sublabel is two lines tall (Archify's default node is
    # 120x60); a one-line box stays compact.
    box_height_tall: int = 60
    # Boundary padding: Archify's 30px on three sides plus 20px extra at the
    # bottom, so the region label has room above the top row of members.
    region_pad: int = 30
    region_extra_bottom: int = 20
    region_label_height: int = 16
    # Route label masks: height is the font plus this padding, and two masks
    # (or a mask and a box) must be this far apart. One definition, read by
    # the engine that places labels, the validator that checks them and the
    # renderer that draws them: three copies of "6" and "8" disagreed only
    # by luck, and a mutation of any one of them survived.
    label_pad: int = 6
    label_gap: int = 8
    box_min_width: int = 96
    box_max_width: int = 260
    box_pad_x: int = 12
    gap_x: int = 28
    gap_y: int = 56
    margin: int = 32
    # A vertical corridor to the right of every box, so a route spanning more
    # than one row has somewhere legal to run. Reserved on every canvas, since
    # a lane that appears only when a long edge exists would make canvas width
    # depend on edge topology.
    lane_gutter: int = 48
    band_pad: int = 16
    band_label_height: int = 22

    def __post_init__(self) -> None:
        """Reject anything that would make the validator agree with a lie.

        The engine sizes a box with these numbers and the validator checks it
        with the same numbers, so a corrupt value makes producer and checker
        agree on a falsehood that neither can catch. A negative `box_pad_x`
        was demonstrated to produce a 202px box holding a 242px label, blessed
        by the geometry check, because `budget = w - 2 * pad` grew instead of
        shrinking. Past construction no check here is independent, so the
        validation has to happen at construction.
        """
        if self.box_min_width > self.box_max_width:
            raise ValueError(
                f"box_min_width {self.box_min_width} exceeds box_max_width {self.box_max_width}"
            )
        if min(self.font_size, self.box_height, self.box_min_width) <= 0:
            raise ValueError("font size, box height and min width must be positive")
        negative = {
            name: value
            for name, value in (
                ("box_pad_x", self.box_pad_x),
                ("gap_x", self.gap_x),
                ("gap_y", self.gap_y),
                ("margin", self.margin),
                ("lane_gutter", self.lane_gutter),
                ("band_pad", self.band_pad),
            )
            if value < 0
        }
        if negative:
            raise ValueError(f"spacing values cannot be negative: {negative}")
        if self.text_budget <= 0:
            raise ValueError(
                f"box_max_width {self.box_max_width} leaves no room for text after "
                f"{self.box_pad_x}px of padding on each side"
            )

    @property
    def text_budget(self) -> int:
        """Pixels a label may occupy inside a box."""
        return self.box_max_width - 2 * self.box_pad_x


@dataclass(frozen=True, slots=True)
class Box:
    id: str
    label: str
    full_label: str
    kind: str
    x: int
    y: int
    w: int
    h: int
    evidence: tuple[Evidence, ...]
    child_spec: str | None = None
    attrs: tuple[tuple[str, str], ...] = ()
    sublabel: str = ""

    @property
    def right(self) -> int:
        return self.x + self.w

    @property
    def bottom(self) -> int:
        return self.y + self.h

    @property
    def center(self) -> tuple[int, int]:
        return (self.x + self.w // 2, self.y + self.h // 2)

    @property
    def is_drillable(self) -> bool:
        """Mirrors `DiagramNode.is_drillable`, on the positioned form.

        Tested against `None` rather than truthiness: a spec id is never empty
        today, but box *ids* legitimately are (the repository root), and having
        one of the two read as falsy while the other does not is the kind of
        near-miss that produces a bug nobody can see.
        """
        return self.child_spec is not None

    def overlaps(self, other: Box, gap: int = 0) -> bool:
        """Strict: touching edges do not overlap, `gap` demands clearance."""
        return not (
            self.right + gap <= other.x
            or other.right + gap <= self.x
            or self.bottom + gap <= other.y
            or other.bottom + gap <= self.y
        )


@dataclass(frozen=True, slots=True)
class Route:
    """An edge with its polyline already computed."""

    src: str
    dst: str
    label: str
    points: tuple[tuple[int, int], ...]
    evidence: tuple[Evidence, ...]
    resolution: Resolution = Resolution.RESOLVED
    weight: int = 1
    variant: str = "default"
    note: str = ""
    # Where the label's mask sits (centre) and how wide it is, computed at
    # layout time so the validator can check it against boxes and other
    # labels. None means the route draws no text.
    label_at: tuple[int, int] | None = None
    label_w: int = 0


@dataclass(frozen=True, slots=True)
class Band:
    """A labelled horizontal region, drawn behind the boxes it contains.

    Bands carry meaning, not decoration: on module deps a band is a dependency
    layer, so its label is a claim ("layer 3") that the boxes inside it must
    actually satisfy. Validation checks that containment.
    """

    label: str
    y: int
    h: int
    members: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RegionBox:
    """A boundary rectangle drawn behind its member boxes.

    Only drawn when it can be honest: a rectangle that would also enclose a
    non-member says something false about membership, so the engine skips it
    and reports why rather than drawing it.
    """

    id: str
    label: str
    kind: str
    x: int
    y: int
    w: int
    h: int
    members: tuple[str, ...]
    evidence: tuple[Evidence, ...]

    @property
    def right(self) -> int:
        return self.x + self.w

    @property
    def bottom(self) -> int:
        return self.y + self.h


def band_label_rect(band: Band, style: Style) -> tuple[int, int, int, int]:
    """Where the renderer draws a band's label: (x, y, w, h).

    One definition read by the engine that keeps route labels off it, the
    validator that checks them and the renderer that draws it. The label is a
    10px uppercase run above the band's top edge (see `emit.svg._band`); six
    band-label collisions with route verbs survived every gate because no
    gate knew where the band label was (review #20 S5).
    """
    from svarupa.layout.text import advance

    h = 10
    y = band.y - style.band_pad - 6 - h // 2 - 1
    w = advance(band.label.upper(), h) + len(band.label) + 2
    return style.margin // 2, y, w, h + 2


def region_label_rect(region: RegionBox, style: Style) -> tuple[int, int, int, int]:
    """Where the renderer draws a region's label (see `emit.svg._region`)."""
    from svarupa.layout.text import advance

    h = style.label_font_size
    cy = region.y + 6 + style.region_label_height // 2 + 1
    return region.x + 13, cy - h // 2 - 1, advance(region.label, h) + 2, h + 2


@dataclass(frozen=True, slots=True)
class Canvas:
    """One positioned diagram, ready to draw."""

    spec_id: str
    kind: DiagramKind
    engine: str
    title: str
    subtitle: str
    width: int
    height: int
    boxes: tuple[Box, ...]
    routes: tuple[Route, ...]
    bands: tuple[Band, ...] = ()
    regions: tuple[RegionBox, ...] = ()
    parent: str | None = None
    diagnostics: tuple[Diagnostic, ...] = field(default=())
    # Boxes that are bends in a line rather than claims about the codebase.
    # A long edge owns a slot in every row it crosses so it has somewhere of
    # its own to run; those slots are not drawn, carry no evidence, and are
    # exempt from the checks that demand a citation and a readable label.
    waypoints: frozenset[str] = frozenset()

    def box(self, box_id: str) -> Box | None:
        return next((b for b in self.boxes if b.id == box_id), None)
