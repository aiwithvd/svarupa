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
