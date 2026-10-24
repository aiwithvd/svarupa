"""svarupa check: what a change added, matched so line shifts never count."""

from __future__ import annotations

from svarupa.check import Finding, Snapshot, compare

RATINGS = (
    ("maintainability", "A"),
    ("reliability", "A"),
    ("security", "A"),
    ("performance", "A"),
)


def f(
    check: str = "many-parameters",
    line: int = 1,
    symbol: str | None = "a.py#m.f",
    message: str = "f has 6 parameters (limit 5)",
    file: str = "a.py",
) -> Finding:
    return Finding(check, file, line, symbol, message)


def snap(*found: Finding, ratings=RATINGS) -> Snapshot:
    return Snapshot(tuple(found), ratings)


def test_a_moved_finding_is_not_new() -> None:
    d = compare(snap(f(line=1)), snap(f(line=40)))
    assert d.new == () and d.fixed == ()


def test_a_second_copy_is_new_and_a_removed_one_is_fixed() -> None:
    d = compare(snap(f()), snap(f(), f(line=9)))
    assert [x.line for x in d.new] == [9]
    d = compare(snap(f(), f(check="deep-nesting")), snap(f()))
    assert [x.check for x in d.fixed] == ["deep-nesting"]


def test_digits_in_messages_do_not_break_matching() -> None:
    base = f(check="large-file", symbol=None, message="a.py has 1200 lines (limit 1000)")
    head = f(check="large-file", symbol=None, message="a.py has 1300 lines (limit 1000)")
    assert compare(snap(base), snap(head)).new == ()


def test_a_grade_drop_fails_alone() -> None:
    worse = (
        ("maintainability", "A"),
        ("reliability", "A"),
        ("security", "C"),
        ("performance", "A"),
    )
    d = compare(snap(), snap(ratings=worse))
    assert d.new == () and d.worse() == ["security"] and d.failed("minor")
    assert d.grades == (("security", "A", "C"),)


def test_a_newly_graded_area_is_not_worse() -> None:
    before = (
        ("maintainability", "A"),
        ("reliability", None),
        ("security", "A"),
        ("performance", None),
    )
    d = compare(snap(ratings=before), snap())
    assert d.worse() == [] and not d.failed("minor")


def test_min_severity_ignores_small_new_findings() -> None:
    d = compare(snap(), snap(f()))  # many-parameters is minor
    assert d.failed("minor") and not d.failed("major")


def test_json_and_text_say_the_same() -> None:
    d = compare(snap(f(check="deep-nesting")), snap(f()))
    j = d.to_json(d.failed("minor"))
    assert j["failed"] is True and j["new"][0]["check"] == "many-parameters"
    assert j["fixed"][0]["check"] == "deep-nesting"
    text = d.render()
    assert text.startswith("1 new, 1 fixed")
    assert "a.py:1 many-parameters: f has 6 parameters (limit 5)" in text
    assert "fix: Group the values that travel together" in text
