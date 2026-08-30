# Review Prompt Template

Copy this for every component review. Keeping the shape constant is what makes
findings comparable across components and lets a reviewer see whether an earlier
concern was addressed or quietly dropped.

---

## Prompt skeleton

```
You are performing review #<N> in a standing per-component adversarial review
protocol for a greenfield tool called Svarupa. This review covers <COMPONENT>.

## Read these, in order
1. docs/superpowers/specs/2026-08-30-svarupa-design.md   (design, incl. §15 decision log)
2. <the component source files>
3. <the component test files>
4. docs/reviews/<previous review>.md                      (what was already established)

## What previous reviews established (do not relitigate)
<bullet list of accepted findings with their one-line reasoning>

## What this component was supposed to do
<the plan's spec for this component, verbatim>

## Your job
Verify the implementation against the spec. Do not take the author's word for
anything; read the code.

<component-specific stress axes — see below>

## Output format
- Verdict: does this component meet its spec? One paragraph.
- Findings, each with: what's wrong, why it matters, recommended fix, and the
  reasoning behind that fix. Rank MUST-FIX / SHOULD-FIX / CONSIDER.
- Riskiest remaining untested assumption.

Be direct and skeptical. The author is motivated to declare success. Find where
they fooled themselves.
```

---

## Standing stress axes (every component)

Ask these every time, regardless of component:

1. **Determinism.** Does anything here depend on iteration order, locale, hash
   seed, filesystem order, wall-clock time, or absolute paths?
2. **Evidence integrity.** Can any element reach the graph without evidence?
   Does every evidence range actually exist at those lines?
3. **Boundary purity.** Does this module reach outside its declared inputs?
   (`derive/` reads only the graph; `layout/` reads only a DiagramSpec.)
4. **Failure honesty.** When this cannot do its job, does it say so loudly, or
   does it emit something plausible? Silent degradation is the cardinal sin.
5. **Test adequacy.** Do the tests actually exercise the claim, or do they pass
   vacuously? Construct an input where the test passes but the code is wrong.

---

## Component-specific axes

| Component | Additional axes |
|---|---|
| `detect` | Symlink loops, traversal outside root, NFC normalization, exclusion defaults not over- or under-matching, workspace detection across four ecosystems, size and count caps |
| `extract` | Resolution correctness per language, candidate-edge arity, scorecard honesty (external vs failed-to-resolve must be distinguishable), grammar version pinning, adversarial and malformed input |
| `build` | Dedup correctness, fail-closed enforcement cannot be bypassed, module aggregation from file-level edges, global resolution never runs incrementally |
| `cluster` | Seeding actually wired, canonical edge ordering, no cluster id can reach the lockfile (assert it), behavior when the native wheel is missing |
| `derive` | Returns None rather than fabricating, hierarchical zoom leaf degradation, declared boundary exceptions honored |
| `layout`/`emit` | Geometry invariants, coordinate quantization, evidence links resolve to correct lines, viewer degrades without JS |
| `lock` | Grammar and escaping, schema evolution policy, byte identity across platforms, churn on synthetic refactors is exactly zero |

---

## Triage table

Every review file ends with this. **A rejected finding needs a reason recorded
just as much as an accepted one** — that is what stops the same objection
resurfacing three reviews later.

| # | Finding | Rank | Disposition | Why |
|---|---|---|---|---|
| 1 | | MUST-FIX | Accepted | |
| 2 | | SHOULD-FIX | Rejected | |
| 3 | | CONSIDER | Deferred to P<n> | |

Accepted findings are promoted into design doc **§15 decision log** with their
reasoning, so the next reviewer inherits the rationale instead of relitigating.

---

## Gate

A component is not done until:

- [ ] Review run and written to `docs/reviews/YYYY-MM-DD-<component>-review.md`
- [ ] Every finding triaged with a recorded why
- [ ] Accepted MUST-FIX findings fixed, or explicitly deferred with reasoning
- [ ] Accepted findings promoted to design §15
