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


SHARED = (
    "services:\n"
    "  api:\n"
    "    build: ./api\n"
    "    depends_on: [db]\n"
    "  worker:\n"
    "    build: ./worker\n"
    "    environment:\n"
    "      DATABASE_URL: postgres://app:${PW}@db:5432/app\n"
    "  admin:\n"
    "    image: adminer\n"
    "    depends_on: [db]\n"
    "  db:\n"
    "    image: postgres:16\n"
)


def test_two_codebases_on_one_datastore_are_flagged(tmp_path: Path) -> None:
    write(tmp_path, "docker-compose.yml", SHARED)
    shared = [v for v in run(tmp_path).violations if v.check == "shared-datastore"]
    assert len(shared) == 1
    v = shared[0]
    assert [e.start_line for e in v.evidence] == [12, 2, 5]
    assert v.value == 2
    assert v.message.startswith("db is used by 2 services (api, worker)")


def test_one_codebase_with_two_services_is_not_shared(tmp_path: Path) -> None:
    write(
        tmp_path,
        "docker-compose.yml",
        SHARED.replace("build: ./worker", "build: ./api"),
    )
    assert [v for v in run(tmp_path).violations if v.check == "shared-datastore"] == []


ROUTE = (
    "from fastapi import APIRouter\n"
    "from hub.core import run\n"
    "router = APIRouter()\n"
    "@router.get('/x')\n"
    "def x():\n"
    "    return run()\n"
)


def hub_repo(root: Path, route_modules: int) -> None:
    for i in range(10):
        write(root, f"user{i}/m.py", "from hub.core import run\n")
        write(root, f"dep{i}/m.py", "def run():\n    return 1\n")
    imports = "".join(f"from dep{i}.m import run as r{i}\n" for i in range(10))
    write(root, "hub/core.py", imports + "\n\ndef run():\n    return 1\n")
    for i in range(route_modules):
        write(root, f"api{i}/routes.py", ROUTE)


def test_hub_reached_from_every_route_module_is_flagged(tmp_path: Path) -> None:
    hub_repo(tmp_path, 3)
    found = [v for v in run(tmp_path).violations if v.check == "hub-on-request-path"]
    assert [(v.module, v.value) for v in found] == [("hub", 3)]
    assert found[0].evidence[0].file == "hub/core.py"
    assert "every request path" in found[0].message


def test_two_route_modules_are_not_enough(tmp_path: Path) -> None:
    hub_repo(tmp_path, 2)
    assert [v for v in run(tmp_path).violations if v.check == "hub-on-request-path"] == []


def test_a_route_module_that_avoids_the_hub_clears_it(tmp_path: Path) -> None:
    hub_repo(tmp_path, 3)
    write(
        tmp_path, "api9/routes.py", ROUTE.replace("from hub.core import run\n", "run = len\n")
    )
    assert [v for v in run(tmp_path).violations if v.check == "hub-on-request-path"] == []
