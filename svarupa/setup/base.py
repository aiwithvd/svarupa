"""The `Target` contract, below the targets that implement it."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import ClassVar

__all__ = ["Target"]


class Target(ABC):
    """One installable integration: a named set of files and what to do next."""

    name: ClassVar[str]
    summary: ClassVar[str]

    @abstractmethod
    def files(self) -> tuple[tuple[str, str], ...]:
        """(repository-relative POSIX path, exact file content) pairs."""

    @abstractmethod
    def next_steps(self) -> tuple[str, ...]:
        """What the user does after the files exist, e.g. what to commit."""
