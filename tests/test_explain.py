"""Check explanations: fixed text per check, examples per language."""

from __future__ import annotations

import pytest

from svarupa.health import explain
from svarupa.health.explain import (
    Example,
    ExplanationError,
    example_for,
    explanation,
    parse,
    required_languages,
)


def test_an_explanation_has_text_and_examples() -> None:
    e = explanation("query-in-loop")
    assert e.what.startswith("The same database query runs once for every item")
    assert e.why and e.fix
    langs = [lang for lang, _ in e.examples]
    assert langs == sorted(langs) == ["go", "java", "python", "typescript"]
    py = dict(e.examples)["python"]
    assert "for order in orders" in py.before and "in_bulk" in py.after


def test_javascript_uses_the_typescript_example() -> None:
    assert example_for("query-in-loop", "javascript") == example_for(
        "query-in-loop", "typescript"
    )
    assert example_for("query-in-loop", None) is None
    assert example_for("query-in-loop", "cobol") is None


def test_to_json_is_plain_data() -> None:
    j = explanation("query-in-loop").to_json()
    assert set(j) == {"what", "why", "fix", "examples"}
    assert set(j["examples"]["python"]) == {"before", "after"}  # type: ignore[index]


def test_bad_files_are_refused() -> None:
    with pytest.raises(ExplanationError, match="unknown key"):
        parse("x", {"what": "a", "why": "b", "fix": "c", "colour": "d"})
    with pytest.raises(ExplanationError, match="empty"):
        parse("x", {"what": " ", "why": "b", "fix": "c"})
    with pytest.raises(ExplanationError, match="language"):
        parse(
            "x",
            {
                "what": "a",
                "why": "b",
                "fix": "c",
                "example": {"cobol": {"before": "a", "after": "b"}},
            },
        )
    with pytest.raises(ExplanationError, match="before and after"):
        parse("x", {"what": "a", "why": "b", "fix": "c", "example": {"go": {"before": "a"}}})
    with pytest.raises(ExplanationError, match="no explanation"):
        explanation("no-such-check")


def test_required_languages_follow_the_packs() -> None:
    assert required_languages("complex-function") == {"python", "typescript", "go", "java"}
    assert required_languages("blocking-in-async") == {"python", "typescript"}
    assert required_languages("code-injection") == {"python", "typescript", "java"}
    assert required_languages("debug-enabled") == {"python", "go"}
    assert required_languages("module-cycle") == frozenset()


def test_example_text_is_dedented_and_trimmed() -> None:
    e = parse(
        "x",
        {
            "what": "a",
            "why": "b",
            "fix": "c",
            "example": {"go": {"before": "\n  a\n    b\n", "after": "c\n"}},
        },
    )
    assert dict(e.examples)["go"] == Example("a\n  b", "c")
    assert explain.LANGS == ("python", "typescript", "go", "java")
