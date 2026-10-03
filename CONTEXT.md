# Svarupa

Svarupa reads a repository and draws what exists, never what was meant. Every
term below serves one promise: a claim is shown only when a source line proves it.

## Evidence and honesty

**Evidence**:
A `file:line` range in the repository that proves one element exists. An element without evidence is not emitted.
_Avoid_: reference, source location, proof

**Role**:
What a scanned file is for: source, config, test, generated, vendored or tooling. Only source and config shape the architecture; the rest stay queryable.
_Avoid_: file type, category

**Resolved**:
A call or import pinned to a definite target inside the repository, or to one of a few named candidates.
_Avoid_: found, linked

**Known-external**:
A target the project declares as a dependency or that belongs to the language's standard library. Must be justified, never a fallback.
_Avoid_: external (as a catch-all for failures)

**Unresolved**:
A target that is neither resolved nor known-external. Counted and reported, never drawn.
_Avoid_: external, unknown, failed

## Structure

**Module**:
A directory holding source the build can extract. The unit the lockfile is keyed on; its identity comes from the file tree, never from grouping.
_Avoid_: component, community, package

**Community**:
A group of modules found by clustering, used only to arrange the picture. Never an identity and never in the lockfile.
_Avoid_: module, cluster id

**Environment**:
A declared deploy target, folded to one of production, staging, qa or development, or kept verbatim when it matches none.
_Avoid_: stage, branch, profile

## Diagrams

**Diagram**:
One of the six kinds: architecture, module dependencies, data flow, request flow, deploy topology, ERD.
_Avoid_: chart, graph

**View**:
One drawn canvas of a diagram: its root, or a level reached by drilling.
_Avoid_: page, screen, spec (in user-facing text)

**Box**:
A drawn element in a view, always carrying evidence.
_Avoid_: node (in user-facing text), card

**Drill**:
Opening the view behind a box, in place or on its own.
_Avoid_: zoom, expand, navigate

**Passport**:
The panel that describes one box: its kind, connections, reach and cited lines.
_Avoid_: details panel, inspector, tooltip

**Absent**:
A diagram kind the evidence cannot produce, named with the reason. Never drawn empty.
_Avoid_: missing, unavailable, empty

**Withheld**:
A view that was derived but failed geometry validation, so it is not drawn and the reason is stated.
_Avoid_: hidden, skipped, absent

## Governance

**Lockfile**:
The committed file of architecture facts, one per line, with no line numbers.
_Avoid_: snapshot, manifest, baseline

**Record**:
One fact in the lockfile: a kind followed by its fields.
_Avoid_: entry, row, line

**Delta**:
The records added and removed between a base lockfile and the current one. There is no "modified".
_Avoid_: diff (for the result), change set

**Drift**:
A committed base lockfile that no longer matches one regenerated from the base branch's code.
_Avoid_: stale diff, mismatch

## Design

**Language pack**:
The data that teaches the generic extractor one language: how imports, functions and branches look, and how an import name maps to a file. A language without a pack is detected but not analyzed.
_Avoid_: plugin, parser, language support

**Design style**:
A known way to arrange code, such as layered, hexagonal or feature modules, written as rules over parts: which parts exist and which may depend on which. A system can use one style between its services and another inside each service.
_Avoid_: pattern, architecture (for the style itself)

**Target design**:
The design style and the assignment of modules to its parts that the code is measured against. It is inferred until a person accepts it, then accepted and committed.
_Avoid_: ideal design, intended design, blueprint

**Check**:
One rule in the catalog that code is measured against, such as a layer direction, a long function or an endpoint without auth. Each check names the standard it comes from.
_Avoid_: lint rule, detector

**Violation**:
One place, with evidence, where code breaks a check or the target design.
_Avoid_: smell, issue, warning, finding (findings are Svarupa's own diagnostics)

**Maturity**:
Whether a style or check is stable, proven on the benchmark repositories, or experimental and labelled so in every report.
_Avoid_: beta, confidence

**Health**:
The graded result per quality area, built only from violations by one published formula.
_Avoid_: quality score, rating
