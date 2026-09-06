"""Mutation check for the semantics wave (routes, tasks, entrypoints, roles).

Each entry deletes a property the wave's claims name; a test must go red.
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
PY = ROOT / ".venv" / "bin" / "python"
SUITE = ["tests/test_semantics.py", "tests/test_diagnostics.py", "tests/test_lock.py"]

MUTATIONS: list[tuple[str, str, str, str]] = [
    (
        "a route shape claims fastapi without the import",
        "svarupa/extract/semantics.py",
        '    fastapi = _imports_top(f, "fastapi")',
        "    fastapi = True",
    ),
    (
        "a task shape claims celery without the import",
        "svarupa/extract/semantics.py",
        '    if not _imports_top(f, "celery"):\n        return []',
        "    if False:\n        return []",
    ),
    (
        "flask loses its documented GET default",
        "svarupa/extract/semantics.py",
        '                for method in dec.methods or ("GET",):',
        '                for method in dec.methods or ("POST",):',
    ),
    (
        "the route claim cites the line below the decorator",
        "svarupa/extract/python.py",
        "        ev = self.evidence(path, node.start_point[0], node.end_point[0])",
        "        ev = self.evidence(path, node.start_point[0] + 1, node.end_point[0] + 1)",
    ),
    (
        "an f-string route path is guessed from its static parts",
        "svarupa/extract/python.py",
        '    if any(c.type == "interpolation" for c in node.children):',
        "    if False:",
    ),
    (
        "the manifest line scan ignores table scope",
        "svarupa/extract/semantics.py",
        "        if current == table and pattern.match(line):",
        "        if pattern.match(line):",
    ),
    (
        "endpoint records key on the handler name, not the module",
        "svarupa/lock/build.py",
        '                Record("endpoint", (f"{r.method} {r.path}", spell(module_of(r.file))))',
        '                Record("endpoint", (f"{r.method} {r.path}", r.handler))',
    ),
    (
        "a route in a test file becomes an architectural fact",
        "svarupa/lock/build.py",
        "    arch_routes = [r for r in graph.routes if r.file in graph.architecture_paths]",
        "    arch_routes = list(graph.routes)",
    ),
    (
        "an entrypoint target is trusted instead of resolved",
        "svarupa/build.py",
        "    for cand in candidates:\n        if cand in graph.nodes:\n            return _module_of(cand)\n    return None",
        "    for cand in candidates:\n        return _module_of(cand)\n    return None",
    ),
    (
        "role records are dropped from the lockfile",
        "svarupa/lock/build.py",
        '            Record("role", (spell(m), role))',
        '            Record("role", (spell(m), role))\n            for _never in ()',
    ),
    (
        "the schema minor is not bumped for the new kinds",
        "svarupa/lock/grammar.py",
        "SCHEMA_MINOR = 2",
        "SCHEMA_MINOR = 1",
    ),
    (
        "boxes stop wearing their role",
        "svarupa/derive/architecture.py",
        "def _role_kind(roles: dict[str, tuple[str, ...]], module: str) -> str:\n    held = roles.get(module, ())",
        "def _role_kind(roles: dict[str, tuple[str, ...]], module: str) -> str:\n    held = ()",
    ),
    (
        "build stops re-verifying fact evidence",
        "svarupa/build.py",
        "    routes = tuple(r for r in extracted.routes if fact_ok(r.evidence, r.handler))",
        "    routes = tuple(extracted.routes)",
    ),
    (
        "worker roles vanish from the shared role map",
        "svarupa/build.py",
        '            roles.setdefault(_module_of(t.file), set()).add("worker")',
        "            pass",
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
