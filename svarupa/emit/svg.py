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
from svarupa.layout.geometry import Box, Canvas, RegionBox, Route, Style
from svarupa.layout.text import advance
from svarupa.model import Evidence, Resolution, evidence_order

__all__ = ["EVIDENCE_ATTR", "canvas_body", "canvas_svg", "evidence_ref", "svg_document"]

# The viewer resolves this against a base the reader supplies. It is stored as
# a repository-relative path plus a line, never as an absolute path: an
# absolute path would leak the author's home directory into a shared artifact
# and would break the byte-identity the whole pipeline is built around.
EVIDENCE_ATTR = "data-evidence"


def evidence_ref(evidence: Iterable[Evidence]) -> str:
    """Citations as `path:line` pairs, newline-separated, in canonical order.
    An empty file is cited as its path alone: it has no line to point at."""
    # Real lines first, then empty files: the first citation is the one a
    # reader clicks.
    return "\n".join(
        f"{e.file}:{e.start_line}" if e.start_line else e.file
        for e in sorted(evidence, key=evidence_order)
    )


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
                # Above the band's top edge. Inside it, the label sat behind
                # whichever box happened to be leftmost and was unreadable.
                y=y - style.band_pad - 6,
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
    # Two text lines when there is a sublabel: label above centre, sublabel
    # 14px below it (Archify's spacing); one centred line otherwise.
    # In a one-line box the label sits 2px below centre, clear of the SRC
    # capsule at the top right (1.6px of overlap on 48 boxes, review #20 C5).
    label_y = box.y + box.h // 2 - (7 if box.sublabel else -2)
    body = join(
        (
            tag(
                "title",
                esc(f"{box.full_label}\n{evidence_ref(box.evidence)}"),
            ),
            # An opaque mask under the translucent fill, so a route passing
            # behind a box never shows through it (Archify's `.c-mask`).
            tag(
                "rect",
                EMPTY,
                x=box.x,
                y=box.y,
                width=box.w,
                height=box.h,
                rx=6,
                class_="sv-mask",
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
                y=label_y,
                class_="sv-box-label",
                font_size=style.font_size,
                # A rendering backstop for the width model. `textLength` makes
                # the browser fit the text to the measured width whatever font
                # it actually resolved, so a script the stack lacks produces a
                # squeezed label rather than one that overflows its box. The
                # model is an estimate; this makes its failure mode safe.
                textLength=max(1, advance(box.label, style.font_size)),
                lengthAdjust="spacingAndGlyphs",
            ),
            (
                tag(
                    "text",
                    esc(box.sublabel),
                    x=box.x + box.w // 2,
                    y=label_y + 15,
                    class_="sv-box-sublabel",
                    font_size=style.sublabel_font_size,
                    textLength=max(1, advance(box.sublabel, style.sublabel_font_size)),
                    lengthAdjust="spacingAndGlyphs",
                )
                if box.sublabel
                else raw("")
            ),
            # The kind sigil, top-left: a small glyph per semantic type, so a
            # colour-blind reader and a low-zoom screenshot both keep the
            # type. Drawn as paths, not a font glyph the stack might lack.
            _sigil(box),
            _src_capsule(box),
            _drill_marker(box, style),
        )
    )
    roles = next((v for k, v in box.attrs if k == "roles"), None)
    members = next((v for k, v in box.attrs if k == "members"), None)
    return tag(
        "g",
        body,
        # The kind class sits on the group as well as the rect: the sigil, the
        # SRC capsule and the passport all read `--k` / `sv-kind-*` from the
        # group, and with it only on the rect every sigil rendered grey.
        class_="sv-node"
        + f" sv-kind-{_slug(box.kind)}"
        + (" sv-drillable" if box.is_drillable else ""),
        data_id=box.id,
        data_child=box.child_spec,
        # The passport reads these back; both are repository-derived text
        # and go through the same escaping as every other attribute.
        data_sublabel=box.sublabel or None,
        data_roles=roles,
        data_members=members,
        # Reachable from the keyboard: Enter opens the passport, as a click
        # does (review #20 S9 found no focusable box and no Escape).
        tabindex="0",
        **{EVIDENCE_ATTR: evidence_ref(box.evidence)},
    )


# 10x10 sigils per semantic kind, in path form so no font is involved. Each
# is drawn at the box's top-left corner in the kind colour.
_SIGILS: dict[str, str] = {
    # angle brackets: code that serves
    "backend": "M3 1 L0 5 L3 9 M7 1 L10 5 L7 9",
    "module": "M1 2 H9 V9 H1 Z M1 4 H9",
    "group": "M1 1 H9 V9 H1 Z M1 4 H9 M4 4 V9",
    "service": "M1 1 H9 V9 H1 Z M1 4 H9 M4 4 V9",
    # a window: a top bar over a pane
    "frontend": "M1 1 H9 V9 H1 Z M1 3.5 H9",
    # a cylinder
    "database": "M1 2.5 A4 1.5 0 0 0 9 2.5 A4 1.5 0 0 0 1 2.5 V7.5 A4 1.5 0 0 0 9 7.5 V2.5",
    "datastore": "M1 2.5 A4 1.5 0 0 0 9 2.5 A4 1.5 0 0 0 1 2.5 V7.5 A4 1.5 0 0 0 9 7.5 V2.5",
    "table": "M1 1 H9 V9 H1 Z M1 4 H9 M5 4 V9",
    # two arrows: a bus
    "messagebus": "M1 3 H8 M6 1 L8 3 L6 5 M9 7 H2 M4 5 L2 7 L4 9",
    "queue": "M1 3 H8 M6 1 L8 3 L6 5 M9 7 H2 M4 5 L2 7 L4 9",
    # a cloud
    "cloud": "M3 8 H8 A2 2 0 0 0 8 4 A3 3 0 0 0 2.5 4.5 A1.8 1.8 0 0 0 3 8 Z",
    # a shield
    "security": "M5 1 L9 2.5 V5 C9 7.5 7 9 5 9.5 C3 9 1 7.5 1 5 V2.5 Z",
    "endpoint": "M1 5 H7 M5 3 L7 5 L5 7",
}


def _sigil(box: Box) -> Markup:
    d = _SIGILS.get(box.kind)
    if d is None:
        return tag("circle", EMPTY, cx=box.x + 11, cy=box.y + 12, r=3, class_="sv-dot")
    return tag(
        "path",
        EMPTY,
        d=d,
        transform=f"translate({box.x + 7} {box.y + 7})",
        class_="sv-sigil",
    )


def _src_capsule(box: Box) -> Markup:
    """Archify's "Verified Source Beacon", made unconditional: every box of
    ours has sources, so every box says how many. Sits left of the drill
    chevron when there is one."""
    n = len(box.evidence)
    text = f"SRC {n}"
    width = advance(text, 8) + 8
    right = box.right - (22 if box.is_drillable else 8)
    return tag(
        "g",
        join(
            (
                tag(
                    "rect",
                    EMPTY,
                    x=right - width,
                    y=box.y + 5,
                    width=width,
                    height=12,
                    rx=6,
                    class_="sv-src",
                ),
                tag(
                    "text",
                    esc(text),
                    x=right - width // 2,
                    y=box.y + 11,
                    class_="sv-src-text",
                    font_size=8,
                ),
            )
        ),
        class_="sv-beacon",
    )


def _region(region: RegionBox, style: Style) -> Markup:
    """A boundary: dashed rectangle in its kind colour, label on a mask at
    the top-left inside, drawn behind everything it wraps."""
    label_w = advance(region.label, style.label_font_size) + 10
    return tag(
        "g",
        join(
            (
                tag("title", esc(f"{region.label}\n{evidence_ref(region.evidence)}")),
                tag(
                    "rect",
                    EMPTY,
                    x=region.x,
                    y=region.y,
                    width=region.w,
                    height=region.h,
                    rx=10,
                    class_=f"sv-region sv-kind-{_slug(region.kind)}",
                ),
                tag(
                    "rect",
                    EMPTY,
                    x=region.x + 8,
                    y=region.y + 6,
                    width=label_w,
                    height=style.region_label_height,
                    rx=3,
                    class_="sv-mask",
                ),
                tag(
                    "text",
                    esc(region.label),
                    x=region.x + 13,
                    y=region.y + 6 + style.region_label_height // 2 + 1,
                    class_="sv-region-label",
                    font_size=style.label_font_size,
                ),
            )
        ),
        class_=f"sv-boundary sv-kind-{_slug(region.kind)}",
        data_id=region.id,
        data_kind=region.kind,
        data_label=region.label,
        data_members="\n".join(region.members),
        **{EVIDENCE_ATTR: evidence_ref(region.evidence)},
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
        x=box.right - 10,
        y=box.y + 16,
        class_="sv-drill",
        font_size=style.font_size,
    )


def _weight_class(weight: int) -> str:
    """Coarse buckets rather than a continuous width.

    Three widths a reader can tell apart beats forty they cannot, and bucketing
    keeps the stroke width out of the SVG attributes, so the same relationship
    renders identically whether it has eleven imports or twelve.
    """
    if weight >= 8:
        return "sv-w3"
    if weight >= 3:
        return "sv-w2"
    return "sv-w1"


def _route(route: Route, style: Style) -> Markup:
    """An edge as a rounded path, with its verb on a mask where the layout
    found room for one.

    Every edge used to stamp its count at its polyline midpoint. On a real
    diagram they all landed in the same band and collapsed into strings like
    `7122.26.62.5nimports`. Numbers never return; a short verb does, at a
    position the layout settled against every other label and box, and only
    on arrows whose verb says something (a store, a bus, an API). Structural
    import arrows are silent; the count is in the tooltip and the passport.

    Corners are rounded because a diagram of hard right angles reads as a
    circuit board. Weight is a stroke width, which shows the same information
    the labels were carrying, at a glance and without collision.
    """
    classes = " ".join(
        (
            "sv-edge",
            _weight_class(route.weight),
            f"sv-variant-{_slug(route.variant)}",
            *(("sv-edge-weak",) if route.resolution is not Resolution.RESOLVED else ()),
        )
    )
    label: Markup = raw("")
    if route.label_at is not None and route.label:
        # The verb on a mask at the midpoint of the longest segment, the
        # placement the validator checked against boxes and other labels.
        cx, cy = route.label_at
        h = style.label_font_size + style.label_pad
        label = join(
            (
                tag(
                    "rect",
                    EMPTY,
                    x=cx - route.label_w // 2,
                    y=cy - h // 2,
                    width=route.label_w,
                    height=h,
                    rx=3,
                    class_="sv-mask",
                ),
                tag(
                    "text",
                    esc(route.label),
                    x=cx,
                    y=cy + 1,
                    class_=f"sv-edge-label sv-variant-{_slug(route.variant)}",
                    font_size=style.label_font_size,
                ),
            )
        )
    return tag(
        "g",
        join(
            (
                tag(
                    "title",
                    esc(f"{route.note or route.label}\n{evidence_ref(route.evidence)}"),
                ),
                tag("path", EMPTY, d=_rounded(route.points), class_=classes),
                label,
            )
        ),
        class_="sv-route",
        data_src=route.src,
        data_dst=route.dst,
        data_label=route.label,
        data_note=route.note or None,
        **{EVIDENCE_ATTR: evidence_ref(route.evidence)},
    )


def _rounded(points: tuple[tuple[int, int], ...], radius: int = 8) -> str:
    """An orthogonal polyline with rounded corners, as an SVG path.

    The corner radius is clamped to half the shorter adjacent segment, so a
    short segment cannot make two corners overlap into a loop.
    """
    if len(points) < 3:
        return "M " + " L ".join(f"{x} {y}" for x, y in points)

    out = [f"M {points[0][0]} {points[0][1]}"]
    for i in range(1, len(points) - 1):
        (px, py), (cx, cy), (nx, ny) = points[i - 1], points[i], points[i + 1]
        before = max(abs(cx - px), abs(cy - py))
        after = max(abs(nx - cx), abs(ny - cy))
        r = max(0, min(radius, before // 2, after // 2))
        if r == 0:
            out.append(f"L {cx} {cy}")
            continue
        sx = cx + (r if px > cx else -r if px < cx else 0)
        sy = cy + (r if py > cy else -r if py < cy else 0)
        ex = cx + (r if nx > cx else -r if nx < cx else 0)
        ey = cy + (r if ny > cy else -r if ny < cy else 0)
        out.append(f"L {sx} {sy}")
        out.append(f"Q {cx} {cy} {ex} {ey}")
    out.append(f"L {points[-1][0]} {points[-1][1]}")
    return " ".join(out)


def canvas_body(canvas: Canvas, style: Style, skip: frozenset[str] = frozenset()) -> Markup:
    """The drawable content of a canvas, without the `<svg>` wrapper.

    Split from the wrapper so an expanded view can nest one canvas's body
    inside another under a single `<svg>` element. Nesting whole documents
    instead would duplicate the arrowhead `<defs>`, and duplicate SVG marker
    ids silently resolve to whichever came first.

    Bands are drawn first so they sit behind, then routes, then boxes. Boxes
    last means an arrow never covers the label it points at.
    """
    return join(
        (
            join(_region(r, style) for r in canvas.regions),
            join(_band(b.label, b.y, b.h, canvas.width, style) for b in canvas.bands),
            join(_route(r, style) for r in canvas.routes),
            join(
                _box(b, style)
                for b in canvas.boxes
                if b.id not in canvas.waypoints and b.id not in skip
            ),
        )
    )


def svg_document(body: Markup, width: int, height: int, label: str) -> Markup:
    return tag(
        "svg",
        join((_arrow_defs(), body)),
        viewBox=f"0 0 {width} {height}",
        width=width,
        height=height,
        class_="sv-canvas",
        role="img",
        aria_label=label,
        xmlns="http://www.w3.org/2000/svg",
    )


def canvas_svg(canvas: Canvas, style: Style) -> Markup:
    """One positioned diagram as a complete inline SVG element."""
    return svg_document(
        canvas_body(canvas, style),
        canvas.width,
        canvas.height,
        f"{canvas.kind.value}: {canvas.title}",
    )


def _slug(text: str) -> str:
    """A CSS-class-safe form of a node kind.

    Kinds come from derivers rather than from a repository today, but they are
    interpolated into a class attribute, so this keeps that true by
    construction rather than by assumption.
    """
    return "".join(c if c.isalnum() or c == "-" else "-" for c in text.lower()) or "none"


def expanded_svg(exp: object, style: Style) -> Markup:
    """A pre-rendered expansion: the parent view with one box opened in place.

    The host box is drawn as a dashed container with its label as a header,
    and the child's already-validated body is translated inside. One `<svg>`
    and one set of `<defs>`: nesting whole documents would duplicate marker
    ids, which silently resolve to whichever came first.
    """
    from svarupa.layout.compose import HEADER_H, Expanded

    assert isinstance(exp, Expanded)
    host = exp.canvas.box(exp.host)
    assert host is not None  # expand() returned this state, so the host exists

    container = tag(
        "g",
        join(
            (
                tag("title", esc(f"{host.full_label}\n{evidence_ref(host.evidence)}")),
                tag(
                    "rect",
                    EMPTY,
                    x=host.x,
                    y=host.y,
                    width=host.w,
                    height=host.h,
                    rx=10,
                    class_="sv-container",
                ),
                tag(
                    "text",
                    esc(host.full_label),
                    x=host.x + 14,
                    y=host.y + HEADER_H // 2 + 2,
                    class_="sv-container-label",
                    font_size=style.font_size,
                ),
                tag(
                    "text",
                    esc("\u00d7"),  # multiplication sign, the visual for close
                    x=host.right - 14,
                    y=host.y + HEADER_H // 2 + 2,
                    class_="sv-collapse",
                    font_size=style.font_size,
                ),
            )
        ),
        class_=f"sv-node sv-kind-{_slug(host.kind)} sv-expanded-host",
        data_id=host.id,
        **{EVIDENCE_ATTR: evidence_ref(host.evidence)},
    )
    embedded = tag(
        "g",
        canvas_body(exp.child, style),
        transform=f"translate({exp.offset[0]} {exp.offset[1]})",
    )
    return svg_document(
        join(
            (
                # Parent and child are two canvases in one document, and a
                # child box can share an id with a parent box (the module
                # `api` inside the group `api`). Each is scoped so the
                # passport and the hover read connections from their own
                # canvas, never from the other.
                tag(
                    "g",
                    join(
                        (canvas_body(exp.canvas, style, skip=frozenset({exp.host})), container)
                    ),
                    data_scope="parent",
                ),
                tag("g", embedded, data_scope="child"),
            )
        ),
        exp.canvas.width,
        exp.canvas.height,
        f"{exp.canvas.kind.value}: {host.full_label} expanded",
    )
