# Review #24: confirmation pass on the #23 fixes (`34e1759`)

**Date:** 2026-09-09
**Reviewer:** Fable (adversarial), independent of the author's #23 triage
**Fixes:** the commit following this record
**Scope:** every #23 finding (F1 to F9, C1 to C7) re-verified against what a person sees in headless Chromium and what the artifact files and CLI say; regressions the fixes could have introduced; a stranger's minute on every tab. Five repositories built from the archived commit (demo, descovo-data-core, svarupa itself, boxcricket_umpier, mcp-finnhub; all exit 0, 0.4 to 2.5s), 1364 views geometry-scanned at 1440x900, every tab walked at 1024/1100/1280/1440 in both themes, 568 screenshots, 26,611 citations checked against the source files, a real `mouse.dblclick` sent to every drillable root box at two viewports in two panel states (360 double-clicks on 90 drillable boxes, plus second-level and empty-canvas cases), card titles read on 47 chapters, 47 story steps, 209 arrows and 164 boxes, `uv sync` + `pyright` + `ruff` + `pytest` from the archive.
**Artifacts:** `/tmp/review24/` (`shots/`, `exports/`, `dbl-*.json`, `cards-*.json`, `lab.json`, `verbs24-*.json`, `walk-*.json`, `stranger-*.json`, `scan-*.json`, `cited.txt`, `out/*.stdout|stderr`, `log.md`). Source read only under `/tmp/review24/src`; every file:line below is in that tree.

## Verdict

**Ship it? No, but only one fix stands in the way:** the double-click drill that #23 named first is now fixed for the case the fix targeted (second click on the empty scroller) and still fails on every drillable box that has another box 192px to its left, which is 3 of 11 boxes on the demo's data-flow tab and 1 of 25 on descovo's at 1440 (4 failures in 180 real double-clicks with the passport closed, 0 in 180 with it open, 0 at 1024), where the stranger who does what the hint says gets a different box's passport instead of the drill; everything else #23 asked for is fixed as a person sees it (0 raw ids on 467 card openings, 0 citations to a line of an empty file in 26,611, REPORT.md equal to `graph.json` and to the drawn boxes on all five repos, `.` as the root id, bare `svarupa` exits 2, the README development loop green), nothing regressed (geometry numbers identical to #23 across 1364 views, byte-identical rebuilds under `PYTHONHASHSEED=7 LC_ALL=C` on demo and descovo, 0 console errors), and the remaining faults are the README's stale screenshot and small wording items.

## Part A: the #23 findings

Status: **verified** (measured, holds everywhere tested), **partly** (holds on the path the fix targeted, fails on another), **not fixed**, **regressed**. One measured line each.

| # | Finding | Status | Evidence |
|---|---|---|---|
| F1 | Double-click cannot open a box (first click moves it) | **partly** | Real `mouse.dblclick` at the centre of every drillable root box, passport closed (`dbl-*.json`): demo 1440 architecture 2/2, request-flow 4/4, **data-flow 8/11**; demo 1024 2/2, 11/11, 4/4; boxcricket 3/3 and 3/3; mcp-finnhub 2/2 and 2/2; self 2/2 and 2/2; descovo 1440 architecture 12/12, **data-flow 24/25**, module-deps 4/4, request-flow 25/25; descovo 1024 12/12, 25/25, 4/4, 25/25. In total 176 of 180 double-clicks drilled (7 of them into a child view that is too large to embed, e.g. `/spec/src/api//flow`, by design). With the passport already open: every box on every repo at both widths. Second level (a drillable box inside the expansion): demo, mcp-finnhub open `//expanded`; self's `scripts` opens its own view `/spec/scripts//flow` (too large to embed, by design). Double-click on the empty scroller: view unchanged and panel closed on every tab of every repo. The 3 demo failures: after the first click the `.tab` padding goes 20 to 404px and `elementFromPoint` at the double-click point is `in:agent`, `in:agent/routers`, `in:api` (the ingress box drawn left of each module); the view stays `/spec/root`, the passport shows `/, /health` (`shots/demo-d-data-flow-1440-dbl-closed-FAIL-3.png`). See N1 |
| F2 | Raw ids as card titles on Play story, chapter, Connection | **verified** | `cards-*.json`: 47 chapter clicks, 47 Play-story steps (sampled every 400ms through the whole story on every tab), 209 arrow clicks and 164 box clicks across the five repos: 0 titles matching `group:`, `tree:`, `ext:`, `in:`, `req:` or `#service.`. Story titles `MongoDB`, `agent`, `Gemini API`, `api` (demo), `src +2`, `api +4`, `scripts +4` (descovo), `components +3`, `store +1`, `svarupa +6`, `emit +1`, `mcp_finnhub +3`. Connection card: title `agent → Gemini API`, sub `calls`, summary `an arrow in this view: calls` (`shots/demo-d-architecture-connection-card.png`); every one of the 209 arrow titles equals `<src label> → <dst label>` and the verb appears in the card for 76/76 labelled arrows; deploy arrows read `agent → api` / `depends on`, not `docker-compose.yml#service.agent` |
| F3 | 259 citations to line 1 of empty files | **verified in the artifact a person sees; partly in JSON and CLI** | `cited.py`: 26,611 evidence entries (1136 + 11,247 + 8961 + 898 + 4369) in all diagram JSONs and `graph.json`; 0 cite a line ≥ 1 of a 0-line file (was 51 + 208); 387 cite `(0, 0)` (demo 69, descovo 317, self 1: `tests/__init__.py`), all on files that are empty; 0 `end_line` past EOF. `data-evidence` on the demo `api` box: `api/config/settings.py:1 … api/main.py:1 api/__init__.py` (no `:line`); in 907 multi-ref attributes 0 list an empty file before a real line. Passport: `api/__init__.py (empty file)` with `href=…/api/__init__.py` (no `#L`), after the real lines. `get_node api`: `api  [api/__init__.py:0, api/main.py:1]`. **But** the diagram JSON and the CLI keep the sorted order with the empty file first (138 evidence lists on demo and descovo, e.g. `architecture.json api [__init__.py 0, main.py 1, main.py 79]`), and the CLI prints it as `:0` (`query/cli.py:89,101`). See N3 |
| F4 | Deploy verbs dropped under a legend that promised them | **partly** | `lab.json`: demo deploy root 4 of 5 labelled routes drawn (was 3; missing `api -> redis depends on`, a solid arrow), legend `dashed arrows carry their verb`, true (every dashed one has its verb). descovo deploy root **2 of 6 drawn, unchanged from #23** (`app -> postgres`, `app -> redis` dashed without a verb; `grafana -> prometheus`, `opensearch-dashboards -> opensearch` solid without), legend now `dashed arrows carry their verb (some only in the passport)`, true (`shots/descovo-deploy-topology-1440x900-dark.png`). Across all 1364 views the legend sentence is false in 3 expanded views (see N4). The slide put the demo `api -> MongoDB reads/writes` 48px from `api`, directly under the solid `api -> redis` line (N8) |
| F5 | REPORT.md and stdout counted a different graph | **verified** | REPORT.md line 9 vs `svarupa query graph_stats`: demo 184/194 = 184/194; descovo 2370/3064; self 1663/4251; boxcricket 299/254; mcp-finnhub 1664/1994; all equal. Stdout: `code graph: 132 symbol and file nodes, 110 edges, 13 modules, 18 module deps`; REPORT explains `(132 code symbols and files, 110 edges between them; the rest are modules, routes, externals and rationale)` |
| F6 | Boxes column counted routing waypoints | **verified** | REPORT Boxes vs `.sv-node` summed over the plain (non-expanded) views in the browser (`scan-*.json`): demo 110/109/5/12/125 = 110/109/5/12/125; descovo 763/625/10/723/624 equal; self 484/9; boxcricket 76/6; mcp-finnhub 282/1/8; 17 of 17 diagrams equal, root module-deps 12 (was 21). `report.py:187` subtracts `c.waypoints` |
| F7 | Root module box id was the empty string | **verified** | No `""` id in any of the 17 diagram JSONs; `.` present in boxcricket's; `get_node .` answers `matched_by: id … kind=module`, 4 `contains` edges; passport chip `.` with title `(repo root)`; lockfile line 12 `module\t.`; a real double-click and Shift+Enter on the box open `/spec/root//.//expanded`. Small mismatch: the box says `5 files` (`attrs.files = 5`) and cites and contains 4 (N9) |
| F8 | Bare `svarupa` scanned the cwd | **verified** | From an empty dir: `usage: svarupa [-h] … path` + `error: the following arguments are required: path`, exit 2, directory still empty. `--help` names `setup`, `query` and `mcp` in its epilogue |
| F9 | `uv sync && uv run pyright svarupa` failed | **verified** | In the archive: `uv sync` exit 0; `uv run pyright svarupa` `0 errors, 0 warnings, 0 informations`, exit 0; `uv run ruff check svarupa` clean; `uv run pytest` 777 passed, 2 skipped, 2 xfailed in 38.6s, exit 0 (the triage's "778 passed, 1 skipped" differs by one environment-dependent skip) |
| C1 | `in:`/`req:` boxes lacked the diagram-only chip | **verified** | `in:agent`, `req:agent` (demo), `in:src/api` (descovo) carry `diagram box, not a graph node`; SKILL.md:80-82 names all four prefixes |
| C2 | One refusal without a `fix:` line | **verified** | File as `--out`: `ERROR SVA-E-001: '…README.md' exists but is not a directory` + `fix: Pass a directory to --out, or move the file out of the way.`, exit 1 |
| C3 | CI workflow installs an unpublished version | **verified** | `setup ci_github` third `next:` line: `The workflow installs svarupa from PyPI; until the package is published there, point its uv tool install line at a checkout of svarupa.` |
| C4 | Function names cut from the head | **verified** | 0 box labels beginning with `…` in any of the five `index.html`; names now end `_eval_person_employment_type…`, `_navigability_after_withhold…`; paths still keep their tail (`/available-rules, /batch …`) |
| C6 | Module-deps subtitle wording | **verified, one slip** | descovo root: `30 modules in 4 boxes, 1 drillable; 76 dependencies: 17 between boxes (3 arrows), 59 inside the parts`; two expanded views read `1 dependencies: 1 between boxes (1 arrow)` (N10) |
| C7 | `get_neighbors` listed `contain` edges | **verified** | `get_neighbors api/routers`: 4 import rows out, 1 in, `hidden: contain edges (ask with --relation contain)`; `--relation contain` lists the 4 files |
| C5 | Escape leaves a lit state (carried) | **carried, still reproduced** | `walk-*.json escapeClearsFocus: false` on deploy-topology (demo 1440; descovo at all four widths) and module-deps (self, boxcricket, mcp-finnhub at all four widths) |

Of 16: 12 verified, 3 partly (F1, F3's JSON/CLI order, F4's descovo canvas), 1 carried (C5). Nothing regressed.

## Part B: regressions and the stranger's minute

**Geometry scan, all views of all five repos at 1440x900 dark** (`scan-*.json`): demo 163 views, descovo 976, self 106, boxcricket 33, mcp-finnhub 86 = 1364. Cross-element text collisions 0; same-node overlaps 1 (descovo, known); labels over foreign boxes 0; routes through boxes 0; hidden arrowheads 0; forced-length labels outside 0.94-1.06 of natural: 69/1692, 158/12278, 41/1183, 12/224, 5/835. Every number identical to #23.

**Verb distance** (arc distance along the route from the label's nearest point to the nearer box; every drawn verb in every view). With the previous reviewer's script unchanged: demo 197 labels max 226, boxcricket 31 max 96, mcp-finnhub 77 max 212, self 473 max 274 (2 over 240), descovo 1857 max 316 (5 over 240). The over-240 cases are all in expanded views, and inspecting one (`shots/descovo-far-scripts-sqldb.png`, `reads/writes` on `scripts -> SQL database`) showed the label 112px from its box with the script reporting 227px off its own path: the script maps the label through the outer SVG's matrix while the child canvas has its own transform. Re-measured through `path.getScreenCTM()` (`verbs24-*.json`): **demo 226, boxcricket 56, mcp-finnhub 212, self 232, descovo 240; 0 labels farther than 240 from both boxes on any repo**. #23's "self 352 / 274" was partly this artifact.

**Byte identity.** `PYTHONHASHSEED=7 LC_ALL=C` builds of demo and descovo (with `--lock`) `diff -rq` clean against the default builds, lockfile included; self built twice byte-identical; demo with and without `--lock` differ only by the lockfile.

**Interaction walk, five repos at 1024/1100/1280/1440 dark** (`walk-*.json`): chevron single-click drills on every drillable tab, crumb 18 to 190px below the header; Open in place lands the same view; second-level drill (`← agent internals`, `← src component flow`, `← src module dependencies`); crumb returns to the root; Escape closes the card everywhere (lit state left on two tabs, C5); hover lights 2 boxes + 1 route; Shift-click path `path (2 hops, undirected): agent → MongoDB ← api`; legend swatch mutes 1-2 boxes and 1-3 routes; chapter click lights 2-10 boxes, `1 / N`, strip uncovered; Fact card 1-8 sources; export SVG/PNG on every root and the largest view (descovo data-flow 1473x2716 → SVG 78,570 B in 108ms, PNG 2946x5432 in 1197ms); no-JS page 163/976/106/33/86 SVGs with the `noscript` hint; theme button `☾ dark` / `☀ light`; 0 console errors, page errors or failed requests in any run (dbl, cards, walk, stranger, scan, lab, kb).

**Keyboard** (`kb.js`, new since #23's tree): Enter on a focused drillable box opens its passport; **Shift+Enter drills** (`/spec/root` → `/spec/root//group:agent/routers//expanded`, `/spec/tree:src`, `/spec/root//.//expanded`, `/spec/root//req:agent//expanded`) and leaves `document.activeElement` on `BODY` (N6); Enter on a chapter lights its boxes and opens the card; Enter on a swatch mutes (2-4 elements) but the swatch has no `aria-pressed` (N7).

**Search** on the architecture root of each repo: full id 1 match, partial id 1 match (2 on mcp-finnhub), label 1-2, upper-case the same, `zzqqxx` 0 with caption `0 matches`.

**Docs vs artifact.** README.md, SKILL.md and the on-page hint agree with each other and with the CLI (`svarupa <path>`, `--out`, `--lock`, `--diff`, `--drift-base`, `query` x 7, `mcp`, `setup skill|ci_github`, `python -m svarupa`, Enter / Shift+Enter, double-click, chevron, Open in place); `setup skill` writes SKILL.md byte-identical to `skills/svarupa/SKILL.md`; `<title>` present on 1221/1221 nodes and 749/749 routes of the demo (the "citations on hover" promise). **The README's screenshot does not agree with the artifact** (N2).

## New findings

Ranked. Pixel numbers from `getBoundingClientRect` in Chromium 1234 at 1440x900 unless stated.

### MUST-FIX

**N1. Double-click still fails when the shifted canvas puts another box under the second click (F1 residual).**
Screenshots: `shots/demo-d-data-flow-1440-dbl-closed-FAIL-3.png` (after a real double-click on `agent/routers`: root view, passport titled `/, /health`), `-FAIL-5.png` (`api`), `-FAIL-9.png` (`api/routers`).
Repro: demo, data-flow, passport closed, `mouse.dblclick` at the centre of `agent/routers` (577.5, 449.6). Measured (`dbl-demo.json`): the first click opens the passport, `.tab` padding 20 → 404px, the canvas moves 192px right, and `elementFromPoint` at the same point is now the `in:agent` box (the ingress box drawn immediately left of each handler module); the second click opens *its* passport; the view stays `/spec/root`. Same for `api` (lands on `in:agent/routers`) and `api/routers` (lands on `in:api`). At 1024 the 154px shift lands on the section background and all 11 drill. descovo: the one module with an ingress box, `src/api`, fails the same way at 1440 (second click on `in:src/api`, `shots/descovo-d-data-flow-1440-dbl-closed-FAIL-4.png` is its passport-open twin, which drills); the other 65 drillable boxes on descovo drill at both widths. So the failure is exactly "a drillable box with another box in the 154 to 192px band to its left", which the data-flow layout produces for every handler module that has routes.
Cause: `viewer.py:818` `if (node.classList.contains('sv-node')) lastBoxClick = { node: node, at: Date.now() };` runs on the second click too and replaces the remembered drillable box with the non-drillable one it landed on; `viewer.py:505` then finds no `data-child` and returns. The pinning test (`tests/js/viewer_harness.js:275`) dispatches the `dblclick` on the scroller, which is the one landing the fix handles.
Why: the hint on every tab, README:36 and SKILL.md:29 promise the gesture; the data-flow tab is where every module has a neighbour 192px to its left. The fix is small (do not overwrite a pending drillable `lastBoxClick` within the 700ms window, or resolve the `dblclick` from the first click's node), but the promise is still broken on the second tab a demo reader opens.

### SHOULD-FIX

**N2. The README's screenshot shows the bug this commit fixed.**
`docs/images/demo-architecture.png` (last changed in `3907f57`, review #21; not in `34e1759`): VERIFIED SOURCE lists `api/__init__.py:1`, `api/config/__init__.py:1`, `api/database/__init__.py:1` first, and the legend reads `dashed arrows carry their verb`. The built artifact lists `api/config/settings.py:1` first and `api/__init__.py (empty file)` last, and its legend reads `dashed arrows carry their verb (some only in the passport)` (`shots/demo-d-architecture-1440x900-light.png`). The README's defining constraint ("a claim you can click through to the source line that proves it") is illustrated by a picture of three claims that no longer exist. Regenerate the image from this build.

**N3. Diagram JSON and the CLI still put the empty file first, and the CLI writes it as `:0`.**
Repro: `svarupa query demo get_node api` → `api  [api/__init__.py:0, api/main.py:1]`; `diagrams/architecture.json` box `api` evidence `[api/__init__.py 0, api/main.py 1, api/main.py 79]`; 138 evidence lists on demo and descovo have an empty file before a real line (`cited.txt` run). The viewer reorders at render time (`data-evidence` and the passport are right) so the three surfaces disagree, and `:0` (`query/cli.py:89,101` `f"{e['file']}:{e['start_line']}"`) reads as line 0 to an agent or a person. The decision log entry for F3 says the empty file "follows the module's real lines"; make `module_evidence` (`derive/base.py:369`) sort empties last so JSON, CLI and viewer agree, and print the CLI brief the way the passport does.

### CONSIDER

- **N4.** The legend sentence `dashed arrows carry their verb` is false in 3 expanded views (`lab.json legendTrue=false`): demo `/spec/root//group:api//expanded` (3 of 4 dashed routes drawn; the parent-scope `group:api -> MongoDB`, a 100px path, has none), descovo `//group:src/api//expanded` (9/17) and `//group:src/search//expanded` (7/16). The check at `viewer.py:1217` runs over the canvas's routes; the ghosted parent-scope routes in an expansion are drawn without their verbs.
- **N5.** A click on a drillable box followed within 700ms by a double-click on the empty scroller opens the box (`dbl-*.json emptyAfterBoxClick.drilled: true` on demo architecture and request-flow, boxcricket, mcp-finnhub, self). This is the mechanism of the F1 fix (`viewer.py:500-508`), and a double-click on empty canvas with nothing pending does nothing, but the window is not bound to where the second gesture lands.
- **N6.** After Shift+Enter drills, focus is on `BODY` (`kb.js active: BODY` on all four cases); a keyboard reader has to Tab from the page top to reach the new view's crumb or boxes.
- **N7.** Legend swatches toggle on Enter but carry no `aria-pressed` (`kb.js pressed: null`), so a screen reader cannot tell muted from not.
- **N8.** The slid `reads/writes` for `api -> MongoDB` on the demo deploy topology sits at (780, 435), 48px from `api` and directly under the solid `api -> redis` line at y≈420 (`shots/demo-deploy-topology-1440x900-dark.png`); it reads as redis's verb. Placing a verb near its box is right; placing it in another arrow's corridor is the next thing to check.
- **N9.** boxcricket root box `(repo root) · 5 files` (`attrs.files = 5`) cites and `contains` 4 (`eslint.config.mjs`, `next.config.ts`, `postcss.config.mjs`, `vitest.config.ts`); the fifth architecture-eligible root file has no extractor and is counted but not shown (`architecture.py:353` comment).
- **N10.** Two descovo expanded module-deps subtitles read `1 dependencies: 1 between boxes (1 arrow)`.
- **N11.** The Connection card's summary line `an arrow in this view: calls` says the verb twice (sub line `calls` above it); the sentence is filler around a word already shown.
- **N12.** The previous reviewer's verb-distance script (`verbs.js`, kept in `/tmp/review23`) mismeasures labels in child canvases (outer-SVG matrix applied to a transformed path); `verbs24.js` corrects it. Worth knowing before anyone quotes "352px" from #23 again.

## What the review confirmed sound

- **Builds and determinism.** Five repositories exit 0 in 0.36 to 2.52s; demo and descovo byte-identical under `PYTHONHASHSEED=7 LC_ALL=C` (lockfile included); self rebuilt byte-identical.
- **Evidence integrity (F3).** 26,611 citations checked against the files: 0 to a line ≥ 1 of an empty file, 0 past EOF, 387 `(0, 0)` citations all on 0-line files; the passport shows `(empty file)` and links without `#L`; the rendered order puts real lines first in 907 of 907 multi-line attributes.
- **Cards (F2).** 467 card openings (47 chapters, 47 story steps, 209 arrows, 164 boxes) titled by labels; 209 of 209 Connection titles equal `src → dst` by label; the verb in the card for 76 of 76 labelled arrows.
- **Reports (F5, F6).** REPORT.md equals `graph_stats` on 5 of 5 repos and equals the drawn box count on 17 of 17 diagrams.
- **Root id (F7).** `.` in the diagram JSON, the passport chip, the lockfile and `get_node`; drills by double-click and by Shift+Enter.
- **CLI (F8, F9, C2, C3, C7).** Bare command exits 2 and writes nothing; `--help` names the subcommands; fresh `uv sync` then pyright 0 errors, ruff clean, pytest green; refusal has its `fix:` line; CI `next:` says the package must be published; `contain` edges hidden unless asked.
- **Geometry, 1364 views.** 0 cross-element text collisions, 0 labels over foreign boxes, 0 routes through boxes, 0 hidden arrowheads; 1 same-node overlap; every number equal to #23. Every drawn verb within 240px of one of its boxes on all five repos (corrected measurement).
- **Interaction.** Real double-click drills on 4 of 5 repos at both widths and on every box with the passport already open; chevron, Open in place, crumb, second-level drill, Escape, hover, Shift-click path, legend mute, chapters, Fact cards, search, export SVG/PNG, Enter, Shift+Enter, theme toggle, no-JS page; 0 console errors in 5 repos x 4 widths x 2 themes.
- **Docs.** README, SKILL.md, the hint and `--help` agree on every command and gesture; `setup skill` output byte-identical to the source skill; the five #23 decisions are in the design log.

## Riskiest remaining untested assumption

That the 700ms `lastBoxClick` window is the right model of a human double-click. It was verified here with Playwright's `dblclick`, which sends the two clicks a few milliseconds apart at one exact point; a hand double-clicks with 100 to 400ms between clicks and a few pixels of drift, and on a trackpad or a touch screen the first click's layout shift is visible as a jump before the second click lands. N1 shows the model already breaks when the second click lands on a neighbour; whether it also breaks on a slow or drifting double-click, or on a tap, has not been measured by any review, because no review has used a pointing device. Also unverified by me, by rule: `scripts/mutate_*.py`; JetBrains Mono is still absent here, so every label width was measured in the fallback monospace.


## Triage table

| # | Finding | Rank | Disposition | Why |
|---|---|---|---|---|
| N1 | Double-click failed when the second click landed on a neighbouring non-drillable box | MUST-FIX | **Accepted, fixed** | The click that completes the pair no longer overwrites a pending drillable click within its window. The harness sends the second click to a neighbouring box; the real-`dblclick` walk is the reviewer's evidence for the rest |
| N2 | README screenshot showed the fixed bug | SHOULD-FIX | **Accepted, fixed** | Regenerated from this build |
| N3 | Diagram JSON and CLI kept the empty file first and printed `:0` | SHOULD-FIX | **Accepted, fixed** | One order for citations everywhere, `evidence_order` in the model, used by `module_evidence`, the renderer and graph.json; the CLI brief prints `pkg/__init__.py (empty file)` |
| N4 | The legend's verb sentence was false in three expanded views | CONSIDER | **Accepted, fixed** | An expansion's legend hedges: its ghosted parent arrows carry no verb by design |
| N5 | A pending drillable click plus a double-click on empty canvas opens the box | CONSIDER | **Rejected, recorded** | That sequence is a double-click whose second click drifted; 700ms after clicking a drillable box, a double-click anywhere on the canvas is the gesture the page names. Nothing pending, nothing happens |
| N6 | Focus stayed on the body after a keyboard drill | CONSIDER | **Accepted, fixed** | Focus moves to the opened view's crumb |
| N7 | Swatches had no `aria-pressed` | CONSIDER | **Accepted, fixed** | |
| N8 | A slid verb sits under another arrow's line | CONSIDER | **Carried** | The mask is clear of the line by rule; nearness to a foreign line is a further rule for the settle |
| N9 | Root box says 5 files, cites 4 | CONSIDER | **Carried** | The fifth is an eligible file with no extractor; the count is the module's, the citations are what could be cited |
| N10 | `1 dependencies` | CONSIDER | **Accepted, fixed** | |
| N11 | Connection summary repeated the verb | CONSIDER | **Accepted, fixed** | The summary is the note only when it says more than the verb |
| N12 | The #23 verb-distance script mismeasured child canvases | CONSIDER | **Noted** | #23's "352px" was an artifact; the corrected measurement shows 0 verbs farther than 240px |

The reviewer's report left three `<<DESCOVO_F1...>>` placeholders unfilled;
its closing summary gave the numbers (360 real double-clicks on 90 boxes, 4
failures, all the same shape), which are substituted above. The harness case
added for N1 covers that shape on every fixture.

## Promoted to the decision log

1. **A remembered gesture is not overwritten by the gesture that completes it** (N1).
2. **One order for citations, in every surface** (N3).

## Measured after

```
<<MEASURED>>
```
