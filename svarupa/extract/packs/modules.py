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

__all__ = ["GoModules", "JvmPackages", "ModuleContext", "ModuleResolver"]


class ModuleResolver(Protocol):
    # True when `pkg.Name` can only name a top-level definition (Go); False
    # when it may name a class member (Java static imports).
    top_level_only: bool
    # True when a bare name can only mean a definition in the caller's own
    # package directory (Go).
    package_scoped_bare: bool
    # True when `field.m()` without `this.` is a call on the class's field
    # (Java); languages that require `this.` leave it False.
    fields_without_this: bool

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


class GoModules:
    """Go: an import path names a package, which is a directory.

    `<module path>/<dir>` resolves to that directory's non-test `.go` files,
    using every go.mod in the repository (longest module path first, so a
    nested module wins over its parent). The standard library is any path
    whose first element has no dot: that is the Go toolchain's own rule.
    """

    top_level_only = True
    package_scoped_bare = True
    fields_without_this = False

    def __init__(self, context: ModuleContext) -> None:
        self.modules = tuple(sorted(context.go_modules, key=lambda m: (-len(m[0]), m[0])))
        self.deps = context.deps
        by_dir: dict[str, list[str]] = {}
        for path in context.files:
            if path.endswith(".go") and not path.endswith("_test.go"):
                directory = path.rsplit("/", 1)[0] if "/" in path else ""
                by_dir.setdefault(directory, []).append(path)
        self.by_dir = {d: tuple(sorted(v)) for d, v in by_dir.items()}

    def _module_of(self, spec: str) -> tuple[str, str] | None:
        for path, directory in self.modules:
            if spec == path or spec.startswith(path + "/"):
                return path, directory
        return None

    def targets(self, spec: str, from_file: str, /) -> tuple[str, ...]:
        if spec.startswith(("./", "../")):
            cur = from_file.split("/")[:-1]
            for part in spec.split("/"):
                if part in ("", "."):
                    continue
                if part == "..":
                    cur = cur[:-1]
                else:
                    cur.append(part)
            return self.by_dir.get("/".join(cur), ())
        hit = self._module_of(spec)
        if hit is None:
            return ()
        path, directory = hit
        rest = spec[len(path) :].strip("/")
        return self.by_dir.get("/".join(p for p in (directory, rest) if p), ())

    def is_external(self, spec: str, /) -> bool:
        # A module of this repository is never external, even when its path
        # has no dot (`module shop`), or a broken intra-repo import would be
        # filed under the standard library.
        if self._module_of(spec) is not None:
            return False
        if spec == "C" or "." not in spec.split("/", 1)[0]:
            return True
        return any(spec == d or spec.startswith(d + "/") for d in self.deps)


# Packages the JDK itself provides.
_JDK = (
    "java.",
    "javax.",
    "jdk.",
    "sun.",
    "com.sun.",
    "org.w3c.",
    "org.xml.",
    "org.ietf.",
    "org.omg.",
)


class JvmPackages:
    """Java: files are found by their `package` declaration, not their path.

    `a.b.C` names `C.java` in package `a.b`; a longer name (`a.b.C.member`,
    `a.b.C.Inner`) still names the file of `C`; a bare package (`a.b`, from
    `import a.b.*`) names every file in it. Source roots never need guessing,
    because every file says which package it is in.
    """

    top_level_only = False
    package_scoped_bare = False
    fields_without_this = True

    def __init__(self, context: ModuleContext) -> None:
        by_ns: dict[str, list[str]] = {}
        for f in context.facts:
            if f.lang == "java":
                by_ns.setdefault(f.namespace, []).append(f.path)
        self.by_ns = {ns: tuple(sorted(paths)) for ns, paths in by_ns.items()}
        self.deps = context.deps

    def targets(self, spec: str, _from_file: str, /) -> tuple[str, ...]:
        parts = spec.split(".")
        for n in range(len(parts) - 1, 0, -1):
            package, cls = ".".join(parts[:n]), parts[n]
            hits = tuple(
                p for p in self.by_ns.get(package, ()) if p.rsplit("/", 1)[-1] == f"{cls}.java"
            )
            if hits:
                return hits
        return self.by_ns.get(spec, ())

    def is_external(self, spec: str, /) -> bool:
        if spec.startswith(_JDK):
            return True
        if any(spec == ns or spec.startswith(ns + ".") for ns in self.by_ns if ns):
            return False  # our own package: a miss here is a resolution failure
        return any(spec == d or spec.startswith(d + ".") for d in self.deps)
