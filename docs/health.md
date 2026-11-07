# Health

Svarupa grades a codebase with the SQALE model, the method behind
SonarQube's ratings and aligned with ISO/IEC 25010.

## Ratings

- **Maintainability** uses the technical debt ratio: the minutes needed to
  fix every maintainability violation, divided by the minutes it took to
  write the code (30 minutes per line of code, SQALE's default).
  A: up to 5%. B: up to 10%. C: up to 20%. D: up to 50%. E: above 50%.
- **Reliability, security, performance** use the worst severity found
  (minor B, major C, critical D, blocker E). Until checks exist for an area
  it shows "not assessed yet", never a grade. All four areas are assessed.

## Explanations

Every check has a plain explanation: what it means, why it matters and how
to fix it, with a short before/after in Python, TypeScript/JavaScript, Go,
Java, C# or Rust where the fix is code. They live in `svarupa/health/explanations/` and
show in REPORT.md ("What these mean"), the Health tab ("why?"), graph.json
(`health.checks[].explanation`) and MCP `get_health`. From the command line:

    svarupa explain                      # list the checks
    svarupa explain query-in-loop        # one check, every example
    svarupa explain query-in-loop --lang go

Every box on the map also has one plain sentence in its passport, in
graph.json (`about`) and in `get_node`, for example "Module app/api: serves
12 HTTP routes (FastAPI), uses 3 modules and is used by 5, talks to
PostgreSQL. 2 health findings, worst: Query in a loop (major)."

## Checking a change

    svarupa check <repo> --base main          # a git ref, built in a temporary worktree
    svarupa check <repo> --base old/graph.json --json

Lists the violations the working tree adds against the base, the ones it
fixed, and every area whose grade moved. A finding is the same finding when
it is the same check in the same file on the same definition, so moving
code never makes it new, and a finding is matched across files only when
its old file is gone (a rename or move). Exit 3 when a new violation is at or above
`--min-severity` (default minor) or a grade got worse; exit 0 otherwise.
Both sides are judged by the working tree's `.svarupa/design.yaml`.
Annotations (`--format github` and `gitlab`) use repository-root paths, so
`svarupa check` works on a monorepo subfolder.

AI agents get every rule up front from `get_design_rules` (`checks`: id,
rule, area, severity, limit, fix), and the agent skill tells them to run
`svarupa check` before they finish.

In CI: `svarupa setup ci_github` adds a "health of this change" step to the
pull request workflow, and `svarupa setup ci_gitlab` adds a `health_check`
job. Both run `svarupa check` against the pull request's base and fail only
for problems the pull request adds. On GitHub each new violation is an
annotation on its line (`--format github`); on GitLab it appears in the
merge request through a Code Quality report (`--format gitlab`). A comment
in each file shows the one-line change that makes the gate report-only.

AI coding tools learn these rules from `svarupa setup ide`, which writes them into each tool's rules file.
`svarupa check` also warns (without failing) when a tool's rules file no
longer matches the design, and names the file: run `svarupa setup ide
--update` to refresh it.

## Checks

| Id | Violated when | Source | Severity | Minutes |
|---|---|---|---|---|
| complex-function | cyclomatic complexity > 10 | McCabe 1976; NIST SP 500-235 | major | 10 + 1 per point over |
| long-function | function longer than 50 lines | Fowler, Refactoring: Long Method | minor | 10 |
| many-parameters | more than 5 parameters (`self`/`cls` not counted) | pylint max-args default | minor | 5 |
| deep-nesting | blocks nested more than 4 deep (`else if` does not nest) | ESLint max-depth default | minor | 10 |
| large-file | file longer than 1000 lines | pylint max-module-lines default | minor | 30 |
| large-class | sum of method complexity > 47 | Lanza and Marinescu, WMC | major | 60 |
| duplicated-block | 10 or more lines repeated | SonarQube CPD default | major | 15 |
| module-cycle | modules import each other in a cycle | Martin, Acyclic Dependencies Principle | major | 60 |
| hub-module | 10 or more modules depend on it and it depends on 10 or more | Arcan hub-like dependency | major | 60 |

Thresholds are the published defaults of the named sources. Remediation
minutes are this project's estimates. Every check starts experimental and is
promoted when the benchmark proves it (see `benchmark/`).

Test, generated, vendored and tooling files are not assessed (secrets are
the exception, see below).

## Security checks

| Id | Fires on | Source | Severity | Minutes |
|---|---|---|---|---|
| hardcoded-secret | a known key format (AWS, GitHub, Slack, Stripe, Google, Anthropic, OpenAI, private key) or a secret-named setting (`password`, `secret`, `api_key`, `token`...) given a literal | CWE-798; gitleaks default rules | blocker | 30 |
| sql-injection | a query call whose argument is built from strings | CWE-89 | critical | 30 |
| command-injection | a shell call (`os.system`, `shell=True`, `exec`, `sh -c`) whose argument is built from strings | CWE-78 | critical | 30 |
| code-injection | `eval`, `exec`, `new Function` and similar on a built string | CWE-94 | critical | 30 |
| tls-verify-off | `verify=False`, `InsecureSkipVerify: true`, `rejectUnauthorized: false`, no-op hostname verifier | CWE-295 | critical | 10 |
| unsafe-deserialize | `pickle`, `yaml.load` without a safe loader, `ObjectInputStream`, `unserialize` | CWE-502 | critical | 30 |
| weak-hash | MD5 or SHA-1 (Python's `usedforsecurity=False` is respected) | CWE-327 | major | 10 |
| debug-enabled | `DEBUG = True`, `debug=True`, gin debug mode | CWE-489 | major | 5 |
| cors-any-origin | CORS that allows every origin (`*`, `cors()` with defaults, `@CrossOrigin`) | CWE-942 | major | 10 |
| endpoint-without-auth | a POST, PUT, PATCH or DELETE route with no auth marker | CWE-306 | major | 20 |

How each one decides:

- **Built strings.** A string is built when it uses concatenation, an
  f-string or template literal, `%`, `.format`, `fmt.Sprintf`,
  `String.format`, or in Rust `format!`, `push_str` onto a variable and
  `"a".to_string() + x`, inline or through a variable assigned that way earlier
  in the same function. Reassigning the variable to a literal clears it.
  A parameterized query (`execute("... %s", (x,))`) is never flagged. There
  is no taint tracking across functions.
- **Secrets.** Every non-ignored file is scanned, config included (`.env`,
  YAML, properties), except generated and vendored files, lockfiles and
  `.svarupa/`. The value is never stored or shown: a violation carries the
  rule, `file:line` and a fingerprint: 12 hex characters of a scrypt hash
  of the value salted with the file path. It changes when the key is
  rotated, and it cannot be used to test password guesses.
  Placeholders are skipped: empty or short values, `changeme`, `example`,
  `${VAR}`, `{{...}}`, `<...>`, environment reads, values that name
  themselves (`email-server-password`), digit-only values, password hashes
  and sample JSON Web Tokens.
- **Auth.** A marker is a known decorator, dependency, guard or middleware
  name (`login_required`, `Depends(get_current_user)`, `CurrentUser`,
  `passport.authenticate`, `@UseGuards`, `@PreAuthorize`...); teams add
  their own under `auth_markers` in `.svarupa/design.yaml`. A route is
  protected when a marker is on its declaration or handler, or in its
  file's top-level code (a router built with a dependency, a guard on the
  controller class). Imports never count. Public paths (login, signup,
  register, token, refresh, webhook, health, password reset) are skipped.
  A unit with no marker anywhere is not judged, and the report says so.
  Routes are known for FastAPI, Flask, Express and NestJS today.

## Performance checks

| Id | Fires on | Source | Severity | Minutes |
|---|---|---|---|---|
| shared-datastore | services built from two or more codebases use the same compose datastore (`depends_on`, `links`, or a connection setting that names it) | Newman, Building Microservices: database per service | major | 240 |
| query-in-loop | a database query inside a loop body, or inside a `forEach`/`map` callback (in Java, `map` only on a stream: `Optional.map` runs once; in C#, `ForEach`, `Select` and `SelectMany`) | N+1 query anti-pattern; Fowler, PoEAA | major | 20 |
| blocking-in-async | a blocking call (`requests`, `time.sleep`, `fs.readFileSync`, `bcrypt.hashSync`...) inside an async function | Python asyncio docs; Node.js "Don't block the event loop" | major | 15 |
| hub-on-request-path | a hub module (the `hub-module` limits) that every route module reaches, with at least 3 route modules | Arcan hub-like dependency | major | 60 |

How each one decides:

- **Loops.** Only the loop body counts: `for u in User.objects.all():` runs
  one query. A function passed to `forEach`, `map` or `flatMap` (in Rust
  `for_each`, `map` or `try_for_each`) runs once per item. A query is a call
  on a database client, ORM model or repository (`cursor.execute`,
  `User.objects.get`, `prisma.user.findMany`, `userRepository.findById`,
  `db.QueryRow`, `sqlx::query(..).fetch_one`, `users::table.load`).
- **Async.** A function marked `async` (Python, JavaScript, TypeScript, C#, Rust). A
  function defined inside it is judged on its own.
- **Shared datastore.** Only services built from this repository count, so
  an admin image like adminer is never an owner. Two services built from
  the same folder (an app and its migration job) are one owner.

## Reliability checks

| Id | Fires on | Source | Severity | Minutes |
|---|---|---|---|---|
| no-timeout | `requests` or `urlopen` without `timeout=`, axios without `timeout`, Go's default HTTP client, `http.Client{}` without `Timeout`, Java `HttpClient.newHttpClient()` and `new RestTemplate()` | CWE-1088; Nygard, Release It! | major | 10 |
| swallowed-error | an `except` or `catch` whose body is empty, `pass` or comments; Go `if err != nil {}` with nothing but comments inside | CWE-390; Bandit B110 | major | 10 |
| no-retry | a client built with retries off or a single attempt: pika without `connection_attempts`, `aio_pika.connect` (not `connect_robust`), Redis `MaxRetries: -1`, RabbitMQ automatic recovery off | Nygard, Release It! | minor | 15 |

`fetch` is not flagged: Node's fetch has 300-second timeouts by default and
a browser user can cancel a request. Clients that retry by default (most
Redis, MongoDB and Kafka clients) are not flagged either.

## Typed parameters and locals

Calls through a typed parameter and through an explicitly typed local now
resolve in Rust, Go, Java, C#, TypeScript and Python. A local is scoped to the
block that binds it, and the innermost binding wins. A name that is rebound
without a written type hides the older type, a field and an import of the same
name, so Svarupa leaves that call unresolved instead of guessing. A type that
the file imports from a dependency stays external, even when the repository
has a class with the same name.

Locals whose type is only inferred do not resolve. These also do not resolve:

- inferred locals: `var x = new T()`, `let x = T::new()`, `x := NewT()`;
- generic parameters;
- chains longer than one field, such as `a.b.c.m()`;
- flow-sensitive narrowing, such as Java
  `if (!(o instanceof T x)) return; x.f();`;
- a name that is bound on the same line where its scope starts or ends, which
  is left unresolved;
- Python `global` names.

## Language gaps

The Python, TypeScript, Go, Java, C# and Rust packs are stable: each passed
the benchmark. The JavaScript pack is experimental. A pack is stable only when
two of its benchmark repositories reach 90% recall with no false fact. The
benchmark corpus has 15 repositories. The two Rust repositories score 98%
(rust-web-app) and 93% (zero-to-production), and no probe fired. These gaps
are known and are not guessed around.

**Rust**

- A name rebound by any pattern (`if let`, `match`, `for`, a tuple `let`, a
  closure parameter) or by an untyped `let` hides the earlier type inside the
  scope of that new binding, so a call on it may be left unresolved. A local
  with no written type (`let x = Repo::new()`) is not resolved.
- A call through a module path with several segments, such as
  `web::routes::f()`, is not resolved.
- A `pub use` inside an inline `mod { }` block is not followed. A `pub use`
  at the top of a module file is.
- Each file under `tests/` is its own crate, so its `mod` and `crate::` paths
  do not resolve.
- Macros are not calls, and `macro_rules!` names are not symbols.
- `const`, `static` and type aliases are not symbols.
- Routes are not extracted, so `endpoint-without-auth` does not fire for
  Rust.
- Async closures and async blocks inside a sync `fn` are not checked for
  blocking calls.
- Some Diesel and SeaORM query styles inside loops are not caught.
- `Hmac::<Sha1>` with the turbofish form is not flagged.
- Calls on a generic parameter (`S: Store`) are not resolved. A call through a
  `Box<dyn Store>` field resolves to the trait method, not to an implementing
  type.
- Unit tests inside a source file (`#[cfg(test)]` modules and functions marked
  `#[test]`, `#[tokio::test]` or `#[rstest]`) are skipped by the metrics and
  risk checks, but the `duplicated-block` check reads text and still counts
  repeated lines inside them.

**C#**

- Calls to framework types such as `Console.WriteLine` stay unresolved, even
  with `using System;`.
- `global using` applies only to the file that writes it, not to the whole
  project.
- A partial class split across files is ambiguous: its calls and members
  are not merged.
- A bare call after `using static` is unresolved.
- ASP.NET routes are not extracted, so `endpoint-without-auth` does not fire
  for C#.
- `.Result` on a task is not flagged as a blocking call in async code.
- Building `ProcessStartInfo.Arguments` or `SqlCommand.CommandText` by
  assignment is not flagged.
- `query-in-loop` only knows common receiver names, such as `_db`,
  `_context`, `...DbContext`, `conn` and `...Repository`.

## Exceptions

Accept a violation in `.svarupa/design.yaml` with a reason; it is listed
as accepted and not counted:

```yaml
exceptions:
  - rule: weak-hash
    at: app/cache.py:12
    reason: cache key, not a password
  - rule: hardcoded-secret
    at: tests/fixtures/fake_key.pem
    reason: test fixture, not a real key
```

`at` is a file (any line) or `file:line`.
