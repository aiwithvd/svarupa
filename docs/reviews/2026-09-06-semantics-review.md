# Review #13: the semantics wave (routes, tasks, entrypoints, roles)

**Date:** 2026-09-06
**Reviewer:** Fable (adversarial)
**Reviewed at:** `af84a8f` · **Fixes:** the commit following this record
**Verdict:** *"The wave's evidence machinery is genuinely sound... but its
facts fail the decision log's own first commandment ('a wrong edge is worse
than a missing one') on four demonstrated fronts, the sharpest being that a
`pyproject.toml` sitting in `tests/fixtures/` mints a committed architecture
record for the production tree."*

The review's demonstrations, all reproduced before fixing: a fixture manifest
produced `entrypoint evil pkg` / `role pkg cli` in the committed lockfile, in
the same commit whose own test proves the route half of that gate; a Flask
`methods=("POST",)` route was recorded as `endpoint GET /pay`; a monorepo
script in `packages/a/pyproject.toml` resolved onto a root-level decoy `pkg/`;
`import fastapi` plus a homemade `@cache.get("user:profile")` recorded
`endpoint GET user:profile`; and five of six reviewer-written mutations
survived the full 603-test suite.

## Findings and triage

| # | Finding | Rank | Disposition | Why |
|---|---|---|---|---|
| F1 | `methods=("POST",)` recorded as GET | MUST-FIX | **Accepted, fixed** | `DecoratorRef.methods` conflated "kwarg absent" (Flask's documented GET default, a fact) with "present but not a literal collection" (unknown). Now `None` vs `()`: tuples of strings count as literal, anything dynamic loops zero times and mints nothing |
| F2 | Python entrypoint targets resolved from the repo root; a decoy captured a monorepo's script | MUST-FIX | **Accepted, fixed** | Anchored at the declaring manifest's directory (plus the one `src/` layout anchor), like the JS branch always was. The test now contains the decoy that demonstrated it, and a `src`-layout test besides |
| F3 | Manifests in test/vendored roles minted committed records | MUST-FIX | **Accepted, fixed** | The same `in_architecture` gate routes and tasks already had, applied to the manifest loop that sat one function below them. Asserted at both the lockfile and the graph level, because after F2's anchoring the lockfile alone can mask a removed gate |
| S1 | The TOML scanner could cite a line inside a multiline string; the promised value cross-check did not exist | SHOULD-FIX | **Accepted, fixed** | `_key_line` now requires the parsed value on the located line; the docstring's promise and the code agree. Gated by a decoy string inside `[project.scripts]` itself |
| S2 | package.json locator cited the first key+value co-occurrence anywhere | SHOULD-FIX | **Accepted, fixed** | The scan is brace-tracked to the `bin` object's own line range; `"config": {"serve": ...}` above `bin` no longer captures the citation |
| S3 | The 1.1 -> 1.2 upgrade was attributed as code drift and as the first PR's changes | SHOULD-FIX | **Accepted, fixed** | `diff()` compares schema minors (SVA-L-013, mirroring the grammar-version rule): new-kind lines come from the tool upgrade, not the change. `drift_check`'s message says so too when minors differ, from its own two inputs, predicting nothing about its caller |
| S4 | TypeScript/JS blindness unscoped: an Express repo shows no api role and no signal | SHOULD-FIX | **Accepted, fixed** | REPORT.md now states the semantics language boundary per run, with an explicit warning line when TS/JS files were scanned; SKILL.md carries the same sentence. Absence and blindness are different facts |
| S5 | Five reviewer mutations survived (quoted-key branch, JS anchoring, role priority, module_roles gating, canonical order) | SHOULD-FIX | **Accepted, fixed** | Each survivor got a test that reds under it and a mutation in the list; the list went 14 -> 28 and re-runs clean. Rebuilding the fixture for the table-scope test showed *its* decoy no longer bit after S1's value check, so the decoy now shares the value and differs only by table |
| S6 | The import gate was file-scoped: `import fastapi` plus a homemade `.get()` claimed a route | SHOULD-FIX | **Accepted, fixed (narrow)** | A route path must start with `/` or be empty (FastAPI's router-prefix idiom); both frameworks reject anything else as a path. Receiver-scoped resolution is the stronger fix and is recorded as the next step for this surface, not silently dropped |
| C1 | Role evidence appended after the cap exceeded `MAX_EVIDENCE_PER_BOX` | CONSIDER | **Accepted, fixed** | One combined, capped list with role citations reserved first, since the role colour is the claim a reader will question |
| C2 | A route path records its source spelling, not its decoded value | CONSIDER | **Accepted, documented** | `DecoratorRef.arg` docstring says "spelled as at the decorator". Raw control characters decode and round-trip correctly (the reviewer verified); escape-sequence spellings are the source's own words |
| C3 | An unreadable manifest was skipped silently | CONSIDER | **Accepted, fixed** | SVA-X-008: degrade and say so; the loading channel is part of the boundary, again |
| C4 | No semantic assertion entered through the CLI door | CONSIDER | **Accepted, fixed** | One test runs `main([repo, "--lock"])` and reads the written lockfile |

## What the review confirmed sound

Hostile route paths (raw tab, raw newline) round-trip the grammar and diff
engine byte-identically; one added route churns exactly one line; reordering
decorators and renaming handlers churn zero; lockfile and `module_roles` hash
identical across four hash seeds on a 108-route fixture; the 1.1 base diffs
the 1.2 head as opaque adds; `build()`'s independent fact re-verification;
dual-role modules emit both `role` records with the box wearing `api` by
priority; legend counts, expand-in-place, and the new CSS kinds all hold.

## Promoted to the decision log

1. **A default is a claim about absence, so presence-but-unknown must be representable** (F1). `methods or ("GET",)` could not distinguish "no kwarg" from "dynamic kwarg", and the conflation invented a method in a committed file. When a field's absence has a documented meaning, the type must have three states, not two.
2. **A resolver is anchored, and its test contains a decoy at the wrong anchor** (F2). Root-relative resolution was correct on every single-package fixture and wrong on the first monorepo, silently; the JS branch had the anchor all along, three lines away.
3. **Every fact channel inherits every gate, checked per channel** (F3). Routes were architecture-gated and manifests were not, one function apart in one file, and the suite proved the half its author remembered.
4. **A locator must agree with the parser on the value, not only the key** (S1/S2). Positionless parsers force line scanners, and a scanner keyed on the key alone cites decoys in strings and sibling objects; requiring the parsed value on the located line is the cross-check that makes scanner and parser one claim.
5. **A tool upgrade is not a code change** (S3). Schema and grammar steps are recorded in both headers precisely so the delta can attribute new-kind lines to the upgrade; recording them and not comparing them left the first post-upgrade PR blamed for the tool's own growth.
6. **Blindness is stated where absence would be read** (S4). A JS repo with no api role must say "not extracted", in the artifact the reader is looking at, not in a design doc.

## Measured after

```
619 passed, 1 skipped, 2 xfailed; ruff clean; pyright strict 0 errors
mutations: semantics 28/28 (was 14), setup 21/21, lock 22/22, emit 16/16, wave11 14/14
descovo: byte-identical across PYTHONHASHSEED 1/42, 101 semantic records
warehouse repo: 23 endpoints; REPORT.md states the python-only boundary
```

## Riskiest remaining untested assumption

The reviewer's, standing: `entrypoint_module`'s contract is string membership
in `graph.nodes`, whose keys are file paths today and `path#symbol` for
symbol nodes elsewhere. A future id-shape change would degrade every
entrypoint to SVA-L-012 warnings as an ordinary-looking diff rather than a
red test. The CLI-door and anchoring tests pin the behaviour from the outside;
a shape change that preserves module-node ids passes them, which is the
correct scope, but the assumption itself is now written down here rather than
implicit.
