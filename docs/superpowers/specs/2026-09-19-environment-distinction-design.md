# Svarupa — Environment Distinction Design (extraction slice)

Date: 2026-09-19
Status: implementing (extraction layer first; diagram/lockfile surfacing is the
next slice and gets its own review)

## Requirement (user, 2026-09-19)

Users "should be able to distinguish between production, stage, qa, dev and
development" — environments must be visible, evidence-backed facts, like
everything else svarupa emits.

## Evidence survey (5 real repos on this machine, 2026-09-19)

| Signal | Seen in | Precision |
|---|---|---|
| Env-suffixed config filenames (`values-dev.yaml`, `values-prod.yaml`) | e-kisanmitra | Highest — filename IS the env label |
| Build-profile manifests (`eas.json` keys `development`/`preview`/`production`) | MedMind | High — keys are literally env names |
| CI workflow `environment:` keys; branch/tag triggers | svarupa (release.yml), descovo-backend | High for `environment:`; triggers say "what ships from what ref" |
| Dockerfile `ENV NODE_ENV=production` | descovo-backend | Medium — one env only |
| Runtime code checks (`process.env.NODE_ENV === ...`) | descovo-web | Low — noisy; out of scope |
| docker-compose env variants (`.prod`, `.override`) | none of the 5 | Do not over-index; support filename pattern anyway |

Environments are heavily *implicit* in real repos (prod defined by a manual
dispatch + a secret ARN; Vercel dashboard only). So "no environment evidence"
must be an honest absence, and low-confidence hints must not be fabricated
into claims.

## Extraction design (this slice)

New module `svarupa/extract/environments.py`, run during stage 2 (extract).

**Canonical environments** (alias folding, case-insensitive):
`development` (dev, local), `qa` (test is NOT folded — a test role is a file
role, not a deploy env), `staging` (stage, uat, preprod, preview), `production`
(prod, live, main-prod? no — only exact tokens/aliases). An unrecognized env
token is kept verbatim (e.g. `preview` before folding? no: preview→staging).

Decision: aliases fold to the four canonical names; unmatched tokens are
kept as-is so nothing is lost and nothing is invented.

**Sources extracted (each with file:line evidence):**

1. **Filename-pattern environments** — paths matching
   `(^|[./_-])(dev|development|qa|staging|stage|uat|preprod|preview|prod|production|live)([./_-]|$)`
   under config-looking locations (`*.yaml/yml/json/toml/py` settings files,
   `values-*.yaml`, `settings/`, `config/`, `environments/`, `overlays/`).
   Evidence = the file itself (`0,0` whole-file is only legal for empty files,
   so cite line 1... decision: cite the filename line is impossible — a file
   path fact cites the file with start_line=1, end_line=1? No: use the first
   line of the file; the claim is "this file exists and is named so").
2. **`eas.json` / profile manifests** — JSON parse; each top-level profile key
   under `build` is an environment with its key's line as evidence.
3. **GitHub/GitLab workflow `environment:` keys** — YAML parse of
   `.github/workflows/*.yml`, `.gitlab-ci.yml`; evidence = the key's line.
   Branch triggers are recorded as attributes (`ref: main`) on the environment,
   not as environments themselves.
4. **Dockerfile `ENV NODE_ENV=<value>`** — line evidence.

**Data structure:**

```python
@dataclass(frozen=True)
class EnvironmentFact:
    name: str            # canonical or verbatim token
    source: str          # "filename" | "profile" | "workflow" | "dockerfile"
    evidence: tuple[Evidence, ...]   # non-empty, always
    attrs: tuple[tuple[str, str], ...]  # e.g. ("ref", "main"), ("path", ...)
```

**Honesty rules:**
- No evidence → no fact. A repo with no signals reports "no environments
  declared" — an absence statement, not a failure.
- `NODE_ENV=development` in a Dockerfile and a `prod` values file coexist;
  contradiction is reported, never resolved by fiat.
- Test files/configs (FileRole test) contribute nothing (same gate as
  semantics.py entrypoints).

**Surfacing (NEXT slice, not this one):** deploy-topology regions/badges per
environment, cards, and a `environment` lockfile record kind — each needs the
review protocol before landing. Extraction lands first so the surfacing design
can be reviewed against real extracted data.

## Verification

`tests/test_environments.py`: synthetic repos per source, decoy files (a
`development-notes.md` must NOT yield an environment; `NODE_ENV` in a comment
must not), alias folding, contradiction reporting, determinism. Full suite +
ruff + pyright green.
