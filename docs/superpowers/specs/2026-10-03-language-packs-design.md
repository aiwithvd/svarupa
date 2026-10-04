# Svarupa: Language Packs (step 1a)

Date: 2026-10-03
Status: spec approved 2026-10-03; revised 2026-10-04 after reading the
extractors closely (see "Revision 1" at the end)
Parent: `2026-10-03-system-health-umbrella-design.md`

## Goal

Make code extraction generic. Each language is described by a language pack
(mostly data) that one engine runs, instead of a hand-written extractor. This
is what lets diagrams and every later health check work for any language.

## Current state (measured 2026-10-03)

- Two hand-written extractors, `extract/python.py` (460 lines) and
  `extract/typescript.py` (785 lines), walk syntax trees by hand. Neither uses
  tree-sitter queries.
- Both produce `FileFacts` (`extract/base.py`): symbols, imports, calls,
  fields, re-exports, constructor assigns, diagnostics. Everything after
  extraction (resolve, semantics, build, derive, lock) reads only `FileFacts`.
- Import resolution in `extract/resolve.py` is split by language
  (`_resolve_python`, `_resolve_ts`). Call resolution is driven by
  `CallShape` and is already language-neutral.
- `extract/rationale.py` parses Python and TS a second time for comments,
  with its own suffix lists.
- About half of the extractor code is node names and lists that can become
  data. The rest is real per-language logic: scope tracking, string decoding,
  import quirks (Python relative levels and `__init__` re-exports, CommonJS
  `require`, alias direction), decorator pairing, call-shape edge cases.
- There are no fact-level golden tests. The safety net is whole-artifact byte
  tests (`test_determinism.py`, `test_emit.py`, `test_lock.py`) and the
  mutation scripts.
- Two bugs found while mapping:
  1. `.jsx` is parsed with the TS grammar, not TSX, so JSX in `.jsx` files
     can mis-parse (`typescript.py:153`).
  2. `.js` facts are labelled `typescript` (`typescript.py:484`), so every
     `javascript` branch in resolve, semantics and vocabulary never runs.

## Design

### Pack layout

```
svarupa/extract/packs/
  __init__.py     registry: which pack serves which detected language
  model.py        the rule types a pack is written in
  walker.py       the one engine that turns a syntax tree into FileFacts
  python.py       Python pack: node rules (data) plus hooks
  typescript.py   TypeScript pack
  javascript.py   JavaScript pack (reuses the TypeScript hooks)
```

A pack is a typed Python value, `Pack(lang, grammar, maturity, rules,
decorator, qualified_prefix)`. `rules` maps a syntax node type to one of
seven rule kinds:

| Rule | Meaning |
|---|---|
| `Define` | a definition (class, function, method, interface): name field, body field, whether it opens a class scope, how `exported` is decided, own and member decorators, optional `bases` and `after` hooks |
| `Decorated` | decorators wrapping one definition (Python `decorated_definition`) |
| `Import` | an import statement, parsed by the pack's import hook |
| `Call` | a call, parsed by the pack's call hook; children are still walked |
| `Field` | a typed class field, parsed by the pack's field hook |
| `Carry` | walk the children and keep the `exported` flag (`export const`) |
| `Custom` | the pack's hook owns this node and its walk |

Node types without a rule are walked through. Hooks are plain functions
with typed signatures; they hold what data cannot say: literal decoding,
import spelling (relative levels, aliases, `require`, type-only), call-shape
edge cases (`super(`, `this.x.m()`, call chains).

### Engine

`svarupa/extract/packs/walker.py` holds `Ctx` (one per file), `Frame`
(scope stack, enclosing class and function, exported flag, pending
decorators) and `PackExtractor`:

- Facts come out in document order. Several resolver steps are
  first-seen-wins, so this order is part of the contract.
- Nesting deeper than `MAX_AST_DEPTH` stops the walk below that depth and
  emits SVA-X-003; syntax errors emit SVA-X-001. Both are written once, here.
- One Python frame per tree level on the generic path, so the depth cap
  stays well under the interpreter's recursion limit.
- `Extractor.parse(path, data) -> FileFacts` stays the public contract, so
  nothing downstream changes.
- Branch points and comments for 1c are added to the rule kinds when 1c
  needs them; `rationale.py` keeps its own parse until then.

### Resolvers

`resolve.py`'s per-language import resolution becomes five resolver kinds,
selected by the `resolver` field of the pack:

| Kind | Languages | Reads |
|---|---|---|
| `dotted` | Python, Java, Kotlin | package roots, suffix list, `__init__`-style package files |
| `path` | JavaScript, TypeScript, Ruby, PHP `require` | relative paths, index files, tsconfig paths, workspace packages |
| `module-file` | Go, Rust, Swift | `go.mod` / `go.work`, `Cargo.toml`, `Package.swift` |
| `namespace` | C#, PHP `use` | namespace declarations mapped to files |
| `include` | C, C++ | include directories from the repo layout and `compile_commands.json` when present |

Settings such as suffixes, index names and stdlib lists come from
the pack. Call resolution stays as it is. Known-external classification
uses the pack's stdlib list plus declared dependencies; anything else is
unresolved and counted.

### Resolvers and new languages

Resolver kinds and the Go and Java packs are the second plan of 1a
(`1a-2`). The first plan (`1a-1`) builds the engine, ports Python and
TypeScript with zero diff, fixes JavaScript and reports unanalyzed
languages.

### Grammars and install

Every grammar is an `==`-pinned wheel; all are MIT with prebuilt abi3 wheels
(checked on PyPI 2026-10-03):

| Language | Wheel | Version |
|---|---|---|
| Python | tree-sitter-python | 0.25.0 (current pin) |
| TypeScript, TSX | tree-sitter-typescript | 0.23.2 (current pin) |
| Go | tree-sitter-go | 0.25.0 |
| Java | tree-sitter-java | 0.23.5 |
| C# | tree-sitter-c-sharp | 0.23.5 |
| Kotlin | tree-sitter-kotlin | 1.1.0 |
| Swift | tree-sitter-swift | 0.7.3 |
| Ruby | tree-sitter-ruby | 0.23.1 |
| PHP | tree-sitter-php | 0.24.1 |
| Rust | tree-sitter-rust | 0.24.2 |
| C | tree-sitter-c | 0.24.2 |
| C++ | tree-sitter-cpp | 0.23.4 |

Versions are re-checked when each pack lands. All twelve are in the default
install. A self-check test loads each grammar under the pinned `tree-sitter`
binding, so an ABI mismatch fails in CI, not on a user's machine.

### Lockfile

The header already records only grammars that contributed a fact
(`lock/build.py:196`). Adding packs therefore does not change the lockfile of
a repository that does not use the new languages. No schema change.

### Errors

| Case | Behavior |
|---|---|
| Grammar wheel missing or fails to load | Language reported "detected, not analyzed"; new SVA-X-012 |
| File fails to parse, or a hook raises | SVA-X-004, file skipped (today's behavior) |
| Nesting too deep | SVA-X-003 (today's behavior) |
| Invalid pack (rule names a node type or field the grammar lacks, pin mismatch) | Caught by pack self-check tests; never ships |

## Work order

1. **Golden facts.** Fixture repos under `tests/packs/python/` and
   `tests/packs/typescript/` covering relative imports, `__init__`
   re-exports, aliases, decorators (FastAPI, Flask, NestJS), `require`, DI
   fields, call shapes and deep nesting. Expected `FileFacts` are generated
   from the current extractors and committed as canonical JSON.
2. **Engine, capture contract, resolver kinds** with unit tests.
3. **Port Python and TypeScript to packs** with zero diff: golden facts,
   whole-artifact byte tests, mutation scripts and the repository's own
   `architecture.lock` all unchanged. Old extractors are deleted in the same
   PR once parity holds.
4. **Bug fixes, separate commit:** `.js`/`.jsx`/`.mjs`/`.cjs` get a
   `javascript` pack that reuses the TypeScript hooks and parses with the
   TSX grammar, so JSX in `.js` and `.jsx` files parses; `.tsx` stays on
   TSX and `.ts` on TypeScript. Golden facts
   are updated in that commit, and the release notes state the label
   change.
5. **Go and Java packs** (`experimental`), each with a fixture repo, golden
   facts and a measured import-resolution rate.
6. **Follow-up PRs, one per language:** C#, Kotlin, Swift, Ruby, PHP, Rust,
   C, C++. Each has the same fixture, golden facts and resolution rate.

Steps 1 to 5 are the 1a PR series; step 6 is one small PR per language.

## Testing

- Golden facts per pack (canonical JSON, byte compare).
- Pack self-checks: every rule names a node type and field that exist in
  the grammar, the grammar version equals the installed wheel and the
  `pyproject.toml` pin, every detected language has a pack or is reported.
- Existing determinism, emit and lock byte tests, run on Linux and macOS,
  Python 3.10 to 3.13, with `PYTHONHASHSEED` varied.
- Mutation scripts: add mutations for the engine's ordering and the depth
  cap.
- Per-language import resolution rate recorded in the pack's test, so
  maturity starts from a number.

## Out of scope

- Framework facts for new languages (Spring, Rails, ASP.NET, Gin routes).
- Code metrics and checks (1c reads `@branch` and definitions later).
- User-written packs loaded from outside the package.
- Languages beyond the twelve above.

## Decision log

| Decision | Choice | Why |
|---|---|---|
| Pack form | Rule table (data) plus typed hooks | About half the logic is data; the rest needs code; see Revision 1 |
| Grammar source | Pinned wheels, not tree-sitter-language-pack | That package downloads parsers at runtime, which breaks offline use and byte-identical output |
| Default languages | All twelve | Covers backend, enterprise, mobile, web and systems code |
| Port first | Golden facts, then zero-diff port | No fact-level tests exist today; parity must be provable |
| Bug fixes | Separate commit after parity | They change output on purpose and must be visible |
| Rollout | Go and Java first, then one PR per language | Small reviews; each language proven by its own fixture |
| Lockfile | No schema change | Header already lists only contributing grammars |

## Revision 1 (2026-10-04)

Found while writing the implementation plan, by reading every branch of
`python.py` and `typescript.py`:

- **Rule table walk instead of `.scm` queries.** The extractors depend on
  where the walk does not go: calls inside decorator arguments, default
  arguments and class base lists are not recorded; decorators pair with the
  next sibling; `exported` passes through `export` and `const` wrappers.
  Queries match anywhere in the tree, so reproducing this byte for byte
  would need a second walk anyway. A rule table keeps the walk explicit,
  is still data, and makes the zero-diff port provable.
- **Packs are typed Python values, not `pack.toml`.** Pyright checks them
  in strict mode, nothing extra has to be shipped as package data, and the
  hooks they name are type-checked against the rule signatures.
- **Packs live in `svarupa/extract/packs/`.** Their facts are the types in
  `svarupa/extract/base.py`; inside the `extract` package the import order
  is fixed and cannot cycle.
- **JavaScript uses the TSX grammar, not tree-sitter-javascript.** The
  TypeScript hooks depend on TypeScript node shapes (`extends_clause`,
  `class_heritage`) that the JavaScript grammar spells differently; TSX
  parses JSX and plain JavaScript, and JavaScript has no `<T>x` casts for
  TSX to misread. No new dependency.
- **Resolver kinds, Go and Java move to plan 1a-2**, so plan 1a-1 is one
  reviewable series that changes no output except the JavaScript fix and
  the new SVA-X-012.
