"""Write endpoints without an auth marker on the handler, router or middleware."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from svarupa.build import build
from svarupa.detect import detect
from svarupa.extract import declared_dependencies, extract
from svarupa.health import Health, assess, source_texts
from tests.test_health import write

DEPS = "def get_current_user():\n    return 1\n"


def run(root: Path, markers: Sequence[str] = ()) -> Health:
    scan = detect(root)
    graph = build(scan, extract(scan, declared_dependencies(scan)), strict=False)
    return assess(graph, source_texts(scan, graph), auth_markers=markers)


def flagged(h: Health) -> list[tuple[str, int]]:
    return [
        (v.evidence[0].file, v.evidence[0].start_line)
        for v in h.violations
        if v.check == "endpoint-without-auth"
    ]


def test_fastapi_write_route_without_auth_is_flagged(tmp_path: Path) -> None:
    write(tmp_path, "app/deps.py", DEPS)
    write(
        tmp_path,
        "app/items.py",
        "from fastapi import APIRouter, Depends\n"
        "from app.deps import get_current_user\n"
        "router = APIRouter()\n"
        "@router.post('/items')\n"
        "def create(item: dict):\n"
        "    return item\n"
        "@router.delete('/items/{i}')\n"
        "def remove(i: int, user=Depends(get_current_user)):\n"
        "    return i\n"
        "@router.get('/items')\n"
        "def read():\n"
        "    return []\n"
        "@router.post('/login/access-token')\n"
        "def login():\n"
        "    return 1\n",
    )
    assert flagged(run(tmp_path)) == [("app/items.py", 4)]


def test_router_level_dependency_protects_every_route(tmp_path: Path) -> None:
    write(tmp_path, "app/deps.py", DEPS)
    write(
        tmp_path,
        "app/items.py",
        "from fastapi import APIRouter, Depends\n"
        "from app.deps import get_current_user\n"
        "router = APIRouter(dependencies=[Depends(get_current_user)])\n"
        "@router.post('/items')\n"
        "def create(item: dict):\n"
        "    return item\n",
    )
    assert flagged(run(tmp_path)) == []


def test_express_middleware_in_the_route_call_protects_it(tmp_path: Path) -> None:
    write(
        tmp_path,
        "src/routes.js",
        "const express = require('express');\n"
        "const { requireAuth } = require('./auth');\n"
        "const router = express.Router();\n"
        "router.post('/a', requireAuth, (req, res) => res.send(1));\n"
        "router.post('/b', (req, res) => res.send(2));\n"
        "module.exports = router;\n",
    )
    assert flagged(run(tmp_path)) == [("src/routes.js", 5)]


def test_nest_controller_guard_protects_its_methods(tmp_path: Path) -> None:
    write(
        tmp_path,
        "src/a.controller.ts",
        "import { Controller, Post, UseGuards } from '@nestjs/common';\n"
        "@UseGuards(JwtAuthGuard)\n"
        "@Controller('a')\n"
        "export class A {\n"
        "  @Post()\n"
        "  create() { return 1; }\n"
        "}\n",
    )
    assert flagged(run(tmp_path)) == []


def test_unit_without_any_marker_is_not_judged(tmp_path: Path) -> None:
    write(
        tmp_path,
        "app/items.py",
        "from fastapi import APIRouter\nrouter = APIRouter()\n"
        "@router.post('/items')\ndef create(item: dict):\n    return item\n",
    )
    h = run(tmp_path)
    assert flagged(h) == []
    assert any("no auth marker" in n for n in h.notes)


def test_team_markers_from_design_yaml_count(tmp_path: Path) -> None:
    write(
        tmp_path,
        "app/items.py",
        "from fastapi import APIRouter\nfrom app.s import require_session\n"
        "router = APIRouter()\n"
        "@router.post('/items')\n@require_session\ndef create(item: dict):\n    return item\n"
        "@router.post('/other')\ndef other(item: dict):\n    return item\n",
    )
    assert flagged(run(tmp_path, ("require_session",))) == [("app/items.py", 8)]


def test_auth_attached_when_the_router_is_included_protects_it(tmp_path: Path) -> None:
    write(tmp_path, "app/deps.py", DEPS)
    write(
        tmp_path,
        "app/items.py",
        "from fastapi import APIRouter\nrouter = APIRouter()\n"
        "@router.post('/items')\ndef create(item: dict):\n    return item\n",
    )
    write(
        tmp_path,
        "app/main.py",
        "from fastapi import FastAPI, Depends\nfrom app import items\n"
        "from app.deps import get_current_user\napp = FastAPI()\n"
        "app.include_router(items.router, dependencies=[Depends(get_current_user)])\n",
    )
    assert flagged(run(tmp_path)) == []


def test_many_routes_in_one_file_stay_fast(tmp_path: Path) -> None:
    import time

    body = "".join(
        f"@router.post('/r{i}')\ndef r{i}(item: dict):\n    return {i}\n" for i in range(800)
    )
    write(tmp_path, "app/deps.py", DEPS)
    write(
        tmp_path,
        "app/big.py",
        "from fastapi import APIRouter, Depends\nfrom app.deps import get_current_user\n"
        "router = APIRouter(dependencies=[Depends(get_current_user)])\n" + body,
    )
    scan = detect(tmp_path)
    graph = build(scan, extract(scan, declared_dependencies(scan)), strict=False)
    texts = source_texts(scan, graph)
    start = time.perf_counter()
    from svarupa.health.auth import auth_checks

    out, _ = auth_checks(graph, texts, ())
    assert out == [] and time.perf_counter() - start < 5
