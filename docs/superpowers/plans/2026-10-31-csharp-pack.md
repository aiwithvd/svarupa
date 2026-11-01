# C# Language Pack Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Svarupa analyzes C# the way it analyzes Java: symbols, imports, calls, namespace resolution, function metrics, security and runtime risk facts, plain explanations with C# examples, and benchmark facts.

**Architecture:** A new data pack `svarupa/extract/packs/csharp.py`, built by mirroring `svarupa/extract/packs/java.py` (the closest language: classes, namespaces, attributes, method invocations) with C# node types; a namespace resolver next to `JvmPackages` in `svarupa/extract/packs/modules.py`; RiskSpec data; `csharp` examples in the explanation TOML files; a benchmark corpus entry. The walker and the health engine are language-neutral and do not change.

**Tech Stack:** Python 3.10+, `tree-sitter-c-sharp==0.23.5` (pinned, as the language-packs spec says), pytest.

**Spec:** `docs/superpowers/specs/2026-10-03-language-packs-design.md` (C# row: `tree-sitter-c-sharp` 0.23.5; import kind `namespace`: namespace declarations mapped to files; "Follow-up PRs, one per language"). Health behavior per `docs/superpowers/specs/2026-10-10-security-scaling-1c3-design.md` and `2026-10-24-plain-explanations-1c4-design.md`.

## Global Constraints

- The pack is data plus the few hooks data cannot express; the walker never names a language (model.py contract).
- New pack maturity is `experimental` until the benchmark proves it (`test_pack_maturity_follows_the_benchmark`).
- Evidence for every fact is the node's own line; facts come out in document order.
- `using` a namespace imports every file that declares that namespace; `System.*` and `Microsoft.*` are external.
- No literal from source reaches a risk fact's detail.
- Every explanation check the C# pack can produce has a `csharp` before/after example; `LANGS` gains `csharp`.
- This branch must pass its own gate: `uv run svarupa check . --base main` exits 0 before the last commit.
- Plain English, no em dashes. Commits SSH-signed (repo-local), imperative, under 72 characters, no AI attribution.

## Node types (probed from tree-sitter-c-sharp 0.23.5)

- `compilation_unit`; `using_directive` (child `qualified_name` or `identifier`; `using static`/aliases also exist); `file_scoped_namespace_declaration` and `namespace_declaration` (field `name`, `body: declaration_list`).
- `class_declaration`, `interface_declaration`, `struct_declaration`, `record_declaration`, `enum_declaration` (field `name`, `body: declaration_list`, child `base_list`, child `attribute_list` > `attribute` with field `name`, `modifier` children).
- `method_declaration` (fields `name`, `parameters: parameter_list`, `body: block` or arrow `arrow_expression_clause`), `constructor_declaration`, `property_declaration`, `local_function_statement`, `lambda_expression`.
- `invocation_expression` (fields `function`, `arguments: argument_list`; `function` is `identifier` or `member_access_expression` with fields `expression`, `name`), `object_creation_expression` (fields `type`, `arguments`).
- Statements: `if_statement`, `for_statement`, `foreach_statement` (fields `left`, `right`, `body`), `while_statement`, `do_statement`, `switch_statement`/`switch_section`, `try_statement`, `catch_clause` (field `body: block`), `conditional_expression`, `binary_expression` with `&&`/`||`/`??` operator tokens.
- Strings: `string_literal`, `verbatim_string_literal`, `raw_string_literal`, `interpolated_string_expression` (children `interpolation`).
- `async` is a token inside a `modifier` child of the method (not a direct child): the async marker hook must look one level down.

## Review Focus

1. A file-scoped namespace (`namespace Shop.Api;`) and a block namespace (`namespace Shop.Api { ... }`) qualify symbols the same way. Pinned in Task 1.
2. `using Shop.Data;` with two files declaring `Shop.Data` imports both; `using System.Text;` is external, never a dangling edge. Pinned in Task 2.
3. `async Task M()` is async although `async` sits under `modifier`. Pinned in Task 4.
4. A parameterized query (`FromSqlInterpolated($"... {id}")`) is not SQL injection; `FromSqlRaw("..." + id)` is. Pinned in Task 4.
5. Every explanation that C# can trigger has a C# example that is valid C#-looking code with no em dash. Pinned in Task 5.

---

### Task 1: The pack: symbols, imports, calls, attributes

**Files:**
- Modify: `pyproject.toml` (dependency `tree-sitter-c-sharp==0.23.5`), `uv.lock` (`uv lock`)
- Modify: `svarupa/detect.py` (`LANG_BY_EXT[".cs"] = "csharp"`)
- Create: `svarupa/extract/packs/csharp.py`
- Modify: `svarupa/extract/packs/__init__.py` (`PACKS` adds `csharp.PACK`)
- Create: `tests/test_extract_csharp.py`, `tests/golden/csharp/controller.cs` + `.json` + `.txt` (follow how `tests/golden/java/` fixtures and `tests/test_golden_facts.py` are laid out and regenerated)
- Modify: `tests/test_golden_facts.py`, `tests/test_packs.py`, `tests/test_extract.py` (add csharp wherever java is listed)

**Interfaces:**
- Produces: `csharp.PACK` with `lang="csharp"`, `maturity=Maturity.EXPERIMENTAL`, rules for `using_directive` (Import), both namespace node types (Custom: record the namespace like `package_declaration` in java.py, and for a block namespace walk its body with that namespace), the five type declarations (Define kind class or interface, `sets_class=True`, bases from `base_list`, decorators from `attribute_list` via a `decorators_from` hook like java.py `annotations_of`), `method_declaration`/`constructor_declaration`/`local_function_statement` (Define kind method in a class, function otherwise), `invocation_expression` and `object_creation_expression` (Call).
- `qualified_prefix`: the file's namespace when declared, else the path-based prefix (mirror java.py: the namespace replaces the directory prefix).

- [ ] **Step 1: Write the failing tests** (create `tests/test_extract_csharp.py`; mirror the structure of `tests/test_extract_java.py`). Required cases, each its own test:
  - a file-scoped and a block namespace produce the same qualified names for `class OrdersController` (`Shop.Api.OrdersController`) and its method `Create`;
  - `using Shop.Data;` produces one ImportRef with specifier `Shop.Data`; `using static Shop.Util.Math;` and `using Db = Shop.Data.Db;` are handled (specifier `Shop.Util.Math` and `Shop.Data.Db`);
  - class, interface, record, struct and enum are symbols; `base_list` names become bases (`ControllerBase`, `IThing`);
  - `[HttpPost]` and `[Authorize]` on a method and `[ApiController]` on a class become DecoratorRefs with those names;
  - `_repo.FindAsync(id)` and `new Helper()` are call sites with evidence on their own line, in document order;
  - a file with a syntax error yields SVA-X-001 and still extracts the parseable part.
- [ ] **Step 2:** `uv run pytest tests/test_extract_csharp.py -q` fails at import (no pack).
- [ ] **Step 3:** Implement `csharp.py` mirroring java.py with the node types above; add the dependency, the extension, and the PACKS entry. Generate the golden fixture outputs with the same command the Java goldens use (read `tests/test_golden_facts.py` docstring) and read them: every fact must be true of the fixture.
- [ ] **Step 4:** `uv run pytest tests/test_extract_csharp.py tests/test_golden_facts.py tests/test_packs.py tests/test_extract.py -q` passes; full suite green; ruff, pyright 0 errors.
- [ ] **Step 5:** Commit "Add the C# language pack".

### Task 2: Namespace resolution

**Files:**
- Modify: `svarupa/extract/packs/modules.py` (new `CSharpNamespaces`, mirroring `JvmPackages`), `svarupa/extract/packs/csharp.py` (`modules=CSharpNamespaces`)
- Create: `tests/golden_repos/csharp_shop.json` and its fixture repo, mirroring `java_shop`
- Modify: `tests/test_module_resolvers.py`, `tests/test_golden_repos.py`

**Interfaces:**
- Produces: `CSharpNamespaces(context)` with `targets(spec, from_file)` returning every `.cs` file whose declared namespace equals `spec` (or, for a `using` alias or static using of a type `A.B.C`, the files of namespace `A.B` that declare type `C`, else all of `A.B`), and `is_external(spec)` true for `System`, `System.*`, `Microsoft`, `Microsoft.*`.

- [ ] Tests first: a two-file namespace resolves to both files; a nested namespace `Shop.Data.Sql` is not part of `Shop.Data`; `System.Text` is external; a namespace nobody declares is unresolved (counted in the scorecard), never invented. Then the golden repo (an `Api` project using `Domain` and `Data` namespaces) produces module dependencies api -> domain -> data. Commit "Resolve C# using directives through namespaces".

### Task 3: Function metrics

**Files:** `svarupa/extract/packs/csharp.py` (`METRICS`), `tests/test_metrics.py` (add C# cases next to the Java ones)

- [ ] Tests first: complexity counts `if`, `else if` (no extra nesting), loops, `case` labels (not `default`), `catch`, `&&`, `||`, `??`, `?:`; nesting counts if/for/foreach/while/do/switch/try; parameters exclude nothing (C# has no `self`); lambdas and local functions are measured on their own. Implement `MetricsSpec` (function types: method, constructor, local function, lambda, property accessors with bodies). Commit "Measure C# functions".

### Task 4: Security and runtime risks

**Files:** `svarupa/extract/packs/csharp.py` (`risk_callee`, `RISKS`), `tests/test_risks.py` (C# true cases and safe twins, mirroring the Java ones)

Rules (data; mirror java.py's RISKS shape):
- **Sinks:** sql: `(?:^|\.)(?:ExecuteSqlRaw|ExecuteSqlRawAsync|FromSqlRaw|SqlQueryRaw)$`, `^new (?:SqlCommand|NpgsqlCommand|MySqlCommand|SqliteCommand)$`, `(?:^|\.)(?:Query|QueryAsync|Execute|ExecuteAsync|QueryFirst\w*|QuerySingle\w*)$` on a receiver named like a connection (`conn`, `connection`, `db`, `\w*Connection`); shell: `(?:^|\.)Process\.Start$` and `^new ProcessStartInfo$` when an argument is built; code: `(?:^|\.)CSharpScript\.(?:EvaluateAsync|RunAsync)$`.
- **String building:** `interpolated_string_expression` with interpolations, `+` on strings, `string.Format`/`String.Format`.
- **Unsafe:** tls-verify-off `ServerCertificateCustomValidationCallback\s*=.*=>\s*true` and `DangerousAcceptAnyServerCertificateValidator`; unsafe-deserialize `new BinaryFormatter`, `TypeNameHandling\s*=\s*TypeNameHandling\.(?:All|Auto|Objects)`; weak-hash `(?:MD5|SHA1)\.Create\(`, `new (?:MD5CryptoServiceProvider|SHA1Managed|SHA1CryptoServiceProvider)`; cors-any-origin `\.AllowAnyOrigin\(\)`; debug-enabled `\.UseDeveloperExceptionPage\(\)` outside an `IsDevelopment()` check is out of scope (always flag only `app.UseDeveloperExceptionPage()` at top level is too noisy: leave debug-enabled without a C# rule).
- **Runtime:** loops `for_statement`, `foreach_statement`, `while_statement`, `do_statement` (body field `body`); loop callee `\.(?:ForEach|Select|SelectMany)$`; datastore calls on EF/Dapper receivers (`_db`, `db`, `\w*Context`, `\w*Repository`, `\w*Repo`, `conn`, `connection`) with `Find\w*|First\w*|Single\w*|Where|ToList\w*|Any\w*|Count\w*|Add\w*|Update|Remove|SaveChanges\w*|Query\w*|Execute\w*`; async marker found under `modifier` (add a hook or extend the walker's async check through a pack field, ruled in the ledger); blocking calls in async `Thread\.Sleep$`, `\.Wait$`, `\.Result` (member access, not a call: model it as an Unsafe-style rule on `member_access_expression` named `blocking-in-async`? No: keep `blocking` to calls `Thread.Sleep`, `.Wait`, `.GetAwaiter().GetResult`), `File\.(?:ReadAll\w+|WriteAll\w+)$`; catch types `catch_clause` (body field `body`), quiet `comment`.
- **No-timeout and no-retry:** `HttpClient` has a 100-second default timeout, so no rule; no no-retry rule.
- **Auth markers:** `Authorize`, `AllowAnonymous` is not auth (routes for ASP.NET are not extracted yet, so endpoint-without-auth cannot fire; keep `auth_markers` for later).

- [ ] Tests first (true cases and safe twins as in test_risks.py, plus the Review Focus items 3 and 4); implement; full suite. Commit "Add C# sinks, unsafe settings and runtime risks".

### Task 5: Explanations with C# examples

**Files:** `svarupa/health/explain.py` (`LANGS` adds `"csharp"`), every `svarupa/health/explanations/*.toml` that `required_languages` now requires for csharp (add `[example.csharp]`), `tests/test_explain.py`

- [ ] `uv run pytest tests/test_explain.py -q -k every_language` lists the missing `csharp` examples (RED). Write each example: short, idiomatic C#, a real fix, no em dash; methods with return types have real bodies. The before example of a risk check must fire that check (verify with a scratch file through `svarupa check` or the extractor). Commit "Add C# examples to the check explanations".

### Task 6: Benchmark

**Files:** `benchmark/corpus.toml` (two pinned C# repositories, for example `jasontaylordev/CleanArchitecture` and `dotnet-architecture/eShopOnWeb`, each pinned to one commit), `benchmark/expected/<name>.toml`, `benchmark/scores.json`

- [ ] Read each repository's source first and write at least 15 `must` facts (modules, module dependencies between projects/namespaces, resolved calls) and 4 `must_not` probes, per `benchmark/README.md`; then `uv run python scripts/benchmark.py validate`, `run`, and `accept`. Misses are findings to fix in the pack, never facts to edit. Set pack maturity only as `test_pack_maturity_follows_the_benchmark` demands. Commit "Benchmark the C# pack".

### Task 7: Mutations, docs, self-gate

**Files:** `scripts/mutate_packs.py` (two mutations: a namespace no longer qualifies symbols; `System.*` stops being external), `README.md` and `docs/` language lists, `svarupa/setup/skill.py` + `skills/svarupa/SKILL.md` if they list languages

- [ ] Mutations caught; `uv run svarupa check . --base main` exits 0; full suite, ruff, pyright. Commit "Document the C# pack and cover it with mutations".
