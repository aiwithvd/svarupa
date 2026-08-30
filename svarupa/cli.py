"""Command line entry point.

Stub for P1-0. The `svarupa` console script is declared in pyproject, so it
must at least run: a console script that dies with ModuleNotFoundError is the
worst possible first impression, and nothing in CI would have caught it,
because the tests never invoke the script.
"""

from __future__ import annotations

import argparse
import sys

from svarupa import __version__


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="svarupa",
        description=(
            "Verified architecture diagrams and a queryable knowledge graph, "
            "derived from your codebase."
        ),
    )
    parser.add_argument("--version", action="version", version=f"svarupa {__version__}")
    parser.add_argument("path", nargs="?", default=".", help="repository root to analyze")
    args = parser.parse_args(argv)

    print(f"svarupa {__version__}")
    print(f"target: {args.path}")
    print()
    print("Analysis is not implemented yet. Phase 1 in progress:")
    print("  [x] P1-0  foundations, core model, lockfile grammar")
    print("  [ ] P1-1  detect     file classification, exclusions, workspaces")
    print("  [ ] P1-2  extract    Python and TypeScript AST, config parsers")
    print("  [ ] P1-3  build      graph assembly, structural module identity")
    print("  [ ] P1-4  cluster    presentation-only grouping")
    print("  [ ] P1-5  derive     architecture, module deps, ERD")
    print("  [ ] P1-6  layout     coordinates, geometry validation, viewer")
    print("  [ ] P1-7  lock       serializer and diff engine")
    return 0


if __name__ == "__main__":
    sys.exit(main())
