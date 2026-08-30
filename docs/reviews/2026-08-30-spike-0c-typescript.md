# Spike 0c: TypeScript Resolution

**Date:** 2026-08-30
**Status:** **PASS.** TypeScript resolves substantially better than Python. Request Flow decision unchanged.
**Code:** `spike/calls_ts.py` (throwaway)
**Origin:** Review #1 R2-8.1. All five Spike 0b subjects were Python; TS ships in P1 and had never been run.

---

## Headline: TypeScript is the stronger language for this tool

Same methodology as Spike 0b, so numbers are directly comparable. Percentages are of **intra-repo** items, with known-external excluded.

| Subject | Kind | Import rate | Call pinned | Call depth | Import depth |
|---|---|---|---|---|---|
| zod | library (pnpm monorepo) | **96.3%** | **77.5%** | max 10, 260/1779 ≥3 | max 12, 145/239 ≥3 |
| dexter | app | 95.3% | 54.0% | max 12, 142/590 ≥3 | max 12, 41/148 ≥3 |
| munder-difflin | Electron app | 92.7% | 45.5% | max 10, 395/1877 ≥3 | max 10, 80/228 ≥3 |
| nestjs-realworld | **service** | 88.6% | 49.2% | max 3, **2/71 ≥3** | max 8, 13/34 ≥3 |
| supersplat | app | 86.1% | 47.6% | max 7, 152/805 ≥3 | max 7, 58/119 ≥3 |
| InvoiceApp | app | 63.5% | 34.9% | max 7, 37/248 ≥3 | max 5, 9/64 ≥3 |

Against Python for reference:

| | Python | TypeScript |
|---|---|---|
| Import resolution | 37-47% | **63-96%** |
| Call resolution, services | ~20% | ~49% |
| Call resolution, libraries | ~60% | **77%** |

TypeScript's explicit relative module specifiers resolve far more reliably than Python's implicit package resolution, and its class-oriented idiom means `this.x` carries real type information.

### `this.field.method()` is the standout

| Subject | `this` shape n | Pinned |
|---|---|---|
| nestjs-realworld | 74 | **100.0%** |
| InvoiceApp | 168 | **99.1%** |
| munder-difflin | 762 | 72.0% |
| dexter | 424 | 57.3% |

NestJS constructor injection is explicitly typed (`constructor(private readonly userService: UserService)`), so recording constructor parameter types lets `this.userService.findOne()` resolve to a definite target. This is the direct analogue of the FastAPI `Depends()` case that failed in Python, and it succeeds precisely because the type names an **intra-repo** class rather than a framework class.

### `module` shape remains the weak spot, as in Python

`var.method()` with a local-variable receiver: 5-28% pinned across subjects. Same root cause as Python, and the same reason it is not worth chasing.

---

## Three resolver bugs found, all silent

Each of these would have made the TS extractor look broken while reporting plausible-looking numbers. This is the entire value of running the spike before P1-2.

### 1. ESM `.js` specifiers that name `.ts` files

Modern ESM TypeScript writes `import { x } from "./foo.js"` for a file that is `foo.ts` on disk. Without remapping, **zod scored 0.0% import resolution** (436 unresolved).

Fix: try `.ts`, `.tsx`, `.mts`, `.cts` after stripping a `.js`, `.mjs`, `.cjs`, or `.jsx` suffix.
**zod: 0.0% → 96.3%.**

### 2. tsconfig `references` and `extends` not followed

The project-references layout puts `"files": []` in the root tsconfig and points at sibling configs, where the `paths` aliases actually live. Reading only the root found zero aliases.

Fix: walk `references` and `extends` transitively, resolving alias targets relative to the config that declares them.

### 3. Regex JSONC comment-stripping corrupts `paths` (the subtle one)

This one is worth remembering. Stripping JSONC comments with a regex **cannot work** on tsconfig files:

```jsonc
"paths": {
  "@/*": ["src/renderer/src/*"],     // the /* here opens a "block comment"
  "@shared/*": ["src/shared/*"]      // ...which is closed by a later */
}
```

`re.sub(r"/\*.*?\*/", "", raw, flags=re.S)` treats the `/*` inside the string `"@/*"` as a comment opener and deletes everything through the next `*/`, silently mangling the file into invalid JSON. The loader then returned `None` and reported zero aliases.

Since tsconfig `paths` values **always** contain glob patterns, this would have broken alias resolution on essentially every TypeScript project that uses path aliases.

Fix: a string-aware scanner that tracks quote state and escapes.
**munder-difflin: 61.3% → 92.7%**, and files reaching import-depth ≥3 went from 33/228 to 80/228.

---

## Request Flow decision: unchanged, and now language-independent

The pattern from Spike 0b reproduces exactly in TypeScript:

| Project kind | Call-chain depth |
|---|---|
| **Services** (nestjs-realworld) | max 3, **2 of 71 symbols reach ≥3** |
| Apps and libraries (zod, dexter, munder-difflin, supersplat) | max 7-12, hundreds reach ≥3 |

**Services have thin intra-repo call graphs in both languages**, for the same structural reason: a service is glue over frameworks. Apps and libraries contain their own logic and therefore their own call chains.

Since services are the audience for the CI thesis, Request Flow (imports + routes + config, gated at depth 3) remains the right call. TypeScript does not rescue function-level sequence for services; it merely makes everything else better.

**However:** TS call edges are genuinely rich on apps and libraries (dexter 142/590 symbols reach depth ≥3). They stay in the graph, and `impact_of_change` will return materially better answers on TypeScript projects than on Python ones. Worth stating plainly in the report rather than pretending quality is uniform.

---

## Amendments

| # | Change | Where |
|---|---|---|
| 1 | ESM `.js` → `.ts` specifier remapping is required behavior, with a fixture | P1-2 |
| 2 | tsconfig loading must follow `references` and `extends`, resolving alias targets relative to the declaring config | P1-2 |
| 3 | **Never parse JSONC with regex.** Use a string-aware scanner. Applies to `tsconfig.json`, `.babelrc`, `devcontainer.json`, and any other JSONC config | P1-2, and a note in the config-parser contract |
| 4 | Record constructor parameter types so `this.field.method()` resolves. Measured 99-100% on DI-heavy codebases and it is cheap | P1-2 |
| 5 | Resolution quality varies by language and project kind. The scorecard must be per-language, and the report should say so rather than implying uniformity | P1-2 report |
| 6 | Barrel `index.ts` re-export chasing, the TS analogue of R2-4's `__init__.py` chain, still unimplemented | P1-2 |

---

## Remaining gaps

- **InvoiceApp at 63.5%** (69 unresolved non-relative specifiers) is a nested-subproject layout where `baseUrl` resolved to `invoice-app`. Per-subproject tsconfig scoping needs handling; monorepo/workspace roots more generally.
- **Barrel re-exports untested.** Same class of problem as `__init__.py`: `from "./services"` hitting `services/index.ts` reports the barrel as the dependency target rather than the real definition.
- **Go, Rust, Java unmeasured.** Go's implicit interface satisfaction is the next material unknown, but it lands in P2/P3, so the risk is not on the P1 critical path.
