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
* **A symlink is a redirection, and setup never follows one.** A cloned
  repository can contain a pre-planted symlink at any path setup writes:
  `.claude/skills/svarupa/SKILL.md -> ~/.zshrc` would turn "set this repo up"
  into a file overwrite outside it, with SVA-S-001's own `--force` hint as the
  social engineering. A planned path that is a symlink, or whose existing
  ancestors resolve outside the destination, is refused, and `--force` does
  not override it: force means "replace your file", never "follow your link".
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from svarupa.diagnostics import Diagnostic, DiagnosticError, Severity
from svarupa.setup.base import Target
from svarupa.setup.ci_github import CiGithubTarget
from svarupa.setup.ci_gitlab import CiGitlabTarget
from svarupa.setup.skill import SkillTarget

__all__ = ["TARGETS", "Installed", "Target", "install"]


# Verified rather than trusted: a test asserts each entry's key equals its
# class's `name`, so a renamed target cannot leave a stale key behind.
TARGETS: dict[str, type[Target]] = {
    SkillTarget.name: SkillTarget,
    CiGithubTarget.name: CiGithubTarget,
    CiGitlabTarget.name: CiGitlabTarget,
}


@dataclass(frozen=True, slots=True)
class Installed:
    written: tuple[Path, ...]
    unchanged: tuple[Path, ...]


def _escapes(path: Path, dest: Path) -> bool:
    """Whether writing `path` could land outside `dest`.

    True if the path itself is a symlink (dangling ones included: `exists()`
    is False on those, which is exactly how one slipped past the collision
    sweep), or if its nearest existing ancestor resolves outside the resolved
    destination, which catches a symlinked directory like `.github -> /outside`.
    """
    if path.is_symlink():
        return True
    anchor = path.parent
    while not anchor.exists():
        anchor = anchor.parent
    try:
        real = anchor.resolve(strict=True)
        droot = dest.resolve(strict=True)
    except OSError:
        return True
    return real != droot and droot not in real.parents


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
    # Checked before collisions and never overridden by --force: force means
    # "replace your file", never "follow your link somewhere else".
    escaping = [p for p, _ in planned if _escapes(p, dest)]
    if escaping:
        raise DiagnosticError(
            Diagnostic(
                code="SVA-S-003",
                severity=Severity.ERROR,
                message=(
                    "is a symlink or resolves outside the destination, so setup "
                    "refuses to write through it (--force does not override this)"
                ),
                subject=str(escaping[0]),
                suggested_fixes=("Remove the symlink if it is not yours, then re-run.",),
            )
        )
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
                    *target.collision_note(),
                ),
            )
        )
    written: list[Path] = []
    unchanged: list[Path] = []
    for path, content in planned:
        if path.exists() and not _differs(path, content):
            unchanged.append(path)
            continue
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            # newline="" so the bytes on disk are exactly `content` on every
            # platform, the same rule the lockfile writer follows.
            path.write_text(content, encoding="utf8", newline="")
        except OSError as exc:
            # The write channel is part of the boundary too: a read-only
            # checkout reached the user as a raw PermissionError traceback.
            # If earlier files of a multi-file target were already written,
            # the refusal says so instead of leaving a half-install silent.
            done = "; already written: " + ", ".join(str(w) for w in written) if written else ""
            raise DiagnosticError(
                Diagnostic(
                    code="SVA-S-004",
                    severity=Severity.ERROR,
                    message=f"could not be written ({type(exc).__name__}: {exc}){done}",
                    subject=str(path),
                    suggested_fixes=("Check permissions on the destination.",),
                )
            ) from exc
        written.append(path)
    return Installed(written=tuple(written), unchanged=tuple(unchanged))
