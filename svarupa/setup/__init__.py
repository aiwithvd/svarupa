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
  The one exception is a file setup merges into (not one it writes whole): a
  symlink there is edited through when it resolves to a regular file inside
  the destination, such as `AGENTS.md -> CLAUDE.md`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from svarupa.diagnostics import Diagnostic, DiagnosticError, Severity
from svarupa.setup.base import Merge, Target
from svarupa.setup.ci_github import CiGithubTarget
from svarupa.setup.ci_gitlab import CiGitlabTarget
from svarupa.setup.ide import (
    AgentsTarget,
    ClaudeTarget,
    CopilotTarget,
    CursorTarget,
    IdeTarget,
    WindsurfTarget,
)
from svarupa.setup.merge import MergeError, merge_block, merge_mcp
from svarupa.setup.skill import SkillTarget

__all__ = ["TARGETS", "Installed", "Target", "install"]


# Verified rather than trusted: a test asserts each entry's key equals its
# class's `name`, so a renamed target cannot leave a stale key behind.
TARGETS: dict[str, type[Target]] = {
    SkillTarget.name: SkillTarget,
    CiGithubTarget.name: CiGithubTarget,
    CiGitlabTarget.name: CiGitlabTarget,
    AgentsTarget.name: AgentsTarget,
    ClaudeTarget.name: ClaudeTarget,
    CursorTarget.name: CursorTarget,
    CopilotTarget.name: CopilotTarget,
    WindsurfTarget.name: WindsurfTarget,
    IdeTarget.name: IdeTarget,
}


@dataclass(frozen=True, slots=True)
class Installed:
    written: tuple[Path, ...]
    unchanged: tuple[Path, ...]
    actions: tuple[tuple[Path, str], ...] = ()


def _escapes(path: Path, dest: Path, merge: bool = False) -> bool:
    """Whether writing `path` could land outside `dest`.

    True if the path itself is a symlink (dangling ones included: `exists()`
    is False on those, which is exactly how one slipped past the collision
    sweep), or if its nearest existing ancestor resolves outside the resolved
    destination, which catches a symlinked directory like `.github -> /outside`.
    A merge path may be a symlink to a regular file inside the destination.
    """
    if path.is_symlink():
        if not merge:
            return True
        try:
            target = path.resolve(strict=True)
            droot = dest.resolve(strict=True)
        except OSError:
            return True
        if not target.is_file() or droot not in target.parents:
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


def _read(path: Path) -> str:
    """The file's text with its line endings exactly as stored."""
    return path.read_bytes().decode("utf8")


def _differs(path: Path, content: str) -> bool:
    """Whether an existing path stands in the way of writing `content`.

    A directory, a binary file, or an unreadable file all differ: each is
    something already there that is not the file we would write, and the
    refusal message is the honest report for every one of those shapes.
    """
    try:
        return _read(path) != content
    except (OSError, UnicodeDecodeError):
        return True


def _merged(path: Path, merge: Merge) -> str:
    """The content `merge` leaves at `path`, or SVA-S-005 when it cannot."""
    try:
        if path.exists() and not path.is_file():
            raise MergeError("is a directory, not a file")
        existing = _read(path) if path.is_file() else None
        if merge.kind == "block":
            return merge_block(existing, merge.body)
        return merge_mcp(existing, merge.key, dict(merge.entry or {}))
    except (MergeError, OSError, UnicodeDecodeError) as exc:
        raise DiagnosticError(
            Diagnostic(
                code="SVA-S-005",
                severity=Severity.ERROR,
                message=f"{exc}; nothing was written",
                subject=str(path),
                suggested_fixes=("Fix the file by hand, then re-run setup.",),
            )
        ) from exc


def _by_target(
    merges: list[tuple[Path, Merge]], planned: list[tuple[Path, str]]
) -> list[tuple[Path, Merge]]:
    """Merges keyed by the real file they edit, each file once.

    Two block merges that resolve to one file with the same body (AGENTS.md
    symlinked to CLAUDE.md) apply once; any other pair on one file is refused.
    """
    owned = {p.resolve() for p, _ in planned}
    out: dict[Path, Merge] = {}
    for p, m in merges:
        real = p.resolve()
        prior = out.get(real)
        if real in owned or (prior is not None and not _same_block(prior, m)):
            raise DiagnosticError(
                Diagnostic(
                    code="SVA-S-005",
                    severity=Severity.ERROR,
                    message="is set up twice by this target; nothing was written",
                    subject=str(p),
                    suggested_fixes=("Report this as a bug in the target.",),
                )
            )
        out.setdefault(real, m)
    return list(out.items())


def _same_block(a: Merge, b: Merge) -> bool:
    return a.kind == b.kind == "block" and a.body == b.body


def _action(path: Path, content: str) -> str:
    if not path.exists():
        return "create"
    return "update" if _differs(path, content) else "unchanged"


def install(target: Target, dest: Path, force: bool, *, dry_run: bool = False) -> Installed:
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
    merges = [(dest / Path(m.path), m) for m in target.merges()]
    seen: set[Path] = set()
    for p in [*(p for p, _ in planned), *(p for p, _ in merges)]:
        if p in seen:
            raise DiagnosticError(
                Diagnostic(
                    code="SVA-S-005",
                    severity=Severity.ERROR,
                    message="is set up twice by this target; nothing was written",
                    subject=str(p),
                    suggested_fixes=("Report this as a bug in the target.",),
                )
            )
        seen.add(p)
    # Checked before collisions and never overridden by --force: force means
    # "replace your file", never "follow your link somewhere else".
    escaping = [
        *(p for p, _ in planned if _escapes(p, dest)),
        *(p for p, _ in merges if _escapes(p, dest, merge=True)),
    ]
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
                    "If this is Svarupa's own file and the design changed, re-run with --update.",
                    "Re-run with --force to overwrite it.",
                    "Or move the existing file aside if it is yours.",
                    *target.collision_note(),
                ),
            )
        )
    # Every merge is computed before anything is written, so a refused merge
    # (SVA-S-005) leaves the destination exactly as it was.
    merged = [(p, _merged(p, m)) for p, m in _by_target(merges, planned)]
    todo = [*planned, *merged]
    actions = tuple((p, _action(p, c)) for p, c in todo)
    if dry_run:
        return Installed(
            written=(),
            unchanged=tuple(p for p, a in actions if a == "unchanged"),
            actions=actions,
        )
    written: list[Path] = []
    unchanged: list[Path] = []
    for path, content in todo:
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
    return Installed(written=tuple(written), unchanged=tuple(unchanged), actions=actions)
