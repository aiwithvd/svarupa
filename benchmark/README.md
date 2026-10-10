# Benchmark

Svarupa is scored on the public repositories in `corpus.toml`, each pinned to
one commit, against facts a person checked by reading the source.

## What the score covers

Recall measures what Svarupa claims to extract: modules, dependencies between
them, compose services, datastores and queues, resolved calls, and HTTP
endpoints for FastAPI, Flask, Express and NestJS. Features Svarupa does not
extract yet (for example routes in Django, Next.js, Gin, Echo, Fiber or
Spring) are not part of the expected facts, so a high recall means "what it
claims is right and complete", not "it sees everything". Each expected file
says in its top comment what was deliberately left out.

## Writing expected facts

`expected/<name>.toml` holds two lists:

- `[[must]]`: facts Svarupa must produce. `record = [...]` is a lockfile
  record (`module`, `dep`, `endpoint`, `datastore`, `service`, `queue`,
  `entrypoint`, `role`, `environment`). `call = "src -> dst#name"` is a
  resolved file-level call. `why` starts with `path:line` in the pinned
  commit and says what that line shows.
- `[[must_not]]`: probes that must not appear, with the reason.
- `violation = "<check> <path>:<line>"` is a health violation (see
  `docs/health.md`). The line is where the violation's evidence starts: the
  function's first line, the class line, line 1 for a large file, the first
  import line of a cycle, the first line of the first copy of a duplicated
  block.
- `design = "<unit> <style>"` is the design style a unit follows (`.` is
  the repository root; styles in `docs/design.md`).

Rules:

1. Read the source first. Write the facts before running Svarupa on the repo.
2. After a run, change a fact only if its own citation is shown wrong by the
   source. Never change a fact because Svarupa disagrees; a miss is a finding.
3. At least 15 `must` and 4 `must_not` per repo.

## Commands

    uv run python scripts/benchmark.py validate   # citations point at real lines
    uv run python scripts/benchmark.py run NAME   # score one repo
    uv run python scripts/benchmark.py check      # what CI runs
    uv run python scripts/benchmark.py accept     # record new scores

`check` fails when a fact that was found goes missing, a probe starts firing,
or a repo's expected facts changed since the last `accept`. If the change is
intended, run `accept` and explain why in the pull request.
