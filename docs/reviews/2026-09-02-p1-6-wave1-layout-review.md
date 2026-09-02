# Review #8: P1-6 wave 1 (layout) and the diagnostic registry

**Date:** 2026-09-02
**Reviewer:** Fable (adversarial)
**Reviewed at:** `9d7d422` · **Fixes at:** `bea2fe5`, `17ea79a`
**Verdict:** *"This component does not meet its spec... the design's named invariant edge-through-node crossings is not implemented while a docstring claims the engine makes it unnecessary."*

---

## The finding that is about process, not code

Two of them, and both are mine rather than the code's.

**The commit shipped two stale nested copies of the entire layout package.**
`svarupa/layout/layout/` and a third level below it, tracked in git. The cause
was mine and mundane: the mutation-testing loop ran `cp -r svarupa/layout
/tmp/layout.bak` against a directory that already existed from an earlier
batch, so the tree was copied *inside* itself, and the restore copied the
nesting back into the package.

The consequences were not mundane:

* Svarupa run on itself reported three phantom modules, so the tool's own
  architecture diagram became a wrong claim about the tool.
* The registry meta-test rglobs the package, so the dead copies counted as
  emitters. The reviewer demonstrated that deleting a live code emission left
  the test green, satisfied entirely by files nothing imports. **The dead-code
  direction that commit existed to add was already defeated by that same
  commit.**

The generalisation: *a scan over a source tree certifies dead code as alive,
and the file list of a commit is part of the change under review, not
packaging noise.*

**And the invariant I argued instead of checking.** Design §6.1 lists
edge-through-node crossings as validated. I did not implement it, and wrote a
routing docstring saying vertical segments stay in the row gaps *"which is why
the geometry check for a segment crossing a box interior can pass rather than
being quietly omitted."* That sentence is the whole failure in one line: it
claims an omission is safe on the strength of a property the code does not
have. Implementing the check first showed 80+ crossings on one real
architecture view and 100+ on its module deps.

---

## Findings and triage

| # | Finding | Rank | Disposition | Why |
|---|---|---|---|---|
| F1 | Two stale nested copies of the package committed; they poison self-analysis and neutralize the new meta-test | MUST-FIX | **Accepted, fixed** | Detailed above. The emission scan now walks the import graph from `svarupa` and `svarupa.cli` and counts only live modules, plus a test asserting no orphan module exists. Reachability comes from imports rather than the filesystem, because the filesystem is what was wrong |
| F2 | Routes pass through box interiors in two common shapes, and validation is structurally unable to notice | MUST-FIX | **Accepted, fixed** | Detailed above. Routing is now structural: every horizontal run in a row gap, every vertical run in one gap or a reserved side lane beyond every box. The lane is reserved unconditionally, so canvas width does not depend on edge topology |
| F3 | Evidence-free boxes and routes validate clean | MUST-FIX | **Accepted, fixed** | `Box` and `Route` have no constructor check and `validate` never looked, so the product's central promise was unenforced at the last gate before a browser. Review #2 F10 already ruled that the pipeline gate is the contract; this was the third component to apply that decision only where a previous review pointed |
| F4 | Non-finite and non-integer coordinates unchecked; NaN defeats every other check | MUST-FIX | **Accepted, fixed** | A box at `(nan, nan)` passed everything, because `nan < 0` and `nan > width` are both `False`. The plan names non-finite explicitly. Now a `type(v) is int` check, which subsumes it and moves the integer promise from a property of the engines to a property of the gate |
| F5 | Band labels make false depth claims after row wrapping | SHOULD-FIX | **Accepted, fixed** | 40 boxes all at depth 0 produced bands "level 1" through "level 4", four wrong claims, each geometrically contained so validation said nothing. One band per level now, spanning its wrapped rows; a cycle band says "in a cycle" rather than inventing a number |
| F6 | `_routes` silently drops edges whose endpoint has no box | SHOULD-FIX | **Accepted, fixed** | The `continue` turned a derive defect into a clean canvas and hid it from SVA-G-004, which exists for exactly that case |
| F7 | Fan-out collapses at out-degree 6; the test stops at 4 | SHOULD-FIX | **Accepted, fixed** | Edges 6 and 7 landed exactly on 1 and 2, verbatim the failure the docstring claimed to prevent. Spread over actual degree, tested at 4, 6, 7 and 12, plus a 200-degree case asserting the points stay inside the box |
| F8 | `Style` accepts values that make the validator lie consistently with the engine | SHOULD-FIX | **Accepted, fixed** | `box_pad_x=-20` produced a 202px box holding a 242px label, blessed by the geometry check, because `budget = w - 2 * pad` grew instead of shrinking. My own commit narrated finding this class and fixed only the one instance |
| F9 | Text model edges: orphaned combining mark, emoji width, `sanitize` is not HTML escaping, "integer arithmetic" overstated | CONSIDER | **Partly fixed, partly carried** | Orphan marks now trimmed; `sanitize` documents that escaping is the embedder's job and that a directory named `</script>` is scannable. The emoji and non-Latin-script width gaps are **carried to wave 2** as a rendering backstop, since they are not fixable in Python. The "integer arithmetic" claim was overstated: `advance` is float multiplication under `ceil`. Recorded rather than reworded away |
| F10 | No reachability re-check after withholding | CONSIDER | **Accepted, fixed** | Promoted from CONSIDER because wave 2 consumes it directly. Reported, never repaired: pruning a dangling link would turn a drillable group into a leaf and tell a reader it has no internal structure |

**Nothing rejected.** The reviewer also confirmed the `clustered` narrowing from
communities to depth bands is sound rather than a rationalisation, since §15.1
genuinely forbids community identity outside `derive`. But it then held the
narrowed claim to its own standard, which is F5.

---

## The three mutations that survived

Fifteen mutation checks ran. Three survived the first pass, and every one was a
real test gap rather than an equivalent mutant.

| Mutation | Why nothing caught it | Fix |
|---|---|---|
| Crossing check with closed intervals | Nothing in any fixture grazed a box, so the open-interval boundary was asserted only in a docstring. It matters: a route legitimately leaves the edge of the box it starts on | A fixture whose horizontal run lies exactly along a non-endpoint box's top edge, asserted as touching |
| Same-row route drawn across its own row | My reconstruction ended at the target's **left** edge where the original ended at its **right**, so it grazed instead of crossing | Reconstructed with the coordinates that actually failed, plus a sanity assertion that the reconstructed polyline does cross |
| Combining-mark orphan trim removed | The fixture used a literal e-acute, which the editor stored precomposed as U+00E9, so the string held no combining marks at all and the test could not fail | Written with explicit escapes, plus an assertion that the fixture is decomposed |

The third is the same lesson as the invisible-character deny-list: **when the
exact codepoint is the subject, write the codepoint.** A literal is whatever
the editor decided.

---

## Promoted to the decision log

1. **A scan over a source tree certifies dead code as alive** (F1). Liveness comes from the import graph, and a commit's file list is part of the change.
2. **A docstring arguing that a check is unnecessary is a check that does not exist** (F2). When a design names a validated invariant, either the validator contains it or the artifact says plainly that it is unchecked. An engine-property argument in prose is not a gate, because the next engine edit voids it silently.
3. **A check against a pathological value must be a type test, not a range test** (F4). Every comparison with NaN is false, so ordered comparisons pass exactly the input the requirement names.
4. **A label claiming a semantic fact must be computed from that fact** (F5). Deriving it from a presentation artifact makes it false the moment presentation and semantics diverge, and wrapping is the built-in divergence.
5. **A producer-side guard that skips input the validator would reject converts a failure into a pass** (F6). The code-side twin of a conditional guard around an assertion.
6. **A parameter read by both producer and checker must be validated at construction** (F8). Past that point no check is independent, because the checker co-computes with the corrupt value.
7. **When the exact codepoint is the subject, write the codepoint** (mutation 3). A literal is whatever the editor's normalization decided, and a test about combining marks whose fixture is precomposed tests nothing.

---

## Found independently while the review ran

Running the CLI against a real 10,403-file project crashed the entire analysis:

```
RecursionError: maximum recursion depth exceeded
  svarupa/extract/typescript.py:336 in visit
  [Previous line repeated 992 more times]
  .../evidence/js_bundles/7e9d546a1c3779e92ebdc85a75742b71.js   15,966 bytes
```

One minified bundle, of 10,403 files, took down the run. Two defects: the
extractor's tree walk recurses per AST node, and `extract()` wraps only
`read_bytes` in its `try`, so any parse failure escapes. That is a direct
violation of "partial failure must degrade, not disable" in the stage that has
the most hostile input in the system. **Carried as the first item of the next
extract touch**, tracked in the plan.

---

## Measured after

```
409 passed, 1 skipped, 2 xfailed; ruff clean; pyright strict 0 errors
dexter architecture 7 canvases / 0 withheld / 0 crossings; module-deps 1 / 0 / 0
```

---

## Riskiest remaining untested assumption

The reviewer's answer, which I accept unchanged: that the pinned monospace
stack renders every label at or under 0.62 em per cell. Every fits-in-the-box
verdict rests on it, it is untestable until the viewer exists, and it is
already known false for any script the stack's fonts lack, **including the
Devanagari this project is named in.** Wave 2 needs a rendering-side backstop,
so that a wrong width model produces a clipped label rather than an
overflowing one.
