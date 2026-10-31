"""The release helpers: CalVer arithmetic and the release-notes draft.

The shell script only orchestrates; every decision it relies on lives in these
two helpers so it can be tested without git, GitHub or PyPI.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from datetime import date
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    # Registered first: dataclasses resolve string annotations through it.
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


rv = _load("release_version")
rn = _load("release_notes")


# --------------------------------------------------------------------------
# release_version
# --------------------------------------------------------------------------


def test_first_release_of_a_month_is_n_1() -> None:
    assert rv.next_version(date(2026, 10, 3), ["v0.2.2"], []) == "2026.10.1"


def test_an_existing_tag_in_the_month_moves_n_up() -> None:
    tags = ["v2026.10.1", "v2026.10.2", "v2026.9.7"]
    assert rv.next_version(date(2026, 10, 30), tags, []) == "2026.10.3"


def test_a_new_month_starts_over() -> None:
    assert rv.next_version(date(2026, 11, 1), ["v2026.10.9"], []) == "2026.11.1"


def test_a_version_on_pypi_without_a_tag_still_counts() -> None:
    """A manual upload must never be overwritten by a computed version."""
    assert rv.next_version(date(2026, 10, 3), [], ["2026.10.4"]) == "2026.10.5"


def test_n_compares_as_a_number_not_text() -> None:
    assert rv.next_version(date(2026, 10, 3), ["v2026.10.9", "v2026.10.10"], []) == "2026.10.11"


def test_no_zero_padding_because_pep_440_would_rewrite_it() -> None:
    """PEP 440 normalizes 2026.01.1 to 2026.1.1, so a padded tag would not
    match the version on PyPI."""
    assert rv.next_version(date(2026, 1, 5), [], []) == "2026.1.1"


@pytest.mark.parametrize("good", ["2026.10.1", "2026.1.12", "2027.12.35"])
def test_valid_versions_pass(good: str) -> None:
    assert rv.validate(good) == good


@pytest.mark.parametrize(
    "bad",
    ["2026.01.1", "2026.10.0", "2026.13.1", "0.2.3", "2026.10", "2026.10.1rc1", "v2026.10.1"],
)
def test_invalid_versions_are_refused(bad: str) -> None:
    with pytest.raises(ValueError):
        rv.validate(bad)


# --------------------------------------------------------------------------
# release_notes
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("subject", "files", "section"),
    [
        ("Fix Linux NFD file drop", ["svarupa/detect.py"], "Fixes"),
        ("Read files by NFC id (fixes Linux drop)", ["svarupa/detect.py"], "Changes"),
        ("Add --fail-on-change", ["svarupa/cli.py"], "New"),
        ("README: name ci_gitlab", ["README.md"], "Docs"),
        ("Clarify lockfile grammar", ["docs/lockfile-grammar.md"], "Docs"),
        ("Clarify grammar and code", ["docs/x.md", "svarupa/a.py"], "Changes"),
        ("Apply ruff format", ["svarupa/a.py"], "Changes"),
    ],
)
def test_commits_are_grouped(subject: str, files: list[str], section: str) -> None:
    assert rn.section_of(subject, files) == section


@pytest.mark.parametrize("subject", ["Release 0.2.2", "Release 2026.10.1: notes"])
def test_release_commits_are_left_out(subject: str) -> None:
    assert rn.section_of(subject, ["pyproject.toml"]) is None


def test_the_draft_has_every_section_a_todo_and_the_compare_link() -> None:
    commits = [
        rn.Commit("Fix A", ("svarupa/a.py",)),
        rn.Commit("Add B", ("svarupa/b.py",)),
        rn.Commit("README: C", ("README.md",)),
        rn.Commit("Release 0.2.2", ("pyproject.toml",)),
    ]
    text = rn.draft("2026.10.1", "v0.2.2", commits, "https://github.com/o/r")
    assert text.index("## Fixes") < text.index("## New") < text.index("## Docs")
    assert "- Fix A" in text and "- Add B" in text and "- README: C" in text
    assert "Release 0.2.2" not in text
    assert "## Changes" not in text, "empty sections are left out"
    assert "## Upgrading" in text and "TODO:" in text
    assert "https://github.com/o/r/compare/v0.2.2...v2026.10.1" in text


def test_a_draft_with_no_previous_tag_has_no_compare_link() -> None:
    text = rn.draft(
        "2026.10.1", None, [rn.Commit("Fix A", ("a.py",))], "https://github.com/o/r"
    )
    assert "/compare/" not in text


def test_the_shell_script_parses() -> None:
    subprocess.run(["bash", "-n", str(ROOT / "scripts" / "release.sh")], check=True)


# --------------------------------------------------------------------------
# Resuming a publish: the GitHub Release and pin steps can run twice
# --------------------------------------------------------------------------


def _run_release_fn(tmp_path: Path, gh_script: str, call: str) -> tuple[int, str]:
    """Source release.sh with a fake `gh` on PATH, run one function, return
    (exit code, the fake gh's call log)."""
    import os
    import shutil

    if not shutil.which("bash"):
        pytest.skip("bash is not installed")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    log = tmp_path / "gh.log"
    gh = bindir / "gh"
    gh.write_text(f'#!/bin/sh\necho "$*" >> "{log}"\n{gh_script}\n', encoding="utf8")
    gh.chmod(0o755)
    (tmp_path / "a.whl").write_text("x", encoding="utf8")
    env = {**os.environ, "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}"}
    script = ROOT / "scripts" / "release.sh"
    proc = subprocess.run(
        ["bash", "-c", f'source "{script}"; {call}'],
        env=env,
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    return proc.returncode, log.read_text(encoding="utf8") if log.exists() else ""


def test_sourcing_the_script_runs_nothing(tmp_path: Path) -> None:
    code, calls = _run_release_fn(tmp_path, "exit 0", "true")
    assert code == 0 and calls == ""


def test_an_existing_release_gets_its_files_and_notes_updated(tmp_path: Path) -> None:
    # `gh release view` succeeds: the release exists from an earlier, half-done run.
    code, calls = _run_release_fn(
        tmp_path, "exit 0", f'github_release v1.0.0 docs/releases/v2026.10.2.md "{tmp_path}"'
    )
    assert code == 0, calls
    assert "release create" not in calls
    assert "release upload v1.0.0" in calls and "--clobber" in calls
    assert "release edit v1.0.0" in calls


def test_a_failed_create_falls_back_to_upload_when_the_release_now_exists(
    tmp_path: Path,
) -> None:
    # view fails first (no release), create fails (a retry made the asset twice),
    # then view succeeds: the release exists, so the files are re-uploaded.
    fake = (
        'case "$1 $2" in\n'
        '  "release view") if [ -f "$0.created" ]; then exit 0; else exit 1; fi ;;\n'
        '  "release create") touch "$0.created"; exit 1 ;;\n'
        "  *) exit 0 ;;\n"
        "esac"
    )
    code, calls = _run_release_fn(
        tmp_path, fake, f'github_release v1.0.0 docs/releases/v2026.10.2.md "{tmp_path}"'
    )
    assert code == 0, calls
    assert "release create v1.0.0" in calls
    assert "release upload v1.0.0" in calls and "--clobber" in calls
