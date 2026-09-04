"""Replay real commits from a foreign repository and measure lockfile churn.

Every stability test in the suite edits bytes in place: rename a local, add a
docstring, reformat. Those are the shapes the design's verification table
names, and they are not the changes developers actually make. A promoted
decision says a gate must be able to fail and prefers replayed real history
over synthetic string edits, and until this script existed the stability
promise, which is the product, was verified only on the shapes its fixtures
contained.

So: walk N real commits, build the lockfile at each tree, and record how many
lines the delta contains. The output is a distribution, not a pass mark. The
question is not "is it zero" but "when it is not zero, is it because the
architecture changed".

**Read-only.** Trees are extracted with `git archive`, which never writes to the
source repository. `git worktree` would add entries under its `.git`, and
measuring someone's repository must not modify it.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tarfile
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from svarupa import __version__
from svarupa.build import build
from svarupa.detect import ScanLimits, detect
from svarupa.extract import declared_dependencies, extract
from svarupa.lock import Lockfile, build_lock, diff


@dataclass(frozen=True, slots=True)
class Step:
    sha: str
    subject: str
    files_changed: int
    added: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()
    error: str | None = None

    @property
    def lines(self) -> int:
        return len(self.added) + len(self.removed)


@dataclass
class Report:
    repo: str
    steps: list[Step] = field(default_factory=list[Step])

    def render(self) -> str:
        ok = [s for s in self.steps if s.error is None]
        if not ok:
            return f"{self.repo}: no commits could be measured"
        quiet = [s for s in ok if s.lines == 0]
        small = [s for s in ok if 1 <= s.lines <= 2]
        loud = sorted((s for s in ok if s.lines > 2), key=lambda s: -s.lines)

        out = [
            f"## {self.repo}",
            "",
            f"{len(ok)} commits measured"
            + (f", {len(self.steps) - len(ok)} skipped" if len(ok) < len(self.steps) else ""),
            "",
            f"- **{len(quiet)}** produced no architectural change",
            f"- **{len(small)}** produced 1-2 lines",
            f"- **{len(loud)}** produced 3 or more",
            "",
            f"Median lines per commit: {_median([s.lines for s in ok])}",
            f"Median files changed:    {_median([s.files_changed for s in ok])}",
            "",
        ]
        if loud:
            out += ["Largest deltas, for eyeballing whether they are real:", ""]
            for s in loud[:10]:
                out.append(
                    f"  {s.sha[:9]}  {s.lines:>3} lines  "
                    f"{s.files_changed:>3} files  {s.subject[:56]}"
                )
                for line in (*s.removed[:2], *s.added[:2]):
                    out.append(f"                 {line}")
            out.append("")
        return "\n".join(out)


def _median(values: list[int]) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    mid = len(s) // 2
    return float(s[mid]) if len(s) % 2 else (s[mid - 1] + s[mid]) / 2


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    ).stdout


def extract_tree(repo: Path, sha: str, into: Path) -> None:
    """Materialize one commit's tree without touching the source repository."""
    into.mkdir(parents=True, exist_ok=True)
    archive = into.parent / f"{sha}.tar"
    with archive.open("wb") as fh:
        subprocess.run(
            ["git", "-C", str(repo), "archive", "--format=tar", sha],
            stdout=fh,
            check=True,
        )
    with tarfile.open(archive) as tf:
        tf.extractall(into, filter="data")
    archive.unlink()


def lock_at(repo: Path, sha: str, workdir: Path, limits: ScanLimits) -> Lockfile:
    tree = workdir / sha
    extract_tree(repo, sha, tree)
    scan = detect(tree, limits)
    graph = build(scan, extract(scan, declared_dependencies(scan)), strict=False)
    return build_lock(graph, __version__).lockfile


def replay(repo: Path, count: int, limits: ScanLimits) -> Report:
    report = Report(repo.name)
    shas = git(repo, "rev-list", "--no-merges", f"-{count + 1}", "HEAD").split()
    # Oldest first, so each lockfile is reused as the next step's base and
    # every tree is built exactly once.
    shas.reverse()

    with tempfile.TemporaryDirectory() as td:
        workdir = Path(td)
        previous: Lockfile | None = None
        for sha in shas:
            subject = git(repo, "log", "-1", "--format=%s", sha).strip()
            changed = len(
                [
                    ln
                    for ln in git(repo, "show", "--name-only", "--format=", sha).splitlines()
                    if ln.strip()
                ]
            )
            try:
                current = lock_at(repo, sha, workdir, limits)
            except Exception as exc:
                report.steps.append(
                    Step(sha, subject, changed, error=f"{type(exc).__name__}: {exc}")
                )
                previous = None
                continue

            if previous is not None:
                delta = diff(previous, current)
                report.steps.append(
                    Step(
                        sha,
                        subject,
                        changed,
                        added=tuple(f"+ {r.render()}" for r in delta.added),
                        removed=tuple(f"- {r.render()}" for r in delta.removed),
                    )
                )
            previous = current
            print(f"  {sha[:9]} {subject[:60]}", file=sys.stderr)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repo", type=Path)
    parser.add_argument("-n", "--count", type=int, default=30)
    parser.add_argument("--max-files", type=int, default=20_000)
    parser.add_argument("--json", default=None, help="also write the raw steps here")
    args = parser.parse_args(argv)

    report = replay(args.repo.resolve(), args.count, ScanLimits(max_files=args.max_files))
    print(report.render())
    if args.json:
        Path(args.json).write_text(
            json.dumps(
                [
                    {
                        "sha": s.sha,
                        "subject": s.subject,
                        "files_changed": s.files_changed,
                        "added": list(s.added),
                        "removed": list(s.removed),
                        "error": s.error,
                    }
                    for s in report.steps
                ],
                indent=2,
            ),
            encoding="utf8",
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
