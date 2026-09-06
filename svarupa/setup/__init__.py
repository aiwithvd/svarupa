"""Installation targets: files svarupa writes into someone else's repository.

`svarupa setup <target>` installs an integration: the agent skill, or the
GitHub Actions workflow that diffs architecture per PR. Each target is a
`Target` declaring the files it owns; `install` is the one place that touches
the filesystem, so the rules live once:

* **Nothing is written until every collision is checked.** A refusal that
  arrives after a partial write leaves a half-installed target, which is worse
  than either outcome it sits between.
* **An existing file with different content is a refusal, not an overwrite.**
  These paths live in the user's repository; the file that is already there
  may be theirs. `--force` exists and says what it does.
* **An existing file is hostile input.** It can be binary, unreadable, or a
  directory; any of those counts as "different content", never as a crash.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from svarupa.diagnostics import Diagnostic, DiagnosticError, Severity
from svarupa.setup.base import Target
from svarupa.setup.ci_github import CiGithubTarget
from svarupa.setup.skill import SkillTarget

__all__ = ["TARGETS", "Installed", "Target", "install"]


# Verified rather than trusted: a test asserts each entry's key equals its
# class's `name`, so a renamed target cannot leave a stale key behind.
TARGETS: dict[str, type[Target]] = {
    SkillTarget.name: SkillTarget,
    CiGithubTarget.name: CiGithubTarget,
}


@dataclass(frozen=True, slots=True)
class Installed:
    written: tuple[Path, ...]
    unchanged: tuple[Path, ...]


def _differs(path: Path, content: str) -> bool:
    """Whether an existing path stands in the way of writing `content`.

    A directory, a binary file, or an unreadable file all differ: each is
    something already there that is not the file we would write, and the
    refusal message is the honest report for every one of those shapes.
    """
    try:
        return path.read_text(encoding="utf8") != content
    except (OSError, UnicodeDecodeError):
        return True


def install(target: Target, dest: Path, force: bool) -> Installed:
    """Write a target's files under `dest`, refusing before writing anything."""
    if not dest.is_dir():
        raise DiagnosticError(
            Diagnostic(
                code="SVA-S-002",
                severity=Severity.ERROR,
                message="is not an existing directory, so there is nowhere to set up into",
                subject=str(dest),
                suggested_fixes=("Pass --dest pointing at the repository root.",),
            )
        )
    planned = [(dest / Path(rel), content) for rel, content in target.files()]
    clashes = [p for p, content in planned if p.exists() and _differs(p, content)]
    if clashes and not force:
        raise DiagnosticError(
            Diagnostic(
                code="SVA-S-001",
                severity=Severity.ERROR,
                message=(
                    "already exists with different content, so setup would overwrite it; "
                    "nothing was written"
                ),
                subject=str(clashes[0]),
                suggested_fixes=(
                    "Re-run with --force to overwrite it.",
                    "Or move the existing file aside if it is yours.",
                ),
            )
        )
    written: list[Path] = []
    unchanged: list[Path] = []
    for path, content in planned:
        if path.exists() and not _differs(path, content):
            unchanged.append(path)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        # newline="" so the bytes on disk are exactly `content` on every
        # platform, the same rule the lockfile writer follows.
        path.write_text(content, encoding="utf8", newline="")
        written.append(path)
    return Installed(written=tuple(written), unchanged=tuple(unchanged))
