# Svarupa — Distribution & Onboarding Design

Date: 2026-09-19
Status: approved by user (round 1: 1A/2A/3A/4A/5A; round 2: 1A/2A/3A/4A)

## Goal ("3-minute goal")

A developer who has never seen svarupa goes from discovery to a working
architecture map of their own repository in under 3 minutes, with one install
command and no clone-and-build step.

## Decisions (settled with the user)

| Decision | Choice |
|---|---|
| Primary cycle goal | The 3-minute goal above (measurable) |
| PyPI | Publish now; name registration is release-blocking |
| GitHub | Public repo under `aiwithvd`, created via `gh`; dogfood `svarupa setup ci_github` on it |
| License | Switch AGPL-3.0 → MIT |
| Dirty tree | Land review-#24 changes as baseline first (done: commit `b41217b`, 779 tests green) |
| Install channels | Document `uv tool install svarupa` (primary), `pipx` / `pip` (fallbacks); no Homebrew |
| First-run UX | Terminal success summary + quickstart-led README (no `--open` flag) |
| Release automation | GitHub Actions trusted publishing (PyPI OIDC) on `v*` tags |
| First public version | `0.1.0` |

## Work items

### 1. Baseline (done)
Commit `b41217b` — review-#24 carried fixes; 779 passed, ruff/pyright clean.

### 2. License → MIT
Replace `LICENSE`, update `pyproject.toml` license metadata and classifiers,
sweep docs for AGPL mentions.

### 3. Packaging
- Version `0.1.0.dev0` → `0.1.0`; classifiers, project URLs, readme wiring.
- Verify wheel completeness (packaged `SKILL.md` constant, CI workflow
  template; existing tests assert skill byte-equality).
- `uv build`; smoke-test install into a clean venv; `svarupa --version` and a
  scan of a toy repo.

### 4. GitHub
`gh repo create` (public), push, description/topics; run
`svarupa setup ci_github` on the repo itself.

### 5. Release automation
`.github/workflows/release.yml`: on `v*` tag, build with uv, publish to PyPI
via trusted publishing (OIDC, no stored token). Tag `v0.1.0`.

### 6. First-run experience
- Terminal success summary after a scan: artifact paths, exact open command,
  scorecard one-liner, hints for `--lock` and `svarupa setup ci_github`.
- README rewrite: 3-step quickstart (install → scan → open), demo screenshot,
  install fallbacks, "what you get".

### 7. User-manual step
PyPI account + trusted-publisher configuration is done by the user with
click-by-click instructions provided at that point. Everything else is
automated first so that step is the only blocker.

## Verification (finish line)

Clean-environment simulation: fresh venv → install from (Test)PyPI →
`svarupa .` in a repo the tool has never seen → open `index.html` → diagrams
render. Full suite green (`uv run pytest`, `ruff`, `pyright`).

## Out of scope this cycle (queued for next design round)

- Better-than-archify interactive diagram polish (visual-quality pass).
- Environment distinction (production / staging / qa / dev) in diagrams and
  lockfile diffs.
- These were raised by the user 2026-09-19 and get their own spec after this
  cycle ships.
