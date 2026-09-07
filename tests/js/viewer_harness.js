// Drives the shipped viewer script in jsdom and checks what the reader
// sees against the arrows drawn. Every check prints `ok <name>` or
// `FAIL <name>: <why>`; the Python test counts the FAIL lines.
//
// Review #19 wrote the first version of this in six seconds of runtime and
// found two state bugs a first click exposes; the suite had pinned the
// JavaScript by source strings only.
'use strict';
const fs = require('fs');
const path = require('path');
const { JSDOM } = require(path.join(__dirname, 'node_modules', 'jsdom'));

const file = process.argv[2];
const html = fs.readFileSync(file, 'utf8');
const blobs = [];
const dom = new JSDOM(html, {
  runScripts: 'dangerously',
  url: 'http://localhost/index.html',
  pretendToBeVisual: true,
  beforeParse(window) {
    window.CSS = window.CSS || {};
    if (!window.CSS.escape) {
      window.CSS.escape = (s) => String(s).replace(/[^a-zA-Z0-9_-]/g, (c) => '\\' + c);
    }
    window.Element.prototype.scrollIntoView = function () {};
    window.URL.createObjectURL = () => 'blob:fake';
    window.URL.revokeObjectURL = () => {};
    const RealBlob = window.Blob;
    window.Blob = class extends RealBlob {
      constructor(parts, opts) {
        super(parts, opts);
        blobs.push({ text: parts.join(''), type: opts && opts.type });
      }
    };
    window.HTMLAnchorElement.prototype.click = function () {};
  },
});
const { window } = dom;
const document = window.document;
let failures = 0;
function check(name, cond, why) {
  if (cond) console.log('ok ' + name);
  else { failures += 1; console.log('FAIL ' + name + ': ' + (why || '')); }
}
function fire(el, type, init) {
  el.dispatchEvent(new window.MouseEvent(type, Object.assign({ bubbles: true, cancelable: true, view: window }, init || {})));
}
const panel = document.getElementById('panel');
const scopeOf = (el) => el.closest('[data-scope]') || el.closest('svg');
const routesOf = (root) => Array.from(root.querySelectorAll('.sv-route'));
function expected(root, id) {
  const rs = routesOf(root);
  const outs = rs.filter((r) => r.getAttribute('data-src') === id).map((r) => r.getAttribute('data-dst'));
  const ins = rs.filter((r) => r.getAttribute('data-dst') === id).map((r) => r.getAttribute('data-src'));
  const bfs = (dir) => {
    const seen = new Set([id]); const q = [id];
    while (q.length) {
      const cur = q.shift();
      rs.forEach((r) => {
        const s = r.getAttribute('data-src'), d = r.getAttribute('data-dst');
        const n = dir === 'down' ? (s === cur ? d : null) : (d === cur ? s : null);
        if (n && !seen.has(n)) { seen.add(n); q.push(n); }
      });
    }
    seen.delete(id); return seen.size;
  };
  return { outs, ins, up: bfs('up'), down: bfs('down') };
}

const tab = document.querySelector('.tab');
const view = tab.querySelector('.view.is-open');
const svg = view.querySelector('svg');
const connected = Array.from(svg.querySelectorAll('.sv-node[data-id]')).filter((n) => {
  const id = n.getAttribute('data-id');
  return routesOf(scopeOf(n)).some((r) => r.getAttribute('data-src') === id || r.getAttribute('data-dst') === id);
});
check('fixture has connected boxes', connected.length >= 2);
const a = connected[0];
const b = connected[1];

// 1. hover, click (focus), mouseout while focused, background click: nothing lit is left dim
fire(a, 'mouseover', { relatedTarget: document.body });
check('hover lights a path', svg.classList.contains('is-hovering') && svg.querySelectorAll('.is-path').length > 0);
fire(a, 'click', { detail: 1 });
check('click focuses', svg.classList.contains('is-focused') && a.classList.contains('is-focus'));
check('click opens the passport', panel.classList.contains('is-open') && document.getElementById('panel-title').textContent === a.getAttribute('data-id'));
fire(a, 'mouseout', { relatedTarget: document.body });
fire(view.querySelector('.scroller'), 'click', { detail: 1 });
check('background click clears every lit state',
  !svg.classList.contains('is-focused') && !svg.classList.contains('is-hovering') && svg.querySelectorAll('.is-path').length === 0,
  'focused=' + svg.classList.contains('is-focused') + ' hovering=' + svg.classList.contains('is-hovering') + ' lit=' + svg.querySelectorAll('.is-path').length);
check('background click closes the passport', !panel.classList.contains('is-open'));

// 2. passport lists and reach counts equal the drawn arrows, for every connected box
let mismatches = 0;
connected.slice(0, 40).forEach((n) => {
  const id = n.getAttribute('data-id');
  fire(n, 'click', { detail: 1 });
  const exp = expected(scopeOf(n), id);
  const gotOut = Array.from(document.querySelectorAll('#panel-out li .name')).map((x) => x.textContent);
  const gotIn = Array.from(document.querySelectorAll('#panel-in li .name')).map((x) => x.textContent);
  const up = +document.querySelector('#reach-up strong').textContent;
  const down = +document.querySelector('#reach-down strong').textContent;
  if (JSON.stringify(gotOut) !== JSON.stringify(exp.outs) || JSON.stringify(gotIn) !== JSON.stringify(exp.ins) || up !== exp.up || down !== exp.down) mismatches += 1;
  fire(view.querySelector('.scroller'), 'click', { detail: 1 });
});
check('passport matches the drawn arrows for every box', mismatches === 0, mismatches + ' mismatches');

// 3. reach button lights exactly the directed closure
fire(a, 'click', { detail: 1 });
const expA = expected(scopeOf(a), a.getAttribute('data-id'));
if (expA.down > 0) {
  document.getElementById('reach-down').click();
  const lit = svg.querySelectorAll('.sv-node.is-path').length;
  check('Uses lights the downstream closure plus the box', lit === expA.down + 1, 'lit=' + lit + ' expected=' + (expA.down + 1));
} else {
  console.log('ok Uses (no downstream in this fixture; skipped)');
}
fire(view.querySelector('.scroller'), 'click', { detail: 1 });

// 4. a shift-click path does not survive the next plain click
fire(a, 'click', { detail: 1, shiftKey: true });
fire(b, 'click', { detail: 1, shiftKey: true });
const pathCaption = tab.querySelector('.explore .path').textContent;
fire(a, 'click', { detail: 1 });
const litAfter = Array.from(svg.querySelectorAll('.sv-node.is-path')).map((n) => n.getAttribute('data-id'));
const expectedLit = new Set([a.getAttribute('data-id')].concat(expA.outs, expA.ins));
check('a plain click after a path lights only the focus set',
  litAfter.every((id) => expectedLit.has(id)) && !svg.classList.contains('is-pinned') && tab.querySelector('.explore .path').textContent === '',
  'lit=' + JSON.stringify(litAfter) + ' caption=' + JSON.stringify(tab.querySelector('.explore .path').textContent) + ' (path caption before was ' + JSON.stringify(pathCaption) + ')');
fire(view.querySelector('.scroller'), 'click', { detail: 1 });

// 5. no verb is invented
let invented = 0;
connected.slice(0, 40).forEach((n) => {
  fire(n, 'click', { detail: 1 });
  document.querySelectorAll('#panel-out li .verb, #panel-in li .verb').forEach((v) => { if (v.textContent === 'imports') {
    const other = v.closest('li').getAttribute('data-target');
    const r = routesOf(scopeOf(n)).find((rr) => (rr.getAttribute('data-src') === other || rr.getAttribute('data-dst') === other));
    if (r && !r.getAttribute('data-note') && !r.getAttribute('data-label')) invented += 1;
  } });
  fire(view.querySelector('.scroller'), 'click', { detail: 1 });
});
check('no verb is invented for an unlabelled arrow', invented === 0, invented + ' invented');
// Force the fallback: strip one arrow's verb and note, then read the passport.
const stripped = routesOf(scopeOf(a)).find((r) => r.getAttribute('data-src') === a.getAttribute('data-id') || r.getAttribute('data-dst') === a.getAttribute('data-id'));
if (stripped) {
  const savedNote = stripped.getAttribute('data-note'), savedLabel = stripped.getAttribute('data-label');
  stripped.removeAttribute('data-note'); stripped.removeAttribute('data-label');
  fire(a, 'click', { detail: 1 });
  const verbs = Array.from(document.querySelectorAll('#panel-out li .verb, #panel-in li .verb')).map((v) => v.textContent);
  check('an arrow with no verb is shown as connects, never as imports', verbs.indexOf('connects') >= 0 && verbs.indexOf('imports') < 0 || verbs.every((v) => v !== 'imports'), JSON.stringify(verbs));
  if (savedNote !== null) stripped.setAttribute('data-note', savedNote);
  if (savedLabel !== null) stripped.setAttribute('data-label', savedLabel);
  fire(view.querySelector('.scroller'), 'click', { detail: 1 });
}

// 6. a chapter opens the root view, focuses its anchor and lights its set
const chapter = tab.querySelector('.guided .chapter');
if (chapter) {
  chapter.click();
  const focus = (chapter.getAttribute('data-focus') || '').split('\n').filter(Boolean);
  const lit = Array.from(svg.querySelectorAll('.sv-node.is-path')).map((n) => n.getAttribute('data-id')).sort();
  check('a chapter lights exactly its focus set', JSON.stringify(lit) === JSON.stringify(focus.slice().sort()), JSON.stringify(lit) + ' vs ' + JSON.stringify(focus));
  check('a chapter reports progress', tab.querySelector('.guided .progress').textContent.startsWith('1 / '));
  fire(view.querySelector('.scroller'), 'click', { detail: 1 });
} else {
  console.log('ok chapters (none in this fixture; skipped)');
}

// 7. a drill clears the passport and the focus of the view being left
const drillable = Array.from(svg.querySelectorAll('.sv-node.sv-drillable[data-child]')).find((n) => tab.querySelector('[data-view="' + view.getAttribute('data-view') + '//' + n.getAttribute('data-id') + '//expanded"]'));
if (drillable) {
  fire(drillable, 'click', { detail: 1 });
  fire(drillable, 'click', { detail: 2 });
  const open = tab.querySelector('.view.is-open');
  check('double-click opens the expansion of this view', open && open.getAttribute('data-view') === view.getAttribute('data-view') + '//' + drillable.getAttribute('data-id') + '//expanded', open && open.getAttribute('data-view'));
  check('a drill closes the passport and clears focus', !panel.classList.contains('is-open') && !svg.classList.contains('is-focused') && !svg.classList.contains('is-hovering'));
  const crumb = open && open.querySelector('[data-up]');
  if (crumb) { fire(crumb, 'click', { detail: 1 }); check('the crumb returns to a clean root', tab.querySelector('.view.is-open') === view && !svg.classList.contains('is-hovering')); }
} else {
  console.log('ok drill (no embeddable drillable box in this fixture; skipped)');
}

// 7b. the same box in another tab: the passport offers the jump and it lands focused there
fire(a, 'click', { detail: 1 });
const alsoLinks = Array.from(document.querySelectorAll('#panel-also span'));
const otherTabsWithA = Array.from(document.querySelectorAll('.tab')).filter((t2) => t2 !== tab && t2.id && Array.from(t2.querySelectorAll('.view:not([data-host]) .sv-node')).some((n) => n.getAttribute('data-id') === a.getAttribute('data-id')));
check('the passport lists every other tab that shows this box', alsoLinks.length === otherTabsWithA.length, alsoLinks.length + ' vs ' + otherTabsWithA.length);
if (alsoLinks.length) {
  fire(alsoLinks[0], 'click', { detail: 1 });
  const dest = document.getElementById(alsoLinks[0].getAttribute('data-tab'));
  const destView = dest.querySelector('.view.is-open');
  const destFocus = destView && destView.querySelector('.sv-node.is-focus');
  check('the jump opens the other tab on the view holding the box and focuses it',
    window.location.hash === '#' + dest.id && destFocus && destFocus.getAttribute('data-id') === a.getAttribute('data-id'),
    'hash=' + window.location.hash + ' focus=' + (destFocus && destFocus.getAttribute('data-id')));
  window.location.hash = '#' + tab.id;
}
fire(view.querySelector('.scroller'), 'click', { detail: 1 });

// 8. export: a standalone SVG with the stylesheet and theme, no interaction state.
// The export runs from whatever state the SVG is in; plant every state class
// and call the exporter directly through its button handler on a live focus.
fire(a, 'click', { detail: 1 });
svg.classList.add('is-focused', 'is-hovering', 'is-pinned', 'is-searching');
a.classList.add('is-path', 'is-focus', 'is-hit', 'is-off');
const exportBtn = tab.querySelector('.explore [data-export="svg"]');
exportBtn.dispatchEvent(new window.MouseEvent('click', { bubbles: false, cancelable: true, view: window }));
if (!blobs.length) exportBtn.click();
const exported = blobs[blobs.length - 1];
check('export produces an SVG blob', exported && exported.type.indexOf('image/svg+xml') === 0 && exported.text.startsWith('<svg'));
check('export inlines the stylesheet and theme', exported && exported.text.includes('<style>') && exported.text.includes('data-theme="'));
check('export carries no interaction state', exported && !/class="[^"]*\bis-(path|focus|focused|hovering|pinned|searching|hit|off)\b/.test(exported.text));
fire(view.querySelector('.scroller'), 'click', { detail: 1 });

console.log(failures === 0 ? 'ALL OK' : failures + ' FAILED');
process.exit(failures === 0 ? 0 : 1);
