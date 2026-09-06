# Review #16: Wave A, conceptual diagrams (`8987b91`, `ec3c911`, `6152cb4`)

**Date:** 2026-09-06
**Reviewer:** Fable (adversarial)
**Fixes:** the commit following this record
**Verdict:** *"The wave's vocabulary, externals and lockfile plumbing are real
and deterministic... but the headline view is not drawn on the wave's own
acceptance repo and the wave fails 'a name is not an object' at a new
surface."*

The two demonstrations that carried it: the follow-up commit's "externals sink
to the bottom layer" pushed Gemini API and MongoDB into one flow column on the
warehouse repo, the corridor drop crossed a box, the System view was withheld
and the CLI exited 1, with no CLI run recorded for that commit; and
`import jwt` in a repository that owns a `jwt/` package resolved intra-repo
and still committed `role app auth`, because classification ran on the
specifier's name with no knowledge of what the resolver decided.

## Findings and triage

| # | Finding | Rank | Disposition | Why |
|---|---|---|---|---|
| F1 | The follow-up commit withheld the System view on the acceptance repo; the CLI was not re-run there | MUST-FIX | **Accepted, fixed** | The external sink stays for layered/clustered rows (where a store in a cycle's row was the problem) and is off in the flow engine, whose columns stack sunk externals under a corridor. Gated by a unit test that a service's external stays in its inferred column, and by the CLI running on the demo-shaped fixture. Process: the acceptance CLI runs after every commit that touches layout, follow-ups included |
| F2 | An INFO diagnostic withheld a whole canvas and the report gave a circular reason | MUST-FIX | **Accepted, fixed** | Only ERROR severity withholds; every finding still reaches the report. Gated by a hand-built spec whose only finding is SVA-G-014 |
| F3 | Waypoints counted as boundary intruders, dropping honest boundaries and printing NUL-delimited dummy ids | MUST-FIX | **Accepted, fixed** | `_regions` takes the waypoint set and skips it; the skipping-import fixture keeps its boundary and its view. Bends in lines are not boxes, and every geometric check enumerates them |
| F4 | A vocabulary hit was a fact before the resolver was asked: a local `jwt/` package, a tsconfig alias `stripe`, a type-only `ioredis` import all claimed externals | MUST-FIX | **Accepted, fixed** | `semantics()` receives the resolver's `resolve_module`; an import that resolves to the codebase's own file, or is type-only, claims nothing. Three fixtures, one per shape. The removed relative-import guard stays removed for the reason the reviewer confirmed (Python keeps the leading dots) |
| F5 | `lstrip("./")` in two new sites (third recurrence); contexts anchored at the repo root instead of the compose file; groups with outsiders became boundary members | MUST-FIX | **Accepted, fixed** | One `build_context_of()` in build.py: joined to the compose file's directory with path operations, `..`-escaping contexts are None, `.web` survives. At the top level a group is inside a service only if every module it represents is under the context |
| F6 | The System view said "6 databases" for three; image-only services wore the code sigil | SHOULD-FIX | **Accepted, fixed** | Compose images map to the vocabulary's labels (`postgres` -> PostgreSQL); an import-derived store with the same label is the compose box, and a generic family label (`sqlalchemy` = SQL) attaches when exactly one store of that family exists. Services with no build context are `service`, not `backend` |
| S1 | Dotted keys collapsed per file: `google.cloud` and `google.generativeai` became one fact | SHOULD-FIX | **Accepted, fixed** | Facts are keyed on the matched vocabulary key, which `classify_import` now returns |
| S2 | Passport kind empty and sigils grey (kind class only on the rect); expanded views mixed parent and child connections; `data-note` unread; boundary clicks said "connection" | SHOULD-FIX | **Accepted, fixed** | Kind class on the group; parent and child wrapped in `data-scope` groups the passport and hover read from; connections show the note; boundaries carry `data-kind` |
| S3 | The design's "no label over a route" gate did not exist; the mask constants were three separate literals | SHOULD-FIX | **Accepted, fixed** | `Style.label_pad`/`label_gap` shared by engine, validator and renderer; labels settle away from other routes' segments and the validator checks it |
| S4 | 16 reviewer mutations survived | SHOULD-FIX | **Accepted, fixed** | Wave A's list went 15 -> 28, including the emit half (kind class on groups, scopes) and the layout guards, with unit pins where integration fixtures cannot reach |
| C1 | `celery` was both a worker role and a bus box | CONSIDER | **Accepted, fixed** | Removed from the bus table; `kombu` stays. `sqlite3` stays: a database file is a datastore |
| C2 | Boundaries cited the service key, not the `build:` line | CONSIDER | **Accepted, fixed** | The extractor records `build_line`; the region cites it |
| C3 | External sublabel packages were gathered from test files too | CONSIDER | **Accepted, fixed** | Gated on architecture eligibility |
| C4 | Dark default reverses the earlier light-default decision | CONSIDER | **Accepted as a knowing reversal** | Recorded in design §5: Archify's grammar is dark-first and the toggle is remembered per reader |

## What the review confirmed sound

Byte-identical artifacts across seeds on both repos; the 1.4 -> 1.5 delta adds
only role lines with SVA-L-013 firing; roles gated on architecture paths;
relative imports never classify; prefix precedence right on every probe; verb
drawn once per target; externals below modules in layered views; no innerHTML,
every passport string via textContent; hover per-svg and cheap.

## Promoted to the decision log

1. **A vocabulary hit is a claim about a name; the resolver says whether the name is the codebase's own** (F4). Classification that runs before resolution turns every table key into a decoy waiting for a repository that owns a package by that name.
2. **Only an ERROR withholds a view** (F2). An INFO is a note about what could not be drawn; letting it withhold turned "a boundary was not drawn" into "the view vanished", with a circular reason in the report.
3. **Bends in lines are not boxes** (F3). Every geometric check that iterates boxes enumerates the waypoint set, or a long edge inside a boundary drops the boundary.
4. **A build context is a path anchored at the declaring compose file** (F5, the third `lstrip("./")` recurrence). One function owns the normalisation; string stripping of paths is a defect on sight.
5. **Two names for one thing are one box** (F6). Compose images and import packages are canonicalised to the vocabulary's labels before anything is counted or drawn.
6. **A follow-up commit is a wave: the acceptance CLI runs after it** (F1). The withheld view shipped in a commit whose message reported tests and mutations and no artifact.

## Measured after

```
685 passed, 1 skipped, 2 xfailed; ruff clean; pyright strict 0 errors
mutations: conceptual 28/28 (was 15), semantics 60/60, setup 21/21, lock 22/22,
  emit 15/15, wave11 14/14
demo: all three views drawn, byte-identical across PYTHONHASHSEED 1/42;
descovo: all views drawn, System says "1 backend, 4 services, 3 databases, 2 clouds"
```

## Riskiest remaining untested assumption

The reviewer's, narrowed: a vocabulary hit is now a claim about a name the
resolver did NOT bind to the codebase, which still leaves declared-dependency
knowledge unused (a table key that is neither local nor declared as a
dependency is odd and could be diagnosed). And the boundary layout has still
never run on a real repository with a subtree build context; every boundary
so far is a fixture.
