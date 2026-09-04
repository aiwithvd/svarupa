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
from svarupa.emit.svg import canvas_svg, evidence_ref
from svarupa.layout import LaidOutDiagram
from svarupa.layout.geometry import Style
from svarupa.layout.text import FONT_STACK

__all__ = ["render_viewer"]


def _css() -> Markup:
    """The visual system.

    Written after looking at a real 36-module repository and finding the output
    unreadable. Three things were wrong and all three were invisible to the
    geometry validator, which checks boxes against boxes and nothing else:
    every edge stamped its count in the same band, every long edge ran through
    one shared rail, and everything was set in the monospace face the width
    model needs, which makes a diagram look like a terminal.

    Monospace is now used where it is true (paths, code, citations) and a UI
    face everywhere else. The width model still measures in monospace and the
    SVG clamps text to the measured width, so the estimate cannot overflow a
    box whatever the browser resolves.
    """
    return raw(
        """
:root {
  --bg: #0d1017; --surface: #151a23; --raised: #1c2230; --line: #2a3242;
  --ink: #e8ecf4; --dim: #93a0b8; --faint: #5d6b85;
  --accent: #7aa2f7; --accent-soft: #7aa2f733;
  --group: #9d7cd8; --group-soft: #9d7cd826;
  --warn: #e0af68; --edge: #3d4661;
  --ui: -apple-system, BlinkMacSystemFont, 'Segoe UI', Inter, Roboto, sans-serif;
  --mono: MONO_PLACEHOLDER;
  --r: 10px;
}
* { box-sizing: border-box; }
html { color-scheme: dark; }
body {
  margin: 0; background: var(--bg); color: var(--ink);
  font-family: var(--ui); font-size: 14px; line-height: 1.5;
  -webkit-font-smoothing: antialiased;
}
code, .mono { font-family: var(--mono); }

header {
  position: sticky; top: 0; z-index: 5;
  background: color-mix(in srgb, var(--surface) 92%, transparent);
  backdrop-filter: blur(8px);
  border-bottom: 1px solid var(--line); padding: 12px 20px;
  display: flex; gap: 20px; align-items: center; flex-wrap: wrap;
}
h1 { font-size: 15px; margin: 0; font-weight: 650; letter-spacing: -0.01em; }
h1 small {
  color: var(--dim); font-weight: 400; margin-left: 10px;
  font-family: var(--mono); font-size: 12px;
}
nav { display: flex; gap: 6px; flex-wrap: wrap; }
nav a {
  color: var(--dim); text-decoration: none; padding: 5px 12px;
  border: 1px solid transparent; border-radius: 7px; font-size: 13px;
  transition: background .12s, color .12s;
}
nav a:hover { color: var(--ink); background: var(--raised); }
.base { margin-left: auto; display: flex; gap: 8px; align-items: center; }
.base label { color: var(--dim); font-size: 12px; }
.base input {
  background: var(--bg); color: var(--ink); border: 1px solid var(--line);
  border-radius: 7px; padding: 6px 10px; font: inherit; font-size: 12px;
  font-family: var(--mono); width: 24em;
}
.base input:focus { outline: none; border-color: var(--accent); }

.tab { display: none; padding: 24px 20px 64px; }
.tab:first-of-type { display: block; }
body:has(.tab:target) .tab:first-of-type { display: none; }
body:has(.tab:target) .tab:target { display: block; }

h2 { font-size: 20px; margin: 0 0 4px; font-weight: 650; letter-spacing: -0.02em; }
.meta { color: var(--dim); margin: 0 0 20px; font-size: 13px; }
.crumbs { margin: 0 0 12px; font-size: 13px; }
.crumbs a { color: var(--accent); text-decoration: none; }
.crumbs a:hover { text-decoration: underline; }
.view { display: none; }
.view.is-open { display: block; animation: sv-enter .18s ease; }
/* A drill-down that appears with a small rise reads as "you went somewhere",
   which is the mental model the navigation is built on. Honouring
   prefers-reduced-motion is not optional polish. */
@keyframes sv-enter {
  from { opacity: 0; transform: translateY(6px); }
  to { opacity: 1; transform: none; }
}
@media (prefers-reduced-motion: reduce) {
  .view.is-open { animation: none; }
  aside { transition: none; }
}

/* Never scaled down to fit. A layered diagram is as wide as its widest
   layer, and shrinking a 2700px canvas into a 1200px column makes every
   label unreadable, which defeats the point of drawing it. Wide diagrams
   scroll at their natural size; narrow ones sit centred rather than
   huddling in the top-left corner of an empty page. */
.scroller {
  overflow-x: auto; overflow-y: hidden; padding-bottom: 12px;
  scrollbar-color: var(--line) transparent;
}
.sv-canvas { display: block; margin: 0 auto; }
.sv-band { fill: #ffffff05; rx: 12; }
.sv-band-label {
  fill: var(--faint); font-size: 10px; font-family: var(--ui);
  letter-spacing: .08em; text-transform: uppercase;
}
.sv-box { fill: var(--surface); stroke: var(--line); stroke-width: 1.25; }
.sv-drillable .sv-box { fill: var(--raised); stroke: var(--group); }
.sv-kind-group .sv-box { fill: var(--group-soft); stroke: var(--group); }
.sv-box-label {
  fill: var(--ink); text-anchor: middle; dominant-baseline: middle;
  font-family: var(--mono); font-weight: 500;
}
.sv-drill { fill: var(--group); text-anchor: end; dominant-baseline: middle; }
.sv-node { cursor: pointer; }
.sv-node .sv-box { transition: stroke .1s, fill .1s; }
.sv-node:hover .sv-box { stroke: var(--accent); stroke-width: 2; }
.sv-node:hover .sv-box-label { fill: #fff; }

.sv-edge { fill: none; stroke: var(--edge); stroke-linecap: round; }
.sv-w1 { stroke-width: 1.25; opacity: .55; }
.sv-w2 { stroke-width: 2; opacity: .75; }
.sv-w3 { stroke-width: 3; opacity: .95; }
.sv-edge-weak { stroke: var(--warn); stroke-dasharray: 5 4; }
.sv-route { cursor: pointer; }
.sv-route:hover .sv-edge { stroke: var(--accent); opacity: 1; }
.sv-arrowhead { fill: var(--edge); }
.sv-route:hover .sv-arrowhead { fill: var(--accent); }

aside {
  position: fixed; right: 0; top: 0; bottom: 0; width: 28em; overflow: auto;
  background: var(--surface); border-left: 1px solid var(--line);
  padding: 20px; transform: translateX(100%); transition: transform .16s ease;
  box-shadow: -16px 0 40px #0006;
}
aside.is-open { transform: none; }
aside h2 { font-size: 14px; margin: 0 0 2px; font-family: var(--mono); }
aside .sub { color: var(--dim); font-size: 12px; margin: 0 0 14px; }
aside ul { list-style: none; padding: 0; margin: 0; }
aside li { margin: 0 0 2px; }
aside a, aside span.dead {
  color: var(--accent); font-family: var(--mono); font-size: 12px;
  text-decoration: none; display: block; padding: 5px 8px; border-radius: 6px;
}
aside a:hover { background: var(--raised); }
aside span.dead { color: var(--warn); }

.hint { color: var(--dim); font-size: 13px; }
.withheld {
  border-left: 3px solid var(--warn); padding: 2px 0 2px 14px;
  margin-top: 24px; color: var(--dim); font-size: 13px;
}
noscript .hint { color: var(--warn); }
.legend {
  display: flex; gap: 18px; flex-wrap: wrap; align-items: center;
  color: var(--faint); font-size: 12px; margin: 18px 0 0;
}
.legend b { color: var(--dim); font-weight: 500; }
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

  function show(name, refs) {
    title.textContent = name;
    current = refs;
    render();
    panel.classList.add('is-open');
  }

  document.addEventListener('click', function (ev) {
    var node = ev.target.closest('[data-evidence]');
    if (!node) {
      if (!ev.target.closest('aside')) panel.classList.remove('is-open');
      return;
    }
    var refs = node.getAttribute('data-evidence').split('\\n').filter(Boolean);
    var child = node.getAttribute('data-child');
    if (child && ev.detail === 2) { openView(node, child); return; }
    show(node.getAttribute('data-id') || node.getAttribute('data-src') || '', refs);
  });

  function openView(node, child) {
    var tab = node.closest('.tab');
    var target = tab.querySelector('[data-view="' + CSS.escape(child) + '"]');
    if (!target) return;
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
            )
        ),
        class_="view" + (" is-open" if spec_id == ds.root else ""),
        data_view=spec_id,
    )


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
                join(_view(ds, lo, sid, style) for sid in ordered if sid in lo.canvases),
                _withheld_note(lo),
            )
        ),
        class_="tab",
        id=f"d-{ds.kind.value}",
    )


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
                        )
                    ),
                    class_="base",
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
                        tag("h2", raw(""), id="panel-title"),
                        tag("ul", raw(""), id="panel-list"),
                    )
                ),
                id="panel",
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
