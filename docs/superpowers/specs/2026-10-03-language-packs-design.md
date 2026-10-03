# Svarupa: Language Packs (step 1a)

Date: 2026-10-03
Status: design approved, spec under review
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
svarupa/packs/<lang>/
  pack.toml    name, extensions, grammar (wheel, pinned version, entry point),
               resolver kind and its settings, stdlib and builtin lists,
               maturity (stable | experimental)
  facts.scm    tree-sitter queries using the fixed capture names below
  hook.py      optional; only what data cannot express
```

Fixed capture names (the contract between packs and the engine):

| Capture | Produces |
|---|---|
| `@scope` | pushes a scope (module, class, function) for qualified names and enclosing class or function |
| `@definition.class`, `.function`, `.method`, `.interface` with `@name` | `SymbolRef` |
| `@base` | class bases |
| `@decorator` | `DecoratorRef` |
| `@import` with `@import.specifier`, `@import.name`, `@import.alias` | `ImportRef` |
| `@export` | exported flag, re-exports |
| `@call` with `@call.callee`, `@call.receiver` | `CallSite` and its `CallShape` |
| `@field` with `@field.type` | `FieldType` |
| `@branch` | branch points (if, loop, case, catch, boolean operators); unused in 1a, read by 1c for complexity |
| `@comment` | rationale extraction, replacing `rationale.py`'s own parse |

`hook.py` may define any of these functions; the engine calls them when
present:

- `decode_string(node) -> str | None`: literal decoding (f-strings, template
  strings, escapes).
- `normalize_import(ref, node) -> ImportRef`: relative levels, alias
  direction, `require`, type-only imports.
- `call_shape(node, captures) -> CallShape`: edge cases such as `super(`,
  `this.x.m()` and call chains.
- `post_file(facts) -> FileFacts`: file-level fixes such as Python
  `__init__` re-exports.

### Engine

`svarupa/packs/engine.py` loads packs, compiles queries once per process,
and turns matches into `FileFacts`:

- Matches are processed in document order: start byte, then pattern index
  in `facts.scm`. Today's code is first-seen-wins in several places, so this
  order is part of the contract.
- A scope stack built from `@scope` captures gives qualified names,
  `enclosing` and `enclosing_class`.
- Nesting deeper than `MAX_AST_DEPTH` stops fact collection below that depth
  and emits SVA-X-003, the same as today.
- `Extractor.parse(path, data) -> FileFacts` stays the public contract, so
  nothing downstream changes.

### Resolvers

`resolve.py`'s per-language import resolution becomes five resolver kinds,
selected by `resolver =` in `pack.toml`:

| Kind | Languages | Reads |
|---|---|---|
| `dotted` | Python, Java, Kotlin | package roots, suffix list, `__init__`-style package files |
| `path` | JavaScript, TypeScript, Ruby, PHP `require` | relative paths, index files, tsconfig paths, workspace packages |
| `module-file` | Go, Rust, Swift | `go.mod` / `go.work`, `Cargo.toml`, `Package.swift` |
| `namespace` | C#, PHP `use` | namespace declarations mapped to files |
| `include` | C, C++ | include directories from the repo layout and `compile_commands.json` when present |

Settings such as suffixes, index names and stdlib lists come from
`pack.toml`. Call resolution stays as it is. Known-external classification
uses the pack's stdlib list plus declared dependencies; anything else is
unresolved and counted.

### Grammars and install

Every grammar is an `==`-pinned wheel; all are MIT with prebuilt abi3 wheels
(checked on PyPI 2026-10-03):

| Language | Wheel | Version |
|---|---|---|
| Python | tree-sitter-python | 0.25.0 (current pin) |
| TypeScript, TSX | tree-sitter-typescript | 0.23.2 (current pin) |
| JavaScript, JSX | tree-sitter-javascript | 0.25.0 |
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
| Invalid pack (query does not compile, unknown capture, duplicate extension) | Caught by pack self-check tests; never ships |

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
4. **Bug fixes, separate commit:** `.js`/`.jsx` get a `javascript` pack on
   the JavaScript grammar (JSX included); `.tsx` stays on TSX. Golden facts
   are updated in that commit, and the release notes state the label
   change.
5. **Go and Java packs** (`experimental`), each with a fixture repo, golden
   facts and a measured import-resolution rate.
6. **Follow-up PRs, one per language:** C#, Kotlin, Swift, Ruby, PHP, Rust,
   C, C++. Each has the same fixture, golden facts and resolution rate.

Steps 1 to 5 are the 1a PR series; step 6 is one small PR per language.

## Testing

- Golden facts per pack (canonical JSON, byte compare).
- Pack self-checks: queries compile, capture names are known, grammar
  version equals the installed wheel, no extension is claimed twice.
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
| Pack form | Data (queries and config) plus optional hook | About half the logic is data; the rest needs code |
| Grammar source | Pinned wheels, not tree-sitter-language-pack | That package downloads parsers at runtime, which breaks offline use and byte-identical output |
| Default languages | All twelve | Covers backend, enterprise, mobile, web and systems code |
| Port first | Golden facts, then zero-diff port | No fact-level tests exist today; parity must be provable |
| Bug fixes | Separate commit after parity | They change output on purpose and must be visible |
| Rollout | Go and Java first, then one PR per language | Small reviews; each language proven by its own fixture |
| Lockfile | No schema change | Header already lists only contributing grammars |
