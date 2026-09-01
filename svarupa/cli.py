"""Command line entry point."""

from __future__ import annotations

import argparse
import sys

from svarupa import __version__
from svarupa.build import build
from svarupa.cluster import cluster
from svarupa.derive import derive_all
from svarupa.detect import FileRole, ScanLimits, detect
from svarupa.extract import declared_dependencies, extract


def _scan(path: str, max_files: int) -> int:
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

    print()
    print("Next: layout and the viewer (P1-6) are not implemented yet.")
    return 1 if errors or graph_errors else 0


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
    args = parser.parse_args(argv)
    return _scan(args.path, args.max_files)


if __name__ == "__main__":
    sys.exit(main())
