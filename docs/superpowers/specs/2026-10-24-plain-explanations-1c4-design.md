# Svarupa: Plain Explanations (step 1c-4)

Date: 2026-10-24
Status: design approved, spec written
Parent: `2026-10-03-system-health-umbrella-design.md` ("Plain explanations");
builds on 1c-1 to 1c-3 (health engine, design styles, security,
performance and reliability)

## Goal

An engineer who does not know the language or the stack can read any
health finding and know what it means, why it matters and how to fix it,
with a short example in the finding's own language. Every box on the map
also gets one plain sentence that says what it is, what it does and how
healthy it is. AI agents read the same text through MCP. All text is fixed
and reviewed; no AI is in the pipeline.

## Decisions (maintainer, 2026-10-24)

| Question | Decision |
|---|---|
| Scope | Both check explanations and box lines; check explanations are built first |
| Fix detail | A language-neutral explanation, plus a before/after example in each language whose pack can produce the check |
| Surfaces | Everywhere, once per check: graph.json, MCP `get_health`, REPORT.md, the viewer, and a new CLI `svarupa explain`. No new MCP tool |
| Box health | The box line ends with the finding count and the worst finding |
| Storage | One TOML file per check, shipped in the wheel |

## Part A: check explanations

### The files

`svarupa/health/explain/<check-id>.toml`, one per catalog check (30 today):

```toml
what = "The same database query runs once for every item in a loop."
why = "100 items means 100 round trips to the database. Pages get slow as data grows, and the database gets busy for everyone."
fix = "Load everything the loop needs in one query before the loop, then look items up in memory."

[example.python]
before = """
for order in orders:
    customer = Customer.objects.get(id=order.customer_id)
"""
after = """
customers = Customer.objects.in_bulk([o.customer_id for o in orders])
for order in orders:
    customer = customers[order.customer_id]
"""
```

- `what`, `why`, `fix`: required, plain words, one to three sentences
  each. A technical term is explained in the same sentence the first time
  it is used ("cyclomatic complexity, the number of paths through the
  code").
- `[example.<lang>]` with `before` and `after`: languages are `python`,
  `typescript`, `go`, `java`. JavaScript findings use the `typescript`
  example; it is written in plain JavaScript syntax where possible.
- Which checks need examples: a check needs an example for every language
  whose pack can produce it. Function checks (`complex-function`,
  `long-function`, `many-parameters`, `deep-nesting`, `large-class`) and
  every risk-fact check need all four languages, except where the packs
  have no rule for that language (for example `blocking-in-async` has no
  Go or Java rule, `endpoint-without-auth` has no Go routes). The required
  set is computed from the pack data, not written by hand.
- Structural checks (`module-cycle`, `hub-module`, `hub-on-request-path`,
  `shared-datastore`, `large-file`, `duplicated-block`, the four design
  style checks, `hardcoded-secret`) get text only; an example is allowed
  but not required.
- Secrets examples never contain a real-looking key: placeholders such as
  `os.environ["API_KEY"]` only.

### Loading

`svarupa/health/explain.py`:

- `Explanation(check, what, why, fix, examples: tuple[tuple[str, Example], ...])`
  and `Example(before, after)`, frozen dataclasses.
- `explanation(check_id) -> Explanation`, read with `tomllib` (or `tomli`
  on 3.10) through `importlib.resources`, cached.
- `example_for(check_id, lang) -> Example | None`: `javascript` maps to
  `typescript`.
- Unknown keys, a missing required field or a missing required example
  raise at load time; a test loads every file so this fails in CI, never
  for a user.

### Where it shows

- **graph.json and MCP `get_health`:** each entry of `health.checks[]`
  gains `explanation: {what, why, fix, examples: {lang: {before, after}}}`.
  Violations are unchanged: the text is stored once per check.
- **REPORT.md:** after "Fix first", a section "What these mean" lists each
  check that has violations, once, with what, why and fix, and the example
  in the language of that check's first violation.
- **Viewer:** each violation in the Health tab has a "Why and how to fix"
  fold (`<details>`), closed by default, with the explanation and the
  example in that violation's language.
- **CLI:** `svarupa explain` lists check ids and titles; `svarupa explain
  <check-id> [--lang python]` prints what, why, fix and the example (all
  examples when `--lang` is not given). An unknown id exits 2 and names the
  closest ids.
- **Violation language:** the language of the violation's first evidence
  file, from the scan (`FileRec.lang`). No language, or one without an
  example, shows the text only.

## Part B: one plain line per box

### Templates

`svarupa/derive/about.py`, `about_lines(graph, health) -> dict[str, str]`,
keyed by diagram box id. Every clause comes from a fact the graph already
holds; a clause with no fact is left out, never guessed.

| Box | Line |
|---|---|
| Module | "Module app/api: serves 12 HTTP routes (FastAPI), runs 2 background tasks, uses 3 modules and is used by 5, talks to PostgreSQL and Redis." |
| Compose service, built | "Service api: built from ./api, depends on db and redis." |
| Compose service, image | "Service adminer: runs the image adminer." |
| Datastore or queue | "PostgreSQL database (compose service db), used by api and worker." |
| External system | "External database (MongoDB), used from 4 modules." |
| Endpoint | "HTTP endpoint POST /orders, handled by create_order." |
| Table | "Database table orders with 6 columns." |
| Class | "Class OrderService with 8 methods, based on BaseService." |
| Group | "Group of 7 modules under app/services." |

A box with no facts at all reads "Module app/util." (the kind and id).

### Health clause

Every line ends with either "No health findings." or "N health findings,
worst: <check title> (<severity>)." A box's findings are:

- module: violations whose `module` equals the box id;
- group: the sum over its member modules;
- compose service: the modules it ships (`modules_shipped`);
- compose datastore or queue: violations whose evidence cites its line
  (`shared-datastore`);
- endpoint: violations in the handler's file;
- table, class, external: violations whose `symbol` or evidence file
  matches; none otherwise.

The worst finding is the highest severity, then the highest impact, then
the check id. In the passport the worst finding links to its fold in the
Health tab.

### Where it shows

- **Passport:** the line appears under the box title.
- **graph.json:** a top-level `about` map, box id to line, covering
  diagram-only boxes (`group:`, `tree:`) too.
- **Query and MCP `get_node`:** the result gains `about` when a box with
  that id exists.
- SVG boxes are unchanged; the viewer reads the map from data embedded once
  in `index.html`, so diagram bytes stay the same.

## Testing

- Every catalog check has a file; every required language has an example;
  unknown keys, empty fields and em dashes fail.
- Each box template has a fixture test; a test removes each fact and checks
  its clause disappears.
- The health clause: a module with two findings names the worse one; a box
  with none says so; a group adds its members.
- Outputs: the explanation reaches graph.json, MCP `get_health`, REPORT.md,
  the viewer fold and `svarupa explain`; `about` reaches graph.json, the
  passport data and `get_node`.
- Determinism: the existing byte-identity runs cover the new keys.
- Mutations: a missing required example passes; a box takes another box's
  findings; `javascript` loses the `typescript` example.

## Out of scope

Which code reads or writes which table (needs new extraction);
translations; AI-written or AI-chosen text; explanations for diagnostics
(SVA-X codes) and design-style proposals.
