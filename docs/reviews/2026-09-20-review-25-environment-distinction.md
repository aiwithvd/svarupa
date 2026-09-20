# Review #25 — Environment distinction & layout changes (2026-09-20)

Scope: `34e1759..HEAD` — environment extraction/surfacing/diagram overlays,
label anchoring, drill-heading humanization, packaging/release.
Protocol: docs/reviews/TEMPLATE.md. Reviewer read the spec
(`2026-09-19-environment-distinction-design.md`), the new source, and the new
tests. The delegated full-protocol pass timed out twice; this record is the
focused review the parent performed directly on the highest-risk surface
(`extract/environments.py`, `detect.classify` contract, lock/determinism
surface). A full second pass remains valuable.

## Findings

### MUST-FIX

**F1 — `eas.json` profile evidence can cite the wrong line.**
`svarupa/extract/environments.py` `_locate_profile_line` finds the profile
key's line with `needle in line` where `needle = f'"{name}"'`. A string
*value* containing the quoted profile name matches too: with
`"preview": {"channel": "production"}` sorting before the real
`"production": {` key, the citation lands on the value line — evidence that
points at a line that does not make the claim. That is the one invariant the
product exists to protect. Remedy: require key shape,
`re.search(rf'"{re.escape(name)}"\s*:', line)`.

### SHOULD-FIX

**F2 — weak aliases `live` / `local` fire on path segments.**
`_ENV_RE` treats `live` and `local` as env tokens anywhere in a
config-looking path. `deploy/live/reload.yaml` claiming `production` and
`config/local/overrides.yaml` claiming `development` are plausible false
claims in the wild (e-kisanmitra's own tree is LiveKit-heavy). In a workflow
`environment:` key these tokens are strong; in a path segment they are weak.
Remedy: split the alias table by source — workflow/profile/dockerfile values
keep `live`/`local`; filename tokens drop them (or require them as the full
stem: `values-live.yaml` yes, `live/reload.yaml` no).

### CONSIDER

**F3 — the self-walk does not consult ignore files.** Documented boundary in
the module docstring; a repo that gitignores its real `values-prod.yaml`
secrets-adjacent overlays will be silently misread as "not declared". Accept
for this slice; record the boundary in the spec (already done) and revisit if
a user report arrives.

**F4 — `uv.lock` grew by 881 lines during this cycle** (mcp subtree
re-resolution at baseline commit). Runtime pins (`tree-sitter*`, `networkx`,
`pyyaml`, `pathspec`) verified unchanged. Note only; lockfile hygiene at
baseline commits deserves a glance in future reviews.

## Verified non-findings

- `classify(rel, b"", None, None)` passing empty content looked like a
  generated-content-detection gap; `_looks_generated(data)` is consulted only
  when `lang is not None`, so empty bytes change nothing. Not a bug.
- NFC normalization of walk-derived evidence paths: covered by
  `Evidence.__post_init__` (norm_path).
- Determinism of the walk: scandir sorted by codepoint, final sort, set→tuple
  dedupe; byte-stability tests exist.
- Lockfile churn: byte-identity test with and without environment facts
  exists (`tests/test_environments.py`).

## Summary

MUST-FIX 1 · SHOULD-FIX 1 · CONSIDER 2
