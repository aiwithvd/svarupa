# Language Packs 1a-2: Module Resolvers, Go and Java Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Go and Java as analyzed languages: packs for both, a module resolver protocol that lets a pack resolve imports that name whole packages, and dependency detection from `go.mod`, `pom.xml` and Gradle files.

**Architecture:** A pack may now carry a module resolver factory. `resolve.py` asks a language's resolver first and falls back to its existing Python and TypeScript code, which does not change. A resolver returns every file an import names, so a package import becomes one edge per source file, counted once in the scorecard. Go resolves through `go.mod` module paths, Java through each file's `package` declaration.

**Tech Stack:** Python 3.10 to 3.13, py-tree-sitter 0.26, tree-sitter-go 0.25.0, tree-sitter-java 0.23.5, pytest, ruff, pyright (strict).

**Spec:** `docs/superpowers/specs/2026-10-03-language-packs-design.md` (Revision 1 and Revision 2 at the end) and the parent `docs/superpowers/specs/2026-10-03-system-health-umbrella-design.md`.

## Global Constraints

- Python and TypeScript output stays byte-identical: the pass-1 golden facts in `tests/golden/{python,typescript}` and the new pass-2 golden repositories (Task 1) must not change, except the one reviewed addition of the `namespace` key in Task 3.
- Grammars are `==` pinned in `pyproject.toml` and `uv.lock`: `tree-sitter-go==0.25.0`, `tree-sitter-java==0.23.5`.
- No other new runtime dependency. XML is read with the standard library.
- Python 3.10 compatible. `uv run pyright svarupa` passes in strict mode; `uv run ruff check svarupa tests` and `uv run ruff format --check svarupa tests` pass.
- Every emitted diagnostic code is registered in `svarupa/diagnostics.py` and vice versa.
- Work on branch `feat/language-packs-1a-2`, one commit per task, PR at the end. Never push to `main`. No AI attribution in commits or PRs.
- Plain English, no em dashes, in comments, docs, commit messages and the PR. Commit subjects under 72 characters, imperative mood.

## Review Focus

1. A Go module path with no dot (`module shop`): an unresolvable `shop/x` import must count as unresolved, not as standard library. Pinned in Task 4 `test_a_dotless_module_path_is_never_standard_library`.
2. A Go package whose functions are split across files: `orders.NewService()` must find `NewService` in whichever file of the package defines it. Pinned by Task 2 `test_a_qualified_call_finds_its_target_in_any_file_of_the_package` and Task 4 `test_a_qualified_call_resolves_into_another_file_of_the_package`.
3. Go test files: an import of a package must not point at `_test.go` files. Pinned in Task 4 `test_package_imports_skip_test_files`.
4. A Java wildcard import of a package with several classes, and a static import: one edge per class file for the wildcard, the class file plus a reference to the member for the static import. Pinned in Task 5 tests.
5. A `pom.xml` with placeholders (`${project.groupId}`), that is not valid XML, or that declares entities: no crash, no invented dependency, no entity expansion. Pinned in Task 5 `test_maven_placeholders_and_broken_xml_add_nothing` and `test_a_pom_with_entities_is_refused`.

---

## File Structure

| File | Responsibility |
|---|---|
| `tests/test_golden_repos.py` | Builds small fixture repositories in a temp dir, runs `extract`, byte-compares nodes, edges, scorecard and diagnostics with `tests/golden_repos/<name>.json`. |
| `svarupa/extract/packs/modules.py` | `ModuleResolver` protocol, `ModuleContext`, `GoModules`, `JvmPackages`. |
| `svarupa/extract/packs/model.py` | `Pack.modules`, `Define.exported_by`, `Define.decorators_from`. |
| `svarupa/extract/packs/walker.py` | Uses the two new `Define` hooks; `Ctx.namespace`. |
| `svarupa/extract/base.py` | `FileFacts.namespace`. |
| `svarupa/extract/resolve.py` | `modules` parameter, `_resolve_targets`, multi-target import edges and lookups. |
| `svarupa/extract/__init__.py` | Builds the module context; `go_modules`; Go, Maven and Gradle dependencies. |
| `svarupa/extract/packs/go.py` | Go pack and hooks. |
| `svarupa/extract/packs/java.py` | Java pack and hooks. |
| `tests/test_module_resolvers.py` | Protocol behavior with a fake resolver; Go and Java resolver unit tests. |
| `tests/test_extract_go.py`, `tests/test_extract_java.py` | End-to-end extraction behavior per language. |
| `tests/golden/go/*.txt`, `tests/golden/java/*.txt` | Pass-1 golden cases. |

---

### Task 1: Pass-2 golden repositories for Python and TypeScript

**Files:**
- Create: `tests/test_golden_repos.py`
- Create (generated): `tests/golden_repos/python_shop.json`, `tests/golden_repos/ts_web.json`

**Interfaces:**
- Consumes: `svarupa.detect.detect`, `svarupa.extract.extract`, `svarupa.extract.declared_dependencies`, `tests.test_golden_facts._jsonable`.
- Produces: `REPOS: dict[str, dict[str, str]]` and `render_result(result) -> str`; Tasks 4 and 5 add entries to `REPOS`.

- [ ] **Step 1: Write the test**

`tests/test_golden_repos.py`:

```python
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
```

- [ ] **Step 2: Run it to see it fail**

Run: `uv run pytest tests/test_golden_repos.py -q`
Expected: `test_every_repo_has_a_golden_file` FAILS (no JSON files), both parametrized cases FAIL with "no golden file".

- [ ] **Step 3: Record and re-run**

Run: `SVARUPA_UPDATE_GOLDEN=1 uv run pytest tests/test_golden_repos.py -q; uv run pytest tests/test_golden_repos.py -q`
Expected: second run `3 passed`.

- [ ] **Step 4: Check the goldens hold what the fixtures were built to show**

Run: `grep -c '"kind": "imports"' tests/golden_repos/python_shop.json tests/golden_repos/ts_web.json`
Expected: both counts at least 5.

Run: `grep -o '"src/ns/tool/run.py", *$' tests/golden_repos/python_shop.json | head -1; grep -c 'ns/tool/helpers.py' tests/golden_repos/python_shop.json`
Expected: the namespace-package import resolved (second count at least 2).

Run: `python3 -c "import json;d=json.load(open('tests/golden_repos/ts_web.json'));print(sorted({r['lang'] for r in d['scorecard']['rows']}))"`
Expected: `['javascript', 'typescript']`.

Determinism: run the test 3 times with different hash seeds.

Run: `for s in 0 1 2; do PYTHONHASHSEED=$s uv run pytest tests/test_golden_repos.py -q 2>&1 | tail -1; done`
Expected: `3 passed` each time.

- [ ] **Step 5: Full suite and commit**

Run: `uv run ruff check svarupa tests && uv run ruff format --check svarupa tests && uv run pytest -q`
Expected: all pass.

```bash
git add docs/superpowers tests/test_golden_repos.py tests/golden_repos
git commit -m "Add pass-2 golden repositories and plan 1a-2"
```

(The plan and spec Revision 2 ride along in this first commit.)

---

### Task 2: Module resolver protocol and multi-target imports

**Files:**
- Create: `svarupa/extract/packs/modules.py`
- Modify: `svarupa/extract/packs/model.py` (add `Pack.modules`)
- Modify: `svarupa/extract/resolve.py` (constructor, `_package_root`, `_is_external`, `_resolve_targets`, `_resolve_module`, `_import_edges`, `_call_targets`, `resolve()`)
- Modify: `svarupa/extract/__init__.py` (build the module context and pass resolvers)
- Test: `tests/test_module_resolvers.py`

**Interfaces:**
- Consumes: Task 1 goldens (must stay unchanged).
- Produces:
  - `modules.ModuleResolver` protocol: `targets(spec: str, from_file: str, /) -> tuple[str, ...]` (sorted repository files, `()` when none), `is_external(spec: str, /) -> bool`.
  - `modules.ModuleContext(files: frozenset[str], facts: tuple[FileFacts, ...], deps: frozenset[str], go_modules: tuple[tuple[str, str], ...] = ())`.
  - `model.Pack.modules: Callable[[ModuleContext], ModuleResolver] | None = None`.
  - `Resolver(..., modules: Mapping[str, ModuleResolver] | None = None)` and `resolve(..., modules=None)`.
  - `Resolver._resolve_targets(spec, from_file, level, lang) -> tuple[str, ...]`.

- [ ] **Step 1: Write the failing tests**

`tests/test_module_resolvers.py`:

```python
"""Module resolvers: imports that name a package rather than one file."""

from __future__ import annotations

from svarupa.extract.base import CallShape, CallSite, FileFacts, ImportRef, SymbolRef
from svarupa.extract.resolve import Resolver
from svarupa.model import EdgeKind, Evidence, Resolution


class TwoFiles:
    """A package `pkg` made of two files; `std` is provably external."""

    def targets(self, spec: str, _from_file: str, /) -> tuple[str, ...]:
        return ("pkg/a.toy", "pkg/b.toy") if spec == "pkg" else ()

    def is_external(self, spec: str, /) -> bool:
        return spec == "std"


def ev(path: str, line: int = 1) -> Evidence:
    return Evidence(path, line, line)


def _import(spec: str, line: int) -> ImportRef:
    return ImportRef(
        specifier=spec,
        names=(),
        alias_of={spec: spec},
        evidence=ev("app/main.toy", line),
        is_from=True,
    )


def facts() -> list[FileFacts]:
    main = FileFacts(
        path="app/main.toy",
        lang="toy",
        imports=(_import("pkg", 1), _import("std", 2), _import("missing", 3)),
        calls=(CallSite("Open", CallShape.QUALIFIED, "pkg", ev("app/main.toy", 4), None, None),),
    )
    a = FileFacts(
        path="pkg/a.toy",
        lang="toy",
        symbols=(SymbolRef("Helper", "pkg.a.Helper", "function", ev("pkg/a.toy")),),
    )
    b = FileFacts(
        path="pkg/b.toy",
        lang="toy",
        symbols=(SymbolRef("Open", "pkg.b.Open", "function", ev("pkg/b.toy")),),
    )
    return [main, a, b]


def run():
    return Resolver(facts(), modules={"toy": TwoFiles()}).run()


def test_a_package_import_points_at_every_file_in_it() -> None:
    edges = sorted((e.src, e.dst) for e in run().edges if e.kind is EdgeKind.IMPORTS)
    assert edges == [("app/main.toy", "pkg/a.toy"), ("app/main.toy", "pkg/b.toy")]


def test_a_package_import_counts_once_in_the_scorecard() -> None:
    card = run().scorecard
    assert card.get("toy", "imports", Resolution.RESOLVED) == 1
    assert card.get("toy", "imports", Resolution.EXTERNAL) == 1
    assert card.get("toy", "imports", Resolution.UNRESOLVED) == 1


def test_a_qualified_call_finds_its_target_in_any_file_of_the_package() -> None:
    calls = [(e.src, e.dst) for e in run().edges if e.kind is EdgeKind.CALLS]
    assert calls == [("app/main.toy", "pkg/b.toy#pkg.b.Open")]


def test_languages_without_a_resolver_are_untouched() -> None:
    r = Resolver(facts(), modules={})
    assert r.resolve_module("pkg", "app/main.toy", 0, "toy") is None
```

Run: `uv run pytest tests/test_module_resolvers.py -q`
Expected: `TypeError: Resolver.__init__() got an unexpected keyword argument 'modules'` in three tests.

- [ ] **Step 2: Write the protocol module**

`svarupa/extract/packs/modules.py`:

```python
"""Module resolution for languages whose imports name packages.

Python and TypeScript resolve in `resolve.py`, where their measured traps
are documented. A language added as a pack brings its resolver here, and
`resolve.py` asks it first. A resolver answers two questions only: which
repository files does this specifier name, and is it provably outside the
repository. Everything else (edges, scorecard bins, references) stays in
one place.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from svarupa.extract.base import FileFacts

__all__ = ["ModuleContext", "ModuleResolver"]


class ModuleResolver(Protocol):
    def targets(self, spec: str, from_file: str, /) -> tuple[str, ...]:
        """Repository files the specifier names, sorted; () when none."""
        ...

    def is_external(self, spec: str, /) -> bool:
        """True only when the specifier is provably outside the repository:
        the standard library or a declared dependency."""
        ...


@dataclass(frozen=True, slots=True)
class ModuleContext:
    """What a resolver may know. It never reads files itself."""

    files: frozenset[str]
    facts: tuple[FileFacts, ...]
    deps: frozenset[str]
    go_modules: tuple[tuple[str, str], ...] = ()  # (module path, directory)
```

In `svarupa/extract/packs/model.py`, add below the `if TYPE_CHECKING:` import of walker:

```python
    from svarupa.extract.packs.modules import ModuleContext, ModuleResolver
```

and add to `Pack`, after `decorator`:

```python
    # Imports that name packages need their own resolver; None means the
    # language resolves in resolve.py (Python, TypeScript, JavaScript).
    modules: Callable[[ModuleContext], ModuleResolver] | None = None
```

Because `ModuleContext` is imported only under `TYPE_CHECKING`, write the annotation as a string if pyright or the runtime complains: `modules: Callable[["ModuleContext"], "ModuleResolver"] | None = None`. `from __future__ import annotations` already makes dataclass field annotations lazy, so the unquoted form works at runtime; keep it unquoted if pyright accepts it.

- [ ] **Step 3: Teach the resolver about module resolvers**

In `svarupa/extract/resolve.py`:

Add the import:

```python
from svarupa.extract.packs.modules import ModuleResolver
```

Constructor: add the parameter after `ts_packages` and store it:

```python
        ts_packages: Sequence[tuple[str, str]] = (),
        modules: Mapping[str, ModuleResolver] | None = None,
    ) -> None:
        self.facts = list(facts)
        self.deps = declared_deps
        # Languages whose imports name packages resolve through their pack's
        # resolver; the rest use the code below.
        self.modules: dict[str, ModuleResolver] = dict(modules or {})
```

`_package_root`: remove `@staticmethod`, add `self`, and return the specifier for resolver languages:

```python
    def _package_root(self, spec: str, lang: str) -> str:
        """...existing docstring..."""
        if lang in self.modules:
            return spec
        if lang in ("typescript", "javascript"):
```

`_is_external`: first line of the body:

```python
        if lang in self.modules:
            return self.modules[lang].is_external(top)
```

Replace `_resolve_module` with:

```python
    def _resolve_module(
        self, spec: str, from_file: str, level: int, lang: str = "python"
    ) -> str | None:
        targets = self._resolve_targets(spec, from_file, level, lang)
        return targets[0] if targets else None

    def _resolve_targets(
        self, spec: str, from_file: str, level: int, lang: str = "python"
    ) -> tuple[str, ...]:
        """Every repository file the specifier names: one for a module
        import, all of a package's source files for a package import."""
        if lang in self.modules:
            return self.modules[lang].targets(spec, from_file)
        if lang in ("typescript", "javascript"):
            single = self._resolve_ts(spec, from_file)
        else:
            single = self._resolve_python(spec, from_file, level)
        return (single,) if single else ()
```

In `_import_edges`, replace the line

```python
            target = self._resolve_module(imp.specifier, f.path, imp.level, f.lang)
```

with

```python
            targets = self._resolve_targets(imp.specifier, f.path, imp.level, f.lang)
            target = targets[0] if targets else None
```

and replace the block from `if target == f.path:` down to (and including) the `out.append(Edge(... producer=f"{f.lang}.imports",))` that follows `self.scorecard.record(f.lang, EdgeKind.IMPORTS, Resolution.RESOLVED)` with:

```python
            # A package import names several files; it is still one import.
            others = tuple(t for t in targets if t != f.path)
            if not others:
                continue

            self.scorecard.record(f.lang, EdgeKind.IMPORTS, Resolution.RESOLVED)
            for dst in others:
                out.append(
                    Edge(
                        src=f.path,
                        dst=dst,
                        kind=EdgeKind.IMPORTS,
                        evidence=(imp.evidence,),
                        confidence=Confidence.RESOLVED,
                        resolution=Resolution.RESOLVED,
                        attrs=(("type_only", "true"),) if imp.type_only else (),
                        producer=f"{f.lang}.imports",
                    )
                )
```

Further down in the same method, replace

```python
                found = self._follow_reexport(target, lookup)
```

with

```python
                found = next(
                    (x for t in others if (x := self._follow_reexport(t, lookup)) is not None),
                    None,
                )
```

In `_call_targets`, both the BARE and the QUALIFIED branch contain this block (with `name` as the looked-up symbol):

```python
                top = self._package_root(spec, f.lang)
                target = self._resolve_module(spec, f.path, level, f.lang)
                if target is None:
                    return None if self._is_external(top, f.lang) else []
                found = self._follow_reexport(target, name)
                return [found] if found else []
```

Replace each occurrence with:

```python
                return self._lookup_in_module(f, spec, level, name)
```

and add this method right after `_call_targets`:

```python
    def _lookup_in_module(
        self, f: FileFacts, spec: str, level: int, name: str
    ) -> list[tuple[str, str]] | None:
        """`name` defined in the module or package `spec` names.

        None when the module is provably external, [] when it is ours but the
        name is not found. A package spreads its definitions over files, so
        every file is searched; two hits are candidates, not a guess.
        """
        targets = self._resolve_targets(spec, f.path, level, f.lang)
        if not targets:
            return None if self._is_external(self._package_root(spec, f.lang), f.lang) else []
        hits = _dedupe(
            [x for t in targets if (x := self._follow_reexport(t, name)) is not None]
        )
        return hits if len(hits) <= MAX_CANDIDATE_ARITY else []
```

`resolve()` at the end of the file: add `modules: Mapping[str, ModuleResolver] | None = None` as the last parameter and pass it: `Resolver(facts, declared_deps, source_roots, ts_aliases, ts_packages, modules).run()`.

- [ ] **Step 4: Run the new tests and the goldens**

Run: `uv run pytest tests/test_module_resolvers.py tests/test_golden_repos.py tests/test_golden_facts.py -q`
Expected: all pass. The goldens did not change.

- [ ] **Step 5: Build resolvers from packs during extraction**

In `svarupa/extract/__init__.py`, extend the packs import:

```python
from svarupa.extract.packs import ANALYZED_ELSEWHERE, BY_DETECTED, load_extractors
from svarupa.extract.packs.modules import ModuleContext, ModuleResolver
```

and replace the `resolver = Resolver(...)` statement with:

```python
    context = ModuleContext(
        files=frozenset(f.path for f in facts),
        facts=tuple(facts),
        deps=declared_deps,
        go_modules=go_modules(scan),
    )
    modules: dict[str, ModuleResolver] = {
        lang: pack.modules(context)
        for lang, pack in sorted(BY_DETECTED.items())
        if pack.modules is not None and lang in _EXTRACTORS
    }
    resolver = Resolver(
        facts,
        declared_deps,
        roots,
        load_aliases(scan.root),
        workspace_packages(scan),
        modules,
    )
```

Add `go_modules` below `workspace_packages` (Task 4 fills it in; for now it finds nothing because nothing reads `go.mod` yet):

```python
def go_modules(scan: Scan) -> tuple[tuple[str, str], ...]:
    """Map each go.mod `module` path to the directory that declares it."""
    return ()
```

- [ ] **Step 6: Full check and commit**

Run: `uv run ruff check svarupa tests && uv run ruff format --check svarupa tests && uv run pyright svarupa && uv run pytest -q && uv run svarupa . --lock && git diff --stat .svarupa/architecture.lock`
Expected: clean, all pass. The lockfile may gain `dep svarupa/extract svarupa/extract/packs`-style lines only if new imports between modules appeared; the only new import is `resolve.py` -> `packs.modules`, which is inside the existing `svarupa/extract -> svarupa/extract/packs` dependency, so expect no change.

```bash
git add -A svarupa tests .svarupa/architecture.lock
git commit -m "Let packs resolve imports that name whole packages"
```

---

### Task 3: Walker hooks for export and decorators, and file namespaces

**Files:**
- Modify: `svarupa/extract/base.py` (`FileFacts.namespace`)
- Modify: `svarupa/extract/packs/model.py` (`Define.exported_by`, `Define.decorators_from`, two hook aliases)
- Modify: `svarupa/extract/packs/walker.py` (`Ctx.namespace`, use the hooks)
- Test: `tests/test_pack_walker.py`
- Regenerate: every `tests/golden/**/*.json` (adds `"namespace": ""`)

**Interfaces:**
- Produces: `FileFacts.namespace: str = ""` (last field); `Ctx.namespace: str` (hooks may set it); `ExportedHook = Callable[[Ctx, TSNode, str], bool]`; `DecoratorsHook = Callable[[Ctx, TSNode], tuple[DecoratorRef, ...]]`; `Define.exported_by: ExportedHook | None = None`; `Define.decorators_from: DecoratorsHook | None = None`.

- [ ] **Step 1: Write the failing walker tests**

Append to `tests/test_pack_walker.py`:

```python
def test_an_export_hook_overrides_the_name_rule() -> None:
    from dataclasses import replace

    capital = Define(
        kind="function", exported_by=lambda _ctx, _node, name: name[:1].isupper()
    )
    pack = replace(PACK, rules={**PACK.rules, "function_definition": capital})
    f = PackExtractor(pack).parse("m.py", b"def Up():\n    pass\n\ndef down():\n    pass\n")
    assert [(s.name, s.exported) for s in f.symbols] == [("Up", True), ("down", False)]


def test_a_decorators_hook_adds_decorators() -> None:
    from dataclasses import replace

    def tagged(ctx: Ctx, node: TSNode) -> tuple[DecoratorRef, ...]:
        return (DecoratorRef(name="tag", arg=None, evidence=ctx.node_evidence(node)),)

    rule = Define(kind="class", sets_class=True, decorators_from=tagged)
    pack = replace(PACK, rules={**PACK.rules, "class_definition": rule})
    f = PackExtractor(pack).parse("m.py", b"class A:\n    pass\n")
    assert [d.name for d in f.symbols[0].decorators] == ["tag"]


def test_a_hook_can_record_the_file_namespace() -> None:
    from dataclasses import replace

    from svarupa.extract.packs.model import Custom

    def remember(ctx: Ctx, node: TSNode, _frame: Frame, _depth: int) -> None:
        ctx.namespace = ctx.text(node).split()[1]

    pack = replace(PACK, rules={**PACK.rules, "import_statement": Custom(hook=remember)})
    f = PackExtractor(pack).parse("m.py", b"import shop.core\n")
    assert f.namespace == "shop.core"


def test_namespace_defaults_to_empty() -> None:
    assert parse("x = 1\n").namespace == ""
```

Run: `uv run pytest tests/test_pack_walker.py -q`
Expected: the four new tests fail (`TypeError` on unknown `Define` arguments, `AttributeError` on `namespace`).

- [ ] **Step 2: Add the field and the hooks**

`svarupa/extract/base.py`, `FileFacts`, after `ctor_assigns`:

```python
    # The language's own namespace for the file (Java `package a.b;`), or "".
    # Languages whose imports name packages resolve through it.
    namespace: str = ""
```

`svarupa/extract/packs/model.py`: add to the alias block

```python
ExportedHook = Callable[["Ctx", TSNode, str], bool]
DecoratorsHook = Callable[["Ctx", TSNode], tuple[DecoratorRef, ...]]
```

add both names to `__all__`, and add to `Define` after `after`:

```python
    exported_by: ExportedHook | None = None  # Java `public`, Go capital letter
    decorators_from: DecoratorsHook | None = None  # Java annotations in `modifiers`
```

`svarupa/extract/packs/walker.py`:
- in `Ctx.__init__`, after `self.reexports`: `self.namespace = ""`
- in `run()`, add `namespace=self.namespace,` after `ctor_assigns=...`
- in `_define`, right after the existing `if rule.own_decorators is not None:` block (leave that block exactly as it is):

```python
        if rule.decorators_from is not None:
            decorators += rule.decorators_from(self, node)
```

- in the `SymbolRef(...)` call in `_define`, replace the `exported=` line with:

```python
                exported=(
                    rule.exported_by(self, node, name)
                    if rule.exported_by is not None
                    else frame.exported
                    if rule.inherit_exported
                    else not name.startswith("_")
                ),
```

- [ ] **Step 3: Run the walker tests**

Run: `uv run pytest tests/test_pack_walker.py -q`
Expected: all pass.

- [ ] **Step 4: Regenerate the pass-1 goldens and check the diff is only the new key**

```bash
SVARUPA_UPDATE_GOLDEN=1 uv run pytest tests/test_golden_facts.py -q
git diff --stat tests/golden
git diff tests/golden | grep '^[+-] ' | sort | uniq -c
```

Expected: every `tests/golden/**/*.json` changed, and the last command prints exactly three kinds of line, each 13 times (once per case): `- "ctor_assigns": []` or its non-empty form losing nothing but gaining a comma, `+ "ctor_assigns": [],`, and `+ "namespace": ""`. Concretely: the only removed lines are the old last `ctor_assigns` lines, and every added line is either that same line with a trailing comma or `"namespace": ""`. If anything else appears, stop: the walker changed behavior.

Run: `uv run pytest tests/test_golden_repos.py -q`
Expected: pass with no regeneration (pass-2 output does not include `FileFacts`).

- [ ] **Step 5: Full check and commit**

Run: `uv run ruff check svarupa tests && uv run ruff format --check svarupa tests && uv run pyright svarupa && uv run pytest -q`
Expected: all pass.

```bash
git add -A svarupa tests
git commit -m "Add export and decorator hooks and file namespaces to packs"
```

---

### Task 4: Go pack, Go module resolver and go.mod dependencies

**Files:**
- Create: `svarupa/extract/packs/go.py`
- Modify: `svarupa/extract/packs/modules.py` (add `GoModules`)
- Modify: `svarupa/extract/packs/__init__.py` (register Go)
- Modify: `svarupa/extract/__init__.py` (`go_modules`, `go.mod` in `declared_dependencies`)
- Modify: `pyproject.toml`, `uv.lock` (pin `tree-sitter-go==0.25.0`)
- Modify: `tests/test_packs.py` (unanalyzed set), `tests/test_extract.py` (X-012 fixture moves from Go to Java)
- Create: `tests/test_extract_go.py`, `tests/golden/go/{imports,declarations}.txt` (+ generated JSON)
- Modify: `tests/test_golden_repos.py` (add `go_shop`), `tests/test_golden_facts.py` (case count)
- Modify: `tests/test_module_resolvers.py` (Go resolver unit tests)

**Interfaces:**
- Consumes: Task 2 protocol and context, Task 3 hooks.
- Produces: `go.PACK` registered as `BY_DETECTED["go"]`; `modules.GoModules(context)`; `extract.go_modules(scan)`; `extract._go_mod(text) -> tuple[str | None, list[str]]`.

- [ ] **Step 1: Pin the grammar**

In `pyproject.toml` dependencies, after the `tree-sitter-typescript` line, add `"tree-sitter-go==0.25.0",` then run `uv lock && uv sync --dev`.
Run: `uv run python -c "import tree_sitter_go, tree_sitter; print(tree_sitter.Language(tree_sitter_go.language()).abi_version)"`
Expected: `15`.

- [ ] **Step 2: Write the failing resolver unit tests**

Append to `tests/test_module_resolvers.py`:

```python
from svarupa.extract.packs.modules import GoModules, ModuleContext  # noqa: E402

GO_FILES = frozenset(
    {
        "go.mod",
        "cmd/api/main.go",
        "internal/orders/service.go",
        "internal/orders/repo.go",
        "internal/orders/service_test.go",
        "tools/gen/gen.go",
    }
)


def go(modules: tuple[tuple[str, str], ...], deps: frozenset[str] = frozenset()) -> GoModules:
    return GoModules(ModuleContext(files=GO_FILES, facts=(), deps=deps, go_modules=modules))


def test_package_imports_skip_test_files() -> None:
    r = go((("github.com/acme/shop", ""),))
    assert r.targets("github.com/acme/shop/internal/orders", "cmd/api/main.go") == (
        "internal/orders/repo.go",
        "internal/orders/service.go",
    )


def test_the_longest_module_path_wins() -> None:
    r = go((("github.com/acme/shop", ""), ("github.com/acme/shop/tools", "tools")))
    assert r.targets("github.com/acme/shop/tools/gen", "x.go") == ("tools/gen/gen.go",)


def test_standard_library_and_required_modules_are_external() -> None:
    r = go((("github.com/acme/shop", ""),), frozenset({"github.com/gin-gonic/gin"}))
    assert r.is_external("net/http")
    assert r.is_external("github.com/gin-gonic/gin")
    assert r.is_external("github.com/gin-gonic/gin/binding")
    assert not r.is_external("github.com/gin-gonic/ginx")
    assert not r.is_external("github.com/other/lib")


def test_a_dotless_module_path_is_never_standard_library() -> None:
    r = go((("shop", ""),))
    assert r.targets("shop/nowhere", "cmd/api/main.go") == ()
    assert not r.is_external("shop/nowhere")
    assert r.is_external("fmt")
```

Run: `uv run pytest tests/test_module_resolvers.py -q`
Expected: `ImportError: cannot import name 'GoModules'`.

- [ ] **Step 3: Write `GoModules`**

Append to `svarupa/extract/packs/modules.py` and add `"GoModules"` to `__all__`:

```python
class GoModules:
    """Go: an import path names a package, which is a directory.

    `<module path>/<dir>` resolves to that directory's non-test `.go` files,
    using every go.mod in the repository (longest module path first, so a
    nested module wins over its parent). The standard library is any path
    whose first element has no dot: that is the Go toolchain's own rule.
    """

    def __init__(self, context: ModuleContext) -> None:
        self.modules = tuple(sorted(context.go_modules, key=lambda m: (-len(m[0]), m[0])))
        self.deps = context.deps
        by_dir: dict[str, list[str]] = {}
        for path in context.files:
            if path.endswith(".go") and not path.endswith("_test.go"):
                directory = path.rsplit("/", 1)[0] if "/" in path else ""
                by_dir.setdefault(directory, []).append(path)
        self.by_dir = {d: tuple(sorted(v)) for d, v in by_dir.items()}

    def _module_of(self, spec: str) -> tuple[str, str] | None:
        for path, directory in self.modules:
            if spec == path or spec.startswith(path + "/"):
                return path, directory
        return None

    def targets(self, spec: str, from_file: str, /) -> tuple[str, ...]:
        if spec.startswith(("./", "../")):
            cur = from_file.split("/")[:-1]
            for part in spec.split("/"):
                if part in ("", "."):
                    continue
                if part == "..":
                    cur = cur[:-1]
                else:
                    cur.append(part)
            return self.by_dir.get("/".join(cur), ())
        hit = self._module_of(spec)
        if hit is None:
            return ()
        path, directory = hit
        rest = spec[len(path) :].strip("/")
        return self.by_dir.get("/".join(p for p in (directory, rest) if p), ())

    def is_external(self, spec: str, /) -> bool:
        # A module of this repository is never external, even when its path
        # has no dot (`module shop`), or a broken intra-repo import would be
        # filed under the standard library.
        if self._module_of(spec) is not None:
            return False
        if spec == "C" or "." not in spec.split("/", 1)[0]:
            return True
        return any(spec == d or spec.startswith(d + "/") for d in self.deps)
```

Run: `uv run pytest tests/test_module_resolvers.py -q`
Expected: all pass.

- [ ] **Step 4: Write the failing end-to-end tests**

`tests/test_extract_go.py`:

```python
"""Go extraction: packages, receivers and go.mod modules."""

from __future__ import annotations

from pathlib import Path

from svarupa.detect import detect
from svarupa.extract import declared_dependencies, extract
from svarupa.extract.packs import extractor
from svarupa.model import EdgeKind, Resolution


def write(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf8")


def run(root: Path):
    scan = detect(root)
    return extract(scan, declared_dependencies(scan))


GO_MOD = (
    "module github.com/acme/shop\n\ngo 1.22\n\nrequire (\n"
    "\tgithub.com/gin-gonic/gin v1.9.1 // web\n)\n"
)


def shop(root: Path) -> None:
    write(root, "go.mod", GO_MOD)
    write(
        root,
        "cmd/api/main.go",
        "package main\n\nimport (\n\t\"fmt\"\n\t\"github.com/gin-gonic/gin\"\n"
        "\t\"github.com/acme/shop/internal/orders\"\n\t\"github.com/acme/unknown/x\"\n)\n\n"
        "func main() {\n\tr := gin.Default()\n\tsvc := orders.NewService()\n"
        "\tfmt.Println(svc, r, x.Y())\n}\n",
    )
    write(
        root,
        "internal/orders/service.go",
        "package orders\n\ntype Service struct{ repo *Repo }\n\n"
        "func NewService() *Service {\n\treturn &Service{repo: newRepo()}\n}\n\n"
        "func (s *Service) Place() error {\n\treturn s.repo.Save()\n}\n",
    )
    write(
        root,
        "internal/orders/repo.go",
        "package orders\n\ntype Repo struct{}\n\nfunc newRepo() *Repo { return &Repo{} }\n\n"
        "func (r *Repo) Save() error { return nil }\n",
    )
    write(root, "internal/orders/service_test.go", "package orders\n\nfunc TestPlace() {}\n")


def test_a_package_import_points_at_its_source_files(tmp_path: Path) -> None:
    shop(tmp_path)
    res = run(tmp_path)
    imports = sorted(
        (e.src, e.dst) for e in res.edges if e.kind is EdgeKind.IMPORTS
    )
    assert imports == [
        ("cmd/api/main.go", "internal/orders/repo.go"),
        ("cmd/api/main.go", "internal/orders/service.go"),
    ]


def test_go_imports_land_in_the_right_bins(tmp_path: Path) -> None:
    shop(tmp_path)
    card = run(tmp_path).scorecard
    assert card.get("go", "imports", Resolution.RESOLVED) == 1
    assert card.get("go", "imports", Resolution.EXTERNAL) == 2  # fmt, gin
    assert card.get("go", "imports", Resolution.UNRESOLVED) == 1  # acme/unknown/x


def test_a_qualified_call_resolves_into_another_file_of_the_package(tmp_path: Path) -> None:
    shop(tmp_path)
    calls = {(e.src, e.dst) for e in run(tmp_path).edges if e.kind is EdgeKind.CALLS}
    assert (
        "cmd/api/main.go#cmd.api.main.main",
        "internal/orders/service.go#internal.orders.service.NewService",
    ) in calls
    assert (
        "internal/orders/service.go#internal.orders.service.NewService",
        "internal/orders/repo.go#internal.orders.repo.newRepo",
    ) in calls


def test_methods_attach_to_their_receiver_type() -> None:
    f = extractor("go").parse(
        "svc/s.go",
        b"package svc\n\ntype S struct{}\n\nfunc (s *S) Run() {}\n\nfunc (g G[T]) Go() {}\n",
    )
    got = [(s.qualified_name, s.kind, s.enclosing_class, s.exported) for s in f.symbols]
    assert got == [
        ("svc.s.S", "class", None, True),
        ("svc.s.S.Run", "method", "S", True),
        ("svc.s.G.Go", "method", "G", True),
    ]


def test_exported_means_a_capital_first_letter() -> None:
    f = extractor("go").parse("a.go", b"package a\n\nfunc Up() {}\n\nfunc down() {}\n")
    assert [(s.name, s.exported) for s in f.symbols] == [("Up", True), ("down", False)]


def test_go_mod_requires_are_declared_dependencies(tmp_path: Path) -> None:
    write(tmp_path, "go.mod", GO_MOD + "require golang.org/x/sync v0.7.0\n")
    deps = declared_dependencies(detect(tmp_path))
    assert {"github.com/gin-gonic/gin", "golang.org/x/sync"} <= deps
```

Run: `uv run pytest tests/test_extract_go.py -q`
Expected: failures (`KeyError: 'go'` from `extractor`, and import edges empty).

- [ ] **Step 5: Write the Go pack**

`svarupa/extract/packs/go.py`:

```python
"""Go language pack.

Go names a package by import path and compiles it as one unit, so imports
resolve to directories (see modules.GoModules). Methods are declared at the
top level with a receiver (`func (s *Server) Run()`), so the receiver type,
not lexical nesting, makes a function a method. Exported means a capital
first letter: that is the language rule, not a convention.
"""

from __future__ import annotations

import re

from tree_sitter import Node as TSNode

from svarupa.extract.base import CallShape, CallSite, ImportRef, SymbolRef
from svarupa.extract.packs.model import (
    Call,
    Custom,
    Define,
    Grammar,
    Import,
    Maturity,
    Pack,
)
from svarupa.extract.packs.modules import GoModules
from svarupa.extract.packs.walker import Ctx, Frame

__all__ = ["BUILTINS", "PACK"]

# Builtin functions and conversions: calls to them are not intra-repo calls.
# fmt: off
BUILTINS = frozenset(
    {
        "append", "cap", "clear", "close", "complex", "copy", "delete", "imag",
        "len", "make", "max", "min", "new", "panic", "print", "println", "real",
        "recover", "any", "bool", "byte", "complex64", "complex128", "error",
        "float32", "float64", "int", "int8", "int16", "int32", "int64", "rune",
        "string", "uint", "uint8", "uint16", "uint32", "uint64", "uintptr",
    }
)
# fmt: on

_MAJOR = re.compile(r"^v[0-9]+$")


def _exported(name: str) -> bool:
    return name[:1].isupper()


def _package_name(path: str) -> str:
    """The name a package is used by when the import gives none.

    The real name is the target's `package` clause, which pass 1 cannot read
    from here; the last path element is the toolchain's convention, minus a
    major-version suffix (`.../v2`, `gopkg.in/yaml.v3`). A wrong guess only
    leaves a call unresolved: the name still has to match a definition.
    """
    parts = path.split("/")
    last = parts[-1]
    if _MAJOR.match(last) and len(parts) > 1:
        last = parts[-2]
    return re.sub(r"\.v[0-9]+$", "", last)


def _literal(ctx: Ctx, node: TSNode) -> str | None:
    if node.type == "interpreted_string_literal":
        return "".join(
            ctx.text(c) for c in node.children if c.type == "interpreted_string_literal_content"
        )
    if node.type == "raw_string_literal":
        return ctx.text(node).strip("`")
    return None


def import_(ctx: Ctx, node: TSNode) -> ImportRef | None:
    path_node = node.child_by_field_name("path")
    if path_node is None:
        return None
    spec = _literal(ctx, path_node)
    if not spec:
        return None
    alias_node = node.child_by_field_name("name")
    local = ctx.text(alias_node) if alias_node is not None else _package_name(spec)
    # `_` imports for side effects, `.` merges names into the file: neither
    # gives a name to call through.
    alias_of = {} if local in ("_", ".") else {local: spec}
    return ImportRef(
        specifier=spec,
        names=(),
        alias_of=alias_of,
        evidence=ctx.node_evidence(node),
        is_relative=spec.startswith("."),
        is_from=True,
    )


def type_spec(ctx: Ctx, node: TSNode, frame: Frame, _depth: int) -> None:
    name_node = node.child_by_field_name("name")
    ty = node.child_by_field_name("type")
    if name_node is None or ty is None:
        return
    kind = {"struct_type": "class", "interface_type": "interface"}.get(ty.type)
    if kind is None:
        return  # aliases and named basic types are not architecture
    name = ctx.text(name_node)
    ctx.symbols.append(
        SymbolRef(
            name=name,
            qualified_name=ctx.qual((*frame.stack, name)),
            kind=kind,
            evidence=ctx.node_evidence(node),
            exported=_exported(name),
        )
    )


def _receiver_type(ctx: Ctx, params: TSNode) -> str | None:
    for param in params.children:
        if param.type == "parameter_declaration":
            ty = param.child_by_field_name("type")
            if ty is None:
                return None
            name = ctx.text(ty).lstrip("*").split("[")[0].strip()
            return name or None
    return None


def method_declaration(ctx: Ctx, node: TSNode, frame: Frame, depth: int) -> None:
    name_node = node.child_by_field_name("name")
    if name_node is None:
        return
    receiver = node.child_by_field_name("receiver")
    owner = _receiver_type(ctx, receiver) if receiver is not None else None
    name = ctx.text(name_node)
    scope = (*frame.stack, owner, name) if owner else (*frame.stack, name)
    qualified = ctx.qual(scope)
    ctx.symbols.append(
        SymbolRef(
            name=name,
            qualified_name=qualified,
            kind="method" if owner else "function",
            evidence=ctx.node_evidence(node),
            enclosing_class=owner,
            exported=_exported(name),
        )
    )
    body = node.child_by_field_name("body")
    if body is None:
        return
    inner = Frame(scope, owner, qualified)
    for child in body.children:
        ctx.visit(child, inner, depth + 1)


def _first_str_arg(ctx: Ctx, node: TSNode) -> str | None:
    args = node.child_by_field_name("arguments")
    if args is None:
        return None
    for child in args.children:
        if child.type in ("(", ")", ",", "comment"):
            continue
        return _literal(ctx, child)
    return None


def call(ctx: Ctx, node: TSNode, frame: Frame) -> CallSite | None:
    func = node.child_by_field_name("function")
    if func is None:
        return None
    fn, cls = frame.fn, frame.cls
    first = _first_str_arg(ctx, node)
    if func.type == "identifier":
        name = ctx.text(func)
        if name in BUILTINS:
            return None
        row = node.start_point[0]
        return CallSite(name, CallShape.BARE, None, ctx.evidence(row, row), fn, cls, first)
    if func.type == "selector_expression":
        operand = func.child_by_field_name("operand")
        field = func.child_by_field_name("field")
        if operand is None or field is None:
            return None
        row = field.start_point[0]
        ev = ctx.evidence(row, row)
        if operand.type == "identifier":
            # A package (`orders.New()`) or a local value (`svc.Place()`);
            # the resolver decides, because only it knows the imports.
            return CallSite(
                ctx.text(field), CallShape.QUALIFIED, ctx.text(operand), ev, fn, cls, first
            )
        return CallSite(ctx.text(field), CallShape.MEMBER, ctx.text(operand), ev, fn, cls, first)
    return None


PACK = Pack(
    lang="go",
    grammar=Grammar(
        distribution="tree-sitter-go",
        version="0.25.0",
        module="tree_sitter_go",
        default="language",
    ),
    maturity=Maturity.EXPERIMENTAL,
    rules={
        "import_spec": Import(hook=import_),
        "type_spec": Custom(hook=type_spec),
        "function_declaration": Define(
            kind="function",
            records_class=False,
            keeps_decorators=False,
            exported_by=lambda _ctx, _node, name: _exported(name),
        ),
        "method_declaration": Custom(hook=method_declaration),
        "call_expression": Call(hook=call),
    },
    qualified_prefix=lambda path: path.rsplit(".", 1)[0].replace("/", "."),
    modules=GoModules,
)
```

Register it in `svarupa/extract/packs/__init__.py`: import `go` (keep the import list sorted: `go, javascript, python, typescript`), add `go.PACK` to `PACKS`, and add `"go": go.PACK,` to `BY_DETECTED`.

- [ ] **Step 6: Read go.mod**

In `svarupa/extract/__init__.py`, replace the placeholder `go_modules` with:

```python
def _go_mod(text: str) -> tuple[str | None, list[str]]:
    """The `module` path and the `require`d module paths of a go.mod.

    go.mod is a line format by specification (like requirements.txt), so it
    is read line by line; `//` starts a comment.
    """
    module: str | None = None
    required: list[str] = []
    in_block = False
    for raw in text.splitlines():
        line = raw.split("//", 1)[0].strip()
        if not line:
            continue
        if in_block:
            if line == ")":
                in_block = False
            else:
                required.append(line.split()[0])
            continue
        words = line.split()
        if words[0] == "module" and len(words) > 1:
            module = words[1].strip('"')
        elif words[0] == "require" and len(words) > 1:
            if words[1] == "(":
                in_block = True
            else:
                required.append(words[1])
    return module, required


def go_modules(scan: Scan) -> tuple[tuple[str, str], ...]:
    """Map each go.mod `module` path to the directory that declares it."""
    out: dict[str, str] = {}
    for rec in scan.files:
        if Path(rec.path).name != "go.mod":
            continue
        try:
            module, _ = _go_mod(read_text(scan.root, rec.path))
        except OSError:
            continue
        if module:
            holder = str(Path(rec.path).parent)
            out.setdefault(module, "" if holder == "." else holder)
    return tuple(sorted(out.items()))
```

In `declared_dependencies`, add a branch after the `package.json` branch:

```python
        elif name == "go.mod":
            names.update(_go_mod(text)[1])
```

- [ ] **Step 7: Move the X-012 tests off Go**

In `tests/packs` self-checks, `tests/test_packs.py::test_every_detected_language_has_a_pack_or_a_reason` now expects `{"rust", "java"}`. Change that line.

In `tests/test_extract.py::test_languages_without_a_pack_are_reported_once_each`, replace the two `.go` files with `.java` files and update the assertions:

```python
    write(tmp_path, "svc/Main.java", "class Main {}\n")
    write(tmp_path, "svc/Util.java", "class Util {}\n")
    write(tmp_path, "lib/x.rs", "fn main() {}\n")
    write(tmp_path, "app.py", "def keep():\n    pass\n")
    result = run(tmp_path)
    x012 = [d for d in result.diagnostics if d.code == "SVA-X-012"]
    assert [d.subject for d in x012] == ["java", "rust"]
    assert "2 java files" in x012[0].message
```

- [ ] **Step 8: Run the Go tests**

Run: `uv run pytest tests/test_extract_go.py tests/test_module_resolvers.py tests/test_packs.py tests/test_extract.py -q`
Expected: all pass.

- [ ] **Step 9: Golden cases for Go**

`tests/golden/go/imports.txt`:

```
path: cmd/api/main.go
---
package main

import (
	"fmt"
	db "github.com/acme/shop/internal/db"
	_ "net/http/pprof"
	. "github.com/acme/shop/internal/util"
	"gopkg.in/yaml.v3"
	"github.com/acme/shop/v2"
)

import "os"

func main() {
	fmt.Println(db.Open(), yaml.Marshal(nil), shop.Run())
	os.Exit(len("x"))
	helper(`raw`)
	conn.Pool().Get()
}
```

`tests/golden/go/declarations.txt`:

```
path: internal/orders/service.go
---
package orders

type Service struct {
	repo Repo
}

type Store interface {
	Save() error
}

type ID string

func NewService() *Service {
	return &Service{}
}

func (s *Service) Place(id ID) error {
	go func() { notify(id) }()
	return s.repo.Save()
}

func (r Repo) save() {}

func broken( {
```

In `tests/test_golden_facts.py`, change `assert len(CASES) == 13` to `assert len(CASES) == 15`.

Run: `SVARUPA_UPDATE_GOLDEN=1 uv run pytest tests/test_golden_facts.py -q; uv run pytest tests/test_golden_facts.py -q`
Expected: second run all pass.

Check the recorded facts:

Run: `python3 -c "import json;d=json.load(open('tests/golden/go/imports.json'));print([(i['specifier'],i['alias_of']) for i in d['imports']]);print([(c['name'],c['shape'],c['receiver']) for c in d['calls']])"`
Expected: seven imports with aliases `fmt`, `db`, none for `_` and `.`, `yaml`, `shop`, `os`; calls `Println`/QUALIFIED/fmt, `Open`/db, `Marshal`/yaml, `Run`/shop, `Exit`/os, `helper`/BARE (no `len`), `Get` MEMBER and `Pool` QUALIFIED/conn.

Run: `python3 -c "import json;d=json.load(open('tests/golden/go/declarations.json'));print([(s['qualified_name'],s['kind'],s['enclosing_class'],s['exported']) for s in d['symbols']]);print([x['code'] for x in d['diagnostics']])"`
Expected: `Service` class, `Store` interface, no `ID`, `NewService` function, `Service.Place` method, `Repo.save` method not exported; diagnostics `['SVA-X-001']`.

- [ ] **Step 10: Golden repository for Go**

Add to `REPOS` in `tests/test_golden_repos.py`:

```python
    "go_shop": {
        "go.mod": (
            "module github.com/acme/shop\n\ngo 1.22\n\n"
            "require (\n\tgithub.com/gin-gonic/gin v1.9.1\n)\n"
        ),
        "cmd/api/main.go": (
            "package main\n\nimport (\n\t\"fmt\"\n\t\"github.com/gin-gonic/gin\"\n"
            "\t\"github.com/acme/shop/internal/orders\"\n)\n\n"
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
```

Run: `SVARUPA_UPDATE_GOLDEN=1 uv run pytest tests/test_golden_repos.py -q; uv run pytest tests/test_golden_repos.py -q && git diff --stat tests/golden_repos`
Expected: pass; only `tests/golden_repos/go_shop.json` is new, the Python and TypeScript goldens unchanged.

- [ ] **Step 11: Full check and commit**

Run: `uv run ruff check svarupa tests && uv run ruff format --check svarupa tests && uv run pyright svarupa && uv run pytest -q && uv run svarupa . --lock && git diff --stat .svarupa/architecture.lock`
Expected: clean, all pass, lockfile unchanged.

```bash
git add -A svarupa tests pyproject.toml uv.lock
git commit -m "Add the Go language pack and go.mod module resolution"
```

---

### Task 5: Java pack, package resolver and Maven/Gradle dependencies

**Files:**
- Create: `svarupa/extract/packs/java.py`
- Modify: `svarupa/extract/packs/modules.py` (add `JvmPackages`)
- Modify: `svarupa/extract/packs/__init__.py` (register Java)
- Modify: `svarupa/extract/__init__.py` (Maven, Gradle and version-catalog dependencies)
- Modify: `pyproject.toml`, `uv.lock` (pin `tree-sitter-java==0.23.5`)
- Modify: `tests/test_packs.py`, `tests/test_extract.py` (X-012 now only Rust)
- Create: `tests/test_extract_java.py`, `tests/golden/java/{controller,declarations}.txt` (+ JSON)
- Modify: `tests/test_golden_repos.py` (add `java_shop`), `tests/test_golden_facts.py` (case count), `tests/test_module_resolvers.py`

**Interfaces:**
- Consumes: Task 2 protocol, Task 3 hooks and `FileFacts.namespace`.
- Produces: `java.PACK` registered as `BY_DETECTED["java"]`; `modules.JvmPackages(context)`; `extract._maven_groups(text) -> set[str]`, `extract._gradle_groups(text) -> set[str]`, `extract._catalog_groups(text) -> set[str]`.

- [ ] **Step 1: Pin the grammar**

Add `"tree-sitter-java==0.23.5",` after the Go pin in `pyproject.toml`; `uv lock && uv sync --dev`.
Run: `uv run python -c "import tree_sitter_java, tree_sitter; print(tree_sitter.Language(tree_sitter_java.language()).abi_version)"`
Expected: `14`.

- [ ] **Step 2: Write the failing resolver unit tests**

Append to `tests/test_module_resolvers.py`:

```python
from svarupa.extract.packs.modules import JvmPackages  # noqa: E402


def _jfacts(path: str, ns: str) -> FileFacts:
    return FileFacts(path=path, lang="java", namespace=ns)


JAVA = (
    _jfacts("src/main/java/com/acme/shop/Api.java", "com.acme.shop"),
    _jfacts("src/main/java/com/acme/shop/model/Order.java", "com.acme.shop.model"),
    _jfacts("src/main/java/com/acme/shop/model/Line.java", "com.acme.shop.model"),
    _jfacts("src/main/java/com/acme/shop/util/Strings.java", "com.acme.shop.util"),
)


def jvm(deps: frozenset[str] = frozenset()) -> JvmPackages:
    return JvmPackages(
        ModuleContext(files=frozenset(f.path for f in JAVA), facts=JAVA, deps=deps)
    )


def test_a_class_import_names_its_file() -> None:
    assert jvm().targets("com.acme.shop.model.Order", "x") == (
        "src/main/java/com/acme/shop/model/Order.java",
    )


def test_a_package_import_names_every_class_in_it() -> None:
    assert jvm().targets("com.acme.shop.model", "x") == (
        "src/main/java/com/acme/shop/model/Line.java",
        "src/main/java/com/acme/shop/model/Order.java",
    )


def test_a_static_or_nested_import_names_the_class_file() -> None:
    assert jvm().targets("com.acme.shop.util.Strings.trim", "x") == (
        "src/main/java/com/acme/shop/util/Strings.java",
    )


def test_jvm_external_needs_the_jdk_or_a_declared_group() -> None:
    r = jvm(frozenset({"org.springframework"}))
    assert r.is_external("java.util.List")
    assert r.is_external("org.springframework.web.bind.annotation.GetMapping")
    assert not r.is_external("org.springframeworkx.Thing")
    assert not r.is_external("com.acme.missing.Thing")
    assert not r.is_external("com.acme.shop.model")
```

Run: `uv run pytest tests/test_module_resolvers.py -q`
Expected: `ImportError: cannot import name 'JvmPackages'`.

- [ ] **Step 3: Write `JvmPackages`**

Append to `svarupa/extract/packs/modules.py` and add `"JvmPackages"` to `__all__`:

```python
# Packages the JDK itself provides.
_JDK = ("java.", "javax.", "jdk.", "sun.", "com.sun.", "org.w3c.", "org.xml.", "org.ietf.", "org.omg.")


class JvmPackages:
    """Java: files are found by their `package` declaration, not their path.

    `a.b.C` names `C.java` in package `a.b`; a longer name (`a.b.C.member`,
    `a.b.C.Inner`) still names the file of `C`; a bare package (`a.b`, from
    `import a.b.*`) names every file in it. Source roots never need guessing,
    because every file says which package it is in.
    """

    def __init__(self, context: ModuleContext) -> None:
        by_ns: dict[str, list[str]] = {}
        for f in context.facts:
            if f.lang == "java":
                by_ns.setdefault(f.namespace, []).append(f.path)
        self.by_ns = {ns: tuple(sorted(paths)) for ns, paths in by_ns.items()}
        self.deps = context.deps

    def targets(self, spec: str, _from_file: str, /) -> tuple[str, ...]:
        parts = spec.split(".")
        for n in range(len(parts) - 1, 0, -1):
            package, cls = ".".join(parts[:n]), parts[n]
            hits = tuple(
                p for p in self.by_ns.get(package, ()) if p.rsplit("/", 1)[-1] == f"{cls}.java"
            )
            if hits:
                return hits
        return self.by_ns.get(spec, ())

    def is_external(self, spec: str, /) -> bool:
        if spec.startswith(_JDK):
            return True
        if any(spec == ns or spec.startswith(ns + ".") for ns in self.by_ns if ns):
            return False  # our own package: a miss here is a resolution failure
        return any(spec == d or spec.startswith(d + ".") for d in self.deps)
```

Run: `uv run pytest tests/test_module_resolvers.py -q`
Expected: all pass. (If ruff format reflows `_JDK` across lines, accept it.)

- [ ] **Step 4: Write the failing end-to-end tests**

`tests/test_extract_java.py`:

```python
"""Java extraction: packages, annotations and Maven/Gradle dependencies."""

from __future__ import annotations

from pathlib import Path

from svarupa.detect import detect
from svarupa.extract import declared_dependencies, extract
from svarupa.extract.base import CallShape
from svarupa.extract.packs import extractor
from svarupa.model import EdgeKind, Resolution


def write(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf8")


def run(root: Path):
    scan = detect(root)
    return extract(scan, declared_dependencies(scan))


POM = (
    "<project xmlns=\"http://maven.apache.org/POM/4.0.0\"><dependencies>"
    "<dependency><groupId>org.springframework.boot</groupId>"
    "<artifactId>spring-boot-starter-web</artifactId></dependency>"
    "</dependencies></project>\n"
)
SRC = "src/main/java/com/acme/shop"


def shop(root: Path) -> None:
    write(root, "pom.xml", POM)
    write(
        root,
        f"{SRC}/OrderController.java",
        "package com.acme.shop;\n\n"
        "import java.util.List;\n"
        "import org.springframework.web.bind.annotation.GetMapping;\n"
        "import com.acme.shop.service.OrderService;\n"
        "import com.acme.shop.model.*;\n"
        "import static com.acme.shop.util.Strings.trim;\n"
        "import com.acme.missing.Thing;\n\n"
        "public class OrderController {\n"
        "    private final OrderService service;\n\n"
        "    public OrderController(OrderService service) {\n"
        "        this.service = service;\n"
        "    }\n\n"
        '    @GetMapping("/orders")\n'
        "    public List<Order> list() {\n"
        '        trim("x");\n'
        "        return this.service.findAll();\n"
        "    }\n"
        "}\n",
    )
    write(
        root,
        f"{SRC}/service/OrderService.java",
        "package com.acme.shop.service;\n\n"
        "public class OrderService extends BaseService {\n"
        "    public Object findAll() {\n"
        "        return audit();\n"
        "    }\n"
        "}\n",
    )
    write(
        root,
        f"{SRC}/service/BaseService.java",
        "package com.acme.shop.service;\n\n"
        "abstract class BaseService {\n"
        "    protected Object audit() {\n"
        "        return null;\n"
        "    }\n"
        "}\n",
    )
    write(root, f"{SRC}/model/Order.java", "package com.acme.shop.model;\n\npublic class Order {}\n")
    write(root, f"{SRC}/model/Line.java", "package com.acme.shop.model;\n\npublic record Line(int qty) {}\n")
    write(
        root,
        f"{SRC}/util/Strings.java",
        "package com.acme.shop.util;\n\n"
        "public final class Strings {\n"
        "    public static String trim(String s) {\n"
        "        return s.trim();\n"
        "    }\n"
        "}\n",
    )


def test_class_wildcard_and_static_imports_resolve(tmp_path: Path) -> None:
    shop(tmp_path)
    imports = sorted(
        (e.src.rsplit("/", 1)[-1], e.dst.rsplit("/", 1)[-1])
        for e in run(tmp_path).edges
        if e.kind is EdgeKind.IMPORTS
    )
    assert imports == [
        ("OrderController.java", "Line.java"),
        ("OrderController.java", "Order.java"),
        ("OrderController.java", "OrderService.java"),
        ("OrderController.java", "Strings.java"),
    ]


def test_java_imports_land_in_the_right_bins(tmp_path: Path) -> None:
    shop(tmp_path)
    card = run(tmp_path).scorecard
    assert card.get("java", "imports", Resolution.RESOLVED) == 3
    assert card.get("java", "imports", Resolution.EXTERNAL) == 2  # java.util, spring
    assert card.get("java", "imports", Resolution.UNRESOLVED) == 1  # com.acme.missing


def test_field_injection_and_inherited_calls_resolve(tmp_path: Path) -> None:
    shop(tmp_path)
    calls = {
        (e.src.rsplit("#", 1)[-1], e.dst.rsplit("#", 1)[-1])
        for e in run(tmp_path).edges
        if e.kind is EdgeKind.CALLS
    }
    base = "src.main.java.com.acme.shop"
    assert (
        f"{base}.OrderController.OrderController.list",
        f"{base}.service.OrderService.OrderService.findAll",
    ) in calls
    assert (
        f"{base}.service.OrderService.OrderService.findAll",
        f"{base}.service.BaseService.BaseService.audit",
    ) in calls
    assert (
        f"{base}.OrderController.OrderController.list",
        f"{base}.util.Strings.Strings.trim",
    ) in calls


def test_annotations_exports_and_namespace_are_recorded() -> None:
    f = extractor("java").parse(
        "A.java",
        b'package com.a;\n@Rest public class A { @Get("/x") public void m() {} void hidden() {} }\n',
    )
    assert f.namespace == "com.a"
    a, m, hidden = f.symbols
    assert ([d.name for d in a.decorators], a.exported) == (["Rest"], True)
    assert ([(d.name, d.arg) for d in m.decorators], m.exported) == ([("Get", "/x")], True)
    assert hidden.exported is False


def test_an_unqualified_call_in_a_class_is_a_call_on_this() -> None:
    f = extractor("java").parse(
        "A.java",
        b"import static x.U.helper;\nclass A { void m() { own(); helper(); } }\n",
    )
    assert [(c.name, c.shape) for c in f.calls] == [
        ("own", CallShape.SELF),
        ("helper", CallShape.BARE),
    ]


def test_maven_gradle_and_catalog_groups_are_declared(tmp_path: Path) -> None:
    write(tmp_path, "pom.xml", POM)
    write(
        tmp_path,
        "app/build.gradle",
        "dependencies {\n  implementation 'com.google.guava:guava:33.0.0-jre'\n"
        '  implementation("io.jsonwebtoken:jjwt-api:0.12.5")\n}\n',
    )
    write(
        tmp_path,
        "gradle/libs.versions.toml",
        '[libraries]\njackson = { module = "com.fasterxml.jackson.core:jackson-databind" }\n'
        'lombok = "org.projectlombok:lombok:1.18.30"\n'
        'okhttp = { group = "com.squareup.okhttp3", name = "okhttp" }\n',
    )
    deps = declared_dependencies(detect(tmp_path))
    assert {
        "org.springframework",
        "com.google",
        "io.jsonwebtoken",
        "com.fasterxml",
        "org.projectlombok",
        "com.squareup",
    } <= deps


def test_a_pom_with_entities_is_refused(tmp_path: Path) -> None:
    write(
        tmp_path,
        "pom.xml",
        '<?xml version="1.0"?><!DOCTYPE p [<!ENTITY g "com.evil">]>'
        "<project><dependencies><dependency><groupId>&g;</groupId>"
        "</dependency></dependencies></project>\n",
    )
    assert "com.evil" not in declared_dependencies(detect(tmp_path))


def test_maven_placeholders_and_broken_xml_add_nothing(tmp_path: Path) -> None:
    write(
        tmp_path,
        "pom.xml",
        "<project><dependencies><dependency><groupId>${project.groupId}</groupId>"
        "</dependency></dependencies></project>\n",
    )
    write(tmp_path, "other/pom.xml", "<project><dependencies>\n")
    deps = declared_dependencies(detect(tmp_path))
    assert not {d for d in deps if "$" in d or d.startswith("project")}
```

Run: `uv run pytest tests/test_extract_java.py -q`
Expected: failures (`KeyError: 'java'`, missing edges and dependencies).

- [ ] **Step 5: Write the Java pack**

`svarupa/extract/packs/java.py`:

```python
"""Java language pack.

Every file declares its package, so imports resolve through those
declarations (modules.JvmPackages) and source roots never need guessing.
Annotations live inside `modifiers`; `public` there is what exported means.
An unqualified call inside a class is a call on `this`, unless the file
statically imported that name.
"""

from __future__ import annotations

from tree_sitter import Node as TSNode

from svarupa.extract.base import CallShape, CallSite, DecoratorRef, FieldType, ImportRef
from svarupa.extract.packs.model import (
    Call,
    Custom,
    Define,
    Field,
    Grammar,
    Import,
    Maturity,
    Pack,
)
from svarupa.extract.packs.modules import JvmPackages
from svarupa.extract.packs.walker import Ctx, Frame

__all__ = ["PACK"]


def _modifiers(node: TSNode) -> TSNode | None:
    return next((c for c in node.children if c.type == "modifiers"), None)


def _public(_ctx: Ctx, node: TSNode, _name: str) -> bool:
    mods = _modifiers(node)
    return mods is not None and any(c.type == "public" for c in mods.children)


def _string(ctx: Ctx, node: TSNode) -> str | None:
    """A static string literal, escapes kept in source spelling (as in TS)."""
    if node.type != "string_literal":
        return None
    return "".join(
        ctx.text(c) for c in node.children if c.type in ("string_fragment", "escape_sequence")
    )


def _type_name(text: str) -> str:
    """`List<Order>` -> `List`; `a.b.Order` -> `Order`."""
    return text.split("<")[0].split("[")[0].split(".")[-1].strip()


def annotations(ctx: Ctx, node: TSNode) -> tuple[DecoratorRef, ...]:
    mods = _modifiers(node)
    if mods is None:
        return ()
    out: list[DecoratorRef] = []
    for child in mods.children:
        if child.type not in ("annotation", "marker_annotation"):
            continue
        name_node = child.child_by_field_name("name")
        if name_node is None:
            continue
        arg: str | None = None
        dynamic = False
        args = child.child_by_field_name("arguments")
        if args is not None:
            for a in args.children:
                if a.type in ("(", ")", ",", "comment"):
                    continue
                arg = _string(ctx, a)
                dynamic = arg is None
                break
        out.append(
            DecoratorRef(
                name=ctx.text(name_node),
                arg=arg,
                evidence=ctx.node_evidence(child),
                arg_dynamic=dynamic,
            )
        )
    return tuple(out)


def package_declaration(ctx: Ctx, node: TSNode, _frame: Frame, _depth: int) -> None:
    name = next((c for c in node.children if c.type in ("scoped_identifier", "identifier")), None)
    if name is not None:
        ctx.namespace = ctx.text(name)


def import_(ctx: Ctx, node: TSNode) -> ImportRef | None:
    path = next((c for c in node.children if c.type in ("scoped_identifier", "identifier")), None)
    if path is None:
        return None
    dotted = ctx.text(path)
    static = any(c.type == "static" for c in node.children)
    if any(c.type == "asterisk" for c in node.children):
        return ImportRef(
            specifier=dotted,
            names=(),
            alias_of={},
            evidence=ctx.node_evidence(node),
            is_from=True,
            is_star=True,
        )
    head, _, last = dotted.rpartition(".")
    return ImportRef(
        # `import a.b.C` names the class; `import static a.b.C.m` names a
        # member of C, so the module is the class and the name is the member.
        specifier=head if static and head else dotted,
        names=(last,),
        alias_of={},
        evidence=ctx.node_evidence(node),
        is_from=True,
    )


def bases(ctx: Ctx, node: TSNode) -> tuple[str, ...]:
    out: list[str] = []
    for child in node.children:
        if child.type in ("superclass", "super_interfaces", "extends_interfaces"):
            for t in _type_nodes(child):
                out.append(_type_name(ctx.text(t)))
    return tuple(b for b in out if b)


def _type_nodes(node: TSNode) -> list[TSNode]:
    types = ("type_identifier", "generic_type", "scoped_type_identifier")
    found: list[TSNode] = []
    for child in node.children:
        if child.type in types:
            found.append(child)
        elif child.type == "type_list":
            found.extend(c for c in child.children if c.type in types)
    return found


def declared_field(ctx: Ctx, node: TSNode, cls: str) -> list[FieldType]:
    ty = node.child_by_field_name("type")
    declarator = node.child_by_field_name("declarator")
    name = declarator.child_by_field_name("name") if declarator is not None else None
    if ty is None or name is None:
        return []
    type_name = _type_name(ctx.text(ty))
    return [FieldType(cls, ctx.text(name), type_name)] if type_name else []


def _first_str_arg(ctx: Ctx, node: TSNode) -> str | None:
    args = node.child_by_field_name("arguments")
    if args is None:
        return None
    for child in args.children:
        if child.type in ("(", ")", ",", "comment"):
            continue
        return _string(ctx, child)
    return None


def call(ctx: Ctx, node: TSNode, frame: Frame) -> CallSite | None:
    name_node = node.child_by_field_name("name")
    if name_node is None:
        return None
    name = ctx.text(name_node)
    row = name_node.start_point[0]
    ev = ctx.evidence(row, row)
    fn, cls = frame.fn, frame.cls
    first = _first_str_arg(ctx, node)
    obj = node.child_by_field_name("object")
    if obj is None:
        imported = {n for imp in ctx.imports for n in imp.names}
        if cls and name not in imported:
            return CallSite(name, CallShape.SELF, "this", ev, fn, cls, first)
        return CallSite(name, CallShape.BARE, None, ev, fn, cls, first)
    if obj.type == "this":
        return CallSite(name, CallShape.SELF, "this", ev, fn, cls, first)
    if obj.type == "super":
        return CallSite(name, CallShape.SUPER, "super", ev, fn, cls, first)
    if obj.type == "field_access":
        target = obj.child_by_field_name("object")
        field = obj.child_by_field_name("field")
        if target is not None and target.type == "this" and field is not None:
            return CallSite(name, CallShape.SELF_FIELD, ctx.text(field), ev, fn, cls, first)
    if obj.type == "identifier":
        return CallSite(name, CallShape.QUALIFIED, ctx.text(obj), ev, fn, cls, first)
    return CallSite(name, CallShape.MEMBER, ctx.text(obj), ev, fn, cls, first)


def _type_rule(kind: str) -> Define:
    return Define(
        kind=kind,
        sets_class=True,
        bases=bases,
        exported_by=_public,
        decorators_from=annotations,
    )


PACK = Pack(
    lang="java",
    grammar=Grammar(
        distribution="tree-sitter-java",
        version="0.23.5",
        module="tree_sitter_java",
        default="language",
    ),
    maturity=Maturity.EXPERIMENTAL,
    rules={
        "package_declaration": Custom(hook=package_declaration),
        "import_declaration": Import(hook=import_),
        "class_declaration": _type_rule("class"),
        "enum_declaration": _type_rule("class"),
        "record_declaration": _type_rule("class"),
        "interface_declaration": _type_rule("interface"),
        "method_declaration": Define(
            kind="method", exported_by=_public, decorators_from=annotations
        ),
        "constructor_declaration": Define(
            kind="method", exported_by=_public, decorators_from=annotations
        ),
        "field_declaration": Field(hook=declared_field),
        "method_invocation": Call(hook=call),
    },
    qualified_prefix=lambda path: path.rsplit(".", 1)[0].replace("/", "."),
    modules=JvmPackages,
)
```

Register it in `svarupa/extract/packs/__init__.py`: import `java` (sorted: `go, java, javascript, python, typescript`), add `java.PACK` to `PACKS`, add `"java": java.PACK,` to `BY_DETECTED`.

- [ ] **Step 6: Read Maven, Gradle and version catalogs**

In `svarupa/extract/__init__.py`, add `import re` and `import xml.etree.ElementTree as ET` to the imports, and these helpers below `_go_mod`:

```python
def _jvm_group(group: str) -> str | None:
    """A Maven groupId, trimmed to its first two segments.

    `org.springframework.boot` publishes packages under `org.springframework`;
    the trim is over-inclusive on purpose, the safe direction for the same
    reason `_norm_dep` gives for Python distribution names. Placeholders like
    `${project.groupId}` name nothing.
    """
    group = group.strip()
    if not group or "$" in group or "{" in group:
        return None
    return ".".join(group.split(".")[:2])


def _maven_groups(text: str) -> set[str]:
    """`dependency/groupId` values from a pom.xml.

    Parsed, never grepped. A pom never needs a DTD or entities, so a document
    that declares one is refused before parsing: that closes entity-expansion
    and external-entity attacks without a new dependency.
    """
    if "<!DOCTYPE" in text or "<!ENTITY" in text:
        return set()
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return set()
    out: set[str] = set()
    for el in root.iter():
        if el.tag.rsplit("}", 1)[-1] != "dependency":
            continue
        for child in el:
            if child.tag.rsplit("}", 1)[-1] == "groupId" and child.text:
                if (g := _jvm_group(child.text)) is not None:
                    out.add(g)
    return out


# `group:artifact` or `group:artifact:version` inside a quoted string.
_GRADLE_COORD = re.compile(r"""["']([A-Za-z0-9_.\-]+):([A-Za-z0-9_.\-]+)(?::[^"']*)?["']""")


def _gradle_groups(text: str) -> set[str]:
    """Coordinate strings from a build.gradle(.kts).

    Gradle build files are programs, not data, so only literal coordinate
    strings are read. Anything computed is missed, which leaves an import
    unresolved rather than inventing an external one.
    """
    return {g for m in _GRADLE_COORD.finditer(text) if (g := _jvm_group(m.group(1)))}


def _catalog_groups(text: str) -> set[str]:
    """`[libraries]` entries of a Gradle version catalog (libs.versions.toml)."""
    data = load_toml(text)
    libraries = data.get("libraries") if data else None
    if not isinstance(libraries, dict):
        return set()
    out: set[str] = set()
    for value in cast("dict[str, object]", libraries).values():
        coordinate: object = value
        if isinstance(value, dict):
            entry = cast("dict[str, object]", value)
            coordinate = entry.get("module") or entry.get("group")
        if isinstance(coordinate, str) and (g := _jvm_group(coordinate.split(":")[0])):
            out.add(g)
    return out
```

In `declared_dependencies`, add after the `go.mod` branch:

```python
        elif name == "pom.xml":
            names.update(_maven_groups(text))
        elif name in ("build.gradle", "build.gradle.kts"):
            names.update(_gradle_groups(text))
        elif name == "libs.versions.toml":
            names.update(_catalog_groups(text))
```

- [ ] **Step 7: X-012 is now Rust only**

`tests/test_packs.py`: the expected unanalyzed set becomes `{"rust"}`.

`tests/test_extract.py::test_languages_without_a_pack_are_reported_once_each`: use only Rust files:

```python
    write(tmp_path, "lib/a.rs", "fn a() {}\n")
    write(tmp_path, "lib/b.rs", "fn b() {}\n")
    write(tmp_path, "app.py", "def keep():\n    pass\n")
    result = run(tmp_path)
    x012 = [d for d in result.diagnostics if d.code == "SVA-X-012"]
    assert [d.subject for d in x012] == ["rust"]
    assert "2 rust files" in x012[0].message
    assert any(n.id.endswith(".keep") for n in result.nodes)
```

- [ ] **Step 8: Run the Java tests**

Run: `uv run pytest tests/test_extract_java.py tests/test_module_resolvers.py tests/test_packs.py tests/test_extract.py -q`
Expected: all pass.

- [ ] **Step 9: Golden cases for Java**

`tests/golden/java/controller.txt`:

```
path: src/main/java/com/acme/shop/OrderController.java
---
package com.acme.shop;

import java.util.List;
import com.acme.shop.model.*;
import static com.acme.shop.util.Strings.trim;

@RestController
@RequestMapping("/api")
public class OrderController extends BaseController implements Api, Audited<Order> {
    private final OrderService service;
    private List<Order> cache;

    @Autowired
    public OrderController(OrderService service) {
        this.service = service;
    }

    @GetMapping(PATH)
    public List<Order> list() {
        trim("x");
        own();
        repo.save();
        super.close();
        Order.create().items();
        return this.service.findAll();
    }

    private void own() {}
}
```

`tests/golden/java/declarations.txt`:

```
path: src/main/java/com/acme/shop/model/Types.java
---
package com.acme.shop.model;

public interface Api extends Base, Other {
    void call();
}

enum Status { OPEN, CLOSED }

public record Line(int qty) {
    public int total() { return qty; }
}

class Outer {
    static class Inner {
        void run() { Outer.helper(); }
    }
}

class Broken {
    void m( {
}
```

In `tests/test_golden_facts.py`, change the case count to `17`.

Run: `SVARUPA_UPDATE_GOLDEN=1 uv run pytest tests/test_golden_facts.py -q; uv run pytest tests/test_golden_facts.py -q`
Expected: second run all pass.

Run: `python3 -c "import json;d=json.load(open('tests/golden/java/controller.json'));print(d['namespace']);print([(i['specifier'],i['names'],i['is_star']) for i in d['imports']]);print([(s['name'],s['kind'],s['exported'],[x['name'] for x in s['decorators']]) for s in d['symbols']]);print([(c['name'],c['shape'],c['receiver']) for c in d['calls']]);print(d['fields'])"`
Expected: namespace `com.acme.shop`; imports `java.util.List`/`List`, `com.acme.shop.model`/star, `com.acme.shop.util.Strings`/`trim`; class `OrderController` exported with `RestController`, `RequestMapping`; constructor exported with `Autowired`; `list` exported with `GetMapping`; `own` not exported; calls `trim` BARE, `own` SELF, `save` QUALIFIED/repo, `close` SUPER, `items` MEMBER, `create` QUALIFIED/Order, `findAll` SELF_FIELD/service; fields `service: OrderService`, `cache: List`.

Run: `python3 -c "import json;d=json.load(open('tests/golden/java/declarations.json'));print([(s['qualified_name'],s['kind'],s['bases']) for s in d['symbols']]);print([x['code'] for x in d['diagnostics']])"`
Expected: `Api` interface with bases `Base`, `Other`; `Status` class; `Line` class and `Line.total`; `Outer`, `Outer.Inner`, `Outer.Inner.run`; diagnostics `['SVA-X-001']`.

- [ ] **Step 10: Golden repository for Java**

Add to `REPOS` in `tests/test_golden_repos.py`:

```python
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
            "package com.acme.shop.service;\n\n"
            "class Base {\n    void audit() {}\n}\n"
        ),
    },
```

Run: `SVARUPA_UPDATE_GOLDEN=1 uv run pytest tests/test_golden_repos.py -q; uv run pytest tests/test_golden_repos.py -q && git diff --stat tests/golden_repos`
Expected: pass; only `java_shop.json` new.

- [ ] **Step 11: Full check and commit**

Run: `uv run ruff check svarupa tests && uv run ruff format --check svarupa tests && uv run pyright svarupa && uv run pytest -q && uv run svarupa . --lock && git diff --stat .svarupa/architecture.lock`
Expected: clean, all pass, lockfile unchanged.

```bash
git add -A svarupa tests pyproject.toml uv.lock
git commit -m "Add the Java language pack and package-based import resolution"
```

---

### Task 6: Mutation coverage, README and the pull request

**Files:**
- Modify: `scripts/mutate_packs.py` (four mutations)
- Modify: `README.md` (supported languages)

- [ ] **Step 1: Add mutations**

Append to `MUTATIONS` in `scripts/mutate_packs.py`, and add `"tests/test_module_resolvers.py"`, `"tests/test_extract_go.py"`, `"tests/test_extract_java.py"`, `"tests/test_golden_repos.py"` to `SUITE`:

```python
    (
        "a package import points at only its first file",
        "svarupa/extract/resolve.py",
        "            for dst in others:\n",
        "            for dst in others[:1]:\n",
    ),
    (
        "go test files become import targets",
        "svarupa/extract/packs/modules.py",
        'if path.endswith(".go") and not path.endswith("_test.go"):',
        'if path.endswith(".go"):',
    ),
    (
        "a dotless go module path counts as standard library",
        "svarupa/extract/packs/modules.py",
        "        if self._module_of(spec) is not None:\n            return False\n",
        "",
    ),
    (
        "a pom that declares entities is parsed anyway",
        "svarupa/extract/__init__.py",
        '    if "<!DOCTYPE" in text or "<!ENTITY" in text:\n        return set()\n',
        "",
    ),
    (
        "maven placeholders become dependencies",
        "svarupa/extract/__init__.py",
        '    if not group or "$" in group or "{" in group:\n        return None\n',
        "    if not group:\n        return None\n",
    ),
```

Also add to the module docstring's bullet list:

```
* a package import points at every file in the package;
* Go test files are never import targets;
* a dotless Go module path is never the standard library;
* Maven placeholders are never dependencies;
* a pom that declares entities is refused.
```

Run: `uv run python scripts/mutate_packs.py`
Expected: 12 `caught` lines, exit 0. `git status --short svarupa` is empty afterwards.

- [ ] **Step 2: README**

In `README.md`, under "Quickstart" after the artifact bullet list's last bullet, add:

```markdown
- Code facts come from language packs: Python, TypeScript, JavaScript, Go
  and Java today. Other detected languages are listed as detected but not
  analyzed (SVA-X-012), never guessed.
```

- [ ] **Step 3: Full check, commit, PR**

Run: `uv run ruff check svarupa tests && uv run ruff format --check svarupa tests && uv run pyright svarupa && uv run pytest -q`
Expected: all pass.

```bash
git add scripts/mutate_packs.py README.md
git commit -m "Cover package resolution with mutations and list languages"
git push -u origin feat/language-packs-1a-2
gh pr create --base main --head feat/language-packs-1a-2 \
  --title "Language packs 1a-2: Go and Java, package imports" --body-file - <<'EOF'
Step 1a-2 of docs/superpowers/specs/2026-10-03-language-packs-design.md (Revision 2).

- Go and Java are analyzed: packs for both, experimental maturity.
- A pack can bring a module resolver; a package import points at every source file in the package (one scorecard count).
- Go: modules from every go.mod, standard library by the toolchain's dot rule, dependencies from `require`.
- Java: files indexed by their `package` declaration; class, wildcard and static imports; JDK prefixes; dependencies from pom.xml, Gradle coordinates and version catalogs.
- Python and TypeScript output unchanged: pass-1 goldens and new pass-2 golden repositories prove it.

Output changes for users:
- Repos with Go or Java files now get module diagrams, symbols and calls for them; SVA-X-012 no longer fires for Go or Java.
- `FileFacts` gains `namespace` (internal; not in any emitted artifact).

Next: the benchmark (1b).
EOF
```

Expected: PR URL printed. Do not merge.
