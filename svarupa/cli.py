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
from svarupa.diagnostics import DiagnosticError, Severity
from svarupa.emit import claim, emit
from svarupa.extract import declared_dependencies, extract
from svarupa.lock import LOCK_NAME, Lockfile, build_lock, diff, drift_check


def _scan(
    path: str,
    max_files: int,
    out: str | None,
    write_lock: bool,
    diff_base: str | None,
    drift_base: str | None,
) -> int:
    # Claim the output directory first. Scanning a large repository takes
    # minutes, and discovering afterwards that the target is unwritable means
    # the refusal arrives under a page of output that read as success.
    if out is not None:
        claim(Path(out))

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

    lock_failed = _lockfile(artifact.directory, graph, write_lock, diff_base, drift_base)
    return 1 if errors or graph_errors or not artifact.ok or lock_failed else 0


def _lockfile(
    directory: Path,
    graph: Graph,
    write_lock: bool,
    diff_base: str | None,
    drift_base: str | None,
) -> bool:
    """Build, optionally write, and optionally diff the architecture lockfile.

    Returns whether anything here should fail the run. A collision is an error
    because it would make a committed file silently wrong; drift is a warning
    because the delta is still worth reading, it just has to be read
    differently.
    """
    if not (write_lock or diff_base or drift_base):
        return False

    result = build_lock(graph, __version__)
    print()
    print(f"  lockfile: {len(result.lockfile.records)} record(s)")
    for d in result.diagnostics:
        print("    " + d.render().replace("\n", "\n    "))

    if write_lock:
        # Written next to the artifact but deliberately not part of it: this
        # is the one file in that directory meant to be committed, so `emit`
        # must never list it among the names it clears.
        path = directory / LOCK_NAME
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(result.lockfile.render(), encoding="utf8", newline="")
        print(f"    wrote {path}")

    failed = any(d.severity is Severity.ERROR for d in result.diagnostics)

    if drift_base and not diff_base:
        print("    --drift-base needs --diff; nothing to compare drift against")
        return True

    if diff_base:
        base = Lockfile.parse(Path(diff_base).read_text(encoding="utf8"))
        if drift_base:
            regenerated = Lockfile.parse(Path(drift_base).read_text(encoding="utf8"))
            drift = drift_check(base, regenerated)
            for d in drift:
                print()
                print("  " + d.render().replace("\n", "\n  "))
            if drift:
                # Read the delta against the code, not against the stale file.
                # Reporting the delta from a stale base would attribute other
                # people's changes to this one.
                base = regenerated
        delta = diff(base, result.lockfile)
        print()
        print("  " + delta.render().replace("\n", "\n  "))
        for d in delta.diagnostics:
            print("  " + d.render())
    return failed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="svarupa",
        description=(
            "Verified architecture diagrams and a queryable knowledge graph, "
            "derived from your codebase. Every box points at a line of code."
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
    args = parser.parse_args(argv)
    try:
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
