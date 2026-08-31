"""Build a lockfile from a real fixture tree, for the cross-platform CI gate.

Deliberately writes files to disk and walks them, rather than rendering an
in-memory corpus. An in-memory corpus is pure string manipulation and can
essentially never differ between Linux and macOS, so it proves the serializer
is deterministic while saying nothing about the product. The hazards that
actually matter -- NFD paths, dirent ordering, case-insensitive volumes --
only enter through a filesystem walk.
"""

from __future__ import annotations

import itertools
import sys
import tempfile
import unicodedata
from pathlib import Path

from svarupa import __version__
from svarupa.detect import detect
from svarupa.lock import Lockfile, dep_record, module_record

# Names chosen to provoke the known hazards: NFD composition, case-only
# differences, non-ASCII, and creation order that is not sort order.
FILES = [
    "src/zeta/handler.py",
    "src/alpha/main.py",
    "src/alpha/util.py",
    "src/Beta/mod.py",
    f"src/{unicodedata.normalize('NFD', 'café')}/order.py",
    "src/Ünïcode/x.py",
    "web/app.ts",
    "web/Component.tsx",
    "schema/init.sql",
    "docker-compose.yml",
]


def build_tree(root: Path) -> None:
    for rel in FILES:
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x = 1\n", encoding="utf8")


def main(out: str) -> int:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "fixture"
        root.mkdir()
        build_tree(root)

        scan = detect(root)
        modules = sorted({str(Path(f.path).parent) for f in scan.architecture_files})
        records = [module_record(m) for m in modules]
        # A couple of synthetic deps so `dep` records are exercised too.
        for a, b in itertools.pairwise(modules):
            records.append(dep_record(a, b))

        lock = Lockfile.build(
            __version__, {"python": "0.25.0", "typescript": "0.23.2"}, records
        )
        Path(out).write_text(lock.render(), encoding="utf8")

        print(
            f"{len(scan.files)} files scanned, {len(modules)} modules, {len(records)} records"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "lockfile.out"))
