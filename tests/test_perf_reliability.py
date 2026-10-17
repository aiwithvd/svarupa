"""Performance and reliability checks."""

from __future__ import annotations

from pathlib import Path

from svarupa.health import BY_ID
from tests.test_health import write
from tests.test_security import run


def test_perf_and_reliability_checks_are_in_the_catalog() -> None:
    for cid, area, sev, minutes in (
        ("shared-datastore", "performance", "major", 240),
        ("query-in-loop", "performance", "major", 20),
        ("blocking-in-async", "performance", "major", 15),
        ("hub-on-request-path", "performance", "major", 60),
        ("no-timeout", "reliability", "major", 10),
        ("swallowed-error", "reliability", "major", 10),
        ("no-retry", "reliability", "minor", 15),
    ):
        check = BY_ID[cid]
        assert (check.area, check.severity, check.minutes, check.maturity) == (
            area,
            sev,
            minutes,
            "experimental",
        )


def test_runtime_facts_become_violations(tmp_path: Path) -> None:
    write(
        tmp_path,
        "app/m.py",
        "import requests\n"
        "def load(ids):\n"
        "    for i in ids:\n"
        "        User.objects.get(id=i)\n"
        "async def handler(url):\n"
        "    requests.get(url)\n"
        "def quiet():\n"
        "    try:\n"
        "        run()\n"
        "    except ValueError:\n"
        "        pass\n",
    )
    h = run(tmp_path)
    found = sorted((v.check, v.evidence[0].start_line) for v in h.violations)
    assert found == [
        ("blocking-in-async", 6),
        ("no-timeout", 6),
        ("query-in-loop", 4),
        ("swallowed-error", 10),
    ]
    by = {v.check: v.message for v in h.violations}
    assert by["query-in-loop"].startswith("User.objects.get in load runs a query on every pass")
    assert "(CWE-390)" in by["swallowed-error"]
    assert "(CWE-1088)" in by["no-timeout"]
    assert (h.rating("performance"), h.rating("reliability")) == ("C", "C")


def test_clean_code_rates_performance_and_reliability_a(tmp_path: Path) -> None:
    write(tmp_path, "app/m.py", "def f():\n    return 1\n")
    h = run(tmp_path)
    assert (h.rating("performance"), h.rating("reliability")) == ("A", "A")
