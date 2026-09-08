# Review #20: product readiness at the diagram level (`5e599a1`)

**Date:** 2026-09-07
**Reviewer:** Fable (adversarial)
**Fixes:** the commit following this record
**Scope:** what a stranger sees. Both acceptance repositories built (exit 0, 0 withheld), svarupa built on itself (crashed), fresh-venv install, hostile inputs, 338 screenshots from a headless Chromium at 1440x900, 1920x1080, 1280x720 and 2560x1440 in both themes, a geometry scan of every one of the 883 views (163 demo, 720 descovo), and every arrow of every root view checked against the lines it cites.
**Artifacts:** `/tmp/review20/` (`shots/`, `exports/`, `measure-*.json`, `scan-*.json`, `debug*-*.json`, `misc.json`, `log.md`).

## Verdict

**Not product ready.** The one sentence for the user: *on the two-service demo the pictures are Archify-class, but the tool crashes on its own source tree, the only way to drill is a double-click that nothing on the page mentions, and on the eight-service repo the architecture and module-deps tabs are wiring closets whose arrows name modules the cited lines never import.* Five things are MUST-FIX before a stranger opens this: a use-after-free crash in the rationale scanner (exit 138/139, no diagnostic), a deploy-topology arrow that is missing for one service and mis-cited under the other, community groups that borrow a member's name and id so that "src/api → scripts (16 imports)" is drawn from six lines none of which import anything under `scripts/`, an undiscoverable drill gesture (design §5.1's chevron click is not built), and dense views that no longer read as diagrams. Everything below the diagram surface that earlier reviews certified still holds: byte identity across seeds, zero routes through boxes, zero hidden arrowheads, zero label-over-box, escaping, the refusal codes, and the query/MCP plumbing.

## Findings

Ranked. Each names the screenshot, the reproduction, why a first-time reader cares, and the svarupa line I believe is responsible. Pixel numbers come from `getBoundingClientRect` in Chromium 1234 at the named viewport.

### MUST-FIX

**M1. Building svarupa on itself crashes with a bus error or segfault; nothing is printed.**
Repro: `./.venv/bin/svarupa /Users/vishvdeep/Documents/svarupa --out /tmp/review20/self` → exit 139, then 138 on rerun; stdout and stderr empty (buffered output lost); `/tmp/review20/self/` left holding `.svarupa-artifact` and an empty `diagrams/`. `python -X faulthandler` places it in `svarupa/extract/rationale.py:129 _walk` while "Garbage-collecting". Bisect (`bisect_crash.py`): seven files crash in isolation (`svarupa/detect.py`, `extract/base.py`, `layout/engines.py`, `derive/architecture.py`, `derive/base.py`, `derive/dataflow.py`, `tests/test_emit.py`), signals 10 and 11. Cause: `rationale.py:133` and `:159` do `Parser(...).parse(data).root_node`, dropping the `Tree` while its nodes are still walked; py-tree-sitter 0.26 nodes do not own the tree. Verified (`crash_repro.py`): the same walk with the tree bound to a local exits 0 on all four files tried; as written, 138 on all four. The `except (RecursionError, ValueError)` at `rationale.py:203` cannot catch a signal.
Why: the README's own `uv run` flow builds this repository; a stranger's first `svarupa .` on any Python tree of this shape dies without a code, violating the "one structured diagnostic, never a traceback" rule, and this one is worse than a traceback. Also a determinism hazard: GC timing decides whether a build finishes.

**M2. Deploy topology drops the `api` service's MongoDB edge and cites `api`'s file under `agent`.**
Screenshot: `shots/demo-d-deploy-topology-1440x900-dark.png` (api has arrows only to redis; MongoDB hangs off agent alone).
Repro: build demo; open deploy-topology; hover or click `agent → MongoDB`: its verified sources are `agent/database/mongodb.py:1`, `api/database/database.py:1`, `api/database/database.py:2` (`from pymongo import MongoClient`, `from motor... import AsyncIOMotorClient`). There is no `api → MongoDB` arrow. Both compose services have `build.context: .` (`docker-compose.yml:15`, `:33`).
Cause: `svarupa/derive/system.py:97-101` gives every service with context `""` all modules (`modules_under(graph, "")`), and the external-edge builder (`derive/architecture.py:181-184`) maps each module to one source box through a dict, so the whole repository lands on one service and the other loses its stores. The comment at `system.py:108-111` handles exactly this ambiguity for the route counts and not for the edges.
Why: a wrong edge in the view named "System" tells a reader the API service has no database. The decision log says a wrong edge is worse than a missing one; this is both.

**M3. Architecture groups are named after one member and reuse that member's id; arrows to a group read as arrows to that member.**
Screenshots: `shots/descovo-d-architecture-1440x900-dark.png` (root), `shots/demo-d-architecture-1440x900-dark.png` (group "routers" = the whole agent service), `shots/demo-group-passport.png` and `shots/demo-group-after-also-jump.png` (the jump).
Measured against the cited lines (`architecture.json`, descovo root): `src/api → scripts (16 imports)` cites 6 lines, 0 of them import anything under `scripts/` (all `src.pipeline.*`, `src.enrichment.*`); `src/browser → dagster/jobs (1 import)` cites `src/browser/login.py:9 from src.alerts.captcha_slack ...`; `dagster/jobs → Redis` cites `src/rotation/rate_limiter.py:4`; `scripts → AWS` cites `src/pipeline/builtwith.py:13`. The groups are Louvain communities anchored at their most connected member (`svarupa/cluster.py:54-66`), labelled by the anchor's leaf name (`derive/architecture.py:371-390 labels_for(anchor)`) and given the anchor's id. In the demo the group labelled "routers" (id `agent/routers`) holds `agent, agent/core/react_agent, agent/database, agent/routers, agent/schema`; the root says "routers calls Gemini API".
The id reuse leaks into the viewer: `debug2-demo.json` shows the passport of the group `agent/routers` ("GROUP · 5 modules") offering "also in data-flow", and the jump lands focused on the *module* `agent/routers` ("backend · FastAPI · 7 routes"); `debug-descovo.json idCollisions` lists 7 ids (`src`, `scripts`, `dagster/jobs`, `src/api`, `src/models`, `src/search`, `src/browser`) that are a `group` in one view and a `module`/`backend` in others, including inside the architecture tab itself. `viewer.py:546-562` matches "also in" by id alone.
Why: decision F2 made communities presentation-only for identity; drawing them with a member's name and id makes every arrow into the group a false sentence about that member, and it is the sentence a stranger reads.

**M4. The drill is undiscoverable: only a double-click opens it, nothing on the page says so, and the design's chevron click is not built.**
Screenshots: `shots/demo-chevron-single-click.png`, `shots/descovo-chevron-single-click.png` (single click on `›` opens the passport, view stays `/spec/root`).
Repro: click the `›` glyph (7.8x15 px) of any drillable box → passport, no drill. `document.body.innerText` contains no "double-click" anywhere; the explore hint reads "shift-click two boxes for a path; click a legend swatch to mute a kind". `SKILL.md:28` says "click a drillable box ... to expand it in place". Design §5.1 says "double-click a box, or its chevron"; `viewer.py:716` gates on `ev.detail === 2` only, and `svg.py:296 _drill_marker` has no handler of its own.
Why: "click on the component and then module level" is the interaction the user asked for by name. A stranger single-clicks, gets a card, and never finds the second level. The card itself has no "open" affordance either.

**M5. The dense views do not read as diagrams.**
Screenshots: `shots/descovo-d-module-deps-1440x900-dark-full.png` (2795x2592 canvas, 30 boxes, 76 edges, 76 "imports" labels, the middle 1000 px is lines only, boxes cut off on the right at 1440 and 1920), `shots/descovo-d-architecture-1440x900-dark.png` (37 edges in a 350 px corridor between row 2 and the externals row; seven "imports" labels floating on lines no eye can follow to either end), `shots/descovo-d-architecture-2560x1440-dark.png` (same at 2560: the corridor does not thin out with width).
Measured: architecture root 17 boxes and 37 routes; module-deps 30 boxes and 76 routes; every route validates (no crossings, no shared segments) and it still cannot be read. Archify's teardown rule is ≤12 primary nodes; the design's own hierarchical zoom exists to avoid this, but module-deps has no drill (`drillCandidates total 0`) and architecture's root does not cap edges. The `imports` verb on every module-deps edge (`svg.py:330 _route`; every edge in that view is an import) doubles the ink without adding a bit.
Why: the user's bar is "visually and conceptually like Archify". On the larger acceptance repo two of five tabs miss it entirely.

### SHOULD-FIX

**S1. A band of externals is labelled "IN A CYCLE".**
Screenshots: `shots/descovo-d-architecture-1440x900-dark-full.png` (second "IN A CYCLE" band holds Redis, PostgreSQL, AWS, SQL database, Gemini API), `shots/demo-d-architecture-drill1-dark.png` (inside "routers internals": Gemini API and MongoDB under "IN A CYCLE").
Measured from `architecture.json`: 2 bands per repo whose members are all `ext:*` carry `label: "in a cycle"` (descovo `/spec/root`, `/spec/src`; demo `/spec/agent/routers`, `/spec/api`). Externals have only incoming edges and cannot be in a cycle. `layout/engines.py:1016-1019` labels a band "in a cycle" when `all(b.id in cyclic ...)`; the externals were sunk to the bottom level at `engines.py:409-413` after the cycle fallback had already claimed them. Review #8 F5 is the standing decision: a label claiming a semantic fact must be computed from that fact.

**S2. After a drill the crumb and the title land under the sticky header.**
Screenshots: `shots/descovo-debug-after-drill-landing.png`, `shots/demo-d-request-flow-drill-landing2-dark.png` (first visible line is the meta "4 components" / "5 modules within 2 import hops...").
Measured at 1440x900 after `dblclick`: crumb top -6 / bottom 14, h2 top 26 / bottom 57, header bottom 58, `scrollY` 215, in four of five drills (demo data-flow, demo request-flow, descovo architecture, descovo data-flow); the demo architecture drill lands at scrollY 103 with the crumb visible only because the view is short. `viewer.py:1009 target.scrollIntoView({block:'start'})` under a `position: sticky` header; `scroll-margin-top: 72px` is set on `.tab` (`viewer.py:264`) and not on `.view`. The way back is hidden exactly when the reader most needs it.

**S3. The passport card covers the guided strip, the title and the leftmost boxes, and clips long citations.**
Screenshots: `shots/descovo-d-architecture-passport-dark.png`, `shots/descovo-d-architecture-chapter-dark.png` (chapter 01 opens a card that hides chapter 01 and the "1 / 6" progress), `shots/demo-d-architecture-passport-light.png`.
Measured at 1440x900: card at x 28-380, y 132-540; guided strip at y 120-197 and h2 at 223-254 are under it; `coveredNodes` = `src`, `dagster/jobs` on the descovo architecture root (a click on `src` lands on the card, which is how my first group test hit the wrong node). `#panel.scrollWidth` 484 vs `clientWidth` 350: `alembic/versions/a3f2c9e81d45_add_saved_search_and...` is cut at the card edge and a horizontal scrollbar appears. At 1280x720 the card is 132-692 of 720. `viewer.py:282 aside { position: fixed; left: 28px; top: 132px; width: 352px }`.

**S4. Route lists start with a comma.**
Screenshots: `shots/descovo-d-request-flow-1440x900-dark.png` (the only box reads "109 routes · , /available-rules, /batch …"), `shots/descovo-d-data-flow-1280x720-dark.png` (chapter "02 , /available-rules, /batch … 2 boxes").
Measured: 51 occurrences of `, /available-rules, /batch …` in descovo `index.html` (ingress box label, request-flow root sublabel, chapter title, card text). An endpoint declared with path `""` (handler-relative, the router prefix is not composed) is joined as an empty token at `derive/dataflow.py:151` and `emit/cards.py:79`.

**S5. Band labels collide with edge labels; the label gate does not see bands.**
Screenshots: `shots/descovo-worst-d-architecture-_spec_root_scripts_expanded.png`, `shots/descovo-worst-d-architecture-_spec_src_api.png` (offenders outlined in red).
Measured (`scan-descovo.json`): 6 cross-element text collisions across 720 views, all `sv-band-label` vs `sv-edge-label`: "level 4" vs "reads/writes" overlap 15.4x10 px (`/spec/scripts`, `/spec/root//scripts//expanded`), 15.4x7 (`/spec/dagster/jobs` and its expansion), "level 2" vs "imports" 2.5x6 (`/spec/src/api` and its expansion). `layout/validate.py:445-496 _check_labels` tests a route label against boxes, routes and other route labels, never against `Band` or region labels, so SVA-G-013 stays silent.

**S6. The descovo data-flow root opens on a wall of domain boxes; the story is 700 px below the fold.**
Screenshots: `shots/descovo-d-data-flow-1440x900-dark-full.png` (1401x2740; ingress and handler at y≈985 of the canvas), `shots/descovo-d-data-flow-1280x720-dark.png` (first screen: "03 / Domain", `src`, `activity`, an unattached "reads/writes" label on a dashed rectangle at the top).
The flow engine centres the one ingress and one handler against a 24-box domain column and fans 24 identical "imports" labels down it. The type promises "where data enters, is handled, lands"; the first screen shows none of the three.

**S7. The picture's ids are not the graph's ids: `get_node api/routers` says no such node.**
Repro: `svarupa query /tmp/review20/demo get_node api/routers` → `match: None`, exit 1; same over MCP (`mcp_list.py`); same for `agent/routers`, `api/database`, `shortest_path agent/routers api`, `affected api/database`. The passport's id chip reads `api/routers`, but `graph.json.nodes` holds file-level modules (`api/routers/api.py`) and directory modules live only in `graph.json.modules`, a bare list of strings. `SKILL.md:65` promises `get_node <label>  # exact id, qualified name or label`. The "graph generated like Graphify so we can query" bar fails for exactly the objects the diagrams draw.

**S8. Guided chapters skip the only interesting service.**
Screenshot: `shots/descovo-d-deploy-topology-1440x900-dark-full.png` (chapters: grafana, opensearch, opensearch-dashboards, prometheus; `app`, the box with four arrows, is not one).
`emit/cards.py:159` stars nodes whose kind is `service` or `endpoint`; `derive/system.py:102` renames every built service to `backend`/`frontend`/`security`, so the code-bearing services are exactly the ones excluded and the image-only ones (two `depends_on` pairs) become the story.

**S9. No keyboard at all.**
Measured (`measure-*.json walk`): Escape leaves the passport open (`escapeClosesPassport: false`, both repos, all tabs); `.sv-node[tabindex]` count 0; Tab order after a click: `#panel-close`, `#reach-down`, the source links, `body`, the nav. `viewer.py` registers no `keydown` handler and the SVG groups are not focusable. Text selection in the card works (drag selected "lembic/versions").

**S10. The documents send a stranger to things that do not exist.**
- `REPORT.md` (both repos): "run with `--json` to consume them" (`emit/report.py:68`, `:249`); `svarupa demo --json` → argparse error, exit 2.
- `SKILL.md:28` "click a drillable box" (it is a double-click, M4); `SKILL.md:35` "`diagrams/*.json` ... one file per view" (one file per diagram kind: 5 files for 163 views).
- `README.md` has no usage section: not one `svarupa <path>` line, no picture of the output; it sells "seven types of architecture diagram" while `derive/__init__.py:41-48` registers six derivers and `DiagramKind` (`derive/base.py:80-88`) carries `API_SURFACE` and `CLASS_HIERARCHY` with no deriver behind them.
- `python -m svarupa` (the shape the review brief and CLAUDE-style docs use) fails: no `svarupa/__main__.py`.
- `svarupa setup skill <dir>` is not the syntax (`--dest`); works with `--dest`.

**S11. The header wraps to two rows below 1440.**
Screenshot: `shots/misc-header-1280-dark.png`, `shots/descovo-d-data-flow-1280x720-dark.png`. Measured: header height 57.6 px at 1440, 107.7 px at 1280 and 1024 (`misc.json`); the sticky header then takes 15 percent of a 720 px viewport and the chrome constants (290/57) are only true at ≥1440. The nav also shows "not drawn" as if it were a diagram tab and "demo" / "descovo-data-core" as a bare word after "svarupa" with no cue that it is the repository.

**S12. "AWS via boto3" names a vendor the code never talks to.**
Screenshots: `shots/descovo-d-deploy-topology-1440x900-dark-full.png` (box "AWS · via boto3" beside a compose "minio" database box), `shots/descovo-d-architecture-1440x900-dark-full.png`.
`descovo-data-core/src/config.py:33-35`: `s3_endpoint_url = "http://localhost:9000"`, `minioadmin` credentials. `extract/vocabulary.py:98` maps `boto3` to "AWS". Two boxes for one store (review #16 F6) and a vocabulary word the code contradicts; "S3 API via boto3" would be true.

### CONSIDER

- **C1.** Theme button reads the *target* state ("light" while the page is dark; `viewer.py:388-395`); with JavaScript off it reads "dark" while dark (`viewer.py:1321`). A stranger cannot tell state from action.
- **C2.** Legend and passport use internal vocabulary: "group 7", "module 5", "GROUP" chip. Nothing says a group is a community.
- **C3.** Only the first arrow into a store carries its verb (`derive/architecture.py:191`); on the demo data-flow root both MongoDB arrows are unlabelled because the one label was dropped by the gate (`shots/demo-d-data-flow-1440x900-dark-full.png`), so the dashed arrows say nothing until clicked.
- **C4.** Head truncation leaves "…r · FastAPI · 2 routes · FastAPI security" on the `agent` handler in the demo request story (`shots/demo-d-request-flow-drill-landing2-dark.png`): the ellipsis ate the word and kept the tail.
- **C5.** Inside a box, the drill chevron overlaps the label by up to 5.3x8 px (`react_agent`, `ai_products`; 28 boxes on demo roots, 64 on descovo roots) and the SRC capsule text overlaps the label by 1.6 px on 48/120 boxes (`measure-*.json textCollisions sameNode`). Visible as the `›` touching the last glyph.
- **C6.** An empty directory scans to "0 files" with exit 0 and writes a 51 KB artifact whose only tab is "not drawn" (`shots/misc-empty-repo-1440x900-dark.png`). A typo in the path that hits an empty dir reads as success.
- **C7.** The repository-root module is spelled `''` in diagnostics ("SVA-R-004 '' 1 module(s) omitted", "', rust-processor, rust-processor/src'") though review #10 F4 spelled it `.` in the lockfile.
- **C8.** `svg.py:331-341 _route` docstring still says an edge carries no text; it now draws the verb.
- **C9.** Tab "deploy-topology", title "System"; sublabel "built from ." means nothing to a stranger ("built from repository root").
- **C10.** Request-flow root is a menu: 4 disconnected boxes on demo, a single box on descovo (`shots/descovo-d-request-flow-1440x900-dark.png`); with one story, open it.
- **C11.** Every page load logs a 404 (favicon) in the console.
- **C12.** Route ids in the passport's OUT/IN lists are raw (`ext:database:SQL database`, `docker-compose.yml#service.postgres`) where the box shows a label.

## What the review confirmed sound

- **Determinism.** Demo and descovo rebuilt under `PYTHONHASHSEED=7 LC_ALL=C`: `diff -r` clean on all five files each (index.html 1,710,989 and 9,657,458 bytes).
- **Gates.** 0 withheld on both repos; REPORT.md "Canvas fits" agrees with Chromium: canvas top measured at 281 px (constant 290 is conservative by 9 px); demo module-deps bottom 855 < 900 (fits 1440x900), descovo architecture bottom 991 (report says 1600x1000, correct), data-flow roots scroll as stated.
- **Geometry in a real browser, all 883 views** (`scan-*.json`): 0 routes through a foreign box, 0 arrowheads or tails inside a box, 0 edge or box labels over a foreign box, 0 cross-element text collisions other than the 6 band cases in S5; forced `textLength` within 6.1 percent of the natural glyph width everywhere (worst: "api"/"src" at 25 px forced vs 23.5 px natural, 69 and 199 labels beyond 6 percent on demo/descovo, none beyond 6.1).
- **Interaction in Chromium** (`debug2-*.json`): hover lights exactly one route and its two boxes on every tab tried; a real Shift-held path reads "path (2 hops, undirected): alembic/versions → ext:database:SQL database ← dagster/jobs" with the arrow directions honoured; search "dag" → "2 matches"; legend mute recedes 2 boxes and 3 routes; chapter 1 lights 10 boxes and reports "1 / 6"; card items open a "Fact" card with 8 sources; the crumb returns to `/spec/root`; the drill opens the in-place expansion, siblings receded (17 and 30 parent-scope nodes), container labelled and closable.
- **Export.** SVG downloads for every tab (21 to 109 KB, standalone with the stylesheet); PNG at 2x (2414x1420 for the 1207x710 descovo architecture root, 5590x5184 for module-deps, 2802x5480 for data-flow), theme carried.
- **Light theme** contrast is fine on every tab; the no-JS page renders all five views and the noscript hint.
- **Hostile inputs**, fresh venv: nonexistent path → `SVA-D-007`; a file → `SVA-D-007`; an unowned `--out` → `SVA-E-001` with two `fix:` lines; a non-lockfile to `--diff` → `SVA-L-007`; a directory named `<img src=x onerror=alert(1)>` is escaped in every attribute, opens with no dialog, and its links resolve; a `javascript:` source base yields "(no safe link for this path)". All one line, all with a code (the crash in M1 is the exception).
- **Install and agent surface.** `uv pip install '.[mcp]'` into a clean venv, `svarupa --version` 0.1.0.dev0; `query graph_stats`, `god_nodes --json`, `query_graph` answer; `svarupa mcp` lists the seven tools under Graphify's names with the documented parameters and answers `graph_stats`; `setup skill --dest` writes `.claude/skills/svarupa/SKILL.md`.
- **Citations are true.** Every cited line I opened (five or more arrows per root view, both repos, `deploy-topology`, `architecture`, `data-flow`, `module-deps`) contains the import or `depends_on` it is cited for. The falsehoods in M2 and M3 are in which box the arrow is attached to, never in the line.

## Riskiest remaining untested assumption

That a fix to M3 exists that keeps the community layout: if groups must stop borrowing a member's name and id, either the group gets a synthetic name and id (and the "routers" box becomes "group 1", which is honest and useless) or the architecture root stops grouping (and inherits M5's tangle). Nothing in this review measured which of those a reader prefers, and Archify's gallery was not fetched for a side-by-side; the comparison rests on the teardown's rules. Also unmeasured: JetBrains Mono is not installed on this machine, so every label was rendered in the fallback monospace under a forced `textLength`; a machine with the font may shift the same-node overlaps in C5 either way.

## Triage table

Every finding accepted. Fixes in the commit after this record, each with a
pinning test and an entry on `scripts/mutate_review20.py`.

| # | Finding | Rank | Disposition | Why |
|---|---|---|---|---|
| M1 | Rationale scanner crashes the build (bus error, exit 138, no diagnostic) | MUST-FIX | **Accepted, fixed** | The reviewer's cause was incomplete: binding the `Tree` for the walk still crashed on every file tried. Bisected in fresh processes to reading `start_point`/`end_point` on the docstring node reached through `child_by_field_name` and `children`; the same scan with byte offsets survives every file. Lines are now counted from the node's byte range (`_lines`), the self build exits 0 three times over, and a test scans the three files that crashed in a subprocess, because a signal cannot be caught in-process |
| M2 | Shared build context: one service got every store edge, the other none, with the wrong file cited | MUST-FIX | **Accepted, fixed** | A module stands for EVERY service whose context holds it (`source_of` values may be tuples). Both root-built services ship the code that talks to MongoDB, so both arrows are true and cite the same line; the demo's `api -> MongoDB` is back, `agent`'s citation is its own |
| M3 | Groups borrowed a member's name and id; arrows and "also in" lied about that member | MUST-FIX | **Accepted, fixed** | A community box has its own id (`group:<anchor>`) and a label that reads as a set: the directory all members share (`agent`, `api` on the demo) or, with none, the anchor's leaf and the count of the rest (`models +5`), made unique the way module labels are. The passport lists the members as chips and the `also in` chips can no longer match a module. The reviewer's riskiest assumption resolved: the grouped layout stays, with a computed name that is honest and not useless |
| M4 | Drill only on an unannounced double-click; the chevron click of design 5.1 not built | MUST-FIX | **Accepted, fixed** | The chevron opens the drill on a single click, the passport carries an "Open in place" button for any drillable box, the explore hint names all three gestures, and SKILL.md and the README say what the artifact does. Pinned in the jsdom harness |
| M5 | Dense views unreadable: 76 `imports` labels, 30-box module deps, 37-edge architecture corridor | MUST-FIX | **Accepted, fixed in two parts; one part carried** | Structural arrows are silent again (the word was drawn for one wave; 76 identical labels doubled the ink), the legend says once what a solid arrow is, and every store arrow carries its verb. Module deps past the top-box budget follow the directory tree: a box per part at the root (descovo: 30 boxes and 76 arrows became 4 and 3), the modules of a part one drill down, every dependency drawn at exactly one level so nothing is summarized away. Carried: descovo's `src/` holds 26 sibling packages and 56 arrows among them, which is that repository's real shape and stays dense one level down; the architecture root keeps its 37 arrows without words. Grouping beyond the tree would invent structure |
| S1 | Externals band labelled "in a cycle" | SHOULD-FIX | **Accepted, fixed** | The label is computed from the fact (review #8 F5): externals are excluded from the cycle set and their band reads `external` |
| S2 | Drill landing under the sticky header | SHOULD-FIX | **Accepted, fixed** | `scroll-margin-top` on `.view`; measured after a chevron drill at 1440x900: crumb top 96px, header bottom 58px |
| S3 | Passport covered the strip, the title, two boxes; clipped citations | SHOULD-FIX | **Accepted, fixed** | The passport is a side panel, not an overlay: while it is open the tab makes room (404px) and nothing sits under it (measured: 0 boxes and no strip covered); long citations wrap anywhere |
| S4 | Route lists starting with a comma (51 places) | SHOULD-FIX | **Accepted, fixed** | An empty route path counts and is not shown, in the ingress label and the cards |
| S5 | Band labels outside the label gate (6 collisions) | SHOULD-FIX | **Accepted, fixed** | Band and region label rectangles are one definition (`band_label_rect`, `region_label_rect`) read by the settle, the validator and the renderer; a route verb settles off them and SVA-G-013 reports a collision with either |
| S6 | Data-flow root opened on the domain column | SHOULD-FIX | **Accepted, fixed** | Flow columns centre only while the tallest fits a 1440x900 screen (600px); past that they align at the top. descovo's ingress and handler sit at y 122 and are on the first screen at 1280x720 |
| S7 | Diagram ids not queryable (`get_node api/routers` null) | SHOULD-FIX | **Accepted, fixed** | Directory modules are graph nodes (kind `module`, `structural: directory`) citing their files' first lines, with `contains` edges to their files and `imports` edges with weights and import lines between modules; `get_node`, `get_neighbors`, `shortest_path`, `affected` answer for the ids the passport shows. The root module (id "") stays with its files |
| S8 | Chapters excluded built services | SHOULD-FIX | **Accepted, fixed** | `backend`, `frontend`, `security` star a chapter; descovo's topology chapters lead with `app` |
| S9 | No keyboard | SHOULD-FIX | **Accepted, fixed in part** | Boxes carry `tabindex="0"` with a focus ring, Enter is a click, Escape closes the passport and clears every lit state. Not built: a roving tab order beyond document order |
| S10 | Documents named a `--json` flag, "click", "one file per view", "seven types", `python -m svarupa` | SHOULD-FIX | **Accepted, fixed** | REPORT.md names what exists; SKILL.md (and its generating constant) describe the three drill gestures and one file per diagram type; README gains a Usage section with a screenshot, counts six types, and `python -m svarupa` runs. The two unbacked `DiagramKind` members stay as the P2/P3 kinds they are; nothing counts them |
| S11 | Header wrapped to 108px below 1440; "not drawn" read as a tab; the repo a bare word | SHOULD-FIX | **Accepted, fixed** | One row at every width down to 1024 (nav scrolls, input yields, nothing wraps; measured 57.6px at 1280); the repository sits in a labelled chip outside the heading; `not drawn` is set apart and titled |
| S12 | "AWS via boto3" against a MinIO endpoint; two boxes for one store | SHOULD-FIX | **Accepted in part** | The vocabulary says what the import is (`AWS SDK`), not where it points. Merging the SDK box with the compose `minio` store is deferred: no line evidences that boto3 talks to that container; an endpoint-URL join is an extractor of its own |
| C1 | Theme button named the target, not the state | CONSIDER | **Accepted, fixed** | Shows the current theme with a glyph; the title names the switch |
| C2 | Internal vocabulary in the legend | CONSIDER | **Accepted, fixed** | Every kind swatch carries a one-line explanation; a group is "modules that import each other, grouped for reading; not an identity" |
| C3 | Only the first store arrow carried its verb | CONSIDER | **Accepted, fixed** | Every store arrow carries it; the gate drops the ones that would collide |
| C4 | Head truncation of semantic sublabels | CONSIDER | **Accepted, fixed** | Sublabels cut from the tail; paths keep cutting from the head |
| C5 | Chevron and SRC capsule touching the label | CONSIDER | **Accepted, fixed** | 22px reserved each side of a drillable label; one-line labels sit 2px lower, clear of the capsule. The reserve exceeds the largest measured overlap (5.3px); not re-measured glyph by glyph |
| C6 | An empty directory was analyzed to a "not drawn" artifact with exit 0 | CONSIDER | **Accepted, fixed** | SVA-D-008 refuses an empty directory (VCS metadata alone does not count); a directory of files that are not source is a repository and is analyzed |
| C7 | The root module spelled `''` in diagnostics | CONSIDER | **Accepted, fixed** | `(repo root)`, as its box reads |
| C8 | Stale `_route` docstring | CONSIDER | **Accepted, fixed** | Says what it draws |
| C9 | Tab `deploy-topology`, title `System`; "built from ." | CONSIDER | **Accepted, fixed** | Title `Deploy topology`; "built from the repository root" |
| C10 | A one-item request-flow menu | CONSIDER | **Accepted, fixed** | One story is the root |
| C11 | Favicon 404 on every load | CONSIDER | **Accepted, fixed** | An empty data-URL icon |
| C12 | Raw ids in the passport's OUT/IN lists | CONSIDER | **Accepted, fixed** | Rows show the box's label; the id is the row's tooltip |

## Promoted to the decision log

1. **A box is named for what it is, never for one of its members** (M3).
2. **A module stands for every service whose build context holds it** (M2).
3. **A gesture the reader needs is written on the page and has a button** (M4).
4. **Past the top-box budget, a complete view follows the directory tree** (M5).
5. **Every text on the canvas is an obstacle for every other** (S5).
6. **A crash in a dependency is worked around at the call and pinned in a fresh process** (M1).
7. **The passport is a side panel, not an overlay** (S3).
8. **The ids a diagram draws are ids the graph answers for** (S7).
9. **No input is a refusal; a repository with no source is a repository** (C6).

## Measured after

```
766 passed, 1 skipped, 2 xfailed; ruff clean; pyright strict 0 errors
scripts/mutate_review20.py: 25 mutations, all caught (24 in the first full
  run less five survivors, each a missing test, then pinned and re-run)
svarupa on itself: exit 0, three runs (was exit 138/139)
demo and descovo: 0 withheld, 0 SVA-G-013/015, byte-identical across
  PYTHONHASHSEED 1/42 and LC_ALL C/tr_TR.UTF-8
descovo module-deps root: 4 boxes, 3 arrows (was 30 and 76); its `src`
  part one drill down: 26 boxes, 56 arrows
headless Chromium 1440x900: header 57.6px at 1440 and 1280; passport open
  covers 0 boxes and no strip; chevron drill lands with the crumb at y 96
  under a 58px header; `api -> MongoDB` drawn; descovo data-flow ingress at
  canvas y 122, on the first screen at 1280x720
```

## Carried

- descovo's `src/` part: 26 sibling packages and 56 arrows one drill below the
  module-deps root; the architecture root's 37 arrows. Both are that
  repository's shape. Bundling or a wider-than-tree grouping would need a
  design decision of its own.
- The `AWS SDK` box beside the compose `minio` store (S12): one thing, two
  boxes, until an extractor joins an SDK's endpoint configuration to a
  composed service.
- Keyboard: a roving tab order; today it is document order plus Enter/Escape.
- PyPI registration of `svarupa` (user action; the documents reference it).
