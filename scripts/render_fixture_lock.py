"""Build a lockfile from a real fixture tree, for the cross-platform CI gate.

Deliberately writes files to disk and walks them, rather than rendering an
in-memory corpus. An in-memory corpus is pure string manipulation and can
essentially never differ between Linux and macOS, so it proves the serializer
is deterministic while saying nothing about the product. The hazards that
actually matter, NFD paths and dirent ordering and case-insensitive volumes,
only enter through a filesystem walk.

**It runs the whole pipeline.** The first version stopped at `detect` and then
computed modules itself with `str(Path(f.path).parent)`, and synthesized `dep`
records by pairing adjacent module names. So `extract`, `resolve`, `build` and
`build_lock` never crossed the OS boundary at all: a platform-dependent
iteration anywhere in resolution would have passed the only gate that compares
Linux to macOS. Its own module derivation had already diverged from the
product's, emitting an id the real tool could not produce.

A gate that rebuilds its expectation with its own implementation of the stage
under test certifies that implementation, not the product. This calls exactly
what the shipped CLI calls.
"""

from __future__ import annotations

import sys
import tempfile
import unicodedata
from pathlib import Path

from svarupa import __version__
from svarupa.build import build
from svarupa.detect import detect
from svarupa.extract import declared_dependencies, extract
from svarupa.lock import build_lock

# Names chosen to provoke the known hazards: NFD composition, case-only
# differences, non-ASCII, and creation order that is not sort order. The
# contents are real imports, so `extract` and `build` produce genuine `dep`
# records rather than synthetic ones.
NFD_CAFE = unicodedata.normalize("NFD", "café")

FILES: dict[str, str] = {
    "src/__init__.py": "",
    "src/zeta/__init__.py": "",
    "src/zeta/handler.py": "from ..alpha.util import helper\n",
    "src/alpha/__init__.py": "",
    "src/alpha/main.py": "from .util import helper\n",
    "src/alpha/util.py": "def helper():\n    return 1\n",
    "src/Beta/__init__.py": "",
    "src/Beta/mod.py": "from ..alpha.util import helper\n",
    f"src/{NFD_CAFE}/__init__.py": "",
    f"src/{NFD_CAFE}/order.py": "from ..zeta.handler import helper\n",
    "src/Unicode/__init__.py": "",
    "src/Unicode/x.py": "from ..alpha import util\n",
    "web/app.ts": "import { thing } from './lib/thing';\nexport const a = thing;\n",
    "web/lib/thing.ts": "export const thing = 1;\n",
    "web/Component.tsx": "export const C = () => null;\n",
    "schema/init.sql": "CREATE TABLE t (id INT);\n",
    "docker-compose.yml": "services:\n  api:\n    image: x\n",
}


def build_tree(root: Path) -> None:
    for rel, text in FILES.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf8")


def main(out: str) -> int:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "fixture"
        root.mkdir()
        build_tree(root)

        # Exactly the shipped path, in the order the CLI runs it.
        scan = detect(root)
        graph = build(scan, extract(scan, declared_dependencies(scan)), strict=False)
        result = build_lock(graph, __version__)

        Path(out).write_text(result.lockfile.render(), encoding="utf8", newline="")

        deps = sum(1 for r in result.lockfile.records if r.kind == "dep")
        modules = sum(1 for r in result.lockfile.records if r.kind == "module")
        print(
            f"{len(scan.files)} files scanned, {modules} modules, {deps} deps, "
            f"{len(result.lockfile.records)} records"
        )
        for d in result.diagnostics:
            print(f"  {d.render()}", file=sys.stderr)
        if not deps:
            # A gate that certifies a lockfile with no dependencies compares
            # only the easy half. Fail loudly rather than pass quietly.
            print("ERROR: the fixture produced no dep records", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "lockfile.out"))
