# Svarupa: Agent Rules and Check My Change (step 1d)

Date: 2026-10-24
Status: design approved, spec written
Parent: `2026-10-03-system-health-umbrella-design.md` (section 1d); builds on
1c-1 to 1c-4

## Goal

Most code is written by AI agents. An agent should learn the target design
and the health rules before it writes code, and prove after writing that
its change added no violation and lowered no grade. Svarupa gives it both,
from fixed rules and evidence, with no AI in the pipeline.

## Decisions (maintainer, 2026-10-24)

| Question | Decision |
|---|---|
| Scope | Health rules in `get_design_rules`, a new `svarupa check`, and an agent workflow in the skill. CI wiring is phase 2, IDE rule files are phase 3 |
| Baseline | A git ref (built in a temporary worktree) or an existing `graph.json` |
| Fail rule | Exit 3 on any new violation at or above `--min-severity` (default minor), or any area grade getting worse |

## 1. Health rules in `get_design_rules`

The result gains `checks`, one entry per catalog check, in catalog order:

```json
{"id": "query-in-loop", "rule": "Query in a loop (N+1)", "area": "performance",
 "severity": "major", "limit": 0, "fix": "Load everything the loop needs in one query before the loop..."}
```

- `rule` is the check title, `limit` the threshold (violated when the
  measured value is strictly greater), `fix` the explanation's fix text
  (1c-4), so the rule and the explanation never disagree.
- `checks` is present even when the artifact has no design: an agent still
  gets the health rules. `available` keeps its meaning (a design exists).
- The same data reaches MCP `get_design_rules` (the MCP tool calls the same
  function).

## 2. `svarupa check`

```
svarupa check <path> --base <git ref | path/to/graph.json> [--min-severity minor|major|critical|blocker] [--json]
```

- **Head:** the working tree at `<path>` as it is, so an agent's
  uncommitted edits count. Analyzed in memory (scan, extract, build,
  assess with the same design.yaml exceptions and auth markers the main
  run uses); nothing is written into the repository.
- **Base from a git ref:** `git worktree add --detach <tmp> <ref>` under a
  temporary directory, analyzed the same way, then `git worktree remove
  --force` in a `finally`, so a failed run leaves no worktree behind. The
  base uses the head's `.svarupa/design.yaml` (the rules being enforced
  are today's rules).
- **Base from a file:** a path ending in `.json` is read as an artifact's
  `graph.json`; its `health` key is used. No `health` key exits 1 with a
  diagnostic.
- **Matching** (line shifts never make a violation new): a violation's key
  is `(check, file, anchor)` where `anchor` is its `symbol` when it has one,
  else its message with every run of digits replaced by `#`. Keys are
  compared as multisets: a third copy of a finding that existed twice is
  one new violation. A finding that moved to another file (a rename) is
  matched too.
- **Output (text):** "N new, M fixed" then each new violation as
  `file:line check: message` plus a `fix:` line from the explanation, each
  fixed violation as one line, and each area whose grade changed
  (`security A -> C`). `--json` prints `{new, fixed, grades:
  {area: [base, head]}, failed}`.
- **Exit codes:** 3 when a new violation is at or above `--min-severity`
  or any area's grade is worse than the base (A best, E worst; `None`
  never compares as worse); 0 otherwise; 1 on an unknown ref, a missing
  git, a path outside a git checkout with a ref base, or an unreadable
  base file; 2 on usage errors (argparse).
- Violations accepted as exceptions in design.yaml are neither new nor
  fixed: only `health.violations` is compared.

## 3. The skill

`SKILL_MD` (and the generated `skills/svarupa/SKILL.md`) gains a section
"Before and after you write code":

1. Run `svarupa <path>` once, or reuse a fresh `.svarupa/`.
2. Before editing: `svarupa query <path>/.svarupa get_design_rules --json`;
   respect each unit's forbidden directions and the health rules.
3. After editing: `svarupa check <path> --base HEAD --json`.
4. Exit 3: fix each new violation (`svarupa explain <check>` says how), or,
   when it is right on purpose, propose a `design.yaml` exception with a
   reason; never weaken a rule silently.

The MCP sentence names the real number of tools; a test derives it from
the MCP server's tool list.

## Testing

- `get_design_rules`: lists every catalog check with its fix; present with
  and without a design.
- Matching: a function moved down the file is not new; a second copy of a
  finding is new; a removed finding is fixed; digits in messages do not
  break matching.
- `check` with a `graph.json` base; with a git-ref base in a temporary
  repository (head adds a violation, exit 3); the worktree is gone after a
  success and after a failure; an unknown ref exits 1.
- Grade drop alone exits 3; `--min-severity major` ignores a new minor.
- An excepted violation is not new.
- The skill names only real flags (existing test) and the right tool count.
- Mutations: line numbers in the match key; exceptions counted as new.

## Out of scope

Running `check` in CI and pull request comments (phase 2); IDE rule files
(phase 3); an MCP `check` tool (the MCP server reads an artifact, not a
repository); comparing design proposals between base and head.
