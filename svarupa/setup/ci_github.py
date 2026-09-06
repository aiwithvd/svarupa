"""The GitHub Actions target: the per-PR architecture diff, installed.

The workflow does the three lockfile steps the skill document describes, in
order: regenerate the base lockfile from base-branch code, read the committed
base from the base branch, then diff the head against the committed base with
the regenerated one as the drift guard. The delta lands in the job summary,
where a reviewer reads it without opening logs.

The committed base is read from the *base branch*, not from the head checkout:
a PR that regenerates its own lockfile (as it should) would otherwise be
diffed against itself and every delta would read as "no change". First
adoption, where the base branch has no lockfile yet, falls back to diffing
against the regenerated base directly, which is the same comparison with no
drift to guard against.
"""

from __future__ import annotations

from typing import ClassVar

from svarupa.setup.base import Target

WORKFLOW = """\
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
        run: uv tool install svarupa

      - name: regenerate the base lockfile from base-branch code
        shell: bash
        run: |
          git worktree add /tmp/base-src "${{ github.event.pull_request.base.sha }}"
          svarupa /tmp/base-src --lock --out /tmp/base-artifact

      # `shell: bash` turns pipefail on, so a failing svarupa run fails the
      # job instead of being masked by the `tee` it feeds.
      - name: architecture delta
        shell: bash
        run: |
          if git show "${{ github.event.pull_request.base.sha }}:.svarupa/architecture.lock" \\
              > /tmp/committed-base.lock 2>/dev/null; then
            svarupa . --lock \\
              --diff /tmp/committed-base.lock \\
              --drift-base /tmp/base-artifact/architecture.lock | tee /tmp/delta.txt
          else
            # First adoption: the base branch has no committed lockfile yet, so
            # the delta is taken against the regenerated base directly.
            svarupa . --lock \\
              --diff /tmp/base-artifact/architecture.lock | tee /tmp/delta.txt
          fi
          {
            echo '## Architecture delta'
            echo '```'
            cat /tmp/delta.txt
            echo '```'
          } >> "$GITHUB_STEP_SUMMARY"
"""


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
