"""Lockfile grammar tests.

Written against the four properties the format must have (design 7.1). Each
test names the failure it is defending against, because a test whose purpose
is forgotten gets deleted during the next refactor.
"""

from __future__ import annotations

import difflib
import re

import pytest

from svarupa.diagnostics import DiagnosticError
from svarupa.lock import (
    Lockfile,
    Record,
    SchemaMismatch,
    collision_check,
    dep_record,
    escape_field,
    module_record,
    unescape_field,
)

TOOL = "0.1.0.dev0"
GRAMMARS = {"python": "0.25.0", "typescript": "0.23.2"}


def build(*records: Record) -> Lockfile:
    return Lockfile.build(TOOL, GRAMMARS, records)


# --------------------------------------------------------------------------
# Property 3: real escaping
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        "plain/module",
        "with space/mod",
        "comma, separated",
        "arrow -> looking",  # the delimiter of the format we rejected
        "tab\there",
        "newline\nhere",
        "back\\slash",
        "carriage\rreturn",
        "\\t literal backslash-t",  # must not round-trip into a real tab
        "café/naïve",
        "emoji/🚀/path",
        "",
    ],
)
def test_field_roundtrip(raw: str) -> None:
    assert unescape_field(escape_field(raw)) == raw


def test_escaped_field_contains_no_raw_delimiter() -> None:
    """A field must never emit a bare tab or newline: that would split the record."""
    nasty = "a\tb\nc\rd\\e"
    esc = escape_field(nasty)
    assert "\t" not in esc
    assert "\n" not in esc
    assert "\r" not in esc


def test_paths_with_delimiters_survive_a_full_roundtrip() -> None:
    """POSIX paths may legally contain spaces, commas, and even ' -> '.

    Asserts the full record set, not `records[0] or records[1]`: an `or`
    would pass while one of the two records was mangled.
    """
    weird = "src/my project, v2/a -> b/mod.py"
    lf = build(module_record(weird), dep_record(weird, "src/other"))
    back = Lockfile.parse(lf.render())
    assert set(back.records) == set(lf.records)
    assert all(r.fields[0] == weird for r in back.records)
    assert back.render() == lf.render()


# --------------------------------------------------------------------------
# Property 2: one fact per line
# --------------------------------------------------------------------------


def test_adding_one_dependency_adds_exactly_one_line() -> None:
    """The whole reason the lockfile is committed.

    Design 7.1 promises a reviewer sees `billing -> auth` appear as a green
    line. An aggregated `module api -> auth, billing` form would render this as
    one removed line plus one added line with a comma list to eyeball.
    """
    before = build(
        module_record("api"),
        module_record("auth"),
        module_record("billing"),
        dep_record("api", "auth"),
    )
    after = build(
        module_record("api"),
        module_record("auth"),
        module_record("billing"),
        dep_record("api", "auth"),
        dep_record("billing", "auth"),
    )
    # A real positional diff, not set membership. Membership would report one
    # added line even if the serializer had also duplicated an existing record
    # or reordered the whole file, both of which a reviewer would see as noise.
    diff = list(
        difflib.unified_diff(
            before.render().splitlines(),
            after.render().splitlines(),
            lineterm="",
            n=0,
        )
    )
    added = [d[1:] for d in diff if d.startswith("+") and not d.startswith("+++")]
    removed = [d[1:] for d in diff if d.startswith("-") and not d.startswith("---")]

    assert added == ["dep\tbilling\tauth"], f"expected one added line, got {added}"
    assert removed == [], f"expected no removed lines, got {removed}"


# --------------------------------------------------------------------------
# Canonical ordering / determinism
# --------------------------------------------------------------------------


def test_input_order_does_not_affect_output() -> None:
    recs = [module_record("z"), module_record("a"), dep_record("z", "a")]
    assert build(*recs).render() == build(*reversed(recs)).render()


def test_duplicates_collapse() -> None:
    """Compares the whole render, not a substring count.

    `count("\\nmodule\\ta")` would over-count against a `module ab` record
    (prefix collision) and ignores dep records entirely.
    """
    lf = build(
        module_record("a"), module_record("a"), dep_record("a", "b"), dep_record("a", "b")
    )
    body = [ln for ln in lf.render().splitlines() if not ln.startswith("#")]
    assert body == ["dep\ta\tb", "module\ta"]


def test_prefix_collision_does_not_confuse_dedup() -> None:
    lf = build(module_record("a"), module_record("ab"), module_record("a"))
    body = [ln for ln in lf.render().splitlines() if not ln.startswith("#")]
    assert body == ["module\ta", "module\tab"]


def test_sorting_is_codepoint_not_locale() -> None:
    """Turkish locale folds i/I differently; sorting must not care."""
    lf = build(module_record("Illinois"), module_record("izmir"), module_record("Izmir"))
    fields = [r.fields[0] for r in lf.records]
    assert fields == sorted(fields)


# Matches every form `Evidence.__str__` can emit, plus the GitHub #L style.
# The first version of this test only caught `path:44` and let the range form
# `src/a.py:44-71` -- which is what the code actually produces -- straight
# through, against a corpus that contained no colons at all.
_LINE_REF = re.compile(r":\d+(-\d+)?$|#L\d+")


def test_line_number_shaped_fields_are_rejected_by_the_detector() -> None:
    """First: prove the detector can fail. R2-1."""
    for bad in ("src/a.py:44", "src/a.py:44-71", "src/a.py#L44"):
        assert _LINE_REF.search(bad), f"detector missed {bad!r}"
    for good in ("src/a.py", "src/api", "POST /refunds/{id}", "C:/win/path"):
        assert not _LINE_REF.search(good), f"detector false-positived on {good!r}"


def test_no_line_numbers_anywhere() -> None:
    """Property 1: facts only.

    Any edit above a record would shift a line number and churn the lockfile,
    contradicting the stability guarantee. Evidence lives in graph.json.
    """
    lf = build(
        module_record("src/api"),
        dep_record("src/api", "src/db"),
        Record("endpoint", ("POST /refunds/{id}", "src/billing")),
        Record("datastore", ("postgres",)),
    )
    body = [ln for ln in lf.render().splitlines() if not ln.startswith("#")]
    assert body, "corpus must actually contain records to inspect"
    for line in body:
        for fld in line.split("\t")[1:]:
            assert not _LINE_REF.search(fld), f"lockfile record carries a line number: {line!r}"


# --------------------------------------------------------------------------
# Property 4: evolution policy
# --------------------------------------------------------------------------


def test_unknown_record_kind_is_preserved_not_dropped() -> None:
    """An older build must still diff a newer lockfile.

    Unknown kinds pass through as opaque lines. Dropping them would silently
    report deletions that did not happen.
    """
    text = (
        "# svarupa 9.9.9\n"
        "# schema 1.7\n"
        "# grammars python@0.25.0\n"
        "module\tapi\n"
        "quantum_widget\tapi\tsomething-new\n"
    )
    lf = Lockfile.parse(text)
    assert lf.unknown_kinds() == {"quantum_widget"}
    assert "quantum_widget\tapi\tsomething-new" in lf.render()


def test_minor_bump_still_diffs() -> None:
    old = Lockfile.parse("# svarupa 0.1\n# schema 1.0\nmodule\ta\n")
    new = Lockfile.parse("# svarupa 0.2\n# schema 1.7\nmodule\ta\nnewkind\tb\n")
    old.assert_diffable(new)  # must not raise


def test_major_bump_refuses_with_actionable_message() -> None:
    old = Lockfile.parse("# svarupa 0.1\n# schema 1.0\nmodule\ta\n")
    new = Lockfile.parse("# svarupa 2.0\n# schema 2.0\nmodule\ta\n")
    with pytest.raises(SchemaMismatch) as exc:
        old.assert_diffable(new)
    assert "regenerate" in str(exc.value).lower()


def test_header_roundtrips() -> None:
    lf = build(module_record("api"))
    back = Lockfile.parse(lf.render())
    assert back.header.tool_version == TOOL
    assert back.header.grammars_dict == GRAMMARS
    assert back.render() == lf.render()


# --------------------------------------------------------------------------
# Collisions: must diagnose, never silently merge
# --------------------------------------------------------------------------


def test_case_collision_is_reported() -> None:
    """Distinct on Linux, one file on a case-insensitive macOS volume."""
    got = collision_check(["src/Utils", "src/utils", "src/api"])
    assert len(got) == 1
    assert got[0].code == "SVA-L-007"
    assert "case" in got[0].message.lower()
    assert got[0].subject == "src/Utils | src/utils"


def test_no_false_positive_collisions() -> None:
    assert collision_check(["src/a", "src/b", "src/c"]) == []


# --------------------------------------------------------------------------
# Negative parse tests
#
# The riskiest assumption in this component was that `Lockfile.parse` would
# only ever be fed bytes that `Lockfile.render` produced. That is guaranteed
# false: this file lives in git, gets merged by humans, and is diffed by CI on
# every PR. Malformed input is the expected case.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "marker",
    ["<<<<<<< HEAD", "=======", ">>>>>>> theirs", "<<<<<<< ours:file.lock"],
)
def test_unresolved_conflict_markers_refuse(marker: str) -> None:
    """A botched merge must not flow through the diff engine as architecture.

    Before this, `unknown_kinds()` cheerfully reported
    {'<<<<<<< HEAD', '======='} and the evolution policy preserved them as
    opaque facts.
    """
    with pytest.raises(DiagnosticError) as exc:
        Lockfile.parse(f"# schema 1.0\nmodule\tsrc/api\n{marker}\n")
    assert exc.value.diagnostic.code == "SVA-L-001"
    assert "conflict" in exc.value.diagnostic.message.lower()


@pytest.mark.parametrize(
    ("line", "why"),
    [
        ("dep\ta", "truncated dep silently becomes a different fact"),
        ("dep\ta\tb\tc", "over-long dep"),
        ("module", "module with no id"),
        ("module\ta\tb", "module with a stray field"),
    ],
)
def test_wrong_arity_refuses(line: str, why: str) -> None:
    with pytest.raises(DiagnosticError) as exc:
        Lockfile.parse(f"# schema 1.0\n{line}\n")
    assert exc.value.diagnostic.code == "SVA-L-002", why
    assert "line 2" in (exc.value.diagnostic.location or "")


@pytest.mark.parametrize("kind", ["Module", "dep-x", "a b", "dep!", "", "12"])
def test_kind_must_match_the_published_production(kind: str) -> None:
    with pytest.raises(DiagnosticError) as exc:
        Lockfile.parse(f"# schema 1.0\n{kind}\tx\n")
    assert exc.value.diagnostic.code == "SVA-L-001"


def test_well_formed_unknown_kind_is_still_tolerated() -> None:
    """Strictness must not break forward compatibility, which is the point."""
    lf = Lockfile.parse("# schema 1.7\nmodule\ta\nquantum_widget\tx\ty\tz\n")
    assert lf.unknown_kinds() == {"quantum_widget"}


@pytest.mark.parametrize("bad", ["a\\qb", "trailing\\", "\\"])
def test_unknown_escape_refuses(bad: str) -> None:
    """Silently canonicalizing would change bytes with no diagnostic."""
    with pytest.raises(DiagnosticError) as exc:
        Lockfile.parse(f"# schema 1.0\nmodule\t{bad}\n")
    assert exc.value.diagnostic.code == "SVA-L-004"


@pytest.mark.parametrize(
    ("name", "cp"),
    [
        ("U+2028 line separator", "\u2028"),
        ("U+2029 paragraph separator", "\u2029"),
        ("vertical tab", "\x0b"),
        ("form feed", "\x0c"),
        ("NEL", "\x85"),
    ],
)
def test_exotic_line_separators_do_not_split_records(name: str, cp: str) -> None:
    """str.splitlines() breaks on all of these; the grammar's NEWLINE is LF.

    Each is legal in a POSIX filename. Splitting on them turned one module into
    a module plus a phantom opaque 'fact' that the evolution policy then
    preserved in diffs.
    """
    original = f"src/a{cp}b"
    lf = build(module_record(original))
    back = Lockfile.parse(lf.render())
    assert len(back.records) == 1, f"{name} split one record into {len(back.records)}"
    assert back.records[0].fields[0] == original
    assert back.render() == lf.render()


def test_crlf_lockfile_still_parses() -> None:
    """A Windows editor must not break the file."""
    lf = build(module_record("src/api"), dep_record("src/api", "src/db"))
    crlf = lf.render().replace("\n", "\r\n")
    assert Lockfile.parse(crlf).render() == lf.render()


def test_unstamped_lockfile_refuses_to_diff() -> None:
    """A missing stamp is less trustworthy than a mismatched one.

    Defaulting to the current schema would manufacture provenance for a file
    that has none.
    """
    unstamped = Lockfile.parse("module\tsrc/api\n")
    stamped = build(module_record("src/api"))
    assert unstamped.header.stamped is False
    with pytest.raises(SchemaMismatch, match="no '# schema' stamp"):
        unstamped.assert_diffable(stamped)
    with pytest.raises(SchemaMismatch):
        stamped.assert_diffable(unstamped)
