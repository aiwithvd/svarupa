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
    return raw(
        """
:root {
  --bg: #11131a; --panel: #171a23; --line: #262b38; --ink: #e6e9ef;
  --dim: #8b93a7; --accent: #7aa2f7; --warn: #e0af68; --edge: #4a5568;
}
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--bg); color: var(--ink);
  font-family: FONT_STACK_PLACEHOLDER; font-size: 13px;
}
header {
  position: sticky; top: 0; z-index: 5; background: var(--panel);
  border-bottom: 1px solid var(--line); padding: 10px 16px;
  display: flex; gap: 16px; align-items: center; flex-wrap: wrap;
}
h1 { font-size: 14px; margin: 0; font-weight: 600; }
h1 small { color: var(--dim); font-weight: 400; margin-left: 8px; }
nav { display: flex; gap: 4px; flex-wrap: wrap; }
nav a {
  color: var(--dim); text-decoration: none; padding: 4px 10px;
  border: 1px solid var(--line); border-radius: 5px;
}
nav a:hover { color: var(--ink); border-color: var(--accent); }
.base { margin-left: auto; display: flex; gap: 6px; align-items: center; }
.base input {
  background: var(--bg); color: var(--ink); border: 1px solid var(--line);
  border-radius: 5px; padding: 4px 8px; font: inherit; width: 22em;
}
.tab { display: none; padding: 16px; }
.tab:target { display: block; }
/* Without :target, and without JS, the first tab must still be visible. */
.tab:first-of-type { display: block; }
body:has(.tab:target) .tab:first-of-type { display: none; }
body:has(.tab:target) .tab:target { display: block; }
.meta { color: var(--dim); margin: 0 0 12px; }
.crumbs { margin: 0 0 10px; color: var(--dim); }
.crumbs a { color: var(--accent); }
.view { display: none; }
.view.is-open { display: block; }
.sv-canvas { max-width: 100%; height: auto; background: var(--bg); }
.sv-band { fill: #ffffff06; }
.sv-band-label { fill: var(--dim); font-size: 11px; }
.sv-box { fill: var(--panel); stroke: var(--line); stroke-width: 1; }
.sv-drillable .sv-box { stroke: var(--accent); }
.sv-box-label {
  fill: var(--ink); text-anchor: middle; dominant-baseline: middle;
}
.sv-drill { fill: var(--accent); text-anchor: end; dominant-baseline: middle; }
.sv-node { cursor: pointer; }
.sv-node:hover .sv-box { stroke: var(--accent); stroke-width: 2; }
.sv-edge {
  fill: none; stroke: var(--edge); stroke-width: 1.5;
  marker-end: url(#sv-arrow);
}
.sv-edge-weak { stroke-dasharray: 4 3; stroke: var(--warn); }
.sv-arrowhead { fill: var(--edge); }
.sv-edge-label {
  fill: var(--dim); font-size: 11px; text-anchor: middle;
  paint-order: stroke; stroke: var(--bg); stroke-width: 3px;
}
aside {
  position: fixed; right: 0; top: 0; bottom: 0; width: 26em; overflow: auto;
  background: var(--panel); border-left: 1px solid var(--line);
  padding: 16px; transform: translateX(100%); transition: transform .12s;
}
aside.is-open { transform: none; }
aside h2 { font-size: 13px; margin: 0 0 4px; }
aside ul { list-style: none; padding: 0; }
aside li { margin: 3px 0; }
aside a { color: var(--accent); }
.hint { color: var(--dim); margin-top: 10px; }
.withheld { border-left: 3px solid var(--warn); padding-left: 10px; }
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
                canvas_svg(canvas, style),
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
    css = str(_css()).replace("FONT_STACK_PLACEHOLDER", FONT_STACK)
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
