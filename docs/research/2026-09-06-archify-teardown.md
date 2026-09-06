# Archify teardown (reverse-engineering, 2026-09-06)

Source: cloned and read `tt-a1i/archify` (MIT, `v2.17.0-dev.1`, fork of
`Cocoon-AI/architecture-diagram-generator`). Quotes are from its SKILL.md,
schemas, renderers, and references. This replaces the earlier browser-only
study of shipped examples, which had informed our visual language but not the
pipeline.

## The headline

**Archify is not "an LLM writes HTML."** It is a ~7 MB zero-dependency
Node.js compiler shipped inside the skill. The agent's only creative output is
a small typed JSON file (JSON Schema 2020-12, `additionalProperties: false`);
deterministic code renders everything. Spec-to-HTML is byte-deterministic;
prompt-to-spec is not ("Fresh authorship means new stable IDs, domain wording,
and layout"), which is exactly the gap svarupa closes: our IR is derived from
code, so we inherit Archify-grade rendering determinism end to end.

## Pipeline (what its SKILL.md makes the agent do)

1. Route to one of five types; read exactly three files (schema, common
   schema, one example) — the SKILL aggressively bounds context ("Do not read
   renderer source... before the first candidate").
2. Author the JSON IR immediately ("Do not plan exact coordinates in prose").
3. `validate` loop with a 9-check showcase gate over the *rendered SVG*.
4. `deliver`: atomic render + SHA-256 receipts.
5. `visual-check`: headless Chrome measures overflow at four viewports.

**It does not read code by default.** Repository evidence is an opt-in,
architecture-only mode: the agent hand-authors `sources[] {path,line}` per
component, and a verifier runs real git against a pinned 40-char SHA
(`git cat-file -e`, blob check, line-count check). Nothing verifies topology:
a diagram can mix one verified box with nine invented ones and pass 9/9.

## Layout

Mostly **agent-authored pixels policed by validators**: architecture `pos
[x,y]`/`size [w,h]`, sequence message `y` values. Workflow v2 is their newest
work and is a real constraint solver — they are migrating *away* from
LLM-authored coordinates, i.e. toward where svarupa already is. Edge routing
is renderer-owned: orthogonal, deterministic port spread (16px corner
gutters), 24px endpoint stubs, rounded corners, side contracts.

## Weaknesses svarupa exploits

- Evidence is optional, per-component only (never on connections), no
  coverage measure. We put evidence on every box AND every arrow, always.
- Evidence mode is architecture-only, GitHub-public-only, and pins a SHA with
  no staleness signal. Our lockfile + drift detection is the freshness story.
- ≤12 primary nodes, no drill-down hierarchy, no incremental regeneration;
  its compare mode diffs two hand-authored snapshots, not two commits.
- Prompt-to-diagram is nondeterministic, so no CI governance is possible.

## Worth adopting (exact values, queued as candidates)

- Label/route legibility math: label mask width ≈ 6.5px × ASCII units + 13px
  (CJK ×2), clear gap > mask + 8px; route rhythm: every nonzero segment ≥8px,
  interior segments ≥16px; shared-endpoint port spread with 16px gutters;
  adjacent-rank baseline 120px. These are automatable legibility checks — the
  gap our review #11 named (no automated legibility gate beyond tracks).
- Diagnostics as the repair channel: stable code + subject + measured
  evidence + machine-verified `supportedFixes[]`. Ours are close; the
  "verified suggested fix" idea is stronger than prose fixes.
- Delivery ritual: SHA-256 receipts for spec and artifact; explicit
  forbidden-counterfeit list (no `overflow: hidden` to fake a fit).
- Styling to compare against ours: JetBrains Mono; dark canvas `#020617`,
  mask `#0f172a`, borders `#1e293b`; translucent fill + saturated stroke
  pairs per kind (fill alpha 0.3-0.5 dark / 0.15-0.2 light); an opaque
  `--mask` colour so arrows never show through translucent boxes.
- A four-viewport no-scroll containment gate run in a real browser.

## Verdict for positioning

Archify's engineering is honest about what it is: a superb deterministic
renderer for agent-authored claims, with optional after-the-fact spot checks.
Its own docs concede the boundary ("Never infer runtime causality...").
Svarupa's thesis survives contact: derived-from-code IR + evidence on every
element + committed lockfile diffs is the part Archify structurally does not
have and is moving toward only at the layout layer.
