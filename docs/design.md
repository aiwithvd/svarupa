# Design styles

Svarupa tells you which industry design style each app in a repository
follows, or is closest to, and reports every import that breaks it.

## Units

A unit is a directory holding an app manifest: `pyproject.toml`,
`requirements.txt`, `package.json`, `go.mod`, `pom.xml`, `build.gradle` or
`build.gradle.kts`. Each module (directory of source) belongs to the closest
unit above it; modules outside every unit belong to the repository root `.`.
A unit's level decides which styles it is compared with: `frontend` (a UI
framework or `.tsx`/`.jsx` files), `backend` (HTTP routes, a datastore or a
message bus), `data` (pipeline directories), `mobile` (an Android manifest),
otherwise `library`.

The repository as a whole also gets system-level labels with their
evidence: microservices, event-driven, serverless, micro-frontends, BFF,
modular monolith or monolith.

## How a style is matched

Each style has parts (for example layered: api, service, data). A module
joins a part when its last directory name is one of the part's names (score
3), another directory in its path is (score 2), or Svarupa proves the
part's evidence in it, such as HTTP routes or a database client (score 2).
The highest score wins; every assignment says why.

Helper folders (`config`, `utils`, `lib`, `common`, `middlewares`,
`validations` and similar) and composition roots (the unit root, `src`,
`cmd`, `bin`, `main`) are left out of a style's score, unless one of its
parts names them (Feature-Sliced Design's `shared`, for example).

- **Coverage**: the share of the remaining modules that joined a part. A
  module placed only by evidence counts half; a directory named for the
  part counts fully.
- **Compliance**: the share of imports between those modules that follow
  the style's rules (and, for pure parts, the share without I/O). Imports
  the team excepted in design.yaml do not lower it.
- **Fit** = coverage x compliance. A style needs at least two of its parts
  named by directories in the code; evidence alone never makes a style. A
  style is proposed at fit 0.5 or more. Ties go to the style whose parts the
  code fills most.
- **No clear design**: when no style reaches 0.5, Svarupa names the closest
  style and the moves that would bring the code to it (imports to fix,
  modules to place). When no style's parts are found at all, it says so.

## Rules and the checks they raise

| Check | Raised when | Severity | Minutes |
|---|---|---|---|
| `layer-direction` | a part imports a part above it, or a pair the style forbids | major | 30 |
| `part-independence` | two slices of an independent part import each other | major | 30 |
| `public-entry` | a slice is imported past its root (`index`, `__init__`, package root) | minor | 10 |
| `core-purity` | a pure part declares routes or uses a database, bus, cloud or UI library | major | 60 |

Each violation cites the import line (or the line that proves the I/O use)
and counts in the health grade like every other check.

## Accepting a design

```
svarupa design .               # show the proposal; in a terminal, ask
svarupa design . --accept      # write .svarupa/design.yaml without asking
svarupa design . --style .=clean --accept
svarupa design . --print       # only print
```

Without a terminal (CI) and without `--accept`, nothing is written. Units
already in design.yaml keep the style and parts the team wrote; only
`--style` changes them. Commit
`.svarupa/design.yaml`; every scan then checks against it instead of the
inferred proposal:

```yaml
units:
  .:
    style: layered
    parts:
      api: [shop/api]
      service: [shop/services]
      data: [shop/repository]
exceptions:
- rule: layer-direction
  from: shop/repository
  to: shop/api
  reason: legacy import, removing in Q4
```

An exception needs a reason. Excepted imports are listed, not counted: they
add no debt and do not lower conformance. An accepted design whose listed
modules no longer exist reports "nothing to check" instead of a score.
Agents read the same rules through `get_design_rules` (query and MCP).

## Styles

Parts are listed top to bottom; imports may point down, never up. Every
style is experimental until the benchmark finds it as expected on two
repositories.

| Id | Name | Level | Parts | Extra rules | Source |
|---|---|---|---|---|---|
| `layered` | Layered (N-tier) | backend | api > service > data | - | Buschmann et al., Pattern-Oriented Software Architecture: Layers |
| `mvc` | Model-View-Controller | backend | controller > view > model | - | Reenskaug 1979; Krasner and Pope 1988 |
| `hexagonal` | Hexagonal (ports and adapters) | backend | adapters > ports > core | pure: core | Cockburn 2005, Hexagonal Architecture |
| `onion` | Onion | backend | infrastructure > application > domain | pure: domain | Palermo 2008, The Onion Architecture |
| `clean` | Clean Architecture | backend | adapters > usecases > entities | pure: usecases, entities | Martin 2012, The Clean Architecture |
| `vertical-slice` | Vertical Slice | backend | features > shared | independent slices: features | Bogard 2018, Vertical Slice Architecture |
| `cqrs` | CQRS | backend | commands > queries > domain | never: commands -> queries | Young 2010, CQRS Documents |
| `feature-sliced` | Feature-Sliced Design | frontend | app > processes > pages > widgets > features > entities > shared | independent slices: processes, pages, widgets, features, entities; public entry only: processes, pages, widgets, features, entities | Feature-Sliced Design v2.1 (feature-sliced.design) |
| `frontend-layers` | Pages, components, state, API client | frontend | pages > components > state > api > shared | - | React and Next.js project structure guidance |
| `mvvm` | Model-View-ViewModel | frontend | view > viewmodel > model | - | Gossman 2005, Introduction to Model/View/ViewModel |
| `mvi` | Model-View-Intent | mobile | view > intent > state > data | - | Staltz, Model-View-Intent (Cycle.js); Android MVI guidance |
| `mobile-clean` | Clean (mobile) | mobile | presentation > data > domain | pure: domain; never: presentation -> data | Android app architecture guide (UI, data and domain layers) |
| `pipeline-stages` | Pipeline stages | data | orchestration > load > transform > extract | - | ETL pipeline pattern (extract, transform, load) |
| `dbt-layers` | dbt layers | data | marts > intermediate > staging | - | dbt Labs, How we structure our dbt projects |
| `medallion` | Medallion | data | gold > silver > bronze | - | Databricks, Medallion architecture |
| `public-api` | Public API over internals | library | public > internal | - | Go internal packages; Python private-module convention |
| `plugin` | Plugin (microkernel) | library | plugins > core | independent slices: plugins | Richards 2015, Software Architecture Patterns: Microkernel |
