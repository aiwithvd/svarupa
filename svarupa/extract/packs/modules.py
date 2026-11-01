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

__all__ = [
    "CSharpNamespaces",
    "GoModules",
    "JvmPackages",
    "ModuleContext",
    "ModuleResolver",
    "RustCrates",
]


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
    rust_crates: tuple[tuple[str, str], ...] = ()  # (crate name, crate directory)
    rust_uses: tuple[tuple[str, str], ...] = ()  # (manifest directory, dependency name)


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


class CSharpNamespaces:
    """C#: a `using` names a namespace, not a file.

    `using A.B;` imports every file that declares namespace `A.B`; a file
    that declares several belongs to each of them. A nested namespace
    (`A.B.C`) is not part of `A.B`. `using static A.B.C` and `using X = A.B.C`
    name a type, so they narrow to the files of `A.B` that declare `C`. A name
    that is neither a declared namespace nor a declared type is unresolved.
    """

    top_level_only = False
    package_scoped_bare = False
    fields_without_this = True

    def __init__(self, context: ModuleContext) -> None:
        by_ns: dict[str, set[str]] = {}
        types: dict[str, set[str]] = {}
        for f in context.facts:
            if f.lang != "csharp":
                continue
            for ns in f.namespaces:
                by_ns.setdefault(ns, set()).add(f.path)
            for s in f.symbols:
                if s.kind in ("class", "interface"):
                    types.setdefault(s.qualified_name, set()).add(f.path)
        # NuGet package ids are case-insensitive (`xunit` provides `Xunit`).
        self.deps = frozenset(d.lower() for d in context.deps)
        self.by_ns = {ns: tuple(sorted(p)) for ns, p in by_ns.items()}
        self.types = {t: tuple(sorted(p)) for t, p in types.items()}

    def targets(self, spec: str, _from_file: str, /) -> tuple[str, ...]:
        if spec in self.by_ns:
            return self.by_ns[spec]
        parts = spec.split(".")
        for n in range(len(parts) - 1, 0, -1):
            hits = self.types.get(".".join(parts[: n + 1]))
            if hits and ".".join(parts[:n]) in self.by_ns:
                return tuple(p for p in hits if p in self.by_ns[".".join(parts[:n])])
        return ()

    def is_external(self, spec: str, /) -> bool:
        # A namespace the repo declares is never external, whatever it is named.
        if spec in self.by_ns:
            return False
        if spec.split(".")[0] in ("System", "Microsoft"):
            return True
        spec = spec.lower()
        head = spec.split(".")[:2]
        for dep in self.deps:
            if spec == dep or spec.startswith(dep + "."):
                return True
            # A package id rarely equals its namespace: the package
            # `Ardalis.Specification.EntityFrameworkCore` declares namespace
            # `Ardalis.Specification`. So a using that shares the first two
            # dot segments of a package id counts as that package too.
            parts = dep.split(".")
            if len(parts) >= 2 and len(head) == 2 and parts[:2] == head:
                return True
        return False


_RUST_STD = frozenset({"std", "core", "alloc", "proc_macro", "test"})


def _rust_module(rest: list[str]) -> tuple[str, ...]:
    """The module path of a file below `src` (or `src/bin/<x>`)."""
    names = [*rest[:-1], rest[-1].removesuffix(".rs")]
    if names[-1] == "mod" or (len(names) == 1 and names[0] in ("lib", "main")):
        names = names[:-1]
    return tuple(names)


class _RustLayout:
    """Where each `.rs` file sits: a tree is one crate root and its modules."""

    def __init__(self, files: frozenset[str], crate_dirs: set[str]) -> None:
        self.trees: dict[str, dict[tuple[str, ...], str]] = {}  # tree -> module -> file
        self.lib: dict[str, str] = {}  # tree -> its lib.rs
        self.where: dict[str, tuple[str, tuple[str, ...]]] = {}
        self.crate_dir: dict[str, str] = {}  # file -> directory of its crate
        for path in sorted(files):
            if path.endswith(".rs"):
                self._place(path, crate_dirs)

    @staticmethod
    def _src_at(parts: list[str], crate_dirs: set[str]) -> int | None:
        srcs = [i for i, p in enumerate(parts[:-1]) if p == "src"]
        known = [i for i in srcs if "/".join(parts[:i]) in crate_dirs]
        return (known or srcs or [None])[0]

    def _place_alone(self, path: str, crate_dirs: set[str]) -> None:
        """tests/, examples/, build.rs: a crate root of its own."""
        owner = [d for d in crate_dirs if d == "" or path.startswith(d + "/")]
        self.crate_dir[path] = max(owner, key=len, default="")
        self.where[path] = (path, ())
        self.trees.setdefault(path, {})[()] = path

    def _place(self, path: str, crate_dirs: set[str]) -> None:
        parts = path.split("/")
        at = self._src_at(parts, crate_dirs)
        if at is None:
            self._place_alone(path, crate_dirs)
            return
        base, rest = "/".join(parts[: at + 1]), parts[at + 1 :]
        self.crate_dir[path] = "/".join(parts[:at])
        if rest[0] == "bin" and len(rest) >= 2:
            tree = f"{base}/bin/{rest[1].removesuffix('.rs')}"
            module = _rust_module(rest[2:]) if len(rest) > 2 else ()
        else:
            tree, module = base, _rust_module(rest)
        self.where[path] = (tree, module)
        is_lib = tree == base and rest == ["lib.rs"]
        if is_lib:
            self.lib[tree] = path
        files = self.trees.setdefault(tree, {})
        # lib.rs names the root when the crate has both it and main.rs
        if module not in files or is_lib:
            files[module] = path

    def longest(
        self,
        tree: str,
        base: tuple[str, ...],
        tail: list[str],
        *,
        root: str | None = None,
        module_only: bool = False,
    ) -> str | None:
        """The file of the module `base + tail`, or of the module `base + tail[:-1]`
        when the last segment is an item in it. Nothing else: a path with two
        segments that are not modules names nothing. `module_only` (a glob)
        allows no item segment. `root` is the file standing for the empty module
        path, when it is not the tree's own."""
        files = self.trees.get(tree, {})
        for n in range(len(tail), max(len(tail) - (0 if module_only else 1), 0) - 1, -1):
            path = (*base, *tail[:n])
            if not path and root is not None:
                return root
            if (hit := files.get(path)) is not None:
                return hit
        return None


class RustCrates:
    """Rust: a `use` path names a module, and a module is a file.

    `crate.a.b.Item` is the module file of `a.b` (`a/b.rs` or `a/b/mod.rs`),
    with at most one trailing segment that is an item in it. A path with two
    segments that name no module file names nothing, and a glob (`use a::*`)
    needs its module itself. `self.x` is relative to the caller's module and
    `super.x` to its parent. `<crate>.a.b` enters another crate's library
    root, where `<crate>` is a `[package]` name (or a path dependency's key)
    with `-` written as `_`. `src/main.rs` and `src/bin/*` are crate roots of
    their own, and they reach the library of their crate by its name. A crate
    nobody declares resolves to nothing. Every `[package]` Cargo.toml is a
    crate (`[workspace] members` is not read); when two crates share a name
    the one in the shortest directory wins. `std`, `core`, `alloc` and every
    Cargo dependency are external; a crate of this repository never is, except
    that one named like `std`, `core` or `alloc` shadows it only for itself and
    for crates whose manifest lists it. An item inside an inline `mod` of a
    file (`crate.a.inline.Item`) is not followed.
    """

    top_level_only = False
    # Bare names are scoped by module (a `use` name or a glob), not by
    # directory, so the resolver reads `names_modules` instead of a package.
    package_scoped_bare = False
    fields_without_this = False
    # `use a::b;` can import a module `b`, so `b::f()` looks in `a.b` first.
    names_modules = True

    def __init__(self, context: ModuleContext) -> None:
        self.deps = frozenset(d.replace("-", "_") for d in context.deps)
        self.names: dict[str, str] = {}
        for name, directory in sorted(context.rust_crates, key=lambda c: (c[1], c[0])):
            self.names.setdefault(name.replace("-", "_"), directory)
        self.uses = frozenset(context.rust_uses)
        self.layout = _RustLayout(context.files, {d for _, d in context.rust_crates})

    def _relative(self, parts: list[str], from_file: str, module_only: bool) -> str | None:
        """`crate.`, `self.` and `super.` paths, from the caller's module."""
        tree, base = self.layout.where[from_file]
        # main.rs is a crate root of its own, next to the library's root
        root = from_file if from_file.rsplit("/", 1)[-1] == "main.rs" and not base else None
        at = 1
        if parts[0] == "crate":
            base = ()
        elif parts[0] == "super":
            at = 0
        while at < len(parts) and parts[at] == "super":
            if not base:
                return None
            base, at = base[:-1], at + 1
        return self.layout.longest(
            tree, base, parts[at:], root=None if base else root, module_only=module_only
        )

    def _crate_dir(self, head: str, from_file: str) -> str | None:
        """The directory of the repo crate `head` names for this caller. A crate
        named like the standard library shadows it only for itself and for the
        crates whose manifest lists it."""
        directory = self.names.get(head)
        if directory is not None and head in _RUST_STD:
            mine = self.layout.crate_dir.get(from_file)
            if mine != directory and (mine, head) not in self.uses:
                return None
        return directory

    def targets(self, spec: str, from_file: str, /) -> tuple[str, ...]:
        """Files a path names. A trailing `*` (the resolver adds it for a glob)
        means the module itself must exist."""
        parts = spec.split(".")
        module_only = parts[-1] == "*"
        if module_only:
            parts = parts[:-1]
        here = self.layout.where.get(from_file)
        directory = self._crate_dir(parts[0], from_file)
        hit: str | None = None
        if parts[0] in ("crate", "self", "super") and here is not None:
            hit = self._relative(parts, from_file, module_only)
        elif directory is not None:
            tree = f"{directory}/src" if directory else "src"
            if tree in self.layout.lib:
                hit = self.layout.longest(tree, (), parts[1:], module_only=module_only)
        elif here is not None and parts[0] not in _RUST_STD:
            hit = self._child(here, parts, module_only)
        return (hit,) if hit is not None else ()

    def _child(
        self, here: tuple[str, tuple[str, ...]], parts: list[str], module_only: bool
    ) -> str | None:
        """`use db::Repo` where `db` is a module in scope: it must exist."""
        hit = self.layout.longest(here[0], here[1], parts, module_only=module_only)
        return None if hit == self.layout.trees[here[0]].get(here[1]) else hit

    def is_external(self, spec: str, /) -> bool:
        head = spec.split(".")[0]
        if head in _RUST_STD:
            return True  # a repo crate of that name was already tried by `targets`
        if head in self.names or head in ("crate", "self", "super"):
            return False
        return head in self.deps
