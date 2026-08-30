# Svarupa: Design

**Date:** 2026-08-30
**Status:** Design approved, pending implementation plan

---

## 1. What this is

Svarupa (स्वरूप, "its own true form") reads a codebase and produces a **verified** map of it: a queryable knowledge graph plus nine types of architecture diagram, delivered as one interactive HTML artifact.

The defining constraint: **every node and every edge in every diagram carries `file:line` evidence, or it does not render.** Not a heuristic guess, not an LLM's plausible story. A claim you can click through to the source line that proves it.

The name states the thesis. Svarupa is a thing's actual form, not its intended one. Architecture documents describe what someone meant to build. Svarupa renders what exists.

### The gap it fills

Every tool in this space sits on one side of a divide and cannot cross it:

| Tool | Analyzes code | Draws formal diagrams |
|---|---|---|
| Graphify (~85k stars) | Deeply, 25+ languages via tree-sitter | No. Force-directed node-link graphs only |
| Understand-Anything (~55k stars) | Yes, tree-sitter + LLM | No. ReactFlow/ELK force layouts only |
| Archify | No. Reads nothing | Yes, five types, excellent quality |
| Cocoon-AI (~2.5k stars) | No. Prompt-to-diagram | Yes, architecture only, static SVG |

Nobody closes the loop `code → AST → derived formal diagram`. The analyzers produce blobs; the drawers produce unverifiable pictures. Svarupa is the first tool on both sides.

### Second, larger thesis: continuous architecture governance

Graphify and Understand-Anything are *onboarding* tools. You run them once when you join a project, look at the graph, and never open it again. Retention is structurally poor.

Because Svarupa's output is deterministic and evidence-backed, it can run in CI on every pull request: diff the architecture, comment the impact, and fail the build on policy violations. That turns it from a tool people enjoy into infrastructure a platform team mandates. **Cross-language architecture linting is a category no competitor occupies.**

---

## 2. Architecture

### 2.1 Pipeline

```
  detect ──▶ extract ──▶ build ──▶ cluster ──▶ derive ──▶ refine ──▶ layout ──▶ emit
    │           │           │          │           │          │          │         │
  files      AST +       evidenced  communities  candidate  overlay   positioned  artifacts
  classified config       graph                  diagrams   applied    diagrams
```

Each stage is a pure function of its input plus pinned versions. No stage reaches for network or wall-clock time. This is what makes the lockfile reproducible.

| Stage | Responsibility |
|---|---|
| `detect` | Walk the tree, classify files, respect ignore rules, emit a manifest with content hashes |
| `extract` | tree-sitter AST per code file, structured parse per config file. Emit evidenced nodes and edges |
| `build` | Assemble into a NetworkX graph. Deduplicate. Validate every element carries evidence |
| `cluster` | Leiden community detection with deterministic edge ordering. Split oversized, resplit low-cohesion |
| `derive` | Per-diagram-type heuristics turn graph structure into candidate diagram specs |
| `refine` | Replay `refinements.yaml` overlay onto candidates. Optional LLM naming pass (constrained) |
| `layout` | Per-type layout engine assigns coordinates. Geometry validation |
| `emit` | Write graph.json, diagrams/*.json, index.html, REPORT.md, architecture.lock |

### 2.2 Module boundaries

Each module is independently testable and has one reason to change.

```
svarupa/
  detect.py        file classification, ignore rules, content-hash manifest
  extract/
    __init__.py    dispatcher: path -> extractor
    base.py        Extractor ABC; every extractor returns Evidenced[]
    lang_python.py, lang_typescript.py, lang_go.py,
    lang_rust.py, lang_java.py, lang_sql.py
    frameworks/    per-language framework detectors (see 4.2)
    config/        compose.py, k8s.py, terraform.py, openapi.py, manifests.py, ci.py
  build.py         graph assembly, dedup, evidence enforcement
  cluster.py       Leiden with determinism guarantees
  derive/
    base.py        Deriver ABC: graph -> DiagramSpec | None
    architecture.py, moduledeps.py, sequence.py, erd.py, apisurface.py,
    deploy.py, lifecycle.py, workflow.py, classhier.py
  refine.py        overlay application, constrained LLM naming
  layout/
    base.py        Layout ABC: DiagramSpec -> PositionedSpec
    columnar.py    sequence
    grid.py        ERD
    layered.py     module deps, class hierarchy
    laned.py       lifecycle, workflow (layered plus lane assignment)
    clustered.py   architecture, deploy topology
    grouped.py     API surface (list-tree by resource path)
    validate.py    overlap, crossing, out-of-bounds, label fit
  emit/
    graph.py, diagrams.py, report.py, viewer.py, bundle.py
  lock/
    serialize.py   canonical lockfile writer
    diff.py        lockfile comparison -> ArchitectureDelta
    policy.py      rule evaluation (P3)
  mcp/
    server.py      MCP stdio + HTTP
    tools.py       the six tools
  targets/
    base.py        Target ABC: detect / plan / apply / verify
    skill.py, ci_github.py, ci_gitlab.py, ci_docker.py,
    hook.py, mcp_register.py, plugin.py
  cache.py         content-hash cache, version-namespaced
  cli.py           argument parsing, command dispatch
viewer/            browser-side rendering (see 6)
```

**Rule:** `derive/` may only read the graph. `layout/` may only read a `DiagramSpec`. Neither may re-parse source. This keeps the evidence chain intact and each layer independently testable.

---

## 3. Data model

### 3.1 Evidence

The primitive on which everything else rests.

```python
@dataclass(frozen=True, order=True)
class Evidence:
    file: str          # repo-relative, always posix separators
    start_line: int    # 1-indexed, inclusive
    end_line: int      # 1-indexed, inclusive
    rev: str | None    # git blob sha, when available
```

### 3.2 Node

```python
@dataclass(frozen=True)
class Node:
    id: str                    # deterministic: hash(kind, file, qualified_name)
    kind: NodeKind             # module|function|class|method|endpoint|table|
                               # column|service|datastore|queue|resource|state|job
    label: str                 # display name as written in source
    qualified_name: str        # fully qualified, language-native
    evidence: tuple[Evidence, ...]   # MUST be non-empty
    lang: str | None
    attrs: Mapping[str, str]   # sorted; framework hints, modifiers, types
```

### 3.3 Edge

```python
@dataclass(frozen=True)
class Edge:
    src: str
    dst: str
    kind: EdgeKind             # calls|imports|inherits|implements|contains|
                               # reads|writes|exposes|references|depends_on|
                               # transitions_to|publishes|consumes|deploys
    evidence: tuple[Evidence, ...]   # MUST be non-empty
    confidence: Confidence     # EXTRACTED | RESOLVED
    attrs: Mapping[str, str]
```

`EXTRACTED` means the edge is literally present at that source location. `RESOLVED` means a cross-file link the resolver established (an import target, a method dispatch), where the evidence points at the call site that motivated it. There is deliberately no `INFERRED` tier: an edge nobody can point at does not exist.

### 3.4 Fail-closed enforcement

`build.py` rejects any node or edge with empty evidence. This is not a warning. It raises `MissingEvidenceError` with the offending element and the extractor that produced it, and the build fails.

Consequence: extractors must be honest. If Python's dynamic dispatch means we cannot resolve `handler()` to a definite target, we emit no edge rather than a plausible one. The report records what we could not resolve so the gap is visible rather than papered over.

---

## 4. Extraction

### 4.1 Languages

Six, each with genuine depth rather than symbol-and-import shallowness.

| Language | Grammar | Extracts |
|---|---|---|
| Python | tree-sitter-python | modules, classes, functions, methods, decorators, imports, calls, inheritance |
| TypeScript/JS | tree-sitter-typescript | + interfaces, type-only imports, re-exports, dynamic `import()` |
| Go | tree-sitter-go | + interfaces, receivers, package-level structure |
| Rust | tree-sitter-rust | + traits, impls, modules, `use` trees |
| Java | tree-sitter-java | + annotations, packages, interfaces |
| SQL | tree-sitter-sql | DDL: tables, columns, keys, constraints, views |

### 4.2 Framework awareness

This is the difference between a diagram that says *"here are your folders"* and one that says *"here are your services and endpoints."* A route handler must be recognizable as an endpoint, not just another function.

| Language | Frameworks |
|---|---|
| Python | FastAPI, Django, Flask, SQLAlchemy, Celery, Pydantic |
| TS/JS | Express, NestJS, React, Prisma, TypeORM |
| Go | net/http, chi, gin, gorm |
| Rust | axum, actix-web, sqlx, diesel |
| Java | Spring, JPA, JAX-RS |

Detectors are declarative where possible: a decorator or annotation pattern maps to a node kind and attribute set. Adding a framework is a data change plus a test, not new control flow.

### 4.3 Config and schema

Architecture lives in these files more than in the code:

| File | Yields |
|---|---|
| `docker-compose.y*ml` | services, datastores, queues, `depends_on` topology |
| `k8s/*.yaml`, Helm charts | deployments, services, ingress |
| `*.tf` | cloud resources, managed datastores |
| `openapi.y*ml`, `swagger.json` | endpoint surface, request/response schemas |
| `*.sql` | tables, columns, foreign keys (ERD source of truth) |
| `package.json`, `pyproject.toml`, `go.mod`, `Cargo.toml`, `pom.xml` | external dependencies |
| `.github/workflows/*`, `.gitlab-ci.yml` | pipeline stages, jobs |

Every one parses deterministically. No LLM, no API key.

---

## 5. Derivation: graph to diagrams

A graph has thousands of nodes. A diagram has about twelve. Derivers make that reduction, and every one obeys the same contract:

```python
class Deriver(ABC):
    kind: DiagramKind
    def applicable(self, g: Graph) -> bool: ...
    def derive(self, g: Graph) -> DiagramSpec | None: ...
```

A deriver returning `None` produces no diagram, and the report says why. **An empty diagram is never fabricated to fill a tab.**

| Diagram | Derivation strategy | Strength |
|---|---|---|
| Architecture | Leiden communities as components, import direction as layers, compose services as concrete boundaries | Strong |
| Module deps | Import edges, topologically layered | Trivial |
| ERD | SQL DDL plus ORM model classes, FK edges | Strong |
| API surface | OpenAPI spec plus route decorators/annotations, grouped by resource | Strong |
| Deploy topology | compose/k8s/terraform, nested by scope | Strong |
| Sequence | Detect entry points (main, route handlers, CLI commands, job entries), trace the call chain, participants are the modules crossed | Strong |
| Class hierarchy | `inherits`/`implements` edges | Trivial |
| Lifecycle | State enums plus the functions that assign them; transitions from assignment sites | Medium, LLM-assisted ordering |
| Workflow | Middleware chains, task pipelines (Celery chains, CI job graphs) | Medium, LLM-assisted grouping |

### 5.1 Hierarchical zoom

**Diagrams are always generated at the community level, regardless of repo size.** The top view targets about twelve boxes. Each box carries a `children` reference to its own sub-diagram, derived the same way one level down. The full graph is never rendered whole.

Small repos degrade gracefully rather than specially: when a community contains few enough nodes that its sub-diagram would restate the parent box, no `children` reference is emitted and the box is a leaf. A twenty-node project therefore produces one flat diagram through the same code path that produces five levels for a monorepo.

This is the answer to the 50,000-node monorepo. The UX is identical at 500 nodes and 500,000, and it avoids the hairball failure that every competing tool exhibits at scale.

### 5.2 The LLM contract

The LLM participates only in lifecycle and workflow derivation, and only under a hard constraint:

**Input:** a list of already-evidenced nodes and edges, nothing else.
**Permitted:** group them, order them, assign human-readable names.
**Forbidden:** introduce any element not present in its input.

The refine stage validates this. Any returned element whose id is not in the input set is dropped and logged. Fail-closed survives because the LLM never becomes a source of facts, only of arrangement and vocabulary.

When no LLM is available, lifecycle and workflow fall back to deterministic ordering with machine-generated names. They still render; they just read less well.

### 5.3 Refinement overlay

Derived names are honest but ugly: `community_3`, `utils_helpers_common`. Refinements fix that without breaking reproducibility.

```yaml
# .svarupa/refinements.yaml  (committed)
architecture:
  - rename:  {id: community_3, to: "Billing"}
  - merge:   {ids: [utils_a, utils_b], as: "Shared Kernel"}
  - exclude: {ids: [test_fixtures, migrations]}
  - pin:     {id: postgres, at: [900, 400]}
overview:
  narrative: |
    This is a FastAPI billing service...
```

Rebuild always re-derives from scratch, then replays the overlay. Human judgment and fresh analysis both survive. A refinement referencing an id that no longer exists produces a warning naming the stale entry, never a silent drop.

---

## 6. Layout and the viewer

### 6.1 Per-type layout

Most of these diagrams do not need general graph layout, and forcing them through one produces bad output. Each type gets a purpose-built engine of roughly 100 to 200 lines:

| Type | Engine |
|---|---|
| Sequence | Columnar. Participants are columns, messages are rows |
| ERD | Grid with foreign-key-aware locality nudging |
| Module deps, class hierarchy | Layered DAG by topological depth, row-packed |
| Architecture, deploy topology | Clustered layered, communities as bands, nested boundaries |
| API surface | Grouped list-tree by resource path |
| Lifecycle, workflow | Layered left-to-right with lane assignment |

Layout runs in Python at build time and writes coordinates into the diagram JSON. Geometry validation (node overlap, out-of-bounds, edge-through-node crossings, label fit) runs immediately after and fails the build on error, following Archify's proven model.

### 6.2 Viewer

`index.html` embeds the rendering engine as JS and lazy-loads the JSON. It draws SVG from pre-positioned specs, so the browser does no layout work.

**Navigation:** lands on a generated **Overview** that reads like a briefing with diagrams embedded inline at the points they explain something. Tabs give direct access to each full diagram. A graph explorer tab exposes the full knowledge graph.

**Cross-linking is the point.** Click any box in any diagram and you can jump to that node in the graph explorer, or straight to the source line that proves it. The evidence chain is navigable, not decorative.

**Overview text:** the CLI always emits a templated overview from real graph facts, so a standalone user with no API key gets something useful. When run through the agent skill, the agent rewrites it as prose and saves it into `refinements.yaml`, where it survives rebuilds.

**Staleness:** the viewer header shows how many commits behind HEAD the graph is, and the command to refresh.

**Bundle:** `--bundle` base64-inlines every JSON asset into a single portable HTML for sharing.

---

## 7. The lockfile and CI

### 7.1 Architecture lockfile

```
.svarupa/architecture.lock     # committed, deterministic, diff-friendly
```

Plain text, canonically ordered, designed so `git diff` renders architecture changes natively in a pull request before CI even runs. A reviewer sees `billing -> auth` appear as a green line.

```
# svarupa 1.0.0
# schema 1
# grammars python@0.21.0 typescript@0.20.5 go@0.20.0 ...
module     api            -> auth, billing
module     billing        -> auth, db
endpoint   POST /refunds  billing.refunds     billing/refunds.py:44-71
datastore  postgres       compose.yml:22-29
```

It records **architecture-level facts only**: modules and their dependencies, endpoints, datastores, queues, service topology, and public type surfaces. Not every function. It stays small and stable so that a refactor inside a module produces no diff, while a new cross-module dependency produces exactly one line.

The full `graph.json` and HTML stay gitignored and regenerate on demand.

### 7.2 Determinism contract

The lockfile must be byte-identical on a developer laptop and in CI, or every PR shows changes that did not happen.

- Every collection canonically sorted before serialization
- Grammar versions pinned exactly, recorded in the header
- Paths always repo-relative with posix separators
- No timestamps, no absolute paths, no iteration-order dependence, no locale-dependent formatting
- Tool version and schema version stamped in the header
- **Stamp mismatch refuses to diff** and instructs the user to regenerate, rather than reporting spurious changes

Enforced by our own CI: build the same fixture repos on Linux and macOS across Python 3.10 through 3.13 and assert the bytes match.

### 7.3 CI capabilities

**Publish.** On merge to `dev`/`qa`/`stage`/`production`, build the artifact and publish per branch to Pages, S3, or an artifact store. Each environment has a live architecture map at a stable URL.

**Diff and comment.** On pull request, rebuild, compare against the base lockfile, and post the architectural impact:

```
Architecture impact of #482

  + 2 components    billing.refunds, billing.webhooks
  + 1 datastore     redis (compose.yml:31)
  ! NEW DEPENDENCY  billing -> auth.internal
                    via billing/refunds.py:44
  + 4 endpoints     POST /refunds, GET /refunds/{id}, ...
  ~ 1 boundary      payments now spans 3 modules (was 2)

  Full artifact: <link>
```

Because the base lockfile is committed, this costs one build per PR, not two.

**Gate.** Policy rules evaluated against the graph, with a non-zero exit on violation:

```yaml
# .svarupa/policy.yaml
rules:
  - forbid: {from: "api/**", to: "db/**"}
    reason: "API must route through the service layer"
  - no-new-cycles: true
  - max-inbound: {module: "auth", limit: 12}
  - require-evidence: strict
```

This is ArchUnit or dependency-cruiser or import-linter, except cross-language and derived from the same graph that draws the diagrams.

### 7.4 CI targets

A published Docker image so any CI runs it in one line with no runtime setup, plus a first-class GitHub Action. GitLab and Jenkins templates in P3.

---

## 8. MCP server

Six tools, so the agent queries the graph instead of grepping the filesystem:

| Tool | Returns |
|---|---|
| `find_symbol(name, kind?)` | Matching nodes with evidence |
| `trace_calls(from, to?, depth?)` | Call chain with evidence at each hop |
| `impact_of_change(symbol)` | Transitive dependents, ranked by distance |
| `explain_module(path)` | Node summary, public surface, in/out edges, community |
| `why_connected(a, b)` | Shortest evidenced path between two nodes |
| `list_entry_points()` | Detected mains, routes, CLI commands, job entries |

Every response carries evidence, so the agent can cite a line rather than paraphrase. `impact_of_change` in particular has no good equivalent in any competing tool.

Stdio transport by default, HTTP optional. Registered via `svarupa setup mcp`.

---

## 9. Distribution and onboarding

### 9.1 Channels

| Audience | Path |
|---|---|
| Humans | `uv tool install svarupa` (PyPI) |
| Agents | `npx skills add <org>/svarupa` |
| CI | `docker run -v $PWD:/repo <image>` |
| Claude plugin marketplace | `.claude-plugin/marketplace.json` |

No npm package. An `npx svarupa` that silently installs Python underneath is surprising in the wrong way and fails behind corporate proxies.

### 9.2 Skill packaging

```
repo/
  skills/
    svarupa/
      SKILL.md          <- exactly this casing
      references/*.md
```

`SKILL.md` must be uppercase. Graphify ships lowercase `skill.md`, which resolves only on case-insensitive macOS; on Linux and in CI, `npx skills add` finds nothing there. Frontmatter requires only `name` and `description`; we also set `license`, `compatibility`, and `metadata.version`.

Because skills.sh has no dependency install hook, `SKILL.md` instructs the agent to check for `svarupa` on PATH and run `uv tool install svarupa` if absent. One bootstrap, first run only.

### 9.3 Target registry

```
svarupa init              # wizard: detect, propose, confirm, execute
svarupa doctor            # what is set up, stale, or broken, and how to fix it
svarupa setup <target>    # non-interactive single target, for scripting
```

Every integration implements one interface:

```python
class Target(ABC):
    def detect(self) -> Status: ...
    def plan(self) -> list[Step]: ...
    def apply(self) -> Result: ...
    def verify(self) -> bool: ...
```

Targets: `skill`, `ci_github`, `ci_gitlab`, `ci_docker`, `hook`, `mcp_register`, `plugin`.

Adding an integration later is one file implementing four methods. `init` is `detect()` across the registry rendered as a checklist.

**Critical scaling decision: we do not write agent installers.** Vercel's skills CLI already maintains 77 platform integrations and absorbs the maintenance when a platform moves its directory. Our `skill` target shells out to `npx skills add`. Graphify hand-maintains roughly twenty installers; that is a treadmill we decline to step onto.

**Ownership split:**

| We own | We delegate |
|---|---|
| Analysis, derivation, layout, viewer | Agent skill installation (77 platforms) |
| Lockfile, diff engine, policy gates | Plugin marketplace discovery |
| MCP server | |
| CI templates (data files, not code) | |

---

## 10. Caching and freshness

Content-hash keyed AST cache, namespaced by `{tool_version}-{grammar_versions}-{schema}`. Stale namespaces are cleaned on first use.

- `svarupa .` full build
- `svarupa --update` re-extract only files whose content hash changed
- `svarupa hook install` optional post-commit hook for automatic refresh

A file manifest tracks content hashes and mtimes. Cache entries are never trusted across a version namespace change, because extractor output is a function of extractor code.

---

## 11. Error handling

Every failure produces a structured diagnostic, never a bare traceback:

```python
@dataclass(frozen=True)
class Diagnostic:
    code: str                  # stable, e.g. SVA-E-014
    severity: Severity         # ERROR | WARNING | INFO
    message: str
    subject: str | None        # node id, edge, or file path
    evidence: tuple[Evidence, ...]
    suggested_fixes: tuple[str, ...]
```

`--json` emits these machine-readably so the agent consumes codes and suggested fixes rather than parsing prose.

| Situation | Behavior |
|---|---|
| Node or edge without evidence | ERROR, build fails, names the extractor |
| Unresolvable dynamic dispatch | INFO, edge omitted, recorded in report as a known gap |
| Deriver finds nothing applicable | INFO, no diagram generated, report says why |
| Geometry validation failure | ERROR, build fails with the offending elements |
| Stale refinement reference | WARNING, names the entry, continues |
| Lockfile stamp mismatch | ERROR, refuses to diff, instructs regeneration |
| LLM returns an element not in its input | WARNING, element dropped, logged |
| Policy rule violated | ERROR, non-zero exit, names rule and violating path |

---

## 12. Testing

| Layer | Approach |
|---|---|
| Extractors | Fixture file per language and framework, asserted against a golden node/edge set |
| Evidence | Property test: every emitted element has non-empty evidence, and every evidence range exists in the file |
| Build | Dedup and merge invariants |
| Cluster | Determinism: same input yields identical communities across 100 runs and both platforms |
| Derivers | Golden `DiagramSpec` per fixture repo |
| Layout | Geometry invariants: no overlap, all coordinates finite, all nodes in bounds |
| Lockfile | **Byte-identical across Linux/macOS and Python 3.10-3.13** in our own CI |
| Diff | Known base/head pairs yield expected deltas |
| Policy | Rule fixtures with expected pass/fail and exit codes |
| MCP | Tool contract tests against a fixture graph |
| Targets | `detect`/`plan`/`apply`/`verify` against temp directories |
| End to end | Real open-source repos per language, asserting the build succeeds and the artifact opens |

The determinism suite is the highest-value test in the project. If it regresses, the CI pillar is worthless.

---

## 13. Phasing

Each phase is a shippable release, so real usage informs the next.

### Phase 1 (~3 weeks): prove the loop
- Python, TypeScript/JS, SQL extraction
- Config parsers: compose, package manifests, SQL DDL
- Graph build with fail-closed evidence
- Leiden clustering with the determinism contract
- Derivers: architecture, module deps, ERD
- Layout: clustered, layered, grid
- Viewer: overview plus tabs, evidence links, graph explorer
- **Lockfile format, canonical serializer, and diff engine**
- MCP server with all six tools
- `init` / `doctor` / `setup skill`

The lockfile format ships in P1 deliberately. Retrofitting a stable serialization format after the graph schema has settled is painful, and everything in P2 and P3 depends on it.

### Phase 2 (~3 weeks): widen
- Go, Rust, Java extraction
- Framework detection across all six languages
- Config parsers: k8s, terraform, OpenAPI, CI
- Derivers: sequence, API surface, deploy topology
- GitHub Action, PR comment bot, per-branch publishing
- Docker image
- Hierarchical zoom drill-down in the viewer

### Phase 3 (~2 weeks): interpret and gate
- Derivers: lifecycle, workflow, class hierarchy
- Constrained LLM naming pass
- `policy.yaml` rule engine and gate exit codes
- `--bundle`
- GitLab and Jenkins templates
- Claude plugin marketplace manifest
- Documentation

---

## 14. Open items

| Item | Status |
|---|---|
| **License** | AGPL-3.0 provisionally. Revisit before public release. Many companies ban AGPL by policy, and that ban typically extends to build tooling, which is in tension with CI being the central pillar |
| **GitHub org** | Deferred. `svarupa` user handle is taken; an org name is needed |
| **Name variants** | Register `swarupa` on PyPI as an alias and secure both `.dev` domains, to mitigate the `sv-`/`sw-` spelling split |
| **Trademark** | Web-evidence clearance only. Run a formal USPTO/TESS search before public launch |

---

## 15. Design decisions worth recording

| Decision | Rationale |
|---|---|
| Fail-closed evidence on nodes **and** edges | The entire differentiator. A relaxation here makes the output indistinguishable from a hand-authored diagram in terms of trust |
| Derived then refined, not agent-authored | Agent-authored output is non-reproducible, which would make the CI pillar impossible |
| LLM may arrange but never introduce | Preserves fail-closed while allowing readable names |
| Committed lockfile over rebuilding both refs | One build per PR instead of two, and architecture changes render natively in GitHub's diff view |
| Hierarchical zoom always on | Avoids the hairball failure mode that every competitor exhibits at scale, with identical UX at any repo size |
| Per-type layout engines | A sequence diagram laid out by a DAG algorithm does not look like a sequence diagram |
| Delegate agent installation to skills.sh | 77 platforms with zero maintenance instead of roughly twenty on a treadmill |
| Python core with `uv` bootstrap | Mature graph ecosystem, real Leiden. Cost is one bootstrap step and a schema duplicated between analyzer and viewer |
| No npm shim | An `npx` entry point that secretly installs Python is surprising and fails in locked-down environments |
| Lockfile records architecture-level facts only | Keeps it small and stable. Intra-module refactors produce no diff; a new cross-module dependency produces one line |
