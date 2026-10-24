# Svarupa: CI Health Gate (phase 2)

Date: 2026-10-24
Status: design approved, spec written
Parent: `2026-10-03-system-health-umbrella-design.md` (phase 2); builds on
1d (`svarupa check`)

## Goal

Every pull request is checked for the health problems it adds, and only
those. Old debt never blocks anyone; a new violation or a lower grade fails
the job, and the reviewer sees each new violation on the changed line with
its fix.

## Decisions (maintainer, 2026-10-24)

| Question | Decision |
|---|---|
| Gate mode | Fail by default (exit 3 on a new violation or a grade drop); report-only is a documented one-line change |
| Output | GitHub: job summary plus inline `::error` annotations. GitLab: job log plus a Code Quality report artifact. No PR comment, no extra token permissions |
| Dogfooding | Svarupa's own CI gets a `health` job on pull requests |

## 1. `svarupa check --format`

`--format text|json|github|gitlab` (default `text`; `--json` stays as an
alias for `--format json`). Exit codes are unchanged.

- `github`: one line per new violation,
  `::error file=<path>,line=<line>,title=<check title>::<message> Fix: <fix>`,
  then one `::warning title=Health grade::<area> <base> -> <head>` per worse
  grade, then the text report. Values are escaped per the GitHub workflow
  command rules (`%` to `%25`, CR to `%0D`, LF to `%0A`, and in properties
  `:` to `%3A`, `,` to `%2C`).
- `gitlab`: a Code Quality JSON array for the new violations:
  `{"description", "check_name", "fingerprint", "severity",
  "location": {"path", "lines": {"begin"}}}`. `fingerprint` is the
  SHA-256 hex of the finding's match key, so it is stable when lines move.
  Severity: minor, major, critical and blocker map to themselves; info maps
  to info.
- Paths are relative to the analyzed root; CI analyzes the repository root.

## 2. The installed CI templates

`svarupa setup ci_github`: the workflow gains a step "health of this
change" after the architecture delta:

- `svarupa check . --base "$BASE_SHA" --format github`, its output in the
  log, the text report under `## Health of this change` in the job summary.
- The job fails when the health check exits 3 (new violation or grade
  drop) or 1 (the check could not run). The architecture delta stays
  informational as today; the two exit codes are combined so neither hides
  the other.
- A comment names the one-line change that makes the gate report-only.

`svarupa setup ci_gitlab`: a second job `health_check` (merge request
events, `GIT_DEPTH: 0`) runs `svarupa check . --base
"$CI_MERGE_REQUEST_DIFF_BASE_SHA" --format gitlab > gl-code-quality-report.json`
and declares it as `artifacts: reports: codequality` with `when: always`,
then prints the text report and fails on exit 3 or 1.

## 3. Dogfooding

`.github/workflows/ci.yml` gains a `health` job on pull requests: checkout
with full history, `uv sync`, `uv run svarupa check . --base
"${{ github.event.pull_request.base.sha }}" --format github`, and the text
report in the job summary. Making it a required check is the maintainer's
later decision.

## 4. Docs

`docs/health.md` "Checking a change" gains an "In CI" paragraph; the setup
targets' `next_steps` say the gate fails only on new problems.

## Testing

- `--format github`: escaping, one annotation per new violation, a warning
  per worse grade, nothing for fixed findings.
- `--format gitlab`: valid JSON, the fields above, a fingerprint that does
  not change when a finding's line moves.
- `--json` still works and equals `--format json`.
- The templates contain the gate step or job, parse as YAML, and name only
  real flags (the existing drift test).
- The combined exit code in the GitHub step: delta 0 and health 3 fails;
  delta 3 (with `--fail-on-change`) and health 0 fails; both 0 passes. Tested
  by running the step's shell script with stub commands.

## Out of scope

Pull request comments; repository branch protection; IDE setup (phase 3).
