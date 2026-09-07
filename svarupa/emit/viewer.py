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

from svarupa.derive.base import DiagramKind, DiagramSet
from svarupa.emit.markup import Markup, esc, join, raw, tag
from svarupa.emit.svg import canvas_svg, evidence_ref, expanded_svg
from svarupa.layout import LaidOutDiagram
from svarupa.layout.geometry import Style
from svarupa.layout.text import FONT_STACK

__all__ = ["render_viewer"]


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

header {
  position: sticky; top: 0; z-index: 5;
  background: color-mix(in srgb, var(--surface) 94%, transparent);
  backdrop-filter: blur(8px);
  border-bottom: 1px solid var(--line); padding: 12px 20px;
  display: flex; gap: 20px; align-items: center; flex-wrap: wrap;
}
h1 { font-size: 15px; margin: 0; font-weight: 700; letter-spacing: -0.01em; }
h1 small {
  color: var(--dim); font-weight: 400; margin-left: 10px;
  font-family: var(--mono); font-size: 12px;
}
nav { display: flex; gap: 6px; flex-wrap: wrap; }
nav a {
  color: var(--dim); text-decoration: none; padding: 5px 12px;
  border-radius: 7px; font-size: 13px; transition: background .12s, color .12s;
}
nav a:hover { color: var(--ink); background: var(--raised); }
.controls { margin-left: auto; display: flex; gap: 8px; align-items: center; }
.controls label { color: var(--dim); font-size: 12px; }
.controls input {
  background: var(--bg); color: var(--ink); border: 1px solid var(--line);
  border-radius: 7px; padding: 6px 10px; font-size: 12px;
  font-family: var(--mono); width: 22em;
}
.controls input:focus { outline: none; border-color: var(--accent); }
#theme {
  background: var(--surface); color: var(--dim); border: 1px solid var(--line);
  border-radius: 7px; padding: 6px 12px; font: inherit; font-size: 12px;
  cursor: pointer;
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
.view { display: none; }
.view.is-open { display: block; animation: sv-enter .18s ease; }
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
/* Explorer controls (design section 4): search, kind toggles, a path tool.
   All of it is class toggling on the SVG the reader already sees; nothing
   here lays anything out or builds markup from repository text. */
.legend .sw-toggle { cursor: pointer; user-select: none; }
.legend .sw-toggle.off { opacity: .35; text-decoration: line-through; }
.explore { display: flex; gap: 10px; align-items: center; margin: 0 0 10px; font-size: 12px; color: var(--faint); flex-wrap: wrap; }
.explore input { font: inherit; background: var(--raised); color: var(--ink); border: 1px solid var(--line); border-radius: 6px; padding: 4px 8px; width: 280px; }
.explore .path { color: var(--ink); }
svg.is-searching .sv-node:not(.is-hit), svg.is-searching .sv-route { opacity: .15; }
svg .sv-node.is-off, svg .sv-route.is-off { opacity: .1; }
svg.is-pinned .sv-node:not(.is-path), svg.is-pinned .sv-route:not(.is-path) { opacity: .15; }
aside .conn.link { cursor: pointer; }
/* Shift-click is the path gesture, and shift-click also selects text. */
svg text { user-select: none; }
aside .conn.link:hover { text-decoration: underline; }

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
aside .chips code { font-size: 10px; color: var(--faint); }
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
  // attribute. The button names the theme you would switch TO.
  function applyTheme(name) {
    if (name === 'light') {
      document.documentElement.setAttribute('data-theme', 'light');
      themeBtn.textContent = 'dark';
    } else {
      document.documentElement.removeAttribute('data-theme');
      themeBtn.textContent = 'light';
    }
  }
  applyTheme(localStorage.getItem(THEME) || 'dark');
  themeBtn.addEventListener('click', function () {
    var next = document.documentElement.hasAttribute('data-theme') ? 'dark' : 'light';
    localStorage.setItem(THEME, next);
    applyTheme(next);
  });

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
    var line = parts.pop();
    var file = parts.join(':');
    var prefix = base.value.replace(/\\/+$/, '');
    var href = safeHref((prefix ? prefix + '/' : '') + file + '#L' + line);
    if (href === null) {
      // Shown, not hidden. The citation is still the evidence; what is
      // withheld is only the ability to click it.
      var span = document.createElement('span');
      span.textContent = ref + ' (no safe link for this path)';
      return span;
    }
    var a = document.createElement('a');
    a.textContent = ref;
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
  var eyebrowEl = document.getElementById('panel-eyebrow');
  var subEl = document.getElementById('panel-sub');
  var metaEl = document.getElementById('panel-meta');
  var sumEl = document.getElementById('panel-summary');
  var outEl = document.getElementById('panel-out');
  var outH = document.getElementById('panel-out-h');
  var inEl = document.getElementById('panel-in');
  var inH = document.getElementById('panel-in-h');
  var upBtn = document.getElementById('reach-up');
  var downBtn = document.getElementById('reach-down');

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
  function connItem(list, dir, said, other) {
    var li = document.createElement('li');
    li.className = 'conn link';
    li.setAttribute('data-target', other);
    var d = document.createElement('span'); d.className = 'dir'; d.textContent = dir;
    var n = document.createElement('span'); n.className = 'name'; n.textContent = other;
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
    var code = document.createElement('code');
    code.textContent = id;
    metaEl.appendChild(code);
    outEl.textContent = '';
    inEl.textContent = '';
    var nOut = 0, nIn = 0;
    routesOf(root).forEach(function (r) {
      var s = r.getAttribute('data-src'), d = r.getAttribute('data-dst');
      if (s !== id && d !== id) return;
      // The note carries the count ("12 imports"); the label is the verb.
      var said = r.getAttribute('data-note') || r.getAttribute('data-label') || 'imports';
      if (s === id) { nOut += 1; connItem(outEl, 'OUT →', said, d); }
      else { nIn += 1; connItem(inEl, '← IN', said, s); }
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
  }

  // Focus: the clicked box glows, a chosen set stays lit, the rest recedes.
  function clearFocus() {
    document.querySelectorAll('svg.is-focused').forEach(function (s) {
      s.classList.remove('is-focused');
      s.querySelectorAll('.is-path, .is-focus').forEach(function (x) {
        x.classList.remove('is-path'); x.classList.remove('is-focus');
      });
    });
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
    title.textContent = name;
    if (node && (node.classList.contains('sv-node') || node.classList.contains('sv-boundary'))) {
      passport(node);
      if (node.classList.contains('sv-node')) focus(node); else clearFocus();
    } else {
      eyebrowEl.textContent = 'Connection';
      subEl.textContent = node ? (node.getAttribute('data-note') || node.getAttribute('data-label') || '') : '';
      metaEl.textContent = '';
      outEl.textContent = ''; inEl.textContent = '';
      outH.textContent = 'Outgoing'; inH.textContent = 'Incoming';
      sumEl.textContent = node ? (node.getAttribute('data-src') + ' → ' + node.getAttribute('data-dst')) : '';
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
    if (child && ev.detail === 2) { openView(node, child); return; }
    show(node.getAttribute('data-id') || node.getAttribute('data-src') || '', refs, node);
  });

  // --- Explorer: search, kind toggles, clickable neighbours, a path tool.
  // Everything below toggles classes on the SVG the reader already sees;
  // the path is found over the drawn routes of one scope, so it is exactly
  // the path the picture shows, never a claim the picture does not make.
  var pins = [];
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
      if (out) out.textContent = 'no path between ' + a + ' and ' + b + ' in this view';
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
    var caption = chain[0];
    for (var i = 1; chain.length > i; i++) {
      var r = byPair[chain[i - 1] + ' ' + chain[i]];
      if (r) r.classList.add('is-path');
      var forward = r && r.getAttribute('data-src') === chain[i - 1];
      caption += (forward ? ' \\u2192 ' : ' \\u2190 ') + chain[i];
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


def _view(ds: DiagramSet, lo: LaidOutDiagram, spec_id: str, style: Style) -> Markup:
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
                tag("div", canvas_svg(canvas, style), class_="scroller"),
                _legend(canvas),
            )
        ),
        class_="view" + (" is-open" if spec_id == ds.root else ""),
        data_view=spec_id,
    )


def _legend(canvas: object) -> Markup:
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
                title="click to mute this kind",
            )
            for kind, n in sorted(counts.items())
        ),
        sep=" ",
    )
    return tag(
        "div",
        join(
            (
                swatches,
                tag(
                    "span",
                    esc("every box and arrow cites a source line; click one"),
                    class_="promise",
                ),
            )
        ),
        class_="legend",
    )


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


def _tab(ds: DiagramSet, lo: LaidOutDiagram, style: Style) -> Markup:
    ordered = [ds.root, *sorted(s for s in lo.canvases if s != ds.root)]
    return tag(
        "section",
        join(
            (
                _explore_bar(),
                join(_view(ds, lo, sid, style) for sid in ordered if sid in lo.canvases),
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
                        "shift-click two boxes for a path; click a legend swatch to mute a kind"
                    ),
                    class_="hint",
                ),
                tag("span", raw(""), class_="path", aria_live="polite"),
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
    """
    from svarupa.layout import ENGINE_FOR_KIND
    from svarupa.layout.compose import expand

    engine = ENGINE_FOR_KIND[ds.kind]
    out: list[Markup] = []
    for sid in sorted(lo.canvases):
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
                            tag("div", expanded_svg(exp, style), class_="scroller"),
                            _legend(lo.canvases[child_id]),
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
) -> str:
    """The whole document, as one self-contained string."""
    kinds = sorted(produced, key=lambda k: k.value)
    nav = join(
        [tag("a", esc(k.value), href=f"#d-{k.value}") for k in kinds]
        + ([tag("a", esc("not drawn"), href="#d-unavailable")] if notes else [])
    )
    header = tag(
        "header",
        join(
            (
                tag(
                    "h1",
                    join((esc("svarupa"), raw(" "), tag("small", esc(root)))),
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
                            raw('<button id="theme" type="button">dark</button>'),
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
            join(_tab(produced[k], laid_out[k], style) for k in kinds),
            _unavailable(notes),
            tag(
                "aside",
                join(
                    (
                        raw('<button class="close" id="panel-close" type="button" '
                            'aria-label="close">\u00d7</button>'),
                        tag("p", esc("Passport"), id="panel-eyebrow", class_="eyebrow"),
                        tag("h2", raw(""), id="panel-title"),
                        tag("p", raw(""), id="panel-sub", class_="sub"),
                        tag("div", raw(""), id="panel-meta", class_="chips"),
                        tag("p", raw(""), id="panel-summary", class_="summary"),
                        tag("h3", esc("Reach in this view"), class_="section"),
                        tag(
                            "div",
                            raw(
                                '<button id="reach-up" type="button"><span>Upstream</span>'
                                "<strong>0</strong></button>"
                                '<button id="reach-down" type="button"><span>Downstream</span>'
                                "<strong>0</strong></button>"
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
