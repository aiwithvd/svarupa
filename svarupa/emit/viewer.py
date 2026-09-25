"""`index.html`: one file, every diagram, every box clickable to a line.

Three properties, in the order they were designed for:

1. **It works with JavaScript disabled.** Every diagram is inline SVG written
   by Python, and tab switching runs on `:target`, which is CSS. JS adds
   drill-down history and the evidence panel; it is not required to see a
   diagram, read a label, or find a source location.
2. **No repository text is templated in the browser.** The whole injection
   surface is one escaping function in one language, checked by the type
   system.
3. **No absolute paths.** Evidence is stored repository-relative. The reader
   sets a base once in the header, and it is remembered in `localStorage`, so
   the artifact stays byte-identical between a laptop and CI and never carries
   a home directory into a shared file.
"""

from __future__ import annotations

from svarupa.build import Graph
from svarupa.derive.base import DiagramKind, DiagramSet
from svarupa.emit.cards import Card, cards_for, chapters_for, render_cards, render_guided
from svarupa.emit.markup import Markup, esc, join, raw, tag
from svarupa.emit.svg import canvas_svg, evidence_ref, expanded_svg
from svarupa.layout import LaidOutDiagram
from svarupa.layout.geometry import Style
from svarupa.layout.text import FONT_STACK

__all__ = ["render_viewer"]

# Above this many real boxes in a host view, in-place expansions are not
# pre-rendered: each expansion re-renders the whole host, so the bytes grow
# with host size times drillable count, and the context an expansion
# preserves is not readable at that size anyway. The drill then opens the
# child as its own view — the same fallback an oversized child already took.
MAX_EXPANSION_HOST_BOXES = 24


def _css() -> Markup:
    """The visual system, studied from Archify's output rather than invented.

    What Archify gets right, adopted: a paper-white canvas with a dotted grid,
    boxes coloured by kind (pastel fill, saturated border), a bold title with a
    quiet caption under it, dashed containers for things that hold other
    things, a legend, and chrome that reads as a product. What stays ours,
    because it is the product: every box cites a source line, no box is
    invented, and two runs produce identical bytes.

    One custom property, `--k`, carries a box's kind colour; fills are derived
    from it with `color-mix` against the surface, so the dark theme recolours
    every kind by changing one variable instead of restating the palette.
    Dark is the default, Archify's midnight console; light is a toggle,
    remembered per reader, never baked into the file.
    """
    return raw(
        """
:root {
  --header-h: 58px; /* measured by the script; the one-row height */
  --bg: #020617; --surface: #0f172a; --raised: #131c33; --line: #1e293b;
  --ink: #f8fafc; --dim: #94a3b8; --faint: #475569;
  --accent: #22d3ee; --group: #a78bfa; --warn: #fbbf24;
  --edge: #64748b; --grid: #1e293b; --canvas: #020617; --mask: #0f172a;
  --ui: -apple-system, BlinkMacSystemFont, 'Segoe UI', Inter, Roboto, sans-serif;
  --mono: MONO_PLACEHOLDER;
  /* Semantic fills: translucent fill + saturated stroke per kind, Archify's
     exact dark values. Colour identifies meaning, never decoration. */
  --frontend-fill: rgba(8,51,68,.4); --frontend-stroke: #22d3ee;
  --backend-fill: rgba(6,78,59,.4); --backend-stroke: #34d399;
  --database-fill: rgba(76,29,149,.4); --database-stroke: #a78bfa;
  --cloud-fill: rgba(120,53,15,.3); --cloud-stroke: #fbbf24;
  --security-fill: rgba(136,19,55,.4); --security-stroke: #fb7185;
  --messagebus-fill: rgba(251,146,60,.3); --messagebus-stroke: #fb923c;
  --external-fill: rgba(30,41,59,.5); --external-stroke: #94a3b8;
  --module-fill: rgba(30,58,138,.35); --module-stroke: #60a5fa;
  --group-fill: rgba(76,29,149,.18); --group-stroke: #a78bfa;
}
[data-theme="light"] {
  --bg: #f8fafc; --surface: #ffffff; --raised: #f1f5f9; --line: #e2e8f0;
  --ink: #0f172a; --dim: #64748b; --faint: #94a3b8;
  --accent: #0891b2; --group: #7c3aed; --warn: #b45309;
  --edge: #94a3b8; --grid: #e2e8f0; --canvas: #f8fafc; --mask: #ffffff;
  --frontend-fill: rgba(34,211,238,.15); --frontend-stroke: #0891b2;
  --backend-fill: rgba(52,211,153,.15); --backend-stroke: #059669;
  --database-fill: rgba(167,139,250,.15); --database-stroke: #7c3aed;
  --cloud-fill: rgba(251,191,36,.18); --cloud-stroke: #b45309;
  --security-fill: rgba(251,113,133,.15); --security-stroke: #be123c;
  --messagebus-fill: rgba(251,146,60,.18); --messagebus-stroke: #c2410c;
  --external-fill: rgba(148,163,184,.15); --external-stroke: #64748b;
  --module-fill: rgba(96,165,250,.15); --module-stroke: #2563eb;
  --group-fill: rgba(124,58,237,.08); --group-stroke: #7c3aed;
}
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--bg); color: var(--ink);
  font-family: var(--mono); font-size: 13px; line-height: 1.55;
  -webkit-font-smoothing: antialiased;
}
code, .mono { font-family: var(--mono); }

/* One row at every width from 1024 up: the header wrapped to 108px at 1280
   and took 15 percent of a 720px viewport (review 20, S11). The nav scrolls
   sideways before anything wraps; the source-base input gives way first. */
header {
  position: sticky; top: 0; z-index: 5;
  background: color-mix(in srgb, var(--surface) 94%, transparent);
  backdrop-filter: blur(8px);
  border-bottom: 1px solid var(--line); padding: 12px 20px;
  display: flex; gap: 16px; align-items: center; flex-wrap: wrap; min-height: 57px; box-sizing: border-box;
}
/* Review 21, N4: a scrolling nav with no scrollbar hid whole tabs. Every tab
   is always visible: the source-base label goes first, then the input
   narrows, and only then does the header take a second row. */
@media (max-width: 1440px) { .controls label { display: none; } .controls input { width: 12em; } }
h1 { font-size: 15px; margin: 0; font-weight: 700; letter-spacing: -0.01em; white-space: nowrap; }
/* The repository the artifact describes, said as such: a bare word after the
   tool name read as part of the name. */
.repo {
  color: var(--dim); font-family: var(--mono); font-size: 12px; white-space: nowrap;
  border: 1px solid var(--line); border-radius: 7px; padding: 3px 9px;
}
.repo small { color: var(--faint); font-size: 9px; letter-spacing: .12em; text-transform: uppercase; margin-right: 6px; }
nav { display: flex; gap: 6px; flex-wrap: wrap; min-width: 0; }
nav a {
  color: var(--dim); text-decoration: none; padding: 5px 12px; white-space: nowrap;
  border-radius: 7px; font-size: 13px; transition: background .12s, color .12s;
}
nav a:hover { color: var(--ink); background: var(--raised); }
/* "not drawn" is a list of absences, not a diagram: set apart from the tabs. */
nav a.muted { color: var(--faint); border-left: 1px solid var(--line); border-radius: 0 7px 7px 0; margin-left: 4px; font-size: 12px; }
.controls { margin-left: auto; display: flex; gap: 8px; align-items: center; flex: 0 1 auto; min-width: 0; }
.controls label { color: var(--dim); font-size: 12px; white-space: nowrap; }
.controls input {
  background: var(--bg); color: var(--ink); border: 1px solid var(--line);
  border-radius: 7px; padding: 6px 10px; font-size: 12px;
  font-family: var(--mono); width: 22em; max-width: 30vw; min-width: 6em;
}
.controls input:focus { outline: none; border-color: var(--accent); }
#theme {
  background: var(--surface); color: var(--dim); border: 1px solid var(--line);
  border-radius: 7px; padding: 6px 12px; font: inherit; font-size: 12px;
  cursor: pointer; white-space: nowrap;
}
#theme:hover { color: var(--ink); border-color: var(--accent); }

.tab { display: none; padding: 24px 20px 64px; }
.tab:first-of-type { display: block; }
body:has(.tab:target) .tab:first-of-type { display: none; }
body:has(.tab:target) .tab:target { display: block; }

h2 { font-size: 20px; margin: 0 0 4px; font-weight: 700; letter-spacing: -0.02em; }
.meta { color: var(--dim); margin: 0 0 16px; font-size: 13px; }
.crumbs { margin: 0 0 12px; font-size: 13px; }
.crumbs a { color: var(--accent); text-decoration: none; }
.crumbs a:hover { text-decoration: underline; }
/* A drill lands below the header whatever height it has: the script writes
   the measured header height into --header-h (review 21, N3). */
.view { display: none; scroll-margin-top: calc(var(--header-h, 58px) + 24px); }
.view.is-open { display: block; animation: sv-enter .18s ease; }
/* The passport is a side panel, not an overlay: while it is open the tab
   makes room for it, so no box, chapter or title sits under the card
   (review 20, S3 measured two boxes and the whole guided strip covered). */
body:has(aside.is-open) .tab { padding-left: 404px; }

/* Keyboard: a focused box shows the same ring as a hovered one. */
.sv-node:focus { outline: none; }
.sv-node:focus-visible .sv-box { stroke: var(--accent); stroke-width: 2.5; }
@keyframes sv-enter {
  from { opacity: 0; transform: translateY(6px); }
  to { opacity: 1; transform: none; }
}
@media (prefers-reduced-motion: reduce) {
  .view.is-open { animation: none; }
  aside { transition: none; }
}

/* The diagram sits on grid paper inside a card, which is most of what makes
   it read as a drawing rather than markup. Wide diagrams scroll at natural
   size; scaling a 2700px canvas into a 1200px column defeats drawing it. */
.scroller {
  overflow-x: auto; overflow-y: hidden;
  border: 1px solid var(--line); border-radius: 12px;
  background: var(--canvas)
    radial-gradient(circle, var(--grid) 1px, transparent 1px);
  background-size: 22px 22px;
  scrollbar-color: var(--line) transparent;
}
.sv-canvas { display: block; margin: 0 auto; }

/* Kind colours: one fill/stroke pair per semantic kind (Archify's vocabulary),
   with `--k` kept as the stroke for anything that derives from the kind. */
.sv-node { --k: var(--external-stroke); --f: var(--external-fill); cursor: pointer; }
.sv-kind-module { --k: var(--module-stroke); --f: var(--module-fill); }
.sv-kind-group { --k: var(--group-stroke); --f: var(--group-fill); }
.sv-kind-backend, .sv-kind-service, .sv-kind-function, .sv-kind-component,
.sv-kind-api, .sv-kind-worker, .sv-kind-cli { --k: var(--backend-stroke); --f: var(--backend-fill); }
.sv-kind-frontend { --k: var(--frontend-stroke); --f: var(--frontend-fill); }
.sv-kind-database, .sv-kind-datastore, .sv-kind-table { --k: var(--database-stroke); --f: var(--database-fill); }
.sv-kind-cloud { --k: var(--cloud-stroke); --f: var(--cloud-fill); }
.sv-kind-security, .sv-kind-auth, .sv-kind-class { --k: var(--security-stroke); --f: var(--security-fill); }
.sv-kind-messagebus, .sv-kind-queue { --k: var(--messagebus-stroke); --f: var(--messagebus-fill); }
.sv-kind-endpoint, .sv-kind-interface, .sv-kind-method { --k: var(--cloud-stroke); --f: var(--cloud-fill); }
.sv-mask { fill: var(--mask); stroke: none; }
.sv-box { fill: var(--f); stroke: var(--k); stroke-width: 1.5; transition: stroke .14s, fill .14s; }
.sv-drillable .sv-box { stroke-dasharray: 6 3; }
.sv-dot { fill: var(--k); }
.sv-sigil { fill: none; stroke: var(--k); stroke-width: 1.3; stroke-linecap: round; stroke-linejoin: round; }
.sv-box-label {
  fill: var(--ink); text-anchor: middle; dominant-baseline: middle;
  font-family: var(--mono); font-weight: 600;
}
.sv-box-sublabel {
  fill: var(--dim); text-anchor: middle; dominant-baseline: middle;
  font-family: var(--mono); font-weight: 400;
}
.sv-src { fill: var(--mask); stroke: var(--k); stroke-width: .8; opacity: .9; }
.sv-src-text { fill: var(--k); text-anchor: middle; dominant-baseline: middle; font-family: var(--mono); font-weight: 700; letter-spacing: .08em; }
.sv-drill { fill: var(--k); text-anchor: end; dominant-baseline: middle; }
.sv-node:hover .sv-box { stroke: var(--accent); stroke-width: 2; }
.sv-region { fill: color-mix(in srgb, var(--k) 5%, transparent); stroke: var(--k); stroke-width: 1; stroke-dasharray: 8 4; }
.sv-boundary { --k: var(--cloud-stroke); }
.sv-boundary.sv-kind-stage { --k: var(--faint); }
.sv-boundary.sv-kind-stage .sv-region { stroke-dasharray: 4 4; fill: color-mix(in srgb, var(--k) 3%, transparent); }
.sv-region-label { fill: var(--k); font-family: var(--mono); font-weight: 600; letter-spacing: .02em; }
.sv-edge-label { fill: var(--dim); text-anchor: middle; dominant-baseline: middle; font-family: var(--mono); font-weight: 500; }
.sv-variant-emphasis { stroke: var(--backend-stroke); }
.sv-variant-emphasis.sv-edge-label { fill: var(--backend-stroke); }
.sv-variant-dashed.sv-edge { stroke-dasharray: 6 4; }
.sv-variant-security { stroke: var(--security-stroke); }
.sv-variant-security.sv-edge-label { fill: var(--security-stroke); }

/* Hover lights the path through the hovered element and recedes the rest. */
.sv-canvas.is-hovering .sv-node:not(.is-path), .sv-canvas.is-hovering .sv-route:not(.is-path) { opacity: .28; transition: opacity .14s; }
.sv-canvas.is-hovering .sv-route.is-path .sv-edge { stroke: var(--accent); opacity: 1; }
.sv-canvas.is-hovering .sv-route.is-path .sv-arrowhead { fill: var(--accent); }
.sv-canvas.is-hovering .sv-node.is-path .sv-box { stroke-width: 2; }
/* Click focuses: the clicked box glows, its neighbours and their arrows stay
   lit with their verbs, everything else recedes hard (Archify's 13%). */
.sv-canvas.is-focused .sv-node:not(.is-path), .sv-canvas.is-focused .sv-route:not(.is-path) { opacity: .13; transition: opacity .14s; }
.sv-canvas.is-focused .sv-boundary { opacity: .4; }
.sv-canvas.is-focused .sv-route.is-path .sv-edge { stroke: var(--accent); opacity: 1; stroke-width: 2.25; }
.sv-canvas.is-focused .sv-route.is-path .sv-arrowhead { fill: var(--accent); }
.sv-canvas.is-focused .sv-route.is-path .sv-edge-label { fill: var(--ink); font-weight: 700; }
.sv-canvas.is-focused .sv-node.is-path .sv-box { stroke-width: 2; }
.sv-canvas.is-focused .sv-node.is-focus .sv-box { stroke: var(--accent); stroke-width: 2.5; filter: drop-shadow(0 0 7px var(--accent)); }
/* An opened container: accent frame, siblings receded so the eye lands inside. */
[data-scope="parent"] .sv-node:not(.sv-expanded-host):not(.is-path), [data-scope="parent"] .sv-route:not(.is-path) { opacity: .5; }
[data-scope="parent"] .sv-boundary { opacity: .55; }

.sv-container {
  fill: color-mix(in srgb, var(--accent) 4%, var(--canvas));
  stroke: var(--accent);
  stroke-width: 1.5; stroke-dasharray: 7 4;
  filter: drop-shadow(0 0 10px color-mix(in srgb, var(--accent) 35%, transparent));
}
.sv-container-label {
  fill: var(--accent); font-family: var(--mono); font-weight: 700; letter-spacing: .02em;
}
.sv-collapse { fill: var(--faint); text-anchor: end; cursor: pointer; }
.sv-collapse:hover { fill: var(--accent); }
.sv-band { fill: color-mix(in srgb, var(--ink) 3%, transparent); rx: 12; }
.sv-band-label {
  fill: var(--faint); font-size: 10px; font-family: var(--ui);
  letter-spacing: .08em; text-transform: uppercase;
}

.sv-edge { fill: none; stroke: var(--edge); stroke-linecap: round; }
.sv-w1 { stroke-width: 1.25; opacity: .6; }
.sv-w2 { stroke-width: 2; opacity: .8; }
.sv-w3 { stroke-width: 3; opacity: 1; }
.sv-edge-weak { stroke: var(--warn); stroke-dasharray: 5 4; }
.sv-route { cursor: pointer; }
.sv-route:hover .sv-edge { stroke: var(--accent); opacity: 1; }
.sv-arrowhead { fill: var(--edge); }
.sv-route:hover .sv-arrowhead { fill: var(--accent); }

/* The legend doubles as the honesty line: kinds with real counts, and the
   promise that a click lands on source. */
.legend {
  display: flex; gap: 16px; flex-wrap: wrap; align-items: center;
  color: var(--dim); font-size: 12px; margin: 12px 2px 0;
}
.legend .sw {
  display: inline-block; width: 10px; height: 10px; border-radius: 3px;
  margin-right: 6px; vertical-align: -1px;
  background: color-mix(in srgb, var(--k) 14%, var(--surface));
  border: 1.5px solid color-mix(in srgb, var(--k) 65%, var(--line));
}
.legend .promise { margin-left: auto; color: var(--faint); }
.legend .arrows { color: var(--faint); }
/* Explorer controls (design section 4): search, kind toggles, a path tool.
   All of it is class toggling on the SVG the reader already sees; nothing
   here lays anything out or builds markup from repository text. */
.legend .sw-toggle { cursor: pointer; user-select: none; }
.legend .sw-toggle.off { opacity: .35; text-decoration: line-through; }
.tab { scroll-margin-top: 72px; }
.explore { display: flex; gap: 10px; align-items: center; margin: 0 0 10px; font-size: 12px; color: var(--faint); flex-wrap: wrap; }
.explore input { font: inherit; background: var(--raised); color: var(--ink); border: 1px solid var(--line); border-radius: 6px; padding: 4px 8px; width: 280px; }
.explore .path { color: var(--ink); }
.explore .export { margin-left: auto; display: flex; gap: 6px; }
.explore .export button { font: inherit; font-size: 11px; color: var(--accent); background: var(--raised); border: 1px solid var(--line); border-radius: 6px; padding: 4px 9px; cursor: pointer; }
.explore .export button:hover { border-color: var(--accent); }
svg.is-searching .sv-node:not(.is-hit), svg.is-searching .sv-route { opacity: .15; }
svg .sv-node.is-off, svg .sv-route.is-off { opacity: .1; }
svg.is-pinned .sv-node:not(.is-path), svg.is-pinned .sv-route:not(.is-path) { opacity: .15; }
aside .conn.link { cursor: pointer; }
/* Shift-click is the path gesture, and shift-click also selects text. */
svg text { user-select: none; }
aside .conn.link:hover { text-decoration: underline; }
/* Narrow screens keep the side panel, narrower (review 21, N5: the overlay
   came back below 1100px and covered three boxes and the strip). Last, so it
   wins over the aside rule above at equal specificity. */
@media (max-width: 1100px) { aside#panel { width: 280px; } body:has(aside.is-open) .tab { padding-left: 328px; } }

/* The passport: a card over the canvas, Archify's semantic passport with
   what only we have, the verified source lines. */
aside {
  position: fixed; left: 28px; top: 132px; width: 352px; max-height: calc(100vh - 160px);
  overflow: auto; background: var(--surface); border: 1px solid var(--accent);
  border-radius: 6px; padding: 14px 16px 16px; font-family: var(--mono); font-size: 11px;
  transform: translateX(-120%); transition: transform .16s ease; z-index: 5;
  box-shadow: 0 18px 48px #0000004d;
}
aside.is-open { transform: none; }
aside .eyebrow { margin: 0 0 4px; font-size: 9px; letter-spacing: .16em; text-transform: uppercase; color: var(--accent); font-weight: 700; }
aside h2 { font-size: 15px; margin: 0 0 2px; font-family: var(--mono); font-weight: 700; padding-right: 60px; }
aside .sub { margin: 0 0 10px; color: var(--dim); font-size: 11px; }
aside .chips { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; margin: 0 0 10px; }
aside .chips span { font-size: 9px; letter-spacing: .08em; text-transform: uppercase; padding: 2px 7px; border: 1px solid var(--line); border-radius: 3px; color: var(--dim); }
aside .chips span.chip-kind { border-color: var(--k); color: var(--k); background: color-mix(in srgb, var(--k) 12%, transparent); font-weight: 700; }
aside .chips span.chip-context { color: var(--ink); }
aside .chips span.chip-role { color: var(--security-stroke); border-color: color-mix(in srgb, var(--security-stroke) 50%, var(--line)); }
aside .chips span.chip-member { text-transform: none; letter-spacing: 0; font-size: 10px; }
aside .open { display: block; width: 100%; margin: 0 0 10px; font: inherit; font-size: 11px; font-weight: 700; color: var(--accent); background: var(--raised); border: 1px solid color-mix(in srgb, var(--accent) 50%, var(--line)); border-radius: 4px; padding: 6px 9px; cursor: pointer; text-align: left; }
aside .open:hover { border-color: var(--accent); }
aside .open[hidden] { display: none; }
aside a, aside span.dead, aside .conn .name { overflow-wrap: anywhere; }
aside .chips code { font-size: 10px; color: var(--faint); }
aside .chips.also:not(:empty)::before { content: 'also in'; font-size: 9px; letter-spacing: .12em; text-transform: uppercase; color: var(--faint); }
aside .chips.also span { cursor: pointer; color: var(--accent); border-color: color-mix(in srgb, var(--accent) 50%, var(--line)); }
aside .chips.also span:hover { background: var(--raised); }
aside .summary { margin: 0 0 8px; color: var(--dim); font-size: 11px; }
aside .rule { border: 0; border-top: 1px solid var(--line); margin: 8px 0 10px; }
aside .section { font-size: 9px; letter-spacing: .14em; text-transform: uppercase; color: var(--faint); margin: 12px 0 4px; font-weight: 700; }
aside .reach { display: flex; gap: 8px; margin: 0 0 4px; }
aside .reach button { flex: 1; display: flex; justify-content: space-between; align-items: center; font: inherit; font-size: 11px; color: var(--ink); background: var(--raised); border: 1px solid var(--line); border-radius: 4px; padding: 6px 9px; cursor: pointer; }
aside .reach button strong { color: var(--accent); }
aside .reach button:disabled { opacity: .4; cursor: default; }
aside .reach button:hover:not(:disabled) { border-color: var(--accent); }
aside .conn { display: grid; grid-template-columns: 52px 1fr; column-gap: 8px; color: var(--ink); font-size: 11px; padding: 5px 4px; border-radius: 4px; }
aside .conn .dir { color: var(--accent); font-size: 9px; letter-spacing: .1em; font-weight: 700; padding-top: 2px; }
aside .conn .name { font-weight: 600; }
aside .conn .verb { grid-column: 2; color: var(--dim); font-size: 10px; }
aside ul { list-style: none; padding: 0; margin: 0; }
aside li { margin: 0; }
aside a, aside span.dead {
  color: var(--accent); font-family: var(--mono); font-size: 11px;
  text-decoration: none; display: block; padding: 4px 4px; border-radius: 4px;
}
aside a:hover { background: var(--raised); }
aside span.dead { color: var(--warn); }
aside .close { position: absolute; top: 10px; right: 10px; font: inherit; font-size: 14px; color: var(--faint); background: none; border: 0; cursor: pointer; padding: 2px 6px; }
aside .close:hover { color: var(--ink); }

.hint { color: var(--dim); font-size: 13px; }
/* Guided views: Archify's strip above the canvas, computed. A chapter is a
   box and what the drawn arrows connect it to; clicking one focuses it. */
.guided { display: flex; gap: 14px; align-items: center; flex-wrap: wrap; border: 1px solid var(--line); border-radius: 8px; padding: 8px 12px; margin: 0 0 12px; background: var(--surface); position: sticky; top: calc(var(--header-h, 58px) + 8px); z-index: 4; }
.guided-head { display: flex; flex-direction: column; gap: 2px; min-width: 190px; font-family: var(--mono); }
.guided-head .eyebrow { font-size: 9px; letter-spacing: .16em; text-transform: uppercase; color: var(--accent); font-weight: 700; }
.guided-head .eyebrow .progress { color: var(--dim); margin-left: 6px; }
.guided-head strong { font-size: 13px; }
.guided-head .play { align-self: flex-start; margin-top: 4px; font: inherit; font-size: 11px; color: var(--accent); background: none; border: 0; padding: 0; cursor: pointer; }
.guided-head .play:hover { text-decoration: underline; }
.chapters { display: flex; gap: 8px; flex-wrap: wrap; list-style: none; margin: 0; padding: 0; }
.chapter { display: flex; gap: 8px; align-items: center; border: 1px solid var(--line); border-radius: 6px; padding: 6px 10px; cursor: pointer; font-family: var(--mono); font-size: 11px; background: var(--raised); }
.chapter:hover, .chapter.is-active { border-color: var(--accent); }
.chapter.is-active { box-shadow: inset 0 0 0 1px var(--accent); }
.chapter .num { color: var(--accent); font-weight: 700; font-size: 10px; border: 1px solid color-mix(in srgb, var(--accent) 50%, var(--line)); border-radius: 3px; padding: 0 4px; }
.chapter .title { color: var(--ink); font-weight: 600; }
.chapter .counts { color: var(--faint); font-size: 10px; }
/* Cards under the root canvas: computed from the graph, every item cites. */
.cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 12px; margin: 14px 0 4px; }
.card { border: 1px solid var(--line); border-radius: 8px; padding: 12px 14px; background: color-mix(in srgb, var(--surface) 70%, transparent); font-family: var(--mono); }
.card-header { display: flex; align-items: center; gap: 8px; font-weight: 700; font-size: 13px; margin: 0 0 8px; }
.card-dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; }
.card-dot.cyan { background: var(--accent); } .card-dot.violet { background: var(--database-stroke); }
.card-dot.amber { background: var(--cloud-stroke); } .card-dot.rose { background: var(--security-stroke); }
.card ul { list-style: none; margin: 0; padding: 0; }
.card-item { font-size: 11px; color: var(--ink); padding: 4px 0; border-radius: 4px; display: flex; justify-content: space-between; gap: 8px; }
.card-item.link { cursor: pointer; } .card-item.link:hover { color: var(--accent); }
.card-src { color: var(--accent); font-size: 9px; letter-spacing: .08em; border: 1px solid color-mix(in srgb, var(--accent) 50%, var(--line)); border-radius: 3px; padding: 0 5px; white-space: nowrap; align-self: flex-start; }
.card-more, .card-empty { font-size: 11px; color: var(--faint); margin: 4px 0 0; }
.withheld {
  border-left: 3px solid var(--warn); padding: 2px 0 2px 14px;
  margin-top: 24px; color: var(--dim); font-size: 13px;
}
noscript .hint { color: var(--warn); }
"""
    )


def _js() -> Markup:
    """Progressive enhancement only.

    Every selector below acts on elements Python already wrote, and no branch
    builds markup from data: the evidence panel reads `textContent` and sets
    `textContent`, never `innerHTML`. That is deliberate, so the escaping
    guarantee is not silently re-opened on the client.
    """
    return raw(
        """
(function () {
  var KEY = 'svarupa.base';
  var base = document.getElementById('base');
  base.value = localStorage.getItem(KEY) || '';
  base.addEventListener('input', function () {
    localStorage.setItem(KEY, base.value);
    render();
  });

  // The theme is the reader's, not the artifact's: it lives in localStorage
  // and never in the file, so the bytes stay identical between runs and the
  // no-JS reader gets the root palette.
  var THEME = 'svarupa.theme';
  var themeBtn = document.getElementById('theme');
  // Dark is the root palette (Archify's midnight console); light is the
  // attribute. The button shows the theme you are IN and its title the one a
  // click gives: naming the target alone read as the state (review #20 C1).
  function applyTheme(name) {
    if (name === 'light') {
      document.documentElement.setAttribute('data-theme', 'light');
      themeBtn.textContent = '☀ light';
      themeBtn.title = 'switch to the dark theme';
    } else {
      document.documentElement.removeAttribute('data-theme');
      themeBtn.textContent = '☾ dark';
      themeBtn.title = 'switch to the light theme';
    }
  }
  applyTheme(localStorage.getItem(THEME) || 'dark');
  themeBtn.addEventListener('click', function () {
    var next = document.documentElement.hasAttribute('data-theme') ? 'dark' : 'light';
    localStorage.setItem(THEME, next);
    applyTheme(next);
  });

  // The header may take two rows on a narrow screen; drills scroll under it.
  function measureHeader() {
    var hd = document.querySelector('header');
    if (hd) document.documentElement.style.setProperty('--header-h', hd.offsetHeight + 'px');
  }
  measureHeader();
  window.addEventListener('resize', measureHeader);

  var panel = document.getElementById('panel');
  var title = document.getElementById('panel-title');
  var list = document.getElementById('panel-list');
  var current = [];

  // Schemes a citation link may use. `file` is repository-derived, and a
  // directory named `javascript:...` otherwise supplies the scheme itself.
  // Relying on the trailing '#L<line>' to break the payload's syntax is not
  // a control, so the scheme is checked here at the sink.
  var SAFE = { 'http:': 1, 'https:': 1, 'file:': 1 };

  function safeHref(candidate) {
    try {
      var u = new URL(candidate, document.baseURI);
      return SAFE[u.protocol] ? u.href : null;
    } catch (e) {
      return null;
    }
  }

  function link(ref) {
    var parts = ref.split(':');
    // A citation without a line is an empty file cited as itself.
    var line = /^\\d+$/.test(parts[parts.length - 1]) ? parts.pop() : null;
    var file = parts.join(':');
    var prefix = base.value.replace(/\\/+$/, '');
    var href = safeHref((prefix ? prefix + '/' : '') + file + (line === null ? '' : '#L' + line));
    if (href === null) {
      // Shown, not hidden. The citation is still the evidence; what is
      // withheld is only the ability to click it.
      var span = document.createElement('span');
      span.textContent = ref + ' (no safe link for this path)';
      return span;
    }
    var a = document.createElement('a');
    a.textContent = line === null ? ref + ' (empty file)' : ref;
    a.href = href;
    return a;
  }

  function render() {
    list.textContent = '';
    current.forEach(function (ref) {
      var li = document.createElement('li');
      li.appendChild(link(ref));
      list.appendChild(li);
    });
  }

  var lastScope = null;
  var lastBoxClick = null;
  var pins = [];
  document.addEventListener('dblclick', function (ev) {
    if (!lastBoxClick || Date.now() - lastBoxClick.at > 700) return;
    var node = lastBoxClick.node;
    var child = node.getAttribute('data-child');
    var tab = ev.target.closest ? ev.target.closest('.tab') : null;
    if (!child || !tab || tab !== node.closest('.tab')) return;
    lastBoxClick = null;
    openView(node, child);
  });
  var eyebrowEl = document.getElementById('panel-eyebrow');
  var subEl = document.getElementById('panel-sub');
  var metaEl = document.getElementById('panel-meta');
  var alsoEl = document.getElementById('panel-also');
  var sumEl = document.getElementById('panel-summary');
  var outEl = document.getElementById('panel-out');
  var outH = document.getElementById('panel-out-h');
  var inEl = document.getElementById('panel-in');
  var inH = document.getElementById('panel-in-h');
  var upBtn = document.getElementById('reach-up');
  var downBtn = document.getElementById('reach-down');
  var openBtn = document.getElementById('panel-open');
  var linkBtn = document.getElementById('panel-link');
  // The box the passport currently shows, for the deep link. A box id is
  // repo-derived text; it enters the URL only percent-encoded.
  var linkTarget = null;

  // A box's full label, from the title the renderer wrote for it: the OUT
  // and IN lists showed raw ids (`ext:database:SQL database`) where the box
  // shows a label (review #20 C12). The id stays as the row's tooltip.
  function labelOf(root, id) {
    var nd = null;
    root.querySelectorAll('.sv-node').forEach(function (x) { if (!nd && x.getAttribute('data-id') === id) nd = x; });
    var t = nd && nd.querySelector('title');
    return t ? t.textContent.split('\\n')[0] : id;
  }

  // The passport: what the element is, the frames it sits in, its
  // connections in both directions with their verbs, and how far it reaches
  // over the arrows drawn in this view. Everything set via textContent;
  // nothing here builds markup from data. A node's canvas: in an expanded
  // view the parent and the embedded child are two canvases in one SVG and
  // can share ids, so connections are read from the nearest scope.
  function scopeOf(el) {
    return el.closest('[data-scope]') || el.closest('svg');
  }
  function routesOf(root) {
    return Array.prototype.slice.call(root.querySelectorAll('.sv-route'));
  }
  function chip(text, cls, kind) {
    if (!text) return;
    var s = document.createElement('span');
    s.textContent = text;
    s.className = cls;
    if (kind) s.classList.add('sv-kind-' + kind);
    metaEl.appendChild(s);
  }
  function connItem(list, dir, said, other, shown) {
    var li = document.createElement('li');
    li.className = 'conn link';
    li.setAttribute('data-target', other);
    li.title = other;
    var d = document.createElement('span'); d.className = 'dir'; d.textContent = dir;
    var n = document.createElement('span'); n.className = 'name'; n.textContent = shown || other;
    var v = document.createElement('span'); v.className = 'verb'; v.textContent = said;
    li.appendChild(d); li.appendChild(n); li.appendChild(v);
    list.appendChild(li);
  }
  // Directed closure over the drawn arrows: what feeds this box (up) or
  // what it feeds (down), within the view the reader is looking at.
  function reach(root, id, dir) {
    var seen = {}; seen[id] = 1;
    var queue = [id];
    var rs = routesOf(root);
    while (queue.length) {
      var cur = queue.shift();
      rs.forEach(function (r) {
        var s = r.getAttribute('data-src'), d = r.getAttribute('data-dst');
        var nxt = dir === 'down' ? (s === cur ? d : null) : (d === cur ? s : null);
        if (nxt && !seen[nxt]) { seen[nxt] = 1; queue.push(nxt); }
      });
    }
    delete seen[id];
    return Object.keys(seen);
  }
  function kindOf(node) {
    var kind = node.getAttribute('data-kind') || '';
    if (!kind) {
      node.classList.forEach(function (c) { if (c.indexOf('sv-kind-') === 0) kind = c.slice(8); });
    }
    return kind;
  }
  function passport(node) {
    var id = node.getAttribute('data-id') || '';
    var root = scopeOf(node);
    lastScope = root;
    var isFrame = node.classList.contains('sv-boundary');
    eyebrowEl.textContent = isFrame ? 'Frame' : 'Passport';
    subEl.textContent = node.getAttribute('data-sublabel') || '';
    metaEl.textContent = '';
    var kind = kindOf(node);
    chip(kind, 'chip-kind', kind);
    if (!isFrame) {
      root.querySelectorAll('.sv-boundary').forEach(function (b) {
        var members = (b.getAttribute('data-members') || '').split('\\n');
        if (members.indexOf(id) >= 0) chip(b.getAttribute('data-label') || '', 'chip-context');
      });
    }
    (node.getAttribute('data-roles') || '').split(',').forEach(function (r) { chip(r, 'chip-role'); });
    // A group names what it holds; its own id is not a module's.
    (node.getAttribute('data-members') || '').split('\\n').filter(Boolean).forEach(function (m) { chip(m, 'chip-context chip-member'); });
    var code = document.createElement('code');
    code.textContent = id;
    metaEl.appendChild(code);
    // `group:` and `tree:` boxes are drawn, not graph nodes: `svarupa query`
    // does not answer for them, and the card says so (review #21 N11).
    if (/^(group:|tree:|in:|req:)/.test(id)) chip('diagram box, not a graph node', 'chip-context');
    // The same box in other diagrams: a module is in Architecture, Data
    // flow, Module deps and a request story at once, and the graph is one
    // graph. Found by id in the plain views of every other tab.
    alsoEl.textContent = '';
    var hereTab = node.closest('.tab');
    if (!isFrame) {
      document.querySelectorAll('.tab').forEach(function (other) {
        if (other === hereTab || !other.id) return;
        var hit = null;
        other.querySelectorAll('.view:not([data-host])').forEach(function (v) {
          if (hit) return;
          v.querySelectorAll('.sv-node').forEach(function (nd) {
            if (!hit && nd.getAttribute('data-id') === id) hit = v;
          });
        });
        if (!hit) return;
        var s = document.createElement('span');
        s.textContent = other.id.replace(/^d-/, '');
        s.setAttribute('data-tab', other.id);
        s.setAttribute('data-view', hit.getAttribute('data-view'));
        s.setAttribute('data-target', id);
        alsoEl.appendChild(s);
      });
    }
    // A deep link names a box, never a frame: only graph-node passports
    // offer it.
    linkBtn.hidden = isFrame;
    linkTarget = isFrame ? null : { tab: hereTab && hereTab.id, box: id };
    outEl.textContent = '';
    inEl.textContent = '';
    var nOut = 0, nIn = 0;
    routesOf(root).forEach(function (r) {
      var s = r.getAttribute('data-src'), d = r.getAttribute('data-dst');
      if (s !== id && d !== id) return;
      // The note carries the count ("12 imports"); the label is the verb.
      var said = r.getAttribute('data-note') || r.getAttribute('data-label') || 'connects';
      if (s === id) { nOut += 1; connItem(outEl, 'OUT →', said, d, labelOf(root, d)); }
      else { nIn += 1; connItem(inEl, '← IN', said, s, labelOf(root, s)); }
    });
    outH.textContent = 'Outgoing · ' + nOut;
    inH.textContent = 'Incoming · ' + nIn;
    sumEl.textContent = nOut + ' outgoing · ' + nIn + ' incoming';
    var up = isFrame ? [] : reach(root, id, 'up');
    var down = isFrame ? [] : reach(root, id, 'down');
    upBtn.querySelector('strong').textContent = up.length;
    downBtn.querySelector('strong').textContent = down.length;
    upBtn.disabled = !up.length;
    downBtn.disabled = !down.length;
    upBtn.onclick = function () { light(root, node, up); };
    downBtn.onclick = function () { light(root, node, down); };
    // The drill, named: a single click on a drillable box gave a card and no
    // way on to the next level (review #20 M4). The button and the chevron
    // both open it; a double-click still does. It says "in place" only when
    // the pre-rendered expansion exists; a host past MAX_EXPANSION_HOST_BOXES
    // or a child too big to embed falls back to the child as its own view,
    // and the button names what it does. A withheld child view has no target
    // at all: no button, rather than a named click that opens nothing
    // (review #26 F5); the tab's withheld note names the view and the reason.
    var child = node.getAttribute('data-child');
    var childWithheld = child !== null && node.getAttribute('data-child-withheld') !== null;
    openBtn.hidden = !child || childWithheld;
    if (child && !childWithheld) {
      var pv = node.closest('.view');
      var hostId = pv.dataset.view;
      if (pv.dataset.host) {
        hostId = node.closest('[data-scope="child"]') ? pv.dataset.plain : pv.dataset.host;
      }
      var expandedId = hostId + '//' + node.getAttribute('data-id') + '//expanded';
      var hasExpansion = !!node.closest('.tab').querySelector('[data-view="' + CSS.escape(expandedId) + '"]');
      openBtn.textContent = hasExpansion ? 'Open in place \u203a' : 'Open \u203a';
    }
    openBtn.onclick = child && !childWithheld ? function () { openView(node, child); } : null;
  }

  // Focus: the clicked box glows, a chosen set stays lit, the rest recedes.
  function clearFocus() {
    // Every lit state goes: focus, the hover left behind while focus
    // suspended mouseout (review #19 F1: a background click left the whole
    // canvas at 28 percent with nothing lit), and a pinned path (F2).
    document.querySelectorAll('svg.is-focused, svg.is-hovering, svg.is-pinned').forEach(function (s) {
      s.classList.remove('is-focused'); s.classList.remove('is-hovering'); s.classList.remove('is-pinned');
      s.querySelectorAll('.is-path, .is-focus').forEach(function (x) {
        x.classList.remove('is-path'); x.classList.remove('is-focus');
      });
    });
    pins = [];
    document.querySelectorAll('.explore .path').forEach(function (p) { p.textContent = ''; });
  }
  function light(root, node, ids) {
    var svg = node.closest('svg');
    if (!svg) return;
    clearFocus();
    var set = {};
    ids.forEach(function (i) { set[i] = 1; });
    set[node.getAttribute('data-id')] = 1;
    root.querySelectorAll('.sv-node').forEach(function (nd) {
      if (set[nd.getAttribute('data-id')]) nd.classList.add('is-path');
    });
    routesOf(root).forEach(function (r) {
      if (set[r.getAttribute('data-src')] && set[r.getAttribute('data-dst')]) r.classList.add('is-path');
    });
    node.classList.add('is-focus');
    svg.classList.add('is-focused');
  }
  function focus(node) {
    var root = scopeOf(node);
    var id = node.getAttribute('data-id');
    var ids = [];
    routesOf(root).forEach(function (r) {
      var s = r.getAttribute('data-src'), d = r.getAttribute('data-dst');
      if (s === id) ids.push(d); else if (d === id) ids.push(s);
    });
    light(root, node, ids);
  }

  function show(name, refs, node) {
    // A frame is titled by what it says (02 / Handlers), not by its id.
    var frameLabel = node && node.classList.contains('sv-boundary') ? node.getAttribute('data-label') : null;
    title.textContent = frameLabel || name;
    if (node && (node.classList.contains('sv-node') || node.classList.contains('sv-boundary'))) {
      passport(node);
      if (node.classList.contains('sv-node')) focus(node); else clearFocus();
    } else if (node && node.classList.contains('card-item')) {
      openBtn.hidden = true;
      linkBtn.hidden = true;
      linkTarget = null;
      eyebrowEl.textContent = 'Fact';
      subEl.textContent = '';
      metaEl.textContent = '';
      outEl.textContent = ''; inEl.textContent = '';
      outH.textContent = 'Outgoing'; inH.textContent = 'Incoming';
      sumEl.textContent = 'computed from the graph; the lines below are the claim';
      upBtn.disabled = true; downBtn.disabled = true;
      clearFocus();
    } else {
      openBtn.hidden = true;
      linkBtn.hidden = true;
      linkTarget = null;
      eyebrowEl.textContent = 'Connection';
      subEl.textContent = node ? (node.getAttribute('data-note') || node.getAttribute('data-label') || '') : '';
      metaEl.textContent = '';
      outEl.textContent = ''; inEl.textContent = '';
      outH.textContent = 'Outgoing'; inH.textContent = 'Incoming';
      var noteText = node ? (node.getAttribute('data-note') || '') : '';
      sumEl.textContent = noteText && noteText !== subEl.textContent ? noteText : '';
      upBtn.disabled = true; downBtn.disabled = true;
      clearFocus();
      if (node) { node.classList.add('is-path'); var svg = node.closest('svg'); if (svg) svg.classList.add('is-focused'); }
    }
    current = refs;
    render();
    panel.classList.add('is-open');
  }
  document.getElementById('panel-close').addEventListener('click', function () {
    panel.classList.remove('is-open');
    clearFocus();
  });

  // Deep links: `#d-<tab>/<box id>` opens the tab and the box's passport.
  // The plain `#d-<tab>` form stays pure CSS (`:target`); the box suffix is
  // read here, and the address bar is then set to the plain form so the CSS
  // switch sees it. The box id is repo-derived text and travels only
  // percent-encoded; the base keeps whatever scheme the page was opened
  // over, file: and http(s): alike. With JS off the link degrades to the
  // default tab — the no-JS story already is "the diagrams, static".
  linkBtn.addEventListener('click', function () {
    if (!linkTarget || !linkTarget.tab) return;
    var url = location.href.split('#')[0] + '#' + linkTarget.tab + '/' + encodeURIComponent(linkTarget.box);
    function done() {
      linkBtn.textContent = 'copied';
      setTimeout(function () { linkBtn.textContent = 'copy link'; }, 1200);
    }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(url).then(done, done);
      return;
    }
    var ta = document.createElement('textarea');
    ta.value = url;
    document.body.appendChild(ta);
    ta.select();
    try { document.execCommand('copy'); } catch (e) { /* the textarea stays selected */ }
    ta.remove();
    done();
  });

  function openFromHash() {
    // Split at the first '/', no regex: the JS here is embedded in a Python
    // string, where every backslash is two.
    var h = location.hash;
    var slash = h.indexOf('/');
    // indexOf answers -1 for "absent"; a less-than sign in this script fails
    // the escaped-markup check the emit suite runs on the whole document.
    if (slash === -1) return;
    var tab = document.getElementById(h.slice(1, slash));
    if (!tab) return;
    var id;
    try { id = decodeURIComponent(h.slice(slash + 1)); } catch (e) { return; }
    if (history.replaceState) history.replaceState(null, '', '#' + tab.id);
    var node = null;
    tab.querySelectorAll('.sv-node').forEach(function (nd) {
      if (!node && nd.getAttribute('data-id') === id) node = nd;
    });
    if (!node) return;
    // The box may live in a sibling view of the tab (a drill level down);
    // open that view, the way a click path to it would have.
    var view = node.closest('.view');
    if (view && !view.classList.contains('is-open')) {
      tab.querySelectorAll('.view').forEach(function (v) { v.classList.remove('is-open'); });
      view.classList.add('is-open');
    }
    // The same card a click produces, on the view the box was found in.
    var root = scopeOf(node);
    var refs = (node.getAttribute('data-evidence') || '').split('\\n').filter(Boolean);
    show(labelOf(root, id), refs, node);
    node.scrollIntoView({ block: 'center', inline: 'center' });
  }
  window.addEventListener('hashchange', openFromHash);
  openFromHash();
  // Keyboard (review #20 S9): Escape closes the passport and clears every lit
  // state; Enter on a focused box is its click. Boxes carry tabindex="0".
  document.addEventListener('keydown', function (ev) {
    if (ev.key === 'Escape') {
      panel.classList.remove('is-open'); clearPins(); clearFocus();
      return;
    }
    if (ev.key === 'Enter' && ev.target && ev.target.classList && (ev.target.classList.contains('sv-node') || ev.target.classList.contains('chapter') || ev.target.classList.contains('sw-toggle'))) {
      ev.preventDefault();
      // Shift+Enter on a drillable box is the keyboard drill (review #21
      // N14: the chevron is not focusable, so Enter alone left a keyboard
      // reader with the passport and 5 to 19 Tabs to the Open button).
      var kid = ev.target.getAttribute('data-child');
      if (ev.shiftKey && kid && ev.target.classList.contains('sv-node')) { openView(ev.target, kid); return; }
      ev.target.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, detail: 1 }));
    }
  });

  // Hover: light the path through the element and recede the rest.
  document.addEventListener('mouseover', function (ev) {
    var el = ev.target.closest('.sv-node, .sv-route');
    var svg = el && el.closest('svg');
    if (!svg || svg.classList.contains('is-pinned') || svg.classList.contains('is-focused')) return;
    var root = scopeOf(el);
    svg.querySelectorAll('.is-path').forEach(function (x) { x.classList.remove('is-path'); });
    var ids = {};
    if (el.classList.contains('sv-node')) {
      var id = el.getAttribute('data-id');
      ids[id] = 1;
      root.querySelectorAll('.sv-route').forEach(function (r) {
        var s = r.getAttribute('data-src'), d = r.getAttribute('data-dst');
        if (s === id || d === id) { r.classList.add('is-path'); ids[s] = 1; ids[d] = 1; }
      });
    } else {
      el.classList.add('is-path');
      ids[el.getAttribute('data-src')] = 1;
      ids[el.getAttribute('data-dst')] = 1;
    }
    root.querySelectorAll('.sv-node').forEach(function (nd) {
      if (ids[nd.getAttribute('data-id')]) nd.classList.add('is-path');
    });
    svg.classList.add('is-hovering');
  });
  document.addEventListener('mouseout', function (ev) {
    var el = ev.target.closest('.sv-node, .sv-route');
    var svg = el && el.closest('svg');
    if (!svg) return;
    var to = ev.relatedTarget && ev.relatedTarget.closest && ev.relatedTarget.closest('.sv-node, .sv-route');
    if (to || svg.classList.contains('is-pinned') || svg.classList.contains('is-focused')) return;
    svg.classList.remove('is-hovering');
    svg.querySelectorAll('.is-path').forEach(function (x) { x.classList.remove('is-path'); });
  });

  document.addEventListener('click', function (ev) {
    // The container header's close mark collapses back to the parent view.
    var collapse = ev.target.closest('.sv-collapse');
    if (collapse) {
      var crumb = collapse.closest('.view').querySelector('[data-up]');
      if (crumb) { crumb.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true})); }
      return;
    }
    var node = ev.target.closest('[data-evidence]');
    if (!node) {
      if (!ev.target.closest('aside')) { panel.classList.remove('is-open'); clearPins(); clearFocus(); }
      return;
    }
    if (ev.shiftKey && node.classList.contains('sv-node')) { pinPath(node); return; }
    var refs = node.getAttribute('data-evidence').split('\\n').filter(Boolean);
    var child = node.getAttribute('data-child');
    if (child && (ev.detail === 2 || ev.target.closest('.sv-drill'))) { openView(node, child); return; }
    // The first click of a double-click opens the side panel, which moves
    // the canvas 154 to 192px; the second click then lands on the scroller
    // (review #23 F1). Remember the box, so the dblclick that follows can
    // still open it wherever its second click landed.
    // The second click of the pair may land on a neighbouring box the shift
    // moved under the cursor (review #24 N1: a non-drillable ingress box took
    // the pending drillable one's place); a pending drillable click survives
    // its window.
    var pending = lastBoxClick && lastBoxClick.node.getAttribute('data-child') && 700 > Date.now() - lastBoxClick.at;
    if (node.classList.contains('sv-node') && !(pending && !child)) lastBoxClick = { node: node, at: Date.now() };
    // Titled by the box's label, never its id: a box labelled `agent` opened
    // a card reading `group:agent/routers` (review #21 N2). The id stays in
    // the chip below.
    var shown = node.classList.contains('sv-node') ? labelOf(scopeOf(node), node.getAttribute('data-id'))
      : node.classList.contains('sv-boundary') ? node.getAttribute('data-id')
      : node.classList.contains('card-item') ? node.textContent.replace(/SRC \\d+$/, '').trim()
      : (labelOf(scopeOf(node), node.getAttribute('data-src') || '') + ' → ' + labelOf(scopeOf(node), node.getAttribute('data-dst') || ''));
    show(shown, refs, node);
  });

  // --- Explorer: search, kind toggles, clickable neighbours, a path tool.
  // Everything below toggles classes on the SVG the reader already sees;
  // the path is found over the drawn routes of one scope, so it is exactly
  // the path the picture shows, never a claim the picture does not make.
  function clearPins() {
    pins = [];
    document.querySelectorAll('svg.is-pinned').forEach(function (s) {
      s.classList.remove('is-pinned');
      s.querySelectorAll('.is-path').forEach(function (x) { x.classList.remove('is-path'); });
    });
    document.querySelectorAll('.explore .path').forEach(function (p) { p.textContent = ''; });
  }
  function pinPath(node) {
    var root = scopeOf(node);
    // A new selection starts a new path: without this the SVG showed the
    // union of every path tried under a caption naming only the last.
    if (pins.length === 0 || scopeOf(pins[0]) !== root) clearPins();
    pins.push(node);
    node.classList.add('is-path');
    node.closest('svg').classList.add('is-pinned');
    var out = node.closest('.tab').querySelector('.explore .path');
    if (pins.length === 1) { if (out) out.textContent = 'shift-click a second box'; return; }
    var a = pins[0].getAttribute('data-id'), b = pins[1].getAttribute('data-id');
    var adj = {}, byPair = {};
    root.querySelectorAll('.sv-route').forEach(function (r) {
      var s = r.getAttribute('data-src'), d = r.getAttribute('data-dst');
      (adj[s] = adj[s] || []).push(d);
      (adj[d] = adj[d] || []).push(s);
      byPair[s + ' ' + d] = r;
      byPair[d + ' ' + s] = r;
    });
    var prev = {}; prev[a] = null;
    var queue = [a]; var found = a === b;
    while (queue.length && !found) {
      var cur = queue.shift();
      (adj[cur] || []).slice().sort().forEach(function (n) {
        if (!(n in prev)) { prev[n] = cur; queue.push(n); if (n === b) found = true; }
      });
    }
    if (!found) {
      if (out) out.textContent = 'no path between ' + labelOf(root, a) + ' and ' + labelOf(root, b) + ' in this view';
      pins = [];
      return;
    }
    var chain = [b];
    while (prev[chain[0]] !== null) chain.unshift(prev[chain[0]]);
    var onPath = {};
    chain.forEach(function (id) { onPath[id] = 1; });
    root.querySelectorAll('.sv-node').forEach(function (nd) {
      if (onPath[nd.getAttribute('data-id')]) nd.classList.add('is-path');
    });
    // The caption follows each arrow's drawn direction: the search is
    // undirected so a path exists whenever the picture connects the two
    // boxes, but "A -> B" for an arrow drawn B -> A is a wrong claim.
    var caption = labelOf(root, chain[0]);
    for (var i = 1; chain.length > i; i++) {
      var r = byPair[chain[i - 1] + ' ' + chain[i]];
      if (r) r.classList.add('is-path');
      var forward = r && r.getAttribute('data-src') === chain[i - 1];
      caption += (forward ? ' \\u2192 ' : ' \\u2190 ') + labelOf(root, chain[i]);
    }
    if (out) out.textContent = 'path (' + (chain.length - 1) + ' hops, undirected): ' + caption;
    pins = [];
  }

  document.addEventListener('input', function (ev) {
    var box = ev.target.closest('.explore input');
    if (!box) return;
    var tab = box.closest('.tab');
    var q = box.value.trim().toLowerCase();
    tab.querySelectorAll('svg').forEach(function (svg) {
      svg.classList.remove('is-searching');
      svg.querySelectorAll('.is-hit').forEach(function (x) { x.classList.remove('is-hit'); });
    });
    var out = tab.querySelector('.explore .path');
    var view = tab.querySelector('.view.is-open') || tab.querySelector('.view');
    if (!q || !view) { if (out) out.textContent = ''; return; }
    var svg = view.querySelector('svg');
    var hits = 0;
    svg.querySelectorAll('.sv-node').forEach(function (nd) {
      var id = (nd.getAttribute('data-id') || '').toLowerCase();
      var lbl = nd.querySelector('.sv-box-label');
      var label = lbl ? lbl.textContent.toLowerCase() : '';
      if (id.indexOf(q) >= 0 || label.indexOf(q) >= 0) { nd.classList.add('is-hit'); hits += 1; }
    });
    svg.classList.add('is-searching');
    if (out) out.textContent = hits + (hits === 1 ? ' match' : ' matches');
  });

  document.addEventListener('click', function (ev) {
    var sw = ev.target.closest('.legend .sw-toggle');
    if (!sw) return;
    var view = sw.closest('.view');
    var svg = view && view.querySelector('svg');
    if (!svg) return;
    sw.classList.toggle('off');
    var off = sw.classList.contains('off');
    sw.setAttribute('aria-pressed', off ? 'true' : 'false');
    svg.querySelectorAll('.sv-node.sv-kind-' + sw.getAttribute('data-kind')).forEach(function (nd) {
      nd.classList.toggle('is-off', off);
    });
    var offIds = {};
    svg.querySelectorAll('.sv-node.is-off').forEach(function (nd) { offIds[nd.getAttribute('data-id')] = 1; });
    svg.querySelectorAll('.sv-route').forEach(function (r) {
      r.classList.toggle('is-off', !!(offIds[r.getAttribute('data-src')] || offIds[r.getAttribute('data-dst')]));
    });
  });

  document.addEventListener('click', function (ev) {
    var li = ev.target.closest('#panel li.link');
    if (!li || !lastScope) return;
    var target = li.getAttribute('data-target');
    // Resolve in the view the reader is looking at: after a drill the
    // passport's scope is a hidden view, and lighting a box there is a
    // rewrite of the panel for something the reader cannot see.
    var open = document.querySelector('.view.is-open');
    var scope = (open && open.contains(lastScope)) ? lastScope : (open || lastScope);
    var hit = null;
    scope.querySelectorAll('.sv-node').forEach(function (nd) {
      if (!hit && nd.getAttribute('data-id') === target) hit = nd;
    });
    if (!hit) return;
    hit.scrollIntoView({ block: 'center', inline: 'center' });
    show(target, (hit.getAttribute('data-evidence') || '').split('\\n').filter(Boolean), hit);
  });

  // Guided views: a chapter focuses its anchor box in the root view and
  // lights the boxes the drawn arrows connect it to. Play steps through.
  var playing = null;
  function openChapter(li) {
    var strip = li.closest('.guided');
    var tab = li.closest('.tab');
    var root = tab.querySelector('.view[data-view="' + CSS.escape(strip.getAttribute('data-root')) + '"]');
    if (!root) return;
    tab.querySelectorAll('.view').forEach(function (v) { v.classList.remove('is-open'); });
    root.classList.add('is-open');
    var anchorId = li.getAttribute('data-anchor');
    var anchor = null;
    root.querySelectorAll('.sv-node').forEach(function (nd) { if (!anchor && nd.getAttribute('data-id') === anchorId) anchor = nd; });
    if (!anchor) return;
    var ids = (li.getAttribute('data-focus') || '').split('\\n').filter(Boolean);
    clearPins();
    passport(anchor);
    title.textContent = labelOf(root, anchorId);
    current = (anchor.getAttribute('data-evidence') || '').split('\\n').filter(Boolean);
    render();
    panel.classList.add('is-open');
    light(scopeOf(anchor), anchor, ids);
    strip.querySelectorAll('.chapter').forEach(function (c) { c.classList.remove('is-active'); });
    li.classList.add('is-active');
    var all = strip.querySelectorAll('.chapter');
    var idx = Array.prototype.indexOf.call(all, li);
    var progress = strip.querySelector('.progress');
    if (progress) progress.textContent = (idx + 1) + ' / ' + all.length;
    anchor.scrollIntoView({ block: 'center', inline: 'center' });
  }
  document.addEventListener('click', function (ev) {
    var li = ev.target.closest('.chapter');
    if (li) { if (playing) { clearInterval(playing); playing = null; } openChapter(li); return; }
    var play = ev.target.closest('.guided .play');
    if (!play) return;
    var strip = play.closest('.guided');
    var chapters = Array.prototype.slice.call(strip.querySelectorAll('.chapter'));
    if (playing) { clearInterval(playing); playing = null; play.textContent = '▶ Play story'; return; }
    var i = 0;
    play.textContent = '■ Stop';
    openChapter(chapters[0]);
    playing = setInterval(function () {
      i += 1;
      if (i >= chapters.length) { clearInterval(playing); playing = null; play.textContent = '▶ Play story'; return; }
      openChapter(chapters[i]);
    }, 2500);
  });

  // Export: the open view's SVG as a file, or rasterised at 2x. The page's
  // own stylesheet travels with it (a standalone SVG's :root is the svg
  // element, so the theme variables resolve), and the current theme goes
  // along as the data attribute the stylesheet keys on. Serialised by the
  // browser, never assembled from strings.
  function exportView(tab, format) {
    var view = tab.querySelector('.view.is-open') || tab.querySelector('.view');
    var svg = view && view.querySelector('svg');
    if (!svg) return;
    var copy = svg.cloneNode(true);
    copy.setAttribute('xmlns', 'http://www.w3.org/2000/svg');
    copy.setAttribute('data-theme', document.documentElement.getAttribute('data-theme') || 'dark');
    // The click that reaches this button has cleared focus, hover and path,
    // but a search or a muted kind survives a click and would ride along:
    // the export is of the diagram, never of the reader's session.
    copy.querySelectorAll('.is-path, .is-focus, .is-hit, .is-off').forEach(function (x) {
      x.classList.remove('is-path'); x.classList.remove('is-focus'); x.classList.remove('is-hit'); x.classList.remove('is-off');
    });
    copy.classList.remove('is-focused', 'is-hovering', 'is-pinned', 'is-searching');
    var style = document.createElementNS('http://www.w3.org/2000/svg', 'style');
    var sheet = document.querySelector('style');
    style.textContent = sheet ? sheet.textContent : '';
    copy.insertBefore(style, copy.firstChild);
    var bg = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
    bg.setAttribute('width', '100%'); bg.setAttribute('height', '100%');
    bg.setAttribute('fill', getComputedStyle(document.documentElement).getPropertyValue('--canvas') || '#0f172a');
    copy.insertBefore(bg, style.nextSibling);
    var name = (view.getAttribute('data-view') || 'view').replace(/[^A-Za-z0-9._-]+/g, '_');
    var xml = new XMLSerializer().serializeToString(copy);
    if (format === 'svg') {
      download(new Blob([xml], { type: 'image/svg+xml;charset=utf-8' }), name + '.svg');
      return;
    }
    var w = parseInt(svg.getAttribute('width'), 10) || svg.viewBox.baseVal.width;
    var h = parseInt(svg.getAttribute('height'), 10) || svg.viewBox.baseVal.height;
    var img = new Image();
    var url = URL.createObjectURL(new Blob([xml], { type: 'image/svg+xml;charset=utf-8' }));
    img.onload = function () {
      var c = document.createElement('canvas');
      c.width = w * 2; c.height = h * 2;
      var ctx = c.getContext('2d');
      ctx.scale(2, 2);
      ctx.drawImage(img, 0, 0);
      URL.revokeObjectURL(url);
      c.toBlob(function (blob) { if (blob) download(blob, name + '.png'); }, 'image/png');
    };
    img.src = url;
  }
  function download(blob, filename) {
    var a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(function () { URL.revokeObjectURL(a.href); }, 1000);
  }
  document.addEventListener('click', function (ev) {
    var b = ev.target.closest('.explore [data-export]');
    if (!b) return;
    exportView(b.closest('.tab'), b.getAttribute('data-export'));
  });

  document.addEventListener('click', function (ev) {
    var s = ev.target.closest('#panel-also span');
    if (!s) return;
    var tab = document.getElementById(s.getAttribute('data-tab'));
    if (!tab) return;
    location.hash = '#' + tab.id;
    var target = null;
    tab.querySelectorAll('.view').forEach(function (v) {
      var open = v.getAttribute('data-view') === s.getAttribute('data-view');
      v.classList.toggle('is-open', open);
      if (open) target = v;
    });
    if (!target) return;
    var wanted = s.getAttribute('data-target');
    var hit = null;
    target.querySelectorAll('.sv-node').forEach(function (nd) {
      if (!hit && nd.getAttribute('data-id') === wanted) hit = nd;
    });
    if (!hit) return;
    hit.scrollIntoView({ block: 'center', inline: 'center' });
    show(wanted, (hit.getAttribute('data-evidence') || '').split('\\n').filter(Boolean), hit);
  });

  function openView(node, child) {
    var tab = node.closest('.tab');
    var view = node.closest('.view');
    // A drill changes the picture: the passport and focus of the box that
    // was double-clicked belong to the view being left.
    panel.classList.remove('is-open');
    clearFocus();
    clearPins();
    // The expansion to open belongs to the view the box is IN: the plain
    // view's own id, the host of an expanded view for a sibling box, or the
    // embedded child for a box inside the container. Keyed on the child
    // alone, two stories sharing a module opened each other's copy.
    var host = view.dataset.view;
    if (view.dataset.host) {
      host = node.closest('[data-scope="child"]') ? view.dataset.plain : view.dataset.host;
    }
    var nodeId = node.getAttribute('data-id') || '';
    // Prefer the pre-rendered in-place expansion; a child too large to embed
    // has no variant and opens as its own view instead.
    var target = tab.querySelector('[data-view="' + CSS.escape(host + '//' + nodeId + '//expanded') + '"]')
      || tab.querySelector('[data-view="' + CSS.escape(child) + '"]');
    if (!target) return;
    // A plain child view records one parent, but a shared child is reached
    // from several: its crumb goes back to where the reader came from.
    var crumb = target.querySelector('[data-up]');
    if (crumb && !target.dataset.host) { crumb.dataset.up = view.dataset.view; }
    tab.querySelectorAll('.view').forEach(function (v) {
      v.classList.remove('is-open');
    });
    target.classList.add('is-open');
    target.scrollIntoView({ block: 'start' });
    // Focus follows the drill (review #24 N6: after Shift+Enter it sat on
    // the body and a keyboard reader tabbed from the page top).
    var crumbLink = target.querySelector('[data-up]');
    if (crumbLink && crumbLink.focus) crumbLink.focus();
  }

  document.addEventListener('click', function (ev) {
    var up = ev.target.closest('[data-up]');
    if (!up) return;
    ev.preventDefault();
    var tab = up.closest('.tab');
    var target = tab.querySelector('[data-view="' + CSS.escape(up.dataset.up) + '"]');
    if (!target) return;
    tab.querySelectorAll('.view').forEach(function (v) {
      v.classList.remove('is-open');
    });
    target.classList.add('is-open');
  });
})();
"""
    )


def _view(
    ds: DiagramSet,
    lo: LaidOutDiagram,
    spec_id: str,
    style: Style,
    cards: tuple[Card, ...] = (),
) -> Markup:
    canvas = lo.canvases[spec_id]
    spec = ds.specs[spec_id]
    crumb: Markup = raw("")
    if canvas.parent:
        crumb = tag(
            "p",
            join(
                (
                    raw("&#8592; "),
                    tag(
                        "a",
                        esc(ds.specs[canvas.parent].title),
                        href="#",
                        data_up=canvas.parent,
                    ),
                )
            ),
            class_="crumbs",
        )
    return tag(
        "section",
        join(
            (
                crumb,
                tag("h2", esc(spec.title)),
                tag("p", esc(spec.subtitle), class_="meta"),
                tag("div", canvas_svg(canvas, style, frozenset(lo.withheld)), class_="scroller"),
                _legend(canvas),
                render_cards(cards) if cards and spec_id == ds.root else raw(""),
            )
        ),
        class_="view" + (" is-open" if spec_id == ds.root else ""),
        data_view=spec_id,
    )


def _legend(canvas: object, expanded: bool = False) -> Markup:
    """Kind swatches with real counts, plus the product's one-line promise.

    The counts are computed from the canvas being drawn, never typed in, so
    the legend cannot claim kinds the diagram does not contain. Waypoints are
    bends in lines and are not counted as anything.
    """
    from collections import Counter

    from svarupa.layout.geometry import Canvas

    assert isinstance(canvas, Canvas)
    counts = Counter(b.kind for b in canvas.boxes if b.id not in canvas.waypoints)
    if not counts:
        return raw("")
    swatches = join(
        (
            tag(
                "span",
                join((raw('<span class="sw"></span>'), esc(f"{kind} {n}"))),
                class_=f"sv-kind-{_kind_slug(kind)} sw-toggle",
                data_kind=_kind_slug(kind),
                tabindex="0",
                role="button",
                aria_pressed="false",
                title=_KIND_HELP.get(kind, "") + "click to mute this kind",
            )
            for kind, n in sorted(counts.items())
        ),
        sep=" ",
    )
    # What the arrows are, said once (the structural ones carry no word).
    variants = {r.variant for r in canvas.routes}
    arrow_words: list[str] = []
    if "default" in variants:
        arrow_words.append("solid arrows are imports (count on hover)")
    if "dashed" in variants:
        dashed = [r for r in canvas.routes if r.variant == "dashed" and r.label]
        # Said only when true of this canvas: a verb the settle could not
        # place lives in the passport (review #23 F4).
        # An expansion also draws the parent's arrows, ghosted and without
        # their verbs (review #24 N4), so its legend hedges.
        if not expanded and all(r.label_at is not None for r in dashed):
            arrow_words.append("dashed arrows carry their verb")
        else:
            arrow_words.append("dashed arrows carry their verb (some only in the passport)")
    arrows = (
        tag("span", esc("; ".join(arrow_words)), class_="arrows") if arrow_words else raw("")
    )
    return tag(
        "div",
        join(
            (
                swatches,
                arrows,
                tag(
                    "span",
                    esc("every box and arrow cites a source line; click one"),
                    class_="promise",
                ),
            )
        ),
        class_="legend",
    )


# One line per kind for the legend's tooltip, so "group 7" is not internal
# vocabulary (review #20 C2). Archify's component types, in our terms.
_KIND_HELP: dict[str, str] = {
    "group": "modules grouped for reading, by community or by directory; a group is not an identity. ",
    "module": "a directory of code with no evidenced role. ",
    "backend": "code that serves routes, runs tasks or is a declared entrypoint. ",
    "frontend": "code that imports a UI framework. ",
    "security": "code that imports an auth library. ",
    "database": "a store the code imports a driver for, or composes. ",
    "messagebus": "a queue or bus the code imports a client for, or composes. ",
    "cloud": "an external API the code imports an SDK for. ",
    "service": "a composed service that builds no code in this repository. ",
    "endpoint": "declared routes, grouped by the module that declares them. ",
    "table": "a table from SQL DDL. ",
}


def _kind_slug(kind: str) -> str:
    return "".join(c if c.isalnum() or c == "-" else "-" for c in kind.lower()) or "none"


def _withheld_note(lo: LaidOutDiagram) -> Markup:
    """Name what was not drawn, in the tab where a reader expects to see it.

    An unexplained gap reads as a bug. An explained one is information, and
    this is the same rule the report follows: a diagram that could not be drawn
    says so where it would have been.
    """
    if not lo.withheld:
        return raw("")
    items = join(
        (tag("li", esc(f"{sid}")) for sid in sorted(lo.withheld)),
    )
    return tag(
        "div",
        join(
            (
                tag(
                    "p",
                    esc(
                        f"{len(lo.withheld)} view(s) failed geometry validation and "
                        "were not drawn. See REPORT.md for the reason."
                    ),
                ),
                tag("ul", items),
            )
        ),
        class_="withheld",
    )


def _tab(
    ds: DiagramSet, lo: LaidOutDiagram, style: Style, cards: tuple[Card, ...] = ()
) -> Markup:
    ordered = [ds.root, *sorted(s for s in lo.canvases if s != ds.root)]
    guided = (
        render_guided(chapters_for(ds.root_spec), ds.root)
        if ds.root in lo.canvases
        else raw("")
    )
    return tag(
        "section",
        join(
            (
                _explore_bar(),
                guided,
                join(_view(ds, lo, sid, style, cards) for sid in ordered if sid in lo.canvases),
                join(_expanded_views(ds, lo, style)),
                _withheld_note(lo),
            )
        ),
        class_="tab",
        id=f"d-{ds.kind.value}",
    )


def _explore_bar() -> Markup:
    """Search, and the hint for the two click gestures. Static markup: the
    only text is ours, so `raw` is right here as it is for the base input."""
    return tag(
        "div",
        join(
            (
                raw('<input class="search" placeholder="find a box by id or label">'),
                tag(
                    "span",
                    esc(
                        "click a box for its passport; double-click it, or its \u203a, to open "
                        "it in place (keyboard: Enter, Shift+Enter); shift-click two boxes for a "
                        "path; a legend swatch mutes a kind"
                    ),
                    class_="hint",
                ),
                tag("span", raw(""), class_="path", aria_live="polite"),
                raw(
                    '<span class="export"><button type="button" data-export="svg">Export SVG</button>'
                    '<button type="button" data-export="png">Export PNG</button></span>'
                ),
            )
        ),
        class_="explore",
    )


def _expanded_views(ds: DiagramSet, lo: LaidOutDiagram, style: Style) -> list[Markup]:
    """One pre-rendered expansion per drillable box.

    Clicking a drillable box opens it **in place**: the same view with that box
    grown into a container holding its child diagram, siblings still around
    it. Pre-rendered rather than laid out in the browser, so each state is a
    real validated canvas and the artifact stays deterministic; linear in the
    number of drillable boxes, not exponential in paths.

    A child too large to embed gets no variant, and the click falls back to
    opening the child as its own view. Different, but stated by behaviour a
    reader can see, never a silent scale-down.

    A host bigger than MAX_EXPANSION_HOST_BOXES gets no variants either: each
    expansion re-renders the whole host, so cost grows with host size times
    drillable count (the 161-box, 176-drillable acceptance-repo host paid
    90MB), and past a couple of dozen boxes the "surrounding context" the
    expansion exists to preserve is not readable anyway. The drill falls back
    to the child view, and the passport's button says "Open" rather than
    "Open in place" so the affordance names what it does.
    """
    from svarupa.layout import ENGINE_FOR_KIND
    from svarupa.layout.compose import expand

    engine = ENGINE_FOR_KIND[ds.kind]
    out: list[Markup] = []
    for sid in sorted(lo.canvases):
        canvas = lo.canvases[sid]
        if sum(1 for b in canvas.boxes if b.id not in canvas.waypoints) > MAX_EXPANSION_HOST_BOXES:
            continue
        for node in ds.specs[sid].nodes:
            child_id = node.child_spec
            if child_id is None or child_id not in lo.canvases:
                continue
            exp = expand(ds.specs[sid], node.id, lo.canvases[child_id], style, engine)
            if exp is None:
                continue
            crumb = tag(
                "p",
                join(
                    (
                        raw("&#8592; "),
                        tag("a", esc(ds.specs[sid].title), href="#", data_up=sid),
                    )
                ),
                class_="crumbs",
            )
            out.append(
                tag(
                    "section",
                    join(
                        (
                            crumb,
                            tag("h2", esc(lo.canvases[child_id].title)),
                            tag("p", esc(lo.canvases[child_id].subtitle), class_="meta"),
                            tag(
                                "div",
                                expanded_svg(exp, style, frozenset(lo.withheld)),
                                class_="scroller",
                            ),
                            _legend(lo.canvases[child_id], expanded=True),
                        )
                    ),
                    class_="view",
                    data_view=exp.id,
                    data_plain=child_id,
                    data_host=sid,
                )
            )
    return out


def _unavailable(notes: tuple[str, ...]) -> Markup:
    """A tab for what has no diagram, rather than a tab that is empty.

    An empty diagram fabricated to fill a tab is a wrong claim about a
    codebase. A named absence with its reason is a true one.
    """
    if not notes:
        return raw("")
    return tag(
        "section",
        join(
            (
                tag("h2", esc("Not drawn")),
                tag(
                    "p",
                    esc(
                        "These were not produced. No empty diagram is ever "
                        "fabricated to fill a tab."
                    ),
                    class_="meta",
                ),
                tag("ul", join(tag("li", esc(n)) for n in notes)),
            )
        ),
        class_="tab",
        id="d-unavailable",
    )


def render_viewer(
    root: str,
    produced: dict[DiagramKind, DiagramSet],
    laid_out: dict[DiagramKind, LaidOutDiagram],
    notes: tuple[str, ...],
    style: Style,
    version: str,
    graph: Graph | None = None,
) -> str:
    """The whole document, as one self-contained string."""
    kinds = sorted(produced, key=lambda k: k.value)
    cards = cards_for(graph) if graph is not None else ()
    nav = join(
        [tag("a", esc(k.value), href=f"#d-{k.value}") for k in kinds]
        + (
            [
                tag(
                    "a",
                    esc("not drawn"),
                    href="#d-unavailable",
                    class_="muted",
                    title="the diagram types the evidence could not produce, and why",
                )
            ]
            if notes
            else []
        )
    )
    header = tag(
        "header",
        join(
            (
                tag("h1", esc("svarupa")),
                tag(
                    "span",
                    join((tag("small", esc("repo")), esc(root))),
                    class_="repo",
                    title="the repository this artifact describes",
                ),
                tag("nav", nav),
                tag(
                    "div",
                    join(
                        (
                            tag("label", esc("source base"), for_="base"),
                            raw(
                                '<input id="base" placeholder="../ or '
                                'https://github.com/org/repo/blob/main">'
                            ),
                            raw(
                                '<button id="theme" type="button" '
                                'title="switch to the light theme">☾ dark</button>'
                            ),
                        )
                    ),
                    class_="controls",
                ),
            )
        ),
    )
    body = join(
        (
            header,
            join(_tab(produced[k], laid_out[k], style, cards) for k in kinds),
            _unavailable(notes),
            tag(
                "aside",
                join(
                    (
                        raw(
                            '<button class="close" id="panel-close" type="button" '
                            'aria-label="close">\u00d7</button>'
                        ),
                        tag("p", esc("Passport"), id="panel-eyebrow", class_="eyebrow"),
                        tag("h2", raw(""), id="panel-title"),
                        tag("p", raw(""), id="panel-sub", class_="sub"),
                        tag("div", raw(""), id="panel-meta", class_="chips"),
                        tag("div", raw(""), id="panel-also", class_="chips also"),
                        tag("p", raw(""), id="panel-summary", class_="summary"),
                        raw(
                            '<button id="panel-open" class="open" type="button" hidden>'
                            "Open in place \u203a</button>"
                        ),
                        raw(
                            '<button id="panel-link" class="open" type="button" hidden>'
                            "copy link</button>"
                        ),
                        tag("h3", esc("Reach in this view"), class_="section"),
                        tag(
                            "div",
                            raw(
                                '<button id="reach-up" type="button" title="everything with an arrow into this box, transitively">'
                                "<span>Used by</span><strong>0</strong></button>"
                                '<button id="reach-down" type="button" title="everything this box has an arrow to, transitively">'
                                "<span>Uses</span><strong>0</strong></button>"
                            ),
                            class_="reach",
                        ),
                        raw('<hr class="rule">'),
                        tag("h3", esc("Outgoing"), id="panel-out-h", class_="section"),
                        tag("ul", raw(""), id="panel-out"),
                        tag("h3", esc("Incoming"), id="panel-in-h", class_="section"),
                        tag("ul", raw(""), id="panel-in"),
                        tag("h3", esc("Verified source"), class_="section"),
                        tag("ul", raw(""), id="panel-list"),
                    )
                ),
                id="panel",
                role="region",
                aria_label="passport",
            ),
            tag(
                "noscript",
                tag(
                    "p",
                    esc(
                        "JavaScript is off. Every diagram, label and source "
                        "location is still here: hover a box to see its full "
                        "name and citations. Drill-down and the evidence panel "
                        "need JavaScript."
                    ),
                    class_="hint",
                ),
            ),
            tag("script", _js()),
        )
    )
    css = str(_css()).replace("MONO_PLACEHOLDER", FONT_STACK)
    return (
        "<!doctype html>\n"
        '<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<link rel="icon" href="data:,">'
        f"<title>{esc(f'svarupa {version}: {root}')}</title>"
        f"<style>{css}</style>"
        f"</head><body>{body}</body></html>\n"
    )


def evidence_summary(ds: DiagramSet, lo: LaidOutDiagram) -> list[str]:
    """Every citation the viewer can reach, for tests to compare against."""
    out: list[str] = []
    for sid in sorted(lo.canvases):
        canvas = lo.canvases[sid]
        out.extend(evidence_ref(b.evidence) for b in canvas.boxes)
        out.extend(evidence_ref(r.evidence) for r in canvas.routes)
    _ = ds
    return out
