"""Security checks from risk facts, and exceptions by location."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from svarupa.build import build
from svarupa.design.file import DesignException, load_design
from svarupa.detect import detect
from svarupa.extract import declared_dependencies, extract
from svarupa.health import BY_ID, Health, assess, source_texts
from tests.test_health import write


def run(root: Path, exceptions: Sequence[DesignException] = ()) -> Health:
    scan = detect(root)
    graph = build(scan, extract(scan, declared_dependencies(scan)), strict=False)
    return assess(graph, source_texts(scan, graph), None, exceptions)


def test_security_checks_are_in_the_catalog() -> None:
    for cid, sev in (
        ("hardcoded-secret", "blocker"),
        ("sql-injection", "critical"),
        ("command-injection", "critical"),
        ("code-injection", "critical"),
        ("tls-verify-off", "critical"),
        ("unsafe-deserialize", "critical"),
        ("weak-hash", "major"),
        ("debug-enabled", "major"),
        ("cors-any-origin", "major"),
        ("endpoint-without-auth", "major"),
    ):
        check = BY_ID[cid]
        assert (check.area, check.severity, check.maturity) == ("security", sev, "experimental")
        assert "CWE-" in check.source


def test_injection_is_reported_with_its_cwe_and_rates_security_d(tmp_path: Path) -> None:
    write(tmp_path, "app/m.py", 'def f(cur, x):\n    cur.execute("SELECT " + x)\n')
    h = run(tmp_path)
    v = next(v for v in h.violations if v.check == "sql-injection")
    assert (v.evidence[0].file, v.evidence[0].start_line) == ("app/m.py", 2)
    assert "cur.execute" in v.message and "CWE-89" in v.message
    assert h.rating("security") == "D"


def test_clean_code_rates_security_a(tmp_path: Path) -> None:
    write(tmp_path, "app/m.py", "def f():\n    return 1\n")
    assert run(tmp_path).rating("security") == "A"


def test_at_exception_moves_a_violation_to_accepted(tmp_path: Path) -> None:
    write(tmp_path, "app/m.py", "import hashlib\ndef f(d):\n    return hashlib.md5(d)\n")
    exc = (DesignException("weak-hash", "", "", "cache key, not a password", at="app/m.py:3"),)
    h = run(tmp_path, exc)
    assert "weak-hash" not in [v.check for v in h.violations]
    assert [v.check for v in h.accepted] == ["weak-hash"]
    assert h.rating("security") == "A"
    accepted = h.to_json()["accepted"]
    assert isinstance(accepted, list) and accepted[0]["check"] == "weak-hash"


def test_design_yaml_reads_at_exceptions_and_auth_markers(tmp_path: Path) -> None:
    write(
        tmp_path,
        ".svarupa/design.yaml",
        "units: {}\n"
        "auth_markers: [require_session]\n"
        "exceptions:\n"
        "  - rule: hardcoded-secret\n    at: tests/fixtures/key.pem\n    reason: fake key\n"
        "  - rule: weak-hash\n    at: app/m.py:3\n",
    )
    loaded, problem = load_design(tmp_path / ".svarupa" / "design.yaml")
    assert problem == "" and loaded is not None
    assert loaded.auth_markers == ["require_session"]
    assert [(e.rule, e.at) for e in loaded.exceptions] == [
        ("hardcoded-secret", "tests/fixtures/key.pem")
    ]  # the second has no reason, so it is dropped
