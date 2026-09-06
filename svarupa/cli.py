"""Command line entry point."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from svarupa import __version__
from svarupa.build import Graph, build
from svarupa.cluster import cluster
from svarupa.derive import derive_all
from svarupa.detect import FileRole, ScanLimits, detect
from svarupa.diagnostics import Diagnostic, DiagnosticError, Severity
from svarupa.emit import OUTPUT_DIR, claim, emit
from svarupa.extract import declared_dependencies, extract
from svarupa.lock import (
    LOCK_NAME,
    SCHEMA_MAJOR,
    Lockfile,
    SchemaMismatch,
    build_lock,
    diff,
    drift_check,
)
from svarupa.setup import TARGETS, install


def _scan(
    path: str,
    max_files: int,
    out: str | None,
    write_lock: bool,
    diff_base: str | None,
    drift_base: str | None,
) -> int:
    # Validate everything that can be validated before the expensive work.
    # Scanning a large repository takes minutes, and a refusal that arrives
    # afterwards has already wasted that time under a page of output that read
    # as success. The output directory was moved here last wave; the lockfile
    # arguments belong here for exactly the same reason, and were not.
    if out is not None:
        claim(Path(out))
    elif Path(path).is_dir():
        # The default output directory gets the same up-front claim `--out`
        # already had. Without this, a refusal about the output directory
        # arrived from inside `emit`, after the full scan. The `is_dir` guard
        # keeps a nonexistent root as `detect`'s refusal (SVA-D-007) rather
        # than creating `<typo>/.svarupa` on the way to it.
        claim(Path(path) / OUTPUT_DIR)
    bases = {
        name: _read_lock(value, name)
        for name, value in (("--diff", diff_base), ("--drift-base", drift_base))
        if value is not None
    }

    scan = detect(path, ScanLimits(max_files=max_files))

    counts: dict[FileRole, int] = {}
    for f in scan.files:
        counts[f.role] = counts.get(f.role, 0) + 1

    print(f"{scan.root}")
    print(f"  {len(scan.files)} files, {len(scan.architecture_files)} in architecture")
    print()
    for role in FileRole:
        n = counts.get(role, 0)
        if not n:
            continue
        mark = " " if role.in_architecture else "-"
        print(f"  {mark} {role.value:<10} {n:>6}")
    print()

    langs = scan.languages()
    if langs:
        print("  languages: " + ", ".join(f"{k} {v}" for k, v in langs))
    if scan.workspaces:
        print(
            "  workspaces: " + ", ".join(f"{w.kind}@{w.root or '.'}" for w in scan.workspaces)
        )

    errors = [d for d in scan.diagnostics if d.severity.value == "ERROR"]
    warnings = [d for d in scan.diagnostics if d.severity.value == "WARNING"]
    if scan.diagnostics:
        print()
        print(f"  {len(errors)} error(s), {len(warnings)} warning(s)")
        for d in scan.diagnostics[:10]:
            print("   " + d.render().replace("\n", "\n   "))
        if len(scan.diagnostics) > 10:
            print(f"    ... and {len(scan.diagnostics) - 10} more")

    print()
    graph = build(scan, extract(scan, declared_dependencies(scan)), strict=False)
    print(
        f"  graph: {len(graph.nodes)} nodes, {len(graph.edges)} edges, "
        f"{len(graph.modules)} modules, {len(graph.module_deps)} module deps"
    )
    print()
    print(graph.scorecard.render())
    for (lang, kind), samples in sorted(graph.scorecard.samples.items()):
        if samples:
            print(f"  {lang}/{kind} unresolved e.g. " + ", ".join(samples[:5]))

    graph_errors = graph.errors
    if graph_errors:
        print()
        print(f"  {len(graph_errors)} graph integrity error(s)")
        for d in graph_errors[:5]:
            print("   " + d.render())

    clustering = cluster(graph)
    print()
    print(
        f"  grouping: {len(clustering.communities)} communities "
        f"(presentation only; never reaches the lockfile)"
    )
    for c in sorted(clustering.communities, key=lambda c: -c.size)[:8]:
        head = ", ".join(c.members[:3]) + ("..." if c.size > 3 else "")
        print(f"    {c.label:<22} n={c.size:<3} cohesion={c.cohesion:.2f}  {head}")

    produced, notes = derive_all(graph, clustering)
    print()
    print(f"  diagrams: {len(produced)}")
    for kind, ds in sorted(produced.items(), key=lambda kv: kv[0].value):
        root = ds.root_spec
        print(
            f"    {kind.value:<14} {len(root.nodes):>3} boxes  {len(root.edges):>3} edges  "
            f"depth {ds.depth()}  ({ds.total_nodes()} nodes across {len(ds.specs)} views)"
        )
        for n in root.nodes[:5]:
            drill = "  >" if n.is_drillable else "   "
            print(f"      {drill} {n.label:<20} {n.evidence[0]}")
        if len(root.nodes) > 5:
            print(f"        ... and {len(root.nodes) - 5} more")
    for note in notes:
        print(f"    - {note}")

    artifact = emit(Path(scan.root), graph, produced, notes, out_dir=Path(out) if out else None)
    print()
    print(f"  wrote {artifact.directory.name}/")
    for name, size in artifact.files:
        print(f"    {name:<28} {size:>9,} bytes")

    print()
    print("  layout:")
    for kind in sorted(produced, key=lambda k: k.value):
        result = artifact.laid_out[kind]
        root_canvas = result.canvases.get(produced[kind].root)
        size = f"{root_canvas.width}x{root_canvas.height}" if root_canvas else "root withheld"
        print(
            f"    {kind.value:<14} {result.engine_name:<10} {len(result.canvases):>3} drawn  "
            f"{len(result.withheld):>2} withheld   root {size}"
        )
    for d in artifact.diagnostics[:5]:
        print("      " + d.render())
    if len(artifact.diagnostics) > 5:
        print(f"      ... and {len(artifact.diagnostics) - 5} more")

    print()
    print(f"  open {artifact.directory / 'index.html'}")

    lock_failed = _lockfile(artifact.directory, graph, write_lock, bases, drift_base)
    return 1 if errors or graph_errors or not artifact.ok or lock_failed else 0


def _read_lock(path: str, flag: str) -> Lockfile:
    """Load a lockfile named on the command line, refusing rather than crashing.

    The grammar was hardened on the premise that malformed input is the
    expected case for a committed file. That premise covers the bytes and
    stopped at the channel: a missing path, a directory, or a file that is not
    UTF-8 each reached the user as a raw traceback, and so did the tool's own
    designed refusal for a mismatched schema. The loading channel is part of
    the parser's boundary, and every failure it can produce has to arrive as
    the same structured refusal a malformed line does.
    """
    try:
        text = Path(path).read_text(encoding="utf8")
    except (OSError, UnicodeDecodeError) as exc:
        raise DiagnosticError(
            Diagnostic(
                code="SVA-L-008",
                severity=Severity.ERROR,
                message=f"could not be read as a lockfile for {flag} ({type(exc).__name__}: {exc})",
                subject=path,
                suggested_fixes=(
                    f"Check the path given to {flag}.",
                    "Generate one with: svarupa <repo> --lock",
                ),
            )
        ) from exc

    lockfile = Lockfile.parse(text)
    # Check the stamp here, not at diff time. `parse` accepts an unstamped file
    # and `assert_diffable` refuses it, which is the right place semantically
    # and the wrong place in time: the refusal then arrives after the whole
    # scan. Everything knowable about an input before the expensive work should
    # be known before it.
    if not lockfile.header.stamped:
        raise SchemaMismatch(
            f"carries no '# schema' stamp, so its format cannot be established. "
            f"A missing stamp is less trustworthy than a mismatched one: refusing "
            f"rather than assuming the current schema (given to {flag})",
            subject=path,
        )
    if lockfile.header.schema_major != SCHEMA_MAJOR:
        raise SchemaMismatch(
            f"is schema {lockfile.header.schema_major}.x and this build writes "
            f"{SCHEMA_MAJOR}.x; a field's meaning may differ, so a delta would be "
            f"nonsense (given to {flag})",
            subject=path,
        )
    return lockfile


def _lockfile(
    directory: Path,
    graph: Graph,
    write_lock: bool,
    bases: dict[str, Lockfile],
    drift_base: str | None,
) -> bool:
    """Build, optionally write, and optionally diff the architecture lockfile.

    Returns whether anything here should fail the run. A collision is an error
    because it would make a committed file silently wrong; drift is a warning
    because the delta is still worth reading, it just has to be read
    differently.
    """
    if not (write_lock or bases):
        return False

    result = build_lock(graph, __version__)
    print()
    print(f"  lockfile: {len(result.lockfile.records)} record(s)")
    for d in result.diagnostics:
        print("    " + d.render().replace("\n", "\n    "))

    failed = any(d.severity is Severity.ERROR for d in result.diagnostics)

    if write_lock and failed:
        # Refuse to write a file whose own build reported an error. It was
        # written first and the error only set the exit code, so a user who
        # does not check exit codes had a known-wrong lockfile on disk, ready
        # to commit as the base every future diff is measured against.
        print("    not written: the errors above would make it silently wrong")
    elif write_lock:
        # Written next to the artifact but deliberately not part of it: this
        # is the one file in that directory meant to be committed, so `emit`
        # must never list it among the names it clears.
        path = directory / LOCK_NAME
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(result.lockfile.render(), encoding="utf8", newline="")
        print(f"    wrote {path}")

    if drift_base and "--diff" not in bases:
        print("    --drift-base needs --diff; nothing to compare drift against")
        return True

    if "--diff" in bases:
        base = bases["--diff"]
        if drift_base:
            regenerated = bases["--drift-base"]
            drift = drift_check(base, regenerated)
            for d in drift:
                print()
                print("  " + d.render().replace("\n", "\n  "))
            if drift:
                # Read the delta against the code, not against the stale file:
                # reporting from a stale base attributes other people's changes
                # to this one.
                #
                # This layer says so, because this layer decided it. The
                # diagnostic above reports only what it detected; it cannot
                # know what its caller will do, and a message that guesses is
                # false the moment the caller changes.
                base = regenerated
                print(
                    "    The delta below is taken against the regenerated base, so it "
                    "excludes those differences and shows this change alone."
                )
        else:
            print(
                "    No --drift-base given, so the delta below is against the committed "
                "base as-is. If that file is stale, changes made by other commits will "
                "appear here as though this change caused them."
            )
        delta = diff(base, result.lockfile)
        print()
        print("  " + delta.render().replace("\n", "\n  "))
        for d in delta.diagnostics:
            print("  " + d.render())
    return failed


def _setup(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="svarupa setup",
        description="Install an integration into a repository.",
    )
    parser.add_argument(
        "target",
        choices=sorted(TARGETS),
        help="; ".join(f"{name}: {cls.summary}" for name, cls in sorted(TARGETS.items())),
    )
    parser.add_argument(
        "--dest",
        default=".",
        help="repository root to install into (default: current directory)",
    )
    parser.add_argument(
        "--force", action="store_true", help="overwrite an existing file that differs"
    )
    args = parser.parse_args(argv)
    target = TARGETS[args.target]()
    result = install(target, Path(args.dest), args.force)
    for path in result.written:
        print(f"  wrote     {path}")
    for path in result.unchanged:
        print(f"  unchanged {path}")
    print()
    for step in target.next_steps():
        print(f"  next: {step}")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    parser = argparse.ArgumentParser(
        prog="svarupa",
        description=(
            "Verified architecture diagrams and a queryable knowledge graph, "
            "derived from your codebase. Every box points at a line of code."
        ),
        epilog=(
            'Also: "svarupa setup skill" and "svarupa setup ci_github" install '
            "integration files (see: svarupa setup --help). To analyze a directory "
            'literally named "setup", pass "./setup".'
        ),
    )
    parser.add_argument("--version", action="version", version=f"svarupa {__version__}")
    parser.add_argument("path", nargs="?", default=".", help="repository root to analyze")
    parser.add_argument(
        "--max-files", type=int, default=200_000, help="stop scanning after this many files"
    )
    parser.add_argument(
        "--lock",
        action="store_true",
        help=f"write the committed architecture lockfile ({LOCK_NAME})",
    )
    parser.add_argument(
        "--diff",
        metavar="BASE_LOCK",
        default=None,
        help="print the architecture delta against a base lockfile",
    )
    parser.add_argument(
        "--drift-base",
        metavar="REGENERATED_BASE_LOCK",
        default=None,
        help=(
            "the base lockfile regenerated from base-branch code. With --diff, "
            "reports whether the committed base is stale before showing the delta, "
            "because a stale base attributes other commits' changes to this one"
        ),
    )
    parser.add_argument(
        "--out",
        default=None,
        help=(
            "write the artifact here instead of <repo>/.svarupa. "
            "Use this to analyze a repository without writing into it."
        ),
    )
    try:
        # Dispatched on the literal first argument rather than via subparsers,
        # so `svarupa <path>` keeps working with no subcommand. The cost is
        # that a repository named `setup` needs `./setup`, which the epilog
        # says out loud.
        if argv[:1] == ["setup"]:
            return _setup(argv[1:])
        args = parser.parse_args(argv)
        return _scan(args.path, args.max_files, args.out, args.lock, args.diff, args.drift_base)
    except DiagnosticError as exc:
        # A structured refusal, printed as one. A traceback here would tell a
        # user about our call stack instead of about their input.
        #
        # Exit 1, the same code a completed-but-failed run uses. It was 2,
        # which argparse reserves for usage errors, so a refusal was
        # indistinguishable by exit code from a mistyped flag while also
        # differing from the tool's own failure code. Exit codes are a machine
        # contract and CI is the consumer: one code means "the tool did not
        # give you a usable answer", and 2 stays argparse's.
        print(exc.diagnostic.render(), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
