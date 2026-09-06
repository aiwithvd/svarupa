# Review #15: Express route chaining and same-file mount composition

**Date:** 2026-09-06
**Reviewer:** Fable (adversarial)
**Reviewed at:** `8e693d6` · **Fixes:** the commit following this record
**Verdict:** *"The composition machinery is real and behaves correctly on
every shape the brief named... but the wave fails the newest entry in its own
decision log for the third consecutive wave and ships a confident wrong
committed edge of exactly the kind #14 S1 was promoted to stop."*

Two demonstrations carried the verdict. A parent-built and a head-built
lockfile both stamped `# schema 1.3`, and their delta was one removed
`endpoint GET /things` plus three additions with `diagnostics: []`: SVA-L-013
could not fire because the minor had not moved, one commit after "the minor
tracks what this build can emit" was written at the constant. And the most
ordinary Express factory idiom (top-level `const router = Router()` mounted at
`/users`, plus `function healthRouter() { const router = Router(); ... }`)
committed `endpoint GET /users/healthz` for a route served at `/healthz`,
because the receiver set was keyed on a name, and composition was the first
consumer for which name-equals-object minted a wrong value rather than a
duplicate right one.

## Findings and triage

| # | Finding | Rank | Disposition | Why |
|---|---|---|---|---|
| F1 | Coverage grew and existing endpoints were re-spelled with no minor bump; SVA-L-013 could not fire, and its sentence covered only "newer kinds" | MUST-FIX | **Accepted, fixed** | SCHEMA_MINOR 3 -> 4 with the rule restated at the constant (a re-spelling reads as one removal and one addition of an OLD kind, so the minor is what attributes the pair). The message now says "lines this build newly emits, no longer emits, or spells differently". Gated by a 1.3-era base holding the mount-relative `GET /things`. The reviewer's own analysis that a MAJOR would be worse (every TS adopter's first post-upgrade diff would refuse) is adopted |
| F2 | A name is not an object, at the mount level: a factory's shadowing `router` composed onto the top-level router's prefix | MUST-FIX | **Accepted, fixed** | A name bound by an express constructor more than once in the file is `shadowed`: it neither mounts nor is mounted, and its routes keep declared paths. The regression fixture is the exact factory idiom. Reassignment to a non-call (`app = axios`) remains the recorded residual |
| S1 | Memoized depth-cap poisoning made composition depend on mount statement order | SHOULD-FIX | **Accepted, fixed** | The cap is gone; cycles are detected on the resolution path (a router already on the path) and poison exactly the routers on it, deterministically. A nine-deep chain mounted root-first and leaf-first now composes identically, tested both orders |
| S2 | 7 of 8 reviewer mutations survived: use-only mount trigger, route-only chain root, slash-less prefix poison, chain cap, memo/cap, sorted-set | SHOULD-FIX | **Accepted, fixed** | Each has a test that reds under it and a mutation in the list (semantics 50 -> 60). `sorted(set(out))` stays as canonical-order hygiene; its equivalence through lock dedup is noted rather than pretended away |
| S3 | "Where every part is cited" was false: a composed route cited only its handler line; every verb of a multi-line chain cited the chain's first line | SHOULD-FIX | **Accepted, fixed** | `RouteFact.via` carries the mount lines a composed path rests on; member calls cite the method-name line, so `.delete(remove)` on line 8 cites line 8. SKILL.md now says exactly what is cited |
| C1 | Trailing-slash spelling depended on mount status: `/things/` and `/things` collapsed onto one key only when mounted | CONSIDER | **Accepted, fixed** | `_join_paths` keeps the sub-path's spelling; `/api/` + `/things/` is `/api/things/`. NestJS keeps its strip because Nest normalizes the same way |
| C2 | Path-less middleware-first mounts (`api.use(auth, users)`) poison, correctly, but the boundary sentence listed non-composing causes as if complete | CONSIDER | **Accepted, fixed** | Named in SKILL.md alongside dynamic prefixes and twice-bound names |
| C3 | The real-repo verification covered none of this wave's shapes (descovo has no Express mounts; its `.route({` is Fastify) | CONSIDER | **Accepted, recorded** | True. The local corpus has no live Express composition; verification is on the scratch app and fixtures, and this record says so rather than implying otherwise |
| C4 | Cycles and self-mounts fell to declared paths silently | CONSIDER | **Accepted, fixed** | SVA-X-009 INFO with the cycle spelled out; a mount cycle is a program bug worth a line |

## What the review confirmed sound

Two routers per `use`; array, substituted-template, regex and identifier
prefixes all poison; a dynamic mount poisons beside a static one; `/api/` +
`/things` composes exactly once; `r.get('/')` at `/api` yields `/api`;
`app.route('')` claims nothing; chained `.all` yields `ALL`; sub-app on app
composes, cross-file does not; CommonJS composes; `.tsx` identical; 16-hop
chains claimed and 17-hop fail closed; computed members and bracket access
claim nothing; `app?.route()` claimed correctly; the first argument is
three-state; escape sequences survive composition; byte-identical across four
hash seeds on cycle, nine-deep, two-host and shipped fixtures; shipped 50/50.

## Promoted to the decision log

1. **A change in how a fact is spelled is a change in what the build emits** (F1). Composition re-spelled existing endpoint records; the minor moved for new kinds and new producers but not for new spellings, and the attribution sentence named only "newer kinds". The minor tracks any difference between what the previous build and this one write for the same code.
2. **When a name-keyed approximation gains a consumer that composes, its failure flips from duplicate to wrong** (F2). Name-equals-object was harmless while both objects received declared paths; the first composing consumer needs the approximation tightened at that consumer, and the tightening is to refuse when the name is bound more than once.
3. **Resolution caps must not be memoized as facts** (S1). A depth cap is a property of the query path, not of the node; memoizing its None made the answer depend on statement order, which is the churn class the lockfile exists to remove. Detect cycles on the path and let the cap go.

## Measured after

```
652 passed, 1 skipped, 2 xfailed; ruff clean; pyright strict 0 errors
mutations: semantics 60/60 (was 50), setup 21/21, lock 22/22, emit 16/16, wave11 14/14
scratch Express+Nest app via the CLI: 5 endpoints; warehouse repo byte-identical
  across PYTHONHASHSEED 1/42; schema stamp 1.4
```

## Riskiest remaining untested assumption

The reviewer's, standing: that a receiver *name* denotes one router object
for the whole file. Shadowing is now refused, which covers the factory idiom;
`app.use('/api', makeRouter())`, reassignment, routers as parameters and
`module.exports = router` re-imports all still fail closed and none has a
fixture, so the next one to flip open will not be caught either. Also carried
from C3: no real-world Express repository is in the local corpus, so
composition has only ever run on fixtures.
