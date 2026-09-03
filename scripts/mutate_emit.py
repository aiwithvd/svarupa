"""Mutation check for the emit stage.

Written as a file rather than typed at a shell because several of the patterns
contain invisible characters (U+2028, U+2029), and a shell command carrying
those is both unreviewable and rejected by the harness. Same reason the source
writes them as escapes.

Each entry disables exactly one guarantee. A guarantee with no failing test is
a guarantee nobody is checking.
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
PY = ROOT / ".venv" / "bin" / "python"

MUTATIONS: list[tuple[str, str, str, str]] = [
    (
        "esc becomes a no-op",
        "svarupa/emit/markup.py",
        "    for bad, good in _HTML:\n        out = out.replace(bad, good)",
        "    for bad, good in ():\n        out = out.replace(bad, good)",
    ),
    (
        "esc stops escaping the single quote",
        "svarupa/emit/markup.py",
        '    ("\'", "&#39;"),',
        '    ("\\x00", "&#39;"),',
    ),
    (
        "esc escapes & last, so its own output is double-escaped",
        "svarupa/emit/markup.py",
        '_HTML = (\n    ("&", "&amp;"),\n    ("<", "&lt;"),',
        '_HTML = (\n    ("<", "&lt;"),\n    ("&", "&amp;"),',
    ),
    (
        "attrs renders None as the string 'None'",
        "svarupa/emit/markup.py",
        "        if value is None or value is False:",
        "        if value is False:",
    ),
    (
        "json_script stops escaping the angle bracket",
        "svarupa/emit/markup.py",
        '        ("<", "\\\\u003c"),',
        '        ("\\x00", "\\\\u003c"),',
    ),
    (
        "json_script stops escaping U+2028",
        "svarupa/emit/markup.py",
        '        ("\\u2028", "\\\\u2028"),',
        '        ("\\x00", "\\\\u2028"),',
    ),
    (
        "emit writes the absolute path as the display root",
        "svarupa/emit/__init__.py",
        "    shown = display_root if display_root is not None else root.name",
        "    shown = display_root if display_root is not None else str(root)",
    ),
    (
        "detect stops refusing a root that is not a directory",
        "svarupa/detect.py",
        "    if not root_path.is_dir():",
        "    if False:",
    ),
    (
        "the report drops the pinning caveat",
        "svarupa/emit/report.py",
        '"This table measures **pinning, not correctness.** A confidently wrong "',
        '"This table shows resolution quality. A confidently wrong "',
    ),
    (
        "the viewer stops emitting the noscript block",
        "svarupa/emit/viewer.py",
        '                "noscript",',
        '                "div",',
    ),
    (
        "the parser check is fooled by an element built from data",
        "svarupa/emit/svg.py",
        "                esc(box.label),",
        "                raw(box.label),",
    ),
    (
        "the SVG title stops carrying citations",
        "svarupa/emit/svg.py",
        'esc(f"{box.full_label}\\n{evidence_ref(box.evidence)}"),',
        'esc(f"{box.full_label}"),',
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
                [str(PY), "-m", "pytest", "tests/test_emit.py", "-q", "-x"],
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
