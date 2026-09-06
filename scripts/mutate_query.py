"""Mutation check for Wave C: graph.json schema 2, rationale, the query surface.

Each entry deletes a property the wave names; a test must go red.
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
PY = ROOT / ".venv" / "bin" / "python"
SUITE = ["tests/test_graph_query.py", "tests/test_emit.py"]

MUTATIONS: list[tuple[str, str, str, str]] = [
    (
        "edges lose their typed context",
        "svarupa/emit/data.py",
        '                "context": _CONTEXT.get(e.kind.value, e.kind.value),',
        '                "context": None,',
    ),
    (
        "routes are no longer graph nodes",
        "svarupa/emit/data.py",
        "    for r in sorted(graph.routes):\n        if r.file not in graph.architecture_paths:\n            continue",
        "    for r in sorted(graph.routes):\n        if True:\n            continue",
    ),
    (
        "externals are no longer graph nodes",
        "svarupa/emit/data.py",
        "        if x.file not in graph.architecture_paths or x.category not in _EXTERNAL_KIND:\n            continue",
        "        if True:\n            continue",
    ),
    (
        "a route node cites the module's first line instead of the decorator",
        "svarupa/emit/data.py",
        '                "evidence": _evidence((r.evidence, *r.via)),',
        '                "evidence": _evidence(tuple(graph.nodes[r.file].evidence)),',
    ),
    (
        "the store edge loses its store context",
        "svarupa/emit/data.py",
        '                "context": _EXTERNAL_CONTEXT[xid.split(":")[1]],',
        '                "context": "depends_on",',
    ),
    (
        "rationale attaches to the module, never the innermost definition",
        "svarupa/emit/data.py",
        "        target: str | None = min(inner)[1] if inner else None",
        "        target: str | None = None",
    ),
    (
        "the module docstring takes the whole string instead of its first line",
        "svarupa/extract/rationale.py",
        "                if not first:\n                    first = seg.strip()\n            return None",
        "                first += ' ' + seg.strip()\n            return None",
    ),
    (
        "a marker with no text becomes a rationale",
        "svarupa/extract/rationale.py",
        '        if m and _clean(m.group("text")):',
        "        if m:",
    ),
    (
        "rationale runs over every source file, vendored ones too",
        "svarupa/emit/__init__.py",
        "    rationale = rationale_facts(root, graph.architecture_paths)",
        "    rationale = rationale_facts(root, {n for n in graph.nodes if n.endswith(('.py', '.ts'))})",
    ),
    (
        "built_at_commit is always None",
        "svarupa/emit/__init__.py",
        "    return head if proc.returncode == 0 and len(head) == 40 else None",
        "    return None",
    ),
    (
        "schema stays 1",
        "svarupa/emit/data.py",
        '        "schema": 2,\n        "built_at_commit": built_at_commit,',
        '        "schema": 1,\n        "built_at_commit": built_at_commit,',
    ),
    (
        "an ambiguous label resolves to the first match",
        "svarupa/query/__init__.py",
        "    if len(ids) == 1:\n        return ids[0], {}",
        "    if len(ids) >= 1:\n        return ids[0], {}",
    ),
    (
        "labels match as substrings",
        "svarupa/query/__init__.py",
        "        return sorted(self.by_label.get(label, []))",
        "        return sorted(i for lbl, ids in self.by_label.items() if label in lbl for i in ids)",
    ),
    (
        "relation filters are ignored",
        "svarupa/query/__init__.py",
        '        return relation is None or relation in (e.get("kind"), e.get("context"))',
        "        return True",
    ),
    (
        "shortest_path ignores max_hops",
        "svarupa/query/__init__.py",
        "        if d >= max_hops:\n            continue",
        "        if False:\n            continue",
    ),
    (
        "shortest_path is always undirected",
        "svarupa/query/__init__.py",
        '        if undirected:\n            steps += [(e["src"], e) for e in index.inc.get(cur, [])]',
        '        if True:\n            steps += [(e["src"], e) for e in index.inc.get(cur, [])]',
    ),
    (
        "affected follows outgoing edges (what X uses) instead of incoming (what uses X)",
        "svarupa/query/__init__.py",
        "            for e in index.inc.get(cur, []):\n                if relation is not None and relation not in",
        "            for e in index.out.get(cur, []):\n                if relation is not None and relation not in",
    ),
    (
        "stopwords are searched",
        "svarupa/query/__init__.py",
        "    return [w for w in words if len(w) > 1 and w not in _STOP]",
        "    return [w for w in words if len(w) > 1]",
    ),
    (
        "query_graph stops announcing truncation",
        "svarupa/query/__init__.py",
        "    if len(shown_nodes) < len(ordered) or len(shown_edges) < len(edges):",
        "    if False:",
    ),
    (
        "query_graph claims to be semantic",
        "svarupa/query/__init__.py",
        '        "matching": "keyword (a word of the question appears in the node), not semantic",',
        '        "matching": "semantic",',
    ),
    (
        "the CLI exits 0 on an unanswered question",
        "svarupa/query/cli.py",
        "    return 1 if _unanswered(result) else 0",
        "    return 0",
    ),
    (
        "a missing graph.json is a traceback instead of SVA-Q-001",
        "svarupa/query/__init__.py",
        "        if not candidate.is_file():\n            raise DiagnosticError(",
        "        if False:\n            raise DiagnosticError(",
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
