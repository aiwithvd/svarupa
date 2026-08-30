# Review #1: Spike 0

**Date:** 2026-08-30
**Reviewer:** Fable (adversarial)
**Subject:** `spike/probe.py`, `spike/perturb.py`, `docs/reviews/2026-08-30-spike-0-results.md`
**Verdict:** **PASS does not stand as written. Re-graded PASS-with-a-weak-harness.** Architectural conclusions endorsed; proceed to P1 with amendments.

---

## The core criticism, accepted

The spike's lockfile is a pure function of two inputs: the set of file paths, and the import statements. Three of the four "intra-module" perturbations (rename local variable, add docstring, reformat blank lines) **cannot touch either input**. Zero churn was therefore guaranteed by construction before the harness ran.

The gate could not have failed. A gate that cannot fail is not evidence.

This matters because those numbers were slated for promotion into the design decision log as the empirical basis for F2. Promoting a tautology as evidence would have given F2 false confidence and set a precedent that weak harnesses pass gates.

**F2 itself still stands.** The reasoning was always sound and the Leiden instability is real. What is withdrawn is the claim that the spike *demonstrated* it.

---

## Findings and triage

| # | Finding | Rank | Disposition | Why |
|---|---|---|---|---|
| R2-1 | Gate 3 harness is tautological; no-ops score as passes | MUST-FIX | **Accepted** | A gate that cannot fail is not evidence. Regrade to "consistent with F2", rebuild as a P1 regression suite |
| R2-2 | `flips()` both under- and over-counts regrouping | SHOULD-FIX | **Accepted** | Metric is label-distance, not partition-distance. Replace with 1 − ARI |
| R2-3 | "Genuinely external" is unsupported; all failures counted as external | MUST-FIX | **Accepted** | The scorecard is the headline honesty feature. A two-bin design launders resolver failures as externals |
| R2-4 | File-level is the right substrate but wrong terminal unit | SHOULD-FIX | **Accepted** | `from flask import Flask` resolves to `__init__.py`, not `app.py`. Amendment reworded to symbol-level with file fallback |
| R2-5 | Lockfile serializer violates S1 four ways | MUST-FIX | **Accepted** | Aggregated dep lines break the design's own one-line-diff promise. Grammar moves to P1-0 |
| R2-6 | APFS correction accurate but incomplete | SHOULD-FIX | **Accepted** | git `core.precomposeunicode` is the real delivery channel; collision and case are separate axes |
| R2-7 | leidenalg swap missed a licensing constraint | SHOULD-FIX | **Accepted, resolved differently** | See below. GPL would foreclose the license revisit §14 explicitly plans |
| R2-8.1 | TypeScript never exercised; all four subjects were Python | CONSIDER | **Accepted** | TS ships in P1. Largest untested surface |
| R2-8.2 | Call-edge resolution rate completely unknown | CONSIDER | **Accepted, escalated** | Promoted to a blocking measurement in P1-2. See riskiest assumption |
| R2-8.3 | Cross-platform Leiden float tie-breaks untested | CONSIDER | **Deferred to P1-0** | Rolls into the gate-2 CI job |
| R2-8.4 | No scale or timing data | CONSIDER | **Deferred to P1-5** | Already scheduled with the gate-1 re-run |
| R2-8.5 | Workspace manifests untested; `source_roots` name-matches | CONSIDER | **Accepted** | Folds into P1-1 workspace detection |

**Nothing rejected.** Every finding was actionable.

---

## R2-5 in detail: the serializer broke the design's own promise

Design §7.1 promises *"a new cross-module dependency produces exactly one line"* and *"a reviewer sees `billing -> auth` appear as a green line."*

The spike serializer emits:

```
module api -> auth, billing
```

Adding one dependency renders in `git diff` as **one removed line plus one added line**, and the reviewer must eyeball-diff a comma list. The format cannot deliver the promise the design makes for it.

Fix, one record per line:

```
dep api -> auth
dep api -> billing
```

Three further S1 violations, all accepted: no escaping rule at all (POSIX paths may contain spaces, `", "`, or `" -> "`), silent merging of module keys that collide after NFC normalization, and no rule for whether a parser encountering an unknown record kind skips it or refuses.

---

## R2-7 resolved: BSD clustering, license stays open

Fable caught that `leidenalg` is GPL-3.0-or-later and `igraph` is GPL. Both are one-way compatible with the provisional AGPL-3.0, so nothing is broken today. But design §14 **explicitly plans to revisit the license**, because the CI thesis targets platform teams at enterprises that ban AGPL. A GPL clustering dependency forecloses the two most likely revisit outcomes: relicensing to MIT/Apache, or dual-licensing commercially.

graspologic is MIT, so the original swap traded 30x install footprint for a license constraint. Neither horn is necessary.

**Measured a third option:**

| Option | License | Extra deps | Deterministic | ARI vs planted truth |
|---|---|---|---|---|
| `networkx.louvain_communities` | **BSD-3-Clause** | **0** (already required) | yes, seeded | **1.000** |
| `leidenalg` + `igraph` | GPL-3.0-or-later | 6 | yes, seeded | 1.000 |
| `graspologic` | MIT | 43 (575 MB) | yes, seeded | not measured |

**Resolution:** default to `networkx.louvain_communities`. Zero extra dependencies, permissive, and it matched Leiden exactly on the benchmark. Offer `leidenalg` as an opt-in extra for users who want Leiden's better-connected-communities guarantee.

This is safe specifically *because* F2 made clustering presentation-only. If communities still fed the lockfile, algorithm quality would be a correctness concern rather than an aesthetic one.

**Caveat recorded:** ARI 1.000 for both is on a synthetic planted-partition graph where the answer is easy. Louvain's known weakness (it can produce internally disconnected communities, which is the entire motivation of the Leiden paper) has not been tested on a real code graph. Re-measure during the P1-5 gate-1 re-run at scale; if Louvain degrades there, promote leidenalg to default and accept the license constraint deliberately rather than by accident.

Also accepted from R2-7: use `RBConfigurationVertexPartition` or CPM with a recorded resolution parameter rather than plain modularity (which has a resolution limit and no tuning knob), and feed **edge weights** into clustering. The spike discarded them, so a module pair with 40 imports between them was treated identically to one with 1.

---

## Amendments to Spike 0 results

Of the five the spike proposed: **confirm 1, 4, 5; modify 2 and 3; add five more.**

| # | Amendment | Status |
|---|---|---|
| 1 | graspologic → clustering swap | **Modified.** Not leidenalg. Default `networkx.louvain_communities` (BSD, 0 deps), leidenalg optional extra. Verify pinned license texts in P1-0 |
| 2 | Resolution targets files | **Modified.** Symbol-level with an explicit file-level fallback tier recorded as its own scorecard bin. Module is an aggregation. `__init__.py` re-export chasing is required P1-2 behavior with a fixture |
| 3 | Promote perturbation numbers to decision log | **Modified.** Promote the *conclusion*, not the numbers. Record as "consistent with F2, not demonstrated" |
| 4 | Gate 2 into P1-0 CI | **Confirmed** |
| 5 | Gate 1 re-run at scale in P1-5 | **Confirmed** |
| 6 | Lockfile grammar in P1-0: escaping, one dep record per line, unknown-kind-skip rule, collision diagnostics | **Added** |
| 7 | Scorecard gets three bins: resolved / known-external / **unresolved-unknown**, cross-checked against stdlib and declared dependencies | **Added** |
| 8 | Perturbation harness rebuilt as a P1 regression suite: assert-mutation-or-fail, multi-file, source-only targets, real-PR replay from subject git history, ARI metric | **Added** |
| 9 | F4 extended: git-checkout NFD fixture, post-normalization collision diagnostic, case-insensitivity as a distinct axis | **Added** |
| 10 | A TypeScript subject with tsconfig path aliases added to the P1 fixture set before the TS extractor is done | **Added** |

---

## Riskiest remaining untested assumption

**That import resolution quality predicts call resolution quality.**

Gate 4 measured imports at 37-47%. Every headline capability lives on **call** edges: the sequence deriver, `trace_calls`, `impact_of_change`, and the fail-closed promise that unresolvable dispatch is omitted rather than guessed. The spike produced **zero data** on call resolution, and calls are far harder than imports in Python (dynamic dispatch, decorators, DI, `getattr`).

If call resolution lands in single digits, the sequence deriver returns `None` on most repos and two of the six MCP tools come back empty. That guts the demo while leaving the lockfile pillar intact.

**Action:** a bounded call-resolution measurement is now a **blocking gate in P1-2, before the sequence deriver is scheduled.** If the number is bad, scope moves toward the config-and-import-backed diagrams (architecture, module deps, ERD, API surface, deploy topology), which the spike did validate.

Second place: TypeScript, which P1 ships and the spike never ran.
