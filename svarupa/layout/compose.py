"""Expansion: one box grown into a container holding its child diagram.

The interaction the user chose. Clicking a drillable box should not feel like
leaving the page; the box should open where it stands, with its siblings still
around it. Doing that live in the browser would mean client-side layout, which
the whole pipeline exists to avoid, so every expanded state is **pre-rendered**:
for each drillable box there is one extra canvas in which that box is sized to
hold its child's entire diagram, and the child is drawn inside it.

That is linear, not exponential. One expansion state per drillable box, each a
real laid-out, validated canvas: the parent layout re-runs with one size
override, so siblings genuinely move aside rather than being painted over, and
the geometry checks apply to the expanded state exactly as to any other.

Composition itself adds no geometry. The child canvas keeps its own validated
coordinates and is drawn translated into the container, so there is nothing new
for the validator to be wrong about: parent-with-big-box is checked as a
canvas, the child was already checked as a canvas, and the embedding is one
`translate`.
"""

from __future__ import annotations

from dataclasses import dataclass

from svarupa.derive.base import DiagramSpec
from svarupa.layout.engines import lay_out
from svarupa.layout.geometry import Canvas, Style
from svarupa.layout.validate import validate

__all__ = ["EMBED_MAX_H", "EMBED_MAX_W", "EXPANDED_SUFFIX", "Expanded", "expand"]

# Beyond this, an embedded child would make the parent canvas absurd; the
# viewer falls back to opening the child as its own view, which is stated in
# the interaction rather than silently different.
EMBED_MAX_W = 1500
EMBED_MAX_H = 1300

# Inside the container: room for its header label above the child, and padding
# around it.
HEADER_H = 34
PAD = 14

EXPANDED_SUFFIX = "//expanded"


@dataclass(frozen=True, slots=True)
class Expanded:
    """One pre-rendered expansion state.

    `canvas` is the parent laid out with `host` grown to container size;
    `child` keeps its own coordinates and is drawn at `offset` inside the
    host. The two stay separate precisely so each is validated as itself.
    """

    id: str
    canvas: Canvas
    host: str
    child: Canvas
    offset: tuple[int, int]


def expand(
    parent: DiagramSpec,
    host_id: str,
    child: Canvas,
    style: Style,
    engine: str,
) -> Expanded | None:
    """The parent view with `host_id` opened in place, or None when it cannot be.

    None is a decision, not a failure: an oversized child falls back to its own
    view, and a parent that no longer validates with the big box is dropped
    with the ordinary withholding path rather than shipped wrong.
    """
    if child.width > EMBED_MAX_W or child.height > EMBED_MAX_H:
        return None

    sizes = {host_id: (child.width + 2 * PAD, child.height + HEADER_H + 2 * PAD)}
    canvas = lay_out(parent, style, engine, sizes)
    if validate(canvas, style):
        # The enlarged host made the parent unlayoutable. Shipping it anyway
        # would put the one guaranteed-checked property at risk for a nicety.
        return None

    host = canvas.box(host_id)
    if host is None:
        return None
    return Expanded(
        id=f"{child.spec_id}{EXPANDED_SUFFIX}",
        canvas=canvas,
        host=host_id,
        child=child,
        offset=(host.x + PAD, host.y + HEADER_H + PAD),
    )
