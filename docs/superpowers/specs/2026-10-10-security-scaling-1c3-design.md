# Svarupa: Security, Performance and Reliability Checks (step 1c-3)

Date: 2026-10-10
Status: design approved, spec written
Parent: `2026-10-03-system-health-umbrella-design.md`; builds on 1c-1
(health engine) and 1c-2 (design styles)

## Goal

Fill the three health areas that show "not assessed yet" today (security,
performance, reliability) with checks Svarupa can prove from code, each
cited at `file:line` and tied to a published standard. Engineers see the
security and scaling risks before they ship; AI agents read the same
findings through `get_health`.

## Decisions (maintainer, 2026-10-10)

| Question | Decision |
|---|---|
| Security checks | Committed secrets, injection sinks, unsafe settings, endpoint without auth |
| Performance checks | Shared datastore, query in a loop (N+1), blocking call in async, god module on the request path |
| Reliability | Yes, a small set: no timeout, swallowed error, no retry |
| Where rules live | Data in each language pack (`RiskSpec`), like the 1a packs |
| Delivery | One spec, two PRs: 1c-3a security, 1c-3b performance and reliability |
| Secret output | Never the value: kind, `file:line` and a fingerprint hash only |
| Injection depth | Same function, no taint tracking |
| Auth signal | Pack data plus team markers in `design.yaml`; write routes only |
| Deep sync chains | Deferred until Svarupa resolves HTTP client calls to endpoints; ship `hub-on-request-path` now |

## How it works

Facts are collected in the existing tree-sitter pass and judged in the
health engine, the same way function metrics already flow
(`FileFacts.functions` to `Graph.functions` to `health.checks`).

**`RiskSpec` (per pack, data only):**

- `sinks`: call names tagged `sql`, `shell` or `code` (for example Python
  `cursor.execute`, `subprocess.run` with `shell=True`, `eval`; Go
  `db.Query`, `exec.Command("sh", "-c", ...)`; Java
  `Statement.executeQuery`, `Runtime.exec`; JS/TS `query`, `child_process.exec`,
  `eval`, `new Function`).
- `unsafe`: calls or keyword arguments tagged with a check id
  (`verify=False`, `InsecureSkipVerify: true`, `rejectUnauthorized: false`,
  `pickle.loads`, `yaml.load` without a safe loader, `ObjectInputStream`,
  `hashlib.md5`, `MessageDigest.getInstance("MD5")`, `DEBUG = True`,
  `app.run(debug=True)`, CORS origin `"*"`).
- `blocking`: sync I/O calls (`requests.*`, `time.sleep`, `urllib`, sync DB
  drivers; `fs.*Sync` in JS/TS).
- `datastore_calls`: query-shaped calls on a datastore client (`execute`,
  `query`, `find`, `get`, ORM `.objects.`, `Query`, `QueryRow`).
- `http_clients`: outbound HTTP calls and the argument or option that sets a
  timeout.
- `auth_markers`: decorators, dependencies, guards and middleware names
  (`login_required`, `Depends(get_current_user)`, `permission_classes`,
  `passport.authenticate`, `@UseGuards`, `@PreAuthorize`, `@Secured`,
  Go middleware wrapping a handler).
- `loop_types`, `async_markers`, `catch_types`: node types for loops, async
  functions and except/catch blocks.

**`RiskFact(kind, tag, evidence, function)`**, recorded by the walker:

- `sink-built`: a sink whose first argument is built by concatenation,
  an f-string, a template literal, `%` or `.format`/`String.format`/
  `fmt.Sprintf`, either inline or through a variable assigned that way
  earlier in the same function. A parameterized call (placeholders plus a
  separate argument list) is never recorded.
- `unsafe`: an unsafe call or setting.
- `in-loop`: a datastore call inside a loop body in the same function.
- `blocking-async`: a blocking call inside an async function.
- `no-timeout`: an outbound HTTP call with no timeout argument or option.
- `empty-catch`: an except/catch block whose body is empty or only `pass`
  or a comment.

Facts reach `Graph.risks`. Routes keep their decorators and the middleware
chain the extractor already composes; the auth check reads those.

**Secrets** are found by a text scan over every tracked file, config
included (`.env`, YAML, JSON, properties, source), not only parsed source:

- known key formats (AWS access key `AKIA...`, GitHub `ghp_`/`gho_`/
  `github_pat_`, Slack `xox[abpr]-`, Stripe `sk_live_`, Google `AIza`,
  private key blocks `-----BEGIN ... PRIVATE KEY-----`, JWT-shaped tokens),
  following the gitleaks default rule set;
- assignments whose name says secret (`password`, `passwd`, `secret`,
  `api_key`, `token`, `private_key`) to a literal of 8 or more characters.

Placeholders are skipped: empty values, `changeme`, `example`, `xxx`,
`<...>`, `${...}`, `{{...}}`, values read from the environment. The
outputs carry the rule name, `file:line` and a fingerprint (12 hex of a
scrypt hash of the value, salted with the file path, so it cannot be used to
test password guesses); the value itself is never stored, printed or written.

## The checks

All new checks start `experimental`. Security, performance and reliability
are rated by the worst severity, as today (blocker E, critical D, major C,
minor B, none A). Remediation minutes order the fix list; the SQALE debt
ratio stays maintainability only.

### 1c-3a: security

| Check | Fires on | Severity | Min | Source |
|---|---|---|---|---|
| `hardcoded-secret` | a secret match | blocker | 30 | CWE-798; gitleaks rules |
| `sql-injection` | `sink-built` tagged `sql` | critical | 30 | CWE-89; OWASP A03 |
| `command-injection` | `sink-built` tagged `shell` | critical | 30 | CWE-78; OWASP A03 |
| `code-injection` | `sink-built` tagged `code` | critical | 30 | CWE-94; OWASP A03 |
| `tls-verify-off` | `unsafe` tagged so | critical | 10 | CWE-295; OWASP A02 |
| `unsafe-deserialize` | `unsafe` tagged so | critical | 30 | CWE-502; OWASP A08 |
| `weak-hash` | `unsafe` tagged so | major | 10 | CWE-327; OWASP A02 |
| `debug-enabled` | `unsafe` tagged so | major | 5 | CWE-489; OWASP A05 |
| `cors-any-origin` | `unsafe` tagged so | major | 10 | CWE-942; OWASP A05 |
| `endpoint-without-auth` | a POST, PUT, PATCH or DELETE route with no auth marker on the handler, its router or its middleware chain | major | 20 | CWE-306; OWASP A01 |

`endpoint-without-auth` is not reported for a unit that has no auth
marker anywhere (Svarupa cannot tell "no auth" from "auth it does not
know"); the report says so instead.

### 1c-3b: performance and reliability

| Check | Area | Fires on | Severity | Min | Source |
|---|---|---|---|---|---|
| `shared-datastore` | performance | two or more app services use the same datastore node (compose links or connection settings) | major | 240 | Newman, Building Microservices: database per service |
| `query-in-loop` | performance | `in-loop` | major | 20 | N+1 query anti-pattern (Fowler, PoEAA) |
| `blocking-in-async` | performance | `blocking-async` | major | 15 | Python asyncio docs; Node.js "Don't block the event loop" |
| `hub-on-request-path` | performance | a `hub-module` reached from every route module (at least 3) | major | 60 | Arcan hub-like dependency on the request path |
| `no-timeout` | reliability | `no-timeout` | major | 10 | Nygard, Release It!: timeouts |
| `swallowed-error` | reliability | `empty-catch` | major | 10 | CWE-390 |
| `no-retry` | reliability | a datastore or queue client built with no retry option where the client library has one | minor | 15 | Nygard, Release It!: retries |

Deep synchronous chains between services wait for HTTP call resolution
(client call with a literal URL matched to another service's endpoint),
which is outside this step.

## Exceptions and team markers

`design.yaml` gains two optional keys:

```yaml
auth_markers: [require_session, AdminGuard]
exceptions:
  - rule: hardcoded-secret
    at: tests/fixtures/fake_key.pem
    reason: test fixture, not a real key
  - rule: endpoint-without-auth
    at: app/api/webhooks.py:42
    reason: signed webhook, verified in the handler
```

`at` is a path (the whole file) or `path:line`. As with design exceptions,
a reason is required, and an excepted violation is listed as accepted, not
counted. Existing `from`/`to` exceptions are unchanged.

## Outputs

No new surface: the checks appear wherever health already does (CLI
summary, REPORT.md, the Health tab, `graph.json` `health` key, query and
MCP `get_health`). Each violation message names the CWE or source. Secret
violations show the rule and fingerprint only.

## Benchmark and tests

- Per pack fixtures: one file per check with a true case and its safe twin
  (parameterized query, `shell=False`, `yaml.safe_load`, `verify=True`,
  request with a timeout, a route with an auth marker). Each true case is
  found, each twin is not.
- A secret test asserts the raw value appears in no output (graph.json,
  REPORT.md, HTML, CLI, MCP).
- Benchmark: expected `violation:<check> path:line` facts drafted from the
  pinned repos' source; a check becomes stable when it is expected and
  found on two repos, the 1c-1 rule.
- Mutation tests: dropping each sink, unsafe tag, loop or async marker is
  caught.

## Out of scope

Taint tracking across functions; dependency vulnerability scanning (CVE
lookups need a network and a database, phase 3 at the earliest);
service-to-service call chains; secrets in git history (only the working
tree is scanned); language packs beyond the five shipped.
