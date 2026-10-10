"""Design styles: units, part assignment, fit and the proposal."""

from __future__ import annotations

from pathlib import Path

from svarupa.build import build
from svarupa.design import BY_ID, CATALOG, design_for
from svarupa.detect import detect
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
    assert {"layered", "mvc", "hexagonal", "onion", "clean", "vertical-slice", "cqrs"} <= set(
        BY_ID
    )
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
    rules = [
        (v.rule, v.src, v.dst, v.evidence.file, v.evidence.start_line)
        for v in unit.chosen.violations
    ]
    assert rules == [
        ("layer-direction", "shop/repository", "shop/api", "shop/repository/store.py", 1)
    ]
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
    write(
        tmp_path,
        "src/app/main.tsx",
        "import { A } from '../features/a';\nimport { b } from '../features/b/model/x';\n"
        "export const M = () => <A/>;\n",
    )
    write(
        tmp_path,
        "src/features/a/index.ts",
        "import { b } from '../b/model/x';\nexport const A = () => b;\n",
    )
    write(tmp_path, "src/features/b/index.ts", "export const B = 1;\n")
    write(tmp_path, "src/features/b/model/x.ts", "export const b = 2;\n")
    write(tmp_path, "src/shared/ui.ts", "export const ui = 1;\n")
    unit = run(tmp_path).units[0]
    fit = next(f for f in (unit.chosen, *unit.runners_up) if f and f.style == "feature-sliced")
    assert {v.rule for v in fit.violations} >= {"part-independence", "public-entry"}


def test_core_purity(tmp_path: Path) -> None:
    write(
        tmp_path,
        "pyproject.toml",
        '[project]\nname = "x"\ndependencies = ["sqlalchemy", "fastapi"]\n',
    )
    write(
        tmp_path,
        "app/adapters/http.py",
        "from app.domain.order import Order\n\n\ndef h():\n    return Order()\n",
    )
    write(tmp_path, "app/domain/order.py", "import sqlalchemy\n\n\nclass Order:\n    pass\n")
    write(tmp_path, "app/ports/repo.py", "from app.domain.order import Order\n")
    design = run(tmp_path)
    fits = [
        f
        for u in design.units
        for f in (u.chosen, *u.runners_up)
        if f and f.style == "hexagonal"
    ]
    assert fits and [v.rule for v in fits[0].violations if v.rule == "core-purity"] == [
        "core-purity"
    ]
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


def test_a_purity_breach_lowers_compliance(tmp_path: Path) -> None:
    write(tmp_path, "pyproject.toml", '[project]\nname = "x"\ndependencies = ["sqlalchemy"]\n')
    write(tmp_path, "app/adapters/http.py", "from app.domain.order import Order\n")
    write(tmp_path, "app/domain/order.py", "import sqlalchemy\n\n\nclass Order:\n    pass\n")
    design = run(tmp_path)
    fit = next(
        f
        for u in design.units
        for f in (u.chosen, *u.runners_up)
        if f and f.style == "hexagonal"
    )
    assert fit.compliance == 0.5  # one clean import, one purity breach


def test_every_style_is_documented() -> None:
    doc = (Path(__file__).resolve().parents[1] / "docs" / "design.md").read_text(
        encoding="utf8"
    )
    assert all(f"`{s.id}`" in doc for s in CATALOG)
