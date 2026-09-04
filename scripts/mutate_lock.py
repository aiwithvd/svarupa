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
SUITE = [
    "tests/test_lock.py",
    "tests/test_determinism.py",
    "tests/test_lock_grammar.py",
    "tests/test_diagnostics.py",
]

MUTATIONS: list[tuple[str, str, str, str]] = [
    (
        "the lockfile records file paths instead of modules",
        "svarupa/lock/build.py",
        "for m in sorted(code_modules(graph))",
        "for m in sorted(graph.nodes)",
    ),
    (
        "dependencies are dropped from the lockfile",
        "svarupa/lock/build.py",
        "for src, dst in sorted(graph.module_deps)",
        "for src, dst in sorted(set())",
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
        "diagnostics = list(collision_check(sorted(code_modules(graph))))",
        "diagnostics = []",
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
        "    delta = diff(committed_base, regenerated_base)\n    if delta.empty:",
        "    delta = diff(committed_base, regenerated_base)\n    if True:",
    ),
    (
        "drift is reported even when the base is current",
        "svarupa/lock/diff.py",
        "    delta = diff(committed_base, regenerated_base)\n    if delta.empty:",
        "    delta = diff(committed_base, regenerated_base)\n    if False:",
    ),
    (
        "the drift warning predicts what the delta will contain",
        "svarupa/lock/diff.py",
        'f"{len(delta.removed)} fact(s) it still claims"',
        'f"{len(delta.removed)} fact(s) it still claims, which will appear in the delta below as though this change caused them"',
    ),
    (
        "the delta renders additions before removals",
        "svarupa/lock/diff.py",
        '            out.extend(f"  - {r.render()}" for r in removed)\n            out.extend(f"  + {r.render()}" for r in added)',
        '            out.extend(f"  + {r.render()}" for r in added)\n            out.extend(f"  - {r.render()}" for r in removed)',
    ),
    (
        "a grammar version change is invisible in the delta",
        "svarupa/lock/diff.py",
        "    if moved:",
        "    if False:",
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
        "    if not (write_lock or bases):\n        return False",
        "    write_lock = True\n    if False:\n        return False",
    ),
    (
        "the schema stamp is not checked at load time",
        "svarupa/cli.py",
        "    if not lockfile.header.stamped:",
        "    if False:",
    ),
    (
        "a lockfile is written even when its own build errored",
        "svarupa/cli.py",
        "    if write_lock and failed:",
        "    if False:",
    ),
    (
        "the grammar header counts every scanned file again",
        "svarupa/lock/build.py",
        'if node.lang and nid.split("#", 1)[0] in graph.architecture_paths',
        "if node.lang",
    ),
    (
        "the repository root is spelled as an empty field again",
        "svarupa/lock/build.py",
        'ROOT_MODULE = "."',
        'ROOT_MODULE = ""',
    ),
    (
        "config-only directories are modules again",
        "svarupa/lock/build.py",
        "    return out & set(graph.modules)",
        "    return set(graph.modules)",
    ),
    (
        "an empty lockfile is written without saying so",
        "svarupa/lock/build.py",
        "    if not records:",
        "    if False:",
    ),
    (
        "a dangling dependency reference is not detected",
        "svarupa/lock/build.py",
        "    if dangling:",
        "    if False:",
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
