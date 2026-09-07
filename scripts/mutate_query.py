"""Mutation check for Wave C: graph.json schema 2, rationale, the query surface,
the explorer controls; plus every mutation review #18 wrote that survived.

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
        "routes in test files become graph nodes",
        "svarupa/emit/data.py",
        "    for r in sorted(graph.routes):\n        if r.file not in graph.architecture_paths:\n            continue",
        "    for r in sorted(graph.routes):\n        if False:\n            continue",
    ),
    (
        "externals are no longer graph nodes",
        "svarupa/emit/data.py",
        "        if x.file not in graph.architecture_paths or x.category not in _EXTERNAL_KIND:\n            continue",
        "        if True:\n            continue",
    ),
    (
        "externals in test files become graph nodes",
        "svarupa/emit/data.py",
        "        if x.file not in graph.architecture_paths or x.category not in _EXTERNAL_KIND:\n            continue",
        "        if x.category not in _EXTERNAL_KIND:\n            continue",
    ),
    (
        "a route node cites the module's first line instead of the decorator",
        "svarupa/emit/data.py",
        '                "evidence": _evidence((r.evidence, *r.via)),',
        '                "evidence": _evidence(tuple(graph.nodes[r.file].evidence)),',
    ),
    (
        "the exposes edge cites the module's first line",
        "svarupa/emit/data.py",
        '                "context": "route",\n                "resolution": "resolved",\n                "arity": 1,\n                "evidence": _evidence((r.evidence,)),',
        '                "context": "route",\n                "resolution": "resolved",\n                "arity": 1,\n                "evidence": _evidence(tuple(graph.nodes[r.file].evidence)),',
    ),
    (
        "an external node cites only its first importing line",
        "svarupa/emit/data.py",
        '                "evidence": _evidence(tuple(seen_ext[xid])),',
        '                "evidence": _evidence(tuple(seen_ext[xid][:1])),',
    ),
    (
        "the store edge loses its store context",
        "svarupa/emit/data.py",
        '                "context": _EXTERNAL_CONTEXT[xid.split(":")[1]],',
        '                "context": "depends_on",',
    ),
    (
        "two routes with the same declared path share one id",
        "svarupa/emit/data.py",
        '        rid = _fresh(f"{r.file}#{r.handler}#route:{r.method} {r.path}", taken)',
        '        rid = f"{r.file}#route:{r.method} {r.path}"',
    ),
    (
        "rationale attaches to the module, never the innermost definition",
        "svarupa/emit/data.py",
        "        target: str | None = min(inner)[1] if inner else None",
        "        target: str | None = None",
    ),
    (
        "rationale attaches to the outermost definition instead of the innermost",
        "svarupa/emit/data.py",
        "        target: str | None = min(inner)[1] if inner else None",
        "        target: str | None = max(inner)[1] if inner else None",
    ),
    (
        "rationale labels are never cut",
        "svarupa/emit/data.py",
        '                "label": f.text if len(f.text) <= 60 else f.text[:59] + "…",',
        '                "label": f.text,',
    ),
    (
        "a docstring takes its whole text instead of the first line",
        "svarupa/extract/rationale.py",
        "    for line in body.splitlines():\n        cleaned = _clean(line)",
        "    for line in [body]:\n        cleaned = _clean(line)",
    ),
    (
        "a marker with no text becomes a rationale",
        "svarupa/extract/rationale.py",
        '    if not m or not _clean(m.group("text")):',
        "    if not m:",
    ),
    (
        "a marker word anywhere in a comment is a marker",
        "svarupa/extract/rationale.py",
        '_MARKER_RE = re.compile(r"^\\s*(?P<kind>" + "|".join(MARKERS) + r")\\b[:\\s-]*(?P<text>.*)$")',
        '_MARKER_RE = re.compile(r"(?P<kind>" + "|".join(MARKERS) + r")[:\\s-]*(?P<text>.*)$")',
    ),
    (
        "a comment's end line is dropped",
        "svarupa/extract/rationale.py",
        '            RationaleFact(file, s.start_point.row + 1, s.end_point.row + 1, "docstring", text)',
        '            RationaleFact(file, s.start_point.row + 1, s.start_point.row + 1, "docstring", text)',
    ),
    (
        "every file is scanned for rationale, yaml and markdown too",
        "svarupa/extract/rationale.py",
        "        if not rel.endswith(_PY_SUFFIXES + _TS_SUFFIXES):\n            continue",
        "        if False:\n            continue",
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
        "    return head, bool(status.strip())",
        "    return None, None",
    ),
    (
        "a dirty tree is reported clean",
        "svarupa/emit/__init__.py",
        "    return head, bool(status.strip())",
        "    return head, False",
    ),
    (
        "an untracked directory borrows the enclosing repository's commit",
        "svarupa/emit/__init__.py",
        "    if not tracked or not tracked.strip():\n        return None, None",
        "    if False:\n        return None, None",
    ),
    (
        "schema stays 1",
        "svarupa/emit/data.py",
        '        "schema": 2,\n',
        '        "schema": 1,\n',
    ),
    (
        "an ambiguous label resolves to the first match",
        "svarupa/query/__init__.py",
        "    if len(hits) == 1:\n        ((nid, by),) = hits.items()",
        "    if len(hits) >= 1:\n        (nid, by) = next(iter(hits.items()))",
    ),
    (
        "labels match as substrings",
        "svarupa/query/__init__.py",
        '        for nid in self.by_label.get(label, []):\n            hits.setdefault(nid, "label")',
        '        for lbl, ids in self.by_label.items():\n            if label in lbl:\n                for nid in ids:\n                    hits.setdefault(nid, "label")',
    ),
    (
        "an id that is also another node's label is not an ambiguity",
        "svarupa/query/__init__.py",
        '        if label in self.nodes:\n            hits[label] = "id"\n        for nid in self.by_qualified.get(label, []):',
        '        if label in self.nodes:\n            return {label: "id"}\n        for nid in self.by_qualified.get(label, []):',
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
        "        if undirected:\n            steps += [",
        "        if True:\n            steps += [",
    ),
    (
        "shortest_path walks through docstrings",
        "svarupa/query/__init__.py",
        '        steps = [(e["dst"], e) for e in index.out.get(cur, []) if not _is_rationale(e)]',
        '        steps = [(e["dst"], e) for e in index.out.get(cur, [])]',
    ),
    (
        "affected follows outgoing edges (what X uses) instead of incoming (what uses X)",
        "svarupa/query/__init__.py",
        '            for e in index.inc.get(cur, []):\n                if _is_rationale(e) and relation != "rationale":',
        '            for e in index.out.get(cur, []):\n                if _is_rationale(e) and relation != "rationale":',
    ),
    (
        "a docstring counts as blast radius",
        "svarupa/query/__init__.py",
        '                if _is_rationale(e) and relation != "rationale":\n                    continue',
        "                if False:\n                    continue",
    ),
    (
        "affected hits stop saying how they were reached",
        "svarupa/query/__init__.py",
        '        {**_brief(index.nodes[i]), "hops": h, "via": via}',
        '        {**_brief(index.nodes[i]), "hops": h, "via": ""}',
    ),
    (
        "stopwords are searched",
        "svarupa/query/__init__.py",
        "    return [w for w in words if len(w) > 1 and w not in _STOP]",
        "    return [w for w in words if len(w) > 1]",
    ),
    (
        "one-character substrings match",
        "svarupa/query/__init__.py",
        "    return len(word) >= 4 and any(word in tok for tok in tokens)",
        "    return any(word in tok for tok in tokens)",
    ),
    (
        "query_graph stops announcing truncation",
        "svarupa/query/__init__.py",
        "    if len(shown_nodes) < len(ordered) or len(shown_edges) < len(edges):",
        "    if False:",
    ),
    (
        "the truncation banner counts what was shown as the total",
        "svarupa/query/__init__.py",
        'f"TRUNCATED to {token_budget} tokens: showing {len(shown_nodes)} of "\n            f"{len(ordered)} nodes',
        'f"TRUNCATED to {token_budget} tokens: showing {len(shown_nodes)} of "\n            f"{len(shown_nodes)} nodes',
    ),
    (
        "the budget is measured on compact JSON while the CLI prints indented",
        "svarupa/query/__init__.py",
        "        cost = _cost(item)",
        "        cost = len(json.dumps(item)) // 6",
    ),
    (
        "edges are never truncated",
        "svarupa/query/__init__.py",
        "        cost = _cost(e)\n        if used + cost > token_budget:\n            break",
        "        cost = _cost(e)\n        if False:\n            break",
    ),
    (
        "query_graph claims to be semantic",
        "svarupa/query/__init__.py",
        '        "matching": "keyword (a word of the question appears in the node), not semantic",',
        '        "matching": "semantic",',
    ),
    (
        "god_nodes ties are in dict order",
        "svarupa/query/__init__.py",
        "    ranked = sorted(degree.items(), key=lambda kv: (-kv[1], kv[0]))[:top_n]",
        "    ranked = sorted(degree.items(), key=lambda kv: -kv[1])[:top_n]",
    ),
    (
        "the CLI exits 0 on an unanswered question",
        "svarupa/query/cli.py",
        "    return 1 if _unanswered(result) else 0",
        "    return 0",
    ),
    (
        "zero hits is an answer",
        "svarupa/query/cli.py",
        '        or result.get("hits") == 0',
        "        or False",
    ),
    (
        "a missing graph.json is a traceback instead of SVA-Q-001",
        "svarupa/query/__init__.py",
        "        if not candidate.is_file():\n            raise DiagnosticError(",
        "        if False:\n            raise DiagnosticError(",
    ),
    # --- explorer controls ---
    (
        "the explorer toolbar is gone",
        "svarupa/emit/viewer.py",
        "                _explore_bar(),\n",
        "",
    ),
    (
        "legend swatches stop naming their kind",
        "svarupa/emit/viewer.py",
        "                data_kind=_kind_slug(kind),\n",
        "",
    ),
    (
        "passport neighbours stop being clickable",
        "svarupa/emit/viewer.py",
        "        li.setAttribute('data-target', s === id ? d : s);\n",
        "",
    ),
    (
        "shift-click no longer pins a path",
        "svarupa/emit/viewer.py",
        "    if (ev.shiftKey && node.classList.contains('sv-node')) { pinPath(node); return; }\n",
        "",
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
