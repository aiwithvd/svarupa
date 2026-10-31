"""Small fixes found in reviews of phases 1c-4 to 3."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from svarupa.cli import main
from svarupa.health import CATALOG
from svarupa.health.explain import example_for
from svarupa.setup.ci_gitlab import GITLAB_CI
from svarupa.setup.merge import BEGIN, END, MergeError, merge_block


def test_explain_rejects_an_unknown_language(capsys) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["explain", "query-in-loop", "--lang", "pyhton"])
    assert exc.value.code == 2
    assert "pyhton" in capsys.readouterr().err


def test_explain_still_accepts_javascript(capsys) -> None:
    assert main(["explain", "query-in-loop", "--lang", "javascript"]) == 0
    assert "prisma" in capsys.readouterr().out


def test_a_dry_run_prints_no_next_steps(tmp_path: Path, capsys) -> None:
    assert main(["setup", "agents", "--dry-run", "--dest", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "create" in out and "next:" not in out
    assert not (tmp_path / "AGENTS.md").exists()


def test_the_gitlab_report_is_valid_json_when_the_check_cannot_run(tmp_path: Path) -> None:
    sh = shutil.which("sh")
    if not sh:
        pytest.skip("sh is not installed")
    stub = tmp_path / "bin" / "svarupa"
    stub.parent.mkdir()
    stub.write_text(
        "#!/bin/sh\necho 'svarupa check: unknown ref' >&2\nexit 1\n", encoding="utf8"
    )
    stub.chmod(0o755)
    script = "\n".join(yaml.safe_load(GITLAB_CI)["health_check"]["script"])
    env = {**os.environ, "PATH": f"{stub.parent}{os.pathsep}{os.environ['PATH']}"}
    proc = subprocess.run(
        [sh, "-c", script], cwd=tmp_path, env=env, capture_output=True, text=True
    )
    assert proc.returncode == 1
    assert (tmp_path / "gl-code-quality-report.json").read_text(encoding="utf8").strip() == "[]"


def test_the_viewer_reads_box_lines_only_from_its_own_keys(tmp_path: Path) -> None:
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "m.py").write_text("def f():\n    return 1\n", encoding="utf8")
    assert main([str(tmp_path)]) == 0
    html = (tmp_path / ".svarupa" / "index.html").read_text(encoding="utf8")
    assert "hasOwnProperty.call(ABOUT, id)" in html


def test_java_examples_compile_as_written() -> None:
    # A method with a return type and an empty body does not compile.
    empty = re.compile(r"\b(?!void\b)[A-Z]\w*(?:<[^>]*>)?\s+\w+\([^)]*\)\s*\{\s*\}")
    bad = [
        c.id
        for c in CATALOG
        if (ex := example_for(c.id, "java"))
        and (empty.search(ex.before) or empty.search(ex.after))
    ]
    assert bad == []


def test_an_end_marker_before_the_begin_marker_says_so() -> None:
    with pytest.raises(MergeError, match="end marker before"):
        merge_block(f"{END}\nintro\n{BEGIN}\nrules\n", "body\n")
