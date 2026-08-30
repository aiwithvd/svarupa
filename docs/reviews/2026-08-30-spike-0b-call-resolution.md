# Spike 0b: Call-Resolution Gate

**Date:** 2026-08-30
**Status:** Gate **FAILED for function-level sequence.** Scope changed accordingly.
**Code:** `spike/calls.py` (throwaway)
**Origin:** Review #1 R2-8.2 promoted this to a blocking gate ahead of P1-2.

---

## Question

Every headline capability (sequence diagrams, `trace_calls`, `impact_of_change`) lives on call edges. Import resolution measured 37-47% in Spike 0. Call resolution was never measured, and calls are much harder in Python. If the number is poor, scope moves to the config-and-import-backed diagrams.

---

## Result: call resolution collapses on service code

Scorecard uses the three-bin design from review finding R2-3, plus the candidate tier from F3. Percentages are of **intra-repo** calls, with known-external excluded.

| Subject | Kind | Resolved | Candidate | **Pinned** | Unresolved-unknown |
|---|---|---|---|---|---|
| flask | library | 47.1% | 12.1% | **59.2%** | 40.8% |
| requests | library | 47.3% | 13.8% | **61.1%** | 38.9% |
| full-stack-fastapi-template | service | 17.3% | 0.0% | **17.3%** | 82.7% |
| `demo` (local FastAPI service) | service | 25.2% | 0.0% | **25.2%** | 74.8% |
| Memory-Agent | service | 20.6% | 2.3% | **22.9%** | 77.1% |

**Libraries resolve at ~60%. Services resolve at ~20%.** Services are the larger audience and the target of the CI thesis.

### Where it fails: the `module` shape

Breakdown by call shape reveals a single dominant cause. `something.method()` where `something` is a local variable:

| Shape | Memory-Agent n | Pinned |
|---|---|---|
| `self.method()` | 10 | **90.0%** |
| `bare()` | 423 | 84.1% |
| `attr` (`obj.method()`) | 158 | 100% (mostly candidate) |
| **`module` (`var.method()`)** | **730** | **5.2%** |
| `super()` | 2 | 0% |

730 of 1,323 call sites in Memory-Agent are this shape, and 440 are unresolvable. This is the dominant pattern in service code: `db.query()`, `client.post()`, `agent.run()`, `collection.find_one()`.

MRO-based `self.method()` resolution works excellently (90-100%). It is simply rare in service code.

---

## Chain depth: what sequence diagrams actually need

A sequence diagram needs at least 3-4 participants to be worth drawing.

| Subject | Call-chain depth | Entry points reaching depth ≥3 |
|---|---|---|
| flask | max 6, median 2 | 2 / 4 |
| requests | max 4, median 2 | 1 / 5 |
| full-stack-fastapi-template | max 4, **median 0** | 1 / 2 |
| `demo` | **no entry points detected** | 0 |
| Memory-Agent | **max 1** | 0 / 1 |

On services this produces a one- or two-participant diagram, which reads as *"the tool does not understand my code."* That is worse than shipping nothing, and it is precisely the failure mode the design review warned about.

---

## Type annotations do not rescue it

The obvious lever: modern service code is heavily annotated, so `def handler(db: Session)` should give the receiver type. Measured:

| Subject | Params annotated | Types pointing at an **intra-repo** class |
|---|---|---|
| full-stack-fastapi-template | 104 / 104 (100%) | **13** |
| Memory-Agent | 151 / 156 (97%) | **18** |
| flask | 463 / 463 (100%) | 71 |

Annotation coverage is essentially total, but the types name **external** classes: `Session`, `AsyncClient`, `Redis`, `OpenAI`. Perfect annotation-based inference would recover roughly 13-19 receivers against hundreds of unresolved call sites, a 2-4% improvement.

**The cause is structural, not a tooling gap.** Service code is mostly glue over frameworks, so a service's intra-repo call graph is genuinely thin. No amount of inference recovers edges that are not there.

---

## What does work: imports

The same repos measured with import-derived chains:

| Subject | Call-chain depth | **File-chain depth (imports)** | Module-chain depth |
|---|---|---|---|
| full-stack-fastapi-template | median 0 | **max 10**, 9/125 ≥3 | max 3 |
| Memory-Agent | max 1 | max 4, 3/43 ≥3 | max 4, 4/16 ≥3 |
| `demo` | none | max 3, 2/25 ≥3 | max 3, **5/12 ≥3** |
| flask | median 2 | **max 10**, 31/35 ≥3 | max 4, 4/8 ≥3 |

Import-based chains reach depth 3-10 on exactly the repos where call chains reach 0-1.

---

## Decisions

### 1. Function-level sequence is cut. Replaced by **Request Flow**.

Derived from resolved imports + framework route decorators + config (compose, OpenAPI), at file/module granularity:

```
POST /refunds
  → api/routes/billing.py
  → services/refund_service.py
  → repositories/refund_repo.py
  → postgres (compose.yml)
```

**Gated:** the deriver returns `None` unless the chain reaches depth 3. A thin repo gets no diagram rather than a two-box one.

**Reasoning.** This is not a consolation prize; it is a better product. Nobody wants a forty-hop function trace. The question people actually ask on joining a codebase is "what does this endpoint touch," which is a module-level question answered by module-level data. It also rests on the inputs that measured well (imports 37-47%, config ~100%) rather than the input that measured at 5%.

### 2. MCP tools redefined on the dependency graph

- `trace_calls` → **`trace_dependencies(from, to)`**: import path between two files.
- `impact_of_change(symbol)` → transitive **importers**, not callers.

**Reasoning.** File-level import edges are plentiful (93-172 per repo) and measured depth 3-10. Building these tools on call edges would ship answers that are thin by construction, and an agent told "3 callers found, but roughly 77% unresolved" cannot act on that. Better to answer a slightly different question completely than the intended question badly.

---

## What survives, and what this validates

Unchanged and strong: architecture, module deps, ERD, API surface, deploy topology, class hierarchy. All rest on imports, inheritance, or config, all of which measured well.

`self.method()` resolution at 90-100% is genuinely good and stays in the graph. It is simply too rare in service code to carry a diagram.

**The fail-closed model held up under measurement.** At no point was the answer "emit a plausible edge." Unresolvable dispatch produced an honest unresolved-unknown count, which is exactly what the three-bin scorecard exists to surface.

---

## Follow-ups

| Item | Where |
|---|---|
| Entry-point detection needs framework awareness. `demo` reported zero entry points because FastAPI routes are `@app.get`-decorated, not named `main` | P2 framework detection |
| `super()` resolution misclassifies external base classes as unknown rather than external. Understates the scorecard, small n | P1-2 |
| Re-measure Request Flow depth once route decorators are parsed; it should improve materially | P2 |
| TypeScript call resolution still unmeasured. Second-riskiest remaining assumption | before P1-2 TS extractor |
