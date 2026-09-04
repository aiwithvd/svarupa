"""P1-7: the lockfile as a committed, diffable artifact.

The format's whole promise is asymmetric stability: a refactor **inside** a
module produces no diff, and a new cross-module dependency produces exactly one
line. Both halves need testing, because a format that never changes is as
useless as one that always does.

A promoted decision says a gate must be able to fail. Spike 0's churn gate was
tautological, so every stability test here first asserts that its edit actually
mutated the tree, and each is paired with a test that the same machinery *does*
report a real change.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from svarupa import __version__
from svarupa.build import Graph, build
from svarupa.cli import main
from svarupa.detect import detect
from svarupa.diagnostics import Severity
from svarupa.emit import MARKER, OWNED_DIRS, OWNED_FILES
from svarupa.extract import declared_dependencies, extract
from svarupa.lock import (
    LOCK_NAME,
    Lockfile,
    Record,
    build_lock,
    diff,
    drift_check,
    lock_records,
)


def write(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf8")


def repo(root: Path) -> None:
    write(root, "src/__init__.py", "")
    write(root, "src/api/__init__.py", "")
    write(
        root,
        "src/api/routes.py",
        "from ..core.model import Thing\n\n\ndef show():\n    return Thing\n",
    )
    write(root, "src/core/__init__.py", "")
    write(root, "src/core/model.py", "class Thing:\n    pass\n")
    write(root, "src/worker/__init__.py", "")
    write(root, "src/worker/job.py", "from ..core.model import Thing\n")


def graph_of(root: Path) -> Graph:
    scan = detect(root)
    return build(scan, extract(scan, declared_dependencies(scan)), strict=False)


def lock_text(root: Path) -> str:
    return build_lock(graph_of(root), __version__).lockfile.render()


# --------------------------------------------------------------------------
# What the lockfile records, and what it refuses to
# --------------------------------------------------------------------------


def test_the_lockfile_carries_no_line_numbers(tmp_path: Path) -> None:
    """Line numbers are the most volatile data in the system: any edit above a
    record shifts them and churns a committed file."""
    repo(tmp_path)
    for record in lock_records(graph_of(tmp_path)):
        for field in record.fields:
            assert not any(part.isdigit() for part in field.split(":")[1:]), record
    text = lock_text(tmp_path)
    assert ":1" not in text and ":12" not in text


def test_no_community_identity_reaches_the_lockfile(tmp_path: Path) -> None:
    """The single most important assertion in the file.

    Communities are chaotically sensitive to input perturbation: one added
    import flipped 25-38% of assignments on real repositories. A lockfile keyed
    on them would churn on every pull request, which is the failure the whole
    structural-identity decision exists to prevent.
    """
    from svarupa.cluster import cluster

    repo(tmp_path)
    graph = graph_of(tmp_path)
    clustering = cluster(graph)
    assert clustering.communities, "fixture produced no communities, so nothing was tested"

    text = lock_text(tmp_path)
    for community in clustering.communities:
        assert community.fingerprint() not in text
    # Every field must be a module id the graph actually declared.
    for record in lock_records(graph):
        for field in record.fields:
            assert field in graph.modules, f"{field!r} is not a structural module id"


def test_the_header_records_only_the_grammars_this_scan_used(tmp_path: Path) -> None:
    """A grammar patch release can change extraction output, so the versions
    have to be in the diff. But stamping grammars the scan never used would
    churn the header when an unrelated language is added."""
    repo(tmp_path)
    header = build_lock(graph_of(tmp_path), __version__).lockfile.header
    assert dict(header.grammars_tuple).keys() == {"python"}
    assert "typescript" not in header.render()[2]


def test_a_python_and_typescript_repo_records_both(tmp_path: Path) -> None:
    """The other half of the test above: the filter must not filter everything.

    Without this, a bug returning an empty grammar set would pass the previous
    test's spirit while recording nothing.
    """
    repo(tmp_path)
    write(tmp_path, "web/index.ts", "export const x = 1;\n")
    header = build_lock(graph_of(tmp_path), __version__).lockfile.header
    assert dict(header.grammars_tuple).keys() == {"python", "typescript"}


def test_the_repository_root_module_round_trips(tmp_path: Path) -> None:
    """The repo root is a real module and its id is the empty string, so it
    renders as `module` plus a tab plus nothing.

    A committed file's parser has to survive its own output, and an empty
    trailing field is exactly the shape a naive `split` or `strip` loses.
    """
    write(tmp_path, "top.py", "x = 1\n")
    text = lock_text(tmp_path)
    assert "module\t\n" in text or text.rstrip("\n").endswith("module\t")
    parsed = Lockfile.parse(text)
    assert Record("module", ("",)) in parsed.records
    assert parsed.render() == text, "the lockfile does not survive its own parser"


# --------------------------------------------------------------------------
# Stability: no diff for a refactor, exactly one line for a real change
# --------------------------------------------------------------------------


def test_renaming_a_local_produces_no_diff(tmp_path: Path) -> None:
    repo(tmp_path)
    before = lock_text(tmp_path)
    target = tmp_path / "src/api/routes.py"
    original = target.read_text(encoding="utf8")
    target.write_text(original.replace("def show()", "def render()"), encoding="utf8")
    assert target.read_text(encoding="utf8") != original, "the edit did not mutate the tree"
    assert lock_text(tmp_path) == before


def test_adding_a_docstring_produces_no_diff(tmp_path: Path) -> None:
    """Shifts every line number in the file, which is the point."""
    repo(tmp_path)
    before = lock_text(tmp_path)
    target = tmp_path / "src/core/model.py"
    original = target.read_text(encoding="utf8")
    target.write_text('"""A thing."""\n\n\n' + original, encoding="utf8")
    assert target.read_text(encoding="utf8") != original, "the edit did not mutate the tree"
    assert lock_text(tmp_path) == before


def test_reformatting_produces_no_diff(tmp_path: Path) -> None:
    repo(tmp_path)
    before = lock_text(tmp_path)
    target = tmp_path / "src/worker/job.py"
    original = target.read_text(encoding="utf8")
    target.write_text(original.replace("\n", "\n\n"), encoding="utf8")
    assert target.read_text(encoding="utf8") != original, "the edit did not mutate the tree"
    assert lock_text(tmp_path) == before


def test_adding_a_file_inside_an_existing_module_produces_no_diff(tmp_path: Path) -> None:
    """A new file is not an architectural fact; a new *module* is."""
    repo(tmp_path)
    before = lock_text(tmp_path)
    write(tmp_path, "src/core/helper.py", "def helper():\n    pass\n")
    assert lock_text(tmp_path) == before


def test_a_new_cross_module_import_produces_exactly_one_line(tmp_path: Path) -> None:
    """The other direction. A format that never changes is as useless as one
    that always does."""
    repo(tmp_path)
    base = Lockfile.parse(lock_text(tmp_path))
    write(
        tmp_path,
        "src/worker/job.py",
        "from ..core.model import Thing\nfrom ..api.routes import show\n",
    )
    head = Lockfile.parse(lock_text(tmp_path))

    delta = diff(base, head)
    assert delta.removed == ()
    assert delta.added == (Record("dep", ("src/worker", "src/api")),), delta.render()


def test_a_new_module_produces_its_module_line(tmp_path: Path) -> None:
    repo(tmp_path)
    base = Lockfile.parse(lock_text(tmp_path))
    write(tmp_path, "src/billing/__init__.py", "")
    write(tmp_path, "src/billing/charge.py", "from ..core.model import Thing\n")
    head = Lockfile.parse(lock_text(tmp_path))

    delta = diff(base, head)
    assert Record("module", ("src/billing",)) in delta.added
    assert Record("dep", ("src/billing", "src/core")) in delta.added
    assert delta.removed == ()


def test_deleting_a_dependency_shows_as_a_removal(tmp_path: Path) -> None:
    repo(tmp_path)
    base = Lockfile.parse(lock_text(tmp_path))
    write(tmp_path, "src/worker/job.py", "VALUE = 1\n")
    head = Lockfile.parse(lock_text(tmp_path))

    delta = diff(base, head)
    assert Record("dep", ("src/worker", "src/core")) in delta.removed
    assert delta.added == ()


# --------------------------------------------------------------------------
# The diff engine
# --------------------------------------------------------------------------


def test_an_unchanged_repository_produces_an_empty_delta(tmp_path: Path) -> None:
    """The baseline. Without it every delta test could pass on an engine that
    reports everything as changed."""
    repo(tmp_path)
    lf = Lockfile.parse(lock_text(tmp_path))
    delta = diff(lf, lf)
    assert delta.empty
    assert delta.render() == "No architectural change."


def test_the_delta_renders_removals_before_additions_within_a_kind() -> None:
    """The same delta must always render identically, or a PR comment churns
    on nothing."""
    base = Lockfile.build("t", {}, [Record("dep", ("a", "b")), Record("module", ("a",))])
    head = Lockfile.build("t", {}, [Record("dep", ("a", "c")), Record("module", ("a",))])
    text = diff(base, head).render()
    assert text.index("- dep\ta\tb") < text.index("+ dep\ta\tc")


def test_a_changed_fact_is_one_removal_and_one_addition() -> None:
    """Records are facts, not objects with identity, so there is no "modified".
    That is what lets `git diff` render an architecture change natively."""
    base = Lockfile.build("t", {}, [Record("dep", ("a", "b"))])
    head = Lockfile.build("t", {}, [Record("dep", ("a", "c"))])
    delta = diff(base, head)
    assert delta.added == (Record("dep", ("a", "c")),)
    assert delta.removed == (Record("dep", ("a", "b")),)


def test_an_unknown_record_kind_diffs_opaquely_and_says_so() -> None:
    """Forward compatibility: a P2 record type must not become a flag day for
    everyone holding a P1 lockfile."""
    base = Lockfile.parse("# svarupa t\n# schema 1.0\n# grammars\nmodule\ta\n")
    head = Lockfile.parse(
        "# svarupa t\n# schema 1.3\n# grammars\nmodule\ta\nquantum_flux\tzz\n"
    )
    delta = diff(base, head)
    assert delta.added == (Record("quantum_flux", ("zz",)),)
    assert delta.unknown_kinds == frozenset({"quantum_flux"})
    assert [d.code for d in delta.diagnostics] == ["SVA-L-005"]
    assert "quantum_flux" in delta.render()


def test_a_known_kind_does_not_report_as_unknown() -> None:
    """The other side of the test above: `unknown_kinds` must not be a constant."""
    base = Lockfile.build("t", {}, [Record("module", ("a",))])
    head = Lockfile.build("t", {}, [Record("module", ("a",)), Record("dep", ("a", "b"))])
    assert diff(base, head).unknown_kinds == frozenset()


def test_a_major_schema_difference_refuses_to_diff() -> None:
    """A field's meaning may have changed, so a delta would be nonsense."""
    from svarupa.lock import SchemaMismatch

    base = Lockfile.parse("# svarupa t\n# schema 1.0\n# grammars\nmodule\ta\n")
    head = Lockfile.parse("# svarupa t\n# schema 2.0\n# grammars\nmodule\ta\n")
    with pytest.raises(SchemaMismatch):
        diff(base, head)


# --------------------------------------------------------------------------
# Base drift: the failure that misattributes other people's changes
# --------------------------------------------------------------------------


def test_a_stale_base_lockfile_is_reported_as_drift() -> None:
    """Both inputs describe the same commit: one is what was committed, one is
    what the code there actually produces. Any difference will appear in the
    head delta as though this change caused it."""
    committed = Lockfile.build("t", {}, [Record("module", ("api",))])
    regenerated = Lockfile.build(
        "t", {}, [Record("module", ("api",)), Record("dep", ("api", "auth"))]
    )
    found = drift_check(committed, regenerated)
    assert [d.code for d in found] == ["SVA-L-006"]
    assert found[0].severity is Severity.WARNING
    assert "1 fact(s) missing from it" in found[0].message
    assert "as though this change caused them" in found[0].message


def test_a_current_base_lockfile_reports_no_drift() -> None:
    """The baseline. Without it the drift check could be a constant warning."""
    lf = Lockfile.build("t", {}, [Record("module", ("api",))])
    assert drift_check(lf, lf) == ()


def test_drift_counts_both_directions() -> None:
    """A stale base can be missing facts *and* claim facts that are gone."""
    committed = Lockfile.build("t", {}, [Record("module", ("gone",))])
    regenerated = Lockfile.build("t", {}, [Record("module", ("new",))])
    message = drift_check(committed, regenerated)[0].message
    assert "1 fact(s) missing from it" in message
    assert "1 fact(s) it still claims" in message


# --------------------------------------------------------------------------
# Collisions must diagnose, never merge
# --------------------------------------------------------------------------


def test_a_case_collision_is_diagnosed_rather_than_merged() -> None:
    """Two ids that differ only by case are distinct on Linux and one file on a
    case-insensitive volume. Silently merging makes a committed file wrong."""
    from svarupa.identity import collision_check

    found = collision_check(["src/Utils", "src/utils"])
    assert [d.code for d in found] == ["SVA-L-004"]
    assert found[0].severity is Severity.ERROR


def test_a_clean_module_set_produces_no_collision_diagnostics(tmp_path: Path) -> None:
    """The baseline for the test below."""
    repo(tmp_path)
    result = build_lock(graph_of(tmp_path), __version__)
    assert result.diagnostics == (), [d.render() for d in result.diagnostics]


def test_build_lock_actually_runs_the_collision_check(tmp_path: Path) -> None:
    """Wiring, and the only test here that can prove it.

    The previous version asserted an empty diagnostic list on a clean fixture,
    which passes whether or not the check runs. A mutation replacing the call
    with `()` survived the whole suite. Colliding ids are injected rather than
    created on disk, because the collision this catches is one a
    case-insensitive filesystem cannot represent: the two paths would be one
    file on the machine running the test.
    """
    from dataclasses import replace

    repo(tmp_path)
    graph = graph_of(tmp_path)
    modules = dict(graph.modules)
    sample = next(iter(modules.values()))
    modules["src/Core"] = sample
    modules["src/core"] = sample
    colliding = replace(graph, modules=modules)

    result = build_lock(colliding, __version__)
    codes = [d.code for d in result.diagnostics]
    assert codes == ["SVA-L-004"], codes
    assert result.diagnostics[0].severity is Severity.ERROR
    assert "src/Core" in result.diagnostics[0].subject or ""


# --------------------------------------------------------------------------
# The lockfile is committed; the rest of the artifact is not
# --------------------------------------------------------------------------


def test_emit_never_clears_the_committed_lockfile() -> None:
    """`emit` owns the artifact directory and clears it to a declared set.

    The lockfile lives in that directory and is the one file there meant to be
    committed, so its absence from the cleared set is load-bearing. Asserted
    rather than left to luck, because the two decisions were made a wave apart.
    """
    assert LOCK_NAME not in OWNED_FILES
    assert LOCK_NAME not in OWNED_DIRS
    assert LOCK_NAME != MARKER


def test_a_written_lockfile_survives_a_later_artifact_rebuild(tmp_path: Path) -> None:
    """The same claim, end to end rather than by inspection."""
    source = tmp_path / "repo"
    source.mkdir()
    repo(source)
    out = tmp_path / "out"

    assert main([str(source), "--out", str(out), "--lock"]) == 0
    lock = out / LOCK_NAME
    text = lock.read_text(encoding="utf8")
    assert text.startswith("# svarupa ")

    assert main([str(source), "--out", str(out)]) == 0
    assert lock.is_file(), "rebuilding the artifact deleted the committed lockfile"
    assert lock.read_text(encoding="utf8") == text


# --------------------------------------------------------------------------
# The CLI surface
# --------------------------------------------------------------------------


def test_the_cli_prints_a_delta_against_a_base_lockfile(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = tmp_path / "repo"
    source.mkdir()
    repo(source)
    out = tmp_path / "out"
    main([str(source), "--out", str(out), "--lock"])
    base = tmp_path / "base.lock"
    base.write_text((out / LOCK_NAME).read_text(encoding="utf8"), encoding="utf8")

    write(
        source,
        "src/worker/job.py",
        "from ..core.model import Thing\nfrom ..api.routes import show\n",
    )
    capsys.readouterr()
    assert main([str(source), "--out", str(out), "--diff", str(base)]) == 0
    printed = capsys.readouterr().out
    assert "+ dep\tsrc/worker\tsrc/api" in printed
    assert "1 added, 0 removed" in printed


def test_the_cli_leads_with_drift_before_the_delta(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A stale base attributes other commits' changes to this one, so the
    reader has to be told before they read the numbers."""
    source = tmp_path / "repo"
    source.mkdir()
    repo(source)
    out = tmp_path / "out"
    main([str(source), "--out", str(out), "--lock"])
    current = (out / LOCK_NAME).read_text(encoding="utf8")

    regenerated = tmp_path / "regen.lock"
    regenerated.write_text(current, encoding="utf8")
    stale = tmp_path / "stale.lock"
    stale.write_text(
        "\n".join(line for line in current.splitlines() if line != "dep\tsrc/worker\tsrc/core")
        + "\n",
        encoding="utf8",
    )
    assert stale.read_text(encoding="utf8") != current, "the fixture is not actually stale"

    capsys.readouterr()
    main(
        [
            str(source),
            "--out",
            str(out),
            "--diff",
            str(stale),
            "--drift-base",
            str(regenerated),
        ]
    )
    printed = capsys.readouterr().out
    assert "SVA-L-006" in printed
    assert printed.index("SVA-L-006") < printed.index("No architectural change."), (
        "the delta was shown before the warning that it is misattributed"
    )
    # And with drift accounted for, this change is correctly reported as nothing.
    assert "No architectural change." in printed


def test_drift_base_without_diff_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A flag that silently does nothing is worse than one that refuses."""
    source = tmp_path / "repo"
    source.mkdir()
    repo(source)
    code = main(
        [str(source), "--out", str(tmp_path / "out"), "--drift-base", str(tmp_path / "x.lock")]
    )
    assert code == 1
    assert "needs --diff" in capsys.readouterr().out


def test_without_any_lock_flag_no_lockfile_is_written(tmp_path: Path) -> None:
    """The lockfile is committed, so writing one uninvited would put a file
    into someone's repository they did not ask for."""
    source = tmp_path / "repo"
    source.mkdir()
    repo(source)
    out = tmp_path / "out"
    main([str(source), "--out", str(out)])
    assert not (out / LOCK_NAME).exists()


# --------------------------------------------------------------------------
# Byte identity: the promise the whole format rests on
# --------------------------------------------------------------------------


@pytest.mark.determinism
def test_the_lockfile_is_byte_identical_across_hash_seeds_and_locales(
    tmp_path: Path,
) -> None:
    """The design's headline promise: identical on a laptop and in CI.

    Cross-process, because the churn sources that matter are: `PYTHONHASHSEED`
    is fixed at interpreter start, and locale affects collation. Both are
    varied here. `LC_ALL=tr_TR.UTF-8` is included deliberately: Turkish
    case-folding maps `I` to a dotless form, so any accidental use of
    locale-aware casing or collation shows up there and nowhere else.

    Same-process repetition would assert only that Python iterates identical
    objects identically, which a promoted decision records as no test at all.
    """
    import os
    import subprocess
    import sys

    repo(tmp_path)
    write(tmp_path, "src/écafé/__init__.py", "")
    write(tmp_path, "src/écafé/mod.py", "from ..core.model import Thing\n")

    script = (
        f"import sys; sys.path.insert(0, {str(Path(__file__).resolve().parents[1])!r})\n"
        "from svarupa.detect import detect\n"
        "from svarupa.extract import extract, declared_dependencies\n"
        "from svarupa.build import build\n"
        "from svarupa.lock import build_lock\n"
        f"s = detect({str(tmp_path)!r})\n"
        "g = build(s, extract(s, declared_dependencies(s)), strict=False)\n"
        "sys.stdout.buffer.write(build_lock(g, 'test').lockfile.render().encode())\n"
    )
    outputs: set[bytes] = set()
    for seed, locale in (("0", "C"), ("1", "en_US.UTF-8"), ("4242", "tr_TR.UTF-8")):
        proc = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            env={**os.environ, "PYTHONHASHSEED": seed, "LC_ALL": locale},
            check=True,
        )
        outputs.add(proc.stdout)
    assert len(outputs) == 1, f"the lockfile differed across environments ({len(outputs)})"
    only = next(iter(outputs))
    assert only.strip(), "the subprocess produced no lockfile to compare"
    assert b"module\tsrc/" in only, "the fixture produced no module records"


@pytest.mark.determinism
def test_a_non_ascii_module_name_is_recorded_in_one_normalization_form(
    tmp_path: Path,
) -> None:
    """macOS hands back NFD, Linux NFC. Without normalizing at the boundary the
    first non-ASCII path breaks byte identity between the two."""
    import unicodedata

    repo(tmp_path)
    write(tmp_path, "src/café/__init__.py", "")
    text = lock_text(tmp_path)
    module_lines = [ln for ln in text.splitlines() if ln.startswith("module\t")]
    for line in module_lines:
        assert line == unicodedata.normalize("NFC", line), (
            f"{line!r} is not NFC, so this lockfile differs by platform"
        )
    assert any("caf" in ln for ln in module_lines), "the fixture module is missing"
