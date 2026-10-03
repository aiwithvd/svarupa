"""Mutation check for byte-exact file lookup (the Linux NFD file drop).

Each entry disables one claim; a test must go red:

* every stage reads a scanned file through the NFC-aware reader, so a file
  whose on-disk bytes are NFD is found on Linux too;
* the reader falls back to matching names by NFC form when the direct path
  misses;
* a file that cannot be read at extract time is reported, never skipped
  silently;
* the reader translates newlines exactly as `Path.read_text` does.

Not covered here: two names that normalize alike match nothing. Creating both
needs a byte-exact filesystem, which macOS (APFS) is not; `detect` already
reports such a pair as SVA-L-004.
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
    "tests/test_byte_exact_fs.py",
    "tests/test_diagnostics.py",
]

MUTATIONS: list[tuple[str, str, str, str]] = [
    (
        "extract reopens a file by its NFC id again",
        "svarupa/extract/__init__.py",
        "data = read_bytes(scan.root, rec.path)",
        "data = (scan.root / rec.path).read_bytes()",
    ),
    (
        "the reader no longer falls back to an NFC name match",
        "svarupa/detect.py",
        "        real = on_disk(root, rel)\n",
        "        real = None\n",
    ),
    (
        "an unreadable file is skipped without a diagnostic",
        "svarupa/extract/__init__.py",
        "        except OSError as exc:\n            # Never a silent skip",
        "        except OSError as exc:\n            continue\n            # Never a silent skip",
    ),
    (
        "the reader keeps CRLF line endings",
        "svarupa/detect.py",
        '    return text.replace("\\r\\n", "\\n").replace("\\r", "\\n")',
        "    return text",
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
