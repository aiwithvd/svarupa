# Review #10: P1-7 (the architecture lockfile)

**Date:** 2026-09-04
**Reviewer:** Fable (adversarial)
**Reviewed at:** `3d3be6c` · **Fixes at:** `8385e70`
**Verdict:** *"Closer to its spec than any wave since #6, and the core machinery is sound... But the component does not yet meet the spec on two demonstrated points, both recidivist."*

---

## The finding that is about process, not code

Both MUST-FIX findings are the previous wave's accepted fixes, not applied here.

**F1 is review #9 F2 with the arrow reversed.** The drift warning, this wave's
headline feature, told a reviewer that the drifted facts *"will appear in the
delta below as though this change caused them"* and to *"read the delta as
base-drift plus this change"*. The CLI then substitutes the regenerated base,
correctly, so they do not appear and the delta **is** this change alone. Every
sentence of guidance was false, printed directly above a correct
`No architectural change.`

My own test asserted the message and the `No architectural change.` line, in
sequence, without noticing they contradict each other.

**F2 is review #9 F4, one wave later.** `claim()` was moved to the front of the
CLI last wave, with a commit message explaining that a refusal arriving after
minutes of scanning has already wasted the user's time. The lockfile arguments
went in at the bottom. Four of six hostile inputs produced raw tracebacks after
the full scan, including the tool's **own designed refusal** for a mismatched
schema: a message telling the user to regenerate, wrapped in a stack telling
them about our call frames.

That is nine reviews in which a bug class was fixed in one component and
reintroduced in the next. What is new is that both instances were the
*immediately preceding* wave's findings, which suggests the §15.1 walk is being
done against the component being written rather than against the component just
finished.

---

## Findings and triage

| # | Finding | Rank | Disposition | Why |
|---|---|---|---|---|
| F1 | The drift warning describes behaviour the CLI deliberately does not have | MUST-FIX | **Accepted, fixed** | Detailed above. `drift_check` now reports only what it detected; the CLI prints what it did about it. Also added: when `--diff` runs *without* `--drift-base`, the CLI now says the delta is against the committed base as-is and may misattribute, which nothing said before |
| F2 | `--diff` inputs crash with tracebacks, validated only after the scan | MUST-FIX | **Accepted, fixed** | `SchemaMismatch` subclasses `DiagnosticError` and carries `SVA-L-007`; a new `SVA-L-008` covers unreadable and undecodable files; both lockfile arguments are read **and stamp-checked** before `detect()` runs. All six inputs now refuse with a code, printing nothing first |
| F3 | The grammar header is a function of files the lockfile excludes | SHOULD-FIX | **Accepted, fixed** | Demonstrated: one TypeScript *test* file changed the committed lockfile while changing zero records. `file_languages` counts every scanned file including the test, generated and vendored roles excluded from every fact. Now keyed on architecture-eligible files. My own docstring said the filter existed to prevent exactly this |
| F4 | The root module is an empty field, so its line ends in a bare tab | SHOULD-FIX | **Accepted, fixed** | Verified: after a `trailing-whitespace` trim the file refuses with SVA-L-002 and every CI diff fails until someone regenerates. Nearly every repository has root-level files. Spelled `.` now, before adoption makes it a major schema bump |
| F5 | The cross-OS byte gate certifies a pipeline it does not run | SHOULD-FIX | **Accepted, fixed** | `render_fixture_lock.py` stopped at `detect` and computed modules itself, then paired adjacent names for deps, so `extract`, `resolve`, `build` and `build_lock` never crossed the OS boundary. Its own derivation had already diverged from the product's. Now runs exactly what the CLI runs, over a fixture whose contents are real imports, and fails if no `dep` records result |
| F6 | Config-only directories are committed architecture facts | CONSIDER | **Accepted, fixed** | Promoted from CONSIDER: 1,518 module lines on a directory of unrelated projects is not the "small and stable" §7.1 promises, and nothing in a YAML directory can produce a `dep`. Configuration enters through `datastore`, `endpoint`, `service` and `queue`, which the grammar already publishes |
| F7 | `build_lock`'s NFC collision axis cannot fire on any real graph | CONSIDER | **Accepted, documented** | Correct: `detect` NFC-normalizes at the boundary, so only the case axis is live by the time `build_lock` runs. The raw-identity check correctly lives in `detect`. Kept as defence in depth with the split now stated, rather than presented as the enforcement point |
| F8a | A lockfile whose own build errored was still written | CONSIDER | **Accepted, fixed** | It was written first and the error only set the exit code, so a user who ignores exit codes had a known-wrong file ready to commit as the base every future diff is measured against |
| F8b | A grammar version change is invisible in the delta | CONSIDER | **Accepted, fixed** | §7.2 records versions precisely so a reviewer can tell a grammar bump from a code change. Recording them and never comparing them left exactly the ambiguity they were added to remove. New `SVA-L-011` |
| F8c | An empty scan writes a header-only lockfile silently | CONSIDER | **Accepted, fixed** | An empty lockfile is a claim that a repository has no architecture, and committed silently it becomes the base everyone diffs against. New `SVA-L-010` |
| F8d | `test_locale_does_not_affect_collation` is inert on macOS | CONSIDER | **Accepted, documented** | Verified: BSD libc collation ignores `LC_COLLATE`, so on a developer laptop the test cannot fail and its real execution is the Ubuntu CI leg. Recorded in the docstring, per the platform-conditional decision |
| F8e | §7.1's example lockfile is in an order the serializer cannot produce | CONSIDER | **Accepted, fixed** | `dep` precedes `module` under codepoint order on the kind. The example now also shows `.` for the root and states the module-record rule |

**Nothing rejected.** The reviewer also cleared a great deal under attack:
stability across rename, docstring, reformat and new-file-in-module edits; the
expected lines for a new import, module and deletion; a real historical diff;
duplicate lines, escaped separators, unknown kinds of any arity, prefix-sharing
kinds, CRLF, and empty-field round-trip; and confirmed `identity.py` genuinely
sits below both `detect` and `lock` rather than moving the cycle.

---

## The mutation suite, second time

Twenty-two mutations, written against the claims rather than the lines, per
review #9. **Four survived the first pass, and three were real gaps:** nothing
drove the stamp check specifically at load time (the later refusal produces the
same code, so deleting the early one stayed green), nothing covered the
write-on-error path, and the dangling-reference check had no test at all.

The fourth was an ineffective mutation of mine: it disabled a condition inside
`code_modules` that could not change the result, because config directories
have no nodes to filter. Replaced with one that restores the actual previous
behaviour.

The first draft of the dangling-reference test was worse than no test: it
reimplemented the check inline and asserted the reimplementation agreed with
itself, never calling `build_lock`. Rewritten to drive the real function with a
synthetic dependency on a module the filter drops.

---

## Promoted to the decision log

1. **A diagnostic states what it detected; the layer that decides the response owns the sentence describing that response** (F1). Free text predicting a consumer's behaviour couples a producer to a caller it cannot see, and the next caller-side change silently falsifies it.
2. **The channel that loads a file is part of its parser's boundary** (F2). Hardening the bytes is not enough: absent, unreadable, undecodable and unstamped must all arrive as the same structured refusal a malformed line does, and all of it before the expensive work it gates.
3. **Every byte of a committed artifact must be a function of the facts it commits** (F3). Keying any part of it, even a header, to inputs excluded from those facts reopens the churn channel through the exclusion itself.
4. **A committed plain-text format is designed against its environment, not only against its own parser** (F4). Meaning may not live in trailing whitespace, because the ecosystem's default tooling deletes trailing whitespace silently.
5. **A gate that rebuilds its expectation with its own implementation of the stage under test certifies that implementation, not the product** (F5). The gate calls what the shipped path calls, end to end.
6. **A previous review's accepted fix is part of the checklist for the next component** (F1, F2). Both MUST-FIX findings here were the immediately preceding wave's findings, unapplied. Walking §15.1 against the component being written is not enough; the last review's fixes have to be walked against it too.

---

## Measured after

```
520 passed, 1 skipped, 2 xfailed; ruff clean; pyright strict 0 errors
22 mutations, all caught
six hostile --diff inputs: all refuse with a code, none prints before refusing
svarupa's own lockfile: 7 module records, all real code modules
```

---

## Riskiest remaining untested assumption

The reviewer's, accepted unchanged and not fixed here because fixing it means
building something:

> That the stability promise holds for the refactors developers actually make,
> rather than the string edits the verification table names.

Every verified "refactor" edits bytes in place. Moving `src/core/model.py` into
`src/core/db/` with the external import surface unchanged adds two lines to the
committed file. That is defensible under the structural-identity decision, and
it is still a churn channel nobody has measured on real changes. §15.1's own
gate rule prefers replayed real pull requests over synthetic string edits, and
no replayed-PR corpus exists beyond this repository's own four commits.

**Until a set of real pull requests from a foreign repository is replayed and
the per-PR delta line counts written down, "a refactor inside a module produces
no diff" is verified only on the shapes its fixtures contained.** That corpus is
the single highest-value thing left in P1, and it is recorded in the plan as
such rather than left as a known gap.
