"""Golden repositories: the exact pass-2 output for small fixture repos.

Pass-1 golden facts pin what each file says. These pin what the resolver
makes of it across files: every node, edge, scorecard row and diagnostic.
Resolution is about to learn package imports, and this is what proves the
Python and TypeScript results did not move.

After an intended change, regenerate and review the diff like code:

    SVARUPA_UPDATE_GOLDEN=1 uv run pytest tests/test_golden_repos.py
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from svarupa.detect import detect
from svarupa.extract import declared_dependencies, extract
from svarupa.extract.base import ExtractResult
from tests.test_golden_facts import _jsonable  # pyright: ignore[reportPrivateUsage]

GOLDEN = Path(__file__).parent / "golden_repos"

REPOS: dict[str, dict[str, str]] = {
    "python_shop": {
        "pyproject.toml": '[project]\nname = "shop"\ndependencies = ["fastapi", "requests"]\n',
        "src/shop/__init__.py": "from .orders import Order\n",
        "src/shop/orders.py": (
            "import requests\n"
            "from shop.db import Session\n"
            "from .base import Base\n"
            "\n"
            "\n"
            "class Order(Base):\n"
            "    def save(self):\n"
            "        self.validate()\n"
            "        Session().add(self)\n"
            '        requests.post("x")\n'
        ),
        "src/shop/base.py": "class Base:\n    def validate(self):\n        return True\n",
        "src/shop/db.py": "class Session:\n    def add(self, obj):\n        return obj\n",
        "src/shop/api.py": (
            "from fastapi import APIRouter\n"
            "from shop import Order\n"
            "from . import db\n"
            "\n"
            "router = APIRouter()\n"
            "\n"
            "\n"
            '@router.post("/orders")\n'
            "def create():\n"
            "    Order().save()\n"
            "    db.Session()\n"
            "    missing.call()\n"
        ),
        "src/ns/tool/run.py": (
            "from ns.tool import helpers\n\n\ndef go():\n    helpers.assist()\n"
        ),
        "src/ns/tool/helpers.py": "def assist():\n    return 1\n",
    },
    "ts_web": {
        "package.json": '{"name": "web", "dependencies": {"express": "4"}}\n',
        "tsconfig.json": (
            '{"compilerOptions": {"baseUrl": ".", "paths": {"@/*": ["src/*"]}}}\n'
        ),
        "src/index.ts": (
            "import express from 'express';\n"
            "import { UsersController } from './users/users.controller.js';\n"
            "import { log } from '@/util/log';\n"
            "const app = express();\n"
            "app.get('/health', h);\n"
            "log('up');\n"
            "new UsersController();\n"
        ),
        "src/users/users.controller.ts": (
            "import { UsersService } from './users.service';\n"
            "export class UsersController {\n"
            "  constructor(private readonly svc: UsersService) {}\n"
            "  list() { return this.svc.findAll(); }\n"
            "}\n"
        ),
        "src/users/users.service.ts": (
            "import { Base } from '../shared';\n"
            "export class UsersService extends Base {\n"
            "  findAll() { return this.helper(); }\n"
            "}\n"
        ),
        "src/shared/index.ts": "export * from './base';\n",
        "src/shared/base.ts": "export class Base {\n  helper() { return 1; }\n}\n",
        "src/util/log.ts": "export function log(m: string) { console.log(m); }\n",
        "src/legacy.js": "const { log } = require('./util/log');\nlog('x');\n",
    },
    "go_shop": {
        "go.mod": (
            "module github.com/acme/shop\n\ngo 1.22\n\n"
            "require (\n\tgithub.com/gin-gonic/gin v1.9.1\n)\n"
        ),
        "cmd/api/main.go": (
            'package main\n\nimport (\n\t"fmt"\n\t"github.com/gin-gonic/gin"\n'
            '\t"github.com/acme/shop/internal/orders"\n)\n\n'
            "func main() {\n\tr := gin.Default()\n\tfmt.Println(orders.NewService(), r)\n}\n"
        ),
        "internal/orders/service.go": (
            "package orders\n\ntype Service struct{}\n\n"
            "func NewService() *Service {\n\treturn newRepo().wrap()\n}\n"
        ),
        "internal/orders/repo.go": (
            "package orders\n\ntype Repo struct{}\n\nfunc newRepo() *Repo { return &Repo{} }\n"
        ),
        "internal/orders/service_test.go": "package orders\n\nfunc TestX() {}\n",
    },
    "java_shop": {
        "pom.xml": (
            "<project><dependencies><dependency><groupId>org.springframework.boot"
            "</groupId></dependency></dependencies></project>\n"
        ),
        "src/main/java/com/acme/shop/App.java": (
            "package com.acme.shop;\n\n"
            "import org.springframework.boot.SpringApplication;\n"
            "import com.acme.shop.service.*;\n\n"
            "public class App {\n"
            "    private final OrderService orders = new OrderService();\n"
            "    public void run() { this.orders.place(); SpringApplication.run(App.class); }\n"
            "}\n"
        ),
        "src/main/java/com/acme/shop/service/OrderService.java": (
            "package com.acme.shop.service;\n\n"
            "public class OrderService extends Base {\n"
            "    public void place() { audit(); }\n"
            "}\n"
        ),
        "src/main/java/com/acme/shop/service/Base.java": (
            "package com.acme.shop.service;\n\nclass Base {\n    void audit() {}\n}\n"
        ),
    },
}


def render_result(result: ExtractResult) -> str:
    obj = {
        "nodes": _jsonable(result.nodes),
        "edges": _jsonable(result.edges),
        "scorecard": result.scorecard.to_json_obj(),
        "diagnostics": _jsonable(result.diagnostics),
    }
    return json.dumps(obj, indent=1, ensure_ascii=False) + "\n"


def build(root: Path, files: dict[str, str]) -> ExtractResult:
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf8")
    scan = detect(root)
    return extract(scan, declared_dependencies(scan))


def test_every_repo_has_a_golden_file() -> None:
    # Guards the parametrized test below against passing vacuously.
    assert sorted(REPOS) == sorted(p.stem for p in GOLDEN.glob("*.json"))


@pytest.mark.parametrize("name", sorted(REPOS))
def test_repo_matches_golden(name: str, tmp_path: Path) -> None:
    got = render_result(build(tmp_path, REPOS[name]))
    golden = GOLDEN / f"{name}.json"
    if os.environ.get("SVARUPA_UPDATE_GOLDEN") == "1":
        GOLDEN.mkdir(exist_ok=True)
        golden.write_text(got, encoding="utf8")
        return
    assert golden.exists(), f"no golden file; run with SVARUPA_UPDATE_GOLDEN=1: {golden}"
    assert got == golden.read_text(encoding="utf8")
