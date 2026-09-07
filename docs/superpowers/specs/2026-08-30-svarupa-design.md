# Svarupa: Design

**Date:** 2026-08-30
**Status:** Design approved, pending implementation plan

---

## 1. What this is

Svarupa (स्वरूप, "its own true form") reads a codebase and produces a **verified** map of it: a queryable knowledge graph plus seven types of architecture diagram, delivered as one interactive HTML artifact.

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
| `cluster` | Seeded community detection (Louvain default) over canonically ordered, weighted edges. **Presentation only, never reaches the lockfile** |
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
  cluster.py       seeded community detection; pluggable backend
  derive/
    base.py        Deriver ABC: graph -> DiagramSpec | None
    architecture.py, moduledeps.py, requestflow.py, erd.py, apisurface.py,
    deploy.py, classhier.py
  refine.py        overlay application, constrained LLM naming
  layout/
    base.py        Layout ABC: DiagramSpec -> PositionedSpec
    columnar.py    request flow
    grid.py        ERD
    layered.py     module deps, class hierarchy
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

### 2.3 Dependencies

Deliberately small. This ships as a CLI installed with `uv tool install`
and baked into a CI image pulled on every pipeline run, so install
footprint is an adoption cost, and every dependency is a supply-chain
surface on a tool positioned for enterprise governance.

| Purpose | Choice | License |
|---|---|---|
| Parsing | `tree-sitter` 0.26+, grammar wheels `==`-pinned | MIT |
| Graph | `networkx` | BSD-3-Clause |
| Community detection | `networkx.louvain_communities` | BSD-3-Clause |
| YAML | `pyyaml`, `safe_load` only | MIT |

**Clustering is deliberately not `leidenalg` or `graspologic`.**
Measured in Spike 0: `graspologic` costs 575 MB and 43 transitive
dependencies (matplotlib, pandas, scikit-learn, numba, umap-learn) to
deliver one algorithm. `leidenalg` + `igraph` is 19 MB and 6
dependencies but is **GPL**, which would foreclose the license revisit
§14 explicitly plans. `networkx.louvain_communities` is already present,
adds nothing, is BSD, and scored an identical ARI of 1.000 on the
benchmark. `leidenalg` remains available as an opt-in extra.

This is safe **only because clustering is presentation-only**. If
communities still fed the lockfile, algorithm quality would be a
correctness concern rather than an aesthetic one.

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

**Function-level sequence diagrams were cut after measurement, not speculation.** Spike 0b measured call-edge resolution across five repos. Libraries resolve at ~60%; **services resolve at ~20%**, with median call-chain depth of 0-1. The cause is a single dominant shape: `something.method()` where the receiver is a local variable, which is 730 of 1,323 call sites in one subject and resolves at 5.2%. Type annotations do not rescue it: annotation coverage is 97-100%, but the types name external classes (`Session`, `AsyncClient`, `Redis`), so perfect inference would recover 13-19 receivers against hundreds of unresolved sites. Service code is mostly glue over frameworks, so its intra-repo call graph is genuinely thin, and no inference recovers edges that are not there.

**Request Flow replaces it**, derived from resolved imports plus route decorators plus config. On the same repos where call chains reach depth 0-1, import chains reach **depth 3-10**. This is also the better product: the question people ask on joining a codebase is "what does this endpoint touch", which is a module-level question best answered with module-level data.

`self.method()` resolution measured 90-100% via MRO walking and stays in the graph. It is simply too rare in service code to carry a diagram on its own.

### 5.1 Hierarchical zoom

**Diagrams are always generated at the community level, regardless of repo size.** The top view targets about twelve boxes. Each box carries a `children` reference to its own sub-diagram, derived the same way one level down. The full graph is never rendered whole.

Small repos degrade gracefully rather than specially: when a community contains few enough nodes that its sub-diagram would restate the parent box, no `children` reference is emitted and the box is a leaf. A twenty-node project therefore produces one flat diagram through the same code path that produces five levels for a monorepo.

This is the answer to the 50,000-node monorepo. The UX is identical at 500 nodes and 500,000 *for a repository that is one system*, and it avoids the hairball failure that every competing tool exhibits at scale.

**The measured exception, stated rather than glossed.** The cap holds by merging spilled groups into a group they depend on, or failing that into one they share a directory with. A group that is neither connected nor adjacent to anything kept is left as its own box, because going over budget is more honest than asserting a relationship that does not exist. So the cap is not a hard bound, and a directory of *unrelated* projects defeats it: measured, thirty self-contained top-level projects produce **thirty** top-level boxes, with a diagnostic reading `18 kept separate, being neither connected nor adjacent`.

The diagnostic is honest and the diagram is not wrong, but it is also not twelve boxes, and a claim that "the top level never grows" would be false. Two candidates for the real fix, neither chosen yet: a level *above* architecture that treats each unrelated project as a box and drills into its architecture, or refusing to derive one architecture for a path that contains several unrelated roots and saying so. Both are design decisions rather than tuning, so the number is reported truthfully in the interim and this paragraph is the record that the promise has a known boundary.

### 5.2 No LLM in the derivation path

Both derivers that would have required LLM inference (lifecycle and
workflow) are cut from v1. **Nothing in the analysis or derivation
pipeline calls a model.** The whole build is deterministic and needs no
API key.

The only place a model may participate is rewriting the Overview
narrative when the tool is driven through the agent skill, and that
output lands in `refinements.yaml`, never in the lockfile path (see
§7.2). Cutting the two weakest diagram types also removed the
nondeterminism quarantine problem, the prompt-injection surface that CI
execution on untrusted fork PRs would have created, and the API-key
dependency. One cut removed five problems.

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
| Request Flow | Columnar. Participants are columns, hops are rows |
| ERD | Grid with foreign-key-aware locality nudging |
| Module deps, class hierarchy | Layered DAG by topological depth, row-packed |
| Architecture, deploy topology | Clustered layered, communities as bands, nested boundaries |
| API surface | Grouped list-tree by resource path |

Layout runs in Python at build time and writes coordinates into the diagram JSON. Geometry validation (node overlap, out-of-bounds, edge-through-node crossings, label fit) runs immediately after and fails the build on error, following Archify's proven model.

### 6.2 Viewer

`index.html` **contains** the SVG, written in Python from pre-positioned specs. This inverts what this section originally said (embed a JS rendering engine, lazy-load the JSON), and the reason is that layout already computed every coordinate, so there is nothing left for a browser to calculate. Three consequences, each worth more than the lazy loading: the diagram renders with JavaScript disabled; no repository-derived text is turned into markup in the browser, so the escaping guarantee lives in one function in one language; and the artifact is comparable as bytes without a headless browser. JS adds tab switching, drill-down and the evidence panel, and builds DOM with `textContent`, never `innerHTML`.

**One place repository text does reach the browser**, and it needs its own control: a citation path is joined into an `href`. HTML escaping is the wrong escaping for a URL, and a directory named `javascript:...` supplies the scheme itself. Measured in a real browser: it reached `a.href`, and was non-executable only because the appended `#L<line>` failed to parse, which is an accident rather than a control. The scheme is therefore allow-listed at the sink (`http`, `https`, `file`), and a citation that cannot produce a safe URL is rendered as plain text with its path still visible, because withholding the link must not withhold the evidence.

**Navigation:** lands on a generated **Overview** that reads like a briefing with diagrams embedded inline at the points they explain something. Tabs give direct access to each full diagram. A graph explorer tab exposes the full knowledge graph.

**Cross-linking is the point.** Click any box in any diagram and you can jump to that node in the graph explorer, or straight to the source line that proves it. The evidence chain is navigable, not decorative.

**Overview text:** the CLI always emits a templated overview from real graph facts, so a standalone user with no API key gets something useful. When run through the agent skill, the agent rewrites it as prose and saves it into `refinements.yaml`, where it survives rebuilds.

**Phasing of this section, recorded rather than left to inference.** P1-6 shipped the tabs, the inline SVG, the drill-down and evidence click-through, and `REPORT.md`. The following are **deferred, not dropped**, and review #9 was right to call their silent absence out: the in-viewer generated **Overview** (P2 — `REPORT.md` carries the same facts in the interim, but the briefing belongs in the viewer as described above), the **staleness header** (P2, since it needs the git integration the PR bot also needs), the **graph explorer** tab (P2, always), `--bundle` (P3), and `refinements.yaml` (P2, with `refine`). Anything in this section not on that list is either built or a bug.

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
# schema 1.0
# grammars python@0.25.0 typescript@0.23.2
datastore	postgres
dep	api	auth
dep	api	billing
dep	billing	auth
endpoint	POST /refunds	billing.refunds
module	.
module	api
module	billing
```

Records are sorted by `(kind, fields...)` in codepoint order, so `dep` precedes
`module` and `datastore` precedes both. An earlier version of this example
listed the kinds in a reading order the serializer does not produce.

The repository root is spelled `.`, not the empty string its module id uses.
An empty trailing field renders as a line whose entire meaning is trailing
whitespace, and the ecosystem's default tooling, `trailing-whitespace` hooks
and editor trim-on-save, deletes it silently, after which the file refuses to
parse. A committed plain-text format is designed against its environment, not
only against its own parser.

A `module` record is emitted only for a directory holding source this build can
extract. A directory of YAML is configuration, not a module: nothing in it can
produce a `dep`, so a `module` line for it is pure churn surface. Configuration
enters through the kinds built for it, `datastore`, `endpoint`, `service` and
`queue`.

It records **architecture-level facts only**: modules and their dependencies, endpoints, datastores, queues, service topology, and public type surfaces. Not every function. It stays small and stable so that a refactor inside a module produces no diff, while a new cross-module dependency produces exactly one line.

**No line numbers, ever.** Line numbers are the most volatile data in the system: any edit above a record shifts them and churns the lockfile, which directly contradicts the stability guarantee above. Evidence lives in `graph.json`, which is regenerated rather than committed. The PR comment bot joins the lockfile delta against the fresh head graph to display evidence lines, so nothing is lost from the reviewer's view.

**One fact per line.** Dependencies are never aggregated onto a shared line. `module api -> auth, billing` would render an added dependency as one removed line plus one added line, forcing the reviewer to eyeball-diff a comma list, which breaks the promise this format exists to keep.

#### Grammar

- One record per line. First tab-separated field is the record **kind**.
- Fields separated by **TAB**. Within a field, `\t`, `\n`, and `\\` are backslash-escaped. Tab is chosen because it cannot appear unescaped in a path or an endpoint template, unlike space.
- Lines beginning `#` are header or comment. The header carries tool version, schema version, and exact grammar versions.
- Records sorted by `(kind, fields...)` using codepoint order.

#### Evolution policy

- **Additive** (a new record kind): bumps the schema **minor**. Parsers **must skip unknown kinds**, so an old lockfile still diffs against a new one. Unknown-kind lines diff as opaque adds and removes.
- **Breaking** (field shape of an existing kind changes): bumps the schema **major**, and diffing refuses with an instruction to regenerate.

Without this, the P2 release that introduces `endpoint` and `datastore` records becomes a flag day for every early adopter, which is precisely the moment they are lost.

#### Collisions

Two module keys that are distinct on disk but identical after NFC normalization (or after case-folding on a case-insensitive filesystem) **must raise a diagnostic, never silently merge**. Normalization and case-insensitivity are separate axes and both are checked.

The full `graph.json` and HTML stay gitignored and regenerate on demand.

### 7.2 Determinism contract

The lockfile must be byte-identical on a developer laptop and in CI, or every PR shows changes that did not happen.

- Every collection canonically sorted before serialization, by codepoint (never `locale.strxfrm`)
- Grammar versions pinned with `==`, recorded in the header. A grammar patch release can change node structure and therefore extraction output
- Paths always repo-relative, posix separators, **NFC-normalized at the `detect` boundary**
- No timestamps, no absolute paths, no iteration-order dependence, no locale-dependent formatting
- Coordinates quantized to integers; no float formatting reaches any serialized artifact
- Tool version and schema version stamped in the header
- **Stamp mismatch refuses to diff** and instructs the user to regenerate, rather than reporting spurious changes
- **LLM output is banned from the lockfile path entirely.** It may reach diagrams and `refinements.yaml` only. Otherwise this contract is void whenever a naming pass runs

**On Unicode normalization.** The hazard is real but not for the commonly cited reason. HFS+ normalized filenames to NFD on write; **APFS preserves whatever bytes it is given** and is merely normalization-*insensitive* on lookup (verified directly in Spike 0: a path created with explicit NFD bytes came back non-normalized). So the form a build sees depends on whichever tool created the file, what git stored, and git's own `core.precomposeunicode` (default true in Apple-shipped git, absent on Linux). Two developers can hold byte-different paths for the same logical filename. Recording the true mechanism matters, because a guard justified by a wrong mechanism gets deleted by whoever later discovers that APFS does not normalize.

Enforced by our own CI: build the same fixture repos on Linux and macOS across Python 3.10 through 3.13 and assert the bytes match. The suite varies `PYTHONHASHSEED` across runs so any unsorted-set leak fails immediately, sets `LC_ALL=C`, and includes a **git-checkout** fixture containing an NFD-named path (not an `os.mkdir`-created one, which would test the wrong layer).

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
| `trace_dependencies(from, to?, depth?)` | Import path with evidence at each hop |
| `impact_of_change(symbol)` | Transitive **importers**, ranked by distance |
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

`SKILL.md` must be uppercase. Graphify ships lowercase `skill.md`, which resolves only on case-insensitive macOS; on Linux and in CI, `npx skills add` finds nothing there. Frontmatter requires only `name` and `description`.

**Amended at P1-8 (review #12 S4):** frontmatter ships as `name` + `description` only; `license`, `compatibility`, `metadata.version` and `references/*.md` are deferred to P2 with the marketplace work they serve. The skill body is generated from a constant in the wheel (`svarupa/setup/skill.py`); the repo copy is byte-equality-tested against it, and every `--flag` it names is tested against the live parsers.

Because skills.sh has no dependency install hook, `SKILL.md` instructs the agent to check for `svarupa` on PATH and install it if absent. **The `svarupa` name is not yet registered on PyPI, and P1-8 shipped documents that reference it: registering the name is now a blocking pre-release item, not a nice-to-have (review #12 F3).** Until then the skill points at a source-checkout install, and the generated CI workflow pins `svarupa==<generating version>` so a schema bump can never break adopters' CI overnight.

### 9.3 Target registry

```
svarupa init              # wizard: detect, propose, confirm, execute
svarupa doctor            # what is set up, stale, or broken, and how to fix it
svarupa setup <target>    # non-interactive single target, for scripting
```

Every integration implements one interface.

**Amended at P1-8 (review #12 S4): the shipped `Target` is narrower than the one first designed here, deliberately.**

```python
class Target(ABC):          # shipped in P1-8 (svarupa/setup/base.py)
    def files(self) -> tuple[tuple[str, str], ...]: ...   # (relative path, exact content)
    def next_steps(self) -> tuple[str, ...]: ...
```

The original `detect/plan/apply/verify` quartet exists to serve `init` and `doctor`, which are P2. With two targets and no wizard, the four methods would be three trivial wrappers around one file-write, and the interesting guarantees live in the single `install()` function instead: every collision checked before any write, symlinks refused and never followed, `--force` meaning "replace your file, never follow your link". When `init`/`doctor` arrive in P2, `detect` and `verify` grow on top of `files()` (a target is installed iff its files exist byte-equal), which is a strictly easier contract than keeping four hand-written methods honest per target.

Targets shipped in P1: `skill`, `ci_github`. Remaining (`ci_gitlab`, `ci_docker`, `hook`, `mcp_register`, `plugin`) are P2/P3 with the wizard.

**Scaling decision, narrowed rather than dropped:** the original text said the `skill` target shells out to `npx skills add` so we never maintain platform installers. P1-8 ships a direct write to `.claude/skills/svarupa/SKILL.md` instead: one platform, the one the tool is developed against, with no `npx`/network dependency inside a filesystem-only command. The 77-platform delegation to skills.sh stands for P2, where multi-platform installation is actually wanted; hand-maintaining installers per platform remains declined.

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
Re-scoped after the design review found P1 was three or four parallel
workstreams rather than one.

### Phase 1 (~4 weeks): the spine
- Python, TypeScript/JS, SQL extraction
- Config parsers: compose, package manifests, SQL DDL
- Graph build with fail-closed evidence and structural module identity
- Seeded clustering (Louvain default) with the determinism contract
- Derivers: architecture, module deps, ERD
- Layout: clustered, layered, grid
- Minimal viewer: tabs plus evidence click-through. **No graph explorer**
- **Lockfile grammar, canonical serializer, and diff engine**
- `Target` ABC plus `setup skill` and `setup ci_github` as plain commands
- Call-resolution gate: **done, Spike 0b.** Function-level sequence cut;
  Request Flow (import + route + config derived) replaces it in P2

The lockfile format ships in P1 deliberately. Retrofitting a stable
serialization format after the graph schema has settled is painful, and
everything in P2 and P3 depends on it.

### Phase 2 (~3 weeks): widen and ship CI
- Go extraction
- Framework detection across Python, TypeScript, Go
- Config parsers: k8s, terraform, OpenAPI, CI
- Derivers: request flow, API surface, deploy topology
- **MCP server with all six tools**
- GitHub Action, PR comment bot, per-branch publishing
- Docker image
- **Graph explorer tab**, hierarchical zoom drill-down
- **`init` / `doctor` wizard**

### Phase 3 (~3 weeks): complete and gate
- Rust and Java extraction. Spring DI and AOP proxy resolution is the
  hardest environment in the matrix and gets dedicated time
- Deriver: class hierarchy
- `policy.yaml` rule engine and gate exit codes
- `--bundle`
- GitLab and Jenkins templates
- Claude plugin marketplace manifest
- Documentation

### Cut from v1

**Lifecycle and workflow derivers.** Both were "medium, LLM-assisted" by
this document's own assessment, and cutting them also removes the LLM
naming pass, its nondeterminism quarantine problem, its prompt-injection
surface, and the API-key dependency. One cut removes five problems. Nine
diagram types is a brochure number; **seven excellent ones beat nine
where two are mediocre.** Revisit post-v1 if users ask.

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
| Per-type layout engines | A columnar flow diagram laid out by a DAG algorithm does not look like a flow diagram; an ERD laid out that way ignores foreign-key locality |
| Delegate agent installation to skills.sh | 77 platforms with zero maintenance instead of roughly twenty on a treadmill |
| Python core with `uv` bootstrap | Mature graph and parsing ecosystem. Cost is one bootstrap step and a schema duplicated between analyzer and viewer |
| No npm shim | An `npx` entry point that secretly installs Python is surprising and fails in locked-down environments |
| Lockfile records architecture-level facts only | Keeps it small and stable. Intra-module refactors produce no diff; a new cross-module dependency produces one line |

### 15.1 Promoted from review (reasoning inherited by later components)

Accepted findings are recorded here with their reasoning so subsequent reviewers
inherit the rationale instead of relitigating it. Source review in parentheses.

| Decision | Reasoning |
|---|---|
| **Leiden/Louvain communities may never be committed identity** (design review F2) | Community detection is chaotically sensitive to input perturbation, not merely nondeterministic. Determinism means same input, same output; **a PR changes the input.** Measured in Spike 0: one added import flipped 3 of 8 community assignments in flask (25-38% across subjects). Structural identity churned 0 lines on the same edit. Identity must come from what developers declare (directories, packages, workspace members), which is the only cross-language notion that is both stable and meaningful to a human reading a diff |
| **Lockfile carries no line numbers** (F1) | Line numbers are the most volatile data in the system and buy nothing in the diff, since the engine can recover them from the head build. Stability of the committed artifact outranks convenience of the raw `git diff` view |
| **Fail-closed applies to evidence, not to recall** (F3) | Any edge we emit trivially has *some* source location, so fail-closed alone does not make the graph useful. The real question is how many true edges survive. Candidate edges (N impls yields N edges, each with call-site and candidate-definition evidence) preserve click-through-to-source, which is the actual contract, while restoring the recall that Go implicit interfaces, Spring DI, and Python dynamic dispatch would otherwise destroy |
| **Resolution scorecard needs three bins, not two** (review #1 R2-3) | Counting every resolution failure as "external" launders resolver bugs into a number that looks like honesty. Bins are resolved / known-external (matches stdlib or a declared dependency) / **unresolved-unknown**, and the third is the number the report surfaces. Declared dependencies are already parsed by the config extractors, so the classification is nearly free |
| **Resolution targets symbols, with a file-level fallback tier** (R2-4) | Directory granularity loses every intra-package edge: `src/requests/api.py` importing `.models` is intra-module at that granularity, which scored requests **0 of 221** imports resolved. File level fixed that (83 of 221), but file is the right *substrate* and the wrong terminal unit: `from flask import Flask` resolves at file level to `__init__.py` while the definition lives in `app.py`, so `impact_of_change` would be wrong for most of a well-packaged library's public API. Re-export chains must be chased |
| **Clustering defaults to `networkx.louvain_communities` (BSD)** (R2-7) | `leidenalg`/`igraph` are GPL, which is compatible with the provisional AGPL but forecloses the two most likely outcomes of the license revisit §14 explicitly plans: relicensing permissive, or dual-licensing. Measured identical quality (ARI 1.000) at zero extra dependencies. Safe **only because F2 made clustering presentation-only**; were communities still feeding the lockfile, algorithm quality would be a correctness concern rather than an aesthetic one. Re-measure at scale in P1-5, since Louvain's known weakness (internally disconnected communities) is untested on a real code graph |
| **Function-level sequence cut; Request Flow replaces it** (Spike 0b) | Measured, not assumed. Call resolution is ~60% on libraries but **~20% on services**, with median call-chain depth 0-1. One shape dominates: `var.method()` is 730 of 1,323 call sites in one subject and resolves at 5.2%. Type annotations do not help (97-100% coverage, but the types name external classes), because service code is glue over frameworks and its intra-repo call graph is genuinely thin. Import chains on the same repos reach depth 3-10. Answering "what does this endpoint touch" completely beats answering "what calls what" badly |
| **Resolution quality varies by language and project kind; the scorecard is per-language** (Spike 0c) | TypeScript resolves imports at 63-96% against Python's 37-47%, and service calls at ~49% against ~20%, because TS module specifiers are explicit and its class idiom carries type information (`this.field.method()` pinned 99-100% on DI-heavy code). But the service-versus-app split reproduces in both languages: services have thin intra-repo call graphs because a service is glue over frameworks. Reporting one blended number would imply a uniformity that does not exist |
| **Never parse JSONC with a regex** (Spike 0c) | tsconfig `paths` values always contain glob patterns like `"@/*": ["src/*"]`, and the `/*` inside that string makes a block-comment regex delete everything through the next `*/`, silently yielding invalid JSON and zero aliases. Measured: one project scored 61.3% import resolution instead of 92.7%. Requires a string-aware scanner. Applies to every JSONC config we read |
| **A committed file's parser must assume hostile input** (review #2 F1-F6) | The riskiest assumption in the lockfile was that `parse` would only ever see bytes `render` produced. That is guaranteed false: the file lives in git, is merged by humans, and is diffed by CI on every PR, so malformed input is the expected case. Consequences: NEWLINE means LF only (`splitlines()` also breaks on VT, FF, NEL, U+2028 and U+2029, all legal in POSIX filenames); the published `kind := [a-z_]+` production is enforced, which is what stops an unresolved conflict marker parsing as architecture; known kinds are arity-checked, so a truncated line cannot become a different fact; unknown escapes and unstamped headers refuse rather than silently canonicalizing or assuming |
| **Constructor validation is a convenience, not the enforcement point** (review #2 F10) | `object.__new__` plus `__setattr__` can build an evidence-free Node that survives pickling, and a subclass can override `__post_init__`. Python cannot prevent this and contorting for it would be worse than the disease. Therefore `build` must re-validate evidence **independently at graph insertion**, rather than relying on a comment that the model already checked |
| **Detection must run on raw identities, before normalization** (review #2 F3) | Once `norm_path` has run, the pre-images are gone and there is nothing left to compare. `casefold()` alone does not catch the normalization axis because it does not normalize. Normalization collisions and case collisions are reported separately because the remedy differs |
| **A platform-conditional test is unverified until CI proves it** (review #3 F1) | A collision test was shipped that skipped on case-insensitive APFS, the only platform it ran on, and asserted a diagnostic on input that returned nothing. It would have been red on every Linux job. Prefer restructuring so the assertion is platform-independent (component-wise rather than on-disk); where an on-disk leg is genuinely needed, keep it *in addition to*, never *instead of* |
| **Parse manifests, never grep them** (review #3 F3) | Substring matching produced verified false workspace roots: `[tool.poetry.group` appears in essentially every modern Poetry project, `[workspace]` inside a Cargo comment, `"workspaces"` inside a package.json keywords array. Workspaces are the source of structural module identity, so a false root is a false module boundary in the lockfile. Same lesson as never parsing JSONC with a regex, applied to the files that decide structure |
| **Partial failure must degrade, not disable** (review #3 F2) | Compiling an ignore-pattern list all at once meant one malformed line raised, left the spec empty, and silently applied no patterns at all, letting ignored files and possibly secrets into the graph. Compile element by element, drop only the bad element, and diagnose it. Dropping one input is recoverable; dropping all of them silently is not |
| **A known divergence gets a strict xfail, not silence** (review #3 F5) | Where behaviour genuinely differs from an authority a user trusts (git's view of their repo), the gap is recorded as a failing-by-design oracle test that turns red the day it is fixed. Silent divergence in a tool that diffs architecture per PR is the failure mode the product exists to prevent |
| **Excluding a file type starves whatever derives from it** (review #3 F8) | `migrations/` classified as generated would have made the ERD deriver honestly return `None` on repos where Django or Alembic migrations are the only DDL, which design §4.3 names the ERD source of truth. Check every exclusion against the derivers that consume that input |
| **A wrong edge is worse than a missing one** (review #4 F4) | `UNRESOLVED` is always an acceptable fallback; a confident wrong `RESOLVED` never is. Three constructions produced them: a bare call binding an instance method (which Python cannot dispatch, so the pick was impossible rather than merely arbitrary), MRO resolving base names repo-wide so a class bound to an unrelated same-named class in another file, and import roots sorted by name length letting a decoy tree beat the importer's own ancestor. When a target is genuinely ambiguous, that is a candidate or an unresolved, never a silent first-pick |
| **Every resolver test needs a decoy** (review #4) | A fixture with exactly one plausible target cannot distinguish correct resolution from first-match-wins. Thirty-three tests passed while five wrong-resolution bugs were live, purely because no fixture ever contained a second candidate. Every name in a resolver fixture should exist at least twice, and the assertion should name the specific expected target |
| **Pass 2 must never re-derive a pass 1 parse fact** (review #4 F3) | The relative-import level was recomputed as `spec.count(".")`, which disagrees with the leading-dot count for any multi-segment relative import and anchored resolution at the wrong directory. A second parser can disagree with the first, and here it did. Pass 1 owns the parse; pass 2 carries the result |
| **Every reference lands in exactly one bin** (review #4 F2, F6) | External symbol imports fell through a `continue` and were counted nowhere, so the references denominator silently excluded every symbol imported from a framework. Separately, unknown base classes were recorded external unconditionally. Both re-collapse the three-bin scorecard into the two-bin design it was built to replace, and an unconditioned `EXTERNAL` path is the specific shape to watch for |
| **Repeated edge keys carry distinct evidence** (review #4, contested finding) | Two import lines to the same target share an `(src, dst, kind)` key but name different lines. Of 135 repeated keys on a real repo, zero had identical evidence across copies. `build` must union evidence on merge; deduping by key would silently discard provenance |
| **A promoted decision applies to every new surface, not to the examples that produced it** (review #5) | "Every resolver test needs a decoy" was in the decision log, and was then applied only to the three cases the previous review had named. Aliases, workspace packages, field conflicts and star re-exports shipped decoy-free, and all four hid a confident wrong edge while every test passed. A rule applied only to the instances someone else identified is not a discipline |
| **Path arithmetic needs path operations** (review #5 F2) | `lstrip("./")` is a character-set operation. Applied to an alias target, `../shared/src/*` declared in `packages/web` became `packages/web/shared/src`, which resolved into whatever decoy happened to sit at the mangled path. Use `posixpath.normpath` anchored at the declaring file, and diagnose targets that escape the repo rather than silently trimming them |
| **Configuration is scoped to what declares it** (review #5 F1) | Merging every tsconfig's `paths` into one namespace let two apps declaring the standard `@/*` collide, so an import inside one resolved into the other, with the winner decided by filesystem enumeration order. Scope every declared mapping to its declaring directory and apply it only to files beneath that directory |
| **A guard with an escape hatch is not a guard** (review #5 F3) | Restricting package-name claims to workspace members was written as `if members and holder not in members`, so a workspace declaring members that do not exist yet permitted *everything*. The safe default for an empty allow-list is to allow nothing |
| **The scorecard measures pinning, not correctness** (review #5) | A wrong edge counts as RESOLVED, so no percentage can distinguish a correct resolution from a confident wrong one. Only decoy fixtures and spot-audits of real-repo edges against known targets can. A rising number is never on its own evidence that resolution improved |
| **A promoted decision is a checklist for every new component** (review #6) | Two consecutive reviews found a promoted decision applied only to the code that produced it. "Path arithmetic needs path operations" was fixed in TypeScript alias targets and then written as `lstrip("./")` in the next component's `go.work` parser, re-anchoring an escaping path onto an in-repo decoy. Before a component ships, walk §15.1 and check each entry against it |
| **A feature whose data structure cannot express it is dead code** (review #6 F4) | Clustering weights were counted from `module_deps`, a deduplicated set of pairs, so twenty imports between two modules scored identically to one — while the docstring claimed the opposite. The test asserted only that a `weight` key existed, which is true of a constant. When a docstring makes a quantitative claim, a test must construct the case where the quantity changes the answer |
| **Attributes with per-relationship meaning need explicit merge rules** (review #6 F3) | Merging edges by copying one edge's attrs let a `type_only` site label a whole relationship type-only, and impact analysis is contracted to skip those, so a real runtime dependency would vanish from the answer. `type_only` merges as "one runtime site wins"; any attribute whose meaning is about the relationship rather than the site needs a stated rule |
| **A fix must not be worse than the problem** (review #6 F6) | Splitting an oversized community shattered a 20-clique into 20 singletons, turning a three-box diagram into twenty-two. Reject a degenerate result and keep the original, with a diagnostic, rather than accepting any change that satisfies the trigger |
| **A gate must be able to fail** (R2-1) | Spike 0's headline result was tautological: its lockfile is a pure function of file paths and imports, and three of four perturbations could not touch either input, so 0 churn was guaranteed by construction. No-op perturbations also scored as passes. Every future gate asserts it actually mutated state and fails the run otherwise, prefers replayed real PRs over synthetic string edits, and reports partition distance (1 − ARI) rather than label distance |
| **A verification claim is scoped to the shapes the fixture contained** (review #7 F1) | Commit `58ac99d` stated "edge weight is truthful, five real import statements yields weight five (verified)". True of the fixture, false in general: `from ..lib import mod_a, mod_b` emits three IMPORTS edges (package `__init__` plus one per named submodule) carrying the *same single* Evidence, so an arrow read "3 imports" over one clickable line. The fixture only contained one-edge-per-statement shapes. Write down which shapes a verification covered, or the untested ones silently become assumed-correct |
| **A number in a commit message is copied from command output in the same session** (review #7, self-inflicted) | The commit fixing the above closed with "343 tests"; the real count was 323 passed, 2 skipped, 2 xfailed. The number came from a glance at a truncated `pytest -q` tail, in the very commit whose finding was about laundering an unverified claim. Recall is not measurement, even for a number that feels too boring to check. **The ordering is the rule:** it recurred one commit later at 335 against 333, because the message was drafted before the suite ran, so the copy went the wrong way. Run the command, read the line, then write the message |
| **An assertion joined by OR across branches tests neither** (review #7 F2) | `assert "depend on" in msg or "shared directory" in msg` passes on either code path, so the test named after the connection tier of spill merging never exercised it. Measured: zero connection merges across the whole suite. Worse, the obvious fix skipped instead, because clustering absorbed the dependent group before capping saw it. Assert the specific branch, assert an observable effect (where the module actually landed) rather than only the diagnostic text, and supply the upstream stage's output by hand when letting it decide would hide the branch |
| **A guarantee with an exemption is not a guarantee** (review #7 F3) | The sub-diagram namespace was called "provably disjoint" from module ids while `ROOT` was the bare string `"root"`, which a top-level `root/` directory collides with. The test enforcing the invariant skipped that exact case with `if spec_key == "root": continue`. An exemption inside the test meant to enforce an invariant is the tell; the special case is where the invariant is most likely to be false |
| **A test whose expectation is computed from the code under test proves nothing** (review #7 T1) | Drillability was asserted as `n.is_drillable is (int(n.attr("modules")) > 1)`, but the same loop sets both from the same `len(members)`. A stub emitting `modules="1"` and `child_spec=None` for everything passed. Expectations come from the fixture, which is the only thing the code under test did not author |
| **Same-process repetition is not a determinism test** (review #7 T2) | Calling `derive` five times on the same graph and clustering objects asserts that Python iterates identical objects identically. The churn sources that actually break byte-identity are cross-process: `PYTHONHASHSEED`, filesystem enumeration order, locale. A determinism test rebuilds from the filesystem, and at least one varies the hash seed in a subprocess, since it is fixed at interpreter start |
| **A conditional guard around an assertion converts a failure into a pass** (review #7 T3, T4) | `if ds.specs: assert <the real check>` is green when the deriver returns nothing, which is the case most worth catching. Assert the precondition, then assert unconditionally. Same family as "a guard with an escape hatch is not a guard", on the test side of the line |
| **A scan over a source tree certifies dead code as alive** (review #8 F1) | A mutation-testing `cp -r` into an existing directory nested two stale copies of the whole layout package inside itself, and they were committed. Svarupa run on itself then reported three phantom modules, and the registry meta-test counted the dead copies as emitters, so deleting a live code emission left it green. The dead-code direction that commit added was defeated by that same commit. Liveness is computed from the import graph, never from the filesystem, and a commit's file list is part of the change under review |
| **A docstring arguing that a check is unnecessary is a check that does not exist** (review #8 F2) | Design §6.1 lists edge-through-node crossings as validated. It was not implemented, and a routing docstring said vertical segments stay in row gaps "which is why the geometry check ... can pass rather than being quietly omitted". Both halves were false: an edge skipping a layer put its horizontal run inside the intervening row, and a two-node cycle ran straight across the target's interior. Implementing the check first showed 80+ crossings on one real view. When a design names a validated invariant, either the validator contains it or the artifact says plainly that it is unchecked; a prose argument from engine behaviour is voided silently by the next engine edit |
| **A check against a pathological value must be a type test, not a range test** (review #8 F4) | A box at `(nan, nan)` passed every geometry check, because `nan < 0`, `nan > width` and every overlap comparison are all `False`. Non-finite was an explicitly listed requirement, and ordered comparisons pass exactly the input it names. `type(v) is int` subsumes it, and moves the byte-identity substrate from a property of the producers to a property of the gate |
| **A label claiming a semantic fact must be computed from that fact** (review #8 F5) | Bands were labelled by row index while claiming a dependency level. Forty boxes all at depth 0 wrapped into four rows and produced "level 1" through "level 4": four confident wrong claims, each geometrically contained so validation said nothing. Wrapping is the built-in divergence between presentation and semantics, so any label derived from presentation becomes false there |
| **A producer-side guard that skips input the validator would reject converts a failure into a pass** (review #8 F6) | `if a is None or b is None: continue` in routing turned an edge with a missing endpoint into a clean canvas, hiding it from the diagnostic code that exists for exactly that case. This is the code-side twin of "a conditional guard around an assertion": the gate never sees the defect, so its own tests cannot help |
| **A parameter read by both producer and checker must be validated at construction** (review #8 F8) | `Style(box_pad_x=-20)` produced a 202px box carrying a 242px label, blessed by the geometry check, because `budget = w - 2 * pad` grew instead of shrinking and both sides read the same corrupt value. Past construction no check is independent of what it co-computes with. The same commit narrated discovering this class via `box_max_width=1` and fixed only that instance |
| **When the exact codepoint is the subject, write the codepoint** (review #8, surviving mutation) | A test asserting that truncation never orphans a combining mark used a literal e-acute, which the editor stored precomposed as U+00E9, so the fixture held no combining marks and the test could not fail. A literal is whatever the editor's normalization decided. Same reason the invisible-character deny-list is written as escapes: a reviewer cannot see U+202E in a diff |
| **A type-level guarantee is only as strong as the narrowest type on its boundary** (review #9 F1) | `markup.py` claimed a bare `str` where `Markup` is expected is a pyright error, while `tag(name, body: object)` and `join(parts: object)` accepted exactly that and spliced it in verbatim. Verified: `tag("p", hostile_str)` type-checked clean and emitted `<p><img src=x onerror=alert(1)></p>`. Safety was resting on every call site remembering `esc`, which is the discipline the types were meant to replace. Widening a security-critical parameter for caller convenience erases the invariant and leaves the docstring claiming it, and a `# type: ignore` on such a boundary is the signature admitting it is wrong |
| **A guarantee the type checker provides needs a test that the type checker complains** (review #9 F1) | Every runtime test was green while the hole was open, because no live call site happened to violate it. Runtime tests cannot distinguish "the types enforce this" from "nothing violates it yet", so the suite now runs pyright on snippets and asserts the specific error, with a passing baseline snippet so the assertions cannot succeed on an unrelated failure |
| **A free-text label describing machine behaviour must be checked against that behaviour** (review #9 F2) | The diagnostic registry was checked in both directions for existence and for contiguity, and neither could catch a description that is about something else. Eleven descriptions shared no content word with the message they label: the whole `SVA-B-*` series was off by several positions, so `SVA-B-001` was documented as "an element reached the graph without evidence" while emitting "two different nodes claim the same id", which described `SVA-B-007`. Written from recall one wave after promoting a decision about that, and one wave after correcting the `SVA-D-*` series for the same reason. The check now parses the literal `message=` at each emission site; one shared content word is a floor, not a proof, and catches only the failure that actually happened, twice |
| **HTML escaping is the wrong escaping for a URL** (review #9 F3) | A directory named `javascript:...` let a citation supply the `href` scheme itself, reaching `a.href` in a real browser and failing to execute only because the appended `#L<line>` did not parse. An accident of an unrelated suffix is not a control. Wherever repository text reaches `href`, `src` or `location`, the scheme is allow-listed at the sink. Note `Markup` means "escaped for HTML" and says nothing about a URL, CSS or unquoted-attribute context, so a new context needs a new escaping function rather than this one |
| **An artifact must be a pure function of the run that wrote it** (review #9 F4) | `mkdir(exist_ok=True)` plus writing only this run's files left a `diagrams/erd.json` from a run where the ERD was drawable byte-for-byte in place after a run where it was not, so an agent reading `diagrams/*.json` would consume a diagram the commit never produced. View ids are repository-derived, so a rename leaves its old view beside the new one. Writing into a directory without owning its whole contents makes the output a function of run history. Ownership is declared and marked rather than assumed, and an unowned non-empty directory is refused, because `--out` takes an arbitrary path and a typo must not be destructive |
| **Liveness comes from the import graph, not from having tests** (review #9 F5) | `json_script` sat in `__all__` with two dedicated tests and two mutation entries and no production caller, left orphaned when the viewer inverted to server-side SVG. A tested-but-uncalled export certifies dead code as alive and pads a mutation count with guarantees no artifact depends on. Same shape as the stale nested package in review #8, on the export surface rather than the filesystem |
| **A mutation suite proves what it enumerates** (review #9) | All twelve mutations were caught, and that was true and beside the point: none widened `tag` back to `object`, none touched a registry description, and none exercised the URL sink or the output directory. A clean run is evidence about the list, not about the component, and a list written by the author of the code inherits the author's blind spots. Prefer mutations aimed at the property a wave is *named* for |
| **A diagnostic states what it detected; the caller owns the sentence about the response** (review #10 F1) | The drift warning told a reviewer the drifted facts "will appear in the delta below as though this change caused them", while the CLI substituted the regenerated base so they did not. The substitution was right and every sentence of guidance was false, printed directly above a correct "No architectural change." Free text that predicts a consumer's behaviour couples a producer to a caller it cannot see, and the next caller-side change silently falsifies it. This is review #9 F2 with the arrow reversed, one wave later |
| **The channel that loads a file is part of its parser's boundary** (review #10 F2) | The lockfile grammar was hardened on the premise that malformed input is the expected case for a committed file. That premise covered the bytes and stopped at the channel: a missing path, a directory, a non-UTF-8 file, and the tool's own designed schema refusal each reached the user as a raw traceback, after the full scan. Absent, unreadable, undecodable and unstamped must all arrive as the same structured refusal a malformed line does, and all of it before the expensive work it gates |
| **Every byte of a committed artifact must be a function of the facts it commits** (review #10 F3) | The lockfile's grammar header was keyed on `file_languages`, which counts every scanned file including the test, generated and vendored roles excluded from every record. Adding one TypeScript *test* file to a pure-Python repository changed the committed file while changing zero facts, which is the churn the filter's own docstring said it existed to prevent. Keying any part of a committed artifact to inputs excluded from its facts reopens the churn channel through the exclusion itself |
| **A committed plain-text format is designed against its environment, not only against its own parser** (review #10 F4) | The repository root's module id is the empty string, which rendered as `module` plus a bare tab: a line whose entire meaning is trailing whitespace. The parser survived its own output, and the environment did not. Measured: after a `trailing-whitespace` hook or an editor trim-on-save, the file refuses with SVA-L-002 and every CI diff fails until someone regenerates. Nearly every repository has root-level files, so this would have hit nearly every adopter. Spelled `.` before adoption made it a major schema bump |
| **A gate that rebuilds its expectation with its own implementation certifies that implementation, not the product** (review #10 F5) | The only gate comparing Linux to macOS bytes stopped at `detect` and computed modules itself, then synthesized dependencies by pairing adjacent names. So `extract`, `resolve`, `build` and `build_lock` never crossed the OS boundary at all, and the gate's own module derivation had already diverged from the product's, emitting an id the real tool could not produce. A gate calls what the shipped path calls, end to end |
| **A previous review's accepted fixes are part of the checklist for the next component** (review #10) | Both MUST-FIX findings were the *immediately preceding* wave's findings, unapplied: a diagnostic predicting its caller (#9 F2) and a refusal arriving after minutes of work (#9 F4, whose fix moved `claim()` to the front of the CLI with a commit message explaining exactly why). Walking §15.1 against the component being written is not sufficient, because the newest decisions are the ones least worn in; the last review's fixes have to be walked against the new component specifically |
| **A parser's exception list is what it raises in practice, not what its docstring names** (review #11 F1) | PyYAML's scanner is recursive, so 20,000 nested flow brackets raised `RecursionError` before the post-parse depth guard could ever run, and one hostile compose file took the whole run down with a raw traceback, one wave after the loading-channel decision was promoted. A new parser of repository bytes inherits the full hostile-input posture on day one, including the failure modes of its library |
| **A truthiness filter over an open-ended field is a claim about every future producer** (review #11 F2) | The lockfile's code-module guard was `if node.lang`, and the compose extractor promptly minted `lang="compose"`, resurrecting the config-directory churn one wave after review #10 F3 removed it. The old guard test stayed green because its fixture was config that produces no nodes. Guards over open-ended fields enumerate what they accept, and their tests use the producing shape, not the absent one |
| **A determinism gate is scoped to scale and to stage** (review #11 F3) | networkx Louvain iterates sets of node names internally, so with `seed=` fixed its output changed across `PYTHONHASHSEED` on the first real repository tried, while every existing gate stayed green on fixtures below the size where aggregation engages. The gate then took three attempts: a synthetic fixture was below scale, and the real reproducer aimed at the first-level pass certified the stage that was never broken; the sensitivity lives in the oversized-community resplit over a subgraph. Fixed by relabelling nodes to sorted consecutive integers; gated by the captured real graph, mutation-verified. When synthesis fails to reproduce, a captured real graph is the fixture of record |
| **When a commit names a property as its point, the checklist question is which test fails if the property is deleted, answered by deleting it** (review #11 F4) | Deleting the entire barycentric sweep, the headline of the visual-rework commit, passed all 539 tests; so did routing every flow skip straight through the intervening columns. The mutation lists had not grown while the codebase grew by 2,500 lines, which is review #9's mutation lesson measured at wave scale |
| **User-facing waves do not accumulate unreviewed** (review #11, process) | Four waves shipped under live user feedback without intermediate review, and every documented recurrence pattern fired inside that gap: the previous wave's channel fix unapplied to the new channel, the previous wave's byte-identity fix reopened by the new producer, and frozen mutation lists. The velocity of a feedback loop is precisely when recurrence is fastest, so each wave gets its review before the next |
| **Distinctness is not spread** (review #11 C3) | The flow-track test asserted that track positions differ, and a mutation that budgeted tracks against the whole diagram survived it: the tracks were distinct and crammed into the gap's left sixth, which collides at density. Assert the quantity the failure mode actually degrades, not a property adjacent to it |
| **The state a product's own instructions create is a test fixture** (review #12 F1) | `next_steps` and SKILL.md prescribe an exact repository state (commit `.svarupa/architecture.lock`, nothing else), and the generated per-PR workflow refused with SVA-E-001 on precisely that state, on every PR, after the full scan; so did every other contributor's first run on a fresh clone. The first end-to-end execution of the workflow happened inside the review. Any instruction the product gives a user defines a state the suite must run the product against |
| **A symlink is a redirection: `--force` means replace your file, never follow your link** (review #12 F2) | `exists()` is False on a dangling symlink, so setup's collision sweep never saw one and the write landed wherever the link pointed, outside the repository, exit 0; with a link to a real file, SVA-S-001's own fix text walked the user into `--force`-overwriting the target. Hostile input extends to a cloned repository's paths, not just its file contents, and a refusal's suggested fix must never escalate the attack it refuses |
| **A shipped install command is a claim about a registry** (review #12 F3) | Both shipped documents said `uv tool install svarupa` while PyPI returned 404 for the name: a failing command today, an open name-squat granting code execution in every adopter's CI tomorrow. Generated CI now pins the generating version, so upgrades are reviewable diffs and a squatter's `latest` is never installed; registering the name is release-blocking the moment any shipped document references it |
| **A document drift check must cover the sentences an agent acts on, not only the tokens it can grep** (review #12 F4) | The wave's thesis was documents checked against reality, and the checks stopped at flags and file names: the exit-code and stderr sentences were written from recall and both were false (exit 1 co-occurs with a usable artifact; diagnostics print to stdout). The sentences that direct an agent's behaviour are the ones that most need the check and are hardest to give one, so they are written minimal and verified by hand against measured behaviour, and rewritten whenever the behaviour moves |
| **Wiring is a component: a property tested only below the CLI leaves the CLI free to negate it** (review #12 F5) | Hardcoding `force=True` in the one CLI call site passed all 576 tests, because every collision test drove `install()` directly. Each user-facing guarantee needs at least one test that enters through the same door the user does |
| **The failing run is the one whose report must survive** (review #12 S3) | Under `set -e` the workflow aborted before writing the job summary exactly when the delta was nonzero or the run errored, so the reviewer-facing surface vanished on precisely the runs a reviewer needed. Capture the output, write the report, then re-raise the exit code |
| **A default is a claim about absence, so presence-but-unknown must be representable** (review #13 F1) | `dec.methods or ("GET",)` could not distinguish "no methods kwarg" (Flask's documented GET default, a fact) from "kwarg present but dynamic" (unknown), and the conflation recorded `endpoint GET /pay` for a POST-only route in a committed file. When a field's absence has a documented meaning, its type needs three states: absent, known, and present-but-unknown |
| **A resolver is anchored, and its test contains a decoy at the wrong anchor** (review #13 F2) | Python entrypoint targets resolved from the repo root, so `packages/a/pyproject.toml` declaring `pkg.cli:main` silently bound to a root-level decoy `pkg/`, zero diagnostics; the JS branch had the manifest anchor all along, three lines away. Root-relative was correct on every single-package fixture and wrong on the first monorepo, which is the decoy lesson (#41) recurring at the anchor rather than the name |
| **Every fact channel inherits every gate, checked per channel** (review #13 F3) | Routes and tasks were gated on architecture eligibility and the manifest loop one function below them was not, so `tests/fixtures/pyproject.toml` minted a committed `entrypoint` record and coloured a production module `cli`, in the same commit whose own suite proved the route half of the gate. A gate exists per channel, and its test uses each channel's shape |
| **A locator must agree with the parser on the value, not only the key** (review #13 S1/S2) | tomllib and json discard positions, forcing line scanners; a scanner keyed on the key alone cited a line inside a multiline string reading `serve = ...` and a `"config"` sibling entry above `"bin"`. Requiring the parsed value on the located line (and brace-scoping JSON to the owning object) is what makes the scanner and the parser one claim instead of two |
| **A tool upgrade is not a code change** (review #13 S3) | Diffing a 1.1-stamped base against a 1.2 head attributed every pre-existing route and role to the first PR after the upgrade, and drift called the base "out of date with the code". Schema minors are recorded in both headers exactly so the delta can attribute new-kind lines to the upgrade (SVA-L-013, mirroring the grammar-version rule); recording a version and not comparing it leaves precisely the ambiguity it was recorded to remove |
| **Blindness is stated where absence would be read** (review #13 S4) | An Express repo produced zero routes, zero roles and zero signal while the README pitches endpoints as the product experience: a JS adopter could not tell "no API" from "cannot see APIs". REPORT.md now states the semantics language boundary per run, in the artifact the reader is actually looking at |
| **A promoted three-state lesson is walked against every field of the same shape, in the same commit** (review #14 F1) | `methods` got absent/known/present-but-dynamic and `DecoratorRef.arg` did not, one field over, one commit later: `@Get(PATH)` composed a committed `endpoint GET /users` for a route living at `/users/:id`, byte-identical to a legitimate sibling record. When a decision is promoted for one field, the same commit's checklist item is every other field with the same absent/dynamic ambiguity |
| **The schema minor tracks what this build can emit, not only the kind list** (review #14 F2) | Express/NestJS coverage grew inside the existing `endpoint` kind with no minor bump, so SVA-L-013 could not fire and six pre-existing endpoints were attributed to the first PR after the tool upgrade, one commit after "a tool upgrade is not a code change" was promoted. The delta's upgrade attribution keys on the minor, so the minor moves whenever emission coverage does |
| **A dialect is a coverage boundary** (review #14 F3) | `const express = require('express')`, the majority spelling of real Express programs, was invisible to an import gate that read only ESM, while REPORT.md's boundary sentence said express was covered. The overclaim lived in the very sentence added to state boundaries honestly; require() is now an import in pass 1, and the boundary names both dialects |
| **A name is not an object** (review #14 S1) | The receiver gate held any identifier once bound by an express constructor anywhere in the file, so a helper's `const app = makeCache()` shared the top-level `app` and its `.get('/decoy')` was a wrong committed edge, under the commit message's own claim that the gate was on the object. Names bound by any foreign constructor leave the set; losing a true route to a collision is the cheap direction |
| **String extraction keeps source spelling; dropping unrecognized child nodes corrupts values** (review #14 S2) | Joining only `string_fragment` children deleted escape sequences: `'/a\'b'` became `/ab`, a corrupted committed value that let two distinct routes collide onto one lock key. A joiner that enumerates node types must keep the source text of the rest or fail closed, never silently delete |
| **Where one framework composes and another does not, the artifact says so** (review #14 S4) | NestJS controller prefixes compose (same file, both cited); Express mounts and FastAPI include_router prefixes do not, so their paths are handler-relative. Unstated, a reader compares a full Nest URL against a mount-relative Express path as if they were the same convention; SKILL.md now states the asymmetry |
| **A change in how a fact is spelled is a change in what the build emits** (review #15 F1) | Mount composition re-spelled existing endpoint records (`GET /things` became `GET /api/things`), which a diff reads as one removal and one addition of an OLD kind; the minor had moved for new kinds and new producers but not for new spellings, so SVA-L-013 stayed silent one commit after its rule was written at the constant, and its sentence covered only "newer kinds". The minor tracks any difference between what the previous build and this one write for the same code, and the attribution sentence says newly emits, no longer emits, or spells differently |
| **When a name-keyed approximation gains a composing consumer, its failure flips from duplicate to wrong** (review #15 F2) | Receiver sets keyed on identifiers were harmless while every object received its declared path; composition made the top-level `router` and a factory's inner `router` one key and committed `GET /users/healthz` for a route served at `/healthz`. A name bound by the framework's constructor more than once in a file is refused at the composing consumer: it neither mounts nor is mounted |
| **Resolution caps are properties of the query path and must not be memoized as facts about nodes** (review #15 S1) | A depth cap that stored None on every router it passed made composition depend on which mount statement was met first, so a reorder with no semantic content churned committed lines, the churn class the lockfile exists to remove. Cycles are detected on the path and poison exactly the routers on it; the cap is gone |
| **A vocabulary hit is a claim about a name; the resolver says whether the name is the codebase's own** (review #16 F4) | `import jwt` in a repository that owns a `jwt/` package resolved intra-repo (`app/auth.py -> jwt/__init__.py`) and still committed `role app auth`; a tsconfig alias `stripe` drew a Stripe cloud box; a type-only `ioredis` import drew a Redis store. Classification that runs before resolution turns every table key into a decoy waiting for a repository that owns a package by that name. Semantics now asks the resolver and skips type-only imports |
| **Only an ERROR withholds a view** (review #16 F2) | `lay_out_set` withheld any canvas with any finding, so an INFO saying "a boundary was not drawn, its members still are" removed the whole view and the report's only reason was the consequence (`SVA-R-005`, the drill target vanished). Severity is the withhold rule; every finding still reaches the report |
| **Bends in lines are not boxes** (review #16 F3) | The boundary check iterated every box including waypoint dummies, so a skipping import inside a service dropped the service's boundary and printed a NUL-delimited dummy id to the user. Every geometric check that iterates boxes takes the waypoint set, as the crossing and evidence checks already did |
| **A build context is a path anchored at the declaring compose file** (review #16 F5) | `lstrip("./")` appeared in two new sites, the third recurrence of the path-stripping defect: `./.web` became `web`, `../api` became `api` and wrapped an in-repo decoy, and a compose file in `deploy/` with `context: ./api` wrapped the root's `api`. One function (`build_context_of`) joins to the compose file's directory with path operations, treats escaping contexts as building nothing in the tree, and is the only place that normalises. A group at the top level is inside a service only if every module it represents is under the context |
| **Two names for one thing are one box** (review #16 F6) | The System view said "6 databases" for three: the compose `postgres` service and the `psycopg` import were two boxes because `postgres` and `PostgreSQL` never matched, and `sqlalchemy` added a third. Compose images canonicalise to the vocabulary's labels before anything is counted; a generic family label attaches to the one store of its family. Image-only services are `service`, not `backend`: the code sigil is a claim of code |
| **A follow-up commit is a wave: the acceptance CLI runs after it** (review #16 F1) | The commit that sank externals to the bottom layer withheld the System view on the acceptance repo (one flow column stacked them under a corridor) and shipped with a message reporting tests and mutations and no artifact run. Every commit that touches layout re-runs the CLI on the acceptance repositories and reads the withheld count |
| **An expansion is named for the view it is in, not for what it shows** (review #17 F1) | Two request stories that shared a module each pre-rendered an expansion of the same child under one id built from the child alone, and the viewer's first-match lookup opened the first story's copy from inside the second, crumb and siblings included, on the acceptance repo. Expansion ids are `<host view>//<box>//expanded`; the viewer resolves the host from where the clicked box is (plain view, expanded host, or embedded child) and rewrites a shared plain child's crumb to where the reader came from |
| **Two different edges never share a line, and a gate says so** (review #17 F2) | Corridor climbs indexed per source put every same-rank edge from a column on one x: 65 distinct-edge segment pairs overlapped on one descovo canvas, four for over 440px, with zero findings, because the crossing check sees boxes and the label check sees labels. Drawn on one line, two arrows are one arrow to a reader, a wrong picture rather than an ugly one. SVA-G-015 reports it; the only exemptions are edges sharing an endpoint (a bundle into one box) or connecting the same pair. The gate exposed the layered and clustered routers too (620 shared lines on descovo), so it warns until those tracks are fixed and the report carries the count |
| **A frame cites the lines that place its members** (review #17 F8) | Stage frames cited "the first member's first line", so the Storage frame's SRC link landed on an unrelated import and the Handlers frame on `config.py:1`. The claim "these boxes are in this stage" has specific evidence per member (route lines, the import that reached a domain module, the classified import for a store) and the frame carries those |
| **Reachability passes through what is not drawn** (review #17 F6) | Reach ran over module-to-module pairs, so a handler that imported a generated schema that imported the store stopped at the schema and SVA-R-007 then reported the store as reachable from no handler: a false fact, in the diagnostic added to be honest about omissions. Excluded code is in the graph exactly so it can be walked through; the edge cites both import lines and says what it went through |
| **A reviewer's surviving mutation is a missing test, and it goes on the list** (review #17 F3) | The author's list caught 19 of 19 and none of the 13 the reviewer wrote, including deleting every drill door in both views, the wave's stated deliverable. Review #9 said a mutation suite proves what it enumerates; the corollary is that the reviewer's enumeration is appended to the author's, each with a test, so the next wave inherits both blind-spot lists |
| **A node id is a key, and the emitter proves it** (review #18 F1) | Route ids were built from (file, method, path), which is not unique when one file holds two routers with the same declared paths; the shipped descovo artifact carried two pairs of nodes under one id and `GraphIndex` silently kept the last. The handler is part of the id, collisions get a deterministic suffix, and `graph_json` refuses to write duplicate ids, so every consumer that indexes by id is protected by one gate |
| **A commit hash describes a tree only with its dirty flag** (review #18 F2) | `built_at_commit` named HEAD while the graph was built from a working tree with twelve nodes from files that commit did not have; an untracked directory inside another repository borrowed that repository's HEAD. `worktree_dirty` ships beside the hash, and no hash is written when nothing under the root is tracked |
| **What is a comment is the parser's call** (review #18 F3) | A line scanner over two languages emitted a `todo` from a URL string, a `note` from a string literal, docstrings for classes written inside strings, and cited a one-line docstring as fourteen lines. tree-sitter was already loaded for the file; a `comment` node is a comment, a `string` that is a module or class body's first statement is a docstring, nothing else is either |
| **A search that is not a dependency is not a hop** (review #18 F4) | `affected` counted a class's own docstring as blast radius and `shortest_path` walked through `rationale_for`. Reachability answers follow dependency edges only, say what they excluded, and each hit names the relation it was reached by |
| **Everything the reader does runs in the suite too** (review #19 F1, F2, F6) | The layout gates certified the SVG while hover, click, focus, path, chapter, drill and export ran in a `<script>` pinned only by source strings; a background click left the whole canvas at 28 percent with nothing lit, a pinned path stayed lit under the next focus, and seven JavaScript mutations survived. The jsdom harness (`tests/js/viewer_harness.js`) executes the shipped script against a built artifact and is the runner; a JavaScript change gets a check in it the way a Python change gets a test, and the test skips loudly, never silently, when node or jsdom is absent |
| **A gate that withholds is paired with routers clean on shapes beyond the acceptance repos** (review #19 F3, F4) | "0 withheld on demo and descovo" was a fact about two repositories: a three-node flow spec put a same-column edge and a backward edge on one vertical (two counters for one side of a column), and a thirty-table grid ran a backward key through the rows between. Both shapes are tests now; every router fix carries the spec that found it |
| **A measured constant carries its measurement** (review #19 F8) | The containment chrome constants were guesses that the test read back from the module under test, so no value could fail it; measured in a real 1440x900 viewport they are 290 and 57 (scrollbar included), asserted as literals, and the report column says exactly what fits (the canvas) and what may still scroll (legend and cards) |
