# Review #17: Wave B, data flow and request flow (`e54eeb1`, `53e8548`)

**Date:** 2026-09-06
**Reviewer:** Fable (adversarial)
**Fixes:** the commit following this record
**Verdict:** *"The two derivers are honest in the small: every node, edge and
stage frame in both views carries evidence that exists ... But the wave fails
at the three places it named as its own point."*

The three demonstrations: two request stories that share a module both
pre-rendered an expansion with the same `data-view` id, so drilling
`api/database` from the `api/routers` story opened the copy inside the `api`
story, crumb and siblings included, on the acceptance repo; the corridor fix
that headlined the commit made every climb from a column collinear (65
distinct-edge segment pairs overlapping on descovo, four of them for 440 to
466px) with zero findings, because no gate checked route-on-route overlap;
and 13 of the reviewer's 27 mutations survived the full suite, including
deleting every drill door in both views, so the test file could not fail on
the wave's named properties.

## Findings and triage

| # | Finding | Rank | Disposition | Why |
|---|---|---|---|---|
| F1 | Drill-in-place on request flow opened the wrong story's expansion: the id was built from the child alone, and `openView` took the first match in the tab | MUST-FIX | **Accepted, fixed** | An expansion is named for the HOST view and the box (`<view>//<box>//expanded`); the viewer resolves the host from the view the box is in (plain view, expanded host, or embedded child) and, for a plain child reached from several views, rewrites the crumb to where the reader came from. Gated by a two-story fixture asserting both ids exist and no `data-view` repeats within a tab; verified in a browser on the demo repo (crumb `api/routers request flow`) |
| F2 | Corridor climbs from one column shared an x (indexed per source), so distinct edges were drawn on one line; nothing checked route-on-route geometry | MUST-FIX | **Accepted, fixed** | Each climb from a column gets its own x; drops share a trunk only per TARGET (a bundle into one box). New gate SVA-G-015: two edges that share neither endpoint nor pair never share a line. Adding it exposed the same defect in the layered and clustered routers (516 pairs on descovo's module-deps, 102 across its architecture views) and a third flow shape (an exit and an entry at the same height across a gap, fixed by offsetting entry heights), so the gate is a WARNING that reaches the report while the layered routers' tracks are Wave D work. The flow engine itself produces zero on both repos |
| F3 | 13 of 27 reviewer mutations survived the suite | MUST-FIX | **Accepted, fixed** | Every surviving mutation is now on `scripts/mutate_dataflow.py` (19 -> 45 entries, all caught) with a test that pins the property: drill doors, `handles` evidence and variant, kinds, layers, subtitles, frame citations, the architecture gate on ingress, SVA-R-007 severity, hop attrs, report grouping. Review #9's lesson restated: a list written by the author enumerates the author's blind spots |
| F4 | Stage frames, hop frames, edge notes and sublabels never reached `diagrams/*.json` | SHOULD-FIX | **Accepted, fixed** | `canvas_json` ships `regions` (id, label, kind, geometry, members, evidence), route `note`/`variant` and box `sublabel`. Pre-dates B (service boundaries had the same gap) |
| F5 | "N imports against the flow" counted lateral imports (handler -> handler) as against | SHOULD-FIX | **Accepted, fixed** | Lateral (same hop) and upstream (toward the handlers) are counted separately and named; in a request story the second handler is a hop-1 module, so the same import is downstream there and drawn |
| F6 | Reachability stopped at generated code and SVA-R-007 then stated a false fact | SHOULD-FIX | **Accepted, fixed** | Imports are followed THROUGH code that is not a module (generated, vendored, test) without drawing it or counting it as a hop; the edge cites both import lines and its note says `via gen`. A direct import outranks a pass-through path, the shortest pass-through outranks a longer one |
| F7 | The follow-up commit recorded no acceptance CLI run, one wave after review #16 decision #6 | SHOULD-FIX (process) | **Accepted** | This fix commit records both repos' runs in its message. The rule now has a second instance behind it |
| F8 | A stage frame cited "the first member's first line", which supports no stage claim | CONSIDER | **Accepted, fixed** | Frames cite the lines that put each member in the stage: route lines for ingress and handlers, the import that reached a domain module, the classified import for a store; hop frames the same |
| F9 | Request-story boxes dropped `roles` and `stage` | CONSIDER | **Accepted, fixed** | The module's attrs travel into the story with `hop` added |
| F10 | Stage frames carried `data-kind="boundary"`; embedded component views carry `kind: architecture` | CONSIDER | **Accepted in part** | Frames carry their own kind (`service`, `stage`). The embedded child views keep `architecture`: they ARE the architecture view's component flow, shared by reference, and renaming them per host would claim a different diagram for the same drawing |
| F11 | Ingress labels cut mid-segment; identical labels for two handlers | CONSIDER | **Accepted, fixed** | Cut at a path boundary; when two ingress boxes read the same, the sublabel names the handler |
| F12 | The informational listing's trailing count counted lines, not findings | CONSIDER | **Accepted, fixed** | The count is of findings, computed from what each kept line stands for; unit-tested on eight codes of five |

## What the review confirmed sound

Byte-identical output on both repos across `PYTHONHASHSEED` 1 and 42
(re-verified after the fixes, `LC_ALL=C` against `tr_TR.UTF-8`); every node,
edge and region carries evidence that exists at the cited lines on six
fixtures; root-module routes work end to end; Express repos produce the same
stages; DOMAIN_DEPTH boundary correct; test-file routes excluded (now
pinned); only ERROR withholds; drill-in-place on data flow correct;
lifecycle/workflow named in the artifact and report; deriver diagnostics reach
REPORT.md.

## Promoted to the decision log

1. **An expansion is named for the view it is in, not for what it shows**
   (F1). Two views that share a child each own their expansion of it; an id
   built from the child alone is a first-match lookup waiting for the second
   view.
2. **Two different edges never share a line, and a gate says so** (F2).
   Drawn on one line, two arrows are one arrow to a reader: a wrong picture,
   not an ugly one. Shared endpoints (a bundle into one box) and a mutual pair
   are the only exemptions. The gate warns until every router is clean, and
   the report carries the count.
3. **A frame cites the lines that place its members** (F8). "Some member's
   first line" is a citation that supports nothing; the claim "these boxes are
   in this stage" has specific evidence per member and the frame carries it.
4. **Reachability passes through what is not drawn** (F6). Excluded code is
   in the graph for a reason; a request that goes through a generated schema
   still reaches the store, and the edge says what it went through.
5. **A reviewer's surviving mutation is a missing test, and it goes on the
   list** (F3, review #9 and #11 again). The author's list caught 19 of 19
   and none of the 13 that mattered.

## Measured after

```
709 passed, 1 skipped, 2 xfailed; ruff clean; pyright strict 0 errors
mutations: dataflow 45/45 (was 19)
demo: 0 withheld, 16 SVA-G-015 warnings (all in layered/clustered views), byte-identical
descovo: 0 withheld, 620 SVA-G-015 warnings (516 module-deps, 102 architecture,
  2 in code views embedded in the flow tabs, 0 from the flow engine), byte-identical
flow corridor on descovo data flow root: 3 routes (was about 20), one drop trunk per target
```

## Riskiest remaining untested assumption

The reviewer's, unchanged in kind and now measured: a laid-out view without
an ERROR is not yet a legible one. SVA-G-015 counts 620 shared lines on
descovo, almost all in the layered and clustered routers, and it warns rather
than withholds until those tracks are fixed (Wave D). Second: `clear_run`
decides a straight run on box geometry only; a straight run that crosses a
stage frame's label or another route's label is left to the label gate, and a
run that crosses many adjacent tracks is legal and merely dense.
