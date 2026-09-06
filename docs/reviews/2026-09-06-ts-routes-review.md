# Review #14: TypeScript routes (Express, NestJS)

**Date:** 2026-09-06
**Reviewer:** Fable (adversarial)
**Reviewed at:** `687ea17` · **Fixes:** the commit following this record
**Verdict:** *"The receiver-scoping machinery is real and the e2e claims
reproduce exactly, but the wave fails the two freshest entries in its own
decision log... `@Get(PATH)` mints a committed `endpoint GET /users` for a
route that lives at `/users/:id`... and re-locking a previously locked Express
repo attributes all six pre-existing endpoints to the first unrelated PR, with
no SVA-L-013... this wave repeats the recurrence pattern §15.1 entry #66
exists to stop."*

## Findings and triage

| # | Finding | Rank | Disposition | Why |
|---|---|---|---|---|
| F1 | `DecoratorRef.arg` conflated "no argument" with "dynamic argument"; `@Get(PATH)` and `@Controller(['a','b'])` composed wrong committed paths | MUST-FIX | **Accepted, fixed** | The exact three-state lesson promoted one commit earlier for Flask's `methods`, violated by the next field of the same shape. `arg_dynamic` added; a dynamic method decorator mints nothing and a dynamic controller prefix excludes its whole class. The regression fixture uses `@Post(PATH)` so the wrongly-composed record cannot hide inside the sibling's legitimate `GET /users` in a set comparison |
| F2 | The coverage expansion shipped without a schema-minor bump, so SVA-L-013 could not fire and six endpoint lines were blamed on the first PR after the upgrade | MUST-FIX | **Accepted, fixed** | SCHEMA_MINOR 2 -> 3, with the rule written at the constant: the minor tracks what this build can emit, not only the kind list, because the delta's upgrade attribution keys on it. Gated by a 1.2-era base diffing this head |
| F3 | CommonJS Express (`const express = require('express')`) was invisible while the report claimed Express coverage | MUST-FIX | **Accepted, fixed** | `require()` is now an import in TS pass 1 (plain and destructured), which serves resolution generally, not only routes; SKILL.md names both dialects. The alternative (an honest boundary sentence) was the fallback; support was cheap enough to be the fix |
| S1 | The receiver gate was on the name, not the object: a helper's `const app = makeCache()` shared the top-level `app` and its `.get('/decoy')` was a wrong committed edge | SHOULD-FIX | **Accepted, fixed** | A name bound by any non-express constructor in the file leaves the receiver set; losing a true route to a name collision is the cheap direction, per fail-closed. Reassignment (`app = axios`) remains the known residual, recorded below |
| S2 | `_ts_string` deleted escape sequences, minting corrupted committed values (`'/a\'b'` -> `/ab`) and letting distinct routes collide onto one key | SHOULD-FIX | **Accepted, fixed** | Escape sequences join in source spelling, matching the Python disposition (#13 C2). Gated by a fixture whose path contains one |
| S3 | 7 of 9 reviewer mutations survived: first-arg-only, slash-spelled prefixes, relative `./express`, alias resolution, ctor identifier guard, handler fallback, decorator arg position | SHOULD-FIX | **Accepted, fixed** | Each got a test and a mutation; the semantics list went 35 -> 44. Two side-findings along the way: the alias split was dead code (C2, removed), and the `is_relative` clause was dead too since a relative specifier spells its `./` (removed, with the exposing mutation retired and the decoy test kept) |
| S4 | Express paths are mount-relative while NestJS composes prefixes, and no artifact stated the asymmetry | SHOULD-FIX | **Accepted, fixed** | SKILL.md states it: NestJS composes (same file, both cited); Express `app.use` mounts and FastAPI `include_router` prefixes are not composed, so a path may be mount-relative. `app.route('/x').get(h)` chaining and same-file `app.use` composition are recorded as the next Express step, not silently dropped |
| C1 | Decorators on non-exported top-level classes were dropped | CONSIDER | **Accepted, fixed** | A class declaration's own `decorator` children now merge with the sibling-paired ones; valid Nest without `export` is seen |
| C2 | The Router/default split unioned into one set: a distinction the data structure cannot express | CONSIDER | **Accepted, fixed** | One rule: any express-imported name is a route-holder constructor when called, plus `.Router` on any of them |
| C3 | `exported` lost through the declaration hop; `"javascript"` lang member dead | CONSIDER | **Accepted, fixed (first half)** | `lexical_declaration`/`variable_declaration` now pass `exported` through, pinned by a pass-1 test. The `"javascript"` member stays: the extractor labels `.js` facts "typescript" today, and removing the member would make a future lang-labelling fix silently disable JS routes |

## What the review confirmed sound

The scratch e2e reproduces (export-declarator ctor capture cleared); typed
declarators, `.tsx`, aliased defaults, namespace Nest decorators, bare
`@Get()`, destructuring, class expressions all behave; test-fixture manifests
and routes mint nothing (the #13 gate inherited by the new channels);
`:param`/`*`/raw-tab paths round-trip grammar and parse; handler renames churn
zero; byte-identical across three seeds; both shipped decoys genuinely red.

## Promoted to the decision log

1. **A promoted three-state lesson is walked against every field of the same shape, in the same commit** (F1). `methods` got absent/known/dynamic and `arg` did not, one field over, one commit later; the wrongly-composed path was byte-identical to a legitimate sibling record, which is the worst kind of wrong edge.
2. **The schema minor tracks what this build can emit, not only the kind list** (F2). The delta's upgrade attribution keys on the minor; growing coverage inside an existing kind without bumping it re-opens exactly the misattribution the minor exists to close.
3. **A dialect is a coverage boundary: an import gate that reads ESM does not read CommonJS** (F3). "Express is covered" was false for the majority spelling of Express programs, and the overclaim lived in the very sentence added to state boundaries honestly.
4. **A name is not an object** (S1). Receiver sets keyed on identifiers subtract every name a foreign constructor binds anywhere in the file; losing a true claim to a collision is the cheap direction, inventing one is the expensive one.
5. **String extraction keeps source spelling; dropping unrecognized child nodes corrupts values** (S2). A joiner that enumerates the node types it keeps must fail closed (return nothing) or keep the source text for the rest, never silently delete.
6. **Where one framework composes and another does not, the artifact says so** (S4). A reader comparing a NestJS full path against an Express mount-relative path is comparing two conventions; unstated, that difference reads as fact.

## Measured after

```
636 passed, 1 skipped, 2 xfailed; ruff clean; pyright strict 0 errors
mutations: semantics 44/44 (was 35), setup 21/21, lock 22/22, emit 16/16, wave11 14/14
scratch Express+Nest app via the CLI: 5 module-keyed endpoints incl. composed
  /users/:id; CommonJS file now contributes its routes
descovo byte-identical across PYTHONHASHSEED 1/42; schema stamp 1.3
```

## Riskiest remaining untested assumption

The reviewer's, standing and now partially mitigated: that `ctor_assigns`'
flat name-set approximates data flow. The poisoning rule closes same-file
constructor collisions; reassignment to a non-call (`app = axios`), factories
returning routers, receivers passed as parameters, and `app.route().get()`
chaining remain outside the approximation, all currently failing closed
(claiming nothing). The boundary is now stated in SKILL.md for the mount case;
the chaining idiom is the next one real code will hit.
