"""Mutation check for the lockfile stage.

Review #9's sharpest finding was that a mutation suite proves what it
enumerates, and that a list written by the author of the code inherits the
author's blind spots. So this list is written against the *claims the stage
makes*, taken from its docstrings and from the design, rather than against the
lines that felt interesting to write:

* the lockfile carries facts, not line numbers;
* community identity never reaches it;
* a refactor inside a module produces no diff;
* a real cross-module change produces exactly one line;
* an unknown record kind diffs opaquely instead of being dropped;
* a major schema difference refuses;
* a stale base is reported before the delta that it misattributes;
* collisions diagnose rather than merge;
* `emit` never deletes the committed lockfile.

Each entry disables exactly one of those. A claim with no failing test is a
claim nobody is checking.
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
PY = ROOT / ".venv" / "bin" / "python"
SUITE = ["tests/test_lock.py", "tests/test_determinism.py", "tests/test_lock_grammar.py"]

MUTATIONS: list[tuple[str, str, str, str]] = [
    (
        "the lockfile records file paths instead of modules",
        "svarupa/lock/build.py",
        "    records: list[Record] = [module_record(m) for m in sorted(graph.modules)]",
        "    records: list[Record] = [module_record(m) for m in sorted(graph.nodes)]",
    ),
    (
        "dependencies are dropped from the lockfile",
        "svarupa/lock/build.py",
        "    records.extend(dep_record(src, dst) for src, dst in sorted(graph.module_deps))",
        "    records.extend(dep_record(src, dst) for src, dst in sorted(set()))",
    ),
    (
        "the header stamps every grammar the tool ships, used or not",
        "svarupa/lock/build.py",
        "if lang in langs",
        "if True",
    ),
    (
        "collision detection is not run when building the lockfile",
        "svarupa/lock/build.py",
        "    diagnostics = tuple(collision_check(sorted(graph.modules)))",
        "    diagnostics = ()",
    ),
    (
        "the diff reports additions but never removals",
        "svarupa/lock/diff.py",
        "    removed = tuple(sorted(base_set - head_set))",
        "    removed = ()",
    ),
    (
        "the diff ignores the schema boundary",
        "svarupa/lock/diff.py",
        "    base.assert_diffable(head)",
        "    pass",
    ),
    (
        "unknown record kinds are not reported as unknown",
        "svarupa/lock/diff.py",
        "    unknown = (base.unknown_kinds() | head.unknown_kinds()) & {",
        "    unknown = set() & {",
    ),
    (
        "drift is detected but never reported",
        "svarupa/lock/diff.py",
        "    if delta.empty:\n        return ()",
        "    if True:\n        return ()",
    ),
    (
        "drift is reported even when the base is current",
        "svarupa/lock/diff.py",
        "    if delta.empty:\n        return ()",
        "    if False:\n        return ()",
    ),
    (
        "the delta renders additions before removals",
        "svarupa/lock/diff.py",
        '            out.extend(f"  - {r.render()}" for r in removed)\n'
        '            out.extend(f"  + {r.render()}" for r in added)',
        '            out.extend(f"  + {r.render()}" for r in added)\n'
        '            out.extend(f"  - {r.render()}" for r in removed)',
    ),
    (
        "the CLI shows the delta before the drift warning",
        "svarupa/cli.py",
        "            if drift:\n                # Read the delta against the code",
        "            if False:\n                # Read the delta against the code",
    ),
    (
        "the committed lockfile is cleared with the rest of the artifact",
        "svarupa/emit/__init__.py",
        'OWNED_FILES = ("index.html", "REPORT.md", "graph.json", MARKER)',
        'OWNED_FILES = ("index.html", "REPORT.md", "graph.json", MARKER, "architecture.lock")',
    ),
    (
        "a lockfile is written even when nobody asked for one",
        "svarupa/cli.py",
        "    if not (write_lock or diff_base or drift_base):\n        return False",
        "    write_lock = True\n    if False:\n        return False",
    ),
]


def main() -> int:
    failures: list[str] = []
    backup = pathlib.Path(tempfile.mkdtemp()) / "src"
    shutil.copytree(ROOT / "svarupa", backup)
    try:
        for name, rel, old, new in MUTATIONS:
            path = ROOT / rel
            text = path.read_text(encoding="utf8")
            if old not in text:
                print(f"SKIP (pattern not found)  {name}")
                failures.append(f"{name}: pattern not found")
                continue
            path.write_text(text.replace(old, new, 1), encoding="utf8")
            proc = subprocess.run(
                [str(PY), "-m", "pytest", *SUITE, "-q", "-x"],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            shutil.rmtree(ROOT / "svarupa")
            shutil.copytree(backup, ROOT / "svarupa")
            if proc.returncode == 0:
                print(f"SURVIVED  {name}")
                failures.append(f"{name}: no test failed")
            else:
                caught = [
                    ln.split("::")[-1]
                    for ln in proc.stdout.splitlines()
                    if ln.startswith("FAILED")
                ]
                print(f"caught    {name}  ->  {caught[0] if caught else 'error'}")
    finally:
        if not (ROOT / "svarupa").exists():  # pragma: no cover - safety net
            shutil.copytree(backup, ROOT / "svarupa")
    if failures:
        print("\nnot caught:")
        for f in failures:
            print(f"  {f}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
