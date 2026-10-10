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
  it shows "not assessed yet", never a grade.

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

Test, generated, vendored and tooling files are not assessed.
