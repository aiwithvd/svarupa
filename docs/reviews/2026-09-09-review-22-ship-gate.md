# Review #22: the ship gate (`3907f57`, then `b12fa4d`, `c1290da` and the commit after this record)

**Date:** 2026-09-09
**Reviewer:** Fable (adversarial), then the author
**Fixes:** `b12fa4d`, `c1290da`, and the commit following this record
**Verdict:** not independent. The reviewer agent built all five repositories,
ran the geometry scan on every view, walked every tab at four widths, ran
the hostile inputs, the lockfile diff and drift checks, and then stopped
with an API billing error ("credit balance is too low") before writing its
report. What follows is the author reading the reviewer's measurements and
adding his own. It is a self-review and says so; the independent verdict is
still owed once the account can run another reviewer.

## What the reviewer measured before it stopped

Five repositories built with exit 0: demo, descovo-data-core, svarupa itself,
`boxcricket_umpier` (a Next.js app, never visually reviewed before) and
`mcp-finnhub` (a Python package with a Dockerfile and compose).

- **Geometry, every view of all five repositories** (`/tmp/review22/scan-*.log`):
  0 cross-element text collisions, 0 labels over foreign boxes, 0 routes
  through boxes, 0 hidden arrowheads. demo 163 views, descovo 976, self 106,
  boxcricket 107, mcp-finnhub 86. One same-node overlap on descovo (known).
- **Lockfiles** for demo and descovo: 0 occurrences of `group:`, `tree:`,
  `ext:` or `req:`.
- **Diff on a one-import change**: `+ dep api/utils agent/schema`, one line;
  the unchanged repository says `No architectural change`; a foreign base
  lockfile triggers `SVA-L-006` drift (61 facts missing, 217 stale) before
  the delta.
- **Hostile inputs**, fresh venv: a missing path, a file, an empty directory,
  a non-svarupa `--out`, a file as `--out`, a README as `--diff`, a missing
  artifact for `query` and `mcp`, a missing `setup --dest` all refuse with one
  coded line (`SVA-D-007`, `SVA-D-008`, `SVA-E-001`, `SVA-L-001`, `SVA-Q-001`,
  `SVA-S-002`); a README-only directory, a path with spaces and accents, a
  symlinked root and `--max-files 5` all build with exit 0.
- **Walk at 1024/1280/1440**: passport titles are labels on every tab of all
  five repositories (`agent`, `src +2`, `components +3`, `mcp_finnhub +3`,
  `svarupa +6`); the chevron drill lands with the crumb visible; 0 boxes, no
  strip and no title covered by the panel at 1440 and 1280; 0 console errors;
  the no-JS page renders every view; the theme button names the current
  theme. Header 57.6px on the two-tab repositories at 1024, 103.7px (two
  rows) on the five-tab ones, as decided in #21.
- Its `svarupa query` loop passed several labels to one call and got argparse
  exit 2 each time: a script error, not a product one; `graph_stats` answered.

## The author's findings on the two new repositories

| # | Finding | Rank | Disposition | Why |
|---|---|---|---|---|
| A1 | On the Next.js app nine of twelve top-level architecture boxes were `.agent/skills/*/scripts` and `.claude/skills/*/scripts`; the root was one 2493px row and the application (`components`, `store`) sat in a second row off the first screen | MUST-FIX | **Accepted, fixed** | Files under a top-level hidden directory are the new `tooling` role: in the graph, excluded from derivation and the lockfile like tests and vendored code. The root is now 3 boxes at 398x268. A root dotfile and a hidden directory deeper down are not tooling. Lockfile minor 5 -> 6 since the build emits fewer facts for the same code; the demo's `SVA-R-004` note no longer names `.github/workflows` |
| A2 | `reads/writes` on the top lane of a 1000px corridor (review #21 N16, carried) | SHOULD-FIX | **Accepted, fixed** (`b12fa4d`) | A verb on a multi-segment route is drawn only within 240px along the route of one of its boxes; a straight arrow is exempt. 347 stranded verbs dropped on descovo, the arrows stay, the passport carries the verb |
| A3 | The CLI's grouping section named communities by anchor (`routers`) where the diagram said `agent` | SHOULD-FIX | **Accepted, fixed** (`b12fa4d`) | Named as the architecture view names them |
| A4 | A built service's sublabel said `built from the repository root` when its Dockerfile named exactly what it ships | CONSIDER | **Accepted, fixed** (`c1290da`) | `ships api/ · 19 routes`, `ships src/` |
| A5 | mcp-finnhub's deploy topology is one box with no arrows | CONSIDER | **Carried** | The compose file declares one service and the code imports no store or SDK the vocabulary knows; the honest picture is one box. A one-box view could be folded into a sentence, which is a design decision |

## Not measured

The reviewer did not get to: reading five cited lines per view on the two
new repositories, grading every view A to F, the light theme beyond a spot
check (the author checked mcp-finnhub's architecture in light: fine), Play
story beyond a stop/start (progress advanced 1/2 to 2/2 on mcp-finnhub),
search beyond three probes (`tool` 1 match, a group label 1 match, nonsense
0), README and SKILL.md read as a stranger (verified in #21 S10 and
unchanged since except the tooling sentence).

## Measured after

```
770 passed, 1 skipped, 2 xfailed; ruff clean; pyright strict 0 errors
scripts/mutate_review20.py: 40 mutations (24 from #20, 13 from #21, 3 from
  this round), all caught
demo, descovo, boxcricket_umpier, mcp-finnhub: exit 0, 0 withheld,
  0 SVA-G-013/015, byte-identical across PYTHONHASHSEED 1/42 and
  LC_ALL C/tr_TR.UTF-8; svarupa on itself exit 0
boxcricket architecture root: 3 boxes, 398x268 (was 12 boxes in a 2493px
  row with the application off the first screen)
descovo: 1300 drawn edge labels (was 1647; 347 stranded verbs dropped)
demo deploy topology: `ships agent/ · 9 routes`, `ships api/ · 19 routes`
```

## Riskiest remaining untested assumption

That the author's reading of another agent's partial measurements is as hard
on the product as that agent would have been. It is not, by construction.
The independent gate is still owed, and it needs API credit to run.
