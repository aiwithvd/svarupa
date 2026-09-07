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
SUITE = ["tests/test_waved.py", "tests/test_cards.py", "tests/test_emit.py"]

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
        "                    '<span class=\"export\"><button type=\"button\" data-export=\"svg\">Export SVG</button>'\n                    '<button type=\"button\" data-export=\"png\">Export PNG</button></span>'",
        "                    ''",
    ),
    (
        "the MCP server drops a tool",
        "svarupa/query/mcp_server.py",
        '    @server.tool(description="The most connected nodes.")\n    def god_nodes(top_n: int = 10) -> dict[str, Any]:\n        return run_query(index, "god_nodes", [], top=top_n)\n',
        '    def god_nodes(top_n: int = 10) -> dict[str, Any]:\n        return run_query(index, "god_nodes", [], top=top_n)\n',
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
