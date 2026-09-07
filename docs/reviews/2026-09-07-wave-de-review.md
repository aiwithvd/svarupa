# Review #19: Waves D and E (`305f699`, `6b5866a`, `1d94164`, `44e8bb0`, `8dda62b`)

**Date:** 2026-09-07
**Reviewer:** Fable (adversarial)
**Fixes:** the commit following this record
**Verdict:** *"The pictures are Archify-class; the discipline that made every
earlier wave trustworthy stopped at the `<script>` tag and did not reach the
new routing either."*

The demonstrations: a background click, a drill or a crumb left the whole
canvas at 28 percent with nothing lit (the hover class was orphaned while
focus suspended mouseout); a shift-click path stayed lit under the next
focus; a same-column edge and a backward edge in the flow router shared a
vertical because climbs and backward drops drew from separate counters, so
the new ERROR gate withheld a legitimate view; the grid router ran a backward
foreign key straight through the rows between, withholding any ERD of three
or more rows with one; the `mcp` extra admitted a 1.x SDK the code cannot
import; and 24 of the reviewer's 28 mutations survived the suite, including
every mechanism the wave was named for (channel-constraint tracks, parity,
label preference, the chrome constants) and all of the JavaScript. The
reviewer's jsdom harness found the two click bugs in six seconds.

## Findings and triage

| # | Finding | Rank | Disposition | Why |
|---|---|---|---|---|
| F1 | Clearing focus orphaned the hover class; the canvas stayed dimmed | MUST-FIX | **Accepted, fixed** | `clearFocus` removes every lit state (focus, hover, pinned path, caption). Pinned by the jsdom harness, now part of the suite |
| F2 | A shift-click path survived the next plain click | SHOULD-FIX | **Accepted, fixed** | Same clear; harness checks that a plain click after a path lights only the focus set |
| F3 | Flow router: climbs and backward drops shared a vertical (two counters for one side of a column) | SHOULD-FIX | **Accepted, fixed** | One counter per gap for everything on a column's right side; the reviewer's three-node spec is the test and validates clean |
| F4 | Grid router: a reversed edge ran through the rows between | SHOULD-FIX | **Accepted, fixed** | Reversed edges go round the side lane, each on its own x (two backward keys shared the lane at first, which the same gate caught); the 30-table spec is the test |
| F5 | `mcp>=1.2` admits an SDK the code cannot import | SHOULD-FIX | **Accepted, fixed** | Pinned `mcp>=2`; the refusal's fix text names the layout difference |
| F6 | The JavaScript has no runner | SHOULD-FIX | **Accepted, fixed** | `tests/js/viewer_harness.js` (jsdom) runs the shipped script against a built artifact and checks hover, focus, background click, passport lists and reach against the drawn arrows, path-then-click, chapters, drill, crumb and export. `tests/test_viewer_js.py` runs it and skips with a stated reason when node or jsdom is missing, so the absence is visible, never silent |
| F7 | 24 reviewer mutations survived, including the wave's mechanisms | SHOULD-FIX | **Accepted, fixed** | Every survivor is on `scripts/mutate_waved.py` with a pinning test: cards eligibility, samples, box counts, ranking, message buses, route counts; SVA-G-016 severity; mutual-pair exemption; 3px tolerance; the channel constraint; parity; label preference; chrome constants (literal expectations, not imported ones); `data-members`; MCP plumbing; and the JS mutations through the harness |
| F8 | "Root fits" read as a claim about the page; chrome constants unmeasured and missing the scrollbar | CONSIDER | **Accepted, fixed** | Measured in a real 1440x900 viewport: canvas top at 245px; constants are now 290 and 57 (scrollbar included); the column is "Canvas fits" with a footnote that the legend and cards below may scroll |
| F9 | Chapters with no story ("1 boxes") | CONSIDER | **Accepted, fixed** | A chapter needs at least one arrow; singular forms |
| F10 | Unresolved card showed the scorecard's bucket tags | CONSIDER | **Accepted, fixed** | Tags stripped; samples are repository names |
| F11 | Passport verb fallback `imports` for an unlabelled arrow | CONSIDER | **Accepted, fixed** | The fallback is the neutral `connects`; the harness asserts no invented verb |
| F12 | "Upstream / Downstream" ambiguous over import arrows | CONSIDER | **Accepted, fixed** | Buttons read "Used by" and "Uses", with titles stating the arrow semantics |
| F13 | MCP accepted negative depth and zero budget | CONSIDER | **Accepted, fixed** | Clamped at the tool boundary |

## What the review confirmed sound

Passport OUT/IN lists and reach counts recomputed from the drawn arrows on
131 root boxes across both repos, zero mismatches, scoped correctly inside
expanded views; all text via `textContent`, `CSS.escape` on selectors,
citation scheme allow-listed; export is a parseable standalone SVG with the
theme and no interaction state; SVA-G-015's logic correct and both repos at
zero; byte identity under a third seed; chapters light exactly their set;
cards' eligibility gates hold; MCP tools match `run_query` and keep stdout
clean for the protocol; Wave B and C decisions hold except B5, which F6 and
F7 restore.

## Promoted to the decision log

1. **Everything the reader does runs in the suite too** (F1, F2, F6). The
   layout gates certify the SVG; hover, click, focus, path, chapter, drill
   and export are code, and code that is only pinned by source strings is
   untested. The jsdom harness is the runner, and a JavaScript change gets a
   check in it the way a Python change gets a test.
2. **A gate that withholds must be paired with routers that are clean on
   shapes beyond the acceptance repos** (F3, F4). "0 withheld on demo and
   descovo" is a fact about two repositories; the reviewer's three-node and
   thirty-node specs are the shapes the routers now pin.
3. **A measured constant carries its measurement** (F8). The chrome
   constants were guesses that a test then read back from the module; they
   are now numbers measured in a browser, asserted as literals, and the
   report says exactly what fits.

## Measured after

```
752 passed, 1 skipped, 2 xfailed; ruff clean; pyright strict 0 errors
mutate_waved 9 -> 31 entries, all caught (including 7 JavaScript mutations
  through the jsdom harness); jsdom harness 19 checks green
layered router: 0 errors across 20 skipping-lattice shapes (widths 3-7,
  depths 3-6); flow and grid: the reviewer's specs validate clean
demo and descovo: 0 withheld, byte-identical across seeds
```

One more shape surfaced while pinning the channel constraint: on a 5x5
lattice two chains cross inside one gap, each with a terminal on the other's
column, a constraint cycle no order satisfies. Resolved the way channel
routing does: exits are 0 mod 4, top-side entries 2 mod 4 and waypoint
centres odd (three x classes that cannot coincide), and the net a cycle
still leaves gets a three-pixel dogleg through its waypoints.

## Riskiest remaining untested assumption

The harness runs in jsdom, not a browser: layout-dependent behaviour (what
is on screen, scroll positions, the sticky header covering the explore bar)
is still verified by hand with screenshots. And the two engines are pinned
on the shapes two reviews found; a third shape will need a third review.
