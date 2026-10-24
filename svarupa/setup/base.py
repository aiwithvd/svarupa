"""The `Target` contract, below the targets that implement it."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

__all__ = ["Merge", "Target"]


@dataclass(frozen=True, slots=True)
class Merge:
    """A file the user may own, edited without touching their text: a marked
    Markdown block (`kind="block"`) or one MCP server entry (`kind="mcp"`)."""

    path: str  # repository-relative POSIX path
    kind: str  # "block" | "mcp"
    body: str = ""
    key: str = ""
    entry: Mapping[str, object] | None = None


class Target(ABC):
    """One installable integration: a named set of files and what to do next."""

    name: ClassVar[str]
    summary: ClassVar[str]

    def __init__(self, root: Path = Path()) -> None:
        self.root = root

    def merges(self) -> tuple[Merge, ...]:
        """Files edited in place rather than written whole. Empty for most targets."""
        return ()

    @abstractmethod
    def files(self) -> tuple[tuple[str, str], ...]:
        """(repository-relative POSIX path, exact file content) pairs."""

    def collision_note(self) -> tuple[str, ...]:
        """Extra fix lines for the SVA-S-001 refusal, e.g. a snippet to merge
        into a file the user already has. Empty for most targets."""
        return ()

    @abstractmethod
    def next_steps(self) -> tuple[str, ...]:
        """What the user does after the files exist, e.g. what to commit."""
