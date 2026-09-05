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

import sys
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


def test_the_repository_root_module_is_spelled_as_a_dot(tmp_path: Path) -> None:
    """The root module id is the empty string, which rendered as `module` plus
    a bare tab: a line whose entire meaning is trailing whitespace.

    Not a parser problem, an environment problem. Nearly every repository has
    architecture files at its root, so nearly every committed lockfile would
    carry such a line, and pre-commit's `trailing-whitespace` hook deletes it.
    """
    write(tmp_path, "top.py", "x = 1\n")
    text = lock_text(tmp_path)
    assert Record("module", (".",)) in Lockfile.parse(text).records
    assert not any(line.endswith("\t") for line in text.split("\n"))


def test_the_lockfile_survives_a_trailing_whitespace_hook(tmp_path: Path) -> None:
    """What the previous spelling failed. Measured before the fix: after the
    trim, parsing refused with SVA-L-002 and every CI diff would fail until
    someone regenerated."""
    write(tmp_path, "top.py", "x = 1\n")
    repo(tmp_path)
    text = lock_text(tmp_path)
    trimmed = "\n".join(line.rstrip() for line in text.split("\n"))
    assert Lockfile.parse(trimmed).records == Lockfile.parse(text).records


def test_a_lockfile_survives_its_own_parser(tmp_path: Path) -> None:
    repo(tmp_path)
    text = lock_text(tmp_path)
    assert Lockfile.parse(text).render() == text


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
    # It must NOT predict what the delta below will contain. The CLI decides
    # that, and the first version's guidance was false in the only place it was
    # ever printed: it said the drifted facts would appear below, directly
    # above a correct "No architectural change."
    assert "delta below" not in found[0].message
    assert not any("delta" in f for f in found[0].suggested_fixes)


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
    # Rename a module that really has code, so it survives the code-modules
    # filter: injecting a name with no files behind it would be dropped before
    # the collision check ever saw it.
    modules = dict(graph.modules)
    nodes = dict(graph.nodes)
    paths = set(graph.architecture_paths)
    sample = next(iter(modules.values()))
    modules["src/Core"] = sample
    for nid, node in list(nodes.items()):
        if nid.startswith("src/core/"):
            twin = nid.replace("src/core/", "src/Core/", 1)
            nodes[twin] = node
            paths.add(twin.split("#", 1)[0])
    colliding = replace(
        graph, modules=modules, nodes=nodes, architecture_paths=frozenset(paths)
    )

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
    lock = tmp_path / "x.lock"
    main([str(source), "--out", str(tmp_path / "seed"), "--lock"])
    lock.write_text((tmp_path / "seed" / LOCK_NAME).read_text(encoding="utf8"), encoding="utf8")
    capsys.readouterr()
    code = main([str(source), "--out", str(tmp_path / "out"), "--drift-base", str(lock)])
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


# --------------------------------------------------------------------------
# Review #10: the loading channel is part of the parser's boundary
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "content", "code"),
    [
        ("missing", None, "SVA-L-008"),
        ("empty", "", "SVA-L-007"),
        ("unstamped", "module\ta\n", "SVA-L-007"),
        ("conflict", "<<<<<<< HEAD\nmodule\ta\n", "SVA-L-001"),
        ("wrong_major", "# svarupa t\n# schema 9.0\n# grammars\nmodule\ta\n", "SVA-L-007"),
    ],
)
def test_a_hostile_base_lockfile_refuses_with_a_code(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    name: str,
    content: str | None,
    code: str,
) -> None:
    """The grammar was hardened for hostile bytes and stopped at the channel.

    Four of six inputs arrived as raw Python tracebacks, including the tool's
    own designed refusal for a mismatched schema: a message telling the user to
    regenerate, wrapped in a stack telling them about our call frames.
    """
    source = tmp_path / "repo"
    source.mkdir()
    repo(source)
    target = tmp_path / f"{name}.lock"
    if content is not None:
        target.write_text(content, encoding="utf8")

    assert main([str(source), "--out", str(tmp_path / "out"), "--diff", str(target)]) == 1
    captured = capsys.readouterr()
    assert code in captured.err
    assert "Traceback" not in captured.err


def test_a_directory_given_as_a_lockfile_refuses(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = tmp_path / "repo"
    source.mkdir()
    repo(source)
    assert main([str(source), "--out", str(tmp_path / "out"), "--diff", str(tmp_path)]) == 1
    assert "SVA-L-008" in capsys.readouterr().err


def test_a_non_utf8_lockfile_refuses(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = tmp_path / "repo"
    source.mkdir()
    repo(source)
    target = tmp_path / "utf16.lock"
    target.write_bytes("# svarupa 1\n".encode("utf-16"))
    assert main([str(source), "--out", str(tmp_path / "out"), "--diff", str(target)]) == 1
    assert "SVA-L-008" in capsys.readouterr().err


def test_a_bad_lockfile_refuses_before_the_scan(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The identical failure `claim()` was moved to the front to prevent, one
    wave later. On a large repository a CI job burned minutes before learning
    the base path was mistyped."""
    source = tmp_path / "repo"
    source.mkdir()
    repo(source)
    main([str(source), "--out", str(tmp_path / "out"), "--diff", str(tmp_path / "nope.lock")])
    captured = capsys.readouterr()
    assert captured.out == "", f"the scan ran before the refusal: {captured.out[:120]!r}"


# --------------------------------------------------------------------------
# Review #10: churn channels the verification table does not name
# --------------------------------------------------------------------------


def test_a_test_file_in_another_language_does_not_touch_the_lockfile(
    tmp_path: Path,
) -> None:
    """Demonstrated: one TypeScript **test** file, excluded from every record,
    changed the committed lockfile's grammar header while changing zero facts.

    Every byte of a committed artifact has to be a function of the facts it
    commits.
    """
    repo(tmp_path)
    before = lock_text(tmp_path)
    write(tmp_path, "tests/util.test.ts", "export const t = 1;\n")
    assert (tmp_path / "tests/util.test.ts").is_file(), "the fixture was not created"
    assert lock_text(tmp_path) == before


def test_a_real_source_file_in_another_language_does_register(tmp_path: Path) -> None:
    """The other side: the filter must not filter everything."""
    repo(tmp_path)
    before = lock_text(tmp_path)
    write(tmp_path, "web/app.ts", "export const x = 1;\n")
    after = lock_text(tmp_path)
    assert after != before
    assert "typescript@" in after


def test_a_config_only_directory_is_not_a_module(tmp_path: Path) -> None:
    """A directory of YAML is configuration, not a module. Nothing in it can
    produce a dep, so a module line for it is pure churn surface: the first
    workflow directory arrives as an architectural change."""
    repo(tmp_path)
    before = lock_text(tmp_path)
    write(tmp_path, ".github/workflows/ci.yml", "on: push\njobs: {}\n")
    write(tmp_path, "deploy/values.yaml", "replicas: 1\n")
    assert lock_text(tmp_path) == before


def test_a_directory_with_code_is_still_a_module(tmp_path: Path) -> None:
    """The baseline for the test above."""
    repo(tmp_path)
    write(tmp_path, "tools/__init__.py", "")
    write(tmp_path, "tools/run.py", "from ..src.core.model import Thing\n")
    assert "module\ttools" in lock_text(tmp_path)


def test_every_dependency_names_a_declared_module(tmp_path: Path) -> None:
    """Filtering modules must not leave a dep pointing at nothing."""
    repo(tmp_path)
    write(tmp_path, ".github/workflows/ci.yml", "on: push\n")
    records = lock_records(graph_of(tmp_path))
    declared = {f for r in records if r.kind == "module" for f in r.fields}
    for r in records:
        if r.kind == "dep":
            assert set(r.fields) <= declared, f"{r.render()} names an undeclared module"


# --------------------------------------------------------------------------
# Review #10: a wrong lockfile must not be written, and an empty one explains
# --------------------------------------------------------------------------


def test_a_repository_with_no_extractable_source_says_so(tmp_path: Path) -> None:
    """An empty lockfile is a claim: "this repository has no architecture".
    Committed silently, it becomes the base every future diff is measured
    against."""
    write(tmp_path, "README.md", "# hi\n")
    result = build_lock(graph_of(tmp_path), __version__)
    assert result.lockfile.records == ()
    assert [d.code for d in result.diagnostics] == ["SVA-L-010"]


def test_a_normal_repository_does_not_claim_emptiness(tmp_path: Path) -> None:
    repo(tmp_path)
    result = build_lock(graph_of(tmp_path), __version__)
    assert not [d for d in result.diagnostics if d.code == "SVA-L-010"]


def test_a_grammar_version_change_is_reported_in_the_delta() -> None:
    """Design §7.2 records grammar versions so a reviewer can tell a grammar
    bump from a code change. Recording them and never comparing them leaves
    exactly the ambiguity they were added to remove."""
    base = Lockfile.build("t", {"python": "0.25.0"}, [Record("module", ("a",))])
    head = Lockfile.build("t", {"python": "0.26.0"}, [Record("module", ("a",))])
    codes = [d.code for d in diff(base, head).diagnostics]
    assert codes == ["SVA-L-011"]


def test_an_unchanged_grammar_version_is_not_reported() -> None:
    """The baseline: it must not be a constant."""
    base = Lockfile.build("t", {"python": "0.25.0"}, [Record("module", ("a",))])
    head = Lockfile.build("t", {"python": "0.25.0"}, [Record("module", ("b",))])
    assert not [d for d in diff(base, head).diagnostics if d.code == "SVA-L-011"]


def test_an_unstamped_lockfile_refuses_before_the_scan(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`assert_diffable` refuses an unstamped file, which is right, and it does
    so at diff time, which is too late.

    Without this the load-time check could be deleted and the suite would stay
    green, because the later refusal produces the same code.
    """
    source = tmp_path / "repo"
    source.mkdir()
    repo(source)
    target = tmp_path / "unstamped.lock"
    target.write_text("module\ta\n", encoding="utf8")

    assert main([str(source), "--out", str(tmp_path / "out"), "--diff", str(target)]) == 1
    captured = capsys.readouterr()
    assert "SVA-L-007" in captured.err
    assert captured.out == "", f"the scan ran before the refusal: {captured.out[:120]!r}"


def test_a_lockfile_whose_build_errored_is_not_written(tmp_path: Path) -> None:
    """It was written first and the error only set the exit code, so a user who
    does not check exit codes had a known-wrong lockfile on disk, ready to
    commit as the base every future diff is measured against."""
    source = tmp_path / "repo"
    source.mkdir()
    repo(source)
    out = tmp_path / "out"

    from svarupa import cli as cli_mod

    real = cli_mod.build_lock

    def erroring(graph: Graph, version: str) -> object:
        from dataclasses import replace as dc_replace

        from svarupa.diagnostics import Diagnostic

        result = real(graph, version)
        bad = Diagnostic(
            code="SVA-L-004",
            severity=Severity.ERROR,
            message="synthetic collision",
            subject="a | A",
        )
        return dc_replace(result, diagnostics=(*result.diagnostics, bad))

    cli_mod.build_lock = erroring  # type: ignore[assignment]
    try:
        assert main([str(source), "--out", str(out), "--lock"]) == 1
    finally:
        cli_mod.build_lock = real  # type: ignore[assignment]

    assert not (out / LOCK_NAME).exists(), (
        "a lockfile whose own build reported an error was written to disk"
    )


def test_a_dangling_dependency_reference_is_detected(tmp_path: Path) -> None:
    """Cannot happen today: a dependency comes from an import and an import
    needs code at both ends. "Cannot happen" is what a check is for, and the
    module filter added this wave is exactly the kind of change that could
    break it.

    Driven through `build_lock` rather than by reimplementing the check here.
    An inline reimplementation would assert that the test agrees with itself.
    """
    from dataclasses import replace

    repo(tmp_path)
    graph = graph_of(tmp_path)
    # A dependency on a module with no code, which the filter therefore drops.
    broken = replace(graph, module_deps=(*graph.module_deps, ("src/core", "config")))

    result = build_lock(broken, __version__)
    codes = [d.code for d in result.diagnostics]
    assert "SVA-L-009" in codes, codes
    assert "config" in (result.diagnostics[codes.index("SVA-L-009")].subject or "")


def test_a_normal_repository_has_no_dangling_references(tmp_path: Path) -> None:
    """The baseline: the check must not be a constant."""
    repo(tmp_path)
    result = build_lock(graph_of(tmp_path), __version__)
    assert not [d for d in result.diagnostics if d.code == "SVA-L-009"]


# --------------------------------------------------------------------------
# The replay harness: real history, on a repository built for the purpose
# --------------------------------------------------------------------------


def git_repo(root: Path) -> list[str]:
    """A real git repository whose commits are the shapes that matter.

    Built here rather than pointing at a foreign checkout, so the harness is
    exercised in CI where no foreign repository exists. The measured corpus in
    `docs/reviews/2026-09-04-replay-corpus.md` is the real evidence; this keeps
    the machinery that produced it from rotting.
    """
    import subprocess

    def run(*args: str) -> None:
        subprocess.run(
            ["git", "-C", str(root), *args],
            check=True,
            capture_output=True,
            env={
                "GIT_AUTHOR_NAME": "t",
                "GIT_AUTHOR_EMAIL": "t@t",
                "GIT_COMMITTER_NAME": "t",
                "GIT_COMMITTER_EMAIL": "t@t",
                "PATH": "/usr/bin:/bin:/usr/local/bin",
            },
        )

    root.mkdir(parents=True, exist_ok=True)
    run("init", "-q", "-b", "main")
    subjects: list[str] = []

    repo(root)
    run("add", "-A")
    run("commit", "-qm", "initial")
    subjects.append("initial")

    # A refactor inside a module: no architectural change.
    target = root / "src/api/routes.py"
    target.write_text(
        target.read_text(encoding="utf8").replace("def show()", "def render()"),
        encoding="utf8",
    )
    run("add", "-A")
    run("commit", "-qm", "rename a local")
    subjects.append("rename a local")

    # A new module with a dependency: exactly the lines that describes.
    write(root, "src/billing/__init__.py", "")
    write(root, "src/billing/charge.py", "from ..core.model import Thing\n")
    run("add", "-A")
    run("commit", "-qm", "add billing")
    subjects.append("add billing")

    # Removing it again: the same lines, as removals.
    import shutil

    shutil.rmtree(root / "src/billing")
    run("add", "-A")
    run("commit", "-qm", "drop billing")
    subjects.append("drop billing")
    return subjects


def test_the_replay_harness_measures_real_history(tmp_path: Path) -> None:
    """Four commits: quiet, quiet, loud, loud-in-reverse."""
    import shutil

    if shutil.which("git") is None:  # pragma: no cover - git is always present here
        pytest.skip("git is not available")

    from svarupa.detect import ScanLimits
    from svarupa.lock import ROOT_MODULE

    _ = ROOT_MODULE
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    from replay_history import replay

    source = tmp_path / "repo"
    git_repo(source)
    report = replay(source, count=4, limits=ScanLimits())

    steps = {s.subject: s for s in report.steps}
    assert not [s for s in report.steps if s.error], [s.error for s in report.steps]
    assert len(steps) == 3, sorted(steps)

    assert steps["rename a local"].lines == 0, steps["rename a local"].added

    added = steps["add billing"]
    assert "+ module\tsrc/billing" in added.added
    assert "+ dep\tsrc/billing\tsrc/core" in added.added
    assert added.removed == ()

    dropped = steps["drop billing"]
    assert "- module\tsrc/billing" in dropped.removed
    assert dropped.added == ()


def test_the_replay_harness_does_not_touch_the_source_repository(tmp_path: Path) -> None:
    """`git worktree` would write under the source `.git`. Measuring someone's
    repository must not modify it, and that is a property worth asserting
    rather than trusting to the choice of command."""
    import shutil

    if shutil.which("git") is None:  # pragma: no cover
        pytest.skip("git is not available")

    from svarupa.detect import ScanLimits

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    from replay_history import replay

    source = tmp_path / "repo"
    git_repo(source)
    before = {
        str(p.relative_to(source)): p.stat().st_mtime_ns
        for p in sorted((source / ".git").rglob("*"))
        if p.is_file()
    }
    replay(source, count=4, limits=ScanLimits())
    after = {
        str(p.relative_to(source)): p.stat().st_mtime_ns
        for p in sorted((source / ".git").rglob("*"))
        if p.is_file()
    }
    assert before == after, "the replay modified the repository it was measuring"


# --------------------------------------------------------------------------
# Deployment records: the additive evolution the schema policy was built for
# --------------------------------------------------------------------------


def compose_repo(root: Path) -> None:
    repo(root)
    write(
        root,
        "docker-compose.yml",
        "services:\n"
        "  api:\n"
        "    build: .\n"
        "    depends_on: [mongo, redis]\n"
        "  mongo:\n"
        "    image: mongo:7\n"
        "  redis:\n"
        "    image: redis:7\n",
    )


def test_compose_services_become_lockfile_records(tmp_path: Path) -> None:
    """A new service appearing in a pull request is exactly the green line a
    reviewer wants."""
    compose_repo(tmp_path)
    text = lock_text(tmp_path)
    assert "service\tapi" in text
    assert "datastore\tmongo" in text
    assert "datastore\tredis" in text


def test_the_deployment_records_are_an_additive_bump(tmp_path: Path) -> None:
    """A 1.0 lockfile still diffs against a 1.1 one: the new records appear as
    plain adds, never a refusal. That is the whole point of publishing the
    kinds and arities before anything emitted them."""
    compose_repo(tmp_path)
    head = Lockfile.parse(lock_text(tmp_path))
    assert head.header.schema_minor >= 1

    old = Lockfile.parse("# svarupa old\n# schema 1.0\n# grammars python@0.25.0\nmodule\tsrc\n")
    delta = diff(old, head)  # must not raise
    added_kinds = {r.kind for r in delta.added}
    assert {"service", "datastore"} <= added_kinds


def test_a_new_service_is_one_line_in_the_delta(tmp_path: Path) -> None:
    compose_repo(tmp_path)
    base = Lockfile.parse(lock_text(tmp_path))
    write(
        tmp_path,
        "docker-compose.yml",
        (tmp_path / "docker-compose.yml").read_text(encoding="utf8")
        + "  worker:\n    build: ./worker\n",
    )
    delta = diff(base, Lockfile.parse(lock_text(tmp_path)))
    assert delta.added == (Record("service", ("worker",)),), delta.render()
    assert delta.removed == ()


def test_editing_compose_environment_does_not_churn_the_lockfile(tmp_path: Path) -> None:
    """Ports, env vars and volumes are deployment detail, not architecture."""
    compose_repo(tmp_path)
    before = lock_text(tmp_path)
    write(
        tmp_path,
        "docker-compose.yml",
        "services:\n"
        "  api:\n"
        "    build: .\n"
        "    ports: ['8000:8000']\n"
        "    environment:\n"
        "      DEBUG: 'true'\n"
        "    depends_on: [mongo, redis]\n"
        "  mongo:\n"
        "    image: mongo:7\n"
        "    volumes: ['data:/data/db']\n"
        "  redis:\n"
        "    image: redis:7\n"
        "volumes:\n"
        "  data:\n",
    )
    assert lock_text(tmp_path) == before
