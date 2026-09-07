"""The viewer's JavaScript, executed.

Review #18 named the script as the riskiest untested surface; review #19
found two state bugs a first click exposes with a jsdom harness that runs in
seconds. This test runs `tests/js/viewer_harness.js` against a freshly built
artifact. It needs node and jsdom (`npm install` in `tests/js`); when either
is missing it SKIPS with the reason, so the absence is visible in the run
rather than silent.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from svarupa.cli import main

HERE = Path(__file__).resolve().parent
HARNESS = HERE / "js" / "viewer_harness.js"


def write(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf8")


def _repo(root: Path) -> None:
    write(root, "api/__init__.py", "")
    write(
        root,
        "api/routes.py",
        "from fastapi import APIRouter\nfrom domain import orders\nimport openai\nrouter = APIRouter()\n\n"
        '@router.get("/orders")\ndef a():\n    pass\n',
    )
    write(root, "domain/__init__.py", "")
    write(
        root,
        "domain/orders.py",
        "from store import db\n\n\nclass Orders:\n    def all(self):\n        return db.q()\n",
    )
    write(root, "store/__init__.py", "")
    write(root, "store/db.py", "import psycopg2\n\n\ndef q():\n    return []\n")
    write(root, "util/__init__.py", "")
    write(root, "util/x.py", "from domain import orders\n")


def _node() -> str:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed; the viewer script is not executed in this run")
    if not (HERE / "js" / "node_modules" / "jsdom").exists():
        pytest.skip(
            "jsdom is not installed (npm install in tests/js); the viewer script is not executed in this run"
        )
    return node


def test_the_viewer_script_behaves_in_a_dom(tmp_path: Path) -> None:
    node = _node()
    _repo(tmp_path)
    out = tmp_path / "out"
    assert main([str(tmp_path), "--out", str(out)]) == 0
    proc = subprocess.run(
        [node, str(HARNESS), str(out / "index.html")],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    lines = proc.stdout.strip().splitlines()
    fails = [ln for ln in lines if ln.startswith("FAIL")]
    assert proc.returncode == 0 and not fails, (
        "\n".join(lines[-40:]) + "\n" + proc.stderr[-2000:]
    )
    assert sum(1 for ln in lines if ln.startswith("ok ")) >= 12, "\n".join(lines)
