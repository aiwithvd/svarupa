"""`svarupa setup`: installing files into someone else's repository.

The properties under test, each of which has a mutation that deletes it:

* nothing is written until every collision is checked, so a refusal never
  leaves a half-installed target;
* an existing file that differs refuses rather than overwrites, and a file
  that cannot even be read (binary, a directory) counts as differing rather
  than as a crash;
* the documents never drift from what they document: the repository's
  SKILL.md is byte-equal to the packaged one, and every `--flag` either
  document names is a flag the real parsers accept.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import ClassVar

import pytest
import yaml

from svarupa.cli import main
from svarupa.diagnostics import DiagnosticError
from svarupa.lock import LOCK_NAME
from svarupa.setup import TARGETS, Target, install
from svarupa.setup.ci_github import WORKFLOW, CiGithubTarget
from svarupa.setup.skill import SKILL_MD, SkillTarget

REPO = Path(__file__).resolve().parents[1]


# --- install mechanics ------------------------------------------------------


def test_skill_target_writes_the_skill_file_byte_exactly(tmp_path: Path) -> None:
    result = install(SkillTarget(), tmp_path, force=False)
    path = tmp_path / ".claude/skills/svarupa/SKILL.md"
    assert result.written == (path,)
    assert path.read_bytes() == SKILL_MD.encode("utf8")


def test_ci_target_writes_the_workflow_byte_exactly(tmp_path: Path) -> None:
    result = install(CiGithubTarget(), tmp_path, force=False)
    path = tmp_path / ".github/workflows/svarupa.yml"
    assert result.written == (path,)
    assert path.read_bytes() == WORKFLOW.encode("utf8")


def test_rerun_is_a_no_op_reported_as_unchanged(tmp_path: Path) -> None:
    install(SkillTarget(), tmp_path, force=False)
    result = install(SkillTarget(), tmp_path, force=False)
    assert result.written == ()
    assert len(result.unchanged) == 1


def test_an_existing_differing_file_refuses_with_sva_s_001(tmp_path: Path) -> None:
    path = tmp_path / ".claude/skills/svarupa/SKILL.md"
    path.parent.mkdir(parents=True)
    path.write_text("the user's own file\n", encoding="utf8")
    with pytest.raises(DiagnosticError) as exc:
        install(SkillTarget(), tmp_path, force=False)
    assert exc.value.diagnostic.code == "SVA-S-001"
    assert path.read_text(encoding="utf8") == "the user's own file\n", "it was overwritten"


def test_force_overwrites_a_differing_file(tmp_path: Path) -> None:
    path = tmp_path / ".github/workflows/svarupa.yml"
    path.parent.mkdir(parents=True)
    path.write_text("old\n", encoding="utf8")
    result = install(CiGithubTarget(), tmp_path, force=True)
    assert result.written == (path,)
    assert path.read_bytes() == WORKFLOW.encode("utf8")


class _TwoFiles(Target):
    """First file clean, second clashing: the order that exposes a partial write."""

    name: ClassVar[str] = "two"
    summary: ClassVar[str] = "test double"

    def files(self) -> tuple[tuple[str, str], ...]:
        return (("a/clean.txt", "clean\n"), ("b/clash.txt", "new\n"))

    def next_steps(self) -> tuple[str, ...]:
        return ()


def test_a_refusal_writes_nothing_at_all(tmp_path: Path) -> None:
    """The collision on the *second* file must stop the *first* being written.

    Checking collisions during the write loop instead of before it passes
    every single-file test and still half-installs a two-file target.
    """
    clash = tmp_path / "b/clash.txt"
    clash.parent.mkdir(parents=True)
    clash.write_text("theirs\n", encoding="utf8")
    with pytest.raises(DiagnosticError):
        install(_TwoFiles(), tmp_path, force=False)
    assert not (tmp_path / "a/clean.txt").exists(), "refused, but the first file was written"


def test_an_unreadable_existing_file_is_a_refusal_not_a_crash(tmp_path: Path) -> None:
    path = tmp_path / ".claude/skills/svarupa/SKILL.md"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"\xff\xfe\x00binary")
    with pytest.raises(DiagnosticError) as exc:
        install(SkillTarget(), tmp_path, force=False)
    assert exc.value.diagnostic.code == "SVA-S-001"


def test_a_directory_squatting_on_the_target_path_is_a_refusal(tmp_path: Path) -> None:
    (tmp_path / ".claude/skills/svarupa/SKILL.md").mkdir(parents=True)
    with pytest.raises(DiagnosticError) as exc:
        install(SkillTarget(), tmp_path, force=False)
    assert exc.value.diagnostic.code == "SVA-S-001"


def test_a_missing_dest_refuses_with_sva_s_002(tmp_path: Path) -> None:
    with pytest.raises(DiagnosticError) as exc:
        install(SkillTarget(), tmp_path / "nope", force=False)
    assert exc.value.diagnostic.code == "SVA-S-002"


def test_a_file_as_dest_refuses_with_sva_s_002(tmp_path: Path) -> None:
    file = tmp_path / "a-file"
    file.write_text("x", encoding="utf8")
    with pytest.raises(DiagnosticError) as exc:
        install(SkillTarget(), file, force=False)
    assert exc.value.diagnostic.code == "SVA-S-002"


# --- the CLI command --------------------------------------------------------


def test_cli_setup_installs_and_prints_next_steps(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["setup", "skill", "--dest", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "wrote" in out
    assert "next:" in out
    assert (tmp_path / ".claude/skills/svarupa/SKILL.md").exists()


def test_cli_setup_refusal_is_a_structured_diagnostic_and_exit_1(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["setup", "skill", "--dest", str(tmp_path / "nope")]) == 1
    err = capsys.readouterr().err
    assert "SVA-S-002" in err
    assert "Traceback" not in err


def test_registry_keys_match_their_classes_names() -> None:
    for key, cls in TARGETS.items():
        assert key == cls.name, f"TARGETS[{key!r}] is {cls.name!r}"


# --- document drift ---------------------------------------------------------


def test_the_repositorys_skill_file_equals_the_packaged_one() -> None:
    """Two hand-maintained copies of one document drift; only one is written
    by hand. The repository copy is generated from the constant, and this is
    the check that keeps that true."""
    repo_copy = (REPO / "skills/svarupa/SKILL.md").read_bytes()
    assert repo_copy == SKILL_MD.encode("utf8")


def _real_flags() -> set[str]:
    """Every long option the actual parsers accept, read from their help.

    Read from the running parsers rather than from a list kept here, because
    a list kept here is a third copy of the same facts.
    """
    import contextlib
    import io

    flags: set[str] = set()
    for args in (["--help"], ["setup", "--help"]):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), pytest.raises(SystemExit):
            main(args)
        flags.update(re.findall(r"--[a-z][a-z-]*", buf.getvalue()))
    return flags


def test_every_flag_the_documents_name_exists() -> None:
    """A document written from recall names flags that do not exist; this is
    the drift check that catches it. `--json` and `--update` are planned and
    absent, so a document naming them fails here today."""
    real = _real_flags()
    for name, text in (("SKILL.md", SKILL_MD), ("workflow", WORKFLOW)):
        named = set(re.findall(r"--[a-z][a-z-]*", text))
        assert named, f"{name} names no flags; the scan is broken"
        assert named <= real, (
            f"{name} names flags the CLI does not have: {sorted(named - real)}"
        )


def test_the_workflow_parses_as_yaml_and_names_the_real_lockfile() -> None:
    doc = yaml.safe_load(WORKFLOW)
    steps = doc["jobs"]["diff"]["steps"]
    assert any("svarupa" in str(s.get("run", "")) for s in steps)
    # Every lockfile path in the workflow, not any one occurrence. A bare
    # `LOCK_NAME in WORKFLOW` was satisfied by any single occurrence, so a
    # mutation of one of several survived it; so did pinning paths that
    # themselves appear twice. The workflow's own scratch file is the one
    # legitimate other name.
    lock_paths = re.findall(r"[\w./-]+\.lock\b", WORKFLOW)
    assert f":.svarupa/{LOCK_NAME}" in WORKFLOW, "the committed-base path is not LOCK_NAME"
    for token in lock_paths:
        base = token.rsplit("/", 1)[-1]
        assert base in {LOCK_NAME, "committed-base.lock"}, (
            f"the workflow names a lockfile that is not {LOCK_NAME}: {token}"
        )
    assert LOCK_NAME in SKILL_MD


def test_the_skill_names_the_artifact_files_that_actually_exist() -> None:
    """The artifact's file names come from emit; the skill document lists
    them. If emit renames one, this is what fails."""
    for name in ("index.html", "graph.json", "REPORT.md"):
        assert name in SKILL_MD, f"SKILL.md no longer mentions {name}"
