# Review #12: P1-8 (distribution: `svarupa setup`, the skill, the CI workflow)

**Date:** 2026-09-06
**Reviewer:** Fable (adversarial)
**Reviewed at:** `13c0cf6` · **Fixes:** the commit following this record
**Verdict:** *"The component's headline artifact cannot work on the repository
state its own instructions create... an adopter runs `svarupa . --lock` and
commits `.svarupa/architecture.lock` exactly as `next_steps` and SKILL.md say;
the generated workflow's delta step then fails on every PR with SVA-E-001...
the target ships a per-PR diff that can never produce a diff."*

The review's sharpest demonstrations, all reproduced before fixing:

* The workflow's exact shell, run against a checkout shaped like an adopter's,
  refused with `SVA-E-001` after the full scan; so did every other
  contributor's first plain `svarupa .` on a fresh clone.
* A dangling symlink at a setup path wrote **outside the repository** with
  exit 0, and SVA-S-001's own fix text ("Re-run with --force") walked the user
  into overwriting the symlink's target.
* `uv tool install svarupa` shipped in both documents while PyPI returns 404
  for the name: a failing command today and an open name-squat.
* Hardcoding `force=True` in the CLI wiring passed all 576 tests, as did
  `fetch-depth: 0 -> 1`.

---

## Findings and triage

| # | Finding | Rank | Disposition | Why |
|---|---|---|---|---|
| F1 | The workflow, and plain `svarupa .`, fail on every fresh clone of an adopted repository | MUST-FIX | **Accepted, fixed** | `OWNED_FILES` excluded `LOCK_NAME`, so `claim()` counted the one file the product tells users to commit as foreign. cli.py exempted the lockfile from *clearing* one wave ago and never from the *ownership check* two lines away. Fixed with `TOLERATED_FILES = (LOCK_NAME,)` (owned, never cleared, never foreign); the default output path now gets the same up-front `claim()` that `--out` had, so the refusal cannot arrive after the scan; `.svarupa` joined `DEFAULT_EXCLUDES` so the tool's output is never its input. Gated by an adopter-journey test that builds, "clones", and re-runs, plus the workflow's exact shell replayed end to end against a real git clone and worktree |
| F2 | `install()` follows symlinks out of `--dest`; the refusal's own fix text escalates to overwriting the target | MUST-FIX | **Accepted, fixed** | `exists()` is False on a dangling symlink, so the collision sweep never saw it. New `_escapes()`: a planned path that is a symlink, or whose nearest existing ancestor resolves outside the resolved destination, refuses with SVA-S-003, and `--force` does not override it: force means "replace your file", never "follow your link". Three symlink tests (dangling, to a real file under both force values, directory link), all mutation-backed |
| F3 | Both documents instruct `uv tool install svarupa`; the name is a 404 on PyPI and an open squat | MUST-FIX | **Accepted, fixed within the code's power** | The workflow now pins `svarupa==<generating version>` (also closing the unpinned-latest schema-bump hole: adopters upgrade by re-running `setup ci_github`, a reviewable diff); SKILL.md says the source-checkout install works today and the PyPI command is for once the package is published. **Registering the PyPI name is now a blocking pre-release item recorded in design §9.2; only the maintainer can do it** |
| F4 | SKILL.md's machine-behaviour claims were false (exit 1 co-occurs with a usable artifact; diagnostics are stdout, not stderr) | MUST-FIX | **Accepted, fixed** | The drift checks covered flags and file names; the sentences an agent acts on were written from recall, on the component whose thesis was "a document that cannot lie". Rewritten to the measured behaviour: exit 0 = no error-severity diagnostics; exit 1 = at least one error or a refusal, artifact may still exist, diagnostics on stdout, refusals one structured line on stderr. The perma-red-CI consequence is defused by S3's fix: the delta reaches the summary whatever the exit code |
| F5 | `force=True` hardcoded in the CLI wiring passes the full suite; `fetch-depth` mutation survives | MUST-FIX | **Accepted, fixed** | Every force test drove `install()` directly; none drove `main()`. New CLI-level collision test (refuse, file intact, then `--force` replaces); workflow assertions for fetch-depth 0, the version pin, summary-before-exit ordering, and the verified base commit. Ten mutations added; the list went 11 to 21 |
| S1 | The write channel crashes raw (`PermissionError` traceback on a read-only dest); `--force` can half-install silently | SHOULD-FIX | **Accepted, fixed** | SVA-S-004 wraps the write loop; a mid-loop failure names the files already written instead of leaving the half-install silent. The write channel is part of the boundary, the same rule the lockfile's *read* channel learned in #10 F2 |
| S2 | `git show`'s exit conflates "file absent in base" with "git failed", silently dropping the drift guard | SHOULD-FIX | **Accepted, fixed** | `git cat-file -e "$BASE_SHA^{commit}"` verifies the base commit first and fails the job loudly; only then does file-existence choose the first-adoption fallback. A guard with an escape hatch is not a guard (#5 F3) |
| S3 | The delta never reaches the job summary exactly when someone needs to read it | SHOULD-FIX | **Accepted, fixed** | Output captured to a file, exit code saved, summary written, code re-raised. The failing run is the one whose delta must be readable |
| S4 | Design §9.2/§9.3 said `detect/plan/apply/verify` and "shell out to `npx skills add`"; the shipped component does neither | SHOULD-FIX | **Accepted, fixed** | Both sections amended in place with the reasoning: the quartet serves `init`/`doctor` (P2) and would be three trivial wrappers today; `detect`/`verify` grow on top of `files()` byte-equality later. The skills.sh delegation stands for P2's multi-platform work; P1 writes the one platform it is developed against, with no network dependency inside a filesystem-only command |
| S5 | SKILL.md's determinism claim was broader than any gate proves | SHOULD-FIX | **Accepted, fixed** | Scoped to the measured contract: the lockfile is byte-deterministic across machines and platforms; the full artifact per environment; an agent is explicitly told not to diff artifacts from different machines and report the difference as an architecture change |
| C1 | Repo-derived text can terminate the summary's ``` fence | CONSIDER | **Accepted, fixed** | Four-backtick fence, with a comment saying why it is not decoration, and a test |
| C2 | "each with `fix:` lines" overclaims; `suggested_fixes` defaults empty | CONSIDER | **Accepted, fixed** | "many with `fix:` lines... when present" |
| C3 | TOCTOU between the collision sweep and the write loop | CONSIDER | **Deferred, with reasoning** | A single-user CLI writing two files; the sweep exists for refusal honesty, not as a security boundary, and closing it needs `O_EXCL`-style writes that buy nothing against the actual threat (a hostile *repository*, which F2 closed). Recorded so it is a decision, not an oversight |

**Also fixed in the same wave, found by the plan's end-of-P1 manual check, not
by the review:** the resolver did not understand PEP 420 namespace packages.
On the real two-service FastAPI repo the check prescribes, every absolute
self-package import (`from agent.schema import schema` where no `__init__.py`
exists anywhere) landed in the unresolved bin, and the check's acceptance
criterion — one added cross-module import diffs the lockfile by exactly one
line — produced **zero** lines. Fixed with a submodule fallback in
`_import_edges` when the specifier names no file; two tests (resolves to the
real submodule file; does not invent an edge for a missing name); re-measured
on the repo that failed: the diff is now exactly `+ dep agent agent/schema`.

---

## What the review confirmed sound

`install()`'s check-all-before-write discipline and its mutation coverage; the
binary/directory/unreadable refusals; the byte-equality and flag-drift checks
being the right shape (the failure was stopping at flags); the every-occurrence
lockfile-token test; the dispatch-on-literal-`setup` documentation; the
committed-base-from-the-base-branch reasoning; the honest test count. The
review's riskiest-assumption (does the pipeline behave on a git *worktree*, and
does the committed lockfile inside the scanned tree perturb the regenerated
base) was answered by the fixes: the end-to-end replay scans a real worktree,
and `.svarupa` is no longer scanned at all.

---

## Promoted to the decision log

1. **The state a product's own instructions create is a test fixture** (F1). `next_steps` and SKILL.md prescribe an exact repository state; the component shipped without ever running the tool against that state, and the first end-to-end execution of the workflow happened inside the review.
2. **A symlink is a redirection: `--force` means replace your file, never follow your link** (F2). "Hostile input" extends to the *paths* of a cloned repository, not just file contents, and a refusal's suggested fix must never escalate the attack it refuses.
3. **A shipped install command is a claim about a registry** (F3). Documents referencing an unregistered package name are a failing command today and a supply-chain hole tomorrow; generated CI pins the generating version so upgrades are reviewable diffs, and name registration is release-blocking once any shipped document references the name.
4. **A document drift check must cover the sentences an agent acts on, not only the tokens it can grep** (F4). Flags and file names were machine-checked while the exit-code and stderr claims were written from recall and false, on the component whose thesis was checked documents.
5. **Wiring is a component: a property tested only below the CLI leaves the CLI free to negate it** (F5). `force=True` hardcoded in one call site survived 576 tests because every collision test drove `install()` directly.
6. **The failing run is the one whose report must survive** (S3). Error paths that skip the reporting step deliver the least information exactly when the most is needed; write the report, then re-raise the code.

---

## Measured after

```
585 passed, 1 skipped, 2 xfailed; ruff clean; pyright strict 0 errors
mutations: setup 21/21 (was 11), lock 22/22, emit 16/16, wave11 14/14
workflow shell replayed end to end against a real clone + worktree: exit 0,
  correct branch taken, intra-module change diffs as "No architectural change"
demo repo acceptance: one added cross-module import = one lockfile line
  (+ dep agent agent/schema); before the resolver fix it was zero
```

---

## Riskiest remaining untested assumption

That the pinned `uv tool install 'svarupa==...'` line behaves as written once
the package exists: nothing can execute it until the name is registered and a
version published, so the workflow's install step is the one step of the
sequence that has never run. It fails loudly (a 404 fails the job), so the
failure mode is visible rather than silent, but first-publish should re-run
the adopter journey against the real index before announcing the CI story.
