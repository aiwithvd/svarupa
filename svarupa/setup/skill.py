"""The agent skill: what an agent needs to know to run svarupa well.

The content lives here, in the wheel, so `svarupa setup skill` works from an
installed tool with no source checkout. The repository's own copy at
`skills/svarupa/SKILL.md` is generated from this constant and a test asserts
they are byte-equal, because two hand-maintained copies of one document drift,
which is the registry lesson applied to prose.

Every `--flag` this document names is checked against the real CLI parser by a
test, because a document written from recall is how eleven diagnostic
descriptions came to describe the wrong codes.
"""

from __future__ import annotations

from typing import ClassVar

from svarupa.setup.base import Target

SKILL_MD = """\
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
  diagram; click a drillable box (marked with a chevron) to expand it in
  place; every box and arrow shows its citations on hover and in the
  evidence panel.
- `graph.json` - the full knowledge graph: nodes, edges, evidence. Query
  this when you need relationships programmatically.
- `REPORT.md` - the resolution scorecard: how many edges resolved, per
  language and edge kind. Read this before trusting call edges.
- `diagrams/*.json` - the positioned diagram data, one file per view.

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
- Partial failure degrades: one hostile or broken file becomes a diagnostic,
  and the rest of the repository is still analyzed.
- Routes, tasks and roles are extracted for Python (FastAPI, Flask, Celery)
  in this build; declared entrypoints also cover package.json `bin`. A
  JavaScript or TypeScript service showing no api/worker role means
  not-yet-extracted, not "no API"; REPORT.md states this boundary per run.
"""


class SkillTarget(Target):
    name: ClassVar[str] = "skill"
    summary: ClassVar[str] = "the agent skill, installed into .claude/skills/svarupa/"

    def files(self) -> tuple[tuple[str, str], ...]:
        return ((".claude/skills/svarupa/SKILL.md", SKILL_MD),)

    def next_steps(self) -> tuple[str, ...]:
        return ("Commit .claude/skills/svarupa/SKILL.md so every agent session sees it.",)
