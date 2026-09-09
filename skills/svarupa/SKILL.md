---
name: svarupa
description: Generate verified architecture diagrams and a queryable knowledge graph from a codebase, and diff architecture between commits. Every box and arrow cites a file:line or is not drawn. Use when asked to map, explain, document, or review a system's architecture, or to check what a change did to it.
---

# Svarupa

Svarupa reads a repository and produces a verified map of it. Verified means
every element carries evidence: a box with no `file:line` behind it is not
drawn, and a view with no evidence is absent rather than fabricated. Treat a
missing tab as a fact about the repository, not a failure.

## Install

Check `svarupa --version`. If it is missing, install it with
`uv tool install svarupa` once the package is published; from a source
checkout, `uv tool install <path to the svarupa checkout>` works today.

## Analyze a repository

```
svarupa <path>
```

This writes `<path>/.svarupa/`:

- `index.html` - the interactive artifact. Open it in a browser. Tabs per
  diagram. Click a box for its passport (kind, connections, reach, cited
  lines); a drillable box is marked with a chevron, and a double-click on it,
  a click on the chevron, or the passport's "Open in place" button expands it
  in place. Every box and arrow shows its citations on hover.
- `graph.json` - the full knowledge graph: nodes, edges, evidence. Query
  this when you need relationships programmatically.
- `REPORT.md` - the resolution scorecard: how many edges resolved, per
  language and edge kind. Read this before trusting call edges.
- `diagrams/*.json` - the positioned diagram data, one file per diagram type
  holding every view of it.

To analyze a repository without writing into it, use `--out DIR`. On very
large trees, `--max-files N` caps the scan and says so in a diagnostic.

## Read the output like a machine

Diagnostics are structured: `SEVERITY SVA-<stage>-<n>: <subject> <message>`,
many with `fix:` lines stating what to do; when present, act on the `fix:`
lines rather than parsing prose. Exit 0 means the run completed with no
error-severity diagnostics. Exit 1 means at least one error or a refusal:
an artifact may still have been written (a repository can carry a real
error, like a case-colliding pair of files, and still be analyzable), and
the diagnostics in the report on stdout say what is wrong. A refusal prints
one structured diagnostic to stderr and writes nothing new.

## Query the graph

`graph.json` (schema 2) is a knowledge graph: code symbols, modules, routes,
external stores and APIs, and `rationale` nodes (module and class docstrings
and NOTE / WHY / HACK / TODO / FIXME comments, as the parser sees them)
attached to what they explain. Every node and edge cites `file:line`; node
ids are unique; edges carry a typed `context` (`import`, `call`, `inherit`,
`reference`, `route`, `store`, `cloud`, `message`, `depends_on`, `contain`,
`deploy`, `rationale`). The graph describes the working tree at build time:
`built_at_commit` is HEAD (null outside a git checkout or for untracked
files) and `worktree_dirty` says whether files differed from it. Query it
without rescanning:

```
svarupa query <artifact-dir> get_node <label>              # exact id, qualified name or label
svarupa query <artifact-dir> get_neighbors <label> [--relation import]
svarupa query <artifact-dir> shortest_path <a> <b> [--max-hops 6] [--undirected]
svarupa query <artifact-dir> affected <label> [--depth 3]  # what depends on it, in hops
svarupa query <artifact-dir> god_nodes [--top 10]
svarupa query <artifact-dir> graph_stats
svarupa query <artifact-dir> query_graph "<question>" [--depth 1] [--budget 2000]
```

Add `--json` for structured output (always prefer it when acting on the
answer). Matching is exact: a label shared by several nodes returns an
`ambiguous` list of candidates (each with `matched_by`: id, qualified_name
or label) and exit 1, never a guess; a label that matches nothing returns
`match: null` and exit 1. Boxes whose id starts with `group:` or `tree:` are
drawn groupings (a community, a directory) with no graph node of their own;
query their member modules, which the passport lists. `shortest_path` and `affected` follow dependency
edges only (`rationale_for` is excluded and the answer says so); `affected`
hits carry the relation they were reached by. `query_graph` is keyword search
over names and rationale text, not semantic search, and says so in its
output; a `truncated` banner names how much was cut against a budget measured
on the printed JSON; zero hits exits 1. Exit 0 means the question was
answered (an empty `affected` list is an answer).

For an agent runtime, `svarupa mcp <artifact-dir>` serves the same seven
functions as MCP tools over stdio (needs the optional dependency:
`pip install 'svarupa[mcp]'`; without it the command refuses with
`SVA-Q-002`). Tool names and parameters follow Graphify's, answers follow
svarupa's rules above.

## Architecture diff between commits

The committed lockfile is facts only, no line numbers, so intra-module
refactors produce a zero-line diff and a real architectural change produces
exactly the lines that changed.

1. Adopt: `svarupa <path> --lock`, then commit
   `<path>/.svarupa/architecture.lock`.
2. Compare: build the head, diff against a base lockfile:
   `svarupa <path> --lock --diff <base architecture.lock>`.
3. Guard against a stale base: regenerate the base lockfile from base-branch
   code (for example from a `git worktree` of the merge base) and pass it as
   `--drift-base <regenerated lock>`. Drift is then reported first, and the
   delta is taken against the regenerated base so it shows this change alone.

`svarupa setup ci_github` installs a GitHub Actions workflow that does all
three per pull request.

## Rules the tool holds itself to, which you can rely on

- The committed lockfile is byte-deterministic across machines, platforms
  and Python versions: two lockfiles from the same tree can be compared
  directly. The full artifact is deterministic for a given environment;
  cross-platform byte identity of the whole artifact is verified on a
  narrower gate, so do not diff artifacts from different machines and
  report the difference as an architecture change.
- Communities and visual grouping never define identity; the lockfile's
  modules come from directories, packages, and workspace members only.
- Test, generated and vendored files, and anything under a top-level hidden
  directory (`.claude/`, `.agent/`, `.github/`: tooling for the people and
  agents working on the repository), stay in the graph but shape neither the
  diagrams nor the lockfile.
- Partial failure degrades: one hostile or broken file becomes a diagnostic,
  and the rest of the repository is still analyzed.
- Routes, tasks and roles come from framework detection: FastAPI, Flask and
  Celery (Python), Express and NestJS (TypeScript/JavaScript, ESM and
  CommonJS); declared entrypoints cover pyproject scripts and package.json
  `bin`. A service on another framework showing no api/worker role means
  not-yet-extracted, not "no API"; REPORT.md states this boundary per run.
- Endpoint paths compose only within one file: NestJS controller prefixes,
  and Express `app.use('/api', router)` mounts (router-on-router, and
  `app.route('/x').get()` chains). A composed route cites its handler line
  and carries the mount lines it rests on. Not composed, so handler-relative:
  a router mounted from another file, a dynamic or middleware-first mount
  (`app.use(PFX, r)`, `api.use(auth, users)`), a router name bound more than
  once in the file, and FastAPI `include_router(prefix=...)`.
