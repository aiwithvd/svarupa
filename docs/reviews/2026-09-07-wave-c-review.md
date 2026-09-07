# Review #18: Wave C, the Graphify-class graph (`65f1f15`, `cd4f1b3`)

**Date:** 2026-09-07
**Reviewer:** Fable (adversarial)
**Fixes:** the commit following this record
**Verdict:** *"The query surface keeps its two promises against Graphify ...
but graph.json schema 2 is not yet honest at the level this project holds
itself to."*

The demonstrations: the shipped descovo artifact carried two pairs of nodes
with the same id (a file with two routers declaring the same paths collapsed
four endpoints into two ids, and `GraphIndex` silently kept the last); it
named `built_at_commit aaf9a54` while carrying twelve nodes from files that
commit did not have (the tree was dirty); the rationale line scanner emitted
a `todo` node from a URL string, a `note` from a string literal, "docstrings"
for classes written inside a docstring and a string, and cited a one-line
docstring as spanning fourteen lines into the next class; the explorer's
path caption wrote `A -> B` for an arrow drawn `B -> A` and left earlier
paths lit; 17 of the reviewer's 20 mutations survived the suite.

## Findings and triage

| # | Finding | Rank | Disposition | Why |
|---|---|---|---|---|
| F1 | Duplicate node ids in graph.json (route id assumed one route per file, method, path; rationale id assumed one fact per line) | MUST-FIX | **Accepted, fixed** | The handler is part of a route id and the kind part of a rationale id; a `_fresh` helper suffixes any remaining collision deterministically, and `graph_json` refuses to write duplicate ids at all. Test builds two routers with the same path in one file and asserts uniqueness and two `route` neighbours |
| F2 | `built_at_commit` named HEAD while the graph described a dirty working tree; an untracked directory inside another repository borrowed that repository's HEAD | MUST-FIX | **Accepted, fixed** | `git_state` returns HEAD plus `worktree_dirty` (`git status --porcelain` under the root) and returns nothing when no file under the root is tracked. graph.json carries both; SKILL.md says the graph describes the working tree. Test covers clean, dirty and untracked-inside-a-repo |
| F3 | The rationale scanner emitted claims from strings and from `class` lines inside strings, and cited wrong ranges | MUST-FIX | **Accepted, fixed** | Rewritten on tree-sitter: a `comment` node is a comment, a `string` that is the first statement of a module or class body is a docstring, nothing else is either; markers anchor at the start of the comment text. The reviewer's tricky fixture is the test, byte for byte, with the parser's answer as the expected set |
| F4 | `affected` counted a docstring as blast radius; paths walked through `rationale_for` | SHOULD-FIX | **Accepted, fixed** | Both exclude rationale edges (unless the relation asked for is `rationale`) and say `excluded: [rationale]`; `affected` hits carry `via`, the relation they were reached by |
| F5 | Path tool caption claimed a direction the picture contradicts; stale highlights accumulated | SHOULD-FIX | **Accepted, fixed** | The caption follows each arrow's drawn direction with left and right arrows and says "undirected"; a new selection clears the previous path |
| F6 | Token budget measured on compact JSON while the CLI prints indented; edges always sacrificed first | SHOULD-FIX | **Accepted, fixed** | Costs are measured on `indent=2`; nodes take at most 60 percent of the budget so edges keep a share; tests bound the printed size and check edges survive |
| F7 | `resolve` hid cross-tier ambiguity and did not say how a label matched | CONSIDER | **Accepted, fixed** | Every tier is consulted; each hit and each ambiguous candidate carries `matched_by`; an id that is also another node's label is an ambiguity |
| F8 | 17 reviewer mutations survived | SHOULD-FIX | **Accepted, fixed** | All on `scripts/mutate_query.py` (26 -> 48) with pinning tests: route and exposes evidence, external evidence from every importing line, both architecture gates on fact nodes, label truncation, marker anchoring, suffix filter, god_nodes ties, edge truncation, banner totals, tier precedence. The two JS mutations (directed BFS, legend boolean) have no runner in the suite and are recorded as such |
| F9 | SKILL.md omitted three contexts and FIXME; zero hits exited 0 | CONSIDER | **Accepted, fixed** | Wording complete; zero hits is not an answer and exits 1 |
| F10 | A NOTE between a decorator and its `def` lands on the module | CONSIDER | **Recorded, not changed** | Attachment uses the definition's evidence span; whether that span should start at the decorator is a question for the extractors, not for rationale. Carried |
| F11 | `lastScope` stale after drilling | CONSIDER | **Accepted, fixed** | Neighbour clicks resolve in the view that is open |

## What the review confirmed sound

Byte determinism on both repos across seeds; fail-closed gates on routes,
externals and rationale in test and vendored files; exact match with no fuzzy
path; structured output and exit codes as documented; explorer code builds no
markup from repository text and uses no node id in a selector; the "why" nodes
on descovo are useful one-line summaries, the demo's are pydantic model
summaries (noise, and true).

## Promoted to the decision log

1. **A node id is a key, and the emitter proves it** (F1). Identity was
   assumed from a tuple that is not unique in real code; the emitter now
   refuses to write two nodes under one id, and every consumer that indexes
   by id is protected by that one gate.
2. **A commit hash describes a tree only with its dirty flag** (F2). The
   graph is built from the working tree; naming HEAD alone is a claim about
   files the commit may not contain.
3. **What is a comment is the parser's call** (F3). A line scanner over two
   languages with strings and nested quotes cannot tell a docstring from a
   string, and every false claim it made would have been caught by asking
   the parser already loaded for the file.
4. **A search that is not a dependency is not a hop** (F4). Reachability
   answers ("what depends on X", "how does A reach B") follow dependency
   edges only, and the answer names what it excluded.

## Measured after

```
728 passed, 1 skipped, 2 xfailed; ruff clean; pyright strict 0 errors
mutations: query 48/48 (was 26)
demo and descovo: byte-identical across seeds, 0 withheld; unique node ids;
descovo graph.json: built_at_commit set, worktree_dirty true (as it was)
```

## Riskiest remaining untested assumption

The explorer's JavaScript has no runner in the suite: its behaviour is
verified by hand in a browser and pinned only by source-string presence, and
the two JS mutations the reviewer wrote would survive today. A node-based
harness over the extracted script is the next test the viewer needs.
