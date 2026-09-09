"""Stage 1: walk the tree, classify what we find.

Everything downstream inherits this stage's determinism. If the file set or its
ordering varies between machines, the lockfile varies, every pull request shows
architecture changes that did not happen, and the CI pillar is worthless.

Three properties this stage must hold:

1. **One code path.** We never shell out to ``git ls-files``, tempting as it is.
   Output would then depend on whether git is installed and which version, so
   the same commit could produce two different lockfiles. Ignore handling is
   implemented in-process against the same rules everywhere.
2. **NFC at the boundary.** Nothing downstream ever sees a non-normalized path.
3. **Explicit ordering.** Directory entries are sorted by codepoint before
   recursion. ``os.scandir`` order is filesystem-dependent and is never trusted.

Test, generated, vendored and tooling code is classified but **excluded from
architecture derivation and the lockfile**. On a typical repository it is
30-50% of files and imports everything, which would wreck module layering and
bury the real structure.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import os
import re
import unicodedata
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, cast

import pathspec  # pyright: ignore[reportMissingTypeStubs]

from svarupa.diagnostics import Diagnostic, DiagnosticError, Severity
from svarupa.identity import collision_check
from svarupa.model import norm_path

__all__ = [
    "DEFAULT_EXCLUDES",
    "FileRec",
    "FileRole",
    "Scan",
    "ScanLimits",
    "Workspace",
    "detect",
    "load_toml",
]

# --------------------------------------------------------------------------
# Classification tables
# --------------------------------------------------------------------------

LANG_BY_EXT: dict[str, str] = {
    ".py": "python",
    ".pyi": "python",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".mts": "typescript",
    ".cts": "typescript",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".sql": "sql",
}

# Structured files where architecture actually lives. These parse
# deterministically and are pure signal: compose tells you the service
# topology, OpenAPI the API surface, SQL the data model.
CONFIG_NAMES: dict[str, str] = {
    "docker-compose.yml": "compose",
    "docker-compose.yaml": "compose",
    "compose.yml": "compose",
    "compose.yaml": "compose",
    # Variants are part of the deployed shape, not an exotic case:
    # docker-compose auto-loads the override file, and a service defined only
    # there was silently missing from a view whose whole claim is the deployed
    # system.
    "docker-compose.override.yml": "compose",
    "docker-compose.override.yaml": "compose",
    "package.json": "manifest-npm",
    "pyproject.toml": "manifest-python",
    "go.mod": "manifest-go",
    "Cargo.toml": "manifest-cargo",
    "pom.xml": "manifest-maven",
    "requirements.txt": "manifest-python",
    "pnpm-workspace.yaml": "workspace-pnpm",
    "go.work": "workspace-go",
}

# docker-compose.<env>.yml and compose.<env>.yaml. Matched by shape, not by an
# enumeration of environment names, because teams invent those freely.
_COMPOSE_VARIANT = re.compile(r"^(docker-)?compose\.[A-Za-z0-9_-]+\.ya?ml$")
CONFIG_GLOBS: tuple[tuple[str, str], ...] = (
    (r"^openapi\.(ya?ml|json)$", "openapi"),
    (r"^swagger\.(ya?ml|json)$", "openapi"),
    (r".*\.tf$", "terraform"),
    (r"^\.github/workflows/.*\.ya?ml$", "ci-github"),
    (r"^\.gitlab-ci\.ya?ml$", "ci-gitlab"),
)

# Directories never walked at all: huge, uncommitted, and zero architectural
# signal. Skipped before recursion, so a large node_modules costs nothing.
#
# `vendor/` is deliberately NOT here. It is committed source that a user may
# legitimately ask about, so it is walked and classified VENDORED: present in
# the graph, excluded from architecture and the lockfile. Hard-excluding it
# while also defining a VENDORED role would have made that role unreachable.
DEFAULT_EXCLUDES: frozenset[str] = frozenset(
    {
        ".git",
        # The tool's own output directory. On an adopted repository it holds
        # the committed lockfile (and, on a dev machine, a previous artifact),
        # none of which is input: scanning our own output would make each run
        # a function of the previous one.
        ".svarupa",
        ".hg",
        ".svn",
        ".venv",
        "venv",
        "env",
        "node_modules",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        "dist",
        "build",
        "out",
        "target",
        ".next",
        ".nuxt",
        ".svelte-kit",
        ".terraform",
        ".gradle",
        ".idea",
        ".vscode",
        "site-packages",
        ".turbo",
        "coverage",
        ".coverage",
        "htmlcov",
        ".cache",
        ".parcel-cache",
    }
)

# "fixtures" is deliberately absent: it is a domain noun in sports, lighting,
# and data-processing software, and a legitimate src/fixtures/ module should
# not silently vanish from the architecture. It is caught anyway when nested
# under a real test directory.
_TEST_DIR_NAMES = frozenset({"test", "tests", "__tests__", "spec", "specs", "e2e", "testing"})
_TEST_FILE_RE = re.compile(
    r"(^test_|_test\.|\.test\.|\.spec\.|_spec\.|^conftest\.py$|Test\.java$|Tests\.java$)"
)
_GENERATED_RE = re.compile(
    r"(_pb2\.pyi?$|_pb2_grpc\.py$|\.pb\.go$|_generated\.|\.generated\.|"
    r"\.g\.dart$|_gen\.go$|\.designer\.cs$)"
)
_GENERATED_DIRS = frozenset({"__generated__", "generated", "gen", "migrations", "protogen"})
_VENDOR_DIRS = frozenset({"vendor", "third_party", "thirdparty", "external", "extern"})

# A generated file says so in a banner at the very top. Matching anywhere in
# the first 2KB produced verified false positives: a tooling script whose
# docstring merely *mentions* "DO NOT EDIT", or a cleanup script referring to
# "autogenerated artifacts", both vanished from architecture silently.
#
# So: only the first few lines, and only the strong conventions. `@generated`
# is the Meta/Buck marker, and Go's is a full-line "Code generated ... DO NOT
# EDIT." A bare "DO NOT EDIT" mid-file is no longer enough.
_GENERATED_BANNER = re.compile(
    rb"(@generated\b|@auto-generated\b"
    rb"|^[^\n]{0,20}Code generated .{0,60}DO NOT EDIT"
    rb"|^[^\n]{0,20}DO NOT EDIT[.!]?\s*$"
    rb"|^[^\n]{0,20}This file was automatically generated)",
    re.IGNORECASE | re.MULTILINE,
)
_BANNER_LINES = 5


class FileRole(str, Enum):
    SOURCE = "source"
    TEST = "test"
    GENERATED = "generated"
    VENDORED = "vendored"
    CONFIG = "config"
    # Under a top-level hidden directory: `.claude/skills/*/scripts`,
    # `.agent/`, `.cursor/`, `.github/workflows`. Tooling for the people and
    # agents who work on the repository, not the system it builds. On a
    # Next.js app nine of twelve top-level architecture boxes were agent
    # skill scripts and the app sat in the second row (review #22).
    TOOLING = "tooling"

    @property
    def in_architecture(self) -> bool:
        """Whether this role feeds architecture derivation and the lockfile.

        Test, generated, vendored and tooling files stay in the graph so a
        user can still ask about them, but they never shape the architecture
        picture.
        """
        return self in (FileRole.SOURCE, FileRole.CONFIG)


@dataclass(frozen=True, order=True, slots=True)
class FileRec:
    path: str
    role: FileRole
    lang: str | None
    config_kind: str | None
    size: int
    content_hash: str
    # Captured here because the bytes are already in hand for hashing. `build`
    # uses it to verify that every evidence range points at a line that
    # actually exists, which is the difference between a claim and a promise.
    line_count: int = 0

    @property
    def in_architecture(self) -> bool:
        return self.role.in_architecture


@dataclass(frozen=True, order=True, slots=True)
class Workspace:
    """A declared workspace member.

    This is the source of structural module identity, which is what the
    lockfile is keyed on. Communities are never used for identity because they
    are chaotically sensitive to input perturbation; a declared workspace
    member changes only when a human moves it.
    """

    kind: str
    root: str
    manifest: str


@dataclass(frozen=True, slots=True)
class ScanLimits:
    max_file_bytes: int = 2_000_000
    max_files: int = 200_000
    max_depth: int = 40


@dataclass(frozen=True, slots=True)
class Scan:
    root: Path
    files: tuple[FileRec, ...]
    workspaces: tuple[Workspace, ...]
    diagnostics: tuple[Diagnostic, ...] = ()
    skipped: tuple[tuple[str, int], ...] = ()

    def by_role(self, role: FileRole) -> tuple[FileRec, ...]:
        return tuple(f for f in self.files if f.role is role)

    @property
    def architecture_files(self) -> tuple[FileRec, ...]:
        return tuple(f for f in self.files if f.in_architecture)

    def languages(self) -> tuple[tuple[str, int], ...]:
        counts: dict[str, int] = {}
        for f in self.files:
            if f.lang:
                counts[f.lang] = counts.get(f.lang, 0) + 1
        return tuple(sorted(counts.items()))


# --------------------------------------------------------------------------
# Ignore handling
# --------------------------------------------------------------------------


class _Ignore:
    """gitignore-style matching, implemented in-process.

    Deliberately not ``git check-ignore``: shelling out would make the file set
    depend on whether git is installed and on its version, so the same commit
    could yield two different lockfiles on two machines.
    """

    __slots__ = ("_spec", "rejected")

    def __init__(self, patterns: Sequence[str]) -> None:
        # pathspec ships no type stubs, so the boundary is explicitly Any and
        # narrowed to bool at the single call site below.
        self._spec: Any = None
        self.rejected: list[tuple[str, str]] = []
        if not patterns:
            return

        # pathspec 1.x renamed "gitwildmatch" to "gitignore" and deprecated the
        # old name. Only KeyError/LookupError belong to the style fallback; a
        # pattern error is a property of the user's file, not of the pathspec
        # version.
        style = "gitwildmatch"
        for candidate in ("gitignore", "gitwildmatch"):
            try:
                pathspec.PathSpec.from_lines(candidate, ["ok"])  # pyright: ignore[reportUnknownMemberType]
                style = candidate
                break
            except (KeyError, LookupError):
                continue

        # Compile line by line. Compiling the whole list at once means one
        # malformed pattern (a trailing backslash, a bare "!") raises and
        # silently leaves _spec None, which disables ignore handling entirely
        # and lets ignored files -- possibly secrets -- into the graph with no
        # diagnostic. Dropping one bad line is recoverable; dropping all of
        # them is not.
        good: list[str] = []
        for pat in patterns:
            try:
                pathspec.PathSpec.from_lines(style, [pat])  # pyright: ignore[reportUnknownMemberType]
            except Exception as exc:  # pathspec error types vary by version
                self.rejected.append((pat, type(exc).__name__))
                continue
            good.append(pat)

        if good:
            self._spec = pathspec.PathSpec.from_lines(style, good)  # pyright: ignore[reportUnknownMemberType]

    def matches(self, rel: str, is_dir: bool) -> bool:
        if self._spec is None:
            return False
        probe = rel + "/" if is_dir else rel
        return bool(self._spec.match_file(probe))


def _read_ignore_files(root: Path) -> list[str]:
    patterns: list[str] = []
    for name in (".gitignore", ".svarupaignore"):
        p = root / name
        if not p.is_file():
            continue
        try:
            text = p.read_text(encoding="utf8", errors="replace")
        except OSError:
            continue
        patterns.extend(
            ln.strip() for ln in text.splitlines() if ln.strip() and not ln.startswith("#")
        )
    return patterns


# --------------------------------------------------------------------------
# Classification
# --------------------------------------------------------------------------


def _config_kind(rel: str, name: str) -> str | None:
    if (kind := CONFIG_NAMES.get(name)) is not None:
        return kind
    if _COMPOSE_VARIANT.match(name):
        return "compose"
    for pattern, kind in CONFIG_GLOBS:
        if re.match(pattern, rel) or re.match(pattern, name):
            return kind
    return None


def _line_count(data: bytes) -> int:
    """Lines in a file, floored at one.

    An empty file has no content, but it does have a line 1: that is where an
    editor puts the cursor, and it is the conventional way to point at a file
    rather than at something inside it. Module nodes are evidenced there, and
    an empty `__init__.py` is extremely common.
    """
    if not data:
        return 1
    return data.count(b"\n") + (0 if data.endswith(b"\n") else 1)


def _looks_generated(data: bytes) -> bool:
    """Check only the banner region: the first few lines.

    Takes the already-read bytes rather than re-opening the file. The previous
    version read 2KB here and then the whole file again for hashing, doubling
    I/O on every source file at the 200k-file ceiling.
    """
    head = b"\n".join(data.split(b"\n", _BANNER_LINES)[:_BANNER_LINES])
    return bool(_GENERATED_BANNER.search(head))


def classify(rel: str, data: bytes, lang: str | None, config_kind: str | None) -> FileRole:
    parts = rel.split("/")
    dirs, name = parts[:-1], parts[-1]

    # Only a hidden directory at the repository root: a dotfile at the root
    # (`.env`) and a hidden directory deeper down are not tooling by that rule.
    if dirs and dirs[0].startswith("."):
        return FileRole.TOOLING
    if any(d in _VENDOR_DIRS for d in dirs):
        return FileRole.VENDORED

    # SQL under migrations/ is an exception, and a deliberate one. Django and
    # Alembic migrations are committed, hand-reviewed, and on many projects
    # they are the *only* DDL in the repository. Design 4.3 names *.sql the ERD
    # source of truth, so classifying them GENERATED would make the ERD deriver
    # honestly return None for a codebase that has a complete schema.
    if lang == "sql" and any(d in _GENERATED_DIRS for d in dirs):
        return FileRole.CONFIG

    if any(d in _GENERATED_DIRS for d in dirs) or _GENERATED_RE.search(name):
        return FileRole.GENERATED
    if any(d in _TEST_DIR_NAMES for d in dirs) or _TEST_FILE_RE.search(name):
        return FileRole.TEST
    if config_kind is not None:
        return FileRole.CONFIG
    if lang is not None and _looks_generated(data):
        return FileRole.GENERATED
    return FileRole.SOURCE


# --------------------------------------------------------------------------
# Workspace detection
# --------------------------------------------------------------------------


def _toml_loader() -> Callable[[str], object] | None:
    """tomllib is stdlib from 3.11; tomli is the 3.10 backport."""
    for module in ("tomllib", "tomli"):
        try:
            mod = importlib.import_module(module)
        except ModuleNotFoundError:
            continue
        return cast("Callable[[str], object]", mod.loads)
    return None


def load_toml(text: str) -> dict[str, object] | None:
    loader = _toml_loader()
    if loader is None:
        return None
    try:
        loaded = loader(text)
    except Exception:  # a malformed manifest is the user's problem, not a crash
        return None
    return cast("dict[str, object]", loaded) if isinstance(loaded, dict) else None


def _dig(data: object, *keys: str) -> object:
    """Walk nested mappings without losing type information.

    Parsed manifests are `object` all the way down; this keeps the traversal
    honest under strict typing instead of scattering casts at each hop.
    """
    cur: object = data
    for key in keys:
        if not isinstance(cur, dict):
            return None
        cur = cast("dict[str, object]", cur).get(key)
    return cur


def _detect_workspaces(root: Path, files: Iterable[FileRec]) -> list[Workspace]:
    """Find declared workspace roots.

    Manifests are **parsed**, never substring-matched. Grepping produced
    verified false positives that would have become false module boundaries,
    and module identity is what the lockfile is keyed on:

    * ``[tool.poetry.group`` appears in essentially every modern Poetry
      project. Dependency groups are not workspaces; Poetry has no such
      feature.
    * ``[workspace]`` inside a Cargo.toml comment.
    * ``"workspaces"`` inside a package.json ``keywords`` array.

    This is the same lesson as "never parse JSONC with a regex" (design
    §15.1), applied to the manifests that decide structure.

    Only roots are detected here; member globs resolve in `build`, where the
    full file list is already indexed.
    """
    found: list[Workspace] = []
    paths = {f.path for f in files}

    def read(rel: str) -> str | None:
        try:
            return (root / rel).read_text(encoding="utf8", errors="replace")
        except OSError:
            return None

    for rel in sorted(paths):
        base = Path(rel).name
        parent = norm_path(str(Path(rel).parent))
        parent = "" if parent == "." else parent

        # Presence of the file *is* the declaration for these two.
        if base == "pnpm-workspace.yaml":
            found.append(Workspace("pnpm", parent, rel))
            continue
        if base == "go.work":
            found.append(Workspace("go", parent, rel))
            continue

        if base == "package.json":
            text = read(rel)
            if text is None:
                continue
            try:
                loaded: object = json.loads(text)
            except json.JSONDecodeError:
                continue
            # yarn classic/berry and npm all ride this key; recorded as "npm".
            if _dig(loaded, "workspaces"):
                found.append(Workspace("npm", parent, rel))

        elif base == "Cargo.toml":
            text = read(rel)
            cargo = load_toml(text) if text is not None else None
            if isinstance(_dig(cargo, "workspace"), dict):
                found.append(Workspace("cargo", parent, rel))

        elif base == "pyproject.toml":
            text = read(rel)
            proj = load_toml(text) if text is not None else None
            if isinstance(_dig(proj, "tool", "uv", "workspace"), dict):
                found.append(Workspace("uv", parent, rel))

    return sorted(set(found))


# --------------------------------------------------------------------------
# The walk
# --------------------------------------------------------------------------


def _empty_counts() -> dict[str, int]:
    return {}


@dataclass
class _Counter:
    counts: dict[str, int] = field(default_factory=_empty_counts)

    def hit(self, reason: str) -> None:
        self.counts[reason] = self.counts.get(reason, 0) + 1

    def frozen(self) -> tuple[tuple[str, int], ...]:
        return tuple(sorted(self.counts.items()))


def detect(root: str | Path, limits: ScanLimits | None = None) -> Scan:
    """Walk `root` and classify every file.

    Deterministic: entries are sorted by codepoint before recursion, so
    filesystem dirent order never leaks into the result.
    """
    limits = limits or ScanLimits()
    root_path = Path(root).resolve()
    if not root_path.is_dir():
        # Refuse, do not degrade. "Partial failure must degrade" is about one
        # input of many; here there is no input at all, and every downstream
        # stage would then honestly report nothing. Measured before fixing:
        # `svarupa /no/such/place` exited **0** and wrote a complete artifact
        # describing an empty repository, which is indistinguishable from a
        # real repository containing no code.
        raise DiagnosticError(
            Diagnostic(
                code="SVA-D-007",
                severity=Severity.ERROR,
                message=(
                    "is not a directory"
                    if root_path.exists()
                    else "does not exist, so there is nothing to scan"
                ),
                subject=str(root_path),
                suggested_fixes=("Check the path, or run from inside the repository.",),
            )
        )
    real_root = os.path.realpath(root_path)
    ignore = _Ignore(_read_ignore_files(root_path))

    files: list[FileRec] = []
    diags: list[Diagnostic] = []
    skipped = _Counter()

    for pat, err in ignore.rejected:
        diags.append(
            Diagnostic(
                code="SVA-D-006",
                severity=Severity.WARNING,
                message=(
                    f"ignore pattern could not be compiled ({err}); this line is "
                    "not being applied. Other patterns still are."
                ),
                subject=pat,
                location=".gitignore / .svarupaignore",
            )
        )

    def walk(directory: Path, rel_dir: str, depth: int) -> None:
        if depth > limits.max_depth:
            skipped.hit("max-depth")
            diags.append(
                Diagnostic(
                    code="SVA-D-004",
                    severity=Severity.WARNING,
                    message=f"directory nesting exceeded {limits.max_depth}; not descending",
                    subject=rel_dir or ".",
                )
            )
            return
        try:
            # Normalize BEFORE sorting, not after. `os.scandir` may return NFD
            # on one machine and NFC on another for the same logical name, and
            # those sort differently ("café" vs "cafz" flips). Visit order then
            # differs, and since `max_files` truncation keeps whichever files
            # were reached first, two machines would retain different sets.
            entries = sorted(
                ((unicodedata.normalize("NFC", e.name), e) for e in os.scandir(directory)),
                key=lambda pair: pair[0],
            )
        except OSError as exc:
            skipped.hit("unreadable-dir")
            diags.append(
                Diagnostic(
                    code="SVA-D-005",
                    severity=Severity.WARNING,
                    message=f"could not read directory: {exc.strerror}",
                    subject=rel_dir or ".",
                )
            )
            return

        # Collision detection runs per sibling set on RAW component names.
        # Whole-path checking cannot see it: two colliding sibling directories
        # with differently-named children never share a path key. And the
        # parent components must not be normalized first, or the pre-images
        # that make the collision visible are already gone.
        for diag in collision_check([e.name for _, e in entries]):
            diags.append(
                Diagnostic(
                    code=diag.code,
                    severity=diag.severity,
                    message=diag.message,
                    subject=diag.subject,
                    location=rel_dir or ".",
                    suggested_fixes=diag.suggested_fixes,
                )
            )

        for name, entry in entries:
            rel = f"{rel_dir}/{name}" if rel_dir else name

            if entry.is_symlink():
                # Never followed. A symlink into the tree would double-count
                # files; one pointing outside would pull in code that is not
                # part of this repository at all.
                target = os.path.realpath(entry.path)
                if not (target == real_root or target.startswith(real_root + os.sep)):
                    diags.append(
                        Diagnostic(
                            code="SVA-D-001",
                            severity=Severity.WARNING,
                            message="symlink points outside the repository; not followed",
                            subject=rel,
                            location=target,
                        )
                    )
                skipped.hit("symlink")
                continue

            try:
                is_dir = entry.is_dir()
            except OSError:
                skipped.hit("unreadable-entry")
                continue

            if is_dir:
                if name in DEFAULT_EXCLUDES:
                    skipped.hit(f"excluded-dir:{name}")
                    continue
                if ignore.matches(rel, is_dir=True):
                    skipped.hit("ignored")
                    continue
                walk(Path(entry.path), rel, depth + 1)
                continue

            if ignore.matches(rel, is_dir=False):
                skipped.hit("ignored")
                continue

            lang = LANG_BY_EXT.get(Path(name).suffix)
            config_kind = _config_kind(rel, name)
            if lang is None and config_kind is None:
                skipped.hit("unrecognized-type")
                continue

            try:
                size = entry.stat(follow_symlinks=False).st_size
            except OSError:
                skipped.hit("unreadable-entry")
                continue
            if size > limits.max_file_bytes:
                skipped.hit("too-large")
                diags.append(
                    Diagnostic(
                        code="SVA-D-002",
                        severity=Severity.INFO,
                        message=(
                            f"file is {size} bytes, over the "
                            f"{limits.max_file_bytes} limit; skipped"
                        ),
                        subject=rel,
                    )
                )
                continue

            if len(files) >= limits.max_files:
                skipped.hit("max-files")
                continue

            # Read once: hash, size, and the generated-banner sniff all come
            # from the same buffer. Taking size from the hashed bytes instead
            # of the earlier stat() also closes the window where a file
            # mutating mid-scan would record an inconsistent size and hash.
            try:
                data = Path(entry.path).read_bytes()
            except OSError:
                skipped.hit("unreadable-entry")
                continue

            files.append(
                FileRec(
                    path=rel,
                    role=classify(rel, data, lang, config_kind),
                    lang=lang,
                    config_kind=config_kind,
                    size=len(data),
                    content_hash=hashlib.sha256(data).hexdigest(),
                    line_count=_line_count(data),
                )
            )

    walk(root_path, "", 0)

    # Ground truth, not a restatement of the pre-append condition: a repo with
    # exactly max_files files is a COMPLETE scan and must not be reported as
    # partial. A tool whose pitch is verified claims cannot make a false claim
    # about its own scan.
    if "max-files" in skipped.counts:
        diags.append(
            Diagnostic(
                code="SVA-D-003",
                severity=Severity.WARNING,
                message=(
                    f"hit the {limits.max_files} file limit; the scan is incomplete "
                    "and results will be partial"
                ),
                subject=str(root_path),
                suggested_fixes=("Narrow the scan with .svarupaignore, or raise max_files.",),
            )
        )

    ordered = tuple(sorted(files))
    if not ordered and not any(p.name != ".git" for p in root_path.iterdir()):
        # An empty directory scanned to "0 files", exit 0, and a 51 KB artifact
        # whose only tab was "not drawn" (review #20 C6): a typo in the path
        # that lands on an empty directory read as success. No input at all is
        # the refusal case, as with a missing root. A directory that holds
        # files, none of them source (a README, only docs), is a real
        # repository and still gets its artifact and its named absences.
        raise DiagnosticError(
            Diagnostic(
                code="SVA-D-008",
                severity=Severity.ERROR,
                message="is empty, so there is nothing to analyze",
                subject=str(root_path),
                suggested_fixes=(
                    "Check the path: an empty directory is usually a typo or an unfinished checkout.",
                ),
            )
        )
    return Scan(
        root=root_path,
        files=ordered,
        workspaces=tuple(_detect_workspaces(root_path, ordered)),
        diagnostics=tuple(diags),
        skipped=skipped.frozen(),
    )
