"""Which AI tool rules files no longer match what `svarupa setup ide --update`
would write: the agents reading them are working from an old design.

Only files that already hold Svarupa's rules are judged: a marked block in a
file the user owns, or a whole file Svarupa owns. Text outside the markers and
line endings never count. Nothing is written.
"""

from __future__ import annotations

from pathlib import Path

from svarupa.setup.merge import BEGIN, MergeError, merge_block

__all__ = ["stale_rules"]

_BLOCK_FILES = ("AGENTS.md", "CLAUDE.md", ".github/copilot-instructions.md")
_OWNED_FILES = (
    ".claude/skills/svarupa/SKILL.md",
    ".cursor/rules/svarupa.mdc",
    ".windsurf/rules/svarupa.md",
)


def _text(path: Path) -> str | None:
    try:
        return path.read_bytes().decode("utf8") if path.is_file() else None
    except (OSError, UnicodeDecodeError):
        return None


def _lf(text: str) -> str:
    return text.replace("\r\n", "\n")


def _has_block(text: str) -> bool:
    return any(line.rstrip("\r") == BEGIN for line in text.splitlines())


def _present(root: Path) -> tuple[dict[str, str], dict[str, str]]:
    """The block files holding a Svarupa block, and Svarupa's own files."""
    blocks: dict[str, str] = {}
    for rel in _BLOCK_FILES:
        text = _text(root / rel)
        if text is not None and _has_block(text):
            blocks[rel] = text
    owned: dict[str, str] = {}
    for rel in _OWNED_FILES:
        text = _text(root / rel)
        if text is not None:
            owned[rel] = text
    return blocks, owned


def _stale_block(text: str, body: str) -> bool:
    try:
        return _lf(merge_block(text, body)) != _lf(text)
    except MergeError:
        return True  # broken markers: setup would refuse, so say so


def stale_rules(root: Path) -> list[str]:
    """Repository-relative paths of rules files that are out of date, sorted."""
    blocks, owned = _present(root)
    if not blocks and not owned:
        return []  # nothing to compare, so the repository is not analyzed again

    from svarupa.setup.ide import IdeTarget

    target = IdeTarget(root, every=True)
    want_blocks = {m.path: m.body for m in target.merges() if m.kind == "block"}
    want_files = dict(target.files())
    stale = [rel for rel, text in blocks.items() if _stale_block(text, want_blocks[rel])]
    stale += [rel for rel, text in owned.items() if _lf(text) != want_files[rel]]
    return sorted(stale)
