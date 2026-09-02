"""Text metrics, so "the label fits" is a checkable claim.

Python cannot measure a font. A validator that asserts labels fit without a
width model is the shape a promoted decision already names: a feature whose
data structure cannot express it is dead code. So the width model is made
explicit and its assumption is stated rather than hidden.

**The assumption:** the viewer pins a monospace font stack, and monospace means
every advance is the same fraction of the font size. Measured ratios for the
fonts in that stack: Consolas 0.550, SF Mono 0.600, Liberation Mono 0.600,
Courier New 0.600, Menlo 0.6021, DejaVu Sans Mono 0.6023. `ADVANCE_RATIO` is
0.62, above all of them, so the model over-estimates and a box that passes
validation has slack rather than being exactly full.

If the stack ever fell back to a proportional font the model would be wrong,
which is why `FONT_STACK` lives here next to the ratio it justifies and ends in
the generic `monospace`.
"""

from __future__ import annotations

import math
import unicodedata

__all__ = [
    "ADVANCE_RATIO",
    "FONT_STACK",
    "REPLACEMENT",
    "advance",
    "cells",
    "sanitize",
    "truncate",
]

FONT_STACK = (
    "ui-monospace, SFMono-Regular, 'SF Mono', Menlo, Consolas, 'DejaVu Sans Mono', monospace"
)
ADVANCE_RATIO = 0.62
REPLACEMENT = "�"

_ELLIPSIS = "…"

# Format characters that reorder or hide text. Deny-listed individually rather
# than by category: `Cf` also contains U+200C ZWNJ and U+200D ZWJ, which are
# required for correct rendering of Indic scripts and emoji sequences, so
# banning the whole category would mangle legitimate module names.
_UNSAFE_FORMAT = frozenset(
    "\u00ad"  # soft hyphen, invisible
    "\u061c"  # arabic letter mark
    "\u200e\u200f"  # LRM, RLM
    "\u202a\u202b\u202c\u202d\u202e"  # embedding and override
    "\u2066\u2067\u2068\u2069"  # isolates
    "\ufeff"  # zero width no-break space, BOM
)
# Line and paragraph separators. Legal in a POSIX filename, and each one ends
# the SVG text run it is drawn into.
_BREAKS = frozenset("\u2028\u2029")

# Written as escapes on purpose. These characters are invisible or reorder
# their neighbours, so a literal here would be unreviewable in a diff, which is
# exactly the property that makes them worth denying.


def sanitize(text: str) -> str:
    """Make a string safe to draw, before anything measures it.

    Labels come from file paths, and POSIX permits almost every byte in a
    filename, including newlines, tabs, and C0 controls. Left alone, a path
    containing a newline breaks the SVG text element it is drawn into and makes
    the measured width a lie, since the model counts one line of cells.

    Bidirectional overrides matter more than that. `U+202E` in a filename
    renders the label reversed, so a reader sees a different module name from
    the one the box points at. That is a wrong claim about the codebase, which
    is the failure this whole product exists to prevent, and the same trick is
    a known attack on human source review.

    Replaced rather than dropped. Dropping would silently produce a label that
    reads as a plausible different name; U+FFFD shows that something was there.

    **This is not HTML or SVG escaping and must not be mistaken for it.** It
    leaves `<`, `&`, `"` and `'` exactly as they are, because they are
    legitimate characters in a filename and a reader needs to see them. The
    emitter that puts a label into a document owns escaping it; a directory
    named `</script>` is scannable on POSIX, so that escaping is not optional.
    """
    out: list[str] = []
    for ch in text:
        unsafe = (
            ch in _UNSAFE_FORMAT
            or ch in _BREAKS
            or unicodedata.category(ch) in {"Cc", "Cs", "Co", "Cn"}
        )
        out.append(REPLACEMENT if unsafe else ch)
    return "".join(out)


def cells(text: str) -> int:
    """Width in monospace cells.

    Wide and fullwidth characters occupy two cells; combining marks occupy
    none, since they stack onto the preceding character rather than advancing.
    Without both, a CJK module name measures at half its drawn width and
    overflows a box validation called fine.
    """
    total = 0
    for ch in text:
        if unicodedata.combining(ch) or unicodedata.category(ch) in {"Mn", "Me"}:
            continue
        total += 2 if unicodedata.east_asian_width(ch) in {"W", "F"} else 1
    return total


def advance(text: str, font_size: int) -> int:
    """Drawn width in pixels, rounded **up**.

    Up, never to nearest: the result feeds a fits-in-the-box check, so rounding
    down would let a label that overflows by a fraction of a pixel pass.
    """
    return math.ceil(cells(text) * font_size * ADVANCE_RATIO)


def truncate(text: str, font_size: int, max_px: int) -> str:
    """Shorten `text` until it fits `max_px`, marking the cut with an ellipsis.

    Truncation happens at layout time, not in the browser, for the same reason
    coordinates do: the artifact has to be checkable without running it.
    `Box.label` is what gets drawn and what validation measures, and the full
    text is kept beside it for the tooltip, so nothing is lost.

    Cuts from the **left**, keeping the tail. These labels are paths, and what
    distinguishes `src/services/billing/invoice` is its end.
    """
    if advance(text, font_size) <= max_px:
        return text
    budget = max_px - advance(_ELLIPSIS, font_size)
    if budget <= 0:
        return ""
    kept: list[str] = []
    width = 0
    for ch in reversed(text):
        w = cells(ch) * font_size * ADVANCE_RATIO
        if width + w > budget:
            break
        kept.append(ch)
        width += w
    # A cut can land between a base character and its combining marks, leaving
    # the marks to stack onto the ellipsis: truncating a run of "é" produced
    # "…\u0301ééé", an accent floating on the ellipsis. Drop the orphans.
    while kept and (
        unicodedata.combining(kept[-1]) or unicodedata.category(kept[-1]) in {"Mn", "Me"}
    ):
        kept.pop()
    if not kept:
        return ""
    return _ELLIPSIS + "".join(reversed(kept))
