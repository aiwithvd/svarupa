# Review #23: the ship gate, re-run (`4f35bf2`)

**Date:** 2026-09-09
**Reviewer:** Fable (adversarial), independent of the author's #22 record
**Fixes:** the commit following this record
**Scope:** every finding of #21 (N1 to N17) and #22 (A1 to A5) re-verified against what a person sees and what the code does; the stranger's hour on every tab of five repositories in both themes; the CLI as a product from a fresh venv. Five repositories built (demo, descovo-data-core, svarupa itself at `4f35bf2`, boxcricket_umpier, mcp-finnhub; all exit 0), 1364 views geometry-scanned in headless Chromium 1234 at 1440x900, every tab walked at 1024x768, 1100x800, 1280x720, 1440x900, 1920x1080, 496 screenshots, 95 root ids queried, 5283 + 690 + 1396 + 594 + 173 citations checked against the source files, 49 CLI invocations from a fresh `uv venv` install of `/tmp/review23/src[mcp]`.
**Artifacts:** `/tmp/review23/` (`shots/`, `exports/`, `walk-*.json`, `scan-*.json`, `stranger-*.json`, `verbs-*.json`, `cited.txt`, `n11-loop.txt`, `out/*.stdout|stderr`, `log.md`). Source read only under `/tmp/review23/src`; file:line below refer to that tree.

## Verdict

**Do not ship yet; one more short round.** The one sentence for the user: *the picture is honest and the machinery is sound (0 text collisions in 1364 views, byte-identical rebuilds across hash seed and locale, every one of the 22 prior findings fixed or carried as recorded, 770 tests green), but the documented double-click gesture cannot work because the first click moves the box out from under the cursor, the passport still greets a reader with `group:agent/routers` and `ext:database:MongoDB` on two of the three paths that open it (Play story, and clicking an arrow), and the artifact cites 259 source lines that do not exist because the file is empty.* Three things to fix first, in order:

1. **Double-click drill is unreachable** (F1). Opening the passport adds 404px of padding and shifts the canvas 192px; the second click of the pair lands on the scroller and closes the card. README, SKILL.md and the on-page hint all promise the gesture.
2. **Raw ids as card titles on the Play story / chapter path and on the Connection card** (F2). `viewer.py:950 title.textContent = anchorId` and `viewer.py:804` title a route by `data-src`. The #21 N2 decision ("a card is titled by the label the reader clicked; ids are chips") holds only for the click-a-box path.
3. **Citations to line 1 of empty files** (F3): 51 on demo, 208 on descovo, all `<pkg>/__init__.py:1` where the file has 0 lines. The README's own screenshot (`docs/images/demo-architecture.png`) shows `api/__init__.py:1` as the first "verified source". The thesis is "a claim you can click through to the source line that proves it"; there is no such line.

Everything else found is SHOULD-FIX or CONSIDER. Verbs are missing from 2 of 5 arrows on the demo Deploy topology and 4 of 6 on descovo under a legend that reads "dashed arrows carry their verb" (a side effect of the A2 fix); REPORT.md and `graph_stats` disagree on how many nodes and edges the graph has (132/110 vs 184/194 on demo); REPORT.md's "Boxes" column counts invisible routing waypoints (21 where 12 are drawn); `svarupa` with no argument silently analyzes the current directory and writes `.svarupa/` into it.

## Part A: the #21 and #22 fixes

Status: **verified** (measured, holds), **partly** (holds on the path tested in #21/#22, fails on another), **not fixed**, **regressed**. Evidence is one measured line each; numbers from `walk-*.json`, the diagram JSON, or the source tree.

| # | Finding (#21 unless marked) | Status | Evidence |
|---|---|---|---|
| N1 | `api -> Gemini API` drawn; store arrows shared across images | **verified** | demo `deploy-topology.json` root: 5 routes; `agent -> Gemini API` cites `Dockerfile.agent:15` (`COPY agent /app/agent`) + `agent/core/react_agent/agent.py:3`; `agent -> MongoDB` cites `Dockerfile.agent:15` + `agent/database/mongodb.py:1`; `api -> MongoDB` cites `Dockerfile.api:15` (`COPY api /app/api`) + `api/database/database.py:1-2`; no `api -> Gemini`. Sublabels `ships agent/ · 9 routes`, `ships api/ · 19 routes`. mcp-finnhub: `ships src/` (Dockerfile:13 `COPY src/ ./src/`); descovo `app`: `ships the repository · 109 routes`, arrows cite `Dockerfile:12 COPY . .`. Every cited line read (`cited.txt`) |
| N2 | Passport title is the raw id | **partly** | Box click: title = label on every tab of all five repos at four widths (`agent`, `versions`, `svarupa +6`, `components +3`, `mcp_finnhub +3`, `/available-rules, /batch …`); README image regenerated (title `api`). **Not fixed on two paths:** Play story and chapter click set `title.textContent = anchorId` (`viewer.py:950`): titles read `ext:database:MongoDB`, `group:agent/routers`, `group:src`, `group:components` (`shots/demo-play-story-mid.png`); a click on an arrow titles the Connection card by `data-src` (`viewer.py:804`): `group:agent/routers`, `docker-compose.yml#service.agent`, `alembic/versions`, `group:svarupa` (`shots/demo-connection-card.png`). See F2 |
| N3 | Open in place lands under the header | **verified** | `walk-*.json openInPlace.crumb.top` vs `headerBottom`: demo 76.4/75.5/121.8/121.8 vs 57.6/57.6/103.7/103.7 at 1440/1280/1100/1024; descovo 76.4/121.6/122.3/121.8; self 202/75.5/101.6/75.6; boxcricket 76.4/75.5/75.6/75.6; mcp-finnhub 246/98.5/177.8/145.8. Crumb below the header in every case; `.tab` has no padding transition |
| N4 | Nav hid tabs below 1440 | **verified** | `header.navOverflows` false and 6/6 (3/3, 4/4) tabs present at 1024, 1280, 1440, 1920 on all five repos; header 58px, or 104px (two rows) at 1024 on the five-tab repos and at 1024/1280 on descovo (`shots/descovo-header-1024x768.png`) |
| N5 | Passport an overlay again at ≤1100 | **verified** | `coveredNodes: []`, `guidedCovered/h2Covered/exploreCovered: false` on every tab of all five repos at 1100 and 1024; card 280px, `.tab` padding 328px (`shots/demo-d-architecture-passport-1024x768.png`) |
| N6 | "in a cycle" labelled sinks | **verified** | Every member of every `in a cycle` band in all 1364 views reaches itself over that view's routes (descovo 9, self 8, boxcricket 2, mcp-finnhub 2 members; demo has no such band). Fallback floors read `level 1 · cycle inside` (demo `agent internals`), `level 2 · cycle inside` (self, mcp-finnhub) |
| N7 | Group drill titled after the anchor; externals counted as modules | **verified** | `architecture.json` titles: `agent internals · 5 modules, 2 external`, `api internals · 7 modules, 1 external`, `jobs +2 internals · 3 modules, 3 external`, `svarupa +6 internals · 7 modules`; second-level crumb `← agent internals` |
| N8 | `svarupa` beside `svarupa +6` | **verified** | self root boxes `svarupa +6` (7 modules), `emit +1` (2 modules); chapters `01 svarupa +6`, `02 emit +1`; CLI grouping section prints the same names |
| N9 | 79 `imports` labels | **verified** | 0 routes labelled `imports` in any view of any diagram of the five repos (all 17 diagram JSONs scanned) |
| N10 | Ingress label elided at both ends | **verified** | descovo `in:src/api` label `/available-rules, /batch …` (drawn and as passport title); 0 box labels starting with `…` on any root. Code-level views still head-elide function names: `…rson_employment_type_changed`, `…8757c79_initial_schema` (11 descovo, 1 self, 1 mcp-finnhub), see C4 below |
| N11 | `get_node agent` ambiguous; `group:`/`tree:` undocumented | **partly** | `get_node agent` and `get_node api` answer `matched_by: id`; `get_node routers` lists two candidates and exits 1; passport chip `diagram box, not a graph node` on every `group:`/`tree:` box; SKILL.md:80-82 says so. **But** `in:*` (demo 4, descovo 1), `req:*` (demo 4) and the boxcricket `(repo root)` box (id `""`) are equally unqueryable (10 of 95 root ids, `n11-loop.txt`) and carry no such chip; the `(repo root)` chip is an empty `code` element (`shots/boxcricket_umpier-emptyIdBox-passport.png`) |
| N12 | Module-deps subtitle mixed arrows and pairs | **verified, wording poor** | descovo root: `30 modules in 4 boxes of the repository (1 of them parts to drill into); 17 module dependencies between boxes as 3 arrows, 59 inside the parts`; 17 + 59 = 76 = REPORT.md's module dependencies. `1 of them parts to drill into` is not a sentence |
| N13 | `tree:` box kind module; singleton lists itself | **verified** | `tree:src` kind `group` (legend `group 1 module 3`); all 15 `group:`/`tree:` boxes across repos are kind `group`; no singleton member chips seen (descovo `dagster` passport: `module` chip only) |
| N14 | Chapters and swatches unreachable by keyboard | **verified as triaged** | Tab order at 1440: Export SVG, Export PNG, Play story, `LI.chapter` x N, `g.sv-node` x N, `SPAN.sv-kind-*`; Enter on a box opens its passport; Open in place is 4 to 25 Tabs from the box; chevron `<text>` still not focusable (carried) |
| N15 | "readable at every level" docstring | **verified** | `architecture.py:693` area no longer claims it; the dense case (`tree:src`, 26 boxes, 56 arrows, 2715x1872) is still drawn as it was (`shots/descovo-d-module-deps-tree-drill.png`) |
| N16 | `reads/writes` on a 1000px detour | **carried, still visible** | descovo data-flow root, label at (547, 400) on the dashed top-edge detour (`shots/descovo-d-data-flow-1440x900-dark.png`) |
| N17 | 13.5 MB artifact | **carried** | descovo `index.html` 13,317,828 bytes; 976 views; the walk and scan scripts opened and switched its tabs without timeouts (load time not re-measured this round; #21 measured 626ms load, 321ms tab switch) |
| A1 (#22) | Hidden-directory tooling drawn as architecture | **verified** | boxcricket graph.json: 206 of 298 nodes under `.claude/` or `.agent/`; 0 occurrences of `.claude`, `.agent`, `.github` in either diagram JSON or in `architecture.lock`; root is 3 boxes at 398x268; CLI prints `- tooling 32` |
| A2 (#22) | Verbs stranded far from both boxes | **partly** | Measured along every route with a drawn verb (`verbs-*.json`, arc distance from the label's nearest point to the nearer box): demo 177 labels, max 226px; boxcricket 29, max 96; mcp-finnhub 61, max 212; descovo 1300, 4 over 240 (250-254, in `reveal code`, visually beside their boxes; the path detours); **self 405, 2 over 240: `god_nodes -> _brief` 352px, `affected -> _brief` 274px** in `svarupa/query//flow//__init__.py//expanded`, where 11 `calls` labels float in the corridor between rows (`shots/self-a2-far-verbs.png`). The rule also removed verbs from short deploy arrows, see F4 |
| A3 (#22) | CLI grouping named by anchor | **verified** | demo stdout `api n=7`, `agent n=5`; descovo `models +5`, `scripts +4`, `api +4`, `search +3`, `src +2`, `jobs +1`, `browser +1`; self `svarupa +6`, `emit +1`: the diagram's names |
| A4 (#22) | `built from the repository root` sublabel | **verified** | `ships api/ · 19 routes`, `ships src/`, `ships the repository · 109 routes` |
| A5 (#22) | mcp-finnhub deploy is one box | **carried** | 1 box, 0 arrows, subtitle `1 backend`, empty guided strip (`shots/mcp-finnhub-d-deploy-topology-1440x900-dark.png`) |

Of 22: 15 verified, 4 partly (N2, N11, A2, N12's wording), 3 carried as recorded (N16, N17, A5). Nothing regressed below the diagram surface.

## Part B: the stranger's hour

Every tab of every repo at 1440x900, dark and light (`shots/<repo>-<tab>-1440x900-{dark,light}[-full].png`). Grades: A reads at a glance and every claim checked is true; B reads with effort or has one cosmetic fault; C a stranger would misread or give up on part of it; F wrong.

| Repo / tab | Grade | One sentence |
|---|---|---|
| demo / architecture | **B** | Four boxes, three labelled dashed arrows, two bands; reads in two seconds, but `api`'s first cited line is an empty file (F3) and a double-click on `agent` closes the card instead of opening the group (F1). |
| demo / data-flow | **B** | Four stage regions, `handles` on every ingress arrow, 14 boxes on the first screen; the `reads/writes` for `api/database -> MongoDB` sits 180px below MongoDB and the subtitle's "6 lateral imports and 2 upstream imports not drawn" is vocabulary a stranger cannot check. |
| demo / deploy-topology | **C** | Five boxes are clear, but `api -> redis` and `api -> MongoDB` carry no verb while the legend under them says "dashed arrows carry their verb" and `depends on` appears on one of two dependency arrows (F4). |
| demo / module-deps | **B** | 12 boxes, 18 silent arrows in a layered grid; direction is only readable from the tiny heads, and the REPORT says this view has 21 boxes (F6). |
| demo / request-flow | **B** | A four-item menu with no arrows; honest and the subtitle says "drill into one", but nothing on the canvas hints that a chevron is the way in. |
| descovo / architecture | **C** | 17 boxes in a 1555px row cut at the right edge at 1440, 37 arrows in a corridor 400px tall, 3 verbs visible; arrows cannot be followed (carried since #20). |
| descovo / data-flow | **B-** | Regions and the ingress read well; a 20-line bundle leaves `api` and the `reads/writes` on the top-edge detour (N16) is the first label a reader sees. |
| descovo / deploy-topology | **C** | Ten boxes, six arrows, two verbs: `app -> postgres` and `app -> redis` are unlabelled dashed lines and both `depends on` arrows are silent (F4). |
| descovo / module-deps | **B** | Four boxes, three arrows, clean; the subtitle "(1 of them parts to drill into)" is not English and the drill into `src` is 26 boxes and 56 arrows (carried). |
| descovo / request-flow | **B** | `handler`, `hop 1`, `hop 2` regions read; same bundle and detour as data-flow. |
| self / architecture | **B** | Two boxes in an `IN A CYCLE` band with a U-shaped pair of arrows under them; the direction is unreadable at a glance and the cycle is the only fact the view states. |
| self / module-deps | **B-** | 9 boxes, 23 arrows, 730x1136 canvas that scrolls; the corridor between rows is a thicket. |
| boxcricket / architecture | **C** | Root is `(repo root) · 5 files` (eslint, next, postcss, vitest configs) as a LEVEL 1 box with 0 in / 0 out above `components +3` and `store +1`; a stranger reads the build config as the top of the architecture, and its id is the empty string (F7). |
| boxcricket / module-deps | **A-** | Six boxes, eight arrows, `Next.js · React` sublabels; the one view that needs no explanation. |
| mcp-finnhub / architecture | **B** | Same two-box cycle shape as self; `mcp_finnhub +3` and `tools +3` are honest names. |
| mcp-finnhub / deploy-topology | **C** | One box, no arrows, no chapters, subtitle `1 backend`; a tab that says nothing (A5, carried, but still a tab a stranger opens). |
| mcp-finnhub / module-deps | **B** | Eight boxes, 14 arrows, `cli · mcp-finnhub` on the entry module; readable. |

**Geometry scan, all views of all five repos at 1440x900 dark** (`scan-*.json`): demo 163 views, descovo 976, self 106, boxcricket 33, mcp-finnhub 86 = 1364. Cross-element text collisions 0; same-node overlaps 1 (descovo, known); labels over foreign boxes 0; routes through boxes 0; hidden arrowheads 0; forced-length labels outside 0.94-1.06 of natural: 69/1692, 158/12278, 41/1183, 12/224, 5/835; worst squeeze 0.939 everywhere (`api`, `src`, `git`, `Job`).

**Cited lines** (`cited.txt`, 7 per root view, 17 root views = 106 lines read): every line that exists says what the arrow says (`from langchain_google_genai import ChatGoogleGenerativeAI` for `calls Gemini API`, `from pymongo import MongoClient` for `reads/writes MongoDB`, `COPY agent /app/agent` for the image, `- api` under `depends_on` for `depends on`, `@router.post("/chat")` for a route). The exceptions are the empty-file citations (F3): `api/__init__.py:1`, `agent/database/__init__.py:1`, `dagster/__init__.py:1`, `src/outreach/__init__.py:1` on the roots alone.

**Interaction, all five repos at 1440 dark** (`walk-*.json`, `stranger-*.json`): chevron single click drills on every drillable tab and the crumb is visible (top 76-246 under a 58px header); crumb returns to the root; second-level drill works (`← agent internals`, `← src component flow`); Escape closes the card on every tab (focus left lit on deploy-topology and module-deps, both repos, cosmetic); hover lights 2 boxes and 1 route; Shift-click path captions read `path (2 hops, undirected): agent → MongoDB ← api` with labels; legend swatch mutes 1-2 boxes and 1-3 routes; chapter click lights 3-10 boxes and reports `1 / N` with the strip uncovered; card item opens a Fact with 1-8 sources; theme button reads `☾ dark` / `☀ light` and names the target in its title; 0 console errors, warnings, failed requests or HTTP ≥ 400 in any run.

**Play story**: advances every 2.5s (`viewer.py:978`), `1 / 4` to `4 / 4`, button `■ Stop` then back to `▶ Play story`, the anchor's passport opens and the chapter's boxes light; the card title is the raw id on every step (F2). **Search** on the architecture root of each repo: full id 1 match (`src/outreach`), partial id 1 match (`group:agent/rout`, `src/outre`), label 1-2 matches (`dagster` 2: the module and its group), upper-case 1-2 (case-insensitive), nonsense `zzqqxx` 0 matches with the caption `0 matches`. **Double-click**: does not drill on any repo (F1). **Export on the largest root view**: descovo data-flow 1473x2716 to SVG 78,495 bytes in 107ms and PNG 2946x5432 (2x) 819,367 bytes in 1226ms; demo data-flow PNG 2584x2160; self module-deps 1460x2272; every other root exported too (`exports/`). **No-JS page**: 163/976/106/33/86 SVGs present, `noscript` hint, theme button reads `☾ dark`. **Light theme**: every tab of every repo rendered (`*-light.png`); nothing lost, ext boxes keep their hue.

## Part C: the CLI as a product

Fresh `uv venv /tmp/review23/venv` + `uv pip install '/tmp/review23/src[mcp]'`: 1.5s, `svarupa 0.1.0.dev0`. Build times from that install: demo 0.5s, boxcricket 0.4s, mcp-finnhub 0.6s, self 0.9s, descovo 2.4-2.6s. `PYTHONHASHSEED=7 LC_ALL=C` rebuild of demo `diff -rq` clean against the default build (lockfile included); self rebuilt byte-identical.

- **Build stdout read as a stranger** (`out/build-*.stdout`): a role table, a language line, a graph line, the scorecard, unresolved samples, a grouping section, a diagrams section with five cited boxes per diagram, the files written with byte sizes, a layout section with INFO diagnostics, `open <path>/index.html`. It reads well. Two numbers in it are not the artifact's: `graph: 132 nodes, 110 edges` (cli.py:96) where `graph.json` holds 184 nodes and 194 edges and `svarupa query graph_stats` says so (F5); and every INFO carries a code a stranger has no table for (`SVA-R-007`, `SVA-G-016`), though each sentence stands on its own.
- **Every command in README.md and SKILL.md** ran: `--version`, `--help`, `<path>`, `<path> --out`, `--lock`, `--diff`, `--drift-base`, `--max-files 5` (WARNING `SVA-D-003` with a `fix:`), `query` x 7 functions with `--json`, `--relation import`, `--undirected`, `--depth 1`, `--top 3`, a `graph.json` path as the artifact, `mcp` (7 tools listed over stdio, `get_node` and `graph_stats` answered), `setup skill --dest`, `setup ci_github --dest`, `setup --help`. `uv sync`, `uv run pytest -q` (770 passed, 2 skipped, 44s), `uv run ruff check svarupa` (clean). **`uv run pyright svarupa` exits 1 from a fresh `uv sync`**: 3 errors in `svarupa/query/mcp_server.py:29,43` because the `dev` dependency group (pyproject.toml:55-61) does not include the `mcp` extra, so `mcp.server.mcpserver` is unresolved; #22's "pyright strict 0 errors" was measured with the extra installed (F9).
- **`--lock`/`--diff` on a one-import change** (`from agent.schema import schema` appended to `api/utils/__init__.py`): `1 added, 0 removed across 1 record kind(s). dep: + dep api/utils agent/schema`; unchanged tree: `No architectural change.`; without `--drift-base`: a sentence warning that a stale base attributes other commits' changes; `--drift-base` with a foreign lock (descovo) as the committed base: `WARNING SVA-L-006 ... 61 fact(s) missing from it and 217 fact(s) it still claims` then the one-line delta against the regenerated base; `--diff` without `--lock` also prints the delta. All exit 0. Lockfile 62 records, `# schema 1.6`, no `group:`/`tree:`/`ext:`/`req:` anywhere.
- **`query`**: `get_node` by exact id (`matched_by: id`), by label when unique, `ambiguous` list + exit 1 when not (`routers`), `match: None` + exit 1 for a partial id or nonsense; `get_neighbors` lists `contain` edges to file modules alongside `import`s unless `--relation import`; `shortest_path agent/routers api/database` says `no path within 6 hops` (directed, true) and finds a 5-hop undirected path through `ext:database:MongoDB`; `affected api/database` lists `api` and `api/routers` with `via=import`; `god_nodes --top 3` is `api/utils/serializers.py#...serialize_document degree=17`; `query_graph database` 24 hits with `matching: keyword ... not semantic`; nonsense 0 hits exit 1. `graph_stats` reports 184/194 (F5).
- **`mcp`**: `list_tools` returns `query_graph, get_node, get_neighbors, shortest_path, affected, god_nodes, graph_stats` with typed schemas (`label`, `relation_filter`, `max_hops`, `undirected`, `token_budget`); `get_node api/routers` returns the same JSON as the CLI.
- **`setup skill`** writes `.claude/skills/svarupa/SKILL.md` byte-identical to `skills/svarupa/SKILL.md`, prints `next: Commit ...`; a second run prints `unchanged`. **`setup ci_github`** writes `.github/workflows/svarupa.yml`: checkout with `fetch-depth: 0`, `uv tool install 'svarupa==0.1.0.dev0'` (a version that is not on PyPI; the README says "once published"), regenerates the base lock from a worktree at `base.sha`, refuses to fall back when the base commit is unreadable, writes the delta to the job summary before re-raising the exit code, handles first adoption. As the user who must commit it: readable, commented, and it will fail at `uv tool install` until the package is published, which nothing in the generated file or the `next:` lines says.
- **Hostile inputs**, one coded line each, exit 1, nothing written: missing path `SVA-D-007`, a file as the path `SVA-D-007`, empty dir `SVA-D-008`, non-svarupa `--out` `SVA-E-001` (names the foreign file, two `fix:` lines), a file as `--out` `SVA-E-001` (no `fix:` line, the only refusal without one), a README as `--diff` `SVA-L-001 [line 3]` naming the offending text, missing artifact for `query`/`mcp` `SVA-Q-001`, missing `--dest` `SVA-S-002`, `setup foo` argparse exit 2. Exit 0 with an artifact: README-only dir (`0 files, 0 in architecture`, eight named absences), path with spaces and accents (`dé mo répo`), a symlinked root, `--max-files 5`. **`svarupa` with no argument** analyzed `/tmp/review23` itself (122 files, 4.3s) and wrote `/tmp/review23/.svarupa/` (F8).

## New findings

Ranked. Pixel numbers from `getBoundingClientRect` in Chromium 1234 at the named viewport.

### MUST-FIX

**F1. Double-click cannot open a box in place: the first click moves the box.**
Screenshots: `shots/demo-dblclick-panel-closed.png` (after a double-click on `agent`: root view, card closed, nothing happened).
Repro: demo, architecture, passport closed, `mouse.dblclick` at the centre of `agent` (658, 380). Measured (`dbl.js`): after the first click the passport opens, `.tab` padding goes 20 to 404px and the box moves from x 603.5 to 795.5 (+192px); `elementFromPoint(658, 380)` is `DIV.scroller (no node)`; the second click closes the card. View stays `/spec/root` on demo, descovo, self, boxcricket, mcp-finnhub (`stranger-*.json dblclick`). With the card already open (no layout shift) the same double-click drills to `/spec/root//group:agent/routers//expanded`. At 1024 the shift is 154px (padding 328) and the result is the same.
Cause: `viewer.py:155 body:has(aside.is-open) .tab { padding-left: 404px }` (and `:305` for 328px) re-lays the canvas synchronously on the first click; `viewer.py:797` looks for `ev.detail === 2` on a click that now lands elsewhere. The #21 N3 fix removed the transition, so the jump is instant; with the transition the box would still have moved before the second click.
Why: the hint on every tab (`viewer.py:1304`), README:35-37 and SKILL.md:28-31 all promise "double-click it ... to open it in place". A stranger who does what the page says sees the card flash open and shut. The chevron and the Open button work, so the drill is reachable, but the first gesture the text names is broken on every repository.

**F2. The card title is the raw id on the Play story / chapter path and on the Connection card (N2 partly fixed).**
Screenshots: `shots/demo-play-story-mid.png` (PASSPORT `ext:database:MongoDB`, chapter `01 MongoDB` active), `shots/demo-connection-card.png` (CONNECTION `group:agent/routers`, summary `agent → Gemini API`).
Measured (`stranger-*.json play.samples[].panelTitle`, `connection.title`): demo `ext:database:MongoDB`, `group:agent/routers`, `ext:cloud:Gemini API`; descovo `group:src`, `group:src/api`; boxcricket `group:components`, `group:store`; self `group:svarupa`, `group:svarupa/emit`; mcp-finnhub `group:src/mcp_finnhub`. Connection titles: `group:agent/routers`, `docker-compose.yml#service.agent` (deploy arrow), `alembic/versions`, `group:components`, `group:svarupa`, `group:src/mcp_finnhub`. A single click on the same box titles the card `agent` / `MongoDB`.
Cause: `viewer.py:949-950` `passport(anchor); title.textContent = anchorId;` overrides the label; `viewer.py:801-804` computes `shown` as `labelOf(...)` for `.sv-node` but `node.getAttribute('data-src')` for a route.
Why: the decision promoted from N2 is "a card is titled by the label the reader clicked; ids are chips". Play story is the first button on the strip and the one a stranger presses to be shown around; every stop of the tour is titled in internal vocabulary. A Connection card whose h2 is `docker-compose.yml#service.agent` and whose summary line, two rows down, is `agent → Gemini API`, says the same thing twice in two languages.

**F3. 259 citations point at line 1 of files that have no lines.**
Screenshots: `docs/images/demo-architecture.png` in the README (VERIFIED SOURCE `api/__init__.py:1` first), `shots/demo-d-architecture-passport-1024x768.png`.
Measured: every `evidence` entry in every view of the five diagram JSONs checked against the file on disk: demo 51 of 690 cite `start_line` 1 in a 0-line file (`api/__init__.py`, `agent/database/__init__.py`, `api/config/__init__.py`, `api/database/__init__.py`, ...); descovo 208 of 5283 (`src/__init__.py`, `src/activity/__init__.py`, `src/ai_products/__init__.py`, `src/alerts/__init__.py`, `dagster/__init__.py`, `src/outreach/__init__.py`, ...); self, boxcricket, mcp-finnhub 0. On the roots: demo architecture box `api` cites `api/__init__.py:1`; demo data-flow and module-deps `agent/database` cite `agent/database/__init__.py:1`; descovo architecture `dagster` and `outreach` cite their empty `__init__.py:1`. `svarupa query get_node api` also lists `api/__init__.py:1`.
Cause: every file becomes a module node with `Evidence(f.path, 1, 1)` (`extract/resolve.py:656`) whether or not it has a line 1; `derive/base.py:356 module_evidence` collects those for a module's box, sorted, so `__init__.py` comes first and is the line a reader clicks.
Why: the README's defining constraint is "every node and every edge in every diagram carries file:line evidence ... A claim you can click through to the source line that proves it." The first click on the README's own screenshot goes to a line that does not exist. A 0-line `__init__.py` is real evidence that a package exists; `api/__init__.py` (no line) or `api/__init__.py:0` would say so honestly. The TEMPLATE's evidence-integrity axis asks exactly this question.

### SHOULD-FIX

**F4. The Deploy topology drops verbs from short arrows and the legend still says "dashed arrows carry their verb".**
Screenshots: `shots/demo-d-deploy-topology-1440x900-dark.png` (one `reads/writes`, one `calls`, one `depends on` for five arrows), `shots/descovo-d-deploy-topology-1440x900-dark.png` (two `calls` for six arrows).
Measured (`lab.js`, DOM vs JSON): demo JSON has 5 labelled routes; drawn labels: `agent -> api depends on`, `agent -> Gemini calls`, `agent -> MongoDB reads/writes`; **none** on `api -> redis (depends on)` and `api -> MongoDB (reads/writes, weight 2)`. descovo JSON 6 labelled routes; drawn: `app -> AWS SDK calls`, `app -> Gemini calls`; **none** on `app -> postgres (reads/writes, w3)`, `app -> redis (reads/writes, w2)`, `grafana -> prometheus (depends on)`, `opensearch-dashboards -> opensearch (depends on)`. Legend text under both: `dashed arrows carry their verb`.
Cause: the A2 rule in `b12fa4d` ("a verb on a multi-segment route is drawn only within 240px along the route of one of its boxes; a straight arrow is exempt") dropped labels from routes whose only label slot lay beyond the window, or that lost the slot to a neighbour; the legend sentence is written from the route variants (`viewer.py:1196 arrow_words.append("dashed arrows carry their verb")`), not from whether a label was drawn.
Why: the view named for deployment now has dashed lines with no verb next to a legend that promises one, on both repositories that have a deploy view. The passport carries the verb, but the reader has to know to click. Better to place the verb at the arrow's end than to drop it; at minimum the legend should not claim what the canvas does not show.

**F5. REPORT.md and the build stdout count a different graph from the one in `graph.json`.**
Repro: `cat demo/REPORT.md` line 9: `**132** nodes, **110** edges`; stdout `graph: 132 nodes, 110 edges`; `svarupa query demo graph_stats`: `nodes: 184, edges: 194`. Same on all five: descovo 2115/2507 vs 2370/3064; self 1536/4019 vs 1655/4215; boxcricket 292/229 vs 298/250; mcp-finnhub 1474/1734 vs 1664/1994.
Cause: `report.py:128` and `cli.py:96` print `len(graph.nodes)`/`len(graph.edges)` of the in-memory graph; `emit/data.py` then adds file-level module nodes with `contains` edges (:239-277), endpoint nodes (:116), rationale nodes and `rationale_for` edges (:180-220), services and datastores before writing `graph.json`, and `graph_stats` counts the file.
Why: SKILL.md tells an agent to "read REPORT.md before trusting" and to query `graph.json`; the two disagree by 28 to 40 percent on the first number either shows. A stranger comparing them concludes one of the tools is wrong.

**F6. REPORT.md's "Boxes" column counts invisible routing waypoints for layered views.**
Repro: demo REPORT.md `| module-deps | 1 | 21 | 0 | 1 | 1600x1000 |`; stdout `module-deps 12 boxes 18 edges`; `module-deps.json` root has 12 boxes; the canvas shows 12. boxcricket 10 vs 6; mcp-finnhub 19 vs 8; self 47 vs 9; descovo 817 vs 723.
Cause: `report.py:180 boxes = sum(len(c.boxes) for c in lo.canvases.values())` where the layered engine keeps `_dummy_box` waypoints in `canvas.boxes` (`engines.py:475, 514`; the renderer skips them).
Why: a table titled Boxes that is wrong by 75 percent on the tab a stranger will check first (they can count 12).

**F7. The boxcricket root architecture is a box of build configs with the empty string as its id.**
Screenshots: `shots/boxcricket_umpier-d-architecture-1440x900-dark.png`, `shots/boxcricket_umpier-emptyIdBox-passport.png`.
Measured: root boxes `(repo root) · 5 files` (LEVEL 1, 0 outgoing, 0 incoming, cites `eslint.config.mjs:1`, `next.config.ts:1`, `postcss.config.mjs:1`, `vitest.config.ts:1`), `components +3`, `store +1`; the passport's id chip is an empty `code` element; `get_node ''` returns `match: None`; the lockfile records `module	.`.
Cause: `architecture.py:888` labels the module `""` as `(repo root)`; the box keeps id `""` (`architecture.py:974`), which `viewer.py:596` prints as-is and `query` cannot address.
Why: the demo hides its repo-root module with `SVA-R-004` ("no extractable source"), but a Next.js repo has extractable config files at the root, so it gets a box that is the least architectural thing in the repository, drawn above the application. The empty id is a stranger-visible hole in the "every box has an id you can query" promise.

**F8. `svarupa` with no arguments analyzes the current directory and writes into it.**
Repro: `cd /tmp/review23 && svarupa` (fresh venv): 4.3s, `122 files, 94 in architecture`, `wrote .svarupa/` into `/tmp/review23`, exit 0 (`out/h-noargs.stdout`).
Cause: `cli.py:352 parser.add_argument("path", nargs="?", default=".")`.
Why: README:28 and SKILL.md:22 document `svarupa <path>`; nothing documents the default. A stranger typing the bare command to see what it does gets a full scan of wherever they are (their home directory, a monorepo) and a new directory written there. Either require the path or print usage when stdin is a terminal and no path is given.

**F9. `uv sync && uv run pyright svarupa`, the README's own development check, exits 1 on a fresh checkout.**
Repro: `/tmp/review23/src`: `uv sync` (exit 0), `uv run pyright svarupa`: `mcp_server.py:29:14 Import "mcp.server.mcpserver" could not be resolved`, `:29:42`, `:43:12`; 3 errors.
Cause: `pyproject.toml:55-61` `dev` group has pytest, pytest-cov, ruff, pyright and not the `mcp` extra (`:50`).
Why: README:116-120 lists the four commands as the development loop; #22 reports "pyright strict 0 errors". A contributor following the README gets a red check on the first run.

### CONSIDER

- **C1.** `in:*` and `req:*` boxes (demo 8, descovo 1) are diagram-only like `group:`/`tree:` but carry no "diagram box, not a graph node" chip (`viewer.py:600` tests two prefixes); `get_node in:agent` and `get_node req:agent` return `match: None`. SKILL.md:80 names only `group:` and `tree:`.
- **C2.** The refusal for a file as `--out` (`SVA-E-001: '...README.md' exists but is not a directory`) is the only one of eleven refusals without a `fix:` line.
- **C3.** `setup ci_github` emits `uv tool install 'svarupa==0.1.0.dev0'`, which cannot succeed before the package is published; the `next:` lines do not say so and the README says "once published".
- **C4.** Code-level views head-elide identifiers: `…rson_employment_type_changed`, `…al_person_follower_milestone`, `…8757c79_initial_schema`, `…vigability_after_withholding` (13 labels across descovo, self, mcp-finnhub). #21 C4 fixed sublabels; function names are cut from the head by the path rule (`engines.py:75`), losing the prefix that groups them (`rule_`, `_eval_`).
- **C5.** Escape closes the card but leaves a lit state on deploy-topology and module-deps of every repo (`escapeClearsFocus: false`), where a click on a box that is not drillable still focuses it.
- **C6.** The descovo module-deps subtitle `30 modules in 4 boxes of the repository (1 of them parts to drill into); 17 module dependencies between boxes as 3 arrows, 59 inside the parts` is correct arithmetic in a sentence nobody would say; `30 modules in 4 boxes, 1 drillable; 76 dependencies: 17 between boxes (3 arrows), 59 inside src` would do.
- **C7.** `get_neighbors` without `--relation` lists `contain` edges to every file of the module alongside the imports (4 of 8 rows on `api/routers`), which reads as noise to a stranger asking who depends on what.
- **C8.** The `agent internals` in-place view (`shots/demo-d-architecture-chevron-drill-1440x900.png`) draws Gemini API and MongoDB twice: once inside the opened frame and once ghosted in the parent row below, with the parent's `calls`/`reads/writes` labels ghosted between; by design, but a stranger sees two MongoDB boxes for one database in one view.
- **C9.** Two-box architecture roots (self, mcp-finnhub, boxcricket's lower band) draw the cycle as a U-shaped bracket beneath the boxes with the heads under the box edges; direction is unreadable without hover.
- **C10.** In the code views 11 `calls` labels float in the corridor between rows (`shots/self-a2-far-verbs.png`); each is within the rule for some arrow, but the reader cannot tell which. Bundling identical verbs or drawing them at the head would help more than the 240px window.

## What the review confirmed sound

- **Builds.** Five repositories exit 0, 0 withheld, 0 errors, 0 warnings, 0 `SVA-G-013/015`; demo `PYTHONHASHSEED=7 LC_ALL=C` and default builds `diff -rq` clean including the lockfile; self built twice byte-identical. Times 0.4 to 2.6s.
- **Deploy topology evidence (N1).** Every service arrow cites the Dockerfile `COPY` line that puts the importing file in the image plus the import lines; `api -> Gemini API` is gone; mcp-finnhub `ships src/`; descovo `app` `ships the repository` from `COPY . .`.
- **Geometry, 1364 views in a real browser.** 0 cross-element text collisions, 0 labels over foreign boxes, 0 routes through boxes, 0 hidden arrowheads; 1 same-node overlap in 16,212 forced-length labels; worst squeeze 0.939.
- **Lockfiles.** demo 62 records, descovo 221, boxcricket 19; 0 occurrences of `group:`, `tree:`, `ext:`, `req:`, `.claude`, `.agent`, `.github`; a one-import change is exactly one `+ dep` line; the unchanged tree says `No architectural change.`; `SVA-L-006` drift names counts and the delta follows.
- **Bands (N6).** 21 members of `in a cycle` bands across all views, every one reaches itself over the drawn routes; 0 false cycle claims.
- **Header, passport, landing at four widths (N3, N4, N5).** 6/6 tabs visible at 1024 through 1920 on every repo; 0 boxes, no strip, no title covered by the card at 1024 and 1100 (280px card) or 1280 and 1440 (352px card); Open in place and chevron drill land the crumb 18 to 190px below the header on every tab of every repo.
- **Passport titles on click (N2).** The box's label on every tab of all five repos at four widths; `group:`/`tree:` chip present; member chips listed; README screenshot regenerated.
- **Names (N7, N8, A3).** `agent internals · 5 modules, 2 external`; `svarupa +6` and `emit +1` on the canvas, in the chapters and in the CLI's grouping section.
- **Tooling (A1).** 206 hidden-directory nodes stay in `graph.json` and reach no diagram and no lockfile; boxcricket root 3 boxes at 398x268.
- **Interaction.** Chevron drill, second-level drill, crumb back, hover, Shift-click path, legend mute, chapters, cards, search (id, partial id, label, case, nonsense), export SVG/PNG on every root and on the largest view (2946x5432 PNG in 1.2s), keyboard Enter on a box, Escape, theme toggle, no-JS page; 0 console errors across 5 repos x 5 viewports x 2 themes.
- **CLI.** Every documented command runs; 11 refusals are one coded line each with exit 1 and nothing written; the seven query functions and their MCP twins answer identically; `setup` files are readable and idempotent; `pytest` 770 passed, `ruff` clean.

## Riskiest remaining untested assumption

That the gestures the page names are the gestures a hand performs. F1 was invisible to three reviews because every walk drove the chevron and the button with synthetic clicks at recomputed coordinates; a real double-click was never sent. The same blind spot covers hover: SKILL.md:31 says "every box and arrow shows its citations on hover", which is an SVG `<title>` tooltip that a headless browser cannot see and this review did not see either, and touch (a laptop trackpad's two-finger tap, a tablet), which no review has tried. Until an input-level pass exists (real double-click, real hover delay, a 1024-wide window with a real trackpad), every claim about what a reader can do rests on the DOM saying it is possible. Also unverified by me, by rule: `scripts/mutate_*.py`; and JetBrains Mono is still not installed here, so every label was measured in the fallback monospace.


## Triage table

Every finding accepted. Fixes in the commit after this record, each with a
pinning test and an entry on `scripts/mutate_review20.py`.

| # | Finding | Rank | Disposition | Why |
|---|---|---|---|---|
| F1 | Double-click cannot open a box: the first click opens the side panel and moves the box | MUST-FIX | **Accepted, fixed** | The click handler remembers the last box clicked; a `dblclick` within 700ms anywhere in the same tab opens that box, wherever its second click landed. Verified with a real `mouse.dblclick` in Chromium, which no review had sent before |
| F2 | Raw ids as card titles on the Play story and Connection paths | MUST-FIX | **Accepted, fixed** | A chapter titles the card by the anchor's label; a Connection card is titled `agent → Gemini API` and its summary line says the verb. Both in the jsdom harness |
| F3 | 259 citations to line 1 of empty files | MUST-FIX | **Accepted, fixed** | An empty file has zero lines (`detect._line_count`, reversing the "an editor puts the cursor on line 1" rule) and is cited as itself, `(0, 0)`, which the build accepts only for a zero-line file. The renderer writes the path alone, the passport shows `api/__init__.py (empty file)` and links it without a line anchor, and a module's real lines are cited before its empty files. 0 such citations on both repositories after |
| F4 | Verbs dropped from short deploy arrows under a legend that promised them | SHOULD-FIX | **Accepted, fixed** | The verb slides along a long segment toward the nearer box instead of the segment being skipped; the legend says "some only in the passport" when a verb could not be placed |
| F5 | REPORT.md and stdout counted a different graph from graph.json | SHOULD-FIX | **Accepted, fixed** | graph.json is built once; the report quotes its counts and explains what the code-symbol count is; the CLI line says "code graph" |
| F6 | REPORT.md's Boxes column counted routing waypoints | SHOULD-FIX | **Accepted, fixed** | Waypoints subtracted |
| F7 | The root module box had the empty string as its id | SHOULD-FIX | **Accepted, fixed** | Every diagram id spells the repository root `.`, as the lockfile does; graph.json has a `.` module node with `contains` edges to root files, so `get_node .` answers. The box itself (root-level config files) stays: it is code at the root, honestly drawn |
| F8 | Bare `svarupa` scanned and wrote into the current directory | SHOULD-FIX | **Accepted, fixed** | The path is required; argparse prints usage and exits 2 |
| F9 | `uv sync && uv run pyright svarupa` failed without the `mcp` extra | SHOULD-FIX | **Accepted, fixed** | `mcp>=2` in the dev group |
| C1 | `in:`/`req:` boxes lacked the diagram-only chip | CONSIDER | **Accepted, fixed** | Chip and SKILL.md sentence cover them |
| C2 | One refusal without a `fix:` line | CONSIDER | **Accepted, fixed** | |
| C3 | The CI workflow installs an unpublished version | CONSIDER | **Accepted, fixed** | A `next:` line says so until publication |
| C4 | Function names cut from the head | CONSIDER | **Accepted, fixed** | Paths keep their tail, names their head |
| C5 | Escape leaves a lit state on two tabs | CONSIDER | **Carried** | Not reproduced in the harness; needs the browser walk that found it |
| C6 | Module-deps subtitle wording | CONSIDER | **Accepted, fixed** | `30 modules in 4 boxes, 1 drillable; 76 dependencies: 17 between boxes (3 arrows), 59 inside src` |
| C7 | `get_neighbors` lists `contain` edges by default | CONSIDER | **Accepted, fixed** | Hidden unless `--relation contain`; the answer says so |
| C8 | Externals drawn twice in an in-place view | CONSIDER | **Carried** | By design: the opened frame holds the child's view, the parent keeps its own |
| C9 | Two-box cycles drawn as a U-bracket | CONSIDER | **Carried** | A router decision |
| C10 | `calls` labels floating in code views | CONSIDER | **Carried** | Bundling identical verbs is a router decision |

## Promoted to the decision log

1. **A gesture is verified with the input it names** (F1).
2. **An empty file is cited as itself** (F3).
3. **The root module is `.` everywhere** (F7).
4. **A report quotes the artifact it describes** (F5, F6).
5. **A verb moves toward its box before it is dropped** (F4).

## Measured after

```
778 passed, 1 skipped, 2 xfailed; ruff clean; pyright strict 0 errors
scripts/mutate_review20.py: 52 mutations, all caught (a first run left four
  unobservable or stale entries: two tests were strengthened, two entries
  re-aimed at the code as it now reads, then re-run and caught)
demo, descovo, boxcricket_umpier, mcp-finnhub: exit 0, 0 withheld,
  0 SVA-G-013/015, byte-identical across PYTHONHASHSEED 1/42 and
  LC_ALL C/tr_TR.UTF-8; svarupa on itself exit 0
citations to line 1 of an empty file: demo 0 (was 51), descovo 0 (was 208);
  empty files cited as themselves: 51 and 205
a real mouse.dblclick on the demo's `agent` box in headless Chromium opens
  /spec/root//group:agent/routers//expanded
demo deploy topology: 4 of 5 arrows carry a drawn verb (was 3), the legend
  reads "dashed arrows carry their verb" and it is true; the solid
  `api -> redis depends on` is the one still without room
REPORT.md demo: "184 nodes, 194 edges in graph.json (132 code symbols...)";
  module-deps Boxes 12 (was 21)
boxcricket root ids: `.`, group:components, group:store
```
