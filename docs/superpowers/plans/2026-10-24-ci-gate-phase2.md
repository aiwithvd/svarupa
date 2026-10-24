# CI Health Gate (phase 2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every pull request runs `svarupa check` against its base; new violations show on the changed lines and fail the job, old debt never does.

**Architecture:** `svarupa check` gains `--format github|gitlab` (output helpers in `svarupa/check.py`, so the CLI function stays short). The installed GitHub workflow gets a separate health step that runs even when the delta step fails; the installed GitLab file gets a `health_check` job with a Code Quality artifact. Svarupa's own CI gets a `health` job.

**Tech Stack:** Python 3.10+, argparse, GitHub workflow commands, GitLab Code Quality JSON, bash, pytest, PyYAML.

**Spec:** `docs/superpowers/specs/2026-10-24-ci-gate-phase2-design.md`

## Global Constraints

- `--format text|json|github|gitlab`, default `text`; `--json` is an alias for `--format json`; exit codes unchanged (3 new violation or worse grade, 0 clean, 1 could not run, 2 usage).
- GitHub: `::error file=<path>,line=<line>,title=<check title>::<message> Fix: <fix>` per new violation; `::warning title=Health grade::<area> <base> -> <head>` per worse grade; then the text report. Escape data `%`->`%25`, CR->`%0D`, LF->`%0A`; in properties also `:`->`%3A`, `,`->`%2C`.
- GitLab: JSON array of `{"description", "check_name", "fingerprint", "severity", "location": {"path", "lines": {"begin"}}}`; fingerprint is SHA-256 hex of the match key (plus the occurrence number for repeated keys), stable when lines move; the text report goes to stderr so stdout is only the JSON.
- The installed gate fails by default; a commented one-line change makes it report-only. The architecture delta stays informational.
- Svarupa's own `health` job runs on pull requests only.
- This branch must pass its own gate: before the last commit, `uv run svarupa check . --base main` exits 0.
- Plain English, no em dashes. Commits SSH-signed (repo-local), imperative, under 72 characters, no AI attribution.

## Review Focus

1. A message with a comma, colon, `%` or newline cannot break an annotation. Pinned in Task 1 (`test_github_format_escapes_workflow_command_text`).
2. Two identical findings get two different GitLab fingerprints (GitLab merges equal fingerprints). Pinned in Task 1 (`test_gitlab_fingerprints_are_stable_and_unique`).
3. The health step still runs when the architecture delta step failed. Pinned in Task 2 (`test_the_health_step_runs_after_a_failed_delta`).
4. The job summary never contains raw `::error` lines. Pinned in Task 2 (`test_the_health_step_script_fails_and_summarizes`).
5. `--json` together with `--format github` is a usage error, not a silent pick. Pinned in Task 1 (`test_json_and_format_together_is_a_usage_error`).

---

### Task 1: `--format github|gitlab`

**Files:**
- Modify: `svarupa/check.py` (`Delta.render_github`, `Delta.to_gitlab`, `emit`)
- Modify: `svarupa/cli.py` (`_check`: `--format`, call `emit`)
- Test: `tests/test_check.py`

**Interfaces:**
- Produces: `Delta.render_github() -> str`; `Delta.to_gitlab() -> list[dict[str, object]]`; `emit(delta: Delta, fmt: str, failed: bool) -> None` (prints to stdout; for `gitlab` prints the text report to stderr).

- [ ] **Step 1: Write the failing tests** (append to `tests/test_check.py`)

```python
def test_github_format_annotates_each_new_violation() -> None:
    worse = (("maintainability", "A"), ("reliability", "A"), ("security", "C"), ("performance", "A"))
    d = compare(snap(f(check="deep-nesting")), snap(f(), ratings=worse))
    out = d.render_github().splitlines()
    assert out[0].startswith("::error file=a.py,line=1,title=Too many parameters::f has 6 parameters (limit 5) Fix: Group the values")
    assert out[1] == "::warning title=Health grade::security A -> C"
    assert not any("deep-nesting" in line and line.startswith("::") for line in out)
    assert "1 new, 1 fixed" in out


def test_github_format_escapes_workflow_command_text() -> None:
    d = compare(snap(), snap(f(file="a,b:c.py", message="50% done\nnext")))
    line = d.render_github().splitlines()[0]
    assert line.startswith("::error file=a%2Cb%3Ac.py,line=1,")
    assert "50%25 done%0Anext" in line


def test_gitlab_fingerprints_are_stable_and_unique() -> None:
    a = compare(snap(), snap(f(line=3), f(line=9))).to_gitlab()
    b = compare(snap(), snap(f(line=30), f(line=90))).to_gitlab()
    assert [x["fingerprint"] for x in a] == [x["fingerprint"] for x in b]
    assert a[0]["fingerprint"] != a[1]["fingerprint"]
    assert a[0]["severity"] == "minor" and a[0]["check_name"] == "many-parameters"
    assert a[0]["location"] == {"path": "a.py", "lines": {"begin": 3}}
    assert a[0]["description"].startswith("Too many parameters: f has 6 parameters")


def test_json_and_format_together_is_a_usage_error(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["check", str(tmp_path), "--base", "HEAD", "--json", "--format", "github"])
    assert exc.value.code == 2


def test_gitlab_format_keeps_stdout_pure_json(tmp_path: Path, capsys) -> None:
    write(tmp_path, "app/m.py", CLEAN)
    assert main([str(tmp_path)]) == 0
    base = tmp_path / "base.json"
    shutil.copy(tmp_path / ".svarupa" / "graph.json", base)
    write(tmp_path, "app/n.py", SIX)
    capsys.readouterr()
    assert main(["check", str(tmp_path), "--base", str(base), "--format", "gitlab"]) == 3
    cap = capsys.readouterr()
    report = json.loads(cap.out)
    assert [r["check_name"] for r in report] == ["many-parameters"]
    assert cap.err.startswith("1 new, 0 fixed")
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_check.py -q -k "github or gitlab or format"`
Expected: FAIL (`AttributeError: 'Delta' object has no attribute 'render_github'`; `--format` unknown, exit 2 for the wrong reason or not at all).

- [ ] **Step 3: Implement in `svarupa/check.py`**

Add `import hashlib`, `import json` and `import sys` to the imports and `"emit"` to `__all__`. Add these module functions above `class Delta`:

```python
def _data(text: str) -> str:
    """Escape a GitHub workflow command's message."""
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def _prop(text: str) -> str:
    """Escape a GitHub workflow command's property value."""
    return _data(text).replace(":", "%3A").replace(",", "%2C")


def _title(check: str) -> str:
    return BY_ID[check].title if check in BY_ID else check
```

Add these methods to `Delta` (after `render`):

```python
    def render_github(self) -> str:
        """Workflow commands that annotate each new violation on its line,
        a warning per worse grade, then the text report."""
        from svarupa.health.explain import explanation

        lines: list[str] = []
        for f in self.new:
            fix = f" Fix: {explanation(f.check).fix}" if f.check in BY_ID else ""
            lines.append(
                f"::error file={_prop(f.file)},line={f.line},title={_prop(_title(f.check))}"
                f"::{_data(f.message + fix)}"
            )
        grades = {area: (before, after) for area, before, after in self.grades}
        for area in self.worse():
            before, after = grades[area]
            lines.append(f"::warning title=Health grade::{_data(f'{area} {before} -> {after}')}")
        lines.append(self.render())
        return "\n".join(lines)

    def to_gitlab(self) -> list[dict[str, object]]:
        """A GitLab Code Quality report of the new violations. The
        fingerprint hashes the match key, so it survives moved lines; a
        repeated key gets its occurrence number so GitLab keeps both."""
        seen: Counter[tuple[str, str, str]] = Counter()
        out: list[dict[str, object]] = []
        for f in self.new:
            seen[f.key] += 1
            raw = "\x00".join((*f.key, str(seen[f.key])))
            out.append(
                {
                    "description": f"{_title(f.check)}: {f.message}",
                    "check_name": f.check,
                    "fingerprint": hashlib.sha256(raw.encode("utf8")).hexdigest(),
                    "severity": BY_ID[f.check].severity if f.check in BY_ID else "info",
                    "location": {"path": f.file, "lines": {"begin": f.line}},
                }
            )
        return out
```

Add this function after `compare`:

```python
def emit(delta: Delta, fmt: str, failed: bool) -> None:
    """Print a result in the requested format."""
    if fmt == "json":
        print(json.dumps(delta.to_json(failed), indent=2, sort_keys=True))
    elif fmt == "github":
        print(delta.render_github())
    elif fmt == "gitlab":
        print(json.dumps(delta.to_gitlab(), indent=2, sort_keys=True))
        print(delta.render(), file=sys.stderr)
    else:
        print(delta.render())
```

- [ ] **Step 4: The CLI** (`svarupa/cli.py`, `_check`)

Add `emit` to the `from svarupa.check import (...)` list. After the `--json` argument add:

```python
    parser.add_argument(
        "--format",
        choices=["text", "json", "github", "gitlab"],
        default=None,
        help="output: text (default), json, github (annotations) or gitlab (Code Quality JSON)",
    )
```

Right after `args = parser.parse_args(argv)` add:

```python
    if args.json and args.format not in (None, "json"):
        parser.error("--json and --format are two ways to say one thing; use --format")
    fmt = "json" if args.json else (args.format or "text")
```

Replace the output block

```python
    if args.json:
        print(json.dumps(delta.to_json(failed), indent=2, sort_keys=True))
    else:
        print(delta.render())
```

with `    emit(delta, fmt, failed)`.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_check.py tests/test_setup.py -q`
Expected: PASS (the flag drift test reads `check --help`, so `--format` is now a real flag).

- [ ] **Step 6: Full suite, lint, commit**

```bash
uv run pytest -q 2>&1 | tail -2
uv run ruff format svarupa tests && uv run ruff check svarupa tests && uv run pyright svarupa
git add svarupa/check.py svarupa/cli.py tests/test_check.py
git commit -m "Print check results as GitHub annotations or GitLab Code Quality"
```

---

### Task 2: The gate in the installed GitHub workflow

**Files:**
- Modify: `svarupa/setup/ci_github.py` (`_TEMPLATE`, `next_steps`)
- Modify: `scripts/mutate_setup.py` (two mutations)
- Test: `tests/test_setup.py`

**Interfaces:**
- Consumes: `svarupa check --format github` (Task 1).
- Produces: a workflow step named `health of this change` with `if: ${{ !cancelled() }}`.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_setup.py`)

```python
def _health_step() -> dict:
    steps = yaml.safe_load(WORKFLOW)["jobs"]["diff"]["steps"]
    return next(s for s in steps if s.get("name") == "health of this change")


def test_the_health_step_runs_after_a_failed_delta() -> None:
    step = _health_step()
    assert step["if"] == "${{ !cancelled() }}"
    assert step["shell"] == "bash"
    assert 'svarupa check . --base "$BASE_SHA" --format github' in step["run"]
    names = [s.get("name") for s in yaml.safe_load(WORKFLOW)["jobs"]["diff"]["steps"]]
    assert names.index("health of this change") > names.index("architecture delta")


def test_the_health_step_script_fails_and_summarizes(tmp_path: Path) -> None:
    import os
    import shutil
    import subprocess

    bash = shutil.which("bash")
    if not bash:
        pytest.skip("bash is not installed")
    stub = tmp_path / "bin" / "svarupa"
    stub.parent.mkdir()
    stub.write_text(
        "#!/bin/sh\necho '::error file=a.py,line=1,title=T::m'\necho '1 new, 0 fixed'\nexit 3\n",
        encoding="utf8",
    )
    stub.chmod(0o755)
    summary = tmp_path / "summary.md"
    env = {
        **os.environ,
        "PATH": f"{stub.parent}{os.pathsep}{os.environ['PATH']}",
        "GITHUB_STEP_SUMMARY": str(summary),
    }
    script = _health_step()["run"].replace("${{ github.event.pull_request.base.sha }}", "abc123")
    proc = subprocess.run([bash, "-e", "-o", "pipefail", "-c", script], env=env, capture_output=True, text=True)
    assert proc.returncode == 3
    assert "::error file=a.py" in proc.stdout
    text = summary.read_text(encoding="utf8")
    assert "## Health of this change" in text and "1 new, 0 fixed" in text
    assert "::error" not in text
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_setup.py -q -k health_step`
Expected: FAIL with `StopIteration` (no such step).

- [ ] **Step 3: Add the step** (`svarupa/setup/ci_github.py`, at the end of `_TEMPLATE`, after the delta step's `exit "$code"` line and before the closing `"""`)

```yaml

      # Fails the job only for problems this pull request adds: a new health
      # violation or a lower grade (exit 3), or a check that could not run
      # (exit 1). Old findings never fail it. Runs even when the delta step
      # failed, so neither result hides the other. To make it report-only,
      # change the last line to `exit 0`.
      - name: health of this change
        if: ${{ !cancelled() }}
        shell: bash
        run: |
          BASE_SHA="${{ github.event.pull_request.base.sha }}"
          svarupa check . --base "$BASE_SHA" --format github \\
            > /tmp/health.txt 2>&1 && code=0 || code=$?
          cat /tmp/health.txt
          {
            echo '## Health of this change'
            echo '````'
            grep -v '^::' /tmp/health.txt || true
            echo '````'
          } >> "$GITHUB_STEP_SUMMARY"
          exit "$code"
```

(In the Python string a shell line continuation is written `\\`, as the existing step does.)

Change `next_steps` to:

```python
        return (
            "Run `svarupa . --lock` and commit .svarupa/architecture.lock as the base.",
            "Commit .github/workflows/svarupa.yml.",
            "Pull requests now fail only for health problems they add; see the "
            "workflow's comment to make the gate report-only.",
        )
```

Update the target's `summary` to `"a GitHub Actions workflow that diffs architecture and gates health per PR"`.

- [ ] **Step 4: Mutations** (append to `MUTATIONS` in `scripts/mutate_setup.py`)

```python
    (
        "the health step is skipped after a failed delta",
        "svarupa/setup/ci_github.py",
        "        if: ${{ !cancelled() }}\n",
        "        if: ${{ success() }}\n",
    ),
    (
        "raw annotations leak into the job summary",
        "svarupa/setup/ci_github.py",
        "            grep -v '^::' /tmp/health.txt || true\n",
        "            cat /tmp/health.txt\n",
    ),
```

Run: `uv run python scripts/mutate_setup.py 2>&1 | tail -4`
Expected: every mutation caught. If an existing mutation's text now matches a different occurrence first (for example `exit "$code"`), confirm it still breaks the delta step's line and say so in the report.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_setup.py -q`
Expected: PASS (the byte-exact install test compares against `WORKFLOW`, so it follows the template).

- [ ] **Step 6: Lint, commit**

```bash
uv run ruff format svarupa tests && uv run ruff check svarupa tests scripts && uv run pyright svarupa
git add svarupa/setup/ci_github.py scripts/mutate_setup.py tests/test_setup.py
git commit -m "Gate pull request health in the installed GitHub workflow"
```

---

### Task 3: The gate in the installed GitLab file

**Files:**
- Modify: `svarupa/setup/ci_gitlab.py` (`_TEMPLATE`, `next_steps`, `summary`)
- Test: `tests/test_setup.py`

**Interfaces:**
- Consumes: `svarupa check --format gitlab` (Task 1).
- Produces: a `health_check` job with a `codequality` report artifact.

- [ ] **Step 1: Write the failing test** (append to `tests/test_setup.py`)

```python
def test_the_gitlab_health_job_reports_code_quality() -> None:
    job = yaml.safe_load(GITLAB_CI)["health_check"]
    assert job["rules"] == [{"if": '$CI_PIPELINE_SOURCE == "merge_request_event"'}]
    assert job["variables"]["GIT_DEPTH"] == 0
    assert job["artifacts"]["reports"]["codequality"] == "gl-code-quality-report.json"
    assert job["artifacts"]["when"] == "always"
    script = "\n".join(job["script"])
    assert '--base "$CI_MERGE_REQUEST_DIFF_BASE_SHA" --format gitlab' in script
    assert "> gl-code-quality-report.json" in script
    assert 'exit "$code"' in script
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_setup.py -q -k gitlab_health`
Expected: FAIL with `KeyError: 'health_check'`.

- [ ] **Step 3: Add the job** (append to `_TEMPLATE` in `svarupa/setup/ci_gitlab.py`, after the `architecture_diff` job, before the closing `"""`)

```yaml

# Fails the merge request only for health problems it adds: a new violation
# or a lower grade (exit 3), or a check that could not run (exit 1). The new
# violations appear in the merge request through the Code Quality report.
# To make it report-only, change the last line to `exit 0`.
health_check:
  image: python:3.12
  rules:
    - if: $CI_PIPELINE_SOURCE == "merge_request_event"
  variables:
    GIT_DEPTH: 0
  before_script:
    - pip install uv
    - uv tool install 'svarupa==__SVARUPA_VERSION__'
    - export PATH="$HOME/.local/bin:$PATH"
  script:
    - |
      svarupa check . --base "$CI_MERGE_REQUEST_DIFF_BASE_SHA" --format gitlab \\
        > gl-code-quality-report.json && code=0 || code=$?
      exit "$code"
  artifacts:
    when: always
    reports:
      codequality: gl-code-quality-report.json
```

(The text report goes to stderr, so it is in the job log while stdout fills the report file.)

Update `next_steps` to add `"Merge requests now fail only for health problems they add; see the health_check job's comment to make it report-only."` and the target `summary` to mention the health gate.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_setup.py -q`
Expected: PASS.

- [ ] **Step 5: Lint, commit**

```bash
uv run ruff format svarupa tests && uv run ruff check svarupa tests && uv run pyright svarupa
git add svarupa/setup/ci_gitlab.py tests/test_setup.py
git commit -m "Gate merge request health in the installed GitLab job"
```

---

### Task 4: Svarupa's own health job, docs and skill

**Files:**
- Modify: `.github/workflows/ci.yml` (new `health` job)
- Modify: `docs/health.md`, `svarupa/setup/skill.py` (`SKILL_MD`), `skills/svarupa/SKILL.md`
- Test: `tests/test_setup.py`

- [ ] **Step 1: Write the failing test** (append to `tests/test_setup.py`)

```python
def test_svarupa_gates_its_own_pull_requests() -> None:
    ci = yaml.safe_load((REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf8"))
    job = ci["jobs"]["health"]
    assert job["if"] == "github.event_name == 'pull_request'"
    checkout = next(s for s in job["steps"] if "checkout" in str(s.get("uses", "")))
    assert checkout["with"]["fetch-depth"] == 0
    run = "\n".join(str(s.get("run", "")) for s in job["steps"])
    assert "uv run svarupa check . --base" in run and "--format github" in run
    assert "GITHUB_STEP_SUMMARY" in run
```

(`REPO` is the repository root already defined in this test module; if it has another name there, use it.)

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_setup.py -q -k own_pull_requests`
Expected: FAIL with `KeyError: 'health'`.

- [ ] **Step 3: Add the job** (append to `.github/workflows/ci.yml` under `jobs:`, after `benchmark`)

```yaml

  # The health gate Svarupa installs for users, run on itself: a pull
  # request fails only for health problems it adds.
  health:
    if: github.event_name == 'pull_request'
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - uses: astral-sh/setup-uv@v5
        with:
          enable-cache: true
      - run: uv sync --dev
      - name: health of this change
        shell: bash
        run: |
          uv run svarupa check . --base "${{ github.event.pull_request.base.sha }}" --format github \
            > /tmp/health.txt 2>&1 && code=0 || code=$?
          cat /tmp/health.txt
          {
            echo '## Health of this change'
            echo '````'
            grep -v '^::' /tmp/health.txt || true
            echo '````'
          } >> "$GITHUB_STEP_SUMMARY"
          exit "$code"
```

- [ ] **Step 4: Docs and skill**

In `docs/health.md`, at the end of "## Checking a change", add:

```markdown
In CI: `svarupa setup ci_github` adds a "health of this change" step to the
pull request workflow, and `svarupa setup ci_gitlab` adds a `health_check`
job. Both run `svarupa check` against the pull request's base and fail only
for problems the pull request adds. On GitHub each new violation is an
annotation on its line (`--format github`); on GitLab it appears in the
merge request through a Code Quality report (`--format gitlab`). A comment
in each file shows the one-line change that makes the gate report-only.
```

In `SKILL_MD`, change the sentence starting "`svarupa setup ci_github` installs a GitHub Actions workflow that does all three per pull request" so it also says the workflow gates health (fails only on health problems the pull request adds). Regenerate `skills/svarupa/SKILL.md`:
`uv run python -c "from pathlib import Path; from svarupa.setup.skill import SKILL_MD; Path('skills/svarupa/SKILL.md').write_text(SKILL_MD, encoding='utf8')"`.

- [ ] **Step 5: This branch passes its own gate**

Run: `uv run svarupa check . --base main`
Expected: exit 0 ("0 new"). If it reports a new violation from this branch's code (for example a function grown past 50 lines), fix the code, not the check, and say what you changed.

- [ ] **Step 6: Full suite, lint, mutations, commit**

```bash
uv run pytest -q 2>&1 | tail -2
uv run ruff format svarupa tests && uv run ruff check svarupa tests scripts && uv run pyright svarupa
uv run python scripts/mutate_setup.py 2>&1 | tail -3
uv run python scripts/mutate_packs.py 2>&1 | grep -c "^caught"
git add .github/workflows/ci.yml docs/health.md svarupa/setup/skill.py skills/svarupa/SKILL.md tests/test_setup.py
git commit -m "Run the health gate on Svarupa's own pull requests"
```

Expected: suite PASS, every setup mutation caught, 44 pack mutations caught.
