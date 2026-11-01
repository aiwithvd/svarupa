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
    assert (
        langs
        == sorted(langs)
        == [
            "csharp",
            "go",
            "java",
            "python",
            "rust",
            "typescript",
        ]
    )
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
    assert required_languages("complex-function") == {
        "python",
        "typescript",
        "go",
        "java",
        "csharp",
        "rust",
    }
    assert required_languages("blocking-in-async") == {"python", "typescript", "csharp", "rust"}
    assert required_languages("code-injection") == {"python", "typescript", "java", "csharp"}
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
    assert explain.LANGS == ("python", "typescript", "go", "java", "csharp", "rust")


from svarupa.health import CATALOG  # noqa: E402


def test_every_check_has_an_explanation() -> None:
    for check in CATALOG:
        explanation(check.id)


def test_every_language_that_can_fire_has_an_example() -> None:
    missing = [
        f"{c.id}:{lang}"
        for c in CATALOG
        for lang in sorted(required_languages(c.id))
        if example_for(c.id, lang) is None
    ]
    assert missing == []


def test_explanations_are_plain_text() -> None:
    for check in CATALOG:
        e = explanation(check.id)
        text = " ".join([e.what, e.why, e.fix, *(x.before + x.after for _, x in e.examples)])
        assert "\u2014" not in text, check.id
        assert "TODO" not in text, check.id
        for part in (e.what, e.why, e.fix):
            assert len(part) <= 400, check.id


from svarupa.cli import main  # noqa: E402


def test_explain_lists_the_checks(capsys) -> None:
    assert main(["explain"]) == 0
    out = capsys.readouterr().out
    assert "query-in-loop" in out and "Query in a loop (N+1)" in out
    assert len(out.strip().splitlines()) == len(CATALOG)


def test_explain_prints_one_check_in_one_language(capsys) -> None:
    assert main(["explain", "query-in-loop", "--lang", "javascript"]) == 0
    out = capsys.readouterr().out
    assert "What:" in out and "Why:" in out and "How to fix:" in out
    assert "prisma.customer.findMany" in out
    assert "in_bulk" not in out


def test_explain_without_an_example_prints_the_text(capsys) -> None:
    assert main(["explain", "debug-enabled", "--lang", "java"]) == 0
    out = capsys.readouterr().out
    assert "How to fix:" in out and "no java example" in out


def test_explain_unknown_check_names_close_ones(capsys) -> None:
    assert main(["explain", "query-in-loops"]) == 2
    err = capsys.readouterr().err
    assert "unknown check" in err and "query-in-loop" in err


def test_typescript_examples_are_valid_javascript(tmp_path) -> None:
    """JavaScript findings show these, so they must run as plain JavaScript."""
    import shutil
    import subprocess

    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed; the examples are not syntax checked in this run")
    bad = []
    for c in CATALOG:
        ex = example_for(c.id, "typescript")
        for side in ("before", "after") if ex else ():
            f = tmp_path / f"{c.id}.{side}.mjs"
            f.write_text(getattr(ex, side) + "\n", encoding="utf8")
            if subprocess.run([node, "--check", str(f)], capture_output=True).returncode:
                bad.append(f.name)
    assert bad == []


def test_examples_teach_working_fixes() -> None:
    gin = example_for("debug-enabled", "go")
    assert gin is not None and "gin.ReleaseMode" in gin.after and "GIN_MODE" not in gin.after
    java = example_for("no-timeout", "java")
    assert java is not None and ".timeout(Duration" in java.after
    ts = example_for("code-injection", "typescript")
    assert ts is not None and "new Map" in ts.after and ".has(" in ts.after
    py = example_for("deep-nesting", "python")
    assert py is not None and py.after.index("def ship_in_stock") < py.after.index("for order")
    go = example_for("deep-nesting", "go")
    assert go is not None and "func shipInStock" in go.after


# What each risk check records when its C# example runs through the extractor.
_CS_FACTS = {
    "sql-injection": ("sink-built", "sql"),
    "command-injection": ("sink-built", "shell"),
    "code-injection": ("sink-built", "code"),
    "tls-verify-off": ("unsafe", "tls-verify-off"),
    "unsafe-deserialize": ("unsafe", "unsafe-deserialize"),
    "weak-hash": ("unsafe", "weak-hash"),
    "cors-any-origin": ("unsafe", "cors-any-origin"),
    "query-in-loop": ("in-loop", "query"),
    "blocking-in-async": ("blocking-async", "io"),
    "swallowed-error": ("empty-catch", "catch"),
}


def _csharp_facts(code: str) -> set[tuple[str, str]]:
    from svarupa.extract.packs import extractor

    # A snippet that declares its own method goes in a class; the rest in an async method.
    if "async Task" in code:
        src = "class A {\n" + code + "\n}\n"
    else:
        src = "class A {\nasync Task F() {\n" + code + "\n}\n}\n"
    return {(r.kind, r.tag) for r in extractor("csharp").parse("A.cs", src.encode()).risks}


def test_csharp_examples_fire_before_and_stay_quiet_after() -> None:
    for check, fact in _CS_FACTS.items():
        ex = example_for(check, "csharp")
        assert ex is not None, check
        assert fact in _csharp_facts(ex.before), check
        assert _csharp_facts(ex.after) == set(), check


def test_csharp_has_no_example_for_endpoint_without_auth() -> None:
    assert example_for("endpoint-without-auth", "csharp") is None


# What each risk check records when its Rust example runs through the extractor.
_RS_FACTS = {
    "sql-injection": ("sink-built", "sql"),
    "command-injection": ("sink-built", "shell"),
    "tls-verify-off": ("unsafe", "tls-verify-off"),
    "weak-hash": ("unsafe", "weak-hash"),
    "cors-any-origin": ("unsafe", "cors-any-origin"),
    "query-in-loop": ("in-loop", "query"),
    "blocking-in-async": ("blocking-async", "io"),
}


def _rust_facts(code: str) -> set[tuple[str, str]]:
    from svarupa.extract.packs import extractor

    # A snippet that declares its own function goes as is; the rest in an async function.
    src = code if "fn " in code else "async fn f() {\n" + code + "\n}\n"
    return {(r.kind, r.tag) for r in extractor("rust").parse("a.rs", src.encode()).risks}


def test_rust_examples_fire_before_and_stay_quiet_after() -> None:
    for check, fact in _RS_FACTS.items():
        ex = example_for(check, "rust")
        assert ex is not None, check
        assert fact in _rust_facts(ex.before), check
        assert _rust_facts(ex.after) == set(), check


@pytest.mark.parametrize(
    "check",
    ["code-injection", "unsafe-deserialize", "swallowed-error", "endpoint-without-auth"],
)
def test_rust_has_no_example_for_checks_its_pack_cannot_fire(check: str) -> None:
    assert example_for(check, "rust") is None
