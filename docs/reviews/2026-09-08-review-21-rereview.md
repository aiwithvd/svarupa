# Review #21: re-review of the #20 fixes (`a9018f8`)

**Date:** 2026-09-08
**Reviewer:** Fable (adversarial)
**Fixes:** the commit following this record
**Scope:** every one of the 29 findings of review #20 verified against what a person sees and what the code does; regressions the fixes introduced; the new module-deps tree; the lockfile; the keyboard; the passport at narrow widths. Both acceptance repositories and svarupa itself built (all exit 0; the self build three times, byte-identical), 288 screenshots from a headless Chromium (1024x768, 1280x720, 1440x900, 1920x1080, plus 1100x800 for the passport), a geometry scan of all 1139 views (163 demo, 976 descovo), every root arrow of `deploy-topology`, `architecture` and `module-deps` checked against the lines it cites, every id drawn at a root queried, every command named in the documents run.
**Artifacts:** `/tmp/review21/` (`shots/`, `exports/`, `walk-*.json`, `scan-*.json`, `datacheck-*.json`, `s7-loop.txt`, `log.md`).

## Verdict

**Not product ready; one more round.** The one sentence for the user: *the crash is gone, the drill is discoverable, the module-deps tab reads, and 1139 views have zero text collisions, but the System view now draws an arrow the code contradicts (`api` calls Gemini, cited to a file the `api` image never copies), the passport greets a stranger with `group:agent/routers` where the box says `agent` (the README's own screenshot shows it), the new "Open in place" button lands the reader under the header, and between 1024 and 1100 px the header hides a tab and the passport covers the canvas again.* Of the 29 findings: 17 verified, 10 partly fixed, 2 fixed with a regression riding on the fix (M2, S2). Nothing that #20 certified below the diagram surface broke: determinism, gates, geometry, hover, path, search, legend mute, chapters, cards, crumb, export, light theme, no-JS, hostile input, lockfile, tests.

## The 29 findings of #20

| # | #20 finding | Status | Evidence |
|---|---|---|---|
| M1 | Self build crashes (138/139) | **verified** | `svarupa /Users/vishvdeep/Documents/svarupa --out /tmp/review21/self` exit 0 three times; `diff -r self self2`, `self self3` clean; 2 diagrams, 0 withheld |
| M2 | `api -> MongoDB` missing, `api`'s file cited under `agent` | **regressed** | `api -> MongoDB` is drawn, but both services now carry every store arrow with the same lines: `agent -> MongoDB` still cites `api/database/database.py:1-2`, and a new `api -> Gemini API` cites `agent/core/react_agent/agent.py:3`. `Dockerfile.api` does `COPY api /app/api` only. See N1 |
| M3 | Groups wore a member's name and id | **partly** | ids are `group:<anchor>`; labels `agent`, `api` (demo), `src +2`, `api +4`, `models +5` (descovo); member chips listed; "also in" empty for groups; the #20 case `api +4 -> scripts +4 (16 imports)` cites 6 lines all importing `src.pipeline.*`/`src.enrichment.*`, both members of `scripts +4`; 88/88 cited lines on the descovo root resolve into the target group. Not fixed: the passport title shows the raw id (N2), the drill of `agent` is titled `routers internals` and of `api +4` `api internals` (N7), and the self build names one group `svarupa` that does not contain `svarupa` (N8) |
| M4 | Drill undiscoverable | **verified** | Chevron single click opens the drill on every tab of all three repos at 4 widths (`walk-*.json chevronDrill`); `#panel-open` "Open in place ›" shown for drillable boxes, hidden for others and for facts/connections; hint reads "click a box for its passport; double-click it, or its ›, to open it in place; ..."; SKILL.md:28-31 and README:35-37 name the gestures. The button's landing is N3 |
| M5 | Dense views unreadable | **partly** | descovo module-deps root 4 boxes, 3 arrows (`shots/descovo-d-module-deps-1440x900-dark.png`); drill into `src`: 26 boxes, 56 arrows, 2715 px wide (`descovo-d-module-deps-tree-drill.png`, carried). Every one of the 76 graph module pairs is drawn at exactly one level, none at two (`datacheck2`). Flat view on demo: 12 boxes, 18 silent arrows. Not fixed: 31 (demo) and 48 (descovo) `imports` labels remain on data-flow and request-flow arrows (N9); architecture root keeps 37 arrows (carried) |
| S1 | Externals band "in a cycle" | **partly** | Externals bands read `external` (3 demo, 7 descovo). But `in a cycle` still spans sinks: demo `/spec/api` band holds `api/config`, `api/schema`, `api/utils`, which have no outgoing edge; `/spec/agent/routers` holds `agent/schema`. See N6 |
| S2 | Drill landing under the header | **verified for chevron and double-click; regressed for the new button** | Chevron drill: crumb top 78-106 px under a 57.6 px header on every tab at 1440, 1280, 1100, 1024. "Open in place": crumb at y -57.6 (demo data-flow 1440x900, descovo architecture 1440x900), 27.4 (descovo module-deps and request-flow 1440x900), 57.5 (architecture 1280x720). See N3 |
| S3 | Passport covered strip, title, boxes | **verified at 1440 and 1280; not at 1100 or 1024** | 0 covered boxes, strip and h2 clear, `.tab` padding 404 px at 1440 and 1280 on every tab; `#panel` scrollWidth 350 = clientWidth. At 1100 and 1024 the media query drops the padding and the card covers the explore bar, the guided strip, the h2 and 1-3 boxes on every tab (N5) |
| S4 | Leading commas | **verified** | 0 matches for `·\s*,\s*/`, `>\s*,\s*/`, `, /available` in both `index.html`; ingress box reads `/available-rules, /batch, /check …`; chapter `02 /available-rules, /batch, /check …` |
| S5 | Band vs edge label collisions | **verified** | `scan-*.json`: 0 cross-element text collisions in 163 + 976 views; 0 labels over boxes, 0 routes through boxes, 0 hidden heads |
| S6 | Data-flow first screen | **verified** | descovo ingress `in:src/api` box top at y 441 and handler `api` on the first screen at 1440x900 and 1280x720 (`descovo-d-data-flow-1280x720-dark.png`). The ingress label itself is N10 |
| S7 | Diagram ids not queryable | **partly** | 12/16 demo and 22/33 descovo root ids answer `get_node` by id (`s7-loop.txt`); `get_neighbors`, `affected`, `shortest_path` answer for `api/routers`, `api/database`. Not answered: every `group:*` (9) and `tree:src` (`match: None`), and `agent`, `api` on demo (`2 nodes match this text`: the module id ties with the compose service's label, `query/__init__.py:93-104`). Nothing a stranger reads (README, SKILL.md, REPORT.md, the passport) says `group:` and `tree:` ids are diagram-only; only design 15.1 mentions `group:<anchor>` |
| S8 | Chapters skipped built services | **verified** | descovo deploy-topology chapters: `01 app 5 boxes, 02 grafana, 03 opensearch, 04 opensearch-dashboards, 05 prometheus`; demo: `01 api, 02 agent` |
| S9 | No keyboard | **partly, as triaged** | Escape closes the passport and clears focus on every tab (one exception: `d-module-deps@1280x720` left a lit state); `.sv-node[tabindex="0"]` on every box (1138 demo, 9825 descovo); Enter on a focused box opens its passport. Tab order is document order: on descovo data-flow 30 box stops after Export SVG, Export PNG, Play story; chapters (`li`) and legend swatches (`span`) are not focusable; Enter on a chevron is impossible (a `<text>`, not focusable); "Open in place" is 5 to 19 Tabs from the box |
| S10 | Documents named things that do not exist | **verified** | Every command in README/SKILL/REPORT run (`log.md`): `--version`, `python -m svarupa --version/--help`, `query get_node/get_neighbors/shortest_path/affected/god_nodes/graph_stats/query_graph`, `--json` on query, `mcp --help`, `setup skill --dest`, `setup ci_github --dest`, `--max-files`, `--lock`, `--diff`, `--drift-base` all exit 0 (or 1 with a coded answer); `svarupa demo --json` exit 2 and no document names it; REPORT.md has no `--json`; README counts six types; `setup skill --dest` refuses a nonexistent dir with `SVA-S-002` and a `fix:` |
| S11 | Header wrapped to 108 px | **partly** | 57.6 px at 1024, 1280, 1440, 1920 on all three repos; repo chip `REPO demo` titled; `not drawn` muted. But the nav is `overflow-x: auto; scrollbar-width: none` and at 1024 the demo hides `request-flow` and `not drawn` entirely, descovo hides `module-deps` (cut to `module-`), `request-flow` and `not drawn`; at 1280 demo shows the fragment `not`. See N4 |
| S12 | "AWS via boto3" | **verified as triaged** | Box `AWS SDK · via boto3`; the `minio` compose box stays separate (carried) |
| C1 | Theme button named the target | **verified** | `☾ dark` / title "switch to the light theme" while dark; `☀ light` / "switch to the dark theme" while light; no-JS page reads `☾ dark` while dark |
| C2 | Internal vocabulary | **verified** | every swatch carries a one-line tooltip; group: "modules that import each other, grouped for reading; a group is not an identity." |
| C3 | Only the first store arrow carried a verb | **verified** | every dashed route in every JSON carries `reads/writes`/`calls`/`publishes`; both demo MongoDB arrows labelled on the architecture root |
| C4 | Head truncation of sublabels | **verified** | 0 sublabels starting with `…` in three repos; 4 (demo) + 1 (descovo) cut from the tail; `engines.py:76-77 keep_tail=False` |
| C5 | Chevron and SRC capsule touching labels | **verified** | same-node text overlaps: 0 in 163 demo views, 1 in 976 descovo views (12278 forced-length labels); chevron gap to label 16.7-77.7 px on every root |
| C6 | Empty directory analyzed | **verified** | `SVA-D-008` exit 1 for an empty dir and for a `.git`-only dir, with a `fix:`; a `README.md`-only dir builds an artifact (exit 0) whose report lists eight named absences |
| C7 | Root module spelled `''` | **verified** | `SVA-R-004 '(repo root), rust-processor, rust-processor/src'` |
| C8 | Stale `_route` docstring | **verified** | `svg.py:335-343` |
| C9 | Tab vs title; "built from ." | **verified** | title `Deploy topology`; sublabel `built from the repository root` |
| C10 | One-item request menu | **verified** | descovo root is `/spec/req:src/api` (29 boxes); demo keeps its 4-story menu |
| C11 | Favicon 404 | **verified** | 0 console errors, warnings, failed requests or HTTP ≥ 400 across 3 repos x 4 viewports |
| C12 | Raw ids in OUT/IN rows | **partly** | rows read `OUT → Gemini API calls` with the id as tooltip. Raw ids remain in the passport title (`group:agent/routers`, `ext:cloud:Gemini API`, `docker-compose.yml#service.agent`), the Connection card summary (`docker-compose.yml#service.api → ext:cloud:Gemini API`) and the path caption (`group:agent/routers → ext:database:MongoDB ← group:api`) |

## New findings

Ranked. Pixel numbers from `getBoundingClientRect` in Chromium 1234 at the named viewport.

### MUST-FIX

**N1. The Deploy topology draws `api -> Gemini API`, an arrow the repository contradicts, and the M2 fix's own claim ("agent's citation is its own") is false.**
Screenshots: `shots/demo-d-deploy-topology-1440x900-dark.png` (two `calls` arrows into Gemini API, one from `agent`, one from `api`), `shots/demo-d-deploy-topology-arrow-passport.png` (the `api -> Gemini API` connection card cites `agent/core/react_agent/agent.py:3`).
Repro: build demo; open deploy-topology; click the dashed arrow leaving `api` at the right. `deployArrows` in `walk-demo.json`: `api -> ext:cloud:Gemini API [calls] agent/core/react_agent/agent.py:3`; `agent -> ext:database:MongoDB` cites `agent/database/mongodb.py:1, api/database/database.py:1, api/database/database.py:2`; `api -> MongoDB` cites the same three lines.
Facts: `docker-compose.yml:15,33` set `dockerfile: Dockerfile.api` and `Dockerfile.agent`; `Dockerfile.api` does `COPY api /app/api` and runs `python -m api.main`; `Dockerfile.agent` does `COPY agent /app/agent`. No file under `api/` imports anything under `agent/` (`grep -rn "^from agent\|^import agent" api/` is empty). The `api` image never contains `agent/core/react_agent/agent.py`, and the `agent` image never contains `api/database/database.py`.
Cause: `derive/system.py:97-99` maps a service to `modules_under(graph, ctx)` for its build context and `:158-171` now gives every module to every such service; `build.py:516 build_context_of` reads `context` and never the `dockerfile:` key or the Dockerfile's `COPY` lines. The decision promoted from M2, "a module stands for every service whose build context holds it", is wrong whenever two services share a context and select subtrees by Dockerfile, which is the demo's shape and the most common reason two services share a context at all.
Why: the view named for deployment now says the API service calls an LLM. #20 M2 was "a missing edge and a mis-cited one"; the fix trades them for two edges, one false, both cited to files the images do not contain. The decision log says a wrong edge is worse than a missing one.

**N2. The passport title is the raw id: a stranger clicks the box labelled `agent` and reads `group:agent/routers`.**
Screenshots: `shots/demo-d-architecture-passport-1440x900.png` (title `group:agent/routers`, then the same string again as the id chip), `shots/descovo-d-module-deps-tree-passport.png` (title `tree:src` over a box labelled `src`), `shots/demo-d-deploy-topology-...` via `walk-demo.json passport.title` = `docker-compose.yml#service.agent`, `ext:cloud:Gemini API`; and `docs/images/demo-architecture.png`, the README's own screenshot, whose passport reads `group:api`.
Measured: `walk-*.json passport.title` equals `data-id` on every tab of all three repos; label-titled only where id and label coincide (`agent/versions`, `src`).
Cause: `viewer.py:777` passes `node.getAttribute('data-id')` as the title to `show(...)`; the C12 fix (`labelOf`, `viewer.py:497-502`) was applied to the OUT/IN rows and not to the title, the Connection summary (`viewer.py` Connection branch, `summary` = `src → dst` ids) or the path caption (`viewer.py:841`).
Why: this is M3's tail. The group box stopped wearing its anchor's name on the canvas, and the first thing the card says is the anchor's name with a prefix. The design decision reads "never wears a member's identity". The README screenshot ships the defect to every reader of the repository page.

### SHOULD-FIX

**N3. "Open in place", the button M4 added, lands the reader under the header (S2 back, on the new path).**
Screenshots: `shots/demo-d-data-flow-open-in-place-1440x900.png` (first visible line is the canvas; crumb and h2 above the fold), `shots/descovo-d-module-deps-tree-drill.png` (title `src module dependencies` at y 75, crumb under the header).
Measured (`walk-*.json openInPlace.crumb`): demo data-flow 1440x900 crumb top -57.6, bottom -37.5, header bottom 57.6, scrollY 304 (the chevron drill of the same box lands at crumb top 78.4, scrollY 168); demo data-flow 1280x720 top -27.5; descovo architecture 1440x900 top -57.6; descovo module-deps and request-flow 1440x900 top 27.4; demo architecture 1280x720 top 57.5 (touching); descovo architecture 1280x720 top -27.5; self architecture 1280x720 top 57.5. The 1100 and 1024 cases land fine because the padding rule is off there.
Cause: `viewer.py:1044` closes the passport, then `:1069` calls `target.scrollIntoView({block:'start'})` while `.tab { transition: padding-left .16s }` (`viewer.py:148`) is still at 404 px; with the tab 404 px narrower the hint wraps to two lines and the guided strip wraps its chapters onto two rows (visible in `shots/descovo-d-architecture-group-passport.png`), so the scroll target is measured against a layout that is 40 to 136 px taller than the one that exists 160 ms later.
Why: the fix for "the way back is hidden exactly when the reader most needs it" holds for the two gestures #20 tested and fails for the one it added, and the button is the gesture a mouse user who read the card will use.

**N4. Below 1440 the header hides tabs with no cue that they exist.**
Screenshots: `shots/demo-header-1024x768.png` (nav ends at `module-deps`; `request-flow` and `not drawn` gone), `shots/descovo-header-1024x768.png` (`module-` cut mid-word), `shots/demo-header-1280x720.png` (fragment `not`).
Measured (`walk-*.json header`): nav scrollWidth 706 vs clientWidth 493 at 1024 and 652 at 1280 on demo (visible tabs 4 of 6 and 5 of 6); descovo 3 of 6 at 1024, 4 of 6 at 1280, 5 of 6 at 1440 (`not drawn` hidden at the width the constants are measured at).
Cause: `viewer.py:110 nav { overflow-x: auto; scrollbar-width: none }`: the overflow is scrollable and invisible. The header is 57.6 px "at every width down to 1024" because the tabs fell off it.
Why: a stranger at 1280 sees five tabs and a stray word `not`; at 1024 the request-flow tab does not exist for them. Trading a second row for a hidden tab is not a fix of S11, it is a different failure of the same bar.

**N5. At 1100 px and below the passport is an overlay again and covers boxes, the strip and the title (S3 regression by media query).**
Screenshots: `shots/demo-d-architecture-passport-1100x800.png`, `shots/demo-d-architecture-passport-1024x768.png`, `shots/descovo-d-architecture-passport-1024x768.png` (card over `src +2`, `jobs +2`, `scripts +4` and the whole guided strip).
Measured (`walk-*.json`, 1100x800 and 1024x768): `.tab` padding 20 px; `guidedCovered`, `h2Covered`, `exploreCovered` true on every tab; covered boxes: demo data-flow `in:agent, in:agent/routers`, module-deps 3 boxes, deploy-topology the clicked box itself; descovo architecture 3 group boxes on every tab at both widths; self module-deps 3 boxes.
Cause: `viewer.py:150 @media (max-width: 1100px) { body:has(aside.is-open) .tab { padding-left: 20px } }` with the aside still `position: fixed; left: 28px; width: 352px`.
Why: the decision promoted from S3 is "the passport is a side panel, not an overlay"; S11's decision claims 1024 as a supported width. Both cannot be true with this rule. A stranger on a 13-inch laptop with a sidebar gets the #20 experience.

**N6. "in a cycle" still labels modules that are in no cycle.**
Screenshot: `shots/demo-d-architecture-chevron-drill-1280x720.png` (`routers internals`: band `IN A CYCLE` over `agent, react_agent, database, routers, schema`).
Measured (`architecture.json`, demo): `/spec/api` band `in a cycle` holds `api, api/config, api/database, api/middleware, api/routers, api/schema, api/utils`; `api/config`, `api/schema`, `api/utils` have no outgoing edge in the view and no `dep` line as source in `architecture.lock`; `/spec/agent/routers` band holds `agent/schema`, a sink. descovo `/spec/root` (9 groups) and `/spec/src` are genuine strongly connected sets.
Cause: `engines.py:403-424 _inferred_layers_cyclic` returns "the ids Kahn could not drain", which is the cycle plus everything downstream of it; the docstring says "exactly the cyclic set" and `engines.py:1051-1052` labels a band from it. S1 removed the externals from the set and left the sinks.
Why: review #8 F5 and the S1 triage both say a label claiming a semantic fact is computed from that fact. `schema` in a cycle is a false sentence about a leaf module, drawn in a band header.

**N7. The drill of a group is titled after the anchor, not the box: `agent` opens `routers internals`; `svarupa` opens `emit internals`; and the subtitle counts externals as modules.**
Screenshot: `shots/demo-d-architecture-chevron-drill-1280x720.png` (box `agent · 5 modules` opened; h2 `routers internals`, meta `7 modules`; the container is labelled `agent`).
Measured (`architecture.json`): `/spec/agent/routers` title `routers internals`, subtitle `7 modules` (5 modules + 2 externals); `/spec/api` `api internals · 8 modules` (7 + 1); descovo `api +4` opens `api internals`, `models +5` opens `models internals`, `jobs +2` opens `jobs internals`; self `svarupa` (emit, layout) opens `emit internals`, `svarupa +6` opens `svarupa internals`. The second-level crumb reads `← routers internals`.
Cause: `architecture.py:682 title=f"{_label(anchor)} internals"`, `:683 subtitle=f"{len(nodes)} modules"` where `nodes` includes `ext_nodes`.
Why: the reader opens a box named one thing and lands on a page named another; this is M3's naming leak one level down, and the count is a wrong number in a title.

**N8. On svarupa itself the two groups are `svarupa +6` and `svarupa`, and the box named `svarupa` does not contain the `svarupa` module.**
Screenshot: `shots/self-d-architecture-1440x900-dark.png`.
Measured (`self/diagrams/architecture.json`): `group:svarupa` label `svarupa +6`, members `scripts, svarupa, svarupa/derive, svarupa/extract, svarupa/lock, svarupa/query, svarupa/setup`; `group:svarupa/emit` label `svarupa`, members `svarupa/emit, svarupa/layout`. Chapters read `01 svarupa +6`, `02 svarupa`.
Cause: `architecture.py:977-1018 top_box_labels`: the common-directory rule names the emit/layout group `svarupa` because both members sit under `svarupa/`; the clash check compares exact strings only, so `svarupa` and `svarupa +6` do not collide.
Why: the README tells a stranger to build this repository first (`uv sync`, `svarupa .`); the first picture they see has two boxes that read as the same name, and the one that is literally `svarupa` is the one without `svarupa` in it. The decision is "a box is named for what it is".

**N9. `imports` is still drawn on 79 arrows; the legend's sentence about solid arrows is not true of the view it sits under.**
Screenshots: `shots/demo-d-data-flow-1440x900-dark-full.png` (10 `imports` labels; legend: "solid arrows are imports (count on hover)"), `shots/descovo-d-data-flow-1440x900-dark.png`.
Measured (`datacheck2`): `imports` labels on default-variant routes: demo data-flow 10, request-flow 21; descovo data-flow 24, request-flow 24 (the "24 identical imports labels" S6 named); architecture and module-deps 0. Solid arrows also carry `handles`, `calls`, `inherits` in the same views.
Cause: `derive/dataflow.py:232 label="imports"`; the M5 fix silenced `architecture.py:_import_edge` only; `viewer.py:1161-1168` writes the legend sentence from the route variants, not from whether the arrows carry text.
Why: the triage says "structural arrows are silent"; two of five tabs still spell the word on every import, and the legend then explains a convention the picture does not follow.

**N10. The descovo ingress box reads `…able-rules, /batch, /check …`, elided at both ends.**
Screenshots: `shots/descovo-d-data-flow-1440x900-dark.png`, `shots/descovo-d-data-flow-1280x720-dark.png`.
Measured: `full_label` `/available-rules, /batch, /check …`, `label` `…able-rules, /batch, /check …` (`data-flow.json` root box `in:src/api`, w 258).
Cause: `engines.py:75 truncate(full, ...)` cuts every label from the head (the path rule); the ingress label is a route list already elided from the tail by `dataflow.py:167`.
Why: the first box of the first flow view, the one S6 moved onto the first screen, has no readable first route.

### CONSIDER

- **N11.** `get_node agent` and `get_node api` on the demo return `2 nodes match this text` because the module id ties with the compose service's label (`query/__init__.py:93-104`, by documented design). The passport shows `agent` as the id and SKILL.md:65 promises `get_node <label> # exact id`. An exact id match could win, or the passport could show a query-ready id. Also: `group:` and `tree:` ids are unqueryable and no user-facing text says so; the passport shows them in the same `code` chip as every real id.
- **N12.** The module-deps root subtitle mixes units: "3 dependencies between parts, 59 inside them" adds to 62 while REPORT.md says 76 module dependencies; the 3 arrows stand for 17 module pairs (`architecture.py:912-921`: `edges` counts arrows, `within` counts pairs). `tree:src` one level down says "27 modules in 26 parts of src; 56 dependencies between parts, 1 inside them", where 25 of the 26 "parts" are single modules.
- **N13.** A `tree:` box is kind `module` (legend `module 4`, chip `MODULE`) while its sublabel says `27 modules`; a singleton group box lists itself as its own member chip (`architecture.py:428-431` sets `members` for every non-role singleton; visible in `shots/descovo-d-architecture-passport-1024x768.png`, `alembic/versions` with member `alembic/versions`).
- **N14.** Keyboard, beyond the carried roving order: chapters (`li.chapter`) and legend swatches (`span.sw-toggle`) cannot be reached by Tab; a keyboard user cannot mute a kind or play the story; the path tool needs Shift-click. Enter on a chevron is impossible (the chevron is a `<text>` with no tabindex), so the keyboard drill is Enter, then 5 to 19 Tabs to "Open in place".
- **N15.** `architecture.py:693-694` docstring: "Complete, and readable at every level". `tree:src` is 26 boxes and 56 arrows on a 2715 x 1872 canvas, cut at the right edge at 1440 (`shots/descovo-d-module-deps-tree-drill.png`). Carried by the triage; the docstring should not claim it.
- **N16.** The descovo data-flow root still has a `reads/writes` label riding a dashed detour along the top edge (`shots/descovo-d-data-flow-1440x900-dark.png`, y 400), the "unattached label on a dashed rectangle" #20 S6 described; it is attached, to an arrow whose ends are 1000 px apart.
- **N17.** `index.html` for descovo grew from 9.66 MB to 13.5 MB (976 views, 140981 DOM nodes); load 626 ms and tab switch 321 ms in Chromium (`loadtime.txt`), fine today, but the module-deps tab alone added 258 views.

## What the review confirmed sound

- **Builds.** demo, descovo, self: exit 0, 0 withheld, 0 errors, 0 warnings. Self built three times byte-identical. `PYTHONHASHSEED=7 LC_ALL=C` rebuilds of demo and descovo `diff -r` clean against the default builds. `--lock` builds produce an artifact byte-identical to the plain build plus `architecture.lock`.
- **Lockfile.** `architecture.lock` (65 lines demo, 221 descovo): 0 occurrences of `group:`, `tree:`, `ext:`, `req:`; modules are directory paths only.
- **Geometry, all 1139 views in a real browser** (`scan-*.json`): 0 cross-element text collisions (was 6), 0 labels over foreign boxes, 0 routes through boxes, 0 hidden arrowheads; 1 same-node overlap in 12278 forced-length labels (descovo, one expanded code view); worst squeeze ratio 0.939 (`api`, `src`).
- **Evidence.** 88/88 cited lines on the descovo architecture root are an import from a file in the source box's member set into a module in the target box's member set (parsed from the source). The #20 case `api +4 -> scripts +4 (16 imports)` is true now. Module-deps: 18/18 (demo) and 76/76 (descovo) graph module pairs drawn at exactly one level; every aggregated arrow's weight equals the sum of its member-pair weights (`scripts -> src` 36 = 10 pairs). The falsehood in N1 is in which box an arrow attaches to, not in the line.
- **Interaction in Chromium, three repos.** Chevron drill and second-level drill work on every drillable tab; crumb returns to the root; hover lights one route and two boxes; search `age`/`dag`/`sva` reports 1/2/2 matches; legend mute recedes 1-2 boxes and 1-3 routes; Shift-click path reads a two-hop caption; chapter 1 lights 3/10/2 boxes and reports `1 / N` with the strip uncovered; card items open a Fact with 8 sources; "Open in place" opens the right view (only the landing is wrong, N3); Escape closes and clears; Enter on a focused box opens its passport.
- **Export.** SVG 26-63 KB and PNG at 2x (774x536 for the 387x268 demo root, 3110x1420 for descovo) for every repo.
- **Light theme** renders (`shots/demo-d-architecture-light-1440x900.png`); the **no-JS** page shows every view (163/976/105 SVGs) and the noscript hint; a directory named `<img src=x onerror=alert(1)>` builds (exit 0), is escaped in the title and every attribute, and opens with 0 dialogs.
- **Console.** 0 errors, warnings, failed requests or HTTP ≥ 400 across 3 repos x 4 viewports (the favicon 404 is gone).
- **Docs and commands.** Every command and flag named in README.md, SKILL.md and REPORT.md exists and runs; `setup skill --dest` writes a file identical to `skills/svarupa/SKILL.md`; `setup ci_github --dest` writes `.github/workflows/svarupa.yml`.
- **Tests.** `pytest -q -p no:cacheprovider` exit 0 (841 pass marks, 1 skip; the `-q` run printed no summary line). Working tree clean before and after; `scripts/mutate_*.py` not run.

## Riskiest remaining untested assumption

That build-context arithmetic is enough to say which code a service ships. N1 shows the M2 decision failing on the only repository with two built services, because `dockerfile:` and `COPY` decide the image, not `context:`. Nothing measured which of the shapes in the wild (one Dockerfile per service with a shared context, `target:` stages, `.dockerignore`) the derivation gets right; until a Dockerfile reader exists, every service arrow in the System view rests on an assumption the acceptance repo already contradicts. Also unverified by me: the 25 mutations of `scripts/mutate_review20.py` (not run, by rule), and JetBrains Mono is still not installed here, so every label was measured in the fallback monospace.

## Triage table

Every finding accepted; two in part, two carried. Fixes in the commit after
this record, each with a pinning test and an entry on
`scripts/mutate_review20.py` (the review #20 and #21 list).

| # | Finding | Rank | Disposition | Why |
|---|---|---|---|---|
| N1 | `api -> Gemini API` drawn from a file the api image never copies; store citations shared across images | MUST-FIX | **Accepted, fixed** | The compose extractor now reads each built service's Dockerfile (`dockerfile:` or the default in the context) and records every `COPY`/`ADD` source with its line (`--from=` copies skipped, globs cut at the wildcard, `.dockerignore` and `target:` not read, stated). A module stands for the services whose image copies it; the context is the fallback only when no Dockerfile can be read. Each store arrow cites the import lines and the COPY line that puts them in the image: demo `api -> MongoDB` cites `Dockerfile.api:15` with its two import lines, and `api -> Gemini API` is gone |
| N2 | Passport title is the raw id | MUST-FIX | **Accepted, fixed** | The card, the Connection summary and the path caption say the box's label; the id stays in the chip. The README screenshot is regenerated |
| N3 | "Open in place" lands under the header | SHOULD-FIX | **Accepted, fixed** | No transition on the tab's padding, so a scroll measures the layout that exists; the script writes the header's measured height into `--header-h` and views scroll to land below it. Measured: crumb at y 76 to 122 under headers of 58 and 104px at 1024, 1100, 1280 and 1440 |
| N4 | Nav hid tabs at 1024 and 1280 | SHOULD-FIX | **Accepted, fixed** | Every tab is always visible (6/6 at every width measured): the source-base label goes first below 1440, the input narrows, and only then does the header take a second row (104px below 1100, and on descovo at 1280, where the repository name is long). Visible tabs beat a one-row header; S11's decision is restated that way |
| N5 | Passport an overlay again at ≤1100 | SHOULD-FIX | **Accepted, fixed** | A 280px side panel with the tab making 328px of room below 1100 (the overlay rule is gone). Measured at 1024 and 1100: 0 covered boxes, strip and title clear |
| N6 | "in a cycle" labelled sinks | SHOULD-FIX | **Accepted, fixed** | Cycle membership is reachability to self, not "what Kahn could not drain"; a fallback floor holding a cycle and what hangs below it reads `level N · cycle inside`, never `in a cycle` |
| N7 | Group drill titled after the anchor; externals counted as modules | SHOULD-FIX | **Accepted, fixed** | `agent internals · 5 modules, 2 external` |
| N8 | `svarupa` beside `svarupa +6` on the self build | SHOULD-FIX | **Accepted, fixed** | A shared directory names a group only when no other box holds a module under it; label clashes compare the stem before ` +N`. The self build reads `svarupa +6` and `emit +1` |
| N9 | 79 `imports` labels in the flow views | SHOULD-FIX | **Accepted, fixed** | The flow derivers' import arrows are silent like the others |
| N10 | Ingress label elided at both ends | SHOULD-FIX | **Accepted, fixed** | The route list is cut to 28 characters, what the box fits, with a clean ` …` when whole routes are dropped: `/available-rules, /batch …` |
| N11 | `get_node agent` ambiguous; `group:`/`tree:` undocumented | CONSIDER | **Accepted, fixed** | An exact id names one node (ids are unique by construction); a label shared by two nodes stays ambiguous. The passport marks a `group:`/`tree:` box "diagram box, not a graph node" and SKILL.md says so |
| N12 | Subtitle mixed arrows and pairs | CONSIDER | **Accepted, fixed** | One unit: "5 module dependencies between boxes as 1 arrows, 9 inside the parts" |
| N13 | `tree:` box kind `module`; singleton lists itself | CONSIDER | **Accepted, fixed** | Parts are kind `group` (the legend's help now says "by community or by directory"); only groups list members |
| N14 | Chapters and swatches unreachable; no Enter on a chevron | CONSIDER | **Accepted in part** | Chapters and legend swatches are focusable and answer Enter. Enter on the chevron and a roving order stay carried; the keyboard drill is Enter, then the Open button |
| N15 | "readable at every level" docstring | CONSIDER | **Accepted, fixed** | Says "readable at the root" and names the dense case |
| N16 | `reads/writes` on a 1000px detour | CONSIDER | **Carried** | The corridor verb settles near an end of its edge by rule; this edge's ends are 1000px apart. Bundling corridor edges is a router decision of its own |
| N17 | Artifact grew to 13.5 MB | CONSIDER | **Carried** | Pre-rendered expansions are linear in drillable boxes by design; load and tab switch measured under a second. A size gate or lazy views would be a design decision |

## Promoted to the decision log

1. **An image holds what its Dockerfile copies, not what its context permits** (N1).
2. **A card is titled by the label the reader clicked; ids are chips** (N2).
3. **A scroll measures the layout that exists: nothing animates the layout between a state change and a scroll** (N3).
4. **Navigation is never hidden to keep a height** (N4).
5. **A decision holds at every supported width** (N5).
6. **"In a cycle" is membership of a cycle** (N6).
7. **A name chosen for a box follows it everywhere** (N7, N8).
8. **An exact id is never ambiguous** (N11).

## Measured after

```
768 passed, 1 skipped, 2 xfailed; ruff clean; pyright strict 0 errors
scripts/mutate_review20.py: 37 mutations (24 from #20, 13 from #21), all
  caught (two #21 survivors, N8 and N9, were missing tests and are pinned)
demo and descovo: 0 withheld, 0 SVA-G-013/015, byte-identical across
  PYTHONHASHSEED 1/42 and LC_ALL C/tr_TR.UTF-8; svarupa on itself exit 0 x3
demo deploy topology: agent -> Gemini API [Dockerfile.agent:15,
  agent/core/react_agent/agent.py:3]; agent -> MongoDB [Dockerfile.agent:15,
  agent/database/mongodb.py:1]; api -> MongoDB [Dockerfile.api:15,
  api/database/database.py:1-2]; no api -> Gemini API
self build root: `svarupa +6`, `emit +1`; demo drills `agent internals ·
  5 modules, 2 external`, `api internals · 7 modules, 1 external`
headless Chromium, demo and descovo at 1024x768, 1100x800, 1280x720,
  1440x900: every tab visible (6/6); header 58px at 1440 and on the demo
  at 1280, 104px below 1100 and on descovo at 1280; passport titled `api` /
  `api +4`, 0 boxes and no strip covered at every width (card 280px below
  1100); "Open in place" lands with the crumb at y 76-122 under headers of
  58 and 104
```
