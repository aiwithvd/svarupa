# Review #3: P1-1 `detect`

**Date:** 2026-08-31
**Reviewer:** Fable (adversarial)
**Reviewed at:** `e2de623` · **Fixes at:** `2f4a572`
**Verdict:** *"The spine is right; three silent-wrongness paths keep the gate open."*

---

## The finding that matters most is about process

> "The fix commit shipped a test verified only on the platform where it skips."

`test_case_collision_is_diagnosed` asserted `SVA-L-007` on input that returned `[]`. It skipped on case-insensitive APFS, the only platform it was ever run on, and **would have failed every Linux CI job**. This is the same class of mistake as declaring `pyright --strict` in a config without running pyright, and it happened one commit after congratulating myself for catching the collision gap.

Any test that can only run on one platform must be treated as unverified until CI proves otherwise, or restructured so it is platform-independent. Collision detection is now tested component-wise, which needs no filesystem that can hold both spellings.

---

## Findings and triage

| # | Finding | Rank | Disposition | Why |
|---|---|---|---|---|
| F1 | Collision detection structurally unable to detect what matters; its own test red on Linux | MUST-FIX | **Accepted, fixed** | `raw_rel` was built from an already-normalized parent, destroying pre-images — the design's own §15.1 lesson reproduced one layer down. Whole-path bucketing also cannot see sibling-dir collisions with differently-named children |
| F2 | One malformed ignore line silently disables **all** ignore handling | MUST-FIX | **Accepted, fixed** | pathspec raises `GitIgnorePatternError` (a `ValueError`) which the style-fallback swallowed, leaving `_spec = None`. Ignored files, possibly secrets, entered the graph with no diagnostic |
| F3 | Workspace detection by substring false-positives | MUST-FIX | **Accepted, fixed** | `[tool.poetry.group` matches essentially every modern Poetry project. Workspaces are the source of structural module identity, so a false root is a false module boundary in the lockfile |
| F4 | `SVA-D-003` fires on a complete scan at the exact cap | SHOULD-FIX | **Accepted, fixed** | The diagnostic restated the pre-append condition instead of checking whether anything was dropped. A tool selling verified claims cannot make a false claim about its own scan |
| F5 | Nested `.gitignore` files not honoured | SHOULD-FIX | **Deferred, made visible** | Real fix needs per-directory pattern scoping during the walk. Recorded as a **strict xfail** git-oracle test so it goes red the day it is fixed, rather than being an invisible divergence |
| F6 | Banner sniffing false positives | SHOULD-FIX | **Accepted, fixed** | Matching `DO NOT EDIT` anywhere in 2KB meant any tooling script *mentioning* generated files vanished from architecture. Now first 5 lines, strong conventions only |
| F7 | `fixtures` over-matches; promised override absent | SHOULD-FIX | **Partly accepted** | `fixtures` dropped from test-dir names: it is a domain noun in sports, lighting and data software. The **override mechanism remains unbuilt** and is deferred to P1-3 with config |
| F8 | `migrations` as GENERATED starves the ERD deriver | SHOULD-FIX | **Accepted, fixed** | Django and Alembic migrations are committed, hand-reviewed, and often the only DDL. Design §4.3 names `*.sql` the ERD source of truth, so the deriver would have honestly returned `None` on a repo with a complete schema. SQL under `migrations/` is now CONFIG |
| F9 | In-tree symlinks skipped silently | CONSIDER | **Deferred to P1-3** | Never following symlinks is right; the missing diagnostic is cosmetic until the graph exists to notice the gap |
| F10 | Triple-read per file; stat/read TOCTOU | CONSIDER | **Accepted, fixed** | Read once for hash, size and banner. Taking size from the hashed bytes also closes the window where a mid-scan mutation records inconsistent size and hash |
| F11 | Nested workflow files unclassified; `Scan.root` absolute-path leak-in-waiting | CONSIDER | **Deferred to P2** | The CI config parser is P2 work. `FileRec` is proven clean by test; `Scan.root` is a hazard only if a serializer ever emits `Scan` wholesale |

**Nothing rejected outright.** Four deferrals, each with recorded reasoning and, where the divergence is user-visible, a strict-xfail oracle rather than silence.

---

## The git oracle earned its keep on first run

Fable named the riskiest assumption: *"in-process pathspec matching over a root-only pattern list reproduces git's view of the repository"* — a claim the gate had never weighed.

The production rule against shelling out to git stands (the file set must not depend on whether git is installed). But nothing stops the **test suite** using `git ls-files` as an oracle.

It immediately found a genuine **pathspec-vs-git divergence**, confirmed against real `git check-ignore`:

```
.gitignore:  *.py  /  !src/*.py  /  !keep/

git      → keep/important.py IGNORED
pathspec → keep/important.py VISIBLE
```

Un-ignoring a directory does not un-ignore files inside it that another pattern matches. Recorded as a strict xfail so it goes red if pathspec ever changes, instead of quietly passing.

---

## Test quality (R2-1 standard)

**Best test in the file:** `test_visit_order_is_normalized_before_sorting`. It fails against the parent commit, and Fable reports that its own truncation experiment produced the *fixed* behaviour while reading the unfixed source, which is how it discovered the fix commit at all.

**Did not survive:**

| Test | Why |
|---|---|
| `test_case_collision_is_diagnosed` | Skipped on macOS, red on Linux. Replaced |
| `test_scan_order_is_independent_of_creation_order` | Tautological: the final `sorted(files)` guarantees the assertion regardless of visit order. Only guards against deleting that sort |
| `test_symlink_loop_does_not_hang` | No loop-following code exists to exercise. Misnamed rather than useless |
| `test_file_cap_reports_an_incomplete_scan` | Blind to the exact-count false positive, which is the case R2-1 says to construct |
| `test_scan_is_byte_stable_across_repeated_runs` | Ten runs, one process, one filesystem. Nearly cannot fail |

Visit order is observable **only** through truncation and diagnostics. That is why the new regression test caps `max_files` below the file count: it is the sole way the ordering bug becomes visible at all.

---

## Riskiest remaining untested assumption

Carried into P1-2: **that `detect`'s file set equals what the developer believes their repo contains.** Two divergences are now documented rather than hidden (nested `.gitignore`, directory negation). The oracle exists; its coverage does not yet include pathological pattern sets.
