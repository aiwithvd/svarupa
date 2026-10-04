# Svarupa: Benchmark (step 1b)

Date: 2026-10-04
Status: design approved, spec under review
Parent: `2026-10-03-system-health-umbrella-design.md`

## Goal

Answer "do the diagrams reflect the real system?" with numbers before users
have to tell us. Score Svarupa on pinned public repositories against facts a
person checked by reading the source, fail CI when a change makes the score
worse, and decide each language pack's maturity from those scores.

## Decisions (maintainer, 2026-10-04)

| Question | Decision |
|---|---|
| Where repos come from | Cloned at a pinned commit SHA, cached; nothing third-party is copied into this repository |
| Expected answer | Curated key facts per repo: must-find facts and must-not-find probes, each with a `why` that cites the source |
| CI | Its own `benchmark` job on every PR, made a required check once green; fails on regression |
| Maturity | A pack is stable when at least two of its repos reach 90% recall with no probe firing |
| Corpus | 11 repos (below) |

## Corpus

| Name | Repository | Language focus | License |
|---|---|---|---|
| fastapi-template | fastapi/full-stack-fastapi-template | Python (FastAPI) + TS frontend | MIT |
| microblog | miguelgrinberg/microblog | Python (Flask) | MIT |
| bakerydemo | wagtail/bakerydemo | Python (Django) | BSD-3-Clause |
| nestjs-prisma | notiz-dev/nestjs-prisma-starter | TypeScript (NestJS) | MIT |
| express-boilerplate | hagopj13/node-express-boilerplate | JavaScript (Express) | MIT |
| taxonomy | shadcn-ui/taxonomy | TypeScript (Next.js) | MIT |
| gin-realworld | gothinkster/golang-gin-realworld-example-app | Go (Gin) | MIT |
| go-clean-arch | bxcodec/go-clean-arch | Go | MIT |
| go-clean-template | evrone/go-clean-template | Go | MIT |
| petclinic | spring-projects/spring-petclinic | Java (Spring) | Apache-2.0 |
| spring-realworld | gothinkster/spring-boot-realworld-example-app | Java (Spring) | MIT |

Commit SHAs are pinned in `benchmark/corpus.toml` and only change in a PR
that also re-checks that repo's expected facts.

## Expected facts

`benchmark/expected/<name>.toml`:

```toml
[[must]]
record = ["dep", "backend/app/api/routes", "backend/app/crud"]
why = "backend/app/api/routes/items.py:7 from app import crud"

[[must]]
call = "backend/app/api/routes/items.py -> backend/app/crud.py#create_item"
why = "backend/app/api/routes/items.py:58 crud.create_item(...)"

[[must_not]]
record = ["datastore", "mongodb"]
why = "only PostgreSQL is configured (docker-compose.yml:12); no Mongo client"
```

- `record` is a lockfile record (`module`, `dep`, `endpoint`, `datastore`,
  `service`, `queue`, `entrypoint`, `role`, `environment`): module-level,
  line-free, the level a person draws an architecture at.
- `call` is a file-level call: `<source file> -> <target file>#<name>`. It
  counts as found only when the edge is resolved (not a candidate); a probe
  fires on any edge.
- Every `must` cites `path:line` in the pinned commit; a validator checks the
  file and line exist. Every `must_not` gives its reason.
- At least 15 `must` and 4 `must_not` facts per repo.
- **Facts are drafted by reading the source before Svarupa is run on that
  repo.** Afterwards a fact may change only if its own citation is shown to be
  wrong by the source, never because Svarupa disagrees; each such change is
  recorded in the PR.

## Scoring and the CI gate

- Recall per repo: found must-facts over all must-facts, also per fact kind.
- Probes: the list of must-not facts that appear.
- `benchmark/scores.json` holds the accepted result per repo: recall, counts,
  and the exact lists of missed facts and fired probes.
- `scripts/benchmark.py check` fails when a fact that was found is now
  missed, a probe fires that did not, or a repo has no accepted score. An
  intended change is accepted with `scripts/benchmark.py accept` in the same
  PR, with the reason in the PR description.
- The job writes a score table to the GitHub step summary.

## Maturity

`stable_languages(corpus, scores)` returns the languages with at least two
repos at recall >= 0.9 and zero fired probes. A test asserts every pack's
`maturity` equals that rule, so promotion and demotion are reviewed code
changes backed by numbers. Python and TypeScript are labelled stable today;
if the benchmark does not support that, they are demoted until it does.

## Errors

- Clone failure, missing commit, or unreadable expected file: the command
  errors with the repo name and reason; it never passes silently.
- Validation problems (missing citation, cited file or line absent, too few
  facts) fail `validate` and `check`.

## Out of scope

- The task test with engineers new to a stack, and user feedback channels.
- Expected violations for health checks and design styles (added to these
  files by 1c).
- Benchmarking performance or memory.
