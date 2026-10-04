# Language Packs 1a-1: Engine and Python/TypeScript Port Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the hand-written Python and TypeScript extractors with one generic walker driven by per-language packs, with byte-identical output, then fix JavaScript parsing and report languages that have no pack.

**Architecture:** A pack (`svarupa/extract/packs/<lang>.py`) is a typed value: a table from syntax node type to one of seven rule kinds, plus a few hook functions for what data cannot say. `walker.py` is the only code that walks a tree; it owns evidence, fact order, the depth cap and syntax-error diagnostics. Golden facts recorded from the current extractors before any change prove the port moved nothing.

**Tech Stack:** Python 3.10 to 3.13, py-tree-sitter 0.26, tree-sitter-python 0.25.0, tree-sitter-typescript 0.23.2, pytest, ruff, pyright (strict).

**Spec:** `docs/superpowers/specs/2026-10-03-language-packs-design.md` (read "Revision 1" at its end) and the parent `docs/superpowers/specs/2026-10-03-system-health-umbrella-design.md`.

## Global Constraints

- Output is byte-identical to today for every input, except where Task 6 (JavaScript label and grammar) and Task 5 (SVA-X-012) change it on purpose, each in its own commit.
- No new runtime dependency. Grammar pins stay `tree-sitter-python==0.25.0` and `tree-sitter-typescript==0.23.2`.
- Python 3.10 compatible: no `match` on types that 3.10 lacks, no `tomllib`, no PEP 695 syntax.
- `uv run pyright svarupa` must pass in strict mode; `uv run ruff check svarupa tests` and `uv run ruff format --check svarupa tests` must pass.
- Every diagnostic code emitted is registered in `svarupa/diagnostics.py` `CODES` and every registered code is emitted (`tests/test_diagnostics.py` checks both directions and that the description shares a content word with the message).
- Never push to `main`. Work on branch `feat/language-packs-1a-1`, one commit per task, PR at the end. No AI attribution in commits or PRs.
- Plain English, no em dashes, in comments, docs, commit messages and the PR.
- Commit subject under 72 characters, imperative mood.

## Review Focus

1. A Python decorated definition inside a class (`@staticmethod def f` in a class body): the decorator must reach the method symbol and the method kind must stay `method`. Pinned by the `decorators` golden case (Task 1) and asserted again in Task 3 Step 6.
2. Calls written inside decorator arguments, default arguments and class base lists are not recorded today; a walker that visits every child would add them. Pinned by the `calls` golden case and asserted in Task 2 Step 1 (`test_skipped_regions_stay_skipped`).
3. A minified file nested deeper than `MAX_AST_DEPTH`: one SVA-X-003, no crash, other files still extracted, and the walker must not hit Python's recursion limit first. Pinned by Task 2 `test_depth_cap_degrades_without_recursion_error` with depth 900.
4. A `.jsx` or `.js` file containing JSX: today it yields SVA-X-001 and partial facts; after Task 6 it parses cleanly and keeps label `javascript`, and `./a.js` imports from `.js` still resolve. Pinned by the `jsx_in_js` golden case and Task 6 tests.
5. A repository with Go, Rust or Java files: one SVA-X-012 per language, not per file, and no change for repositories without them. Pinned by Task 5 tests.

---

## File Structure

| File | Responsibility |
|---|---|
| `tests/golden/python/*.txt`, `tests/golden/typescript/*.txt` | Source cases. First line `path: <virtual path>`, then a line `---`, then the source. |
| `tests/golden/**/*.json` | Recorded `FileFacts` for each case, canonical JSON. |
| `tests/test_golden_facts.py` | Parses each case with the active extractor and byte-compares with its JSON. |
| `svarupa/extract/packs/__init__.py` | Registry: `PACKS`, `BY_DETECTED`, `ANALYZED_ELSEWHERE`, `load_extractors()`, `extractor()`. |
| `svarupa/extract/packs/model.py` | Rule kinds (`Define`, `Decorated`, `Import`, `Call`, `Field`, `Carry`, `Custom`), `Grammar`, `Pack`, `Maturity`, hook type aliases. |
| `svarupa/extract/packs/walker.py` | `Frame`, `Ctx`, `PackExtractor`, `language_for()`. |
| `svarupa/extract/packs/python.py` | Python pack and hooks (port of `extract/python.py`). |
| `svarupa/extract/packs/typescript.py` | TypeScript pack and hooks (port of `extract/typescript.py`). |
| `svarupa/extract/packs/javascript.py` | JavaScript pack (Task 6). |
| `tests/test_pack_walker.py` | Walker semantics on tiny synthetic packs. |
| `tests/test_packs.py` | Pack self-checks and registry behavior. |
| `scripts/mutate_packs.py` | Mutation check for the walker. |
| Deleted: `svarupa/extract/python.py`, `svarupa/extract/typescript.py` | Replaced by packs. |

---

### Task 1: Golden facts for the current extractors

**Files:**
- Create: `tests/test_golden_facts.py`
- Create: `tests/golden/python/{definitions,imports,package_init,decorators,calls,syntax_error}.txt`
- Create: `tests/golden/typescript/{imports,exports,nest_controller,express,component,jsx_in_js,syntax_error}.txt`
- Create (generated): the matching `.json` file next to each `.txt`

**Interfaces:**
- Consumes: `svarupa.extract._EXTRACTORS: dict[str, Extractor]` (exists today), `svarupa.detect.LANG_BY_EXT: dict[str, str]`.
- Produces: `render(facts: FileFacts) -> str` and the golden files; later tasks only run this test, they never edit it except Task 6 regenerating JSON.

- [ ] **Step 1: Create the branch**

```bash
git switch main && git pull --ff-only && git switch -c feat/language-packs-1a-1
```

- [ ] **Step 2: Write the golden test**

`tests/test_golden_facts.py`:

```python
"""Golden facts: the exact pass-1 output for a fixed set of source files.

Extraction is moving from hand-written extractors to language packs. The
whole-artifact byte tests would notice a change but not say which fact
moved. These files pin every fact, in order, so a port either matches them
byte for byte or shows the exact difference.

After an intended change, regenerate and review the diff like code:

    SVARUPA_UPDATE_GOLDEN=1 uv run pytest tests/test_golden_facts.py
"""

from __future__ import annotations

import dataclasses
import enum
import json
import os
from collections.abc import Mapping
from pathlib import Path

import pytest

from svarupa.detect import LANG_BY_EXT
from svarupa.extract import _EXTRACTORS
from svarupa.extract.base import FileFacts

GOLDEN = Path(__file__).parent / "golden"
CASES = sorted(GOLDEN.glob("*/*.txt"))


def _jsonable(value: object) -> object:
    # Mappings become pair lists, not objects: `alias_of` is read in
    # insertion order downstream, and a JSON object would hide a reorder.
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: _jsonable(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, Mapping):
        return [[_jsonable(k), _jsonable(v)] for k, v in value.items()]
    if isinstance(value, (tuple, list)):
        return [_jsonable(v) for v in value]
    return value


def render(facts: FileFacts) -> str:
    return json.dumps(_jsonable(facts), indent=1, ensure_ascii=False) + "\n"


def load_case(case: Path) -> tuple[str, bytes]:
    header, sep, body = case.read_bytes().partition(b"\n---\n")
    assert sep, f"{case}: missing the '---' line after the path header"
    virtual = header.decode("utf8").removeprefix("path: ").strip()
    return virtual, body


def test_the_case_set_is_complete() -> None:
    # Guards the parametrized test below against passing vacuously.
    assert len(CASES) == 13, [c.name for c in CASES]


@pytest.mark.parametrize("case", CASES, ids=lambda c: f"{c.parent.name}/{c.stem}")
def test_facts_match_golden(case: Path) -> None:
    virtual, src = load_case(case)
    lang = LANG_BY_EXT[Path(virtual).suffix]
    got = render(_EXTRACTORS[lang].parse(virtual, src))
    golden = case.with_suffix(".json")
    if os.environ.get("SVARUPA_UPDATE_GOLDEN") == "1":
        golden.write_text(got, encoding="utf8")
        return
    assert golden.exists(), f"no golden file; run with SVARUPA_UPDATE_GOLDEN=1: {golden}"
    assert got == golden.read_text(encoding="utf8")
```

- [ ] **Step 3: Write the Python cases**

`tests/golden/python/definitions.txt`:

```
path: src/shop/orders.py
---
"""Orders."""
import logging


class Base:
    pass


class Order(Base, models.Model):
    class Meta:
        ordering = ["id"]

    def total(self):
        def helper(x):
            return x * 2
        return helper(self.amount)

    def _private(self):
        pass


@dataclass
class Line:
    qty: int


def make_order():
    return Order()


async def fetch():
    return None


def _hidden():
    pass
```

`tests/golden/python/imports.txt`:

```
path: src/shop/views.py
---
from __future__ import annotations
import os
import os.path, sys
import numpy as np
from . import models
from .models import Order, Line as OrderLine
from ..core.db import (
    session,
    engine,
)
from pkg.sub import *
```

`tests/golden/python/package_init.txt`:

```
path: src/shop/__init__.py
---
from .orders import Order, make_order
from .views import *
from shop.util import helper
import shop.extra
```

`tests/golden/python/decorators.txt`:

```
path: src/api/routes.py
---
from fastapi import APIRouter
from flask import Blueprint

router = APIRouter()
bp = Blueprint("bp", __name__)
PATH = "/dyn"
METHODS = ["GET"]


@router.get("/items")
def list_items():
    return []


@router.post(PATH)
def create_item():
    return {}


@router.get(f"/items/{PATH}")
def fstring_path():
    return {}


@bp.route("/orders", methods=["GET", "POST"])
def orders():
    return ""


@bp.route("/dyn", methods=METHODS)
def dynamic_methods():
    return ""


@bp.route("/mixed", methods=["GET", verb])
def mixed_methods():
    return ""


@bp.route("/plain")
def plain_route():
    return ""


class Thing:
    @staticmethod
    def build():
        return Thing()

    @property
    def name(self):
        return "x"


@celery.task(bind=True)
def job(self):
    return None


@router.get(name="named", path="/kw")
def keyword_only():
    return None
```

`tests/golden/python/calls.txt`:

```
path: src/svc/service.py
---
import helpers
from repo import Repo


def run(items=build_default()):
    print(len(items))
    helpers.prepare(items)
    local = Repo()
    local.save(items)
    result = compute(items)
    chain().then().done()
    return result


class Service(make_base()):
    registry = make_registry()

    def handle(self):
        self.validate()
        super().handle()
        super(Service, self).handle()
        self.repo.load()
        callback = lambda x: transform(x)
        return callback


@decorate(setup())
def wrapped():
    inner()
```

`tests/golden/python/syntax_error.txt`:

```
path: src/bad.py
---
def broken(:
    pass


def fine():
    ok()
```

- [ ] **Step 4: Write the TypeScript and JavaScript cases**

`tests/golden/typescript/imports.txt`:

```
path: src/app/imports.ts
---
import express from 'express';
import { Router, Request as Req } from 'express';
import * as path from "path";
import type { Config } from './config';
import { type Options, load } from './options';
import typeDefs from './schema';
import './side-effect';
import Default, { named } from '../shared/index';
```

`tests/golden/typescript/exports.txt`:

```
path: src/app/index.ts
---
export { UsersService, UsersService as Svc } from './users.service';
export * from './models';
export const handler = () => compute();
export const value = 3;
export function boot(): void {
  start();
}
export default class App {}
export interface Settings {
  port: number;
}
export abstract class Repo<T> extends BaseRepo<T> implements Store, Cache.Like {}
function _internal() {}
const local = function () { return 1; };
```

`tests/golden/typescript/nest_controller.txt`:

```
path: src/users/users.controller.ts
---
import { Controller, Get, Post } from '@nestjs/common';
import { UsersService } from './users.service';

const PATH = 'dynamic';

@Controller('users')
export class UsersController {
  repo: Repository<User>;
  private readonly cache?: Cache | null;

  constructor(
    private readonly usersService: UsersService,
    public logger: Logger,
    config: ConfigService,
  ) {}

  @Get(':id')
  findOne() {
    return this.usersService.findOne();
  }

  @Post(PATH)
  create() {
    this.repo.save();
    return super.create();
  }

  @Get()
  list() {
    return this.helper();
  }

  @Get('/a\'b')
  escaped() {}

  @Get(`/t/${PATH}`)
  templated() {}

  _hidden() {}
}

@Injectable()
class Internal {
  run() {}
}
```

`tests/golden/typescript/express.txt`:

```
path: src/server.js
---
const express = require('express');
const { Router, json } = require('express');
const lazy = require(`./lazy`);
const app = express();
const router = Router();

app.use(json());
app.get('/health', (req, res) => res.send('ok'));
app.use('/api', router);
router.post('/items', createItem);
app
  .route('/orders')
  .get(listOrders)
  .delete(removeOrder);
console.log('started');
fetch('/x');
app.listen(port, () => {
  log(`listening ${port}`);
});
```

`tests/golden/typescript/component.txt`:

```
path: src/App.tsx
---
import React, { useState } from 'react';
import { Button } from './Button';

export function App() {
  const [count, setCount] = useState(0);
  return <Button onClick={() => setCount(count + 1)}>{count}</Button>;
}

export const Panel = ({ title }: { title: string }) => <section>{title}</section>;
```

`tests/golden/typescript/jsx_in_js.txt`:

```
path: src/Button.jsx
---
import React from 'react';

export function Button({ onClick, children }) {
  return <button onClick={onClick}>{children}</button>;
}

export const Icon = () => <svg />;
```

`tests/golden/typescript/syntax_error.txt`:

```
path: src/broken.ts
---
export function broken( {
  return 1;
}

export function fine() {
  ok();
}
```

- [ ] **Step 5: Run the test to see it fail**

Run: `uv run pytest tests/test_golden_facts.py -q`
Expected: 13 FAIL with "no golden file; run with SVARUPA_UPDATE_GOLDEN=1", 1 PASS.

- [ ] **Step 6: Record the goldens from the current extractors**

Run: `SVARUPA_UPDATE_GOLDEN=1 uv run pytest tests/test_golden_facts.py -q && uv run pytest tests/test_golden_facts.py -q`
Expected: second run 14 passed.

- [ ] **Step 7: Read the goldens and confirm they show the known behavior**

Run: `grep -c '"code": "SVA-X-001"' tests/golden/typescript/jsx_in_js.json tests/golden/python/syntax_error.json tests/golden/typescript/syntax_error.json`
Expected: each count is 1. (The `.jsx` file shows today's bug.)

Run: `grep -o '"lang": "[a-z]*"' tests/golden/typescript/express.json | sort -u`
Expected: `"lang": "typescript"` (today's mislabel; Task 6 changes it).

Run: `grep -c '"name": "build_default"\|"name": "setup"\|"name": "make_base"' tests/golden/python/calls.json`
Expected: `0` (calls in default arguments, decorator arguments and base lists are not recorded today).

- [ ] **Step 8: Run the whole suite and commit**

Run: `uv run pytest -q`
Expected: all pass.

```bash
git add tests/test_golden_facts.py tests/golden
git commit -m "Add golden pass-1 facts for Python and TypeScript extractors"
```

---

### Task 2: Pack model and walker

**Files:**
- Create: `svarupa/extract/packs/__init__.py` (registry, no packs yet)
- Create: `svarupa/extract/packs/model.py`
- Create: `svarupa/extract/packs/walker.py`
- Modify: `svarupa/extract/__init__.py:50-59` (merge pack extractors into `_EXTRACTORS`)
- Test: `tests/test_pack_walker.py`

**Interfaces:**
- Consumes: `svarupa.extract.base` (`MAX_AST_DEPTH`, `CallSite`, `DecoratorRef`, `Extractor`, `FieldType`, `FileFacts`, `ImportRef`, `SymbolRef`, `depth_capped`), `svarupa.model.Evidence`, `svarupa.diagnostics`.
- Produces (used by Tasks 3 to 7):
  - `model.Define(kind, kind_in_class=None, name_field="name", body_field="body", inherit_exported=False, sets_class=False, records_class=True, keeps_decorators=True, recurse_without_name=False, own_decorators=None, member_decorators=None, bases=None, after=None)`
  - `model.Decorated(decorator_type="decorator", definition_field="definition")`
  - `model.Import(hook, recurse_on_none=False)`, `model.Call(hook)`, `model.Field(hook)`, `model.Carry()`, `model.Custom(hook)`
  - `model.Grammar(distribution, version, module, default, by_suffix=())`, `model.Maturity.STABLE/EXPERIMENTAL`
  - `model.Pack(lang, grammar, maturity, rules, qualified_prefix, decorator=None)`
  - Hook aliases: `ImportHook = Callable[[Ctx, TSNode], ImportRef | None]`, `CallHook = Callable[[Ctx, TSNode, Frame], CallSite | None]`, `FieldHook = Callable[[Ctx, TSNode, str], list[FieldType]]`, `CustomHook = Callable[[Ctx, TSNode, Frame, int], None]`, `DecoratorHook = Callable[[Ctx, TSNode], DecoratorRef | None]`, `BasesHook = Callable[[Ctx, TSNode], tuple[str, ...]]`, `AfterHook = Callable[[Ctx, TSNode, Frame, str], None]`
  - `walker.Frame(stack=(), cls=None, fn=None, exported=False, decorators=())` with `.plain() -> Frame`
  - `walker.Ctx` attributes `path, data, pack, prefix, symbols, imports, calls, fields, ctor_assigns, reexports`; methods `text(node) -> str`, `evidence(start_row, end_row) -> Evidence`, `node_evidence(node) -> Evidence`, `qual(names: tuple[str, ...]) -> str`, `decorator(node) -> DecoratorRef | None`, `visit(node, frame, depth) -> None`
  - `walker.PackExtractor(pack)` with `.lang`, `.grammar_version`, `.parse(path, data) -> FileFacts`, `.available() -> str | None` (None when the grammar loads, else the reason)
  - `walker.language_for(grammar, path) -> Language`
  - Registry in `svarupa/extract/packs/__init__.py`: `PACKS: tuple[Pack, ...]`, `BY_DETECTED: dict[str, Pack]`, `load_extractors(by_detected: Mapping[str, Pack] = BY_DETECTED) -> tuple[dict[str, PackExtractor], dict[str, str]]` (extractors whose grammar loads, and language to reason for those that do not), `extractor(lang: str) -> PackExtractor`
  - In `svarupa/extract/__init__.py`: `_PACK_EXTRACTORS`, `_UNAVAILABLE: dict[str, str]`

- [ ] **Step 1: Write the failing walker tests**

`tests/test_pack_walker.py`:

```python
"""The walker's own contract, on tiny packs over the Python grammar.

The real packs are tested by the golden facts. These tests pin what every
pack inherits: fact order, scope and qualified names, which regions are
walked, the depth cap and the syntax-error diagnostic.
"""

from __future__ import annotations

from tree_sitter import Node as TSNode

from svarupa.extract.base import MAX_AST_DEPTH, CallShape, CallSite, DecoratorRef
from svarupa.extract.packs.model import (
    Call,
    Decorated,
    Define,
    Grammar,
    Maturity,
    Pack,
)
from svarupa.extract.packs.walker import Ctx, Frame, PackExtractor

GRAMMAR = Grammar(
    distribution="tree-sitter-python",
    version="0.25.0",
    module="tree_sitter_python",
    default="language",
)


def _bare_call(ctx: Ctx, node: TSNode, frame: Frame) -> CallSite | None:
    func = node.child_by_field_name("function")
    if func is None or func.type != "identifier":
        return None
    row = node.start_point[0]
    return CallSite(
        ctx.text(func), CallShape.BARE, None, ctx.evidence(row, row), frame.fn, frame.cls
    )


def _decorator(ctx: Ctx, node: TSNode) -> DecoratorRef | None:
    text = ctx.text(node).lstrip("@").split("(")[0]
    return DecoratorRef(name=text, arg=None, evidence=ctx.node_evidence(node))


PACK = Pack(
    lang="toy",
    grammar=GRAMMAR,
    maturity=Maturity.EXPERIMENTAL,
    qualified_prefix=lambda path: path.rsplit(".", 1)[0].replace("/", "."),
    decorator=_decorator,
    rules={
        "decorated_definition": Decorated(),
        "class_definition": Define(kind="class", sets_class=True),
        "function_definition": Define(kind="function", kind_in_class="method"),
        "call": Call(hook=_bare_call),
    },
)


def parse(src: str, path: str = "pkg/mod.py"):
    return PackExtractor(PACK).parse(path, src.encode())


def test_definitions_get_scope_kind_and_qualified_names() -> None:
    f = parse("class A:\n    def m(self):\n        def inner():\n            pass\n")
    got = [(s.qualified_name, s.kind, s.enclosing_class) for s in f.symbols]
    assert got == [
        ("pkg.mod.A", "class", None),
        ("pkg.mod.A.m", "method", "A"),
        ("pkg.mod.A.m.inner", "method", "A"),
    ]


def test_calls_keep_document_order_and_enclosing_function() -> None:
    f = parse("def go():\n    b()\n    a()\n\nc()\n")
    assert [(c.name, c.enclosing) for c in f.calls] == [
        ("b", "pkg.mod.go"),
        ("a", "pkg.mod.go"),
        ("c", None),
    ]


def test_skipped_regions_stay_skipped() -> None:
    # Default arguments, base lists and decorator arguments are not walked:
    # a Define walks only its body, a Decorated only its definition.
    f = parse("@deco(setup())\nclass A(base()):\n    def m(self, x=default()):\n        body()\n")
    assert [c.name for c in f.calls] == ["body"]


def test_decorators_reach_the_definition_they_wrap() -> None:
    f = parse("class A:\n    @staticmethod\n    def m():\n        pass\n")
    m = next(s for s in f.symbols if s.name == "m")
    assert m.kind == "method"
    assert [d.name for d in m.decorators] == ["staticmethod"]


def test_exported_defaults_to_the_name_rule() -> None:
    f = parse("def public():\n    pass\n\ndef _private():\n    pass\n")
    assert [(s.name, s.exported) for s in f.symbols] == [("public", True), ("_private", False)]


def test_evidence_lines_are_one_based_and_span_the_node() -> None:
    f = parse("\n\ndef go():\n    pass\n")
    ev = f.symbols[0].evidence
    assert (ev.file, ev.start_line, ev.end_line) == ("pkg/mod.py", 3, 4)


def test_syntax_errors_are_reported_once() -> None:
    f = parse("def broken(:\n    pass\n\ndef fine():\n    pass\n")
    assert [d.code for d in f.diagnostics] == ["SVA-X-001"]


def test_depth_cap_degrades_without_recursion_error() -> None:
    depth = MAX_AST_DEPTH * 3
    f = parse("x = " + "(" * depth + "f()" + ")" * depth + "\n\ndef keep():\n    pass\n")
    assert [d.code for d in f.diagnostics] == ["SVA-X-003"]
    assert [s.name for s in f.symbols] == ["keep"]
    # The call sits below the cap, so the walk must not have reached it.
    assert f.calls == ()


def test_missing_grammar_is_reported_not_raised() -> None:
    from dataclasses import replace

    broken = replace(PACK, grammar=replace(GRAMMAR, module="tree_sitter_does_not_exist"))
    reason = PackExtractor(broken).available()
    assert reason is not None
    assert "tree-sitter-python" in reason


def test_working_grammar_is_available() -> None:
    assert PackExtractor(PACK).available() is None
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `uv run pytest tests/test_pack_walker.py -q`
Expected: collection error, `ModuleNotFoundError: No module named 'svarupa.extract.packs'`.

- [ ] **Step 3: Write the model**

`svarupa/extract/packs/__init__.py` (the registry; packs are added from Task 3 on):

```python
"""Language packs: one walker, one small pack per language.

`model.py` defines what a pack may say, `walker.py` is the only code that
turns a syntax tree into facts, and each `<lang>.py` is one pack. This
module says which pack serves which language label from `detect`.
"""

from __future__ import annotations

from collections.abc import Mapping

from svarupa.extract.packs.model import Pack
from svarupa.extract.packs.walker import PackExtractor

__all__ = ["BY_DETECTED", "PACKS", "extractor", "load_extractors"]

PACKS: tuple[Pack, ...] = ()

# detect.LANG_BY_EXT label -> pack.
BY_DETECTED: dict[str, Pack] = {}


def load_extractors(
    by_detected: Mapping[str, Pack] = BY_DETECTED,
) -> tuple[dict[str, PackExtractor], dict[str, str]]:
    """Extractors whose grammar loads, and the reason for each that does not."""
    ready: dict[str, PackExtractor] = {}
    missing: dict[str, str] = {}
    for lang, pack in by_detected.items():
        ex = PackExtractor(pack)
        reason = ex.available()
        if reason is None:
            ready[lang] = ex
        else:
            missing[lang] = reason
    return ready, missing


def extractor(lang: str) -> PackExtractor:
    """The extractor for a detected language label. KeyError if none."""
    return PackExtractor(BY_DETECTED[lang])
```

`svarupa/extract/packs/model.py`:

```python
"""What a language pack is made of.

A pack maps syntax node types to rules. Rules are data: which field holds
the name, which holds the body, whether the definition opens a class scope.
Hooks are the few functions a language needs because data cannot say it:
how an import is spelled, which call target is a builtin, how a string
literal decodes. The walker reads the rules; it never knows the language.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

from tree_sitter import Node as TSNode

from svarupa.extract.base import CallSite, DecoratorRef, FieldType, ImportRef

if TYPE_CHECKING:
    from svarupa.extract.packs.walker import Ctx, Frame

__all__ = [
    "AfterHook",
    "BasesHook",
    "Call",
    "CallHook",
    "Carry",
    "Custom",
    "CustomHook",
    "Decorated",
    "DecoratorHook",
    "Define",
    "Field",
    "FieldHook",
    "Grammar",
    "Import",
    "ImportHook",
    "Maturity",
    "Pack",
    "Rule",
]

# Ctx and Frame are quoted: walker.py imports this module, so they exist
# only for the type checker here.
ImportHook = Callable[["Ctx", TSNode], ImportRef | None]
CallHook = Callable[["Ctx", TSNode, "Frame"], CallSite | None]
FieldHook = Callable[["Ctx", TSNode, str], list[FieldType]]
CustomHook = Callable[["Ctx", TSNode, "Frame", int], None]
DecoratorHook = Callable[["Ctx", TSNode], DecoratorRef | None]
BasesHook = Callable[["Ctx", TSNode], tuple[str, ...]]
AfterHook = Callable[["Ctx", TSNode, "Frame", str], None]


class Maturity(str, Enum):
    """Stable once the benchmark proves a pack; experimental until then."""

    STABLE = "stable"
    EXPERIMENTAL = "experimental"


@dataclass(frozen=True, slots=True)
class Define:
    """A definition: emits one SymbolRef, then walks only its body.

    Walking only the body is the contract, not an optimization: calls in
    default arguments, base lists and decorator arguments have never been
    recorded, and the golden facts pin that.
    """

    kind: str
    kind_in_class: str | None = None  # Python: a def under a class is a method
    name_field: str = "name"
    body_field: str | None = "body"  # None: the body is not walked
    inherit_exported: bool = False  # False: exported means no leading underscore
    sets_class: bool = False  # the body's frame gets this name as its class
    records_class: bool = True  # fill SymbolRef.enclosing_class
    keeps_decorators: bool = True  # take pending decorators from the frame
    recurse_without_name: bool = False  # nameless node: walk children, or stop
    own_decorators: str | None = None  # child type read as own decorators
    member_decorators: str | None = None  # body child type paired with next member
    bases: BasesHook | None = None
    after: AfterHook | None = None  # runs after the symbol, before the body


@dataclass(frozen=True, slots=True)
class Decorated:
    """Decorators wrapping one definition; only the definition is walked."""

    decorator_type: str = "decorator"
    definition_field: str = "definition"


@dataclass(frozen=True, slots=True)
class Import:
    hook: ImportHook
    recurse_on_none: bool = False  # walk children when the hook finds no import


@dataclass(frozen=True, slots=True)
class Call:
    """A call. Its children are walked too: arguments hold calls."""

    hook: CallHook


@dataclass(frozen=True, slots=True)
class Field:
    """A typed class field. Only inside a class; children are walked too."""

    hook: FieldHook


@dataclass(frozen=True, slots=True)
class Carry:
    """Walk the children and keep the exported flag (`export const f = ...`)."""


@dataclass(frozen=True, slots=True)
class Custom:
    """The hook owns this node, including whether and how to walk below it."""

    hook: CustomHook


Rule = Define | Decorated | Import | Call | Field | Carry | Custom


@dataclass(frozen=True, slots=True)
class Grammar:
    distribution: str  # PyPI name, pinned == in pyproject.toml
    version: str  # must equal the installed distribution's version
    module: str  # import name
    default: str  # function in `module` returning the language pointer
    by_suffix: tuple[tuple[str, str], ...] = ()  # (".tsx", "language_tsx")


@dataclass(frozen=True, slots=True)
class Pack:
    lang: str  # the label every fact from this pack carries
    grammar: Grammar
    maturity: Maturity
    rules: Mapping[str, Rule]
    qualified_prefix: Callable[[str], str]
    decorator: DecoratorHook | None = None
```

- [ ] **Step 4: Write the walker**

`svarupa/extract/packs/walker.py`:

```python
"""The one place a syntax tree becomes facts.

Every pack runs through here, so the rules that make facts trustworthy are
written once: evidence comes from the node's own rows, a tree deeper than
MAX_AST_DEPTH degrades with SVA-X-003 instead of crashing, a file with
syntax errors says so with SVA-X-001, and facts come out in document
order. Several resolver steps are first-seen-wins, so that order is part of
the contract.

The generic path costs one interpreter frame per tree level (children are
walked in `visit`'s own loop), so the depth cap trips long before Python's
recursion limit does.
"""

from __future__ import annotations

import importlib
from collections.abc import Callable
from dataclasses import dataclass, replace
from functools import cache
from typing import cast

from tree_sitter import Language, Parser, Tree
from tree_sitter import Node as TSNode

from svarupa.diagnostics import Diagnostic, Severity
from svarupa.extract.base import (
    MAX_AST_DEPTH,
    CallSite,
    DecoratorRef,
    Extractor,
    FieldType,
    FileFacts,
    ImportRef,
    SymbolRef,
    depth_capped,
)
from svarupa.extract.packs.model import (
    Call,
    Carry,
    Custom,
    Decorated,
    Define,
    Field,
    Grammar,
    Import,
    Pack,
)
from svarupa.model import Evidence

__all__ = ["Ctx", "Frame", "PackExtractor", "language_for"]


@dataclass(frozen=True, slots=True)
class Frame:
    """Where the walk is: scope names, enclosing class and function, and the
    two things a parent hands to exactly one child (exported, decorators)."""

    stack: tuple[str, ...] = ()
    cls: str | None = None
    fn: str | None = None
    exported: bool = False
    decorators: tuple[DecoratorRef, ...] = ()

    def plain(self) -> Frame:
        """The frame a generic child gets: same scope, nothing handed down."""
        return Frame(self.stack, self.cls, self.fn)


class Ctx:
    """One file's walk: the source, the pack, and the facts so far."""

    def __init__(self, pack: Pack, path: str, data: bytes) -> None:
        self.pack = pack
        self.path = path
        self.data = data
        self.prefix = pack.qualified_prefix(path)
        self.symbols: list[SymbolRef] = []
        self.imports: list[ImportRef] = []
        self.calls: list[CallSite] = []
        self.fields: list[FieldType] = []
        self.ctor_assigns: list[tuple[str, str]] = []
        self.reexports: list[str] = []
        self.too_deep = False

    def text(self, node: TSNode) -> str:
        return self.data[node.start_byte : node.end_byte].decode("utf8", "replace")

    def evidence(self, start_row: int, end_row: int) -> Evidence:
        return Extractor.evidence(self.path, start_row, end_row)

    def node_evidence(self, node: TSNode) -> Evidence:
        return self.evidence(node.start_point[0], node.end_point[0])

    def qual(self, names: tuple[str, ...]) -> str:
        return ".".join((self.prefix, *names)) if names else self.prefix

    def decorator(self, node: TSNode) -> DecoratorRef | None:
        hook = self.pack.decorator
        return hook(self, node) if hook is not None else None

    def visit(self, node: TSNode, frame: Frame, depth: int) -> None:
        if depth > MAX_AST_DEPTH:
            self.too_deep = True
            return
        rule = self.pack.rules.get(node.type)
        if isinstance(rule, Define):
            self._define(node, rule, frame, depth)
            return
        if isinstance(rule, Decorated):
            decs = tuple(
                d
                for child in node.children
                if child.type == rule.decorator_type
                if (d := self.decorator(child)) is not None
            )
            definition = node.child_by_field_name(rule.definition_field)
            if definition is not None:
                self.visit(definition, replace(frame, decorators=decs), depth + 1)
            return
        if isinstance(rule, Custom):
            rule.hook(self, node, frame, depth)
            return
        if isinstance(rule, Carry):
            carried = Frame(frame.stack, frame.cls, frame.fn, exported=frame.exported)
            for child in node.children:
                self.visit(child, carried, depth + 1)
            return
        if isinstance(rule, Import):
            ref = rule.hook(self, node)
            if ref is not None:
                self.imports.append(ref)
                return
            if not rule.recurse_on_none:
                return
        elif isinstance(rule, Call):
            site = rule.hook(self, node, frame)
            if site is not None:
                self.calls.append(site)
        elif isinstance(rule, Field) and frame.cls:
            self.fields.extend(rule.hook(self, node, frame.cls))
        plain = frame.plain()
        for child in node.children:
            self.visit(child, plain, depth + 1)

    def _define(self, node: TSNode, rule: Define, frame: Frame, depth: int) -> None:
        name_node = node.child_by_field_name(rule.name_field)
        if name_node is None:
            if rule.recurse_without_name:
                plain = frame.plain()
                for child in node.children:
                    self.visit(child, plain, depth + 1)
            return
        decorators = frame.decorators if rule.keeps_decorators else ()
        if rule.own_decorators is not None:
            decorators += tuple(
                d
                for child in node.children
                if child.type == rule.own_decorators
                if (d := self.decorator(child)) is not None
            )
        name = self.text(name_node)
        qualified = self.qual((*frame.stack, name))
        self.symbols.append(
            SymbolRef(
                name=name,
                qualified_name=qualified,
                kind=rule.kind_in_class if rule.kind_in_class and frame.cls else rule.kind,
                evidence=self.node_evidence(node),
                enclosing_class=frame.cls if rule.records_class else None,
                bases=rule.bases(self, node) if rule.bases is not None else (),
                exported=frame.exported if rule.inherit_exported else not name.startswith("_"),
                decorators=decorators,
            )
        )
        if rule.after is not None:
            rule.after(self, node, frame, name)
        if rule.body_field is None:
            return
        body = node.child_by_field_name(rule.body_field)
        if body is None:
            return
        scope = (*frame.stack, name)
        inner = (
            Frame(scope, name, frame.fn) if rule.sets_class else Frame(scope, frame.cls, qualified)
        )
        if rule.member_decorators is None:
            for child in body.children:
                self.visit(child, inner, depth + 1)
            return
        # Member decorators are siblings that precede the member they decorate.
        pending: list[DecoratorRef] = []
        for child in body.children:
            if child.type == rule.member_decorators:
                if (d := self.decorator(child)) is not None:
                    pending.append(d)
                continue
            self.visit(child, replace(inner, decorators=tuple(pending)), depth + 1)
            pending = []

    def run(self, tree: Tree) -> FileFacts:
        diags: list[Diagnostic] = []
        if tree.root_node.has_error:
            diags.append(
                Diagnostic(
                    code="SVA-X-001",
                    severity=Severity.WARNING,
                    message=(
                        "file has syntax errors; extraction continues over the "
                        "parseable regions and may be incomplete"
                    ),
                    subject=self.path,
                )
            )
        self.visit(tree.root_node, Frame(), 0)
        if self.too_deep:
            diags.append(depth_capped(self.path))
        return FileFacts(
            path=self.path,
            lang=self.pack.lang,
            symbols=tuple(self.symbols),
            imports=tuple(self.imports),
            calls=tuple(self.calls),
            fields=tuple(self.fields),
            reexports=tuple(sorted(set(self.reexports))),
            diagnostics=tuple(diags),
            ctor_assigns=tuple(self.ctor_assigns),
        )


@cache
def _load(module: str, function: str) -> Language:
    factory = cast("Callable[[], object]", getattr(importlib.import_module(module), function))
    return Language(factory())


def language_for(grammar: Grammar, path: str) -> Language:
    for suffix, function in grammar.by_suffix:
        if path.endswith(suffix):
            return _load(grammar.module, function)
    return _load(grammar.module, grammar.default)


class PackExtractor(Extractor):
    """The Extractor contract, served by a pack."""

    def __init__(self, pack: Pack) -> None:
        self.pack = pack
        self.lang = pack.lang
        self.grammar_version = pack.grammar.version

    def available(self) -> str | None:
        """None when every grammar function loads, else why it does not."""
        g = self.pack.grammar
        try:
            for function in (g.default, *(f for _, f in g.by_suffix)):
                _load(g.module, function)
        except (ImportError, AttributeError, ValueError, TypeError) as exc:
            return f"the {g.distribution} grammar could not be loaded ({type(exc).__name__})"
        return None

    def parse(self, path: str, data: bytes) -> FileFacts:
        parser = Parser(language_for(self.pack.grammar, path))
        return Ctx(self.pack, path, data).run(parser.parse(data))
```

- [ ] **Step 5: Run the walker tests**

Run: `uv run pytest tests/test_pack_walker.py -q`
Expected: 10 passed.

If `test_depth_cap_degrades_without_recursion_error` raises `RecursionError`, a branch of `visit` is calling a helper per level; move the child loop back into `visit` itself.

- [ ] **Step 6: Wire the registry into extraction**

`tests/test_diagnostics.py` fails on any package module nothing imports, so the registry is wired now, while it is still empty. In `svarupa/extract/__init__.py`, add the import:

```python
from svarupa.extract.packs import load_extractors
```

and replace the `_EXTRACTORS` assignment with:

```python
_PACK_EXTRACTORS, _UNAVAILABLE = load_extractors()

_EXTRACTORS: dict[str, Extractor] = {
    "python": PythonExtractor(),
    "typescript": TypeScriptExtractor(),
    # .js/.jsx parse fine with the TypeScript grammar, which is a superset.
    "javascript": TypeScriptExtractor(),
    # A language served by a pack overrides its hand-written extractor.
    **_PACK_EXTRACTORS,
}
```

- [ ] **Step 7: Lint, type-check, full suite**

Run: `uv run ruff check svarupa tests && uv run ruff format --check svarupa tests && uv run pyright svarupa && uv run pytest -q`
Expected: all clean, all pass. `tests/test_diagnostics.py` passes: the new modules are reachable through the registry import, and SVA-X-001 is emitted both by the old extractors and by `walker.py`.

- [ ] **Step 8: Commit**

```bash
git add svarupa/extract/packs svarupa/extract/__init__.py tests/test_pack_walker.py
git commit -m "Add language pack model and generic syntax tree walker"
```

---

### Task 3: Python pack with zero diff

**Files:**
- Create: `svarupa/extract/packs/python.py`
- Modify: `svarupa/extract/packs/__init__.py` (add the pack)
- Modify: `svarupa/extract/__init__.py` (drop `PythonExtractor`)
- Modify: `tests/test_extract.py:17,38,345-347,577-579,644-650`
- Create then delete in this task: `tests/test_pack_parity.py`
- Delete: `svarupa/extract/python.py`

**Interfaces:**
- Consumes: everything Task 2 produced, including the registry.
- Produces: `svarupa.extract.packs.python.PACK: Pack`, registered under `"python"`.

- [ ] **Step 1: Write the Python pack**

`svarupa/extract/packs/python.py`:

```python
"""Python language pack.

The rules are data. The hooks are the parts of Python data cannot say: how
`from ..x import y as z` spells a relative import, which bare names are
builtins, how a decorator carries a route path. Each hook is a direct port
of the hand-written extractor it replaced; tests/golden/python pins that
nothing moved.
"""

from __future__ import annotations

from tree_sitter import Node as TSNode

from svarupa.extract.base import (
    CallShape,
    CallSite,
    DecoratorRef,
    ImportRef,
    qualified_prefix,
)
from svarupa.extract.packs.model import (
    Call,
    Decorated,
    Define,
    Grammar,
    Import,
    Maturity,
    Pack,
)
from svarupa.extract.packs.walker import Ctx, Frame

__all__ = ["BUILTIN_CALLS", "PACK"]

# Builtins that would otherwise look like unresolved intra-repo calls. Kept
# small and explicit rather than pulling in the whole `builtins` namespace,
# because a project is free to define its own `list` or `filter` and we want
# the local definition to win.
# fmt: off
BUILTIN_CALLS = frozenset(
    {
        "print", "len", "range", "enumerate", "zip", "map", "filter", "sorted",
        "isinstance", "issubclass", "hasattr", "getattr", "setattr", "delattr",
        "int", "str", "float", "bool", "list", "dict", "set", "tuple", "bytes",
        "type", "super", "open", "iter", "next", "repr", "format", "abs", "min",
        "max", "sum", "any", "all", "round", "id", "hash", "vars", "dir",
        "callable", "reversed", "slice", "frozenset", "bytearray", "complex",
        "divmod", "pow", "ord", "chr", "hex", "oct", "bin", "input", "eval",
        "exec", "compile", "globals", "locals", "staticmethod", "classmethod",
        "property", "object", "Exception", "ValueError", "TypeError", "KeyError",
        "IndexError", "RuntimeError", "StopIteration", "NotImplementedError",
        "AttributeError", "OSError", "IOError", "ZeroDivisionError",
    }
)
# fmt: on


def _string_literal(ctx: Ctx, node: TSNode) -> str | None:
    """The content of a plain string literal, or None if it is not one.

    An f-string with interpolation is dynamic: returning its static parts
    would invent a route path that is not the real one.
    """
    if any(c.type == "interpolation" for c in node.children):
        return None
    parts = [c for c in node.children if c.type == "string_content"]
    if not parts:
        return ""  # an empty string literal has no content node
    return "".join(ctx.text(c) for c in parts)


def decorator(ctx: Ctx, node: TSNode) -> DecoratorRef | None:
    """One decorator, with the callee text, its first literal string
    argument, and any literal `methods=[...]` keyword.

    Anything dynamic (an f-string path, a computed methods list) is left
    out rather than guessed: a missing `arg` means "no literal path", not
    an empty path.
    """
    expr = next((c for c in node.children if c.type not in ("@", "comment")), None)
    if expr is None:
        return None
    ev = ctx.node_evidence(node)
    if expr.type != "call":
        name = ctx.text(expr)
        return DecoratorRef(name=name, arg=None, evidence=ev) if name else None
    callee = expr.child_by_field_name("function")
    if callee is None:
        return None
    arg: str | None = None
    # None: no `methods` kwarg at all. (): the kwarg is present but not a
    # literal collection of strings, so the methods are unknown. The two
    # must stay distinguishable: Flask's documented default applies only
    # to the first, and applying it to the second invents a method.
    methods: tuple[str, ...] | None = None
    args = expr.child_by_field_name("arguments")
    if args is not None:
        for child in args.children:
            if arg is None and child.type == "string":
                arg = _string_literal(ctx, child)
            if child.type == "keyword_argument":
                key = child.child_by_field_name("name")
                value = child.child_by_field_name("value")
                if key is None or ctx.text(key) != "methods" or value is None:
                    continue
                methods = ()
                if value.type in ("list", "tuple"):
                    items = [_string_literal(ctx, c) for c in value.children if c.type == "string"]
                    entries = sum(
                        1 for c in value.children if c.type not in ("[", "]", "(", ")", ",")
                    )
                    if items and len(items) == entries and all(i is not None for i in items):
                        methods = tuple(i for i in items if i is not None)
    return DecoratorRef(name=ctx.text(callee), arg=arg, evidence=ev, methods=methods)


def _is_package_init(path: str) -> bool:
    return path.endswith("/__init__.py") or path == "__init__.py"


def import_(ctx: Ctx, node: TSNode) -> ImportRef | None:
    ref = _import(ctx, node)
    # In a package __init__, `from .x import Y` is the re-export chain that
    # makes `from pkg import Y` work. Without following it, resolution lands
    # on the facade rather than the definition.
    if ref is not None and ref.is_relative and _is_package_init(ctx.path):
        ctx.reexports.extend(ref.names)
    return ref


def _import(ctx: Ctx, node: TSNode) -> ImportRef | None:
    ev = ctx.node_evidence(node)

    if node.type == "import_statement":
        names: list[str] = []
        alias: dict[str, str] = {}
        for child in node.children:
            if child.type == "dotted_name":
                names.append(ctx.text(child))
            elif child.type == "aliased_import":
                target = child.child_by_field_name("name")
                as_name = child.child_by_field_name("alias")
                if target is not None:
                    dotted = ctx.text(target)
                    names.append(dotted)
                    if as_name is not None:
                        alias[ctx.text(as_name)] = dotted
        if not names:
            return None
        return ImportRef(
            specifier=names[0],
            names=tuple(names),
            alias_of=alias,
            evidence=ev,
            is_relative=False,
            level=0,
            is_from=False,
        )

    # import_from_statement
    module_node = node.child_by_field_name("module_name")
    spec = ctx.text(module_node) if module_node is not None else ""
    level = len(spec) - len(spec.lstrip("."))
    names_out: list[str] = []
    alias_out: dict[str, str] = {}

    for child in node.children:
        # `is` is wrong here: py-tree-sitter hands back a fresh wrapper object
        # per `child_by_field_name` call, so identity never matches and the
        # module name was captured as if it were an imported symbol.
        if module_node is not None and child.id == module_node.id:
            continue
        if child.type == "dotted_name":
            names_out.append(ctx.text(child))
        elif child.type == "aliased_import":
            target = child.child_by_field_name("name")
            as_name = child.child_by_field_name("alias")
            if target is not None:
                real = ctx.text(target)
                names_out.append(real)
                if as_name is not None:
                    alias_out[ctx.text(as_name)] = real

    # `from x import *` binds an unknowable set; the module edge is kept and
    # the names are left empty rather than guessed.
    return ImportRef(
        specifier=spec,
        names=tuple(names_out),
        alias_of=alias_out,
        evidence=ev,
        is_relative=level > 0,
        level=level,
    )


def call(ctx: Ctx, node: TSNode, frame: Frame) -> CallSite | None:
    func = node.child_by_field_name("function")
    if func is None:
        return None
    row = node.start_point[0]
    ev = ctx.evidence(row, row)
    fn, cls = frame.fn, frame.cls

    if func.type == "identifier":
        name = ctx.text(func)
        if name in BUILTIN_CALLS:
            return None
        return CallSite(name, CallShape.BARE, None, ev, fn, cls)

    if func.type == "attribute":
        obj = func.child_by_field_name("object")
        attr = func.child_by_field_name("attribute")
        if attr is None or obj is None:
            return None
        name = ctx.text(attr)
        recv = ctx.text(obj)
        if recv == "self":
            return CallSite(name, CallShape.SELF, "self", ev, fn, cls)
        if obj.type == "call" and recv.startswith("super("):
            return CallSite(name, CallShape.SUPER, "super", ev, fn, cls)
        if obj.type == "identifier":
            # Ambiguous here: `recv` may be an imported module or a local.
            # The resolver decides, because only it knows the import table.
            return CallSite(name, CallShape.QUALIFIED, recv, ev, fn, cls)
        return CallSite(name, CallShape.MEMBER, recv, ev, fn, cls)

    return None


def bases(ctx: Ctx, node: TSNode) -> tuple[str, ...]:
    supers = node.child_by_field_name("superclasses")
    if supers is None:
        return ()
    return tuple(
        ctx.text(child).rsplit(".", 1)[-1]
        for child in supers.children
        if child.type in ("identifier", "attribute")
    )


PACK = Pack(
    lang="python",
    grammar=Grammar(
        distribution="tree-sitter-python",
        version="0.25.0",
        module="tree_sitter_python",
        default="language",
    ),
    maturity=Maturity.STABLE,
    qualified_prefix=lambda path: qualified_prefix(path, "python"),
    decorator=decorator,
    rules={
        "decorated_definition": Decorated(),
        "import_statement": Import(hook=import_),
        "import_from_statement": Import(hook=import_),
        "class_definition": Define(kind="class", sets_class=True, bases=bases),
        "function_definition": Define(kind="function", kind_in_class="method"),
        "call": Call(hook=call),
    },
)
```

Note on the ported `_import`: the old code tracked a `wildcard` flag that never changed the result (both branches leave `names_out` as it is). The port drops the flag; the golden `imports` and `package_init` cases (which contain `import *`) prove the output is the same.

- [ ] **Step 2: Register the Python pack**

In `svarupa/extract/packs/__init__.py`, add `from svarupa.extract.packs import python` below `from collections.abc import Mapping`, then:

```python
PACKS: tuple[Pack, ...] = (python.PACK,)

# detect.LANG_BY_EXT label -> pack.
BY_DETECTED: dict[str, Pack] = {"python": python.PACK}
```

- [ ] **Step 3: Write the temporary parity test**

`tests/test_pack_parity.py`:

```python
"""Temporary: the Python pack must equal the hand-written extractor on every
Python file in this repository. Deleted with the old extractor at the end of
this task; the golden facts keep guarding afterwards."""

from __future__ import annotations

from pathlib import Path

import pytest

from svarupa.extract.packs import extractor
from svarupa.extract.python import PythonExtractor

ROOT = Path(__file__).resolve().parents[1]
FILES = sorted(
    p
    for p in ROOT.rglob("*.py")
    if not {".venv", "node_modules", ".git"} & set(p.relative_to(ROOT).parts)
)


def test_the_corpus_is_not_empty() -> None:
    assert len(FILES) > 80


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.relative_to(ROOT).as_posix())
def test_python_pack_equals_the_old_extractor(path: Path) -> None:
    rel = path.relative_to(ROOT).as_posix()
    data = path.read_bytes()
    assert extractor("python").parse(rel, data) == PythonExtractor().parse(rel, data)
```

- [ ] **Step 4: Run parity**

Run: `uv run pytest tests/test_pack_parity.py -q`
Expected: all pass. A failure prints both `FileFacts`; fix the pack, never the old extractor.

Optional, local only (not committed): compare on other Python repositories on this machine:

```bash
uv run python - <<'EOF'
import sys
from pathlib import Path
from svarupa.extract.packs import extractor
from svarupa.extract.python import PythonExtractor
bad = 0
for root in sys.argv[1:] or [str(Path.home() / "Documents")]:
    for p in Path(root).rglob("*.py"):
        if any(x in p.parts for x in (".venv", "node_modules", "site-packages", ".git")):
            continue
        rel, data = p.as_posix(), p.read_bytes()
        if extractor("python").parse(rel, data) != PythonExtractor().parse(rel, data):
            bad += 1
            print("DIFF", rel)
print("diffs:", bad)
EOF
```

Expected: `diffs: 0`.

- [ ] **Step 5: Drop the hand-written Python extractor from the table**

The pack already overrides it (Task 2 merged pack extractors last). In `svarupa/extract/__init__.py`, delete `from svarupa.extract.python import PythonExtractor`, remove `"PythonExtractor",` from `__all__`, and delete the `"python": PythonExtractor(),` line from `_EXTRACTORS`.

- [ ] **Step 6: Point the tests at the pack**

In `tests/test_extract.py`:
- line 17: `from svarupa.extract.python import PythonExtractor` becomes `from svarupa.extract.packs import extractor`
- line 38: `return PythonExtractor().parse(path, src.encode())` becomes `return extractor("python").parse(path, src.encode())`
- lines 345 and 347: `PythonExtractor().parse(` becomes `extractor("python").parse(`
- lines 577 to 579: delete the local import line; `PythonExtractor().parse(` becomes `extractor("python").parse(`
- lines 644 to 650: replace the test body with:

```python
def test_grammar_version_matches_the_installed_pin() -> None:
    """Guards against silent drift when the grammar pin is bumped."""
    from importlib.metadata import version

    assert extractor("python").grammar_version == version("tree-sitter-python")
```

Then add this test at the end of `tests/test_extract.py` (Review Focus 1):

```python
def test_a_decorated_method_keeps_kind_and_decorator() -> None:
    f = facts("class A:\n    @staticmethod\n    def build():\n        pass\n")
    build = next(s for s in f.symbols if s.name == "build")
    assert build.kind == "method"
    assert build.enclosing_class == "A"
    assert [d.name for d in build.decorators] == ["staticmethod"]
```

Run: `grep -rn "extract.python\|PythonExtractor" svarupa tests scripts`
Expected: only `tests/test_pack_parity.py` and `svarupa/extract/python.py` itself.

- [ ] **Step 7: Run everything with both implementations present**

`svarupa/extract/python.py` is imported by nothing in the package now, so the orphan check is expected to fail until Step 8 deletes the file:

Run: `uv run pytest -q --deselect tests/test_diagnostics.py::test_every_package_module_is_reachable_by_import`
Expected: all pass, including `tests/test_golden_facts.py` (Python cases now served by the pack) and the parity test.

- [ ] **Step 8: Delete the old extractor and the parity test**

```bash
git rm svarupa/extract/python.py
rm tests/test_pack_parity.py
```

- [ ] **Step 9: Full check**

Run: `uv run ruff check svarupa tests && uv run ruff format --check svarupa tests && uv run pyright svarupa && uv run pytest -q && uv run svarupa . --lock && git diff --exit-code .svarupa/architecture.lock`
Expected: all clean, all pass, lockfile unchanged.

Run each mutation script that touches extraction and confirm none newly survive:

```bash
for s in scripts/mutate_*.py; do uv run python "$s" || echo "FAILED $s"; done
```

Expected: no `FAILED` line. If a script reports `pattern not found` because its pattern pointed into `svarupa/extract/python.py`, retarget that mutation to the same code in `svarupa/extract/packs/python.py` (same old/new text, new path) and rerun.

- [ ] **Step 10: Commit**

```bash
git add -A svarupa tests scripts
git commit -m "Serve Python extraction from a language pack"
```

---

### Task 4: TypeScript pack with zero diff

**Files:**
- Create: `svarupa/extract/packs/typescript.py`
- Modify: `svarupa/extract/packs/__init__.py` (add the pack)
- Modify: `svarupa/extract/__init__.py` (drop `TypeScriptExtractor`)
- Modify: `tests/test_extract_typescript.py:21,38,370-373`, `tests/test_semantics.py:956-958`
- Create then delete in this task: `tests/test_pack_parity.py`
- Delete: `svarupa/extract/typescript.py`

**Interfaces:**
- Consumes: Task 2 model and walker, Task 3 registry.
- Produces: `svarupa.extract.packs.typescript.PACK`, plus module-level hooks reused by Task 6: `import_`, `export_statement`, `variable_declarator`, `call`, `decorator`, `bases`, `constructor_fields`, `declared_field`, `GLOBAL_CALLS`, `GLOBAL_OBJECTS`.

- [ ] **Step 1: Write the TypeScript pack**

`svarupa/extract/packs/typescript.py`:

```python
"""TypeScript language pack.

TypeScript resolves far better than Python (Spike 0c: imports 63-96%
against 37-47%, service calls ~49% against ~20%) because module specifiers
are explicit and constructor injection is typed, so `this.userService.x()`
names an intra-repo class. The hooks below record exactly those facts. Each
is a direct port of the hand-written extractor it replaced;
tests/golden/typescript pins that nothing moved.
"""

from __future__ import annotations

from tree_sitter import Node as TSNode

from svarupa.extract.base import (
    CallShape,
    CallSite,
    DecoratorRef,
    FieldType,
    ImportRef,
    SymbolRef,
)
from svarupa.extract.packs.model import (
    Call,
    Carry,
    Custom,
    Define,
    Field,
    Grammar,
    Import,
    Maturity,
    Pack,
)
from svarupa.extract.packs.walker import Ctx, Frame

__all__ = ["GLOBAL_CALLS", "GLOBAL_OBJECTS", "PACK"]

# Globals that are not intra-repo calls. `require` is included because a
# CommonJS call is a module reference, handled as an import, not a function.
# fmt: off
GLOBAL_CALLS = frozenset(
    {
        "require", "parseInt", "parseFloat", "isNaN", "isFinite", "String",
        "Number", "Boolean", "Array", "Object", "Symbol", "BigInt", "Promise",
        "setTimeout", "setInterval", "clearTimeout", "clearInterval", "fetch",
        "encodeURIComponent", "decodeURIComponent", "structuredClone",
        "queueMicrotask", "atob", "btoa", "alert", "confirm", "prompt",
    }
)
GLOBAL_OBJECTS = frozenset(
    {
        "console", "JSON", "Math", "Object", "Array", "String", "Number",
        "Promise", "Date", "RegExp", "Map", "Set", "WeakMap", "WeakSet",
        "Symbol", "Reflect", "Proxy", "process", "Buffer", "globalThis",
        "window", "document", "localStorage", "sessionStorage", "navigator",
        "crypto", "performance", "URL", "Error", "Intl",
    }
)
# fmt: on


def _ts_string(ctx: Ctx, node: TSNode) -> str | None:
    """A static string from a `string` or substitution-free `template_string`
    node; None for anything dynamic.

    Escape sequences keep their source spelling: joining only the fragments
    turned `'/a\\'b'` into `/ab` and let two distinct routes collide.
    """
    if node.type == "template_string" and any(
        c.type == "template_substitution" for c in node.children
    ):
        return None
    if node.type in ("string", "template_string"):
        return "".join(
            ctx.text(c) for c in node.children if c.type in ("string_fragment", "escape_sequence")
        )
    return None


def _bare_type(annotation: str) -> str:
    """`Promise<UserService | null>` -> `Promise`; `: UserService` -> `UserService`.

    Deliberately crude. A wrong type name yields a failed lookup, an honest
    unresolved; it cannot manufacture an edge, because the name still has to
    match a class that exists.
    """
    t = annotation.lstrip(":").strip()
    for cut in ("|", "&", "<", "["):
        t = t.split(cut)[0]
    return t.strip().strip("()").split(".")[-1].strip()


def _named_exports(ctx: Ctx, node: TSNode, aliases: dict[str, str]) -> tuple[str, ...]:
    out: list[str] = []
    for child in node.children:
        if child.type == "export_clause":
            for spec in child.children:
                if spec.type == "export_specifier":
                    alias = spec.child_by_field_name("alias")
                    name = spec.child_by_field_name("name")
                    target = alias or name
                    if target is not None:
                        out.append(ctx.text(target))
                    if alias is not None and name is not None:
                        aliases[ctx.text(alias)] = ctx.text(name)
    return tuple(out)


def import_(ctx: Ctx, node: TSNode) -> ImportRef | None:
    source = node.child_by_field_name("source")
    if source is None:
        return None
    spec = ctx.text(source).strip("'\"`")
    names: list[str] = []
    alias: dict[str, str] = {}
    # From the tree, never a substring: `import typeA from './x'` matched
    # "import type" and stamped a false type-only flag on a runtime import.
    type_only = any(c.type == "type" for c in node.children) or any(
        ch.type == "type" for c in node.children if c.type == "import_clause" for ch in c.children
    )
    for child in node.children:
        if child.type != "import_clause":
            continue
        for part in child.children:
            if part.type == "identifier":  # default import
                names.append(ctx.text(part))
            elif part.type == "named_imports":
                for spec_node in part.children:
                    if spec_node.type != "import_specifier":
                        continue
                    real = spec_node.child_by_field_name("name")
                    as_name = spec_node.child_by_field_name("alias")
                    if real is None:
                        continue
                    if as_name is not None:
                        alias[ctx.text(as_name)] = ctx.text(real)
                        names.append(ctx.text(as_name))
                    else:
                        names.append(ctx.text(real))
            elif part.type == "namespace_import":
                for ident in part.children:
                    if ident.type == "identifier":
                        names.append(ctx.text(ident))
    return ImportRef(
        specifier=spec,
        names=tuple(names),
        alias_of=alias,
        evidence=ctx.node_evidence(node),
        is_relative=spec.startswith("."),
        level=0,
        type_only=type_only,
    )


def export_statement(ctx: Ctx, node: TSNode, frame: Frame, depth: int) -> None:
    # `export { x } from './y'` and `export * from './y'` are the barrel
    # pattern, the TypeScript analogue of a package __init__.
    src_node = node.child_by_field_name("source")
    if src_node is not None:
        spec = ctx.text(src_node).strip("'\"`")
        aliases: dict[str, str] = {}
        names = _named_exports(ctx, node, aliases)
        ctx.imports.append(
            ImportRef(
                specifier=spec,
                names=names,
                alias_of=aliases,
                evidence=ctx.node_evidence(node),
                is_relative=spec.startswith("."),
                level=0,
                is_from=True,
                is_reexport=True,
                is_star=not names,
            )
        )
        ctx.reexports.extend(names)
        return
    # A bare `export` wrapper: everything inside is exported. Decorators are
    # siblings preceding the declaration they decorate, so they pair here.
    pending: list[DecoratorRef] = []
    for child in node.children:
        if child.type == "decorator":
            if (d := decorator(ctx, child)) is not None:
                pending.append(d)
            continue
        if child.type in ("export", "default", ";"):
            # Keyword tokens sit between decorators and the declaration; they
            # must not consume the pending list.
            continue
        ctx.visit(
            child,
            Frame(frame.stack, frame.cls, frame.fn, exported=True, decorators=tuple(pending)),
            depth + 1,
        )
        pending = []


def variable_declarator(ctx: Ctx, node: TSNode, frame: Frame, depth: int) -> None:
    name_node = node.child_by_field_name("name")
    value = node.child_by_field_name("value")
    if name_node is not None and value is not None and value.type == "call_expression":
        callee = value.child_by_field_name("function")
        # `const x = require('spec')` is the CommonJS import, the dominant
        # dialect of real Express code; `const { A, B } = require('spec')`
        # maps the destructured names like named imports.
        if callee is not None and ctx.text(callee) == "require":
            spec = None
            req_args = value.child_by_field_name("arguments")
            if req_args is not None:
                for c in req_args.children:
                    if c.type in ("(", ")", ","):
                        continue
                    if c.type in ("string", "template_string"):
                        spec = _ts_string(ctx, c)
                    break
            req_names: list[str] = []
            if name_node.type == "identifier":
                req_names = [ctx.text(name_node)]
            elif name_node.type == "object_pattern":
                req_names = [
                    ctx.text(c)
                    for c in name_node.children
                    if c.type == "shorthand_property_identifier_pattern"
                ]
            if spec and req_names:
                row = node.start_point[0]
                ctx.imports.append(
                    ImportRef(
                        specifier=spec,
                        names=tuple(req_names),
                        alias_of={},
                        evidence=ctx.evidence(row, row),
                        is_relative=spec.startswith("."),
                        is_from=True,
                    )
                )
        if callee is not None and name_node.type == "identifier":
            ctx.ctor_assigns.append((ctx.text(name_node), ctx.text(callee)))
    if (
        name_node is not None
        and value is not None
        and value.type in ("arrow_function", "function_expression")
    ):
        name = ctx.text(name_node)
        qualified = ctx.qual((*frame.stack, name))
        ctx.symbols.append(
            SymbolRef(
                name=name,
                qualified_name=qualified,
                kind="function",
                evidence=ctx.node_evidence(node),
                exported=frame.exported,
            )
        )
        inner = Frame((*frame.stack, name), frame.cls, qualified)
        for child in value.children:
            ctx.visit(child, inner, depth + 1)
        return
    plain = frame.plain()
    for child in node.children:
        ctx.visit(child, plain, depth + 1)


def decorator(ctx: Ctx, node: TSNode) -> DecoratorRef | None:
    """`@Get(':id')` or `@Controller('users')`, with its own line."""
    expr = next((c for c in node.children if c.type not in ("@", "comment")), None)
    if expr is None:
        return None
    ev = ctx.node_evidence(node)
    if expr.type != "call_expression":
        name = ctx.text(expr)
        return DecoratorRef(name=name, arg=None, evidence=ev) if name else None
    callee = expr.child_by_field_name("function")
    if callee is None:
        return None
    arg: str | None = None
    dynamic = False
    args = expr.child_by_field_name("arguments")
    if args is not None:
        for child in args.children:
            if child.type in ("(", ")", ",", "comment"):
                continue
            # First argument only. Anything but a static string makes the
            # decorator present-but-dynamic, which must stay distinguishable
            # from "no arguments".
            if child.type in ("string", "template_string"):
                arg = _ts_string(ctx, child)
                dynamic = arg is None
            else:
                dynamic = True
            break
    return DecoratorRef(name=ctx.text(callee), arg=arg, evidence=ev, arg_dynamic=dynamic)


def bases(ctx: Ctx, node: TSNode) -> tuple[str, ...]:
    out: list[str] = []
    for child in node.children:
        if child.type == "class_heritage":
            for clause in child.children:
                if clause.type in ("extends_clause", "implements_clause"):
                    for ref in clause.children:
                        if ref.type in (
                            "identifier",
                            "type_identifier",
                            "generic_type",
                            "member_expression",
                        ):
                            out.append(_bare_type(ctx.text(ref)))
    return tuple(b for b in out if b)


def _field_of(ctx: Ctx, node: TSNode) -> tuple[str | None, str | None]:
    name: str | None = None
    ty: str | None = None
    for child in node.children:
        if child.type in ("identifier", "property_identifier") and name is None:
            name = ctx.text(child)
        elif child.type == "type_annotation":
            ty = _bare_type(ctx.text(child))
    return name, ty


def constructor_fields(ctx: Ctx, node: TSNode, frame: Frame, name: str) -> None:
    """`constructor(private readonly svc: UserService)` declares a field.

    Only parameter properties count: a plain `constructor(config: T)`
    declares no field, and recording one resolved `this.config.run()` to the
    parameter's type instead of the declared field's.
    """
    if name != "constructor" or not frame.cls:
        return
    params = node.child_by_field_name("parameters")
    if params is None:
        return
    for param in params.children:
        if param.type not in ("required_parameter", "optional_parameter"):
            continue
        text = ctx.text(param)
        if not any(
            text.lstrip().startswith(mod) for mod in ("private", "public", "protected", "readonly")
        ):
            continue
        field_name, ty = _field_of(ctx, param)
        if field_name and ty:
            ctx.fields.append(FieldType(frame.cls, field_name, ty))


def declared_field(ctx: Ctx, node: TSNode, cls: str) -> list[FieldType]:
    name, ty = _field_of(ctx, node)
    return [FieldType(cls, name, ty)] if name and ty else []


def _first_str_arg(ctx: Ctx, node: TSNode) -> str | None:
    args = node.child_by_field_name("arguments")
    if args is None:
        return None
    for child in args.children:
        if child.type in ("(", ")", ","):
            continue
        # Only when the string is literally the first argument.
        if child.type in ("string", "template_string"):
            return _ts_string(ctx, child)
        return None
    return None


def _first_arg_ident(ctx: Ctx, node: TSNode) -> str | None:
    args = node.child_by_field_name("arguments")
    if args is None:
        return None
    for child in args.children:
        if child.type in ("(", ")", ",", "comment"):
            continue
        return ctx.text(child) if child.type == "identifier" else None
    return None


def _ident_args(ctx: Ctx, node: TSNode) -> tuple[str, ...]:
    args = node.child_by_field_name("arguments")
    if args is None:
        return ()
    return tuple(ctx.text(c) for c in args.children if c.type == "identifier")


def _chain_base(ctx: Ctx, obj: TSNode) -> tuple[str, str, str | None] | None:
    """Unwind `base.m1(...).m2(...)` to (base, m1, m1's static first string),
    or None when the chain is not rooted at an identifier. Capped at 16 hops:
    a longer chain stays opaque text rather than being walked forever."""
    cur = obj
    innermost: TSNode | None = None
    for _ in range(16):
        if cur.type != "call_expression":
            break
        innermost = cur
        fn = cur.child_by_field_name("function")
        if fn is None or fn.type != "member_expression":
            return None
        nxt = fn.child_by_field_name("object")
        if nxt is None:
            return None
        cur = nxt
    if innermost is None or cur.type != "identifier":
        return None
    inner_fn = innermost.child_by_field_name("function")
    prop = inner_fn.child_by_field_name("property") if inner_fn is not None else None
    if prop is None:
        return None
    return (ctx.text(cur), ctx.text(prop), _first_str_arg(ctx, innermost))


def call(ctx: Ctx, node: TSNode, frame: Frame) -> CallSite | None:
    func = node.child_by_field_name("function")
    if func is None:
        return None
    fn, cls = frame.fn, frame.cls
    row = node.start_point[0]
    ev = ctx.evidence(row, row)
    first_arg = _first_str_arg(ctx, node)
    idents = _ident_args(ctx, node)
    first_ident = _first_arg_ident(ctx, node)

    if func.type == "identifier":
        name = ctx.text(func)
        if name in GLOBAL_CALLS:
            return None
        return CallSite(name, CallShape.BARE, None, ev, fn, cls, first_arg)

    if func.type == "member_expression":
        obj = func.child_by_field_name("object")
        prop = func.child_by_field_name("property")
        if prop is None or obj is None:
            return None
        name = ctx.text(prop)
        otext = ctx.text(obj)
        # A member call cites the line of the method name: in a multi-line
        # chain every verb used to cite the chain's first line.
        prow = prop.start_point[0]
        ev = ctx.evidence(prow, prow)
        if obj.type == "this":
            return CallSite(name, CallShape.SELF, "this", ev, fn, cls, first_arg)
        if obj.type == "super":
            return CallSite(name, CallShape.SUPER, "super", ev, fn, cls, first_arg)
        if obj.type == "member_expression" and otext.startswith("this."):
            parts = otext.split(".")
            return CallSite(
                name,
                CallShape.SELF_FIELD,
                parts[1] if len(parts) > 1 else None,
                ev,
                fn,
                cls,
                first_arg,
            )
        if obj.type == "identifier":
            if otext in GLOBAL_OBJECTS:
                return None
            return CallSite(
                name, CallShape.QUALIFIED, otext, ev, fn, cls, first_arg, idents, first_ident
            )
        if obj.type == "call_expression" and (chain := _chain_base(ctx, obj)):
            base, root_method, root_arg = chain
            return CallSite(
                name,
                CallShape.MEMBER,
                base,
                ev,
                fn,
                cls,
                first_arg,
                idents,
                first_ident,
                recv_call=(root_method, root_arg),
            )
        return CallSite(
            name, CallShape.MEMBER, otext, ev, fn, cls, first_arg, idents, first_ident
        )

    return None


_CLASS = Define(
    kind="class",
    inherit_exported=True,
    sets_class=True,
    own_decorators="decorator",
    member_decorators="decorator",
    bases=bases,
)

RULES = {
    "import_statement": Import(hook=import_, recurse_on_none=True),
    "export_statement": Custom(hook=export_statement),
    "class_declaration": _CLASS,
    "abstract_class_declaration": _CLASS,
    "interface_declaration": Define(
        kind="interface",
        inherit_exported=True,
        body_field=None,
        records_class=False,
        keeps_decorators=False,
    ),
    "method_definition": Define(kind="method", after=constructor_fields),
    "public_field_definition": Field(hook=declared_field),
    "property_signature": Field(hook=declared_field),
    "function_declaration": Define(
        kind="function",
        inherit_exported=True,
        records_class=False,
        keeps_decorators=False,
        recurse_without_name=True,
    ),
    "lexical_declaration": Carry(),
    "variable_declaration": Carry(),
    "variable_declarator": Custom(hook=variable_declarator),
    "call_expression": Call(hook=call),
}

PACK = Pack(
    lang="typescript",
    grammar=Grammar(
        distribution="tree-sitter-typescript",
        version="0.23.2",
        module="tree_sitter_typescript",
        default="language_typescript",
        by_suffix=((".tsx", "language_tsx"),),
    ),
    maturity=Maturity.STABLE,
    qualified_prefix=lambda path: path.rsplit(".", 1)[0].replace("/", "."),
    decorator=decorator,
    rules=RULES,
)
```

Note on `_bare_type`'s docstring: the old docstring claimed `Promise<UserService | null>` gives `UserService`; the code returns `Promise`. The port keeps the code and corrects the docstring. Behavior is unchanged, and the golden `nest_controller` case (`Repository<User>`) pins it.

- [ ] **Step 2: Register the pack**

In `svarupa/extract/packs/__init__.py`:
- the pack import becomes `from svarupa.extract.packs import python, typescript`
- `PACKS: tuple[Pack, ...] = (python.PACK, typescript.PACK)`
- `BY_DETECTED` becomes:

```python
# detect.LANG_BY_EXT label -> pack. JavaScript rides the TypeScript pack
# until it gets its own label and grammar choice.
BY_DETECTED: dict[str, Pack] = {
    "python": python.PACK,
    "typescript": typescript.PACK,
    "javascript": typescript.PACK,
}
```

- [ ] **Step 3: Write the temporary parity test**

`tests/test_pack_parity.py`:

```python
"""Temporary: the TypeScript pack must equal the hand-written extractor on
every TypeScript and JavaScript source this repository has, plus the golden
cases. Deleted with the old extractor at the end of this task."""

from __future__ import annotations

from pathlib import Path

import pytest

from svarupa.extract.packs import extractor
from svarupa.extract.typescript import TypeScriptExtractor

ROOT = Path(__file__).resolve().parents[1]
SUFFIXES = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")
FILES = sorted(
    p
    for p in ROOT.rglob("*")
    if p.suffix in SUFFIXES
    and p.is_file()
    and not {".venv", ".git"} & set(p.relative_to(ROOT).parts)
)


def test_the_corpus_is_not_empty() -> None:
    assert len(FILES) > 0


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.relative_to(ROOT).as_posix())
def test_typescript_pack_equals_the_old_extractor(path: Path) -> None:
    rel = path.relative_to(ROOT).as_posix()
    data = path.read_bytes()
    assert extractor("typescript").parse(rel, data) == TypeScriptExtractor().parse(rel, data)
```

`tests/js/node_modules` is included on purpose: it is real third-party JavaScript.

- [ ] **Step 4: Run parity**

Run: `uv run pytest tests/test_pack_parity.py -q`
Expected: all pass.

Optional, local only: the same comparison over real TypeScript repositories on this machine:

```bash
uv run python - <<'EOF'
import sys
from pathlib import Path
from svarupa.extract.packs import extractor
from svarupa.extract.typescript import TypeScriptExtractor
SUF = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")
bad = 0
for root in sys.argv[1:] or [str(Path.home() / "Documents")]:
    for p in Path(root).rglob("*"):
        if p.suffix not in SUF or not p.is_file():
            continue
        if any(x in p.parts for x in ("node_modules", ".git", "dist", "build", ".next")):
            continue
        rel, data = p.as_posix(), p.read_bytes()
        if extractor("typescript").parse(rel, data) != TypeScriptExtractor().parse(rel, data):
            bad += 1
            print("DIFF", rel)
print("diffs:", bad)
EOF
```

Expected: `diffs: 0`.

- [ ] **Step 5: Serve TypeScript and JavaScript from the pack**

In `svarupa/extract/__init__.py`:
- delete `from svarupa.extract.typescript import TypeScriptExtractor`
- replace the `_EXTRACTORS` assignment with:

```python
_EXTRACTORS: dict[str, Extractor] = dict(_PACK_EXTRACTORS)
```

The `.js/.jsx parse fine with the TypeScript grammar` comment goes with it; `BY_DETECTED` now says the same thing.

- [ ] **Step 6: Point the tests at the pack**

- `tests/test_extract_typescript.py` line 21: `from svarupa.extract.typescript import TypeScriptExtractor` becomes `from svarupa.extract.packs import extractor`
- line 38: `return TypeScriptExtractor().parse(path, src.encode())` becomes `return extractor("typescript").parse(path, src.encode())`
- lines 370 to 373 become:

```python
def test_grammar_version_matches_the_installed_pin() -> None:
    from importlib.metadata import version

    assert extractor("typescript").grammar_version == version("tree-sitter-typescript")
```

- `tests/test_semantics.py` lines 956 to 958: the local import becomes `from svarupa.extract.packs import extractor` and `TypeScriptExtractor().parse(` becomes `extractor("typescript").parse(`.

Run: `grep -rn "extract.typescript\|TypeScriptExtractor" svarupa tests scripts`
Expected: only `tests/test_pack_parity.py` and `svarupa/extract/typescript.py`.

- [ ] **Step 7: Run everything with both implementations present**

Run: `uv run pytest -q --deselect tests/test_diagnostics.py::test_every_package_module_is_reachable_by_import`
Expected: all pass. (The orphan check fails until Step 8 deletes `svarupa/extract/typescript.py`.)

- [ ] **Step 8: Delete the old extractor and parity test, full check**

```bash
git rm svarupa/extract/typescript.py
rm tests/test_pack_parity.py
uv run ruff check svarupa tests && uv run ruff format --check svarupa tests && uv run pyright svarupa && uv run pytest -q
uv run svarupa . --lock && git diff --exit-code .svarupa/architecture.lock
for s in scripts/mutate_*.py; do uv run python "$s" || echo "FAILED $s"; done
```

Expected: clean, all pass, lockfile unchanged, no `FAILED`. Retarget any mutation whose pattern pointed into `svarupa/extract/typescript.py` to `svarupa/extract/packs/typescript.py`.

- [ ] **Step 9: Commit**

```bash
git add -A svarupa tests scripts
git commit -m "Serve TypeScript extraction from a language pack"
```

---

### Task 5: Pack self-checks and SVA-X-012 for unanalyzed languages

**Files:**
- Create: `tests/test_packs.py`
- Modify: `svarupa/extract/packs/__init__.py` (add `ANALYZED_ELSEWHERE`)
- Modify: `svarupa/extract/__init__.py:62-73` (report languages without an extractor)
- Modify: `svarupa/diagnostics.py` (register SVA-X-012)
- Test: `tests/test_extract.py` (two new tests)

**Interfaces:**
- Consumes: registry `PACKS`, `BY_DETECTED`, `load_extractors`; `svarupa.extract._UNAVAILABLE: dict[str, str]`.
- Produces: `ANALYZED_ELSEWHERE: frozenset[str]` (languages read by other extractors, never reported), diagnostic `SVA-X-012` with `subject=<lang>`.

- [ ] **Step 1: Write the self-check tests**

`tests/test_packs.py`:

```python
"""Pack self-checks: a broken pack fails here, never on a user's machine."""

from __future__ import annotations

from importlib.metadata import version
from pathlib import Path

import pytest
from tree_sitter import Language

from svarupa.detect import LANG_BY_EXT
from svarupa.extract.packs import (
    ANALYZED_ELSEWHERE,
    BY_DETECTED,
    PACKS,
    load_extractors,
)
from svarupa.extract.packs.model import Decorated, Define, Pack
from svarupa.extract.packs.walker import language_for

ROOT = Path(__file__).resolve().parents[1]


def _languages(pack: Pack) -> list[Language]:
    paths = ["x"] + [f"x{suffix}" for suffix, _ in pack.grammar.by_suffix]
    return [language_for(pack.grammar, p) for p in paths]


@pytest.mark.parametrize("pack", PACKS, ids=lambda p: p.lang)
def test_grammar_pin_matches_the_installed_wheel(pack: Pack) -> None:
    assert version(pack.grammar.distribution) == pack.grammar.version


@pytest.mark.parametrize("pack", PACKS, ids=lambda p: p.lang)
def test_grammar_is_pinned_exactly_in_pyproject(pack: Pack) -> None:
    pin = f'"{pack.grammar.distribution}=={pack.grammar.version}"'
    assert pin in (ROOT / "pyproject.toml").read_text(encoding="utf8")


@pytest.mark.parametrize("pack", PACKS, ids=lambda p: p.lang)
def test_every_rule_names_a_real_node_type(pack: Pack) -> None:
    for lang in _languages(pack):
        unknown = [t for t in pack.rules if lang.id_for_node_kind(t, True) is None]
        assert not unknown, f"{pack.lang}: not node types in this grammar: {unknown}"


@pytest.mark.parametrize("pack", PACKS, ids=lambda p: p.lang)
def test_every_field_a_rule_reads_exists(pack: Pack) -> None:
    fields: set[str] = set()
    for rule in pack.rules.values():
        if isinstance(rule, Define):
            fields.add(rule.name_field)
            if rule.body_field is not None:
                fields.add(rule.body_field)
        elif isinstance(rule, Decorated):
            fields.add(rule.definition_field)
    for lang in _languages(pack):
        missing = sorted(f for f in fields if lang.field_id_for_name(f) is None)
        assert not missing, f"{pack.lang}: unknown fields {missing}"


def test_every_detected_language_has_a_pack_or_a_reason() -> None:
    detected = set(LANG_BY_EXT.values())
    unanalyzed = detected - set(BY_DETECTED) - ANALYZED_ELSEWHERE
    # These are reported as SVA-X-012. The list shrinks as packs land.
    assert unanalyzed == {"go", "rust", "java"}


def test_all_shipped_packs_load() -> None:
    ready, missing = load_extractors()
    assert missing == {}
    assert set(ready) == set(BY_DETECTED)
```

- [ ] **Step 2: Write the failing SVA-X-012 tests**

Append to `tests/test_extract.py`:

```python
def test_languages_without_a_pack_are_reported_once_each(tmp_path: Path) -> None:
    write(tmp_path, "svc/main.go", "package main\n")
    write(tmp_path, "svc/util.go", "package main\n")
    write(tmp_path, "lib/x.rs", "fn main() {}\n")
    write(tmp_path, "app.py", "def keep():\n    pass\n")
    result = run(tmp_path)
    x012 = [d for d in result.diagnostics if d.code == "SVA-X-012"]
    assert [d.subject for d in x012] == ["go", "rust"]
    assert "2 go files" in x012[0].message
    assert any(n.id.endswith(".keep") for n in result.nodes)


def test_a_repository_of_packed_languages_gets_no_x012(tmp_path: Path) -> None:
    write(tmp_path, "app.py", "def keep():\n    pass\n")
    write(tmp_path, "web/a.ts", "export const a = 1;\n")
    write(tmp_path, "db/schema.sql", "create table t (id int);\n")
    result = run(tmp_path)
    assert not [d for d in result.diagnostics if d.code == "SVA-X-012"]
```

Run: `uv run pytest tests/test_packs.py tests/test_extract.py -q -k "pack or x012 or once_each"`
Expected: `ImportError: cannot import name 'ANALYZED_ELSEWHERE'` and the two X-012 tests fail.

- [ ] **Step 3: Add `ANALYZED_ELSEWHERE` to the registry**

In `svarupa/extract/packs/__init__.py`, add to `__all__` and define after `BY_DETECTED`:

```python
# Detected languages another extractor reads, so no pack is missing: SQL
# feeds the ERD from the scan's file languages, not from a syntax walk.
ANALYZED_ELSEWHERE: frozenset[str] = frozenset({"sql"})
```

- [ ] **Step 4: Register and emit SVA-X-012**

In `svarupa/diagnostics.py`, after the `"SVA-X-011"` line:

```python
    "SVA-X-012": "files in a language were detected but not analyzed, with the reason",
```

In `svarupa/extract/__init__.py`, add `from svarupa.extract.packs import ANALYZED_ELSEWHERE, load_extractors` (merge with the existing import), and in `extract()` replace:

```python
    for rec in scan.files:
        ex = _EXTRACTORS.get(rec.lang or "")
        if ex is None:
            continue
```

with:

```python
    unanalyzed: dict[str, int] = {}
    for rec in scan.files:
        ex = _EXTRACTORS.get(rec.lang or "")
        if ex is None:
            if rec.lang and rec.lang not in ANALYZED_ELSEWHERE:
                unanalyzed[rec.lang] = unanalyzed.get(rec.lang, 0) + 1
            continue
```

and, directly after that loop ends (before the `roots = ...` line), add:

```python
    # One line per language, never one per file: a Go service with 900 files
    # is one fact ("Go is not analyzed"), and 900 warnings would bury it.
    for lang, count in sorted(unanalyzed.items()):
        reason = _UNAVAILABLE.get(lang, f"no language pack exists for {lang} yet")
        crashes.append(
            Diagnostic(
                code="SVA-X-012",
                severity=Severity.WARNING,
                message=(
                    f"{count} {lang} files were detected but not analyzed: {reason}; "
                    "they appear in no diagram"
                ),
                subject=lang,
            )
        )
```

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_packs.py tests/test_extract.py tests/test_diagnostics.py -q`
Expected: all pass. If the description check fails, make sure the registry text and the literal message share the words "detected" and "analyzed".

- [ ] **Step 6: Full check and commit**

Run: `uv run ruff check svarupa tests && uv run ruff format --check svarupa tests && uv run pyright svarupa && uv run pytest -q && uv run svarupa . --lock && git diff --exit-code .svarupa/architecture.lock`
Expected: clean, all pass, lockfile unchanged (this repository has no Go, Rust or Java files). If an existing test builds a fixture with `.go`, `.rs` or `.java` files and asserts the exact diagnostic list, add the expected SVA-X-012 to that assertion and name the test in the commit body.

```bash
git add -A svarupa tests
git commit -m "Report detected languages that have no language pack"
```

---

### Task 6: JavaScript gets its own label and parses JSX

**Files:**
- Create: `svarupa/extract/packs/javascript.py`
- Modify: `svarupa/extract/packs/__init__.py`
- Modify: `svarupa/extract/resolve.py:444`
- Modify: `tests/test_extract_typescript.py:362-367`
- Regenerate: `tests/golden/typescript/express.json`, `tests/golden/typescript/jsx_in_js.json`

**Interfaces:**
- Consumes: `typescript.PACK` (Task 4).
- Produces: `javascript.PACK` with `lang="javascript"`; `BY_DETECTED["javascript"]` points to it.

- [ ] **Step 1: Write the failing tests**

In `tests/test_extract_typescript.py`, replace `test_javascript_files_use_the_typescript_grammar` with:

```python
def test_javascript_files_resolve_like_typescript(tmp_path: Path) -> None:
    write(tmp_path, "src/a.js", "export function go() {}\n")
    write(tmp_path, "src/b.js", "import { go } from './a.js';\n")
    write(tmp_path, "src/c.ts", "import { go } from './a';\n")
    res = run(tmp_path)
    imports = {(e.src, e.dst) for e in res.edges if e.kind is EdgeKind.IMPORTS}
    assert ("src/b.js", "src/a.js") in imports
    assert ("src/c.ts", "src/a.js") in imports


def test_javascript_facts_carry_their_own_label() -> None:
    f = extractor("javascript").parse("src/a.js", b"export function go() {}\n")
    assert f.lang == "javascript"


@pytest.mark.parametrize("path", ["src/Button.jsx", "src/Button.js"])
def test_jsx_in_javascript_parses_cleanly(path: str) -> None:
    src = b"export function Button({ go }) {\n  return <button onClick={go}>x</button>;\n}\n"
    f = extractor("javascript").parse(path, src)
    assert [d.code for d in f.diagnostics] == []
    assert [s.name for s in f.symbols] == ["Button"]


def test_typescript_type_assertions_still_parse_in_ts_files() -> None:
    f = extractor("typescript").parse("src/a.ts", b"const n = <number>value;\n")
    assert [d.code for d in f.diagnostics] == []
```

Run: `uv run pytest tests/test_extract_typescript.py -q`
Expected: `test_javascript_facts_carry_their_own_label` fails (`typescript` != `javascript`) and `test_jsx_in_javascript_parses_cleanly` fails (`SVA-X-001`).

- [ ] **Step 2: Write the JavaScript pack**

`svarupa/extract/packs/javascript.py`:

```python
"""JavaScript language pack: the TypeScript hooks, its own label, TSX grammar.

JavaScript files used to be parsed with the plain TypeScript grammar and
labelled `typescript`. The grammar choice broke JSX in `.js` and `.jsx`
files (the parse failed and facts went missing), and the label meant every
`javascript` branch downstream never ran.

TSX, not tree-sitter-javascript: the TypeScript hooks read TypeScript node
shapes (`class_heritage` with `extends_clause`) that the JavaScript grammar
spells differently, and the one construct TSX misreads, the `<T>x` type
assertion, does not exist in JavaScript.
"""

from __future__ import annotations

from dataclasses import replace

from svarupa.extract.packs import typescript
from svarupa.extract.packs.model import Pack

__all__ = ["PACK"]

PACK: Pack = replace(
    typescript.PACK,
    lang="javascript",
    grammar=replace(typescript.PACK.grammar, default="language_tsx", by_suffix=()),
)
```

- [ ] **Step 3: Register it and fix resolution**

In `svarupa/extract/packs/__init__.py`: import `javascript` alongside `python, typescript`; `PACKS = (python.PACK, typescript.PACK, javascript.PACK)`; `BY_DETECTED["javascript"] = javascript.PACK` (replace the comment about riding the TypeScript pack with `# JavaScript: TypeScript hooks, own label, TSX grammar (see javascript.py).`).

In `svarupa/extract/resolve.py` line 444, change:

```python
        if lang == "typescript":
```

to:

```python
        if lang in ("typescript", "javascript"):
```

Run: `grep -rn '== "typescript"' svarupa`
Expected: no output.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_extract_typescript.py tests/test_packs.py -q`
Expected: all pass. (`test_grammar_is_pinned_exactly_in_pyproject` passes for JavaScript because it shares the `tree-sitter-typescript==0.23.2` pin.)

- [ ] **Step 5: Regenerate the two affected goldens and check the diff is only what we meant**

```bash
SVARUPA_UPDATE_GOLDEN=1 uv run pytest tests/test_golden_facts.py -q
git diff --stat tests/golden
```

Expected: only `tests/golden/typescript/express.json` and `tests/golden/typescript/jsx_in_js.json` changed.

Run: `git diff tests/golden/typescript/express.json`
Expected: the only changed line is `"lang": "typescript"` to `"lang": "javascript"`.

Run: `grep -c "SVA-X-001" tests/golden/typescript/jsx_in_js.json; grep -o '"name": "[A-Za-z]*"' tests/golden/typescript/jsx_in_js.json | sort -u`
Expected: `0`, and the names include `"Button"` and `"Icon"`.

- [ ] **Step 6: Full suite**

Run: `uv run ruff check svarupa tests && uv run ruff format --check svarupa tests && uv run pyright svarupa && uv run pytest -q`
Expected: all pass. A test that asserted `scorecard.get("typescript", ...)` for `.js` files now needs `"javascript"`; update only such assertions, and list each one in the commit body.

Run: `uv run svarupa . --lock && git diff --exit-code .svarupa/architecture.lock`
Expected: unchanged (this repository's architecture has no JavaScript).

- [ ] **Step 7: Commit**

```bash
git add -A svarupa tests
git commit -F - <<'EOF'
Label JavaScript facts javascript and parse JSX in .js files

- JavaScript files get their own pack: TypeScript hooks, TSX grammar
- JSX in .js and .jsx files no longer fails to parse
- Import resolution treats javascript like typescript
- Scorecard rows, graph.json and lockfile headers now say javascript
  for JavaScript files instead of typescript
EOF
```

---

### Task 7: Mutation check for the walker, then the pull request

**Files:**
- Create: `scripts/mutate_packs.py`

**Interfaces:**
- Consumes: the walker and packs as built above.
- Produces: a script that exits 0 only if every mutation is caught.

- [ ] **Step 1: Write the mutation script**

`scripts/mutate_packs.py`:

```python
"""Mutation check for the language pack walker.

Each entry breaks one promise of the walker; a test must go red:

* children are walked in document order;
* a definition walks only its body, never default arguments or bases;
* the depth cap stops the walk instead of recursing on;
* a decorated definition receives its decorators;
* member decorators pair with the next member only;
* JavaScript facts carry the javascript label;
* languages without a pack are reported.
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
PY = ROOT / ".venv" / "bin" / "python"
SUITE = [
    "tests/test_pack_walker.py",
    "tests/test_golden_facts.py",
    "tests/test_extract.py",
    "tests/test_extract_typescript.py",
    "tests/test_packs.py",
]

MUTATIONS: list[tuple[str, str, str, str]] = [
    (
        "generic children are walked in reverse",
        "svarupa/extract/packs/walker.py",
        "        plain = frame.plain()\n        for child in node.children:\n            self.visit(child, plain, depth + 1)\n\n    def _define",
        "        plain = frame.plain()\n        for child in reversed(node.children):\n            self.visit(child, plain, depth + 1)\n\n    def _define",
    ),
    (
        "a definition walks all children, not only its body",
        "svarupa/extract/packs/walker.py",
        "        body = node.child_by_field_name(rule.body_field)\n        if body is None:\n            return\n",
        "        body = node\n",
    ),
    (
        "the depth cap no longer stops the walk",
        "svarupa/extract/packs/walker.py",
        "            self.too_deep = True\n            return\n",
        "            self.too_deep = True\n",
    ),
    (
        "decorated definitions lose their decorators",
        "svarupa/extract/packs/walker.py",
        "self.visit(definition, replace(frame, decorators=decs), depth + 1)",
        "self.visit(definition, frame, depth + 1)",
    ),
    (
        "member decorators are never cleared",
        "svarupa/extract/packs/walker.py",
        "            self.visit(child, replace(inner, decorators=tuple(pending)), depth + 1)\n            pending = []\n",
        "            self.visit(child, replace(inner, decorators=tuple(pending)), depth + 1)\n",
    ),
    (
        "javascript facts are labelled typescript again",
        "svarupa/extract/packs/javascript.py",
        '    lang="javascript",\n',
        '    lang="typescript",\n',
    ),
    (
        "languages without a pack are skipped silently",
        "svarupa/extract/__init__.py",
        "            if rec.lang and rec.lang not in ANALYZED_ELSEWHERE:\n",
        "            if False:\n",
    ),
]


def main() -> int:
    failures: list[str] = []
    backup = pathlib.Path(tempfile.mkdtemp()) / "src"
    shutil.copytree(ROOT / "svarupa", backup)
    try:
        for name, rel, old, new in MUTATIONS:
            path = ROOT / rel
            text = path.read_text(encoding="utf8")
            if old not in text:
                print(f"SKIP (pattern not found)  {name}")
                failures.append(f"{name}: pattern not found")
                continue
            path.write_text(text.replace(old, new, 1), encoding="utf8")
            proc = subprocess.run(
                [str(PY), "-m", "pytest", *SUITE, "-q", "-x"],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            shutil.rmtree(ROOT / "svarupa")
            shutil.copytree(backup, ROOT / "svarupa")
            if proc.returncode == 0:
                print(f"SURVIVED  {name}")
                failures.append(f"{name}: no test failed")
            else:
                caught = [
                    ln.split("::")[-1]
                    for ln in proc.stdout.splitlines()
                    if ln.startswith("FAILED")
                ]
                print(f"caught    {name}  ->  {caught[0] if caught else 'error'}")
    finally:
        # Restore unconditionally. An interrupt during pytest leaves the tree
        # present but mutated, and a presence check would keep the mutation.
        shutil.rmtree(ROOT / "svarupa", ignore_errors=True)
        shutil.copytree(backup, ROOT / "svarupa")
    if failures:
        print("\nnot caught:")
        for f in failures:
            print(f"  {f}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Run it**

Run: `uv run python scripts/mutate_packs.py`
Expected: 7 lines starting with `caught`, exit 0. A `SURVIVED` line means a promise has no test: add the missing test to the task that owns the code, then rerun. A `SKIP` line means the pattern drifted from the code above: fix the pattern, not the code.

Run: `git status --short svarupa`
Expected: empty (the script restored the tree).

- [ ] **Step 3: Run every mutation script once more**

```bash
for s in scripts/mutate_*.py; do uv run python "$s" >/dev/null || echo "FAILED $s"; done
```

Expected: no output.

- [ ] **Step 4: Commit and open the PR**

```bash
git add scripts/mutate_packs.py
git commit -m "Add mutation check for the language pack walker"
git push -u origin feat/language-packs-1a-1
gh pr create --base main --head feat/language-packs-1a-1 \
  --title "Language packs 1a-1: generic walker, Python and TypeScript ported" \
  --body-file - <<'EOF'
Step 1a-1 of docs/superpowers/specs/2026-10-03-language-packs-design.md.

- One walker (`svarupa/extract/packs/walker.py`) turns syntax trees into facts for every language; each language is a pack of node rules plus a few hooks.
- Python and TypeScript are ported with zero output change: golden facts recorded before the port, parity checked file by file, lockfile unchanged.
- JavaScript facts are labelled `javascript` and parsed with the TSX grammar, so JSX in `.js`/`.jsx` files parses (intended output change, own commit).
- Languages with no pack (Go, Rust, Java today) are reported once each as SVA-X-012 instead of being skipped silently.
- `scripts/mutate_packs.py`: every walker promise is caught by a test.

Next: plan 1a-2 (resolver kinds, Go and Java packs).
EOF
```

Expected: PR URL printed. Do not merge; the maintainer merges after review.
