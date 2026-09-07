"""Mutation check for Wave D and E: cards, guided views, containment, export,
and the MCP server. Each entry deletes a property the wave names; a test must
go red.
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
    "tests/test_waved.py",
    "tests/test_cards.py",
    "tests/test_emit.py",
    "tests/test_viewer_js.py",
    "tests/test_layout.py",
]

MUTATIONS: list[tuple[str, str, str, str]] = [
    (
        "the containment check is not run",
        "svarupa/layout/__init__.py",
        "    if ds.root in good:\n        problems.extend(_containment(ds.root, good[ds.root]))\n",
        "",
    ),
    (
        "the containment check ignores the chrome around the canvas",
        "svarupa/layout/__init__.py",
        "        if canvas.width <= w - CHROME_W and canvas.height <= h - CHROME_H",
        "        if canvas.width <= w and canvas.height <= h",
    ),
    (
        "a scrolling root is reported as fitting",
        "svarupa/layout/__init__.py",
        "    if fits(canvas):\n        return []",
        "    if True:\n        return []",
    ),
    (
        "the report's fit column names a viewport for a scrolling root",
        "svarupa/emit/report.py",
        '            else:\n                fits_in = "scrolls"',
        '            else:\n                fits_in = "2048x1320"',
    ),
    (
        "export loses the theme attribute",
        "svarupa/emit/viewer.py",
        "    copy.setAttribute('data-theme', document.documentElement.getAttribute('data-theme') || 'dark');\n",
        "",
    ),
    (
        "the export buttons are gone",
        "svarupa/emit/viewer.py",
        '                    \'<span class="export"><button type="button" data-export="svg">Export SVG</button>\'\n                    \'<button type="button" data-export="png">Export PNG</button></span>\'',
        "                    ''",
    ),
    (
        "the MCP server drops a tool",
        "svarupa/query/mcp_server.py",
        '    @server.tool(description="The most connected nodes.")\n    def god_nodes(',
        "    def god_nodes(",
    ),
    (
        "an MCP tool answers with a different implementation than the CLI",
        "svarupa/query/mcp_server.py",
        '    def get_node(label: str) -> dict[str, Any]:\n        return run_query(index, "get_node", [label])',
        '    def get_node(label: str) -> dict[str, Any]:\n        return {"match": {"id": label}, "matched_by": "id"}',
    ),
    (
        "a missing SDK is a traceback instead of SVA-Q-002",
        "svarupa/query/mcp_server.py",
        "    except ImportError as exc:\n        raise DiagnosticError(",
        "    except MemoryError as exc:\n        raise DiagnosticError(",
    ),
    # --- review #19: the reviewer's surviving mutations, and the JS harness ---
    (
        "the unresolved card loses its samples",
        "svarupa/emit/cards.py",
        '            tail = f" · e.g. {\', \'.join(samples)}" if samples else ""',
        '            tail = ""',
    ),
    (
        "the unresolved card shows the scorecard's bucket tags",
        "svarupa/emit/cards.py",
        '                s.split(":", 1)[1] if ":" in s else s',
        "                s",
    ),
    (
        "a chapter counts a box that is not in its view",
        "svarupa/emit/cards.py",
        "            focus=(n.id, *sorted(neighbours.get(n.id, set()))),",
        "            focus=(n.id, *sorted(neighbours.get(n.id, set())), n.id + '#ghost'),",
    ),
    (
        "chapters are ranked by id instead of degree",
        "svarupa/emit/cards.py",
        "    ranked = sorted(starred, key=lambda n: (-degree.get(n.id, 0), n.id))[:MAX_CHAPTERS]",
        "    ranked = sorted(starred, key=lambda n: n.id)[:MAX_CHAPTERS]",
    ),
    (
        "a box with no arrows leads a chapter",
        "svarupa/emit/cards.py",
        "        if degree.get(n.id)\n        and (",
        "        if True\n        and (",
    ),
    (
        "message buses fall out of Data stores",
        "svarupa/emit/cards.py",
        '        if x.category in ("database", "messagebus"):',
        '        if x.category == "database":',
    ),
    (
        "SVA-G-016 becomes a warning",
        "svarupa/layout/__init__.py",
        '            code="SVA-G-016",\n            severity=Severity.INFO,',
        '            code="SVA-G-016",\n            severity=Severity.WARNING,',
    ),
    (
        "the mutual-pair exemption is dropped",
        "svarupa/layout/validate.py",
        "            if r.src == s.src or r.dst == s.dst or {r.src, r.dst} == {s.src, s.dst}:",
        "            if r.src == s.src or r.dst == s.dst:",
    ),
    (
        "the overlap gate tolerates a few pixels",
        "svarupa/layout/validate.py",
        "                    if hi > lo:",
        "                    if hi > lo + 3:",
    ),
    (
        "backward drops and climbs draw from separate counters again",
        "svarupa/layout/engines.py",
        "        g = order.index(levels.get(s[0], 0))\n        climb_k[s] = right_k.get(g, 0)\n        right_k[g] = right_k.get(g, 0) + 1",
        "        g = order.index(levels.get(s[0], 0))\n        climb_k[s] = 0\n        right_k[g] = right_k.get(g, 0) + 1",
    ),
    (
        "the grid's reversed edge runs through the rows again",
        "svarupa/layout/engines.py",
        "            points = [\n                (ax, a.bottom),\n                (ax, gap_a),\n                (lane, gap_a),\n                (lane, gap_b),\n                (bx, gap_b),\n                (bx, b.bottom),\n            ]",
        "            points = [(ax, a.bottom), (ax, gap_a), (bx, gap_a), (bx, gap_b), (bx, b.bottom)]",
    ),
    (
        "the channel constraint is inverted",
        "svarupa/layout/engines.py",
        "                if m != n and tops[m] & bottoms[n]:",
        "                if m != n and bottoms[m] & tops[n]:",
    ),
    (
        "the chrome constants are ignored",
        "svarupa/layout/__init__.py",
        "CHROME_W = 57\nCHROME_H = 290",
        "CHROME_W = 0\nCHROME_H = 0",
    ),
    (
        "MCP affected ignores a negative depth clamp",
        "svarupa/query/mcp_server.py",
        '        return run_query(index, "affected", [label], relation=relation, depth=max(0, depth))',
        '        return run_query(index, "affected", [label], relation=relation, depth=depth)',
    ),
    (
        "regions stop naming their members",
        "svarupa/emit/svg.py",
        '        data_members="\\n".join(region.members),\n',
        "",
    ),
    (
        "a background click leaves the hover state behind (JS)",
        "svarupa/emit/viewer.py",
        "      s.classList.remove('is-focused'); s.classList.remove('is-hovering'); s.classList.remove('is-pinned');",
        "      s.classList.remove('is-focused'); s.classList.remove('is-pinned');",
    ),
    (
        "a pinned path survives the next click (JS)",
        "svarupa/emit/viewer.py",
        "    document.querySelectorAll('svg.is-focused, svg.is-hovering, svg.is-pinned').forEach(function (s) {",
        "    document.querySelectorAll('svg.is-focused, svg.is-hovering').forEach(function (s) {",
    ),
    (
        "reach direction inverted (JS)",
        "svarupa/emit/viewer.py",
        "        var nxt = dir === 'down' ? (s === cur ? d : null) : (d === cur ? s : null);",
        "        var nxt = dir === 'up' ? (s === cur ? d : null) : (d === cur ? s : null);",
    ),
    (
        "an invented verb for an unlabelled arrow (JS)",
        "svarupa/emit/viewer.py",
        "      var said = r.getAttribute('data-note') || r.getAttribute('data-label') || 'connects';",
        "      var said = r.getAttribute('data-note') || r.getAttribute('data-label') || 'imports';",
    ),
    (
        "a drill keeps the passport open (JS)",
        "svarupa/emit/viewer.py",
        "    panel.classList.remove('is-open');\n    clearFocus();\n    clearPins();",
        "    clearPins();",
    ),
    (
        "export keeps a search or mute state (JS)",
        "svarupa/emit/viewer.py",
        "    copy.classList.remove('is-focused', 'is-hovering', 'is-pinned', 'is-searching');\n",
        "    copy.classList.remove('is-focused', 'is-hovering', 'is-pinned');\n",
    ),
    (
        "export keeps hit and muted boxes (JS)",
        "svarupa/emit/viewer.py",
        "    copy.querySelectorAll('.is-path, .is-focus, .is-hit, .is-off').forEach(function (x) {",
        "    copy.querySelectorAll('.is-path, .is-focus').forEach(function (x) {",
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
