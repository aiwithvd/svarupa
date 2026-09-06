# Graphify teardown (reverse-engineering, 2026-09-06)

Source: cloned and ran `Graphify-Labs/graphify` (PyPI `graphifyy`, v0.9.55,
commit `c9f99018`), the agent skill that builds a code knowledge graph
(`graphify-out/graph.json` + `graph.html` + `GRAPH_REPORT.md`), detects
communities, and exposes `query`/`path`/`explain` plus an MCP server. Three
real runs on a small corpus measured its determinism directly.

## Pipeline and model

`detect -> extract -> build -> cluster -> analyze -> report -> export`. Code
is AST-only (tree-sitter, ~40 languages via a generic `LanguageConfig`); docs
go through LLM subagents. Node ids are `repo-relative path stem + symbol`,
NFKC-casefolded. Node kinds: files, classes, functions, external stubs, and
**rationale nodes** (docstrings and `# NOTE/WHY/HACK` comments) linked by
`rationale_for`. Edge relations: `calls, contains, imports, imports_from,
references, inherits, implements, indirect_call, dynamic_import, re_exports,
...` with a typed `context` (call / import / parameter_type / return_type /
field / generic_arg). Confidence `EXTRACTED | INFERRED | AMBIGUOUS` with
scores 1.0 / 0.55 (0.85 for name-match inference) / 0.2.

**Evidence is line-only**: `source_location: "L392"`, no end line, no
column, no content hash; external stubs have empty location. Edges cite the
relation *site* in the caller's file, which is the right choice for "who
calls X".

## Output folder and schema

`graph.json` is NetworkX node-link: `{directed, multigraph, graph, nodes[],
links[], hyperedges[], built_at_commit}`. Nodes carry `id, label, _origin,
community, community_name, file_type, norm_label, source_file,
source_location, _callable`; links carry `source, target, relation, _origin,
confidence, confidence_score, context, source_file, source_location, weight`.
Serialization is canonical (sorted records, fixed leading keys, `indent=2`,
`ensure_ascii`, atomic replace, a shrink guard). Sidecars: `manifest.json`
(mtimes), `cache/` (content-hash per-file AST cache keyed by tool version),
`.graphify_root` (an **absolute host path**), `.graphify_labels.json`.

## Communities

Leiden (graspologic, seed 42) when installed, else NetworkX Louvain; graph
rebuilt from sorted nodes/edges before clustering because "Louvain, order-
sensitive even with a fixed seed, returned 70 communities under
PYTHONHASHSEED=1 and 69 under =2" (their comment, same class of bug our
review #11 F3 closed). Post-processing: split communities >25% of the graph,
re-split low-cohesion ones, re-index by `(-size, sorted ids)`, greedy id
remap to the previous run. Labels default to the hub member's name; in the
skill flow the **agent invents names**, and those land in `community_name` on
every node.

## HTML explorer

vis-network 9.1.6 **from a CDN** (unpkg); force-directed layout computed in
the browser at load (not persisted, not reproducible); node size by degree,
labels only on hubs; search; click-to-inspect panel with clickable
neighbours; legend checkboxes per community; dashed edges for non-EXTRACTED;
hyperedges as convex hulls. **No click-through to source, no line numbers in
the panel, no edge-relation filter, no path/impact UI, offline-incapable.**
5,000-node cap.

## Query surface

CLI: `query "<q>" [--dfs] [--budget]`, `path A B [--undirected]`,
`explain X`, `affected X [--relation] [--depth]`, `god-nodes`, exports.
MCP tools: `query_graph(question, mode, depth, token_budget,
context_filter)`, `get_node(label)`, `get_neighbors(label, relation_filter,
token_budget)`, `get_community(id)`, `god_nodes(top_n,
exclude_hubs_percentile)`, `graph_stats`, `shortest_path(source, target,
max_hops, undirected)`, PR triage tools. All outputs are free text
(`EDGE A --calls [EXTRACTED context=call]--> B at=file.py:L392`), truncated
against a token budget with an explicit `[!] TRUNCATED` banner. Query is
IDF + trigram + tiered label matching, seeded BFS/DFS; **fuzzy endpoint
resolution silently substitutes** (`path "to_json" ...` on a corpus with no
`to_json` returned a path from `to_html()` with no warning).

Agent integration: a 713-line SKILL.md runbook; a "fast path" (graph exists +
question => just query); a PreToolUse hook injecting "MANDATORY: run
`graphify query` before grepping"; CLAUDE.md always-on block; post-commit
rebuild; merge driver for graph.json.

## Determinism (measured)

`graph.json` and `graph.html` byte-identical across hash seeds, worker
counts and absolute paths on the AST-only, no-LLM, same-commit path. The
**folder** is not: `GRAPH_REPORT.md` stamps today's date, `manifest.json`
and `cache/` carry timestamps, `.graphify_root` is absolute,
`built_at_commit` churns per commit, and any LLM step (docs, labels) is
nondeterministic. The README tells teams to commit `graphify-out/`, which
guarantees diff noise.

## What svarupa exploits

- Evidence precision: ours is `file:start-end`, on every node AND edge,
  re-verified at build; theirs is a line string with no verification and
  empty for externals.
- Deterministic whole artifact, offline HTML, positions persisted; theirs
  needs a CDN and lays out per load.
- Honest resolution bins vs a 0.85 "inferred" that is name matching; our
  fail-closed refusal vs their silent fuzzy substitution in `path`.
- Structural module identity in a committed lockfile vs communities named by
  an LLM baked into every node.

## What svarupa should copy

- **The query surface**, nearly verbatim in shape: `query_graph`,
  `get_node`, `get_neighbors(relation_filter)`, `shortest_path(max_hops,
  undirected)`, `affected(relation, depth)`, `god_nodes`, `graph_stats`, and
  a CLI mirror. Structured JSON output (their gap), exact-match by default
  with an explicit ambiguity list (their fuzzy substitution is the anti-
  pattern), truncation banner honesty.
- `graph.json` conventions: fixed leading keys, `context` typed sub-relation
  on edges, `built_at_commit`, hyperedges slot; rationale nodes from
  docstrings and `# NOTE/WHY` comments.
- Report content: god nodes, surprising cross-community edges with a "why",
  import cycles, cohesion, knowledge gaps, suggested questions from
  betweenness centrality.
- Community post-processing (split >25%, re-split low cohesion, total-order
  re-index, greedy remap to previous run) for our presentation-only
  clustering.
- Explorer interactions: search with community swatch, legend checkboxes,
  degree-sized nodes with hub-only labels, click-to-inspect with clickable
  neighbours, dashed non-resolved edges. Ours must add what they lack:
  click-through to the line, edge-relation filters, path/impact in the UI.
- Agent integration: the fast path, the PreToolUse nudge, the CLAUDE.md
  block, `.svarupaignore` merged with `.gitignore` (we have), a per-file
  content-hash cache keyed by tool version for `--update`.
