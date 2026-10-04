# Svarupa: System Health (umbrella design)

Date: 2026-10-03
Status: approved direction; each step below gets its own spec, plan and PR.
Vocabulary: `CONTEXT.md` (Design section: language pack, design style, target
design, check, violation, maturity, health).

## Goal

An engineer who has engineering knowledge but does not know a project's
language or stack can still understand the system, keep it on an
industry-standard design, and avoid scaling and security bottlenecks. Most
code is now written with AI agents, so the same rules must also guide the
agents before they write code.

Svarupa stays what it is: deterministic, offline, no AI model in the pipeline,
and every claim backed by `file:line` evidence.

## What exists today

- Code facts from Python and TypeScript/JavaScript only (tree-sitter).
- Framework facts: FastAPI, Flask, Celery, Express, NestJS routes; ORM and
  datastore classification by import.
- Six diagrams, `graph.json`, `REPORT.md`, lockfile, `--diff`,
  `--fail-on-change`, query CLI and MCP.
- Go, Rust and Java files are detected but not analyzed.

## Phase 1: four steps, in this order

| Step | What | Depends on |
|---|---|---|
| 1a | Language packs: one generic extractor plus per-language data | none |
| 1b | Benchmark: hand-checked repos scored in CI | 1a |
| 1c | Health engine: target design, checks, health, plain explanations | 1a, 1b |
| 1d | Agent rules: skill and MCP `get_design_rules` | 1c |

Phase 2: CI gate that fails only on new violations or a grade drop in a pull
request. Phase 3: wider MCP surface and one-command setup for every AI IDE.

### 1a. Language packs

Spec: `2026-10-03-language-packs-design.md`. Default install covers Python,
TypeScript/JavaScript, Go, Java, C#, Kotlin, Swift, Ruby, PHP, Rust, C and
C++. A language without a pack is reported as "detected, not analyzed",
never guessed.

### 1b. Benchmark

- About 10 public repositories across stacks, pinned to a commit.
- For each: hand-checked expected modules, edges, diagrams and violations.
- CI scores every release: found, missed, wrong.
- A style, check or pack becomes `stable` only after it passes the
  benchmark; until then it is `experimental` and every report says so.
- On top of the benchmark: a task test (engineers new to the stack answer
  real questions such as "which endpoints write to the orders table?"),
  then user feedback for blind spots.

### 1c. Health engine

**Style catalog.** Styles are data written in five rule types:

1. Parts: groups of modules matched by path or role.
2. Direction: which part may depend on which.
3. Independence: sibling parts must not import each other.
4. Public entry: others may import only a part's public entry file.
5. Purity: a core part must not depend on frameworks or I/O.

Styles are checked per level, because real systems mix them:

| Level | Catalog (first release, each with a maturity label) |
|---|---|
| System | monolith, modular monolith, microservices, event-driven, serverless, micro-frontends, BFF |
| Backend service | layered / N-tier, MVC (Rails, Django, Laravel, Spring), hexagonal, onion, clean, vertical slice, CQRS |
| Frontend | Feature-Sliced Design, pages to components to state to API client, MVVM |
| Mobile | MVVM, MVI, clean |
| Data and ML | pipeline stages, dbt layers, medallion |
| Library and SDK | public API vs internal, plugin / microkernel |

Teams can add their own styles in the same format.

**Target design.**

- Svarupa scores every catalog style against the evidence per level and
  proposes the best fit, with runners-up.
- `svarupa design` shows the proposal and asks: accept, pick another, skip.
  On accept it writes `.svarupa/design.yaml`, which the team commits. Nobody
  writes design files by hand.
- Without an accepted file, the proposal is used and labelled `inferred`.
- When no style fits well, Svarupa proposes the closest standard style for
  that kind of project and lists the migration moves with `file:line`,
  biggest impact first.

**Check catalog** (data, each check names its source standard):

| Source | Examples |
|---|---|
| Code metrics | cyclomatic complexity, function and file size, parameter count, nesting depth, duplication |
| Fowler code smells | long function, large class, long parameter list, duplicated code, dead code, feature envy, data clumps |
| SOLID | single responsibility (low cohesion), dependency inversion (core depends on concrete I/O), interface segregation |
| CK metrics | coupling between classes, LCOM cohesion, inheritance depth, methods per class |
| Martin package metrics | instability, abstractness, package cycles |
| Anti-patterns | god object, circular dependencies, global mutable state, singleton as global |
| Security (OWASP-style) | endpoint without auth, committed secret, SQL built from strings |
| Scaling | one datastore shared by many services, deep synchronous request chains, god module on the request path |

Checks the code cannot prove (for example real traffic load) stay out.

**Health.** A 0 to 100 score and letter grade per ISO/IEC 25010 area
(maintainability, security, reliability, performance efficiency), built only
from violations by one published formula. Violations are ranked by
architectural impact (fan-in, reach, being on a request path). The report
shows everything; the phase 2 gate looks only at what a pull request adds.
Accepted exceptions live in `design.yaml` with a reason.

**Plain explanations.** Every box gets one language-neutral line from fixed
templates, for example "HTTP endpoint POST /orders, writes table orders".
No AI.

**Gang of Four patterns** are shown as information in the passport only,
never scored. Not required for phase 1.

### 1d. Agent rules

The skill and a new MCP tool `get_design_rules` give an agent the accepted
target design (parts, allowed directions, forbidden imports) before it
writes code. CI catches whatever slips through.

## Out of scope for phase 1

- Importing ruff, ESLint or Sonar results (later, optional input).
- User-written language packs (format allows it later).
- Framework facts for new languages (Spring, Rails, ASP.NET routes); new
  languages start at imports, definitions and calls.
- Any AI-generated text in the artifact.

## Decision log

| Decision | Choice | Why |
|---|---|---|
| Surfaces | Developer, CI and agent, equally | All three read one engine |
| Order | Engine and report first, then CI, then agents | Each step ships something usable |
| Languages | Generic packs as data, pinned grammar wheels | Covers the industry without giving up offline and byte-identical output |
| Grammar source | Pinned per-language wheels, not tree-sitter-language-pack | That package downloads parsers at runtime |
| Target design | Proposed by Svarupa, accepted by one prompt, committed | Teams with AI-written code rarely have a written design |
| Styles | Open catalog as data in five rule types, per level, plus custom | A fixed list cannot cover the industry |
| Breadth | Ship all styles and checks with maturity labels | Wide coverage, honest about what is proven |
| No fit | Closest style plus migration moves | Gives a direction, not only a complaint |
| Code checks | Full catalog mapped to named standards | "Industry standard" must be traceable |
| GoF patterns | Information only | No standard says more patterns means better code |
| Score | Fixed published formula, ISO 25010 areas, letter grade | Grades compare across repos and over time |
| Existing code | Show all, gate on new | Teams can adopt it on messy code without a red build |
| Linter import | Later | Phase 1 results must be the same everywhere |
| Accuracy | Benchmark plus task test plus feedback | Measure before users find problems |
| Explanations | Rule-based templates | Deterministic and evidence-backed |
| Agent rules | In phase 1 | The main value for AI-heavy teams |
