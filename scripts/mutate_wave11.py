"""Mutation check for the review #11 surface.

Review #11's demonstration was blunt: deleting the entire barycentric sweep
passed all 539 tests, and so did breaking the flow engine's routing branch,
because the mutation lists stopped growing while the codebase grew by 2,500
lines. This list covers the claims those waves made, plus every fix review #11
forced, so each has a test that reds when the guarantee is removed.
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
    "tests/test_layout.py",
    "tests/test_compose.py",
    "tests/test_lock.py",
    "tests/test_emit.py",
]

MUTATIONS: list[tuple[str, str, str, str]] = [
    (
        "the barycentric sweep is deleted",
        "svarupa/layout/sugiyama.py",
        "    for _ in range(SWEEPS):",
        "    for _ in range(0):",
    ),
    (
        "long edges get no waypoints",
        "svarupa/layout/sugiyama.py",
        "        if b - a <= 1:",
        "        if True:",
    ),
    (
        "reversed edges stop being flipped back",
        "svarupa/layout/engines.py",
        "        if c.reversed_:\n            points.reverse()",
        "        if False:\n            points.reverse()",
    ),
    (
        "flow routes skips straight across instead of the corridor",
        "svarupa/layout/engines.py",
        "        if not takes_corridor(src, dst):",
        "        if True:",
    ),
    (
        "flow tracks budget against the whole diagram again",
        "svarupa/layout/engines.py",
        "            tx = gx0 + 8 + (used * span) // max(1, gap_budget.get(la, 1))",
        "            tx = gx0 + 8 + (used * span) // max(1, len(edge_map) * 4)",
    ),
    (
        "a backward flow edge enters from the left",
        "svarupa/layout/engines.py",
        "            in_edge = b.right if backward else b.x",
        "            in_edge = b.x",
    ),
    (
        "the compose bomb is uncaught again",
        "svarupa/extract/compose.py",
        "        except (OSError, UnicodeDecodeError, yaml.YAMLError, RecursionError) as exc:",
        "        except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:",
    ),
    (
        "compose evidence cites the body again",
        "svarupa/extract/compose.py",
        "            line = service_keys.get(name, _line(body_node))",
        "            line = _line(body_node)",
    ),
    (
        "image classification splits the tag before the path again",
        "svarupa/extract/compose.py",
        '    name = image.rsplit("/", 1)[-1].split(":", 1)[0].lower()',
        '    name = image.split(":", 1)[0].rsplit("/", 1)[-1].lower()',
    ),
    (
        "compose override files are invisible again",
        "svarupa/detect.py",
        '    if _COMPOSE_VARIANT.match(name):\n        return "compose"',
        "    if False:\n        return 'compose'",
    ),
    (
        "a malformed services shape is silent again",
        "svarupa/extract/compose.py",
        "        if not isinstance(services_node, yaml.MappingNode):",
        "        if services_node is None and False:",
    ),
    (
        "compose directories are code modules again",
        "svarupa/lock/build.py",
        "        if node.lang in GRAMMAR_VERSIONS and path in graph.architecture_paths:",
        "        if node.lang and path in graph.architecture_paths:",
    ),
    (
        "service records are dropped from the lockfile",
        "svarupa/lock/build.py",
        "    records.extend(\n        sorted(\n            Record(kind_map[node.kind], (node.label,))",
        "    records.extend(\n        sorted(\n            Record(kind_map[node.kind], (node.label,))\n            for node in []",
    ),
    (
        "same-label services merge silently again",
        "svarupa/lock/build.py",
        "        if len(holders) > 1:",
        "        if False:",
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
