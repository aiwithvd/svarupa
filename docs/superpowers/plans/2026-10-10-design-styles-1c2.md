# Design Styles 1c-2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Detect the design style of each app unit in a repository, let a person accept it as `.svarupa/design.yaml` with `svarupa design`, report imports that break it as four new health checks, and give agents the rules through `get_design_rules`.

**Architecture:** A new `svarupa.design` package: styles are data (`styles.py`), units come from manifests (`units.py`), `match.py` assigns modules to parts by name and evidence and scores fit = coverage x compliance, `file.py` reads and writes design.yaml, `classify.py` labels the system level. `svarupa.health.assess` takes the resulting `Design` and adds its violations. Outputs reuse the 1c-1 surfaces.

**Tech Stack:** Python 3.10 to 3.13, PyYAML (already a dependency), networkx, pytest, ruff, pyright strict.

**Spec:** `docs/superpowers/specs/2026-10-10-design-styles-1c2-design.md`; builds on 1c-1 (`feat/health-1c1`, PR #11). This branch (`feat/design-1c2`) is stacked on it. **When PR #11 merges, retarget this PR to main before deleting `feat/health-1c1`** (deleting a base branch closes stacked PRs).

## Global Constraints

- Fit = coverage x compliance; a style is proposed when fit >= 0.5, else "no clear design" plus the closest style and its migration moves.
- New checks (maintainability, experimental): `layer-direction` major 30 min, `part-independence` major 30 min, `public-entry` minor 10 min, `core-purity` major 60 min; threshold 0 (any occurrence).
- Every design violation cites the import line (or, for purity, the line that proves the I/O use).
- The target is the accepted design.yaml when present, else the inferred proposal, labelled in every output.
- `svarupa design` never blocks when stdin is not a terminal: it prints the proposal and writes nothing unless `--accept` is given.
- Deterministic; no change to outputs for repos without units (they get a `design` key with no units).
- Commits SSH-signed (repo config already set), branch `feat/design-1c2`, PR at the end, never push to main, no AI attribution, plain English without em dashes.

## Review Focus

1. A repository whose directories match no style names at all: every unit is "no clear design", nothing crashes, coverage 0. Pinned in Task 1 `test_unmatched_unit_has_no_clear_design`.
2. A module whose name matches two parts (for example `models` in both MVC and layered): it gets exactly one part per style, and the reason says which rule chose it. Pinned in Task 1 `test_each_module_gets_one_part_with_a_reason`.
3. A design.yaml that names a module or style that no longer exists: a clear message, the unit falls back to inferred, no crash. Pinned in Task 2 `test_stale_design_file_falls_back_with_a_note`.
4. `svarupa design` in CI (no terminal): prints, writes nothing, exit 0. Pinned in Task 2 `test_design_without_a_terminal_only_prints`.
5. An excepted violation: listed as accepted, not counted in debt or conformance. Pinned in Task 3 `test_exceptions_are_listed_not_counted`.

---

## File Structure

| File | Responsibility |
|---|---|
| `svarupa/design/__init__.py` | `design_for(scan, graph, accepted)`, public types |
| `svarupa/design/model.py` | `Part`, `Style`, `Unit`, `Assignment`, `DesignViolation`, `Fit`, `UnitDesign`, `Design` |
| `svarupa/design/styles.py` | `CATALOG` of styles as data, `BY_ID` |
| `svarupa/design/units.py` | units from manifests, unit levels |
| `svarupa/design/match.py` | part assignment, rule checks, fit |
| `svarupa/design/classify.py` | system-level labels with evidence |
| `svarupa/design/file.py` | design.yaml load and dump, `DesignFile`, `DesignException` |
| `svarupa/health/catalog.py` | four new checks |
| `svarupa/health/__init__.py` | `assess(graph, texts, design=None)` |
| `svarupa/cli.py` | `svarupa design`; design in `_scan` |
| `svarupa/emit/*`, `svarupa/query/*` | `design` key, Design section, `get_design_rules` |
| `docs/design.md` | the catalog for users |
| `tests/test_design.py`, `tests/test_design_cli.py`, `tests/test_design_outputs.py` | tests |

---

### Task 1: Styles, units, matching and fit

**Files:** create `svarupa/design/{__init__,model,styles,units,match,classify}.py`, `tests/test_design.py`.

**Interfaces (produced):**
- `Part(id: str, names: frozenset[str], evidence: frozenset[str] = frozenset(), slices: bool = False)`
- `Style(id, name, level, parts, order=(), independent=(), public_entry=(), pure=(), forbidden=(), source="", maturity="experimental")`
- `Unit(id: str, level: str, manifest: str, modules: tuple[str, ...])` (`id` is the unit directory, `""` for the root)
- `Assignment(module: str, part: str, slice: str | None, reason: str)`
- `DesignViolation(rule: str, src: str, dst: str, evidence: Evidence, message: str)` (`src`/`dst` are modules)
- `Fit(unit: str, style: str, coverage: float, compliance: float, assignments: tuple[Assignment, ...], violations: tuple[DesignViolation, ...])` with `.fit`
- `UnitDesign(unit: Unit, chosen: Fit | None, source: str, runners_up: tuple[Fit, ...], note: str = "")` (`source` in `accepted`, `inferred`, `none`)
- `Design(system: tuple[str, ...], system_evidence: tuple[str, ...], units: tuple[UnitDesign, ...])` with `.to_json()`
- `find_units(scan, graph) -> list[Unit]`; `signals(graph) -> dict[str, frozenset[str]]`; `fit_style(unit, style, graph, sig) -> Fit`; `fit_assigned(unit, style, assignments, graph, sig) -> Fit`; `classify_system(scan, graph, units) -> tuple[tuple[str, ...], tuple[str, ...]]`; `design_for(scan, graph, accepted=None) -> Design`.

- [ ] **Step 1: Failing tests**

`tests/test_design.py`:

```python
"""Design styles: units, part assignment, fit and the proposal."""

from __future__ import annotations

from pathlib import Path

from svarupa.build import build
from svarupa.detect import detect
from svarupa.design import BY_ID, CATALOG, design_for
from svarupa.extract import declared_dependencies, extract


def write(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf8")


def run(root: Path):
    scan = detect(root)
    graph = build(scan, extract(scan, declared_dependencies(scan)), strict=False)
    return design_for(scan, graph)


def layered(root: Path, back_edge: bool = False) -> None:
    write(root, "pyproject.toml", '[project]\nname = "shop"\ndependencies = ["fastapi"]\n')
    write(
        root,
        "shop/api/routes.py",
        "from fastapi import APIRouter\nfrom shop.services.orders import place\n\n"
        'router = APIRouter()\n\n\n@router.post("/orders")\ndef create():\n    return place()\n',
    )
    write(
        root,
        "shop/services/orders.py",
        "from shop.repository.store import save\n\n\ndef place():\n    return save()\n",
    )
    store = "def save():\n    return 1\n"
    if back_edge:
        store = "from shop.api.routes import create\n\n\n" + store
    write(root, "shop/repository/store.py", store)


def test_catalog_covers_every_level() -> None:
    levels = {s.level for s in CATALOG}
    assert {"backend", "frontend", "mobile", "data", "library"} <= levels
    assert {"layered", "mvc", "hexagonal", "onion", "clean", "vertical-slice", "cqrs"} <= set(BY_ID)
    assert {"feature-sliced", "frontend-layers", "mvvm", "mvi", "mobile-clean"} <= set(BY_ID)
    assert {"pipeline-stages", "dbt-layers", "medallion", "public-api", "plugin"} <= set(BY_ID)
    assert all(s.maturity == "experimental" and s.source for s in CATALOG)


def test_a_layered_service_is_proposed_as_layered(tmp_path: Path) -> None:
    layered(tmp_path)
    unit = run(tmp_path).units[0]
    assert (unit.unit.id, unit.unit.level, unit.source) == ("", "backend", "inferred")
    assert unit.chosen is not None and unit.chosen.style == "layered"
    assert unit.chosen.coverage == 1.0 and unit.chosen.compliance == 1.0
    assert unit.chosen.violations == ()


def test_each_module_gets_one_part_with_a_reason(tmp_path: Path) -> None:
    layered(tmp_path)
    chosen = run(tmp_path).units[0].chosen
    assert chosen is not None
    parts = {a.module: (a.part, a.reason) for a in chosen.assignments}
    assert parts["shop/api"][0] == "api"
    assert "routes" in parts["shop/api"][1] or "api" in parts["shop/api"][1]
    assert parts["shop/services"][0] == "service"
    assert parts["shop/repository"][0] == "data"
    assert len({a.module for a in chosen.assignments}) == len(chosen.assignments)


def test_an_upward_import_is_a_layer_direction_violation(tmp_path: Path) -> None:
    layered(tmp_path, back_edge=True)
    unit = run(tmp_path).units[0]
    assert unit.chosen is not None and unit.chosen.style == "layered"
    rules = [(v.rule, v.src, v.dst, v.evidence.file, v.evidence.start_line) for v in unit.chosen.violations]
    assert rules == [("layer-direction", "shop/repository", "shop/api", "shop/repository/store.py", 1)]
    assert unit.chosen.compliance < 1.0


def test_unmatched_unit_has_no_clear_design(tmp_path: Path) -> None:
    write(tmp_path, "pyproject.toml", '[project]\nname = "x"\n')
    write(tmp_path, "alpha/one.py", "from beta.two import f\n")
    write(tmp_path, "beta/two.py", "def f():\n    return 1\n")
    unit = run(tmp_path).units[0]
    assert unit.chosen is None and unit.source == "none"
    assert "no clear design" in unit.note


def test_units_follow_manifests(tmp_path: Path) -> None:
    layered(tmp_path / "backend")
    write(tmp_path, "web/package.json", '{"name": "web", "dependencies": {"react": "18"}}\n')
    write(tmp_path, "web/src/pages/home.tsx", "export const Home = () => <div/>;\n")
    design = run(tmp_path)
    assert [(u.unit.id, u.unit.level) for u in design.units] == [
        ("backend", "backend"),
        ("web", "frontend"),
    ]


def test_feature_slices_must_not_import_each_other(tmp_path: Path) -> None:
    write(tmp_path, "package.json", '{"name": "app", "dependencies": {"react": "18"}}\n')
    write(tmp_path, "src/app/main.tsx", "import { A } from '../features/a';\nexport const M = () => <A/>;\n")
    write(tmp_path, "src/features/a/index.ts", "import { b } from '../b/model/x';\nexport const A = () => b;\n")
    write(tmp_path, "src/features/b/index.ts", "export const B = 1;\n")
    write(tmp_path, "src/features/b/model/x.ts", "export const b = 2;\n")
    write(tmp_path, "src/shared/ui.ts", "export const ui = 1;\n")
    unit = run(tmp_path).units[0]
    fit = next(f for f in (unit.chosen, *unit.runners_up) if f and f.style == "feature-sliced")
    assert {v.rule for v in fit.violations} >= {"part-independence", "public-entry"}


def test_core_purity(tmp_path: Path) -> None:
    write(tmp_path, "pyproject.toml", '[project]\nname = "x"\ndependencies = ["sqlalchemy", "fastapi"]\n')
    write(tmp_path, "app/adapters/http.py", "from app.domain.order import Order\n\n\ndef h():\n    return Order()\n")
    write(tmp_path, "app/domain/order.py", "import sqlalchemy\n\n\nclass Order:\n    pass\n")
    write(tmp_path, "app/ports/repo.py", "from app.domain.order import Order\n")
    design = run(tmp_path)
    fits = [f for u in design.units for f in (u.chosen, *u.runners_up) if f and f.style == "hexagonal"]
    assert fits and [v.rule for v in fits[0].violations if v.rule == "core-purity"] == ["core-purity"]
    v = next(v for v in fits[0].violations if v.rule == "core-purity")
    assert (v.evidence.file, v.evidence.start_line) == ("app/domain/order.py", 1)


def test_system_level_is_labelled_with_evidence(tmp_path: Path) -> None:
    layered(tmp_path / "orders")
    layered(tmp_path / "billing")
    write(
        tmp_path,
        "docker-compose.yml",
        "services:\n  orders:\n    build: ./orders\n  billing:\n    build: ./billing\n"
        "  queue:\n    image: rabbitmq:3\n",
    )
    design = run(tmp_path)
    assert "microservices" in design.system
    assert "event-driven" in design.system
    assert design.system_evidence


def test_design_json_is_deterministic(tmp_path: Path) -> None:
    layered(tmp_path, back_edge=True)
    assert run(tmp_path).to_json() == run(tmp_path).to_json()
```

Run: `uv run pytest tests/test_design.py -q`
Expected: `ModuleNotFoundError: No module named 'svarupa.design'`.

- [ ] **Step 2: Model**

`svarupa/design/model.py`:

```python
"""Design styles and what detection found."""

from __future__ import annotations

from dataclasses import dataclass

from svarupa.model import Evidence

__all__ = [
    "Assignment",
    "Design",
    "DesignViolation",
    "Fit",
    "Part",
    "Style",
    "Unit",
    "UnitDesign",
]


@dataclass(frozen=True, slots=True)
class Part:
    id: str
    names: frozenset[str]  # directory names, lowercase
    evidence: frozenset[str] = frozenset()  # routes, datastore, messagebus, cloud, ui
    slices: bool = False  # each directory under a matching name is its own slice


@dataclass(frozen=True, slots=True)
class Style:
    id: str
    name: str
    level: str  # backend | frontend | mobile | data | library
    parts: tuple[Part, ...]
    order: tuple[str, ...] = ()  # top to bottom; dependencies point down
    independent: tuple[str, ...] = ()  # parts whose slices must not import each other
    public_entry: tuple[str, ...] = ()  # parts imported only through a slice's root
    pure: tuple[str, ...] = ()  # parts that must not use frameworks or I/O
    forbidden: tuple[tuple[str, str], ...] = ()  # (from part, to part) never allowed
    source: str = ""
    maturity: str = "experimental"


@dataclass(frozen=True, slots=True)
class Unit:
    id: str  # directory of the manifest, "" for the repository root
    level: str
    manifest: str  # the manifest file, "" when none
    modules: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Assignment:
    module: str
    part: str
    slice: str | None
    reason: str


@dataclass(frozen=True, slots=True)
class DesignViolation:
    rule: str  # layer-direction | part-independence | public-entry | core-purity
    src: str  # module
    dst: str  # module ("" for core-purity)
    evidence: Evidence
    message: str


@dataclass(frozen=True, slots=True)
class Fit:
    unit: str
    style: str
    coverage: float
    compliance: float
    assignments: tuple[Assignment, ...]
    violations: tuple[DesignViolation, ...]

    @property
    def fit(self) -> float:
        return self.coverage * self.compliance

    def to_json(self) -> dict[str, object]:
        return {
            "style": self.style,
            "fit": round(self.fit, 4),
            "coverage": round(self.coverage, 4),
            "compliance": round(self.compliance, 4),
            "parts": [
                {"module": a.module, "part": a.part, "slice": a.slice, "reason": a.reason}
                for a in self.assignments
            ],
            "violations": [
                {
                    "rule": v.rule,
                    "from": v.src,
                    "to": v.dst,
                    "file": v.evidence.file,
                    "line": v.evidence.start_line,
                    "message": v.message,
                }
                for v in self.violations
            ],
        }


@dataclass(frozen=True, slots=True)
class UnitDesign:
    unit: Unit
    chosen: Fit | None
    source: str  # accepted | inferred | none
    runners_up: tuple[Fit, ...]
    note: str = ""


@dataclass(frozen=True, slots=True)
class Design:
    system: tuple[str, ...]
    system_evidence: tuple[str, ...]
    units: tuple[UnitDesign, ...]

    def to_json(self) -> dict[str, object]:
        return {
            "system": list(self.system),
            "system_evidence": list(self.system_evidence),
            "units": [
                {
                    "unit": u.unit.id or ".",
                    "level": u.unit.level,
                    "manifest": u.unit.manifest,
                    "source": u.source,
                    "note": u.note,
                    "chosen": u.chosen.to_json() if u.chosen else None,
                    "runners_up": [
                        {"style": f.style, "fit": round(f.fit, 4)} for f in u.runners_up
                    ],
                }
                for u in self.units
            ],
        }
```

- [ ] **Step 3: The style catalog**

`svarupa/design/styles.py`:

```python
"""The design-style catalog: each style written in five rule types.

Parts are matched by directory name and by evidence Svarupa proves.
`order` lists parts top to bottom: a dependency may point down or stay in
its part, never up. Sources name where each style is defined.
"""

from __future__ import annotations

from svarupa.design.model import Part, Style

__all__ = ["BY_ID", "CATALOG"]


def _p(id_: str, *names: str, evidence: tuple[str, ...] = (), slices: bool = False) -> Part:
    return Part(id_, frozenset(names), frozenset(evidence), slices)


API = _p(
    "api", "api", "apis", "routes", "router", "routers", "controllers", "controller",
    "handlers", "handler", "endpoints", "views", "rest", "http", "web", "resources",
    "delivery", "transport", "presentation", evidence=("routes",),
)  # fmt: skip
SERVICE = _p(
    "service", "services", "service", "usecase", "usecases", "use_cases", "logic",
    "business", "managers", "application",
)  # fmt: skip
DATA = _p(
    "data", "repository", "repositories", "repo", "repos", "dao", "daos", "db",
    "database", "models", "model", "persistence", "store", "stores", "storage", "crud",
    "mapper", "mappers", evidence=("datastore",),
)  # fmt: skip

CATALOG: tuple[Style, ...] = (
    Style(
        "layered", "Layered (N-tier)", "backend", (API, SERVICE, DATA),
        order=("api", "service", "data"),
        source="Buschmann et al., Pattern-Oriented Software Architecture: Layers",
    ),
    Style(
        "mvc", "Model-View-Controller", "backend",
        (
            _p("controller", "controllers", "controller", "routes", "handlers", evidence=("routes",)),
            _p("view", "views", "view", "templates", "ui"),
            _p("model", "models", "model", "entities", "entity", evidence=("datastore",)),
        ),
        order=("controller", "view", "model"),
        source="Reenskaug 1979; Krasner and Pope 1988",
    ),
    Style(
        "hexagonal", "Hexagonal (ports and adapters)", "backend",
        (
            _p("adapters", "adapters", "adapter", "infrastructure", "infra", "api", "http",
               "web", "rest", "controllers", "handlers", "persistence", "repository",
               "repositories", "db", evidence=("routes", "datastore", "messagebus", "cloud")),
            _p("ports", "ports", "port", "interfaces"),
            _p("core", "domain", "core", "application", "usecase", "usecases", "services",
               "service", "entities"),
        ),
        order=("adapters", "ports", "core"), pure=("core",),
        source="Cockburn 2005, Hexagonal Architecture",
    ),
    Style(
        "onion", "Onion", "backend",
        (
            _p("infrastructure", "infrastructure", "infra", "persistence", "db", "repository",
               "repositories", "web", "api", "controllers", evidence=("routes", "datastore")),
            _p("application", "application", "services", "service", "usecase", "usecases"),
            _p("domain", "domain", "entities", "entity", "core"),
        ),
        order=("infrastructure", "application", "domain"), pure=("domain",),
        source="Palermo 2008, The Onion Architecture",
    ),
    Style(
        "clean", "Clean Architecture", "backend",
        (
            _p("adapters", "delivery", "handlers", "handler", "controllers", "controller",
               "http", "rest", "web", "api", "router", "presenter", "presenters", "gateway",
               "gateways", "repository", "repositories", "persistence", "infrastructure",
               "infra", "db", "mysql", "postgres", evidence=("routes", "datastore")),
            _p("usecases", "usecase", "usecases", "use_cases", "interactor", "interactors",
               "service", "services", "application"),
            _p("entities", "entity", "entities", "domain"),
        ),
        order=("adapters", "usecases", "entities"), pure=("usecases", "entities"),
        source="Martin 2012, The Clean Architecture",
    ),
    Style(
        "vertical-slice", "Vertical Slice", "backend",
        (
            _p("features", "features", "feature", "slices", "modules", slices=True),
            _p("shared", "shared", "common", "lib", "utils", "core", "infrastructure"),
        ),
        order=("features", "shared"), independent=("features",),
        source="Bogard 2018, Vertical Slice Architecture",
    ),
    Style(
        "cqrs", "CQRS", "backend",
        (
            _p("commands", "commands", "command", "write", "writes"),
            _p("queries", "queries", "query", "read", "reads", "readservice"),
            _p("domain", "domain", "model", "models", "entities"),
        ),
        order=("commands", "queries", "domain"),
        forbidden=(("commands", "queries"),),
        source="Young 2010, CQRS Documents",
    ),
    Style(
        "feature-sliced", "Feature-Sliced Design", "frontend",
        (
            _p("app", "app"),
            _p("processes", "processes", slices=True),
            _p("pages", "pages", slices=True),
            _p("widgets", "widgets", slices=True),
            _p("features", "features", slices=True),
            _p("entities", "entities", slices=True),
            _p("shared", "shared"),
        ),
        order=("app", "processes", "pages", "widgets", "features", "entities", "shared"),
        independent=("processes", "pages", "widgets", "features", "entities"),
        public_entry=("processes", "pages", "widgets", "features", "entities"),
        source="Feature-Sliced Design v2.1 (feature-sliced.design)",
    ),
    Style(
        "frontend-layers", "Pages, components, state, API client", "frontend",
        (
            _p("pages", "pages", "app", "routes", "screens", "views"),
            _p("components", "components", "ui", "widgets", "layouts"),
            _p("state", "store", "stores", "state", "hooks", "context", "contexts", "redux"),
            _p("api", "api", "services", "client", "clients"),
            _p("shared", "utils", "lib", "helpers", "config", "types", "constants"),
        ),
        order=("pages", "components", "state", "api", "shared"),
        source="React and Next.js project structure guidance",
    ),
    Style(
        "mvvm", "Model-View-ViewModel", "frontend",
        (
            _p("view", "views", "view", "screens", "pages", "components", "ui",
               "activities", "fragments"),
            _p("viewmodel", "viewmodels", "viewmodel", "vm", "vms"),
            _p("model", "models", "model", "repository", "repositories", "data", "services"),
        ),
        order=("view", "viewmodel", "model"),
        source="Gossman 2005, Introduction to Model/View/ViewModel",
    ),
    Style(
        "mvi", "Model-View-Intent", "mobile",
        (
            _p("view", "view", "views", "ui", "screens"),
            _p("intent", "intent", "intents", "actions"),
            _p("state", "state", "store", "reducer", "reducers", "model", "models"),
            _p("data", "data", "repository", "repositories"),
        ),
        order=("view", "intent", "state", "data"),
        source="Staltz, Model-View-Intent (Cycle.js); Android MVI guidance",
    ),
    Style(
        "mobile-clean", "Clean (mobile)", "mobile",
        (
            _p("presentation", "presentation", "ui", "view", "views", "screens"),
            _p("data", "data", "repository", "repositories", "remote", "local"),
            _p("domain", "domain", "usecase", "usecases"),
        ),
        order=("presentation", "data", "domain"),
        forbidden=(("presentation", "data"),),
        pure=("domain",),
        source="Android app architecture guide (UI, data and domain layers)",
    ),
    Style(
        "pipeline-stages", "Pipeline stages", "data",
        (
            _p("orchestration", "dags", "pipelines", "flows", "jobs", "orchestration"),
            _p("load", "load", "loaders", "sinks", "export", "outputs"),
            _p("transform", "transform", "transforms", "transformations", "processing"),
            _p("extract", "extract", "extractors", "ingest", "ingestion", "sources"),
        ),
        order=("orchestration", "load", "transform", "extract"),
        source="ETL pipeline pattern (extract, transform, load)",
    ),
    Style(
        "dbt-layers", "dbt layers", "data",
        (
            _p("marts", "marts", "mart"),
            _p("intermediate", "intermediate", "int"),
            _p("staging", "staging", "stg"),
        ),
        order=("marts", "intermediate", "staging"),
        source="dbt Labs, How we structure our dbt projects",
    ),
    Style(
        "medallion", "Medallion", "data",
        (
            _p("gold", "gold", "curated", "serving"),
            _p("silver", "silver", "clean", "cleansed", "conformed"),
            _p("bronze", "bronze", "raw", "landing"),
        ),
        order=("gold", "silver", "bronze"),
        source="Databricks, Medallion architecture",
    ),
    Style(
        "public-api", "Public API over internals", "library",
        (
            _p("public", "api", "public"),
            _p("internal", "internal", "_internal", "internals", "impl", "private"),
        ),
        order=("public", "internal"),
        source="Go internal packages; Python private-module convention",
    ),
    Style(
        "plugin", "Plugin (microkernel)", "library",
        (
            _p("plugins", "plugins", "plugin", "extensions", "ext", "addons", slices=True),
            _p("core", "core", "kernel", "host", "engine"),
        ),
        order=("plugins", "core"), independent=("plugins",),
        source="Richards 2015, Software Architecture Patterns: Microkernel",
    ),
)  # fmt: skip

BY_ID = {s.id: s for s in CATALOG}
```

(If ruff format reflows the catalog despite `# fmt: skip`, wrap the definitions in `# fmt: off` / `# fmt: on`.)

- [ ] **Step 4: Units, signals and matching**

`svarupa/design/units.py`:

```python
"""App units: one per directory holding an app manifest."""

from __future__ import annotations

from pathlib import Path

from svarupa.build import Graph
from svarupa.detect import Scan
from svarupa.extract.base import module_of
from svarupa.design.model import Unit

__all__ = ["find_units", "signals"]

_MANIFESTS = frozenset(
    {"pyproject.toml", "requirements.txt", "package.json", "go.mod", "pom.xml",
     "build.gradle", "build.gradle.kts"}
)  # fmt: skip
_DATA_DIRS = frozenset({"dags", "pipelines", "etl", "marts", "staging", "bronze", "silver", "gold"})


def signals(graph: Graph) -> dict[str, frozenset[str]]:
    """Evidence per module: routes, datastore, messagebus, cloud, ui."""
    out: dict[str, set[str]] = {}
    for r in graph.routes:
        out.setdefault(module_of(r.file), set()).add("routes")
    names = {"database": "datastore", "messagebus": "messagebus", "cloud": "cloud", "frontend": "ui"}
    for x in graph.externals:
        if x.category in names:
            out.setdefault(module_of(x.file), set()).add(names[x.category])
    return {m: frozenset(s) for m, s in out.items()}


def _level(modules: tuple[str, ...], files: list[str], sig: dict[str, frozenset[str]]) -> str:
    found = set().union(*(sig.get(m, frozenset()) for m in modules)) if modules else set()
    if "ui" in found or any(f.endswith((".tsx", ".jsx")) for f in files):
        return "frontend"
    if found & {"routes", "datastore", "messagebus"}:
        return "backend"
    if any(part in _DATA_DIRS for m in modules for part in m.split("/")):
        return "data"
    if any(Path(f).name == "AndroidManifest.xml" for f in files):
        return "mobile"
    return "library"


def find_units(scan: Scan, graph: Graph) -> list[Unit]:
    manifests: dict[str, str] = {}
    for rec in scan.files:
        name = Path(rec.path).name
        if name in _MANIFESTS and rec.role.value == "config":
            parent = str(Path(rec.path).parent)
            manifests.setdefault("" if parent == "." else parent, rec.path)
    dirs = sorted(manifests, key=lambda d: (-d.count("/"), -len(d), d))

    def owner(path: str) -> str:
        for d in dirs:
            if not d or path == d or path.startswith(d + "/"):
                return d
        return ""

    members: dict[str, list[str]] = {}
    for module in sorted(graph.modules):
        members.setdefault(owner(module), []).append(module)
    files: dict[str, list[str]] = {}
    for rec in scan.files:
        files.setdefault(owner(rec.path), []).append(rec.path)
    sig = signals(graph)
    return [
        Unit(d, _level(tuple(members[d]), files.get(d, []), sig), manifests.get(d, ""), tuple(members[d]))
        for d in sorted(members)
        if members[d]
    ]
```

`svarupa/design/match.py`:

```python
"""Assign modules to a style's parts, check the rules, score the fit."""

from __future__ import annotations

from svarupa.build import Graph
from svarupa.extract.base import module_of
from svarupa.design.model import Assignment, DesignViolation, Fit, Style, Unit
from svarupa.model import EdgeKind, Evidence

__all__ = ["assign", "fit_assigned", "fit_style"]

_IO = frozenset({"routes", "datastore", "messagebus", "cloud", "ui"})


def _segments(module: str, unit: str) -> list[str]:
    rel = module[len(unit) :].strip("/") if unit else module
    return [s.lower() for s in rel.split("/") if s]


def assign(unit: Unit, style: Style, sig: dict[str, frozenset[str]]) -> list[Assignment]:
    out: list[Assignment] = []
    for module in unit.modules:
        segs = _segments(module, unit.id)
        best: tuple[int, int, Assignment] | None = None
        for rank, part in enumerate(style.parts):
            score, reason, at = 0, "", -1
            if segs and segs[-1] in part.names:
                score, reason, at = 3, f"directory '{segs[-1]}' names the {part.id} part", len(segs) - 1
            else:
                hits = [i for i, s in enumerate(segs) if s in part.names]
                if hits:
                    at = hits[-1]
                    score, reason = 2, f"inside '{segs[at]}', the {part.id} part"
            shown = sig.get(module, frozenset()) & part.evidence
            if shown and score < 2:
                score, reason = 2, f"proves {', '.join(sorted(shown))}, which the {part.id} part does"
            if score and (best is None or (score, -rank) > (best[0], best[1])):
                slice_ = segs[at + 1] if part.slices and 0 <= at < len(segs) - 1 else None
                best = (score, -rank, Assignment(module, part.id, slice_, reason))
        if best is not None:
            out.append(best[2])
    return out


def _import_evidence(graph: Graph) -> dict[tuple[str, str], Evidence]:
    first: dict[tuple[str, str], Evidence] = {}
    for e in graph.edges:
        if e.kind is not EdgeKind.IMPORTS or not e.evidence:
            continue
        key = (module_of(e.src.split("#", 1)[0]), module_of(e.dst.split("#", 1)[0]))
        ev = e.evidence[0]
        if key not in first or (ev.file, ev.start_line) < (first[key].file, first[key].start_line):
            first[key] = ev
    return first


def _purity_evidence(graph: Graph, module: str) -> Evidence | None:
    found = [r.evidence for r in graph.routes if module_of(r.file) == module]
    found += [x.evidence for x in graph.externals if module_of(x.file) == module and x.category in ("database", "messagebus", "cloud", "frontend")]
    return min(found, key=lambda ev: (ev.file, ev.start_line)) if found else None


def fit_assigned(
    unit: Unit,
    style: Style,
    assignments: list[Assignment],
    graph: Graph,
    sig: dict[str, frozenset[str]],
) -> Fit:
    by_module = {a.module: a for a in assignments}
    order = {p: i for i, p in enumerate(style.order)}
    evidence = _import_evidence(graph)
    deps = sorted(
        (a, b) for a, b in graph.module_deps if a in by_module and b in by_module and a != b
    )
    violations: list[DesignViolation] = []
    checked = 0
    for a, b in deps:
        src, dst = by_module[a], by_module[b]
        ev = evidence.get((a, b))
        if ev is None:
            continue
        checked += 1
        rule, why = None, ""
        if (src.part, dst.part) in style.forbidden or (
            src.part in order and dst.part in order and order[dst.part] < order[src.part]
        ):
            rule, why = "layer-direction", f"the {src.part} part may not depend on the {dst.part} part"
        elif (
            src.part == dst.part
            and src.part in style.independent
            and src.slice and dst.slice and src.slice != dst.slice
        ):
            rule, why = "part-independence", f"{src.part} slices '{src.slice}' and '{dst.slice}' must not import each other"
        if rule is None and dst.part in style.public_entry and dst.slice and (
            src.part != dst.part or src.slice != dst.slice
        ):
            root = _slice_root(b, dst.slice)
            if b != root:
                rule, why = "public-entry", f"import {dst.part} '{dst.slice}' through its root {root}, not {b}"
        if rule is not None:
            violations.append(DesignViolation(rule, a, b, ev, why))
    for a in sorted(by_module):
        if by_module[a].part in style.pure:
            checked += 1
            if sig.get(a, frozenset()) & _IO:
                ev = _purity_evidence(graph, a)
                if ev is not None:
                    violations.append(
                        DesignViolation("core-purity", a, "", ev, f"the {by_module[a].part} part must not use frameworks or I/O")
                    )
    bad = len(violations)
    coverage = len(by_module) / len(unit.modules) if unit.modules else 0.0
    compliance = (checked - bad) / checked if checked else 1.0
    return Fit(unit.id, style.id, coverage, compliance, tuple(sorted(assignments, key=lambda x: x.module)), tuple(sorted(violations, key=lambda v: (v.rule, v.evidence.file, v.evidence.start_line))))


def _slice_root(module: str, slice_: str) -> str:
    parts = module.split("/")
    return "/".join(parts[: parts.index(slice_) + 1]) if slice_ in parts else module


def fit_style(unit: Unit, style: Style, graph: Graph, sig: dict[str, frozenset[str]]) -> Fit:
    return fit_assigned(unit, style, assign(unit, style, sig), graph, sig)
```

`svarupa/design/classify.py`:

```python
"""System-level styles, labelled from units, compose services and queues."""

from __future__ import annotations

from pathlib import Path

from svarupa.build import Graph
from svarupa.detect import Scan
from svarupa.design.model import Unit
from svarupa.model import NodeKind

__all__ = ["classify_system"]


def classify_system(scan: Scan, graph: Graph, units: list[Unit]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    backends = [u for u in units if u.level == "backend"]
    frontends = [u for u in units if u.level == "frontend"]
    services = sorted(n.label for n in graph.nodes.values() if n.kind is NodeKind.SERVICE)
    queues = sorted(n.label for n in graph.nodes.values() if n.kind is NodeKind.QUEUE)
    buses = sorted({x.file for x in graph.externals if x.category == "messagebus"})
    files = {Path(r.path).name for r in scan.files}
    styles: list[str] = []
    why: list[str] = []
    if len(backends) >= 2 and len(services) >= 2:
        styles.append("microservices")
        why.append(f"{len(backends)} backend units and compose services {', '.join(services)}")
    if queues or len(buses) >= 2:
        styles.append("event-driven")
        why.append("message queues " + ", ".join(queues) if queues else f"message bus clients in {len(buses)} files")
    if files & {"serverless.yml", "serverless.yaml"}:
        styles.append("serverless")
        why.append("serverless.yml declares functions")
    if len(frontends) >= 2:
        styles.append("micro-frontends")
        why.append(f"{len(frontends)} frontend units")
    if frontends and len(backends) >= 2 and len(frontends) >= len(backends) - 1:
        styles.append("bff")
        why.append(f"{len(frontends)} frontend units served by {len(backends)} backend units")
    if not styles:
        deployable = backends or units
        if len(deployable) == 1 and len({m.split('/')[len(deployable[0].id.split('/')) if deployable[0].id else 0] for m in deployable[0].modules if m}) >= 3:
            styles.append("modular-monolith")
            why.append(f"one deployable unit with {len(deployable[0].modules)} modules")
        else:
            styles.append("monolith")
            why.append(f"{len(units)} unit(s), one deployable")
    return tuple(styles), tuple(why)
```

(If the modular-monolith expression is hard to read after formatting, extract `_top_level_dirs(unit)` returning the set of first path segments under the unit; behavior must stay the same.)

`svarupa/design/__init__.py`:

```python
"""Design styles: which industry style each unit follows, and where it breaks.

`design_for` proposes the best-fitting style per unit (fit = coverage x
compliance, proposed at 0.5 or more), or the accepted one from
`.svarupa/design.yaml`. Its violations become health checks.
"""

from __future__ import annotations

from svarupa.build import Graph
from svarupa.detect import Scan
from svarupa.design.classify import classify_system
from svarupa.design.match import assign, fit_assigned, fit_style
from svarupa.design.model import Design, Fit, Style, Unit, UnitDesign
from svarupa.design.styles import BY_ID, CATALOG
from svarupa.design.units import find_units, signals

__all__ = ["BY_ID", "CATALOG", "MIN_FIT", "Design", "Fit", "Style", "Unit", "UnitDesign", "design_for"]

MIN_FIT = 0.5


def design_for(scan: Scan, graph: Graph, accepted: object | None = None) -> Design:
    units = find_units(scan, graph)
    sig = signals(graph)
    chosen_by_file = _accepted_units(accepted)
    out: list[UnitDesign] = []
    for unit in units:
        candidates = [s for s in CATALOG if s.level == unit.level]
        fits = sorted(
            (fit_style(unit, s, graph, sig) for s in candidates),
            key=lambda f: (-f.fit, f.style),
        )
        key = unit.id or "."
        if key in chosen_by_file:
            accepted_fit, note = _apply_accepted(unit, chosen_by_file[key], graph, sig)
            if accepted_fit is not None:
                rest = tuple(f for f in fits if f.style != accepted_fit.style)[:3]
                out.append(UnitDesign(unit, accepted_fit, "accepted", rest, note))
                continue
            best = fits[0] if fits else None
            if best is not None and best.fit >= MIN_FIT:
                out.append(UnitDesign(unit, best, "inferred", tuple(fits[1:4]), note))
            else:
                out.append(UnitDesign(unit, None, "none", tuple(fits[:3]), note))
            continue
        if fits and fits[0].fit >= MIN_FIT:
            out.append(UnitDesign(unit, fits[0], "inferred", tuple(fits[1:4])))
        else:
            closest = fits[0].style if fits else "none"
            out.append(
                UnitDesign(
                    unit,
                    None,
                    "none",
                    tuple(fits[:3]),
                    f"no clear design; closest is {closest}",
                )
            )
    system, why = classify_system(scan, graph, units)
    return Design(system, why, tuple(out))


def _accepted_units(accepted: object | None) -> dict[str, tuple[str, dict[str, list[str]]]]:
    from svarupa.design.file import DesignFile

    if not isinstance(accepted, DesignFile):
        return {}
    return {u: (spec.style, spec.parts) for u, spec in accepted.units.items()}


def _apply_accepted(
    unit: Unit,
    spec: tuple[str, dict[str, list[str]]],
    graph: Graph,
    sig: dict[str, frozenset[str]],
) -> tuple[Fit | None, str]:
    from svarupa.design.model import Assignment

    style_id, parts = spec
    style = BY_ID.get(style_id)
    if style is None:
        return None, f"design.yaml names unknown style '{style_id}'; using the inferred design"
    known = {p.id for p in style.parts}
    listed = {m: p for p, ms in parts.items() if p in known for m in ms}
    missing = sorted(m for m in listed if m not in unit.modules)
    assignments = [
        Assignment(m, p, None, "accepted in design.yaml") for m, p in sorted(listed.items()) if m in unit.modules
    ]
    if not parts:
        assignments = assign(unit, style, sig)
    note = f"design.yaml lists modules that no longer exist: {', '.join(missing)}" if missing else ""
    return fit_assigned(unit, style, assignments, graph, sig), note
```

(`design_for` refers to `svarupa.design.file.DesignFile`, written in Task 2. Until then `_accepted_units` imports it lazily; to keep Task 1 self-contained, create `svarupa/design/file.py` in this task with just the two dataclasses from Task 2 Step 2, and fill in load/dump in Task 2.)

- [ ] **Step 5: Run and fix**

Run: `uv run pytest tests/test_design.py -q`
Expected: pass. The likely adjustments are in name lists and the frontend fixture; change data (styles) or the matcher, not the tests, unless the test contradicts the spec, then ledger a ruling.

- [ ] **Step 6: Commit**

Run: `uv run ruff check svarupa tests && uv run ruff format --check svarupa tests && uv run pyright svarupa && uv run pytest -q --deselect tests/test_diagnostics.py::test_every_package_module_is_reachable_by_import`
Expected: pass (the orphan check passes after Task 3 wires `svarupa.design`).

```bash
git add docs/superpowers svarupa/design tests/test_design.py
git commit -m "Detect design styles per app unit with fit scores"
```

---

### Task 2: design.yaml and `svarupa design`

**Files:** complete `svarupa/design/file.py`; modify `svarupa/cli.py`; create `tests/test_design_cli.py`.

**Interfaces:** `UnitSpec(style: str, parts: dict[str, list[str]])`; `DesignException(rule: str, src: str, dst: str, reason: str)`; `DesignFile(units: dict[str, UnitSpec], exceptions: list[DesignException])`; `load_design(path: Path) -> tuple[DesignFile | None, str]` (file or None, and a problem message or ""); `dump_design(design: Design, overrides: dict[str, str]) -> str`; `DESIGN_FILE = ".svarupa/design.yaml"`.

- [ ] **Step 1: Failing tests**

`tests/test_design_cli.py`:

```python
"""`svarupa design`: propose, accept, write design.yaml."""

from __future__ import annotations

import io
from pathlib import Path

import yaml

from svarupa.cli import main
from tests.test_design import layered, write


def test_design_without_a_terminal_only_prints(tmp_path: Path, capsys, monkeypatch) -> None:
    layered(tmp_path)
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    assert main(["design", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "layered" in out and "fit" in out
    assert not (tmp_path / ".svarupa" / "design.yaml").exists()


def test_accept_writes_the_design_file(tmp_path: Path) -> None:
    layered(tmp_path)
    assert main(["design", str(tmp_path), "--accept"]) == 0
    data = yaml.safe_load((tmp_path / ".svarupa" / "design.yaml").read_text(encoding="utf8"))
    assert data["units"]["."]["style"] == "layered"
    assert data["units"]["."]["parts"]["api"] == ["shop/api"]
    assert data["exceptions"] == []


def test_style_flag_overrides_the_proposal(tmp_path: Path) -> None:
    layered(tmp_path)
    assert main(["design", str(tmp_path), "--style", ".=clean", "--accept"]) == 0
    data = yaml.safe_load((tmp_path / ".svarupa" / "design.yaml").read_text(encoding="utf8"))
    assert data["units"]["."]["style"] == "clean"


def test_interactive_accept(tmp_path: Path, monkeypatch) -> None:
    layered(tmp_path)

    class Tty(io.StringIO):
        def isatty(self) -> bool:
            return True

    monkeypatch.setattr("sys.stdin", Tty("a\n"))
    assert main(["design", str(tmp_path)]) == 0
    assert (tmp_path / ".svarupa" / "design.yaml").exists()


def test_accepted_design_is_used_by_the_scan(tmp_path: Path) -> None:
    layered(tmp_path, back_edge=True)
    assert main(["design", str(tmp_path), "--accept"]) == 0
    assert main([str(tmp_path)]) == 0
    from svarupa.build import build
    from svarupa.design import design_for
    from svarupa.design.file import load_design
    from svarupa.detect import detect
    from svarupa.extract import declared_dependencies, extract

    scan = detect(tmp_path)
    graph = build(scan, extract(scan, declared_dependencies(scan)), strict=False)
    accepted, problem = load_design(tmp_path / ".svarupa" / "design.yaml")
    assert problem == ""
    unit = design_for(scan, graph, accepted).units[0]
    assert unit.source == "accepted"
    assert [v.rule for v in unit.chosen.violations] == ["layer-direction"]


def test_stale_design_file_falls_back_with_a_note(tmp_path: Path) -> None:
    layered(tmp_path)
    write(
        tmp_path,
        ".svarupa/design.yaml",
        "units:\n  .:\n    style: nonsense\n    parts: {}\nexceptions: []\n",
    )
    from svarupa.build import build
    from svarupa.design import design_for
    from svarupa.design.file import load_design
    from svarupa.detect import detect
    from svarupa.extract import declared_dependencies, extract

    scan = detect(tmp_path)
    graph = build(scan, extract(scan, declared_dependencies(scan)), strict=False)
    accepted, _ = load_design(tmp_path / ".svarupa" / "design.yaml")
    unit = design_for(scan, graph, accepted).units[0]
    assert unit.source == "inferred"
    assert "unknown style 'nonsense'" in unit.note


def test_a_broken_design_file_is_reported(tmp_path: Path) -> None:
    from svarupa.design.file import load_design

    write(tmp_path, "design.yaml", "units: [not, a, mapping\n")
    accepted, problem = load_design(tmp_path / "design.yaml")
    assert accepted is None and "design.yaml" in problem
```

Run: `uv run pytest tests/test_design_cli.py -q`
Expected: failures (`design` is not a command).

- [ ] **Step 2: The file format**

`svarupa/design/file.py`:

```python
"""`.svarupa/design.yaml`: the accepted target design, committed by the team."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from svarupa.design.model import Design

__all__ = ["DESIGN_FILE", "DesignException", "DesignFile", "UnitSpec", "dump_design", "load_design"]

DESIGN_FILE = ".svarupa/design.yaml"


@dataclass(frozen=True)
class UnitSpec:
    style: str
    parts: dict[str, list[str]] = field(default_factory=dict[str, list[str]])


@dataclass(frozen=True)
class DesignException:
    rule: str
    src: str
    dst: str
    reason: str


@dataclass(frozen=True)
class DesignFile:
    units: dict[str, UnitSpec]
    exceptions: list[DesignException]


def load_design(path: Path) -> tuple[DesignFile | None, str]:
    """The accepted design, or None with the reason it could not be used."""
    if not path.is_file():
        return None, ""
    try:
        data = yaml.safe_load(path.read_text(encoding="utf8"))
    except (OSError, yaml.YAMLError) as exc:
        return None, f"{path.name} could not be read ({type(exc).__name__}); using the inferred design"
    if not isinstance(data, dict) or not isinstance(data.get("units", {}), dict):
        return None, f"{path.name} must map `units` to unit settings; using the inferred design"
    units: dict[str, UnitSpec] = {}
    for unit, spec in data.get("units", {}).items():
        if isinstance(spec, dict) and isinstance(spec.get("style"), str):
            parts = spec.get("parts") or {}
            units[str(unit)] = UnitSpec(
                spec["style"],
                {str(p): [str(m) for m in ms] for p, ms in parts.items() if isinstance(ms, list)},
            )
    exceptions = [
        DesignException(str(e.get("rule", "")), str(e.get("from", "")), str(e.get("to", "")), str(e.get("reason", "")))
        for e in data.get("exceptions") or []
        if isinstance(e, dict) and e.get("reason")
    ]
    return DesignFile(units, exceptions), ""


def dump_design(design: Design, overrides: dict[str, str], keep: DesignFile | None = None) -> str:
    """design.yaml for every unit with a chosen (or overridden) style."""
    from svarupa.design import BY_ID
    from svarupa.design.match import assign
    from svarupa.design.units import signals  # noqa: F401  (kept for symmetry with design_for)

    units: dict[str, object] = {}
    for u in design.units:
        key = u.unit.id or "."
        style_id = overrides.get(key) or (u.chosen.style if u.chosen else None)
        if style_id is None or style_id not in BY_ID:
            continue
        if u.chosen is not None and u.chosen.style == style_id:
            assignments = u.chosen.assignments
        else:
            fit = next((f for f in u.runners_up if f.style == style_id), None)
            assignments = fit.assignments if fit else ()
        parts: dict[str, list[str]] = {}
        for a in assignments:
            parts.setdefault(a.part, []).append(a.module)
        units[key] = {"style": style_id, "parts": {p: sorted(ms) for p, ms in sorted(parts.items())}}
    exceptions = [
        {"rule": e.rule, "from": e.src, "to": e.dst, "reason": e.reason} for e in (keep.exceptions if keep else [])
    ]
    header = (
        "# The target design for this repository, accepted with `svarupa design`.\n"
        "# Edit parts and add exceptions (each needs a reason); commit this file.\n"
    )
    return header + yaml.safe_dump({"units": units, "exceptions": exceptions}, sort_keys=False, allow_unicode=True)
```

If the override names a style that is neither the chosen one nor a runner-up, assignments are empty: that is fine, `design_for` re-assigns by the style's rules when `parts` is empty (see Task 1 `_apply_accepted`). Remove the unused `assign`/`signals` imports if ruff flags them; they are not needed.

- [ ] **Step 3: The command**

In `svarupa/cli.py`, add next to `_setup`:

```python
def _design(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="svarupa design",
        description=(
            "Propose the design style of each app unit and, on accept, write "
            ".svarupa/design.yaml, the target design every scan checks against."
        ),
    )
    parser.add_argument("path", nargs="?", default=".", help="repository root (default: .)")
    parser.add_argument("--accept", action="store_true", help="write the proposal without asking")
    parser.add_argument(
        "--style",
        action="append",
        default=[],
        metavar="UNIT=STYLE",
        help="use STYLE for UNIT ('.' is the repository root); repeatable",
    )
    parser.add_argument("--print", action="store_true", help="print the proposal and write nothing")
    args = parser.parse_args(argv)

    root = Path(args.path)
    scan = detect(str(root))
    graph = build(scan, extract(scan, declared_dependencies(scan)), strict=False)
    path = root / DESIGN_FILE
    accepted, problem = load_design(path)
    if problem:
        print(f"  note: {problem}")
    design = design_for(scan, graph, None)
    overrides = dict(s.split("=", 1) for s in args.style if "=" in s)
    unknown = sorted(v for v in overrides.values() if v not in DESIGN_STYLES)
    if unknown:
        print(f"  unknown style(s): {', '.join(unknown)}; see docs/design.md")
        return 2

    print(f"  system: {', '.join(design.system)}  ({'; '.join(design.system_evidence)})")
    for u in design.units:
        key = u.unit.id or "."
        print()
        print(f"  unit {key} ({u.unit.level}, {u.unit.manifest or 'no manifest'})")
        if u.chosen is not None:
            print(f"    proposed: {u.chosen.style}  fit {u.chosen.fit:.2f} "
                  f"(coverage {u.chosen.coverage:.0%}, compliance {u.chosen.compliance:.0%})")
            for a in u.chosen.assignments:
                print(f"      {a.part:<14} {a.module}  ({a.reason})")
        else:
            print(f"    {u.note}")
        if u.runners_up:
            print("    also: " + ", ".join(f"{f.style} {f.fit:.2f}" for f in u.runners_up))

    if args.print:
        return 0
    if not args.accept:
        if not sys.stdin.isatty():
            print()
            print("  not written (no terminal); run with --accept to write .svarupa/design.yaml")
            return 0
        for u in design.units:
            key = u.unit.id or "."
            if key in overrides:
                continue
            answer = input(f"  unit {key}: [a]ccept, [s]tyle <name>, s[k]ip? ").strip()
            if answer.startswith("s ") and answer[2:].strip() in DESIGN_STYLES:
                overrides[key] = answer[2:].strip()
            elif answer not in ("a", "accept", ""):
                overrides[key] = ""
    chosen = {k: v for k, v in overrides.items() if v}
    skipped = {k for k, v in overrides.items() if not v}
    kept = Design(design.system, design.system_evidence, tuple(u for u in design.units if (u.unit.id or ".") not in skipped))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dump_design(kept, chosen, accepted), encoding="utf8")
    print()
    print(f"  wrote {DESIGN_FILE}; commit it so every scan and CI run checks against it")
    return 0
```

Imports at the top of `cli.py`: `import sys` (if absent), `from svarupa.design import BY_ID as DESIGN_STYLES, Design, design_for`, `from svarupa.design.file import DESIGN_FILE, dump_design, load_design`, and the existing `detect`, `build`, `extract`, `declared_dependencies` imports. In `main`, next to the `setup` dispatch:

```python
        if argv[:1] == ["design"]:
            return _design(argv[1:])
```

and mention `"svarupa design"` in the parser epilog next to setup.

Note: `design_for(scan, graph, None)` proposes from the code only; `--accept` keeps existing exceptions (`keep=accepted`) so re-accepting never drops a reasoned exception.

- [ ] **Step 4: Run, commit**

Run: `uv run pytest tests/test_design_cli.py tests/test_design.py -q`
Expected: pass.

Run: `uv run ruff check svarupa tests && uv run ruff format --check svarupa tests && uv run pyright svarupa`
Expected: clean.

```bash
git add svarupa tests/test_design_cli.py
git commit -m "Add svarupa design and the design.yaml target file"
```

---

### Task 3: Design violations as health checks, everywhere

**Files:** modify `svarupa/health/catalog.py`, `svarupa/health/__init__.py`, `svarupa/cli.py` (`_scan`), `svarupa/emit/{__init__,data,report,viewer}.py`, `svarupa/query/{__init__,cli,mcp_server}.py`, `tests/test_waved.py`, skill docs, README; create `tests/test_design_outputs.py`, `docs/design.md`.

- [ ] **Step 1: Failing tests**

`tests/test_design_outputs.py`:

```python
"""Design reaches health and every output."""

from __future__ import annotations

import json
from pathlib import Path

from svarupa.cli import main
from tests.test_design import layered, write


def test_layer_violation_is_a_health_violation(tmp_path: Path, capsys) -> None:
    layered(tmp_path, back_edge=True)
    assert main([str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "design: . layered (inferred)" in out
    gj = json.loads((tmp_path / ".svarupa" / "graph.json").read_text(encoding="utf8"))
    checks = [v["check"] for v in gj["health"]["violations"]]
    assert "layer-direction" in checks
    assert gj["design"]["units"][0]["chosen"]["style"] == "layered"
    report = (tmp_path / ".svarupa" / "REPORT.md").read_text(encoding="utf8")
    assert "## Design" in report and "layered" in report


def test_exceptions_are_listed_not_counted(tmp_path: Path) -> None:
    layered(tmp_path, back_edge=True)
    assert main(["design", str(tmp_path), "--accept"]) == 0
    path = tmp_path / ".svarupa" / "design.yaml"
    text = path.read_text(encoding="utf8").replace(
        "exceptions: []",
        "exceptions:\n- rule: layer-direction\n  from: shop/repository\n  to: shop/api\n  reason: legacy",
    )
    path.write_text(text, encoding="utf8")
    assert main([str(tmp_path)]) == 0
    gj = json.loads((tmp_path / ".svarupa" / "graph.json").read_text(encoding="utf8"))
    assert "layer-direction" not in [v["check"] for v in gj["health"]["violations"]]
    assert gj["design"]["exceptions"][0]["reason"] == "legacy"


def test_get_design_rules(tmp_path: Path, capsys) -> None:
    layered(tmp_path)
    assert main([str(tmp_path)]) == 0
    capsys.readouterr()
    assert main(["query", str(tmp_path / ".svarupa"), "get_design_rules", "--json"]) == 0
    rules = json.loads(capsys.readouterr().out)
    unit = rules["units"][0]
    assert unit["style"] == "layered"
    assert {"from": "service", "to": "api"} in unit["forbidden"]
    assert unit["parts"]["data"] == ["shop/repository"]
```

Run: `uv run pytest tests/test_design_outputs.py -q`
Expected: failures.

- [ ] **Step 2: Checks and assess**

`svarupa/health/catalog.py`: append to `CATALOG` (inside the same `# fmt: skip` tuple):

```python
    Check("layer-direction", "Layer imported the wrong way", 0, "the unit's design style (docs/design.md)", "maintainability", "major", 30),
    Check("part-independence", "Independent parts import each other", 0, "the unit's design style (docs/design.md)", "maintainability", "major", 30),
    Check("public-entry", "Part imported past its public entry", 0, "the unit's design style (docs/design.md)", "maintainability", "minor", 10),
    Check("core-purity", "Core uses frameworks or I/O", 0, "the unit's design style (docs/design.md)", "maintainability", "major", 60),
```

Update `tests/test_health.py::test_catalog_matches_the_spec` to include the four ids at the end of the list.

`svarupa/health/__init__.py`: change `assess` to

```python
def assess(graph: Graph, texts: Mapping[str, str], design: Design | None = None, exceptions: Sequence[DesignException] = ()) -> Health:
    violations = [
        *function_checks(graph),
        *file_checks(texts),
        *class_checks(graph),
        *duplicated_blocks(texts),
        *module_checks(graph),
        *design_checks(design, exceptions),
    ]
    return grade(graph, texts, violations)
```

with `design_checks` in `svarupa/health/checks.py`:

```python
def design_checks(design: Design | None, exceptions: Sequence[DesignException] = ()) -> list[Violation]:
    """Imports that break each unit's chosen style; excepted ones are left out."""
    if design is None:
        return []
    excused = {(e.rule, e.src, e.dst) for e in exceptions}
    out: list[Violation] = []
    for u in design.units:
        if u.chosen is None:
            continue
        for v in u.chosen.violations:
            if (v.rule, v.src, v.dst) in excused:
                continue
            check = BY_ID[v.rule]
            out.append(
                Violation(
                    check=v.rule,
                    message=f"{u.chosen.style}: {v.message}",
                    evidence=(v.evidence,),
                    module=v.src,
                    value=1,
                    minutes=check.minutes,
                )
            )
    return out
```

(Import `Design` from `svarupa.design` and `DesignException` from `svarupa.design.file` under `TYPE_CHECKING` to avoid an import cycle if one appears; `svarupa.design` imports only `build`, `detect`, `extract.base`, `model`.)

`Design.to_json` gains the exceptions: give `Design` a field `exceptions: tuple[DesignException, ...] = ()` and add `"exceptions": [{"rule": e.rule, "from": e.src, "to": e.dst, "reason": e.reason} for e in self.exceptions]`; `design_for` sets it from the accepted file (`accepted.exceptions` when it is a `DesignFile`).

- [ ] **Step 3: Scan, outputs, query**

`svarupa/cli.py` `_scan`, before computing health:

```python
    accepted, design_problem = load_design(Path(scan.root) / DESIGN_FILE)
    design = design_for(scan, graph, accepted)
    if design_problem:
        print(f"  design: {design_problem}")
    for u in design.units:
        if u.chosen is not None:
            print(
                f"  design: {u.unit.id or '.'} {u.chosen.style} ({u.source}), "
                f"conformance {u.chosen.compliance:.0%}"
            )
        else:
            print(f"  design: {u.unit.id or '.'} {u.note}")
```

and pass `design, design.exceptions` to `assess(...)`, and `design=design` to `emit(...)`.

`emit`: add `design: Design | None = None` to `emit`, `graph_json` (adds `"design": design.to_json()` when given), `render_report` (new `## Design` section right after `## Health`: system styles with evidence, then per unit a line `- unit: style (source), fit x, compliance y%` and its parts, or its note), and `render_viewer` (Health tab gains a "Design" `h3` with the same unit lines). Follow the 1c-1 patterns exactly (esc/tag in the viewer, `_listing` in the report).

`query`: add `get_design_rules` beside `get_health`:

```python
def get_design_rules(index: GraphIndex) -> dict[str, Any]:
    """What an agent must follow: per unit, parts, allowed and forbidden directions."""
    from svarupa.design import BY_ID as STYLES

    design = index.data.get("design")
    if not isinstance(design, dict):
        return {"available": False, "reason": "this artifact was built without design"}
    units = []
    for u in design.get("units", []):
        chosen = u.get("chosen")
        if not chosen:
            units.append({"unit": u["unit"], "style": None, "note": u.get("note", "")})
            continue
        style = STYLES[chosen["style"]]
        parts: dict[str, list[str]] = {}
        for p in chosen["parts"]:
            parts.setdefault(p["part"], []).append(p["module"])
        order = list(style.order)
        forbidden = [
            {"from": order[j], "to": order[i]} for i in range(len(order)) for j in range(i + 1, len(order))
        ] + [{"from": a, "to": b} for a, b in style.forbidden]
        units.append(
            {
                "unit": u["unit"],
                "style": style.id,
                "source": u["source"],
                "parts": parts,
                "order_top_to_bottom": order,
                "forbidden": forbidden,
                "independent_slices": list(style.independent),
                "public_entry_only": list(style.public_entry),
                "must_stay_pure": list(style.pure),
            }
        )
    return {"available": True, "units": units, "exceptions": design.get("exceptions", [])}
```

Register it exactly like `get_health` (FUNCTIONS, `__all__`, `_ARITY` with `(0, "")`, `run_query`, an MCP tool "The target design an agent must follow: parts, allowed import directions, forbidden imports, exceptions.", the `_ = (...)` tuple, "nine" tools in the docstring, `tests/test_waved.py` list, README query line, skill docs in both copies).

- [ ] **Step 4: docs/design.md**

Write `docs/design.md` with: what a unit is; how parts are matched (names plus evidence, scores 3/2/2); fit, coverage, compliance and the 0.5 threshold; `svarupa design` usage and flags; the design.yaml format with an exception example; the four checks with severities and minutes; and a table of every style (id, name, level, parts top to bottom, extra rules, source) generated from `CATALOG` by hand (keep it in sync; a test in Step 5 checks every style id appears in the doc).

- [ ] **Step 5: Run, commit**

Add to `tests/test_design.py`:

```python
def test_every_style_is_documented() -> None:
    doc = (Path(__file__).resolve().parents[1] / "docs" / "design.md").read_text(encoding="utf8")
    assert all(f"`{s.id}`" in doc for s in CATALOG)
```

Run: `uv run pytest -q`
Expected: all pass (including the orphan-module check now that `_scan` imports `svarupa.design`).

Run: `uv run ruff check svarupa tests scripts && uv run ruff format --check svarupa tests && uv run pyright svarupa && uv run python scripts/benchmark.py check`
Expected: clean; benchmark check passes (design violations are new health violations, and none of them are expected or probed yet).

Run `uv run svarupa . --lock` and commit any lockfile change.

```bash
git add -A svarupa tests skills README.md docs/design.md .svarupa/architecture.lock
git commit -m "Report design violations as health checks in every output"
```

---

### Task 4: Benchmark design

**Files:** `scripts/benchmark.py`, `tests/test_benchmark.py`, `benchmark/README.md`, `benchmark/expected/*.toml`, `benchmark/scores.json`, `svarupa/design/styles.py` (maturity).

- [ ] **Step 1: Failing tests** (append to `tests/test_benchmark.py`)

```python
def test_design_facts_are_parsed(tmp_path: Path) -> None:
    _expected(tmp_path, "demo", '[[must]]\ndesign = ". layered"\nwhy = "api/handlers.py:1 x"\n')
    must, _ = bm.load_expected("demo", tmp_path)
    assert must[0].key == "design:. layered" and must[0].kind == "design"


def test_observe_reports_the_chosen_design(tmp_path: Path) -> None:
    from tests.test_design import layered

    layered(tmp_path)
    resolved, _ = bm.observe(tmp_path)
    assert "design:. layered" in resolved


def test_stable_styles_need_two_repos() -> None:
    accepted = {
        "a": {"missed": [], "fired": [], "design_found": ["layered"]},
        "b": {"missed": [], "fired": [], "design_found": ["layered", "clean"]},
    }
    assert bm.stable_styles(accepted) == {"layered"}


def test_style_maturity_follows_the_benchmark() -> None:
    from svarupa.design import CATALOG as STYLES

    scores = ROOT / "benchmark" / "scores.json"
    stable = bm.stable_styles(json.loads(scores.read_text(encoding="utf8")))
    wrong = {s.id: s.maturity for s in STYLES if (s.maturity == "stable") != (s.id in stable)}
    assert not wrong, f"style maturity disagrees with the benchmark: {wrong}"
```

- [ ] **Step 2: Implement**

`_fact`: a `design` entry gives `Fact("design:" + text, "design", why)`. `observe`: after violations, compute `design_for(scan, graph)` and add `design:<unit or .> <style>` for each unit with a chosen style, plus pass the design to `assess` so design violations are observed as `violation:` keys. `score`/`to_json`: add `"design_found": sorted(style for each must design fact found)` per repo (store the style ids, from keys `design:<unit> <style>`). Add:

```python
def stable_styles(accepted: dict[str, dict[str, object]]) -> set[str]:
    """Styles found as expected on at least two repos."""
    count: dict[str, int] = {}
    for acc in accepted.values():
        for style in set(acc.get("design_found", [])):  # type: ignore[arg-type]
            count[style] = count.get(style, 0) + 1
    return {s for s, n in count.items() if n >= STABLE_REPOS}
```

Document `design = "<unit> <style>"` in `benchmark/README.md` (unit `.` for the repository root).

- [ ] **Step 3: Expected designs from source (agents)**

Four source-only agents (as in 1b and 1c-1). Per repo, read the README, the directory layout and the imports, and decide **without running Svarupa** which style from `docs/design.md` each app unit (manifest directory) follows; add `[[must]] design = "<unit> <style>"` only when the repo clearly follows one (cite the file:line that best shows it, for example the layer import); add `[[must_not]] design = "<unit> <style>"` for one clearly wrong style per repo; add `[[must]] violation = "layer-direction <path>:<line>"` only where the source clearly imports against its own style. Leave out units with no clear style.

- [ ] **Step 4: Score, review, accept, maturity**

Run `scripts/benchmark.py run`, review every design miss against the source (fact error: ruled correction; otherwise finding: fix the name lists in `styles.py` with a failing test first when the fix is general, never a repo-specific name), `accept`, `check`, then set `maturity="stable"` for the styles `test_style_maturity_follows_the_benchmark` lists.

- [ ] **Step 5: Commit**

```bash
git add -A benchmark scripts tests svarupa
git commit -m "Benchmark design styles against hand-checked designs"
```

---

### Task 5: Mutations, review, PR

- [ ] **Step 1:** add to `scripts/mutate_packs.py` `MUTATIONS` (and `tests/test_design.py`, `tests/test_design_cli.py`, `tests/test_design_outputs.py` to `SUITE`):

```python
    (
        "upward imports are allowed",
        "svarupa/design/match.py",
        "order[dst.part] < order[src.part]",
        "order[dst.part] > order[src.part] + 99",
    ),
    (
        "exceptions stop being honored",
        "svarupa/health/checks.py",
        "            if (v.rule, v.src, v.dst) in excused:\n                continue\n",
        "",
    ),
    (
        "a low fit is proposed anyway",
        "svarupa/design/__init__.py",
        "        if fits and fits[0].fit >= MIN_FIT:",
        "        if fits:",
    ),
    (
        "design writes the file without a terminal",
        "svarupa/cli.py",
        "        if not sys.stdin.isatty():\n",
        "        if False:\n",
    ),
```

Run `uv run python scripts/mutate_packs.py`: every line caught.

- [ ] **Step 2:** commit (`Cover design detection with mutations`), final whole-branch review, fix pass, push, PR against `feat/health-1c1` (stacked) with the title `Design 1c-2: style detection, design.yaml, design checks`. Do not merge. In the PR body note: merge #11 first, then retarget this PR to main before deleting `feat/health-1c1`.
