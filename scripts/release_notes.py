"""Draft release notes from the commits since the previous tag.

The draft is a starting point, not the final text: it lands in
docs/releases/vVERSION.md inside the release PR, where it is edited before
merge. `release.sh publish` refuses while a `TODO:` marker is left.

Grouping, first match wins:
    release commits ("Release 0.2.2")     left out
    every changed file is documentation   Docs
    subject starts with Fix               Fixes
    subject starts with Add               New
    anything else                         Changes

Usage:
    release_notes.py VERSION     write docs/releases/vVERSION.md, print its path
"""

from __future__ import annotations

import re
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

REPO_URL = "https://github.com/aiwithvd/svarupa"
SECTIONS = ("Fixes", "New", "Docs", "Changes")
_FIX_WORDS = frozenset({"fix", "fixes", "fixed"})
_NEW_WORDS = frozenset({"add", "adds", "added"})
_RELEASE = re.compile(r"^Release v?[0-9]")


@dataclass(frozen=True)
class Commit:
    subject: str
    files: tuple[str, ...]


def _is_doc(path: str) -> bool:
    return path.endswith(".md") or path.startswith("docs/")


def section_of(subject: str, files: Sequence[str]) -> str | None:
    if _RELEASE.match(subject):
        return None
    first = subject.split(maxsplit=1)[0].rstrip(":").lower() if subject.strip() else ""
    if files and all(_is_doc(f) for f in files):
        return "Docs"
    if first in _FIX_WORDS:
        return "Fixes"
    if first in _NEW_WORDS:
        return "New"
    return "Changes"


def draft(version: str, prev_tag: str | None, commits: Sequence[Commit], repo_url: str) -> str:
    groups: dict[str, list[str]] = {s: [] for s in SECTIONS}
    for c in commits:
        section = section_of(c.subject, c.files)
        if section is not None:
            groups[section].append(c.subject)

    lines: list[str] = []
    for section in SECTIONS:
        if not groups[section]:
            continue
        lines += [f"## {section}", ""]
        lines += [f"- {subject}" for subject in groups[section]]
        lines.append("")
    lines += [
        "## Upgrading",
        "",
        "TODO: say what users must do after `uv tool upgrade svarupa` (regenerate the",
        "lockfile, new flags, changed output), or write `No action needed.`",
        "Rewrite the bullets above for users: what changed for them, not how.",
        "",
    ]
    if prev_tag:
        lines.append(f"**Full changelog:** {repo_url}/compare/{prev_tag}...v{version}")
        lines.append("")
    return "\n".join(lines)


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True, check=True).stdout


def _previous_tag() -> str | None:
    try:
        return _git("describe", "--tags", "--abbrev=0", "HEAD").strip() or None
    except subprocess.CalledProcessError:
        return None


def _commits_since(tag: str | None) -> list[Commit]:
    rng = f"{tag}..HEAD" if tag else "HEAD"
    out: list[Commit] = []
    for line in _git("log", "--no-merges", "--reverse", "--format=%H%x1f%s", rng).splitlines():
        sha, subject = line.split("\x1f", 1)
        files = tuple(
            _git("diff-tree", "--no-commit-id", "--name-only", "-r", sha).splitlines()
        )
        out.append(Commit(subject, files))
    return out


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__, file=sys.stderr)
        return 2
    version = argv[0]
    prev = _previous_tag()
    path = Path("docs/releases") / f"v{version}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(draft(version, prev, _commits_since(prev), REPO_URL), encoding="utf8")
    print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
