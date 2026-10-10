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
  it shows "not assessed yet", never a grade. Security is assessed;
  reliability and performance come next.

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
  f-string or template literal, `%`, `.format`, `fmt.Sprintf` or
  `String.format`, inline or through a variable assigned that way earlier
  in the same function. Reassigning the variable to a literal clears it.
  A parameterized query (`execute("... %s", (x,))`) is never flagged. There
  is no taint tracking across functions.
- **Secrets.** Every non-ignored file is scanned, config included (`.env`,
  YAML, properties), except generated and vendored files, lockfiles and
  `.svarupa/`. The value is never stored or shown: a violation carries the
  rule, `file:line` and the first 12 hex characters of the value's SHA-256.
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
