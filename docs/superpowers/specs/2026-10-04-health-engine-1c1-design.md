# Svarupa: Health Engine and Core Checks (step 1c-1)

Date: 2026-10-04
Status: design approved, spec written, plan not yet written
Parent: `2026-10-03-system-health-umbrella-design.md`

## Decisions (maintainer, 2026-10-04)

| Question | Decision |
|---|---|
| 1c order | 1c-1 engine and core checks, 1c-2 design styles, 1c-3 security and scaling, 1c-4 explanations |
| Thresholds | Published industry defaults, fixed, each with its source |
| Grading | SQALE model (as used by SonarQube): maintainability by technical debt ratio (A <= 5%, B <= 10%, C <= 20%, D <= 50%, E > 50%, development cost 30 minutes per line of code); security and reliability by worst severity |
| Surfaces | REPORT.md section, Health tab in index.html, `health` in graph.json, MCP and query `get_health`, CLI summary |
| Duplication | In 1c-1 |
| Benchmark | Expected violations added in 1c-1, drafted from source like the facts |

## Components

- `svarupa/health/catalog.py`: checks as data (id, title, source, ISO area, severity, threshold, remediation minutes, maturity).
- `svarupa/health/metrics.py` plus pack data: per-function cyclomatic complexity, length, parameters and nesting, computed by the walker in pass 1 and stored as `FileFacts.functions`.
- `svarupa/health/duplication.py`: blocks of 10 or more normalized lines repeated across architecture files.
- `svarupa/health/graph_checks.py`: module import cycles, hub modules, large classes.
- `svarupa/health/score.py`: debt ratio, A to E ratings, impact ranking (fan-in plus being on a request path).

## Check catalog

| Id | Threshold | Source | Area | Severity | Minutes |
|---|---|---|---|---|---|
| complex-function | cyclomatic complexity > 10 | McCabe; NIST SP 500-235 | maintainability | major | 10 + 1 per point over |
| long-function | > 50 lines | Fowler, Long Method | maintainability | minor | 10 |
| many-parameters | > 5 | pylint max-args default | maintainability | minor | 5 |
| deep-nesting | > 4 levels | ESLint max-depth default | maintainability | minor | 10 |
| large-file | > 1000 lines | pylint max-module-lines default | maintainability | minor | 30 |
| large-class | method complexity sum > 47 | Lanza and Marinescu (WMC) | maintainability | major | 60 |
| duplicated-block | >= 10 lines repeated | SonarQube CPD default | maintainability | major | 15 per block |
| module-cycle | any cycle between modules | Martin, Acyclic Dependencies Principle | maintainability | major | 60 per cycle |
| hub-module | fan-in >= 10 and fan-out >= 10 | Arcan hub-like dependency | maintainability | major | 60 |

Remediation minutes are this project's published defaults, not copied from
any vendor. Areas without checks yet (reliability, security, performance)
show "not assessed yet", never a grade.

## Rules

- Every violation cites `file:line`; nothing without evidence.
- Deterministic: sorted output, no timestamps.
- Every check starts experimental; it becomes stable when the benchmark has
  expected violations for it on two repos with 90% recall and no probe.
- Health does not change the CLI exit code in 1c-1 (the CI gate is phase 2).

## Out of scope

Design styles and `design.yaml` (1c-2), security and scaling checks (1c-3),
explanations (1c-4), linter import, team-adjustable thresholds.
