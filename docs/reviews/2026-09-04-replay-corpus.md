# Replay corpus: lockfile churn on 130 real commits

**Date:** 2026-09-04
**Harness:** `scripts/replay_history.py` (read-only; trees via `git archive`)
**Reason:** review #10's riskiest remaining untested assumption

---

## Why this exists

Every stability test in the suite edits bytes in place: rename a local, add a
docstring, reformat, add a file inside an existing module. Those are the shapes
the design's verification table names, and they are not the changes developers
actually make. Review #10 put it plainly:

> Until a set of real pull requests from a foreign repository is replayed and
> the per-PR delta line counts written down, "a refactor inside a module
> produces no diff" is a claim verified only on the shapes its fixtures
> contained.

The stability promise is the product. It had never been measured against real
history on a repository this tool did not write.

---

## Method

For each repository, walk N consecutive non-merge commits oldest-first, build
the lockfile at every tree, and diff each against its predecessor. Each tree is
built once and reused as the next step's base.

Trees are materialized with `git archive` into a temporary directory.
`git worktree` would write entries under the source repository's `.git`, and
measuring someone's repository must not modify it.

**The output is a distribution, not a pass mark.** The question is not "is it
zero" but "when it is not zero, is it because the architecture changed".

---

## Results

| Repository | Language | Commits | No change | 1-2 lines | 3+ lines | Median lines | Median files changed |
|---|---|---:|---:|---:|---:|---:|---:|
| `mcp-finnhub` | Python | 45 | 40 | 3 | 2 | 0 | 2 |
| `supersplat` | TypeScript | 40 | 40 | 0 | 0 | 0 | 2 |
| `descovo-data-core` | Python | 45 | 30 | 6 | 9 | 0 | 6 |
| **Total** | | **130** | **110 (85%)** | **9** | **11** | **0** | |

Median churn is **zero lines** in all three, against a median of 2 to 6 files
changed per commit.

---

## The gate can fail, and fails correctly

85% quiet is only meaningful if the other 15% is real. Every loud commit was
read, and each is a genuine architectural change:

```
mcp-finnhub  03995eb  9 lines / 15 files
             feat(server): add ServerContext with dependency injection
             + dep  src/mcp_finnhub  src/mcp_finnhub/api
             + dep  src/mcp_finnhub  src/mcp_finnhub/jobs

descovo      41e2078  16 lines / 49 files
             Add Phase 4: sales intelligence features
             + dep  src/activity  src
             + dep  src/api       src/activity
```

That second one is the product's whole argument in one line: **49 files changed
compressed to 16 architecture lines.** A reviewer reads sixteen facts instead of
forty-nine diffs.

`supersplat` was 40 for 40 quiet, which is suspicious enough to check rather
than celebrate. It is not blindness: that repository builds a graph of 1,133
nodes, 1,454 edges, 13 modules and 14 dependencies. The forty commits sampled
were genuinely feature work inside existing modules.

To prove the gate can fail *on that repository*, three commits that first
introduced a module directory were replayed directly:

```
e8a241d  Async GPU Operations and WebGPU Compatibility (#766)
         + module src/data-processor  and its 3 dependencies

433f30c  Support SOG compression (#740)
         + module src/sog  and its 3 dependencies

2c248d2  Migrate to splat-transform library (#772)
         - module src/sog  and its 3 dependencies    (removed)
```

The third is the best of them: a migration that deleted a whole module renders
as four removals. That is exactly the line a reviewer wants to see on a pull
request titled "migrate to library X".

---

## What this does and does not establish

**Establishes:** on 130 real commits across three repositories and two
languages, none of which this tool wrote, ordinary work produces no diff in the
committed file, and architectural work produces a small, correct, readable one.
That is the promise, measured rather than asserted.

**Does not establish:**

* **File moves.** No commit in the sample moved a file between module
  directories with its import surface unchanged. That case is known to add
  two lines and is defensible under structural identity, but it is still
  unmeasured on real history.
* **Merge commits.** Skipped with `--no-merges`, so the interaction between a
  merge and base drift is untested.
* **Anything but Python and TypeScript.** The other four languages have no
  extractor yet, so a repository in them produces an empty lockfile, which is
  now diagnosed but not measured here.
* **Scale.** The largest repository sampled is 345 files. Review #10's scale run
  on a 10,400-file directory of unrelated projects produced 7,795 records,
  which the module filter has since cut but which nobody has replayed history
  against.

---

## One observation worth carrying

The SOG commit added `module static/lib/webp` alongside `module src/sog`. That
directory is vendored third-party JavaScript. `detect` classifies vendored code
and excludes it from architecture, so its appearance means either the
classification missed this shape or the file is genuinely first-party. Not
chased here, because it is a `detect` question rather than a lockfile one, and
it is recorded so the next `detect` touch starts with a real example rather
than a hypothetical.
