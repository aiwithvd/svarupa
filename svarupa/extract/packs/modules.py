"""Module resolution for languages whose imports name packages.

Python and TypeScript resolve in `resolve.py`, where their measured traps
are documented. A language added as a pack brings its resolver here, and
`resolve.py` asks it first. A resolver answers two questions only: which
repository files does this specifier name, and is it provably outside the
repository. Everything else (edges, scorecard bins, references) stays in
one place.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from svarupa.extract.base import FileFacts

__all__ = ["ModuleContext", "ModuleResolver"]


class ModuleResolver(Protocol):
    def targets(self, spec: str, from_file: str, /) -> tuple[str, ...]:
        """Repository files the specifier names, sorted; () when none."""
        ...

    def is_external(self, spec: str, /) -> bool:
        """True only when the specifier is provably outside the repository:
        the standard library or a declared dependency."""
        ...


@dataclass(frozen=True, slots=True)
class ModuleContext:
    """What a resolver may know. It never reads files itself."""

    files: frozenset[str]
    facts: tuple[FileFacts, ...]
    deps: frozenset[str]
    go_modules: tuple[tuple[str, str], ...] = ()  # (module path, directory)
