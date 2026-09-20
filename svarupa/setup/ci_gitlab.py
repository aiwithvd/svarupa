"""The GitLab CI target: the per-merge-request architecture diff, installed.

This is the GitHub Actions workflow's GitLab idiom: one `architecture_diff`
job that runs on merge-request pipelines and does the same three lockfile
steps in order — regenerate the base lockfile from base-commit code, read the
committed base from that commit, then diff the head against the committed
base with the regenerated one as the drift guard.

The differences are the platform's, not the design's. GitLab runners clone
shallow by default, and the base commit's tree and its committed lockfile are
both needed, so the job sets `GIT_DEPTH: 0`. The base commit is
`$CI_MERGE_REQUEST_DIFF_BASE_SHA`, verified with `git cat-file -e` before any
fallback, because `git show`'s exit code conflates "no lockfile in that
commit" with "git could not read the commit at all", and the second must
fail the job loudly rather than silently dropping the drift guard. GitLab has
no job-summary surface, so the delta is printed to the job log and the exit
code re-raised after the print: the run that fails is exactly the run whose
delta someone needs to read.

The install is pinned to the version that generated this file, so a future
schema bump cannot break every adopted repository's CI overnight: the adopter
upgrades by re-running `svarupa setup ci_gitlab`, which is a reviewable diff.

Unlike the GitHub workflow's path, `.gitlab-ci.yml` commonly exists already.
The refusal (SVA-S-001) therefore carries the whole generated file as the
snippet to merge in by hand; `--force` still means "replace your file".
"""

from __future__ import annotations

from typing import ClassVar

from svarupa import __version__
from svarupa.setup.base import Target

_TEMPLATE = """\
# Installed by `svarupa setup ci_gitlab`: the per-merge-request architecture
# diff. Regenerates the base lockfile from base-commit code, diffs the head
# against the committed base with the regenerated one as the drift guard, and
# exits non-zero on an architecture change.
architecture_diff:
  image: python:3.12
  rules:
    - if: $CI_PIPELINE_SOURCE == "merge_request_event"
  variables:
    # Runners clone shallow by default; the base commit's tree and its
    # committed lockfile are both needed, so the fetch cannot be shallow.
    GIT_DEPTH: 0
  before_script:
    - pip install uv
    - uv tool install 'svarupa==__SVARUPA_VERSION__'
    - export PATH="$HOME/.local/bin:$PATH"
  script:
    - git worktree add /tmp/base-src "$CI_MERGE_REQUEST_DIFF_BASE_SHA"
    - svarupa /tmp/base-src --lock --out /tmp/base-artifact
    # A base commit git cannot read is a job failure, never a silent
    # fallback: falling back would drop the drift guard exactly when the
    # checkout is wrong. The delta is printed before the exit code is
    # re-raised: a failing run is the one whose delta must be readable.
    - |
      BASE_SHA="$CI_MERGE_REQUEST_DIFF_BASE_SHA"
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
      exit "$code"
"""

GITLAB_CI = _TEMPLATE.replace("__SVARUPA_VERSION__", __version__)


class CiGitlabTarget(Target):
    name: ClassVar[str] = "ci_gitlab"
    summary: ClassVar[str] = "a GitLab CI job that diffs architecture per merge request"

    def files(self) -> tuple[tuple[str, str], ...]:
        return ((".gitlab-ci.yml", GITLAB_CI),)

    def collision_note(self) -> tuple[str, ...]:
        return (
            "Or merge the job into your existing .gitlab-ci.yml by hand:\n\n" + GITLAB_CI,
        )

    def next_steps(self) -> tuple[str, ...]:
        return (
            "Run `svarupa . --lock` and commit .svarupa/architecture.lock as the base.",
            "Commit .gitlab-ci.yml.",
            "The job installs svarupa from PyPI; until the package is published "
            "there, point its `uv tool install` line at a checkout of svarupa.",
        )
