# Spike 0 Results

**Date:** 2026-08-30
**Status:** Gate PASSED. Proceed to P1 with three design amendments.
**Code:** `spike/probe.py`, `spike/perturb.py` (throwaway, delete after P1-0)

---

## Purpose

Validate the riskiest assumption before writing product code: *structural module identity stays stable under small code changes, and grouping over it produces something an engineer recognizes.*

Environment: macOS (APFS), Python 3.12.12, uv 0.10.0.
Subjects: `pallets/flask`, `encode/starlette`, `psf/requests`, plus a multi-project directory.

---

## Gate results

| # | Gate | Target | Result |
|---|---|---|---|
| 1 | Communities recognizable | >70% | **PASS** (qualitative, see §4) |
| 2 | Linux vs macOS byte identity | identical | **DEFERRED** to P1-0 CI (Docker daemon not running locally) |
| 3 | Identity flips on intra-module edits | ~0 | **PASS — 0 of 4 perturbations caused any churn** |
| 4 | Import resolution baseline | record | **37-47%** of all imports; remainder genuinely external |

---

## Finding 1: swap graspologic for leidenalg + igraph

Measured, not estimated:

| Package | venv size | Direct + transitive deps | Deterministic w/ seed |
|---|---|---|---|
| `graspologic` 3.4.4 | **575 MB** | **43** | yes |
| `leidenalg` 0.12.0 + `igraph` 1.0.0 | **19 MB** | **6** | yes |

graspologic drags in matplotlib, pandas, scikit-learn, numba, llvmlite, umap-learn, seaborn, statsmodels, and scipy to deliver one clustering algorithm. That is a **30x** install-size penalty on a CLI meant to be installed with `uv tool install` and baked into a CI Docker image.

`leidenalg` verified on a 160-node planted-partition graph: identical across 10 runs at a fixed seed, different seeds produce different partitions (so seeding is genuinely wired, not ignored), and it recovered all 4 planted communities exactly.

**Recommendation:** use `leidenalg` + `python-igraph`. Amend design §11 and the P1-4 dependency list.
**Reasoning:** identical determinism guarantee at 1/30th the footprint. Install size is a real adoption cost for a tool whose CI story requires pulling an image on every pipeline run, and a 575 MB dependency tree is also 43 more supply-chain surfaces on a tool that will be positioned for enterprise governance.

Canonical edge ordering is still mandatory regardless of library. A shuffled edge list happened not to change the result on the test graph, but that is luck, not a guarantee: tie-breaking follows input order.

---

## Finding 2: module identity must resolve at FILE level, aggregated upward

This is the most consequential finding and it changes the design.

The first implementation defined a module as a directory and resolved imports to directories. Results were near-useless:

| Repo | Resolved (directory-level) | Resolved (file-level) |
|---|---|---|
| flask | 4 / 422 (0.9%) | **198 / 422 (46.9%)** |
| starlette | 83 / 402 (20.6%) | **172 / 402 (42.8%)** |
| requests | **0 / 221 (0.0%)** | **83 / 221 (37.6%)** |

The cause is structural, not a bug in the resolver. `src/requests/api.py` does `from .models import Request`. Both files live in the same directory, so at directory granularity that edge is *intra*-module and disappears. A single-package library therefore produces an almost empty dependency graph.

Edge counts confirm the granularity gap:

| Repo | file-level edges | module-level edges |
|---|---|---|
| flask | 109 | 10 |
| starlette | 172 | 4 |
| requests | 73 | 1 |

**Recommendation:** the graph is file-and-symbol level. "Module" is an **aggregation view** computed from it, used for the lockfile and the architecture diagram. Resolution must always target files. Amend design §3 and §7.1 to state this explicitly.
**Reasoning:** the F2 fix (structural identity) was right about *identity* but ambiguous about *granularity*. Committing to directory-granularity resolution would have produced empty module-dependency diagrams on exactly the well-factored single-package repos that make the best demos, and we would have discovered it in week five instead of day one.

---

## Finding 3: F2 confirmed empirically, with numbers

Five synthetic PRs applied to each repo, measuring lockfile churn under structural identity versus Leiden community assignment flips.

| Perturbation | Lockfile churn | Leiden flips (flask) | Leiden flips (requests) |
|---|---|---|---|
| rename local variable | **0 lines** | 0/8 (0%) | 0/4 (0%) |
| add docstring | **0 lines** | 0/8 (0%) | 0/4 (0%) |
| reformat blank lines | **0 lines** | 0/8 (0%) | 0/4 (0%) |
| add new file to existing module | **0 lines** | 0/8 (0%) | 0/4 (0%) |
| add cross-module import | 2 lines *(correct)* | **3/8 (38%)** | **1/4 (25%)** |

Flips are measured after canonicalizing community ids to their lexicographically smallest member, so this counts genuine regrouping, not relabeling.

**One added import flipped 38% of flask's community assignments.** Had communities been the committed identity, that single-line PR would have rewritten more than a third of the lockfile and invalidated every refinement anchored to those ids.

The 2-line churn on `add-cross-module-import` is correct behavior: one module's dependency list gained an entry, which is precisely the architecture change a reviewer should see.

**Recommendation:** none, F2 stands as accepted. Promote these numbers into the design decision log as the empirical basis.
**Reasoning:** the decision was made on Fable's reasoning alone. It now has measurements, which is what makes it defensible when someone later asks "why don't we just use the communities, they look better."

---

## Finding 4: community quality is promising but unproven at scale

flask grouped `src/flask` with `src/flask/json` and `src/flask/sansio` while separating docs and each example app. requests separated `src/requests` from `docs/_themes`. On a directory containing eight unrelated projects, clustering cleanly recovered each project as its own community without being told they were separate.

That is a genuine positive signal, but all three library subjects are small (22-48 files, 3-8 modules). Gate 1 is marked PASS on qualitative inspection, not measurement.

**Recommendation:** re-run gate 1 during P1-5 against two large services (>500 files) before the architecture deriver is considered done.
**Reasoning:** clustering quality problems appear at scale, not on a 35-file library. Passing here does not predict passing there, and the architecture diagram is the flagship demo.

---

## Finding 5: determinism hazards, tested

| Hazard | Test | Result |
|---|---|---|
| `PYTHONHASHSEED` | 5 values (0, 1, 42, 12345, 99999) | identical lockfile hash |
| Locale collation | `LC_ALL` = C, en_US.UTF-8, **tr_TR.UTF-8** | identical lockfile hash |
| Unicode normalization | explicit NFD path names | guard works, lockfile fully NFC |

Turkish locale is included deliberately: its dotless-i collation is the classic sorting trap.

**Correction to Fable F4:** the review stated macOS returns NFD filenames. That was true of HFS+, which normalized on write. **APFS preserves whatever bytes it is given.** Verified directly: a path created with explicit NFD bytes came back from `rglob` with `is_normalized('NFC') == False`.

The hazard is therefore real but arrives by a different route: not the OS normalizing on read, but whichever tool created the file, and whichever form git has stored. Two developers can hold byte-different paths for the same logical filename.

**Recommendation:** keep the NFC guard, and reword the design's rationale to describe the actual mechanism. Add an NFD fixture to the determinism suite.
**Reasoning:** a guard justified by a wrong mechanism gets removed by whoever later "discovers" that APFS does not normalize. Recording the true reason keeps it in place.

---

## Finding 6: toolchain confirmed

| Package | Version | Note |
|---|---|---|
| `tree-sitter` | 0.26.0 | modern API: `Language(tsp.language())`, `Parser(LANG)` |
| `tree-sitter-python` | 0.25.0 | wheel, no build step |
| `tree-sitter-typescript` | 0.23.2 | exposes `language_typescript` and `language_tsx` |
| `networkx` | 3.6.1 | |
| `leidenalg` / `igraph` | 0.12.0 / 1.0.0 | |

Grammar versions must be `==` pinned: a grammar patch release can change node structure and therefore extraction output.

---

## Amendments required before P1

1. **§11 dependencies:** `graspologic` → `leidenalg` + `python-igraph`. (Finding 1)
2. **§3 data model and §7.1 lockfile:** state that resolution targets files and module records are an aggregation. (Finding 2)
3. **§15 decision log:** add the F2 perturbation numbers, and correct the NFC rationale to the APFS mechanism. (Findings 3, 5)
4. **Gate 2** (Linux/macOS byte identity) moves into P1-0 as the first CI job.
5. **Gate 1** re-run at scale scheduled into P1-5.
