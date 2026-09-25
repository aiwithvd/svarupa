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
const labelOfNode = (n) => n.querySelector('title').textContent.split('\n')[0];
check('click opens the passport titled by the label', panel.classList.contains('is-open') && document.getElementById('panel-title').textContent === labelOfNode(a), document.getElementById('panel-title').textContent);
check('the id chip still carries the id', Array.from(document.querySelectorAll('#panel-meta code')).some((c) => c.textContent === a.getAttribute('data-id')));
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
  // Rows carry the id in data-target and title; the name is the box's label.
  const gotOut = Array.from(document.querySelectorAll('#panel-out li')).map((x) => x.getAttribute('data-target'));
  const gotIn = Array.from(document.querySelectorAll('#panel-in li')).map((x) => x.getAttribute('data-target'));
  const up = +document.querySelector('#reach-up strong').textContent;
  const down = +document.querySelector('#reach-down strong').textContent;
  if (JSON.stringify(gotOut) !== JSON.stringify(exp.outs) || JSON.stringify(gotIn) !== JSON.stringify(exp.ins) || up !== exp.up || down !== exp.down) mismatches += 1;
  fire(view.querySelector('.scroller'), 'click', { detail: 1 });
});
check('passport matches the drawn arrows for every box', mismatches === 0, mismatches + ' mismatches');

// 2b. rows name the box by its label and keep the id as the tooltip (review #20 C12)
fire(a, 'click', { detail: 1 });
const rows = Array.from(document.querySelectorAll('#panel-out li, #panel-in li'));
const labelled = rows.every((li) => {
  const id = li.getAttribute('data-target');
  const nd = Array.from(scopeOf(a).querySelectorAll('.sv-node')).find((n) => n.getAttribute('data-id') === id);
  const want = nd ? nd.querySelector('title').textContent.split('\n')[0] : id;
  return li.title === id && li.querySelector('.name').textContent === want;
});
check('connection rows show the label and carry the id', rows.length > 0 && labelled);
fire(view.querySelector('.scroller'), 'click', { detail: 1 });

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
  const expandedId = view.getAttribute('data-view') + '//' + drillable.getAttribute('data-id') + '//expanded';
  const crumbBack = () => { const o = tab.querySelector('.view.is-open'); const c = o && o.querySelector('[data-up]'); if (c) fire(c, 'click', { detail: 1 }); };
  // review #20 M4: the chevron and the passport's button both open the drill; a plain click does not
  fire(drillable, 'click', { detail: 1 });
  const openBtn = document.getElementById('panel-open');
  check('a single click on a drillable box opens its passport, not the drill', tab.querySelector('.view.is-open') === view && panel.classList.contains('is-open'));
  check('the passport of a drillable box offers Open in place', !openBtn.hidden);
  check('the button says in place only when the expansion is pre-rendered',
    openBtn.textContent === 'Open in place ›', openBtn.textContent);
  openBtn.click();
  check('the Open button opens the expansion', (tab.querySelector('.view.is-open') || {}).getAttribute && tab.querySelector('.view.is-open').getAttribute('data-view') === expandedId);
  crumbBack();
  const chevron = drillable.querySelector('.sv-drill');
  fire(chevron, 'click', { detail: 1 });
  check('a click on the chevron opens the expansion', tab.querySelector('.view.is-open').getAttribute('data-view') === expandedId);
  crumbBack();
  const leaf = Array.from(svg.querySelectorAll('.sv-node[data-id]')).find((n) => !n.hasAttribute('data-child'));
  if (leaf) { fire(leaf, 'click', { detail: 1 }); check('a leaf box has no Open button', openBtn.hidden); fire(view.querySelector('.scroller'), 'click', { detail: 1 }); }
  // review #26 F5: a box whose child view was withheld carries the mark and
  // offers no drill button, instead of a named click that opens nothing
  drillable.setAttribute('data-child-withheld', '1');
  fire(drillable, 'click', { detail: 1 });
  check('a box whose child view was withheld offers no drill button', openBtn.hidden === true);
  drillable.removeAttribute('data-child-withheld');
  fire(drillable, 'click', { detail: 1 });
  check('without the mark the drill button is back', !openBtn.hidden);
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

// 7a. keyboard (review #20 S9): Enter on a focused box is its click; Escape closes everything
check('boxes are focusable', a.getAttribute('tabindex') === '0');
a.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true }));
check('Enter on a box opens its passport and focus', panel.classList.contains('is-open') && a.classList.contains('is-focus'));
document.body.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }));
check('Escape closes the passport and clears every lit state',
  !panel.classList.contains('is-open') && !svg.classList.contains('is-focused') && svg.querySelectorAll('.is-path').length === 0);
const kbDrill = Array.from(svg.querySelectorAll('.sv-node.sv-drillable[data-child]')).find((n) => tab.querySelector('[data-view="' + view.getAttribute('data-view') + '//' + n.getAttribute('data-id') + '//expanded"]'));
if (kbDrill) {
  kbDrill.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'Enter', shiftKey: true, bubbles: true, cancelable: true }));
  const openedByKey = tab.querySelector('.view.is-open');
  check('Shift+Enter on a drillable box opens its drill', openedByKey && openedByKey.getAttribute('data-view') === view.getAttribute('data-view') + '//' + kbDrill.getAttribute('data-id') + '//expanded', openedByKey && openedByKey.getAttribute('data-view'));
  const c = openedByKey && openedByKey.querySelector('[data-up]'); if (c) fire(c, 'click', { detail: 1 });
}

// 7a'. chrome that states rather than implies (review #20 C1, S11, C2)
const themeBtn = document.getElementById('theme');
check('the theme button names the current theme', /dark$/.test(themeBtn.textContent) && !document.documentElement.hasAttribute('data-theme') && /light/.test(themeBtn.title));
themeBtn.click();
check('after a switch it names the new current theme', /light$/.test(themeBtn.textContent) && document.documentElement.getAttribute('data-theme') === 'light' && /dark/.test(themeBtn.title));
themeBtn.click();
check('the repository is labelled as one in the header', !!document.querySelector('header .repo small') && document.querySelector('header .repo small').textContent === 'repo');
check('the explore hint names the drill gesture', /double-click/.test(tab.querySelector('.explore .hint').textContent));
// review #21 N14: chapters and swatches are keyboard stops that answer Enter
const firstSwatch = tab.querySelector('.legend .sw-toggle');
check('legend swatches are focusable', firstSwatch && firstSwatch.getAttribute('tabindex') === '0');
if (firstSwatch) {
  firstSwatch.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true }));
  check('Enter on a swatch mutes its kind', firstSwatch.classList.contains('off'));
  check('a muted swatch says so to assistive tech', firstSwatch.getAttribute('aria-pressed') === 'true');
  firstSwatch.click();
}
const firstChapter = tab.querySelector('.guided .chapter');
if (firstChapter) {
  check('chapters are focusable', firstChapter.getAttribute('tabindex') === '0');
  firstChapter.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true }));
  check('Enter on a chapter opens it', firstChapter.classList.contains('is-active'));
  fire(view.querySelector('.scroller'), 'click', { detail: 1 });
}
const groupNode = Array.from(document.querySelectorAll('.sv-node[data-members]'))[0];
if (groupNode) {
  fire(groupNode, 'click', { detail: 1 });
  const members = groupNode.getAttribute('data-members').split('\n').filter(Boolean);
  const chips = Array.from(document.querySelectorAll('#panel-meta .chip-member')).map((c) => c.textContent);
  check('a group passport lists its members', JSON.stringify(chips) === JSON.stringify(members), JSON.stringify(chips));
  check('a group passport is titled by the label, not the id', document.getElementById('panel-title').textContent === labelOfNode(groupNode));
  check('a group passport says it is not a graph node', Array.from(document.querySelectorAll('#panel-meta span')).some((c) => /not a graph node/.test(c.textContent)));
  fire(groupNode.closest('.view').querySelector('.scroller'), 'click', { detail: 1 });
} else {
  console.log('ok group members (no group in this fixture; skipped)');
}

// 7c. review #23 F1: a real double-click is two clicks, and the first one opens the
// side panel and moves the canvas; the second lands on the scroller. The pair must
// still open the box.
if (drillable) {
  fire(view.querySelector('.scroller'), 'click', { detail: 1 });
  fire(drillable, 'click', { detail: 1 });
  const scroller = view.querySelector('.scroller');
  fire(scroller, 'click', { detail: 2 });
  scroller.dispatchEvent(new window.MouseEvent('dblclick', { bubbles: true, cancelable: true, view: window, detail: 2 }));
  const afterDbl = tab.querySelector('.view.is-open');
  check('a double-click whose second click lands beside the moved box still opens it',
    afterDbl && afterDbl.getAttribute('data-view') === view.getAttribute('data-view') + '//' + drillable.getAttribute('data-id') + '//expanded', afterDbl && afterDbl.getAttribute('data-view'));
  const c2 = afterDbl && afterDbl.querySelector('[data-up]'); if (c2) fire(c2, 'click', { detail: 1 });
  // review #24 N1: the second click lands on a neighbouring box that is not drillable
  const other = Array.from(svg.querySelectorAll('.sv-node[data-id]')).find((n) => n !== drillable && !n.hasAttribute('data-child')) || Array.from(svg.querySelectorAll('.sv-node[data-id]')).find((n) => n !== drillable);
  if (other) {
    fire(view.querySelector('.scroller'), 'click', { detail: 1 });
    fire(drillable, 'click', { detail: 1 });
    fire(other, 'click', { detail: 2 });
    other.dispatchEvent(new window.MouseEvent('dblclick', { bubbles: true, cancelable: true, view: window, detail: 2 }));
    const afterDbl2 = tab.querySelector('.view.is-open');
    check('a double-click whose second click lands on a neighbouring box opens the first box',
      afterDbl2 && afterDbl2.getAttribute('data-view') === view.getAttribute('data-view') + '//' + drillable.getAttribute('data-id') + '//expanded', afterDbl2 && afterDbl2.getAttribute('data-view'));
    check('focus follows the drill to the crumb', document.activeElement && document.activeElement.hasAttribute('data-up'), document.activeElement && document.activeElement.tagName);
    const c3 = afterDbl2 && afterDbl2.querySelector('[data-up]'); if (c3) fire(c3, 'click', { detail: 1 });
  }
}
// review #23 F2: every path that opens the card titles it by label
const route0 = svg.querySelector('.sv-route');
if (route0) {
  fire(route0, 'click', { detail: 1 });
  const want = labelOfNode(Array.from(svg.querySelectorAll('.sv-node')).find((n) => n.getAttribute('data-id') === route0.getAttribute('data-src')) || svg.querySelector('.sv-node')) + ' \u2192 ' + labelOfNode(Array.from(svg.querySelectorAll('.sv-node')).find((n) => n.getAttribute('data-id') === route0.getAttribute('data-dst')) || svg.querySelector('.sv-node'));
  check('a connection card is titled by the two labels', document.getElementById('panel-title').textContent === want, document.getElementById('panel-title').textContent + ' vs ' + want);
  fire(view.querySelector('.scroller'), 'click', { detail: 1 });
}
if (chapter) {
  chapter.click();
  const anchorNode = Array.from(svg.querySelectorAll('.sv-node')).find((n) => n.getAttribute('data-id') === chapter.getAttribute('data-anchor'));
  check('a chapter titles the card by the anchor label', anchorNode && document.getElementById('panel-title').textContent === labelOfNode(anchorNode), document.getElementById('panel-title').textContent);
  fire(view.querySelector('.scroller'), 'click', { detail: 1 });
}
// review #23 F3: a citation without a line is an empty file, linked without an anchor
{
  const probe = document.createElement('div');
  const emptyRef = 'pkg/__init__.py';
  const ids = Array.from(document.querySelectorAll('.sv-node[data-evidence]')).map((n) => n.getAttribute('data-evidence')).join('\n').split('\n');
  const noLine = ids.filter((r) => r && !/:\d+$/.test(r));
  fire(a, 'click', { detail: 1 });
  const rows = Array.from(document.querySelectorAll('#panel-list li a'));
  const emptyRows = rows.filter((x) => / \(empty file\)$/.test(x.textContent));
  check('empty-file citations show as the path with no line anchor', emptyRows.every((x) => !/#L/.test(x.getAttribute('href'))) && emptyRows.length === rows.filter((x) => !/:\d+$/.test(x.textContent.replace(/ \(empty file\)$/, ''))).length, emptyRows.length + ' empty of ' + rows.length + '; refs without line in artifact: ' + noLine.length);
  fire(view.querySelector('.scroller'), 'click', { detail: 1 });
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


// 9. deep links: #d-<tab>/<box id> opens the tab on the box's passport,
// and the passport's copy link produces exactly that fragment.
{
  fire(view.querySelector('.scroller'), 'click', { detail: 1 });  // settle
  const linkId = b.getAttribute('data-id');
  window.location.hash = '#' + tab.id + '/' + encodeURIComponent(linkId);
  window.dispatchEvent(new window.Event('hashchange'));
  check('a deep link opens the passport on the linked box',
    panel.classList.contains('is-open') && document.getElementById('panel-title').textContent === labelOfNode(b),
    document.getElementById('panel-title').textContent);
  check('the deep link focuses the linked box', b.classList.contains('is-focus'));
  check('the address bar settles on the plain tab form', window.location.hash === '#' + tab.id, window.location.hash);
  const linkBtn = document.getElementById('panel-link');
  check('a box passport offers copy link', linkBtn && !linkBtn.hidden);
  linkBtn.click();
  check('copy link acknowledges the copy', linkBtn.textContent === 'copied', linkBtn.textContent);
  // A bogus box id opens nothing and keeps the page stable.
  fire(view.querySelector('.scroller'), 'click', { detail: 1 });
  window.location.hash = '#' + tab.id + '/no%2Fsuch%2Fbox';
  window.dispatchEvent(new window.Event('hashchange'));
  check('a deep link naming no box opens nothing', !panel.classList.contains('is-open'));
  fire(view.querySelector('.scroller'), 'click', { detail: 1 });
}

console.log(failures === 0 ? 'ALL OK' : failures + ' FAILED');
process.exit(failures === 0 ? 0 : 1);
