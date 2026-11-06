# Parameter and local types

## Problem

A call on a value whose type is only known from a function parameter is never
resolved. Some examples:

- Rust `fn h(ctx: &Ctx) { ctx.user_id() }`
- Java `void place(OrderService svc) { svc.place(); }`
- Python `def h(repo: Repo): repo.save()`

This is the main gap left in the benchmark. It covers 13 of the 14 misses in
zero-to-production. It is the same in every language.

## Decisions (approved 2026-11-07)

- **Scope.** All six typed packs: Rust, Go, Java, C#, TypeScript, and Python
  type hints. JavaScript has no types, so it gets nothing.
- **What counts as a type.** Only typed parameters and locals with an explicit
  type: `let x: T`, `T x = ...`, `x: T = ...`, `var x T`. There is no
  inference. `var svc = new T()`, `let s = T::new()` and `x := NewT()` do not
  count.

## Design

- **Fact.** Add a new language-neutral fact, `FileFacts.locals: tuple[LocalType, ...]`.
  `LocalType(function, name, type_name)` uses the same qualified function name
  that `CallSite.enclosing` carries. The default is `()`, so packs that set
  nothing are unchanged.
- **Extraction.** Each pack fills locals through data rules, plus the fewest
  hooks it needs. `self`, `this` and receiver parameters are not locals, and
  neither are destructured patterns.
- **Wrappers.** Each pack names the wrappers to see through, as data:
  - Rust: `&`, `&mut`, `Arc<T>`, `Rc<T>`, `Box<T>`, `web::Data<T>`, `State<T>`, `Extension<T>`
  - Go: `*T`
  - TypeScript: `T | undefined`, `T | null`, `Readonly<T>`
  - Python: `Optional[T]`, `T | None`, `Annotated[T, ...]`
  - Java and C#: none

  Generic parameters (`S: Store`, `<T>`) and unions of two real types are not
  types. They stay unresolved.
- **Resolution.** For a QUALIFIED call `x.m()` inside function `f`:
  1. If `(file, f, x)` is in locals, resolve `m` through that type, the same
     way `_field_call` does (`_nearest_methods` over the MRO).
  2. Otherwise use the current field and receiver logic.

  A local shadows a field of the same name, as it does in each of the six
  languages. A MEMBER call `x.field.m()`, where `x` is a local, takes one more
  step through `field_types`. Nothing else changes.
- **Same safety rules.** An ambiguous type gives no edge. A type that is not in
  the repo is EXTERNAL only if the file imports it. Otherwise it is
  unresolved. No edge is invented.

## Acceptance

- **Per pack.** Tests show that a typed parameter resolves, an explicitly typed
  local resolves, and a local shadows a field. They also show that a generic
  parameter, an untyped or inferred local, and a destructured parameter stay
  unresolved.
- **Benchmark.** No probe may fire on any repo, and no repo's `found` may drop.
  Gains are accepted only when they are true facts already in the expected
  files. Expected files are never edited to match.
- **Gates.** The shared walker and resolver name no language. The full suite,
  ruff, pyright, `svarupa check . --base main` and the pack mutations all pass.
