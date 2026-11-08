# Parameter and Local Types Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A call on a value typed by a function parameter, or by an explicitly typed local, resolves through that type in Rust, Go, Java, C#, TypeScript and Python.

**Architecture:** There is one shared fact, `FileFacts.locals`, and one resolver step that looks up a local's type before fields, reusing `_field_call`'s path. Each pack fills `locals` through data and small hooks, plus a list of wrapper types to see through. Task 1 builds the mechanism together with the Rust pack, where the benchmark gap is. Tasks 2-6 each add one pack.

**Tech Stack:** Python 3.10+, the existing tree-sitter grammars, pytest.

**Spec:** `docs/superpowers/specs/2026-11-07-parameter-types-design.md`

## Global Constraints

- **Default behavior.** `FileFacts.locals` defaults to `()`. Every new pack option defaults to off. The walker and resolver never name a language.
- **Types and inference.** Only typed parameters and locals with an explicit type count. There is no inference. Generic parameters, unions of two real types, destructured patterns, `self`, `this` and Go receivers are not locals.
- **Shadowing.** A local shadows a field with the same name.
- **No invented edges.** An ambiguous type gives no edge. A type outside the repo is EXTERNAL only if the file imports it, otherwise it is unresolved.
- **Benchmark.** Run `uv run python scripts/benchmark.py run` after every task:
  - No probe may fire on any repo.
  - No repo's `found` may drop.
  - Gains are true facts already in the expected files.
  - Never edit `benchmark/expected/*`.
  - `accept` only when found rises and no probe fires.
  - If `test_pack_maturity_follows_the_benchmark` then demands a maturity change, make it, and update README.md and docs/health.md.
- **Gates.** Full `uv run pytest -q` passes, along with `uv run ruff check svarupa tests`, `uv run ruff format --check svarupa tests`, `uv run pyright` (0 errors) and `uv run svarupa check . --base main` (exit 0).
- **Golden facts.** These may gain `locals` entries. Regenerate them with `SVARUPA_UPDATE_GOLDEN=1` and read the diff. Only `locals` may change.
- **Writing and commits.** Plain English, no em dashes. Commits are SSH-signed (repo-local), imperative, under 72 characters, with no AI attribution.

## Review Focus

1. Rust `fn h(ctx: &Ctx) { ctx.user_id() }` with `impl Ctx { fn user_id(&self) }` in another file resolves to `Ctx.user_id`. Task 1.
2. A local named like a field shadows the field. In Java, `class A { Repo repo; void f(Other repo) { repo.x(); } }` resolves to `Other.x`, not `Repo.x`. Task 2.
3. `fn h<S: Store>(s: S) { s.save() }` and Python `def f(x: A | B)` stay unresolved. Tasks 1 and 6.
4. `param.field.m()` takes one step through field types. In Rust, `state.db.fetch()` with `state: AppState` and `AppState { db: Db }` resolves to `Db.fetch`. Task 1.
5. No benchmark probe fires in any task.

---

### Task 1: Shared mechanism and the Rust pack

**Files:**
- `svarupa/extract/base.py`: add `LocalType(function, name, type_name)` and `FileFacts.locals`.
- `svarupa/extract/packs/model.py` and `svarupa/extract/packs/walker.py`: a language-neutral way for a pack to emit locals, for example a `Local` rule kind or a hook on function nodes, plus `type_wrappers` data. Mirror how `Field` rules and `FieldType` work.
- `svarupa/extract/resolve.py`: index locals by `(path, function, name)`. In the QUALIFIED branch, check locals before fields. In the MEMBER branch, resolve `local.field.m()` through `field_types`.
- `svarupa/extract/packs/rust.py`:
  - Locals come from `parameters` > `parameter` (pattern `identifier`, `type`) and `let_declaration` with a `type` field.
  - Wrappers: `reference_type`, `Arc`, `Rc`, `Box`, `web::Data`, `Data`, `State`, `Extension`, `Json`.
  - Skip `self_parameter`, a parameter whose type names a generic parameter of the function or its impl, and patterns that are not a plain identifier.
- Tests: `tests/test_extract_rust.py`, `tests/test_build.py` (or wherever the resolution tests live), and the regenerated goldens.

- [ ] Tests first:
  - Review Focus 1, 3 and 4.
  - `let x: Repo = make();` resolves `x.save()`.
  - `let x = Repo::new();` does NOT (no inference).
  - `fn f((a, b): (A, B))` gives no local.
  - `Arc<AppState>`, `&mut Ctx` and `web::Data<Pool>` unwrap.
  - An external type that is imported (`ctx: &reqwest::Client`) gives EXTERNAL. One that is not imported is unresolved.
- [ ] Implement. Run the benchmark (rust-web-app is 77/80 and zero-to-production is 46/60 now). Commit "Resolve calls through typed parameters and locals".

### Task 2: Java

**Files:** `svarupa/extract/packs/java.py`, `tests/test_extract_java.py`.
- Locals come from `formal_parameter` (`type`, `name`), `spread_parameter`, and `local_variable_declaration` (`type`, then `variable_declarator` `name`) when the type is not `var`.
- Generic type parameters of the method or class are skipped.
- No wrappers. `Optional<T>` stays as written.

- [ ] Tests first:
  - Review Focus 2.
  - A typed parameter resolves.
  - `Repo r = ...;` resolves `r.x()`.
  - `var r = new Repo();` does not.
  - `<T extends Repo> void f(T t)` stays unresolved.
- [ ] Commit "Read Java parameter and local types".

### Task 3: C#

**Files:** `svarupa/extract/packs/csharp.py`, `tests/test_extract_csharp.py`.
- Locals come from `parameter` (`type`, `name`) and from `local_declaration_statement` > `variable_declaration` with an explicit `type`. `var` is not an explicit type.
- `this` extension parameters count as normal parameters.
- Generic type parameters are skipped.
- No wrappers except `T?` (nullable).

- [ ] Tests first, the same shape as Task 2: a typed parameter, an explicit local, `var` not counted, a generic parameter skipped, a local shadowing a field, and `IRepo repo` resolving to the interface method. Commit "Read C# parameter and local types".

### Task 4: TypeScript

**Files:** `svarupa/extract/packs/typescript.py`, `tests/test_extract_typescript.py`.
- Locals come from `required_parameter` and `optional_parameter` with a `type_annotation`, and from `variable_declarator` with a `type_annotation`.
- Constructor parameter properties (`constructor(private repo: Repo)`) are fields, not locals. Check how they are handled today and keep that.
- Wrappers: `T | undefined`, `T | null` and `Readonly<T>`. A union of two real types gives no local.
- Generic type parameters are skipped.
- JavaScript (the same pack family with no types) must stay unchanged.

- [ ] Tests first: a typed parameter, an explicit local, an untyped local not counted, a union skipped, a nullable unwrapped, a generic skipped, and JavaScript unchanged. Commit "Read TypeScript parameter and local types".

### Task 5: Go

**Files:** `svarupa/extract/packs/go.py`, `tests/test_extract_go.py` (or the Go test file that exists).
- Locals come from `parameter_declaration` (several names can share one type) and from `var_declaration` > `var_spec` with a `type`.
- `x := ...` is inference, so it gives no local.
- The method receiver is not a local; it already works as SELF.
- Wrapper: `pointer_type`.
- Interface types resolve to the interface method, as today through the MRO.
- Generic type parameters (`[T any]`) are skipped.

- [ ] Tests first: a typed parameter, shared-type parameters (`a, b *Repo`), `var r Repo` resolving, `r := NewRepo()` not resolving, a pointer unwrapped, and a generic skipped. Commit "Read Go parameter and local types".

### Task 6: Python

**Files:** `svarupa/extract/packs/python.py`, `tests/test_extract.py`.
- Locals come from `typed_parameter` and `typed_default_parameter` (name and `type`), and from an annotated assignment `x: T = ...`.
- `self` and `cls` are skipped.
- Wrappers: `Optional[T]`, `T | None`, `Annotated[T, ...]`, and string annotations `"T"` (unquote them).
- `A | B`, `Union[A, B]` and `TypeVar`-named types give no local.
- FastAPI `Depends(...)` defaults are fine, because the annotation is the type.

- [ ] Tests first: a typed parameter, `Optional[Repo]` unwrapped, `"Repo"` unquoted, a union skipped, an annotated assignment, an untyped parameter not counted, and `self` skipped. Commit "Read Python parameter and local types".

### Task 7: Docs, mutations, self-gate

**Files:**
- `scripts/mutate_packs.py`: add mutations for "locals are ignored by the resolver", "a local does not shadow a field", "Rust `&T` is not unwrapped", and "a generic parameter counts as a type".
- `docs/health.md`: remove "a call on a value typed only by a function parameter is not resolved" from the gap lists, and state what still is not resolved (inferred locals, generic parameters, chains longer than one field).
- `README.md`: update the benchmark numbers and maturity.

- [ ] All mutations are caught and every gate passes. Commit "Document typed-parameter resolution and cover it with mutations".
