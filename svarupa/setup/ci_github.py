"""The GitHub Actions target: the per-PR architecture diff, installed.

The workflow does the three lockfile steps the skill document describes, in
order: regenerate the base lockfile from base-branch code, read the committed
base from the base branch, then diff the head against the committed base with
the regenerated one as the drift guard. The delta lands in the job summary,
where a reviewer reads it without opening logs, and it lands there **whatever
the exit code was**: the run that fails is exactly the run whose delta someone
needs to read, so the summary is written first and the exit code re-raised
after.

The committed base is read from the *base branch*, not from the head checkout:
a PR that regenerates its own lockfile (as it should) would otherwise be
diffed against itself and every delta would read as "no change". First
adoption, where the base branch has no lockfile yet, falls back to diffing
against the regenerated base directly, which is the same comparison with no
drift to guard against. The two cases are told apart with `git cat-file -e`
against a base commit that was itself verified first, because `git show`'s
exit code conflates "the file is absent in that commit" with "git could not
read the commit at all", and the second must fail the job loudly rather than
silently dropping the drift guard.

The install is pinned to the version that generated this workflow, so a future
schema bump cannot break every adopted repository's CI overnight: the adopter
upgrades by re-running `svarupa setup ci_github`, which is a reviewable diff.
"""

from __future__ import annotations

from typing import ClassVar

from svarupa import __version__
from svarupa.setup.base import Target

_TEMPLATE = """\
name: architecture

on:
  pull_request:

permissions:
  contents: read

jobs:
  diff:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          # The base branch's tree and its committed lockfile are both needed,
          # so the fetch cannot be depth 1.
          fetch-depth: 0

      - uses: astral-sh/setup-uv@v5

      - name: install svarupa
        run: uv tool install 'svarupa==__SVARUPA_VERSION__'

      - name: regenerate the base lockfile from base-branch code
        shell: bash
        run: |
          git worktree add /tmp/base-src "${{ github.event.pull_request.base.sha }}"
          svarupa /tmp/base-src --lock --out /tmp/base-artifact

      # `shell: bash` turns pipefail on, so nothing here hides a failure.
      # The delta is written to the job summary before the exit code is
      # re-raised: a failing run is the one whose delta must be readable.
      - name: architecture delta
        shell: bash
        run: |
          BASE_SHA="${{ github.event.pull_request.base.sha }}"
          # A base commit git cannot read is a job failure, never a silent
          # fallback: falling back would drop the drift guard exactly when
          # the checkout is wrong.
          git cat-file -e "$BASE_SHA^{commit}"
          if git cat-file -e "$BASE_SHA:.svarupa/architecture.lock" 2>/dev/null; then
            git show "$BASE_SHA:.svarupa/architecture.lock" > /tmp/committed-base.lock
            svarupa . --lock \\
              --diff /tmp/committed-base.lock \\
              --drift-base /tmp/base-artifact/architecture.lock \\
              > /tmp/delta.txt 2>&1 && code=0 || code=$?
          else
            # First adoption: the base branch has no committed lockfile yet, so
            # the delta is taken against the regenerated base directly.
            svarupa . --lock \\
              --diff /tmp/base-artifact/architecture.lock \\
              > /tmp/delta.txt 2>&1 && code=0 || code=$?
          fi
          cat /tmp/delta.txt
          {
            echo '## Architecture delta'
            echo '````'
            cat /tmp/delta.txt
            echo '````'
          } >> "$GITHUB_STEP_SUMMARY"
          exit "$code"
"""

# The four-backtick fence above is not decoration: delta lines carry
# repository-derived paths, and a path containing ``` would terminate a
# three-backtick fence and inject markdown into the job summary.

WORKFLOW = _TEMPLATE.replace("__SVARUPA_VERSION__", __version__)


class CiGithubTarget(Target):
    name: ClassVar[str] = "ci_github"
    summary: ClassVar[str] = "a GitHub Actions workflow that diffs architecture per PR"

    def files(self) -> tuple[tuple[str, str], ...]:
        return ((".github/workflows/svarupa.yml", WORKFLOW),)

    def next_steps(self) -> tuple[str, ...]:
        return (
            "Run `svarupa . --lock` and commit .svarupa/architecture.lock as the base.",
            "Commit .github/workflows/svarupa.yml.",
        )
