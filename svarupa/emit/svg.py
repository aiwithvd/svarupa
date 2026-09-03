"""Canvas to SVG, rendered in Python.

Layout already computed every coordinate, so there is nothing left for a
browser to calculate. Writing the SVG here rather than building it from JSON in
the browser buys three things:

* **The diagram renders with JavaScript disabled.** JS adds tab switching and
  drill-down; it is not needed to see a diagram or follow an evidence link.
* **No client-side templating of repository text.** The injection surface is
  one escaping function in one language, checked by the type system, instead of
  the same problem solved twice.
* **The artifact is checkable as bytes.** A determinism test can compare two
  runs directly, with no headless browser.

Every string that came from a repository goes through `esc`. Nothing here
builds markup by concatenating a bare `str`.
"""

from __future__ import annotations

from collections.abc import Iterable

from svarupa.emit.markup import EMPTY, Markup, esc, join, raw, tag
from svarupa.layout.geometry import Box, Canvas, Route, Style
from svarupa.model import Evidence, Resolution

__all__ = ["EVIDENCE_ATTR", "canvas_svg", "evidence_ref"]

# The viewer resolves this against a base the reader supplies. It is stored as
# a repository-relative path plus a line, never as an absolute path: an
# absolute path would leak the author's home directory into a shared artifact
# and would break the byte-identity the whole pipeline is built around.
EVIDENCE_ATTR = "data-evidence"


def evidence_ref(evidence: Iterable[Evidence]) -> str:
    """Citations as `path:line` pairs, newline-separated, in canonical order."""
    return "\n".join(f"{e.file}:{e.start_line}" for e in sorted(evidence))


def _arrow_defs() -> Markup:
    return raw(
        "<defs>"
        '<marker id="sv-arrow" viewBox="0 0 10 10" refX="9" refY="5" '
        'markerWidth="6" markerHeight="6" orient="auto-start-reverse">'
        '<path d="M 0 0 L 10 5 L 0 10 z" class="sv-arrowhead"/>'
        "</marker>"
        "</defs>"
    )


def _band(label: str, y: int, h: int, width: int, style: Style) -> Markup:
    return join(
        (
            tag(
                "rect",
                EMPTY,
                x=0,
                y=y - style.band_pad,
                width=width,
                height=h + 2 * style.band_pad,
                class_="sv-band",
            ),
            tag(
                "text",
                esc(label),
                x=style.margin // 2,
                y=y - style.band_pad + style.band_label_height,
                class_="sv-band-label",
            ),
        )
    )


def _box(box: Box, style: Style) -> Markup:
    """One box, as a group carrying everything the viewer needs.

    The full label is on the group as a `title`, so hovering a truncated label
    shows the whole thing even without JavaScript. Truncation happened at
    layout time and is what the geometry check measured, so what is drawn here
    is exactly what was validated.
    """
    body = join(
        (
            tag(
                "title",
                esc(f"{box.full_label}\n{evidence_ref(box.evidence)}"),
            ),
            tag(
                "rect",
                EMPTY,
                x=box.x,
                y=box.y,
                width=box.w,
                height=box.h,
                rx=6,
                class_=f"sv-box sv-kind-{_slug(box.kind)}",
            ),
            tag(
                "text",
                esc(box.label),
                x=box.x + box.w // 2,
                y=box.y + box.h // 2,
                class_="sv-box-label",
                font_size=style.font_size,
            ),
            _drill_marker(box, style),
        )
    )
    return tag(
        "g",
        body,
        class_="sv-node" + (" sv-drillable" if box.is_drillable else ""),
        data_id=box.id,
        data_child=box.child_spec,
        **{EVIDENCE_ATTR: evidence_ref(box.evidence)},
    )


def _drill_marker(box: Box, style: Style) -> Markup:
    """A visible mark on a box that leads somewhere.

    Without it a reader has no way to tell a group from a leaf, which is the
    whole navigation model. Drawn rather than left to a hover state, because a
    hover state does not exist on a touch screen or in a screenshot.
    """
    if not box.is_drillable:
        return raw("")
    return tag(
        "text",
        # U+203A single right-pointing angle quotation mark.
        esc("\u203a"),
        x=box.right - style.box_pad_x,
        y=box.y + box.h // 2,
        class_="sv-drill",
        font_size=style.font_size,
    )


def _route(route: Route, style: Style) -> Markup:
    points = " ".join(f"{x},{y}" for x, y in route.points)
    label_at = route.points[len(route.points) // 2]
    classes = "sv-edge" + (
        " sv-edge-weak" if route.resolution is not Resolution.RESOLVED else ""
    )
    return tag(
        "g",
        join(
            (
                tag("title", esc(f"{route.label}\n{evidence_ref(route.evidence)}")),
                tag("polyline", EMPTY, points=points, class_=classes),
                tag(
                    "text",
                    esc(route.label),
                    x=label_at[0],
                    y=label_at[1] - 4,
                    class_="sv-edge-label",
                    font_size=style.label_font_size,
                ),
            )
        ),
        class_="sv-route",
        data_src=route.src,
        data_dst=route.dst,
        **{EVIDENCE_ATTR: evidence_ref(route.evidence)},
    )


def canvas_svg(canvas: Canvas, style: Style) -> Markup:
    """One positioned diagram as an inline SVG element.

    Bands are drawn first so they sit behind, then routes, then boxes. Boxes
    last means an arrow never covers the label it points at, which matters
    because the label is what a reader is trying to read.
    """
    return tag(
        "svg",
        join(
            (
                _arrow_defs(),
                join(_band(b.label, b.y, b.h, canvas.width, style) for b in canvas.bands),
                join(_route(r, style) for r in canvas.routes),
                join(_box(b, style) for b in canvas.boxes),
            )
        ),
        viewBox=f"0 0 {canvas.width} {canvas.height}",
        width=canvas.width,
        height=canvas.height,
        class_="sv-canvas",
        role="img",
        aria_label=f"{canvas.kind.value}: {canvas.title}",
        xmlns="http://www.w3.org/2000/svg",
    )


def _slug(text: str) -> str:
    """A CSS-class-safe form of a node kind.

    Kinds come from derivers rather than from a repository today, but they are
    interpolated into a class attribute, so this keeps that true by
    construction rather than by assumption.
    """
    return "".join(c if c.isalnum() or c == "-" else "-" for c in text.lower()) or "none"
