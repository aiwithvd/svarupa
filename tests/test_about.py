"""One plain sentence per diagram box, with its health."""

from __future__ import annotations

from pathlib import Path

from svarupa.build import build
from svarupa.cluster import cluster
from svarupa.derive import derive_all
from svarupa.derive.about import About, about_lines
from svarupa.detect import detect
from svarupa.extract import declared_dependencies, extract
from svarupa.health import assess, source_texts
from tests.test_health import write


def lines(root: Path, with_health: bool = True) -> dict[str, About]:
    scan = detect(root)
    graph = build(scan, extract(scan, declared_dependencies(scan)), strict=False)
    health = assess(graph, source_texts(scan, graph)) if with_health else None
    produced, _ = derive_all(graph, cluster(graph))
    return about_lines(graph, health, produced)


def api_repo(root: Path) -> None:
    write(
        root,
        "api/routes.py",
        "from fastapi import APIRouter\nfrom domain import orders\nimport psycopg2\n"
        "router = APIRouter()\n\n@router.get('/orders')\ndef a():\n    return 1\n\n"
        "@router.post('/orders')\ndef b():\n    return 2\n",
    )
    write(root, "domain/orders.py", "def x(a, b, c, d, e, f):\n    return a\n")
    write(root, "util/x.py", "VALUE = 1\n")


def test_a_module_line_names_its_routes_deps_and_externals(tmp_path: Path) -> None:
    api_repo(tmp_path)
    text = lines(tmp_path)["api"].text
    assert text.startswith("Module api: serves 2 HTTP routes (FastAPI)")
    assert "uses 1 module and is used by 0" in text
    assert "talks to PostgreSQL" in text
    assert text.endswith("No health findings.")


def test_a_module_with_no_facts_reads_its_id(tmp_path: Path) -> None:
    api_repo(tmp_path)
    assert lines(tmp_path, with_health=False)["util"].text == "Module util."


def test_the_health_clause_names_the_worst_finding(tmp_path: Path) -> None:
    api_repo(tmp_path)
    a = lines(tmp_path)["domain"]
    assert a.text.endswith("1 health finding, worst: Too many parameters (minor).")
    assert a.worst == "many-parameters"


def test_root_module_box_counts_root_findings(tmp_path: Path) -> None:
    write(tmp_path, "main.py", "def x(a, b, c, d, e, f):\n    return a\n")
    write(tmp_path, "pkg/m.py", "from main import x\n")
    got = lines(tmp_path)
    assert "." in got
    assert got["."].worst == "many-parameters"


def test_an_external_box_says_who_uses_it(tmp_path: Path) -> None:
    api_repo(tmp_path)
    ext = next(v for k, v in lines(tmp_path).items() if k.startswith("ext:"))
    assert ext.text.startswith("External database (PostgreSQL), used from 1 module.")


def test_compose_boxes(tmp_path: Path) -> None:
    write(
        tmp_path,
        "docker-compose.yml",
        "services:\n  api:\n    build: ./api\n    depends_on: [db]\n"
        "  admin:\n    image: adminer\n  db:\n    image: postgres:16\n",
    )
    api_repo(tmp_path)
    got = {k: v.text for k, v in lines(tmp_path).items()}
    svc = next(t for k, t in got.items() if k.endswith("#service.api"))
    assert svc.startswith("Service api: built from ./api, depends on db.")
    adm = next(t for k, t in got.items() if k.endswith("#service.admin"))
    assert adm.startswith("Service admin: runs the image adminer.")
    db = next(t for k, t in got.items() if k.endswith("#service.db"))
    assert db.startswith("PostgreSQL database (compose service db), used by api.")


def test_a_symbol_box_counts_only_its_own_findings(tmp_path: Path) -> None:
    from svarupa.derive.about import _findings  # pyright: ignore[reportPrivateUsage]
    from svarupa.derive.base import DiagramNode

    root = tmp_path
    write(
        root,
        "web/u.ts",
        "export interface P { a: string }\n"
        + "export function f() {\n"
        + "  let x = 1;\n" * 60
        + "  return x;\n}\n",
    )
    scan = detect(root)
    graph = build(scan, extract(scan, declared_dependencies(scan)), strict=False)
    health = assess(graph, source_texts(scan, graph))
    iface = next(
        n for nid, n in graph.nodes.items() if nid.startswith("web/u.ts#") and n.label == "P"
    )
    box = DiagramNode(id=iface.id, label="P", kind="interface", evidence=iface.evidence)
    assert any(v.check == "long-function" for v in health.violations)
    assert _findings(graph, health, box) == []


def test_a_file_box_names_what_it_defines(tmp_path: Path) -> None:
    api_repo(tmp_path)
    write(
        tmp_path,
        "api/deps.py",
        "class Auth:\n    def check(self):\n        return 1\n\n\nclass Key:\n    pass\n",
    )
    got = lines(tmp_path, with_health=False)
    assert got["api/routes.py"].text == "File api/routes.py: defines 2 functions."
    assert got["api/deps.py"].text == "File api/deps.py: defines 2 classes."
