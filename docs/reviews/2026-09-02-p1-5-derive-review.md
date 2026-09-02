# Review #7: P1-5 (derivation: architecture, module-deps, ERD)

**Date:** 2026-09-02
**Reviewer:** Fable (adversarial)
**Reviewed at:** `58ac99d` · **Fixes at:** `c302fba`
**Verdict:** *"The stage is honest about what it cannot draw and dishonest about one thing it does draw."*

---

## The finding that is about process, not code

F1 is not just a bug. It contradicts a claim I had already written into a commit message.

Commit `58ac99d` said, verbatim:

> edge weight is truthful, five real import statements yields weight five (verified)

That was true of the fixture I ran and false in general. The fixture only used shapes where one import statement produces one edge. The general case is `from ..lib import mod_a, mod_b`, which emits **three** IMPORTS edges (the package `__init__` plus one per named submodule), all carrying the **same single** `Evidence`. Measured:

```
ONE import statement
  node-level IMPORTS edges: 3   (all citing src/app/use.py:1)
  module pair src/app -> src/lib: weight=3   DISTINCT citations=1
```

So an arrow read "3 imports" while its clickable evidence list held one line. That is exactly the label/evidence disagreement this stage exists to prevent, recorded in git as verified.

The generalisation: **a verification claim is scoped to the shapes the fixture contained.** Writing "verified" in a commit message without naming the shape is how an untested case becomes an assumed-correct one.

Related, and worth noting because it recurs: three of the last four findings came from *writing the review prompt* rather than from the review that followed it. Stating what a component guarantees, in prose, for an adversary, is itself the cheapest test available.

---

## Findings and triage

| # | Finding | Rank | Disposition | Why |
|---|---|---|---|---|
| F1 | Edge weight double-counts submodule fan-out | MUST-FIX | **Accepted, fixed** | One statement produced weight 3 against 1 distinct citation. Weight is now the size of a citation **set**, so the number on the arrow and the list behind it agree by construction. Both directions tested: fan-out gives 1, five genuine statements give 5 |
| F2 | The connection tier of spill merging is untested, and its named test cannot fail | MUST-FIX | **Accepted, fixed** | `test_capping_prefers_a_real_dependency_over_proximity` asserted `"depend on" in msg or "shared directory" in msg`. Verified the actual message was `"...merged by shared directory"`, so **zero** connection merges ever ran. An OR across two branches passes on either, which means the test named after the highest-stakes tier never touched it. Split into a proximity-specific assertion plus two tier-specific tests over a hand-built clustering. Hand-built because letting Louvain decide the input is what hid the gap: the first replacement fixture *skipped*, since clustering absorbed the dependent group before capping saw it. Both new tests assert the diagnostic count **and** where the module landed, and both were mutation-checked (disabling tier 1 reds one; restoring `best_score = -1` reds all four capping tests) |
| F3 | `ROOT = "root"` is exempt from the namespace disjointness called "provable" | MUST-FIX | **Accepted, fixed** | A top-level directory named `root/` produces module id `root`, and the previous test skipped exactly that case with `if spec_key == "root": continue`. An exemption carved out of a guarantee, inside the test meant to enforce it. `ROOT` is now `/spec/root` and the test asserts both directions unconditionally |
| F4 | `module-deps` silently drops modules and edges | MUST-FIX | **Accepted, fixed** | Its docstring claimed nothing was summarized away while the cap dropped both. The architecture deriver already diagnosed the identical situation, so this was an inconsistency inside one stage. Now emits SVA-R-004 with counts |
| F5 | The repository root labels itself `root` | SHOULD-FIX | **Accepted, fixed** | Collides in a reader's head with the root spec. Now `(repo root)` |
| F6 | Colliding leaf labels are indistinguishable | SHOULD-FIX | **Accepted, fixed** | `src/api/routes` and `src/worker/routes` both rendered as `routes`. Ids disambiguate; the human-facing text did not. Enough path segments are now added to tell them apart |
| F7 | No reachability invariant on `DiagramSet`; `root_spec` raises a bare `KeyError` | CONSIDER | **Deferred to P1-6** | Real, but the consumer that would suffer is the viewer, which does not exist yet. Fixing it now would guess at the contract the viewer needs. Logged so P1-6 opens with it |
| F8 | `_MAX_EVIDENCE_PER_BOX` private, with a hardcoded `[:6]` duplicating it | SHOULD-FIX | **Accepted, fixed** | Two sources of truth for one cap, one of them a literal. Now a single public `MAX_EVIDENCE_PER_BOX` |
| T1-T5 | Five tests that cannot fail | MUST-FIX | **Accepted, fixed** | Detailed below |

**Nothing rejected.**

---

## The five tests that could not fail

Not a style complaint. Each one covered a real guarantee and would have stayed green while that guarantee broke.

| Test | Why it could not fail | Now |
|---|---|---|
| `test_groups_are_drillable_and_singletons_are_not` | Derived `expected` from `n.attr("modules")`, which the same loop sets from the same `len(members)` that decides `child_spec`. Circular: a stub emitting `modules="1"` and `child_spec=None` everywhere passed | Expectation comes from the fixture. A drillable box must lead to a spec with more than one module; a non-drillable one must be a real module id |
| `test_derivation_is_stable_across_runs` | Called `derive` five times on the *same* graph and clustering objects. Python guarantees identical iteration over identical objects, so this asserted a language property | Rebuilds the whole pipeline each iteration, plus a **new** cross-process test varying `PYTHONHASHSEED`, which is the churn source the in-process version could never see |
| `test_type_only_imports_are_excluded_and_counted` | `if ds.specs:` guarded the assertion. An empty result skipped the check and passed | Asserts `ds.specs` is non-empty first, so an empty result is a failure rather than a pass |
| `test_dependency_layers_survive_a_cycle` | Same `if ds.specs:` shape | Same fix |
| `test_every_element_of_every_spec_carries_evidence` | Tautological: the constructor raises on empty evidence, so no object reaching the assertion could violate it | Kept as a cheap regression against the constructor check being removed, with the tautology stated in the docstring so nobody reads it as coverage |

---

## Promoted to the decision log

1. **A verification claim is scoped to the shapes the fixture contained** (F1). Naming the shape is the difference between a verification and an assumption.
2. **An assertion joined by OR across branches tests neither** (F2). If two code paths can satisfy one assertion, the test cannot tell you which ran. Assert the branch, and assert an observable effect of it, not just its diagnostic text.
3. **A guarantee with an exemption is not a guarantee, and a test that skips the exemption is complicit** (F3). `if x == special: continue` inside a test named after an invariant is the tell.
4. **A test whose expectation is computed from the code under test proves nothing** (T1). Expectations come from the fixture.
5. **Same-process repetition is not a determinism test** (T2). The churn sources that matter are cross-process: hash seed, filesystem order, locale.
6. **A conditional guard around an assertion converts a failure into a pass** (T3, T4). `if <the thing produced output>: assert ...` is silence when it matters most. Assert the precondition.

---

## Measured after

```
325 passed, 1 skipped, 2 xfailed; ruff clean; pyright strict 0 errors
dexter (TypeScript): architecture 7 boxes / 16 edges / depth 2, module-deps 26 boxes / 61 edges
```

**And one more instance of F1, found while writing this line.** The fix commit
`c302fba` closed with "343 tests, ruff clean, pyright strict 0 errors". The real
count at that commit is **323 passed, 2 skipped, 2 xfailed**. Nothing produced
343; the number was carried over from a stale glance at a truncated `pytest -q`
tail and never read off a summary line.

So the commit whose headline finding was *a commit message laundered an
unverified claim* laundered a second one in its own closing line. It is left in
git rather than amended away, because the record of the mistake is worth more
than a tidy history. The rule that follows is narrower and more mechanical than
the F1 rule: **a number in a commit message is copied from a command's output in
the same session, or it is not written.**

---

## Riskiest remaining untested assumption

That the derivation is legible. Every check so far is structural: counts, ids, evidence presence, disjointness. Nothing yet asserts that a human looking at the top-level architecture view of a real repository recognises their own system, which was Spike 0's gate 1 and has not been re-run against the real deriver. P1-6 produces the first artifact a person can actually look at, and that is when this becomes checkable.
