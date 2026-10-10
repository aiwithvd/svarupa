# Svarupa: Design Styles and the Target Design (step 1c-2)

Date: 2026-10-10
Status: design approved, spec written
Parent: `2026-10-03-system-health-umbrella-design.md`; builds on 1c-1 (health engine)

## Goal

Tell an engineer which industry design style each part of a repository
follows (or is closest to), let a person accept it as the target design in
one step, and report every import that breaks it, with `file:line`, as a
health violation. The accepted design also becomes the rules an AI agent
reads before writing code (`get_design_rules`).

## Decisions (maintainer, 2026-10-10)

| Question | Decision |
|---|---|
| Units | One unit per directory holding an app manifest (pyproject.toml, requirements.txt, package.json, go.mod, pom.xml, build.gradle[.kts]); the repository is the system unit |
| Part matching | Directory names plus evidence Svarupa already proves (routes, datastore clients, UI frameworks, message buses); every assignment says why |
| Acceptance | `svarupa design`: interactive prompt (accept / pick another / skip) plus flags `--accept`, `--style <unit>=<style>`, `--print` |
| Violations | New checks in the health catalog: `layer-direction`, `part-independence`, `public-entry`, `core-purity`; plus conformance % per unit |
| Catalog | Every style from the umbrella table, as data, experimental until the benchmark proves it |

## Styles as data

A style is `Style(id, name, level, parts, order, independent, public_entry,
pure, source, maturity)`:

- `parts`: `Part(id, names, evidence, slices)`. `names` are directory names
  (lowercase, singular and plural listed); `evidence` are signals Svarupa
  proves: `routes` (the module declares HTTP routes), `datastore`,
  `messagebus`, `cloud`, `ui` (from the externals vocabulary). `slices`
  means each matching directory is its own slice (features/a, features/b).
- `order`: part ids from top to bottom; a dependency may only point down
  or sideways within the same part (direction rule).
- `independent`: parts whose slices must not import each other.
- `public_entry`: parts whose slices may be imported only through their
  entry file (`index.*`, `__init__.py`, `mod.rs`, a Go package root).
- `pure`: parts that must import no framework or I/O library (no routes,
  no datastore, bus, cloud or UI import).

Levels: `backend`, `frontend`, `mobile`, `data`, `library` (part-based) and
`system` (classified from units, compose services and queues: monolith,
modular monolith, microservices, event-driven, serverless; micro-frontends
and BFF from frontend units plus a backend serving them).

## Detection

For each unit and each style of a matching level:

1. Assign each module of the unit to a part: a name match on the module's
   last path segment scores 3, on another segment 2, an evidence match 2;
   the highest score wins, ties by part order; no match leaves it
   unassigned. Each assignment records its reason.
2. Coverage = assigned modules / modules in the unit.
3. Compliance = dependencies between assigned modules that obey the
   style's rules / all such dependencies (1.0 when there are none).
4. Fit = coverage x compliance. The best fit is proposed when it is at
   least 0.5; otherwise the unit is "no clear design", the closest style is
   proposed with migration moves (the violations whose removal raises fit
   most), ranked by fit gained.

Level of a unit: frontend when it imports a UI framework or has `.tsx`
pages; backend when it declares routes or uses a datastore; data when it
has dbt or pipeline manifests; library otherwise. A unit is scored only
against styles of its level.

## Target design

`.svarupa/design.yaml` (written by `svarupa design` on accept, committed by
the team):

```yaml
units:
  backend:
    style: layered
    parts:
      api: [backend/app/api/routes]
      service: [backend/app/crud]
      data: [backend/app/models]
exceptions:
  - rule: layer-direction
    from: backend/app/models
    to: backend/app/api
    reason: legacy import, removing in Q4
```

The target is the accepted file when present, else the inferred proposal,
labelled `inferred` in every output. Exceptions need a reason; an excepted
violation is listed as accepted, not counted.

## Outputs

- New health checks (maintainability area, experimental): `layer-direction`
  (major, 30 min), `part-independence` (major, 30 min), `public-entry`
  (minor, 10 min), `core-purity` (major, 60 min); cited at the import line.
- Conformance % per unit with style, fit and source (accepted or inferred)
  in the CLI, REPORT.md, the Health tab, and `graph.json` (`design` key).
- MCP and query `get_design_rules`: per unit, the parts, allowed
  directions, forbidden imports and exceptions, for an agent to follow.
- `svarupa design` (prompt and flags) writes `.svarupa/design.yaml`.

## Benchmark

New fact `design = "<unit dir> <style id>"`, drafted from source for each
repo (for example go-clean-arch is clean architecture), and expected
design violations where the source clearly breaks its own style. A style
becomes stable when it is the expected and found design on two repos.

## Out of scope

Rendering the target design on the diagrams; editing design.yaml from the
HTML viewer; security and scaling checks (1c-3).
