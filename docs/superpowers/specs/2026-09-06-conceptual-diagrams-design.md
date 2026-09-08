# Conceptual diagrams: Archify's model, Graphify's graph, svarupa's evidence

**Status:** design for the P2 redesign waves. Amends the 2026-08-30 design
sections 5 and 6; those sections stay authoritative where this one is silent.
**Inputs:** the Archify teardown (`docs/research/2026-09-06-archify-teardown.md`,
plus all five diagram types read from source and rendered, screenshots in
`~/Downloads/svarupa-artifacts/archify-*.png`) and the Graphify teardown
(`docs/research/2026-09-06-graphify-teardown.md`).

## 1. The problem, stated plainly

The user's verdict on the current artifact: the diagrams are not *conceptual*
the way Archify's are. Archify's picture of a system says "Guardrail (input
filter) -> Orchestrator (LangGraph agent) -> Vector Store (Milvus)", with
typed, colour-coded components inside named boundaries and labelled arrows.
Ours says "api -> api/routers (12 imports)". Both are true; only one reads as
architecture.

Archify gets there because an agent *authors* those concepts. We cannot do
that and keep the product's promise (every box and arrow cites a line, output
byte-deterministic, facts committed in a lockfile). So the question this
design answers is: **which of Archify's concepts can be derived from evidence
we already extract, and how is each one cited?** Everything below either has
a `file:line` behind it or is not drawn.

## 2. Archify's conceptual model, and its evidence-backed equivalent

Archify's architecture spec has four things: typed **components** with a
semantic **sublabel**, **boundaries** that wrap components, **connections**
with a short label and a variant, and summary **cards**. Its other four types
share the vocabulary (typed nodes, lanes/stages, labelled edges).

### 2.1 Component types

| Archify type | svarupa source of the claim | Evidence cited | Sublabel (semantic, never a path) |
|---|---|---|---|
| `backend` | a code module with an `api` or `worker` role, or a plain code module | the module's files; the role's decorator line | `FastAPI · 23 routes`, `Celery · 4 tasks`, `12 files` |
| `frontend` | a module whose files import `react`, `next`, `vue`, `svelte`, `@angular/core`, or are `.tsx`/`.jsx`-heavy | the import line | `React · 31 components` (component = exported function in a .tsx) |
| `database` | a compose `datastore`; or a module importing a database client (`pymongo`, `motor`, `sqlalchemy`, `psycopg`, `asyncpg`, `redis`, `prisma`, `mongoose`, `pg`, `typeorm`) draws the **external store** as its own component | compose line; the import line | `Redis :6379` (compose), `MongoDB via motor` (import) |
| `messagebus` | a compose `queue`; a Celery broker; `kafka`, `pika`, `aio_pika`, `amqplib`, `bullmq` imports | compose line; the import line | `RabbitMQ`, `Kafka via kafkajs` |
| `cloud` | imports of SaaS SDKs (`openai`, `anthropic`, `boto3`, `stripe`, `twilio`, `sendgrid`, `@google-cloud/*`, `googleapis`, `firebase`) draw the **external API** as a component | the import line | `OpenAI API`, `AWS via boto3` |
| `security` | a module importing an auth library (`jwt`, `pyjwt`, `passlib`, `authlib`, `fastapi.security`, `passport`, `jsonwebtoken`, `bcrypt`) takes the `auth` role | the import line | `JWT · passlib` |
| `external` | not drawn as individual boxes: a third-party library is a dependency, not a component. Only the three external kinds above (database, messagebus, cloud) become boxes, because they are the ones an architecture diagram names | | |

Module roles gain `auth` and `frontend` alongside `api`, `worker`, `cli`; the
lockfile `role` record grows the same values (additive, minor bump, per the
rule that the minor tracks what the build emits). External stores and APIs
become graph nodes of new kinds (`external_store`, `external_api`,
`external_bus`) with the import line as evidence and the *importing module*
as the edge source; they are facts about the codebase ("this module talks to
MongoDB") and are candidates for lockfile records in a later, separate
decision.

The classification tables are data, not code: one module
(`svarupa/extract/vocabulary.py`) holds them, each entry with the package
name and its display label, so a review can attack the table and a user can
read it.

### 2.2 Boundaries

A compose service with a `build.context` inside the repository **wraps** the
modules under that context. That is a real boundary (the deploy unit) with a
real line (the `build:` key). Drawn as Archify's `region`: dashed, labelled
with the service name, the modules inside it. Modules under no service stay
unwrapped. `security-group` is not derived in v1.

### 2.3 Connections

| Relationship | Label | Variant | Evidence |
|---|---|---|---|
| module imports module | `imports` (count in tooltip, weight as width) | default | import lines (capped set) |
| module -> external store / bus / api | `reads/writes`, `publishes`, `calls` | `dashed` for external | the import line(s) |
| service depends_on service / store | `depends on` | `emphasis` | the compose `depends_on` line |
| route -> handler module (request flow) | `GET /items` | `emphasis` | the decorator/call line |

Arrow text returns only where Archify puts it (short verb, on a mask, one per
edge, collision-checked by the label-mask rule in section 5); the count moves
to the tooltip. This reverses the earlier "no edge text" decision for a
reason that decision did not have: the labels are now semantic verbs, not
counts, and there is a legibility gate.

### 2.4 Cards and guided views

Cards are computed, never authored: `Entry points` (routes and CLI
entrypoints, counts and the top three), `Data stores` (compose datastores plus
import-derived stores), `External APIs`, `Unresolved` (the scorecard's third
bin, so the honest number sits next to the pretty picture). Each item cites.

Guided views are computed stories: one per top-level service or api module,
`focus` = the module plus everything it imports plus the stores it talks to.
Deterministic, and each story is a subgraph a reader can already click.

## 3. Per diagram type: concept, evidence, drill

Every type keeps the one interaction the user asked for: click a box, it
expands in place into its own diagram, down to code.

| Type | Concept (what the reader is looking at) | Top-level boxes | Drill | Honesty note |
|---|---|---|---|---|
| **System** (Archify `architecture`) | the deployed system | services typed by the role of their code (backend/frontend), datastores, queues, external APIs; services wrap their modules as boundaries | service -> its modules (architecture of the build context) -> module -> component flow -> code | without compose, the top level is the role-typed module set: still a system view, one level lower |
| **Architecture** | how the code is organised | role-typed modules inside service boundaries, external stores/APIs at the edge | module -> component flow -> code (exists) | |
| **Data flow** (Archify `dataflow`) | where data enters, is handled, lands | stages `Ingress` (routes, grouped by module) -> `Handlers` (api modules) -> `Domain` (modules the handlers import) -> `Storage / External` (stores, buses, APIs); flows are import edges and store edges, in stage order | any node -> its module's component flow | stages are assigned from roles and import depth, both evidenced; a module in no stage is not drawn and the report says how many |
| **Request flow** (replaces sequence) | what one endpoint group touches | participants = the handler module, the modules reached by import from it (depth-limited), the stores/APIs those modules use; hops numbered by import depth | participant -> component flow -> the handler's code | rendered in dataflow notation with numbered hops, NOT as a sequence diagram: import depth is reachability, not temporal order (design 5, Spike 0b) |
| **Module deps** | the raw dependency graph | modules, role-typed, no boundaries | module -> component flow | |
| **ERD** | persisted shape | tables from SQL DDL; ORM models (SQLAlchemy, Prisma, Mongoose, Django) in a later wave | table -> the model's code | absent without a schema source, said so |
| **Lifecycle, workflow** | not generated | | | Archify authors these from a conversation; no evidence source in code describes them. The `not drawn` tab states that in one sentence rather than leaving a gap |

## 4. The graph, Graphify-class

`graph.json` gains what Graphify's consumers rely on, without giving up what
ours already has (start-end evidence on every node and edge, re-verified):

- edge `context` (`import`, `call`, `inherit`, `reference`, `route`, `store`,
  `depends_on`) as a typed sub-relation; `built_at_commit` when in a git
  checkout; a `hyperedges` slot (empty until something produces one);
- **rationale nodes**: module and class docstrings and `# NOTE / WHY / HACK /
  TODO` comments as nodes with `rationale_for` edges, each cited at its line
  (cheap, and the "why" questions an agent asks land on them);
- node `community` and `community_name` are NOT added: communities are
  presentation-only (decision F2) and a community name baked into every node
  is exactly the churn Graphify suffers.

`svarupa query` is a CLI with a JSON mode and, in a later wave, an MCP server
exposing the same functions with Graphify's names so agents already trained
on them need no relearning: `query_graph(question, depth, token_budget)`,
`get_node(label)`, `get_neighbors(label, relation_filter)`,
`shortest_path(source, target, max_hops, undirected)`, `affected(label,
relation, depth)`, `god_nodes(top_n)`, `graph_stats()`. Two deliberate
differences from Graphify, both from its teardown: **exact match by default,
with an explicit ambiguity list** (Graphify's `path "to_json" ...` silently
answered for `to_html()`), and **structured output** (JSON objects with the
evidence on each hit, not prose an agent must parse). Truncation is announced
with a banner and the count, as Graphify does, because that part they got
right.

The viewer gains a **Graph** tab: the module-level graph and, on drill, a
module's symbol graph, laid out by our deterministic engines (positions
persisted in the artifact, so two runs render identically and the page works
offline); search with kind swatches; click-to-inspect with clickable
neighbours and the citations; legend checkboxes per kind and per community;
dashed edges for candidate/unresolved resolution. Everything Graphify's
explorer lacks and we already hold: click-through to the line, edge-relation
filters, a path tool between two selected nodes.

*As built (Wave C):* the module-level graph with drill to a module's symbols
already IS the Module deps tab, so no separate Graph tab was added; instead
every diagram tab carries the explorer controls: a search box (id or label,
matches lit, the rest receded), legend swatches that mute a kind and the
routes touching it, clickable neighbours in the passport, and a shift-click
path tool that finds the path over the routes drawn in that view and states
it in hops. No per-community checkbox: communities are presentation and are
not in the artifact's nodes (decision F2). The controls only toggle classes
on the SVG, so the artifact stays byte-deterministic and the picture never
gains a claim the layout did not make.

## 5. Visual grammar, Archify parity with exact values

Adopted from the teardown, verbatim where a value exists:

- **Type**: JetBrains Mono stack (`"JetBrains Mono", ui-monospace, SFMono-Regular, Menlo, Consolas, monospace`), weights 700/600/400, label size 10-11px, sublabel 9px muted, box label 13px semibold.
- **Dark-first** with a light theme: canvas `#020617`, mask `#0f172a`, border `#1e293b`, muted `#94a3b8`, dim `#475569`; light canvas `#f8fafc`, mask `#ffffff`. Theme choice stays in `localStorage`, never in the bytes.
- **Per-type fill/stroke pairs** (dark): frontend `rgba(8,51,68,.4)`/`#22d3ee`, backend `rgba(6,78,59,.4)`/`#34d399`, database `rgba(76,29,149,.4)`/`#a78bfa`, cloud `rgba(120,53,15,.3)`/`#fbbf24`, security `rgba(136,19,55,.4)`/`#fb7185`, messagebus `rgba(251,146,60,.3)`/`#fb923c`, external `rgba(30,41,59,.5)`/`#94a3b8`; light fills at alpha .15-.2. An **opaque mask rect** under every translucent box so arrows never show through.
- **Box anatomy**: `rx=6`, stroke 1.5, a small type sigil top-left, label centred, sublabel 14px below, optional tag at the bottom in the stroke colour; our drill chevron top-right and an evidence capsule (`SRC n`) beside it, Archify's own "Verified Source Beacon" made unconditional because every box of ours has sources.
- **Boundaries**: dashed `8,4` in the owning type's stroke at 5% fill, 30px padding plus 20px extra at the bottom, label on a mask at the top-left inside.
- **Connections**: orthogonal, rounded corners, 24px endpoint stubs, shared endpoints spread with 16px gutters; the label on a mask at the midpoint of the longest segment.
- **Legibility gates, automated**: label mask width `6.5px x ASCII units + 13px` (CJK counts two), clear gap `> mask + 8px`; every nonzero route segment `>= 8px`, every interior segment `>= 16px`; no route through a box (exists); no label over a route; a headless four-viewport containment check (1440x900, 1600x1000, 1920x1080, 2048x1320) that fails the build on overflow, run in CI where a browser exists and skipped with a stated reason where not.
- **Legend** chips with computed counts; **cards** row under the canvas; **guided views** strip above it; a top-right toolbar (theme, present, export PNG/SVG later).

## 5.1 Interaction model: hover, click, drill

Archify's interaction is two-level: **hover** highlights the connected path
(upstream and downstream edges and their endpoints stay strong, everything
else recedes) and **click** opens the component's Semantic Passport (type,
sublabel, relationships, sources). Ours is three-level, and the third is the
one Archify does not have:

| Gesture | Result | What it is for |
|---|---|---|
| **hover** a box or arrow | the path through it lights up: its edges, their endpoints, the boundary it sits in; unrelated topology recedes | orientation |
| **click** a box or arrow | the **passport** panel: type and role with the citation that earned them, the sublabel facts, incoming and outgoing connections with their verbs and counts, every `file:line` behind the element, and the module's lockfile records | quick information without leaving the picture |
| **double-click** a box, or its chevron | **drill in place**: the box grows into a container holding its own diagram (module -> component flow -> code), its connected neighbours stay around it and re-route to the container; the header's close mark collapses it | deep information, in context |

The passport replaces today's citation-only panel; the drill and hover apply
identically in every diagram type, because the layout data is the same shape
in all of them. Hover state never reaches the bytes (it is CSS on the same
SVG), so byte-identity is unaffected.

*As built (visual pass after the user compared the click state against
Archify's live gallery, 2026-09-07):* a click is a **focus**, not only a
panel: the clicked box glows, its neighbours and the arrows between them stay
lit with their verbs, everything else recedes to 13 percent (Archify's
value), and hover is suspended until the focus is cleared by a background
click or the card's close. The passport is a **card over the canvas**
(352px, accent border, mono 11px) in Archify's order: eyebrow, title,
sublabel, chips (kind in its colour, the frames the box sits in, roles, the
id), "N outgoing · M incoming", **Upstream / Downstream** reach buttons that
light the directed closure over the arrows drawn in that view, OUTGOING and
INCOMING lists with the verb under each name, and last what only we have:
the verified source lines. Structural arrows say **imports** again (a
knowing reversal of the Wave A decision that silenced them): the label
settles or drops under the same collision gates as any other, so dense
views stay clean and focused edges show their verb. An opened container is
drawn in the accent colour and its siblings recede, so the eye lands inside.
Not built: Archify's "Copy link" (a focus hash would collide with the tab
hash), guided views and cards (Wave D).

*As built (review #20, product readiness, 2026-09-08):* the passport is a
**side panel**: the tab makes room for it while it is open, so no box,
chapter or title sits under the card. The drill has three doors, all named on
the page: a double-click, a click on the chevron, and the passport's "Open in
place" button. Structural arrows are silent again, this time for a measured
reason (76 identical `imports` on one view) and with the legend saying once
what a solid arrow is; every store arrow carries its verb. Community boxes
have their own `group:` ids and set names (`agent`, `models +5`) and list
their members; the passport's connection rows show labels with the id as a
tooltip. Escape closes, Enter opens, boxes are focusable. The theme button
names the theme you are in.

## 6. What stays as it is, and why

- Structural module identity, presentation-only communities, the lockfile
  grammar and the evidence rule are untouched. Typing a module `backend` or
  drawing an external store does not change what the lockfile says about
  modules and deps; the new facts it may gain (`role auth`, external store
  records) go through the minor-bump path with their own review.
- No LLM anywhere in derivation (design 5.2). Every concept above is a
  deterministic function of extracted facts; the classification vocabulary
  is a table, reviewed like code.
- Expand-in-place, the four-level drill, and byte-identity across seeds and
  platforms are the acceptance gates every wave re-runs.

## 7. Waves, each reviewed before the next

| Wave | Delivers | Acceptance (in addition to the standing gates) |
|---|---|---|
| **A. Concepts and grammar** | vocabulary table; `auth`/`frontend` roles; external store/bus/API nodes from imports; service boundaries from `build.context`; semantic sublabels; connection verbs on masks; the visual grammar of section 5 in the viewer (fonts, fills, mask, sigils, dark-first) across System, Architecture, Module deps and the drill levels; the hover/click/drill interaction model of section 5.1 (path highlight, passport panel) | screenshots of every level on the warehouse repo and descovo, judged by the user; label-mask and route-rhythm gates green; role records additive with SVA-L-013 firing on the minor |
| **B. Data flow and request flow** | the two new derivers, their drill, the `not drawn` sentence for lifecycle/workflow. Shipped with two layout rules the first screenshots forced: domain modules sit one column per import hop and externals sit one column after the deepest hop drawn (a fixed column left an empty one and pushed every store edge through the corridor); corridor edges climb and drop beside the whole column, never at their own box's edge. Computed cards moved to Wave D: nothing in B needed them and a card without the legibility gates is another unmeasured surface | each stage node cites; a request-flow view for every api module; report states how many modules fell in no stage |
| **C. Graph** | graph.json enrichments, rationale nodes, `svarupa query` (JSON and text), the Graph tab | exact-match ambiguity lists tested; explorer offline; positions byte-identical across seeds |
| **D. Gates and stories** | four-viewport containment in CI, guided views, computed cards (from B), legend and card polish, export; corridor labels settle near an end of their edge rather than on the lane (seen in B's screenshots) | the containment gate fails on a planted overflow |
| **E. MCP** | the query tools as an MCP server (P2 item) | tool names and params match section 4 |

*As built (D and E, 2026-09-07):* one line per edge in every router with
SVA-G-015 as an ERROR (620 shared lines on descovo went to zero); labels
prefer a horizontal segment near an end; guided views and cards computed
from the root spec and the graph (section 2.4), every item citing; the
four-viewport containment check is arithmetic on the canvas and an INFO
(SVA-G-016) plus a "Root fits" report column, because wide views scroll at
natural size by decision, so the check informs rather than fails; Export
SVG and PNG (2x) serialise the open view with the stylesheet and theme
inlined; `svarupa mcp <artifact>` serves the seven query functions under
Graphify's names over stdio through the same `run_query` as the CLI, with
the SDK as an optional extra (SVA-Q-002 without it). Not built: guided
views as animated stories beyond stepping, cross-tab navigation from a
service into its code (still a stub), "Copy link".

*As built (review #20, 2026-09-08):* module dependencies past the top-box
budget follow the directory tree (a box per part at the root, the modules of
a part one drill down, every dependency at exactly one level); band and
region labels are obstacles for route verbs in the settle and the gate; flow
columns align at the top once the tallest exceeds a screen; the externals
band reads `external`; directory modules are graph nodes so the ids the
diagrams draw are queryable; an empty directory is a refusal (SVA-D-008).

*As built (review #21, 2026-09-08):* a service's arrows come from the code its
Dockerfile copies (COPY/ADD sources, each cited), with the build context as
the fallback; every surface names a box by its label; the header keeps every
tab visible and may take a second row below 1100px; the side panel narrows to
280px there; a drill lands below the header at any header height; "in a
cycle" is cycle membership; group drills carry the group's name; flow import
arrows are silent too; an exact id resolves alone.

The Archify teardown's sharpest line still holds and is the reason this can
work: their spec-to-HTML step is deterministic and only their prompt-to-spec
step is not. Wave A replaces the prompt with extraction.
