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


# --- symlinks: a cloned repository is hostile input --------------------------


def test_a_dangling_symlink_is_refused_not_followed(tmp_path: Path) -> None:
    """`exists()` is False on a broken symlink, so the collision sweep never
    saw it and the write landed wherever the link pointed."""
    outside = tmp_path / "outside-file"
    repo = tmp_path / "repo"
    link = repo / ".claude/skills/svarupa/SKILL.md"
    link.parent.mkdir(parents=True)
    link.symlink_to(outside)
    with pytest.raises(DiagnosticError) as exc:
        install(SkillTarget(), repo, force=False)
    assert exc.value.diagnostic.code == "SVA-S-003"
    assert not outside.exists(), "the write followed the symlink out of the repo"


def test_a_symlink_to_a_real_file_is_refused_even_with_force(tmp_path: Path) -> None:
    """--force means replace your file, never follow your link: without this,
    SVA-S-001's own fix text walked the user into overwriting the target."""
    victim = tmp_path / "victim"
    victim.write_text("PRECIOUS USER DATA", encoding="utf8")
    repo = tmp_path / "repo"
    link = repo / ".claude/skills/svarupa/SKILL.md"
    link.parent.mkdir(parents=True)
    link.symlink_to(victim)
    for force in (False, True):
        with pytest.raises(DiagnosticError) as exc:
            install(SkillTarget(), repo, force=force)
        assert exc.value.diagnostic.code == "SVA-S-003"
    assert victim.read_text(encoding="utf8") == "PRECIOUS USER DATA"


def test_a_symlinked_directory_is_refused(tmp_path: Path) -> None:
    outside = tmp_path / "outside-dir"
    outside.mkdir()
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".github").symlink_to(outside)
    with pytest.raises(DiagnosticError) as exc:
        install(CiGithubTarget(), repo, force=False)
    assert exc.value.diagnostic.code == "SVA-S-003"
    assert list(outside.iterdir()) == [], "the write escaped through the directory symlink"


def test_a_readonly_dest_refuses_with_sva_s_004_not_a_traceback(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    repo.chmod(0o555)
    try:
        with pytest.raises(DiagnosticError) as exc:
            install(SkillTarget(), repo, force=False)
        assert exc.value.diagnostic.code == "SVA-S-004"
    finally:
        repo.chmod(0o755)


# --- the CLI command --------------------------------------------------------


def test_cli_collision_refuses_and_only_force_overwrites(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Through main(), not install(): hardcoding force=True in the CLI wiring
    passed the entire suite while every install()-level test stayed green."""
    path = tmp_path / ".claude/skills/svarupa/SKILL.md"
    path.parent.mkdir(parents=True)
    path.write_text("mine\n", encoding="utf8")
    assert main(["setup", "skill", "--dest", str(tmp_path)]) == 1
    assert "SVA-S-001" in capsys.readouterr().err
    assert path.read_text(encoding="utf8") == "mine\n", "the CLI overwrote without --force"
    assert main(["setup", "skill", "--dest", str(tmp_path), "--force"]) == 0
    assert path.read_bytes() == SKILL_MD.encode("utf8")


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


# --- the adopter's journey, end to end ---------------------------------------


def _tiny_repo(root: Path) -> None:
    root.mkdir(parents=True)
    (root / "pkg").mkdir()
    (root / "pkg" / "__init__.py").write_text("", encoding="utf8")
    (root / "pkg" / "a.py").write_text("from pkg import b\n", encoding="utf8")
    (root / "pkg" / "b.py").write_text("x = 1\n", encoding="utf8")


def test_a_fresh_clone_of_an_adopted_repository_can_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The state the product's own instructions create must be runnable.

    next_steps says: run `svarupa . --lock`, commit `.svarupa/architecture.lock`.
    A fresh clone (and every CI checkout) then holds `.svarupa/` with only the
    lockfile and no marker, and that exact state made both the generated
    workflow and every other contributor's first run refuse with SVA-E-001,
    after the full scan. The lockfile is a file svarupa owns but never clears,
    not a foreign file.
    """
    adopter = tmp_path / "adopter"
    _tiny_repo(adopter)
    assert main([str(adopter), "--lock"]) == 0
    lock = (adopter / ".svarupa" / LOCK_NAME).read_bytes()

    clone = tmp_path / "clone"
    _tiny_repo(clone)
    (clone / ".svarupa").mkdir()
    (clone / ".svarupa" / LOCK_NAME).write_bytes(lock)
    capsys.readouterr()
    assert main([str(clone)]) == 0, "the adopted state refuses to run"
    assert (clone / ".svarupa" / LOCK_NAME).read_bytes() == lock, "the lockfile was cleared"


def test_the_output_directory_is_not_scanned_as_input(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A second run must see the same repository the first did, or each run is
    a function of the previous one's artifact."""
    repo = tmp_path / "repo"
    _tiny_repo(repo)
    assert main([str(repo), "--lock"]) == 0
    first = (repo / ".svarupa" / LOCK_NAME).read_bytes()
    capsys.readouterr()
    assert main([str(repo), "--lock"]) == 0
    assert (repo / ".svarupa" / LOCK_NAME).read_bytes() == first
    # The lockfile alone cannot detect this (the grammar-language filter
    # already keeps artifact files out of it), and today's artifact happens to
    # contain only extensions the classifier skips, which is a coincidence of
    # the extension tables, not the guarantee. Plant a file that WOULD
    # classify: the exclusion, not the file types, must keep it out.
    from svarupa.detect import ScanLimits, detect

    (repo / ".svarupa" / "planted.py").write_text("import os\n", encoding="utf8")
    scan = detect(str(repo), ScanLimits())
    scanned = [f.path for f in scan.files if f.path.split("/")[0] == ".svarupa"]
    assert not scanned, f"the output directory was scanned as input: {scanned}"


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
    for args in (["--help"], ["setup", "--help"], ["query", "--help"]):
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
    # Operational content the review demonstrated was outside every test's
    # reach: the fetch depth its own comment says cannot be 1, the pinned
    # install (unpinned, a future schema bump breaks every adopter's CI and a
    # PyPI squatter's "latest" is what gets installed), the summary written
    # before the exit code is re-raised, the verified base commit, and the
    # four-backtick fence that repo-derived ``` cannot terminate.
    checkout = next(s for s in steps if "checkout" in str(s.get("uses", "")))
    assert checkout["with"]["fetch-depth"] == 0
    from svarupa import __version__

    assert f"svarupa=={__version__}" in WORKFLOW, "the install is not pinned"
    assert 'exit "$code"' in WORKFLOW, "the exit code is not re-raised after the summary"
    assert WORKFLOW.index("GITHUB_STEP_SUMMARY") < WORKFLOW.index('exit "$code"')
    # The exact commit-verification line, not any `cat-file`: the
    # file-existence check is also a cat-file, so a substring of the family
    # was satisfied with the commit verification deleted.
    assert 'git cat-file -e "$BASE_SHA^{commit}"' in WORKFLOW, (
        "the base commit is not verified before the first-adoption fallback"
    )
    assert "````" in WORKFLOW, "the summary fence is terminable by repo-derived ```"


def test_the_skill_names_the_artifact_files_that_actually_exist() -> None:
    """The artifact's file names come from emit; the skill document lists
    them. If emit renames one, this is what fails."""
    for name in ("index.html", "graph.json", "REPORT.md"):
        assert name in SKILL_MD, f"SKILL.md no longer mentions {name}"
