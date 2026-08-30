"""Lockfile grammar tests.

Written against the four properties the format must have (design 7.1). Each
test names the failure it is defending against, because a test whose purpose
is forgotten gets deleted during the next refactor.
"""

from __future__ import annotations

import pytest

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
    """POSIX paths may legally contain spaces, commas, and even ' -> '."""
    weird = "src/my project, v2/a -> b/mod.py"
    lf = build(module_record(weird), dep_record(weird, "src/other"))
    back = Lockfile.parse(lf.render())
    assert back.records[0].fields[0] == weird or back.records[1].fields[0] == weird
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
    a = before.render().splitlines()
    b = after.render().splitlines()
    added = [x for x in b if x not in a]
    removed = [x for x in a if x not in b]

    assert len(added) == 1, f"expected exactly one added line, got {added}"
    assert removed == [], f"expected no removed lines, got {removed}"
    assert added[0].split("\t") == ["dep", "billing", "auth"]


# --------------------------------------------------------------------------
# Canonical ordering / determinism
# --------------------------------------------------------------------------


def test_input_order_does_not_affect_output() -> None:
    recs = [module_record("z"), module_record("a"), dep_record("z", "a")]
    assert build(*recs).render() == build(*reversed(recs)).render()


def test_duplicates_collapse() -> None:
    lf = build(module_record("a"), module_record("a"), dep_record("a", "b"))
    assert lf.render().count("\nmodule\ta") == 1


def test_sorting_is_codepoint_not_locale() -> None:
    """Turkish locale folds i/I differently; sorting must not care."""
    lf = build(module_record("Illinois"), module_record("izmir"), module_record("Izmir"))
    fields = [r.fields[0] for r in lf.records]
    assert fields == sorted(fields)


def test_no_line_numbers_anywhere() -> None:
    """Property 1: facts only.

    Any edit above a record would shift a line number and churn the lockfile,
    contradicting the stability guarantee. Evidence lives in graph.json.
    """
    lf = build(module_record("api"), dep_record("api", "db"))
    body = [ln for ln in lf.render().splitlines() if not ln.startswith("#")]
    for line in body:
        for fld in line.split("\t")[1:]:
            assert ":" not in fld or not fld.rsplit(":", 1)[-1].isdigit(), (
                f"lockfile record looks like it carries a line number: {line!r}"
            )


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
    assert back.header.grammars == GRAMMARS
    assert back.render() == lf.render()


# --------------------------------------------------------------------------
# Collisions: must diagnose, never silently merge
# --------------------------------------------------------------------------


def test_case_collision_is_reported() -> None:
    """Distinct on Linux, one file on a case-insensitive macOS volume."""
    got = collision_check(["src/Utils", "src/utils", "src/api"])
    assert got == [("src/utils", ["src/Utils", "src/utils"])]


def test_no_false_positive_collisions() -> None:
    assert collision_check(["src/a", "src/b", "src/c"]) == []
