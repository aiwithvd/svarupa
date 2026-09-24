# Review #26 — Scale & performance (2026-09-24)

Scope: `f6aae3b..HEAD` — flow/layered engines at scale (demand-driven gaps,
wrapped fans, residue-conditional ports), data-flow stage cap
(MAX_STAGE_BOXES=12), near-linear geometry validation, viewer expansion cap
(MAX_EXPANSION_HOST_BOXES=24) + deep links. Protocol: docs/reviews/TEMPLATE.md.
Scope kept tight per the review-25 timeout lesson: the four substantive files,
their new tests, and the exact call sites needed to verify claims.

## Findings

### MUST-FIX

**F1 — Modules reachable only through a capped-away handler are declared
"reachable from no route handler".**
`svarupa/derive/dataflow.py:377-378` caps `handlers` first and then computes
`hops, entry = _reach(set(handlers), ...)`. A domain module whose only path
from a route runs through handler #13 (omitted) never enters `hops`, falls
into `unstaged` (computed from the *uncapped* sets at :503), and is named in
the SVA-R-007 diagnostic: "N module(s) are reachable from no route handler
within 2 import hops" (:507-514). That claim is false — the module *is*
reachable, from the handler the cap chose not to draw. The comment at
:373-375 says the uncapped sets are kept precisely so capped-away modules
don't "read as 'reachable from no handler', which is false" — it guards the
capped handlers themselves but not anything downstream of them. On any repo
that trips the handler cap, the tool makes a factually wrong statement about
real modules; failure honesty is the standing cardinal sin. Remedy: reach
from the uncapped set — `_reach(set(all_handlers), imports, DOMAIN_DEPTH)` —
and keep the existing domain cap afterwards. The domain cap already states
its own remainder, so nothing is hidden, and SVA-R-007 then counts only
genuinely unreachable modules.

### SHOULD-FIX

**F2 — The gap-widening branch drops `strip_right` from the canvas width.**
Old width: `max(x - col_gap + style.margin + pad + style.lane_gutter,
strip_right)` (engines.py:1387). The new demand-driven branch replaces it
with `width = x - gap_w[-1] + style.margin + pad + style.lane_gutter`
(engines.py:1567-1573) — the `max(..., strip_right)` is gone. A repo with a
wide environment strip *and* a gap congested enough to trigger widening gets
a canvas narrower than its own strip: strip boxes past the right edge
(SVA-G-005, or clipped rendering if the check doesn't catch it). The two
conditions co-occur on exactly the large repos this change targets. Remedy:
`width = max(x - gap_w[-1] + style.margin + pad + style.lane_gutter,
strip_right)` in the new branch.

**F3 — RequestFlow: a hop-N module drawn with no drawn parent claims a path
the picture doesn't show.**
`svarupa/derive/dataflow.py:628-639`: `hops` comes from the full
`_reach({handler}, ...)`, then each hop level is capped independently. A
hop-2 module reachable *only* via a capped-away hop-1 module survives the
filter (it is in `kept` by its own hop's ranking) but has no drawn in-edge —
the story shows a box labelled hop 2 with no visible path from the handler.
The diagram asserts "within 2 import hops" while the route that evidences
the claim is invisible. Remedy: after capping, recompute hops through kept
modules only (BFS from the handler over the kept subgraph), or drop modules
that lost every drawn parent and add them to the remainder clause so the
subtitle stays honest.

### CONSIDER

**F4 — The fan wrap can re-create the exact stub overlap it avoids.**
engines.py:1409-1414: `cycle = max(1, (b.h - 1) // max(1, step))`. When
`slots > b.h - 1` (more ports on one side than the box has pixels of
height), the wrap maps distinct ports to the same y. Ports are per *side*,
so an exit and a backward edge's entry can coincide; those routes share
neither src nor dst, the SVA-G-015 exemption doesn't apply, and the view
withholds — trading "port walks off the box" for "ports collide". Narrow
window (needs >`h-1` same-side ports in both directions), and the 70-port
test passes because it exercises one direction. Accept with a note, or
separate the exit/entry lists before wrapping.

**F5 — "Open ›" on a dangling drill is a dead button.**
The new label logic (viewer.py:682-690) correctly says "Open ›" when no
expansion exists, but when the *child view itself* was withheld (SVA-R-005,
"reported rather than repaired" — layout/__init__.py:164), `openView` finds
neither target and silently returns (viewer.py:1207-1209). A named button
that does nothing on click is a louder dead affordance than the old
double-click. Pre-existing design tension, not a regression; consider
disabling/relabelling the button when neither target exists (emit would
need to mark withheld children on the box).

**F6 — The two new cap constants live only in code comments.**
MAX_STAGE_BOXES (dataflow.py:60-66) and MAX_EXPANSION_HOST_BOXES
(viewer.py:31-36) are well-argued in module comments, but a grep of
`docs/superpowers/specs` finds neither; the protocol puts such decisions in
the design §15 log so the next reviewer inherits the rationale. Promote on
triage.

## Verified non-findings

- **validate.py rewrite is verdict-identical** (the highest-risk change).
  `_check_route_overlap`: diagonal segments are skipped by both old
  (`continue` on neither vertical nor horizontal) and new (never bucketed);
  zero-length segments cannot satisfy `hi > lo` in either; the sweep's
  prune (`ahi <= lo` → drop) is sound because the line is sorted by `lo`,
  so no later segment can reach a dropped interval; `best` keeps the
  lexicographically first segment pair per route pair with route-p index
  first — exactly the old nested loop's break order; final diagnostics are
  emitted in sorted `(i, s)` order with the same message coordinates.
  `_check_overlap`: y-groups in `[a.y, a.bottom + MIN_GAP)` reproduce the
  old early-break set exactly; own-row bisect at `(a.x, a.id)` and
  later-rows full scan together emit each pair once, in the old order; the
  `b.x >= a.right + MIN_GAP` break is safe because `overlaps` requires
  x-distance < MIN_GAP. `_check_crossings`/`_check_labels` waypoint
  prefilters preserve iteration order; the id→box dict is a pure memo.
  No counterexample constructed despite trying.
- **engines.py small-diagram invariance holds.** col_gap is 84 (88 with
  regions); the smallest non-trivial demand formula yields 28, and it takes
  ~7 corridor climbs in one gap before `gap_w` exceeds col_gap — below that
  the `any(w != col_gap)` guard skips the whole branch and geometry is
  byte-identical. `aligned()` is `(x & ~3) | residue` whenever the fan step
  is ≥4, identical to the old code (step < 4 needs degree > w/4, ~31 ports
  on a 120px box). The `fan_y` wrap is the identity whenever ports ≤
  `b.h - 1` (proved: `cycle = (b.h-1)//step ≥ slots` in both the step=1 and
  step≥2 regimes). The trailing-lane width extension (engines.py:1575-1576)
  changes small-diagram output only when last-column climbs previously ran
  off-canvas — an intended fix, not a regression.
- **dataflow cap mechanics.** Selection is deterministic (`(-degree, m)`
  total order; double-run test asserts byte-equal specs); subtitle counts
  drawn modules and the remainder clause sums back to the true total;
  capped modules are excluded from both the stage sets and `unstaged`, so
  R-006 and R-007 don't double-report; the omitted module stays in
  graph.json (test asserts) and one drill away. R-006 subject names up to 5
  omitted modules.
- **viewer expansion cap.** The host-size check counts non-waypoint boxes
  on the same canvas the expansion loop iterates (viewer.py:1481-1484);
  `openView` already falls back expansion → child view (:1207-1208), and
  the label lookup mirrors openView's host resolution
  (plain/host/child-scope) line for line, so the label cannot promise an
  expansion that isn't there. Deep links percent-encode box ids and compare
  via `getAttribute` — no markup injection path; the no-JS degradation is
  stated in the comment.
- **Tests are contract tests, not tautologies**: adversarial shapes
  asserting `validate(c) == ()`, cap boundary at budget ±, determinism by
  double-run. The one weak spot is `test_validation_is_near_linear_on_a_dense_canvas`
  (tests/test_layout.py:94-122): a 30s wall-clock assert that would also
  pass if the checks were no-ops — acceptable as asymptotic smoke because
  correctness is pinned elsewhere, but it guards time, not verdicts.

## Riskiest remaining untested assumption

That no real repo produces same-side, both-direction port counts above
`b.h - 1` (F4): the wrap's collision window is unexercised by any test
(the 70-port test is one-directional), so the first repo to hit it
withholds a view with SVA-G-015 and no hint that the wrap caused it.

## Triage table

| # | Finding | Rank | Disposition | Why |
|---|---|---|---|---|
| 1 | Capped handlers poison downstream reachability (SVA-R-007 false claim) | MUST-FIX | pending | |
| 2 | Gap-widening drops strip_right from canvas width | SHOULD-FIX | pending | |
| 3 | Request story draws hop-N boxes with no visible path | SHOULD-FIX | pending | |
| 4 | Fan wrap collision window when slots > b.h-1 | CONSIDER | pending | |
| 5 | "Open ›" dead button on dangling drill | CONSIDER | pending | |
| 6 | Cap constants absent from design §15 | CONSIDER | pending | |

## Summary

MUST-FIX 1 · SHOULD-FIX 2 · CONSIDER 3
