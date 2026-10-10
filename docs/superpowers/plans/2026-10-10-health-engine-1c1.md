# Health Engine 1c-1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Measure every function (complexity, length, parameters, nesting), run the first nine maintainability checks, grade the codebase with the SQALE model, and show the result in REPORT.md, a Health tab, graph.json, `get_health` (query and MCP) and the CLI, with checks benchmarked like every other fact.

**Architecture:** Each language pack gains a small `MetricsSpec` (which nodes are functions, branches, nesting blocks; how to count parameters). The walker measures functions in pass 1 into `FileFacts.functions`, which flow through `ExtractResult` into `Graph.functions`. A new `svarupa.health` package turns the graph, the function metrics and the source text into violations, a technical debt ratio and A to E ratings. The CLI computes health once and hands it to every output.

**Tech Stack:** Python 3.10 to 3.13, py-tree-sitter 0.26, networkx, pytest, ruff, pyright (strict).

**Spec:** `docs/superpowers/specs/2026-10-04-health-engine-1c1-design.md`, parent `docs/superpowers/specs/2026-10-03-system-health-umbrella-design.md`.

## Global Constraints

- Thresholds, severities and minutes exactly as the spec's catalog table. They are not configurable in 1c-1.
- SQALE: development cost 30 minutes per line of code (non-blank, non-comment lines); maintainability rating A <= 5%, B <= 10%, C <= 20%, D <= 50%, E > 50%. Areas without checks (reliability, security, performance) are `null` / "not assessed yet", never a grade.
- Every violation carries at least one `Evidence` (file, start line, end line).
- Deterministic: everything sorted, no timestamps; `graph.json` gains a `health` key only when health was computed.
- Health never changes the CLI exit code in 1c-1.
- Existing outputs must not change except: `FileFacts` gains `functions` (pass-1 goldens regenerate, other keys unchanged), and artifacts built by the CLI gain the health section/tab/key.
- `uv run ruff check svarupa tests scripts`, `uv run ruff format --check svarupa tests`, `uv run pyright svarupa`, `uv run pytest -q` pass; benchmark `check` passes.
- Branch `feat/health-1c1`, one commit per task, PR at the end, never push to `main`, no AI attribution, plain English without em dashes, commit subjects under 72 characters.

## Review Focus

1. A function nested inside another (closure, lambda, arrow callback): its branches count for itself only, not for the outer function. Pinned in Task 1 `test_nested_functions_are_measured_separately`.
2. `else if` chains must not inflate nesting depth in TypeScript, Go and Java. Pinned in Task 1 per-language tests (nesting expectations).
3. Import lines repeated across many files (Go/Java import blocks, license headers) must not count as duplicated code. Pinned in Task 2 `test_import_blocks_and_comments_are_not_duplication`.
4. An empty repository or one with no analyzable source: health must not divide by zero and must rate maintainability A with ratio 0. Pinned in Task 2 `test_no_source_means_no_debt`.
5. Areas with no checks must never show a letter grade. Pinned in Task 2 `test_unassessed_areas_have_no_grade`.

---

## File Structure

| File | Responsibility |
|---|---|
| `svarupa/extract/base.py` | `FunctionMetrics`; `FileFacts.functions`; `ExtractResult.functions` |
| `svarupa/extract/packs/model.py` | `MetricsSpec`, `ParamsHook`, `Pack.metrics` |
| `svarupa/extract/packs/walker.py` | `Ctx.measure()` |
| `svarupa/extract/packs/{python,typescript,go,java}.py` | Each pack's `MetricsSpec` (JavaScript inherits TypeScript's) |
| `svarupa/build.py` | `Graph.functions` |
| `svarupa/health/__init__.py` | `assess`, `source_texts`, public types |
| `svarupa/health/catalog.py` | The check catalog |
| `svarupa/health/model.py` | `Violation`, `Health` |
| `svarupa/health/checks.py` | Function, file, class, cycle and hub checks |
| `svarupa/health/duplication.py` | Duplicated blocks |
| `svarupa/health/score.py` | ncloc, debt ratio, ratings, impact and ordering |
| `svarupa/emit/{__init__,data,report,viewer}.py` | `health` key, `## Health`, Health tab |
| `svarupa/query/{__init__,cli,mcp_server}.py` | `get_health` |
| `svarupa/cli.py` | compute health, print summary, pass to emit |
| `scripts/benchmark.py` | violation facts, `stable_checks` |
| `docs/health.md` | the catalog, sources and the grading model for users |

---

### Task 1: Function metrics in every pack

**Files:**
- Modify: `svarupa/extract/base.py`, `svarupa/extract/packs/model.py`, `svarupa/extract/packs/walker.py`, the four pack files, `svarupa/extract/__init__.py`, `svarupa/build.py`
- Test: create `tests/test_metrics.py`; regenerate `tests/golden/**/*.json`

**Interfaces:**
- Produces: `FunctionMetrics(name: str, evidence: Evidence, complexity: int, params: int, nesting: int, lines: int)`; `FileFacts.functions`, `ExtractResult.functions`, `Graph.functions` (sorted by file, start line, name; architecture files only); `MetricsSpec(function_types, branch_types, nesting_types, count_params, boolean_ops=(), not_branch=(), else_if_parents=frozenset())`; `Pack.metrics`.

- [ ] **Step 1: Branch and failing tests**

```bash
git switch main && git pull --ff-only && git switch -c feat/health-1c1
```

(If `feat/health-1c1` already exists locally with only the spec, switch to it instead and rebase on main.)

`tests/test_metrics.py`:

```python
"""Function metrics: cyclomatic complexity, length, parameters, nesting.

Complexity is McCabe's: 1 plus one per decision point (if, elif, loop,
case, catch, ternary, each boolean operator). A nested function is measured
on its own and adds nothing to the function around it.
"""

from __future__ import annotations

from svarupa.extract.packs import extractor


def metrics(lang: str, path: str, src: str) -> dict[str, tuple[int, int, int, int]]:
    f = extractor(lang).parse(path, src.encode())
    return {m.name: (m.complexity, m.params, m.nesting, m.lines) for m in f.functions}


PY = '''def f(a, b, *args, **kw):
    if a and b:
        for x in args:
            if x:
                pass
    elif kw:
        return [y for y in args if y]
    try:
        pass
    except ValueError:
        pass
    return 1 if a else 2
'''


def test_python_complexity_params_nesting_and_length() -> None:
    assert metrics("python", "m.py", PY) == {"f": (10, 4, 3, 12)}


def test_python_self_and_cls_are_not_parameters() -> None:
    src = "class A:\n    def m(self, a):\n        pass\n\n    @classmethod\n    def k(cls):\n        pass\n"
    got = metrics("python", "m.py", src)
    assert (got["m"][1], got["k"][1]) == (1, 0)


def test_nested_functions_are_measured_separately() -> None:
    src = "def outer(a):\n    if a:\n        pass\n\n    def inner(b):\n        if b:\n            pass\n\n    return inner\n"
    got = metrics("python", "m.py", src)
    assert (got["outer"][0], got["inner"][0]) == (2, 2)


TS = """function f(a, b?) {
  if (a && b) {
  } else if (a || b) {
  }
  switch (a) {
    case 1:
      break;
    default:
      break;
  }
  for (const x of y) {
  }
  return a ? 1 : 2;
}
const g = (x) => x;
const h = x => x;
"""


def test_typescript_metrics_and_else_if_is_not_nesting() -> None:
    got = metrics("typescript", "m.ts", TS)
    assert got["f"] == (8, 2, 1, 15)
    assert got["g"][:2] == (1, 1)
    assert got["h"][:2] == (1, 1)


def test_javascript_uses_the_same_metrics() -> None:
    assert metrics("javascript", "m.js", TS)["f"][0] == 8


GO = """package p

func (s *S) Run(a, b int, rest ...string) error {
	if a > 0 && b > 0 {
		for i := 0; i < a; i++ {
			if i == 1 {
				return nil
			}
		}
	} else if a < 0 {
	}
	switch a {
	case 1:
	case 2:
	default:
	}
	return nil
}
"""


def test_go_metrics() -> None:
    assert metrics("go", "p/s.go", GO)["Run"] == (8, 3, 3, 16)


JAVA = """class A {
  int m(int a, String... r) {
    if (a > 0 || r == null) {
      while (a > 0) {
        a--;
      }
    } else if (a < 0) {
    }
    switch (a) {
      case 1: break;
      default: break;
    }
    try {
    } catch (Exception e) {
    }
    return a > 1 ? 1 : 0;
  }
}
"""


def test_java_metrics() -> None:
    assert metrics("java", "A.java", JAVA)["m"] == (8, 2, 2, 16)


def test_metrics_reach_the_graph(tmp_path) -> None:
    from svarupa.build import build
    from svarupa.detect import detect
    from svarupa.extract import declared_dependencies, extract

    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "m.py").write_text(PY, encoding="utf8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_m.py").write_text("def test_x():\n    pass\n", encoding="utf8")
    scan = detect(tmp_path)
    graph = build(scan, extract(scan, declared_dependencies(scan)), strict=False)
    assert [(m.evidence.file, m.name) for m in graph.functions] == [("app/m.py", "f")]
```

Expected counts, worked out so a reader can check them:
- PY `f`: 1 + if + `and` + for + inner if + elif + comprehension `for` + comprehension `if` + except + conditional = 10; params a, b, *args, **kw = 4; nesting if > for > if = 3; 12 lines.
- TS `f`: 1 + if + `&&` + else-if + `||` + case 1 + for-of + ternary = 8 (`default` is not a decision); nesting 1 (else-if does not nest); 15 lines.
- Go `Run`: 1 + if + `&&` + for + inner if + else-if + case 1 + case 2 = 8; params a, b, rest = 3; nesting 3; 16 lines.
- Java `m`: 1 + if + `||` + while + else-if + case 1 + catch + ternary = 8; params 2; nesting if > while = 2; 16 lines.

Run: `uv run pytest tests/test_metrics.py -q`
Expected: failures (`AttributeError: 'FileFacts' object has no attribute 'functions'`).

- [ ] **Step 2: Data types**

`svarupa/extract/base.py`: add `"FunctionMetrics"` to `__all__`, and above `FileFacts`:

```python
@dataclass(frozen=True, slots=True)
class FunctionMetrics:
    """Size and shape of one function, measured from its syntax tree.

    `complexity` is McCabe's cyclomatic complexity: 1 plus one per decision
    point. A function nested inside this one is measured on its own and adds
    nothing here. `nesting` is the deepest block nesting inside the body,
    where an `else if` continues its chain rather than nesting deeper.
    """

    name: str  # "(anonymous)" when the language gives it none
    evidence: Evidence  # the whole function
    complexity: int
    params: int
    nesting: int
    lines: int
```

`FileFacts`, after `namespace`:

```python
    # Per-function metrics, in document order; empty for packs without a
    # MetricsSpec or a file whose tree nests past the depth cap.
    functions: tuple[FunctionMetrics, ...] = ()
```

`ExtractResult`, after `environments`:

```python
    functions: tuple[FunctionMetrics, ...] = ()
```

`svarupa/extract/packs/model.py`: add `ParamsHook = Callable[[TSNode], int]` to the alias block, `"MetricsSpec"` and `"ParamsHook"` to `__all__`, and before `Pack`:

```python
@dataclass(frozen=True, slots=True)
class MetricsSpec:
    """Which syntax nodes are functions, decisions and nesting blocks."""

    function_types: frozenset[str]
    branch_types: frozenset[str]
    nesting_types: frozenset[str]
    count_params: ParamsHook
    # Node type -> operator tokens that make it a decision (`a && b`).
    boolean_ops: tuple[tuple[str, frozenset[str]], ...] = ()
    # (node type, child token) pairs that are not a decision (`default:`).
    not_branch: tuple[tuple[str, str], ...] = ()
    # An `if` directly under one of these is an `else if`: no extra nesting.
    else_if_parents: frozenset[str] = frozenset()
```

and to `Pack`, after `modules`:

```python
    metrics: MetricsSpec | None = None
```

- [ ] **Step 3: The walker measures**

`svarupa/extract/packs/walker.py`: import `FunctionMetrics` from base and `MetricsSpec` from model. In `run()`, after the `self.visit(...)` line and before building `FileFacts`, add `functions = self.measure(tree.root_node)`, and pass `functions=tuple(functions)` to `FileFacts`. Add these methods to `Ctx`:

```python
    def measure(self, root: TSNode) -> list[FunctionMetrics]:
        """Every function in document order. Iterative, so a deep file costs
        no recursion; skipped when the tree already blew the depth cap."""
        spec = self.pack.metrics
        if spec is None or self.too_deep:
            return []
        out: list[FunctionMetrics] = []
        stack = [root]
        while stack:
            node = stack.pop()
            if node.type in spec.function_types:
                out.append(self._measure(node, spec))
            stack.extend(reversed(node.children))
        return out

    def _measure(self, fn: TSNode, spec: MetricsSpec) -> FunctionMetrics:
        ops = dict(spec.boolean_ops)
        excluded = set(spec.not_branch)
        complexity, deepest = 1, 0
        work = [(c, 0, fn.type) for c in reversed(fn.children)]
        while work:
            node, depth, parent = work.pop()
            kind = node.type
            if kind in spec.function_types:
                continue  # measured on its own
            if kind in spec.branch_types:
                if not any((kind, c.type) in excluded for c in node.children):
                    complexity += 1
            elif kind in ops and any(c.type in ops[kind] for c in node.children):
                complexity += 1
            nests = kind in spec.nesting_types and not (
                kind == "if_statement" and parent in spec.else_if_parents
            )
            here = depth + 1 if nests else depth
            deepest = max(deepest, here)
            work.extend((c, here, kind) for c in reversed(node.children))
        start, end = fn.start_point[0], fn.end_point[0]
        return FunctionMetrics(
            name=self._function_name(fn),
            evidence=self.evidence(start, end),
            complexity=complexity,
            params=spec.count_params(fn),
            nesting=deepest,
            lines=end - start + 1,
        )

    def _function_name(self, fn: TSNode) -> str:
        name = fn.child_by_field_name("name")
        if name is None and fn.parent is not None and fn.parent.type == "variable_declarator":
            name = fn.parent.child_by_field_name("name")
        return self.text(name) if name is not None else "(anonymous)"
```

- [ ] **Step 4: Each pack's MetricsSpec**

`svarupa/extract/packs/python.py`: import `MetricsSpec` and `TSNode` is already imported. Add before `PACK`:

```python
_PARAM_KINDS = frozenset(
    {
        "identifier",
        "typed_parameter",
        "default_parameter",
        "typed_default_parameter",
        "list_splat_pattern",
        "dictionary_splat_pattern",
    }
)


def _count_params(fn: TSNode) -> int:
    params = fn.child_by_field_name("parameters")
    if params is None:
        return 0
    names = [c for c in params.children if c.type in _PARAM_KINDS]
    # `self` and `cls` are the receiver, not an argument a caller passes.
    if names and names[0].type == "identifier" and names[0].text in (b"self", b"cls"):
        names = names[1:]
    return len(names)


METRICS = MetricsSpec(
    function_types=frozenset({"function_definition", "lambda"}),
    branch_types=frozenset(
        {
            "if_statement",
            "elif_clause",
            "for_statement",
            "while_statement",
            "except_clause",
            "conditional_expression",
            "boolean_operator",
            "for_in_clause",
            "if_clause",
            "case_clause",
        }
    ),
    nesting_types=frozenset(
        {
            "if_statement",
            "for_statement",
            "while_statement",
            "try_statement",
            "with_statement",
            "match_statement",
        }
    ),
    count_params=_count_params,
)
```

and `metrics=METRICS,` in `PACK`.

`svarupa/extract/packs/typescript.py` (JavaScript inherits it through `replace`):

```python
def _count_params(fn: TSNode) -> int:
    if fn.child_by_field_name("parameter") is not None:
        return 1  # `x => x`
    params = fn.child_by_field_name("parameters")
    if params is None:
        return 0
    return sum(1 for c in params.children if c.type in ("required_parameter", "optional_parameter"))


METRICS = MetricsSpec(
    function_types=frozenset(
        {
            "function_declaration",
            "generator_function_declaration",
            "method_definition",
            "arrow_function",
            "function_expression",
        }
    ),
    branch_types=frozenset(
        {
            "if_statement",
            "for_statement",
            "for_in_statement",
            "while_statement",
            "do_statement",
            "switch_case",
            "catch_clause",
            "ternary_expression",
        }
    ),
    nesting_types=frozenset(
        {
            "if_statement",
            "for_statement",
            "for_in_statement",
            "while_statement",
            "do_statement",
            "switch_statement",
            "try_statement",
        }
    ),
    count_params=_count_params,
    boolean_ops=(("binary_expression", frozenset({"&&", "||", "??"})),),
    else_if_parents=frozenset({"else_clause"}),
)
```

and `metrics=METRICS,` in `PACK`.

`svarupa/extract/packs/go.py`:

```python
def _count_params(fn: TSNode) -> int:
    params = fn.child_by_field_name("parameters")
    if params is None:
        return 0
    count = 0
    for c in params.children:
        if c.type == "parameter_declaration":
            # `a, b int` declares two; an unnamed `int` declares one.
            count += max(1, sum(1 for x in c.children if x.type == "identifier"))
        elif c.type == "variadic_parameter_declaration":
            count += 1
    return count


METRICS = MetricsSpec(
    function_types=frozenset({"function_declaration", "method_declaration", "func_literal"}),
    branch_types=frozenset(
        {"if_statement", "for_statement", "expression_case", "type_case", "communication_case"}
    ),
    nesting_types=frozenset(
        {
            "if_statement",
            "for_statement",
            "expression_switch_statement",
            "type_switch_statement",
            "select_statement",
        }
    ),
    count_params=_count_params,
    boolean_ops=(("binary_expression", frozenset({"&&", "||"})),),
    else_if_parents=frozenset({"if_statement"}),
)
```

and `metrics=METRICS,` in `PACK`.

`svarupa/extract/packs/java.py`:

```python
def _count_params(fn: TSNode) -> int:
    params = fn.child_by_field_name("parameters")
    if params is None:
        return 0
    if params.type == "identifier":
        return 1  # `x -> x`
    return sum(
        1 for c in params.children if c.type in ("formal_parameter", "spread_parameter", "identifier")
    )


METRICS = MetricsSpec(
    function_types=frozenset({"method_declaration", "constructor_declaration", "lambda_expression"}),
    branch_types=frozenset(
        {
            "if_statement",
            "for_statement",
            "enhanced_for_statement",
            "while_statement",
            "do_statement",
            "catch_clause",
            "ternary_expression",
            "switch_label",
        }
    ),
    nesting_types=frozenset(
        {
            "if_statement",
            "for_statement",
            "enhanced_for_statement",
            "while_statement",
            "do_statement",
            "switch_expression",
            "try_statement",
        }
    ),
    count_params=_count_params,
    boolean_ops=(("binary_expression", frozenset({"&&", "||"})),),
    not_branch=(("switch_label", "default"),),
    else_if_parents=frozenset({"if_statement"}),
)
```

and `metrics=METRICS,` in `PACK`.

- [ ] **Step 5: Carry metrics to the graph**

`svarupa/extract/__init__.py`: in the final `replace(result, ...)` call, add

```python
        functions=tuple(m for f in facts for m in f.functions),
```

`svarupa/build.py`: import `FunctionMetrics`; add to `Graph` after `environments`:

```python
    # Per-function metrics of architecture files, for health checks.
    functions: tuple[FunctionMetrics, ...] = ()
```

and in `build()`'s `return Graph(...)` add

```python
        functions=tuple(
            sorted(
                (m for m in extracted.functions if m.evidence.file in eligible),
                key=lambda m: (m.evidence.file, m.evidence.start_line, m.name),
            )
        ),
```

- [ ] **Step 6: Run the metrics tests**

Run: `uv run pytest tests/test_metrics.py -q`
Expected: all pass. If a count differs, read the tree of the test source (print node types) and fix the spec, never the expected number, unless the worked count above is wrong; then ledger a ruling with the corrected count.

- [ ] **Step 7: Regenerate goldens, confirm only `functions` was added**

```bash
SVARUPA_UPDATE_GOLDEN=1 uv run pytest tests/test_golden_facts.py tests/test_golden_repos.py -q
uv run python - <<'EOF'
import json, subprocess
changed = subprocess.run(["git", "diff", "--name-only", "tests/golden", "tests/golden_repos"], capture_output=True, text=True).stdout.split()
for path in changed:
    old = json.loads(subprocess.run(["git", "show", f"HEAD:{path}"], capture_output=True, text=True).stdout)
    new = json.load(open(path))
    if path.startswith("tests/golden/"):
        new.pop("functions", None)
    assert old == new, path
print("only functions added:", len(changed), "files")
EOF
```

Expected: `only functions added: N files`, with pass-2 golden repos unchanged (they hold no `FileFacts`).

- [ ] **Step 8: Full check and commit**

Run: `uv run ruff check svarupa tests scripts && uv run ruff format --check svarupa tests && uv run pyright svarupa && uv run pytest -q && uv run python scripts/benchmark.py check`
Expected: all pass.

```bash
git add docs/superpowers svarupa tests
git commit -m "Measure function complexity, length, parameters and nesting"
```

---

### Task 2: Health engine and the nine checks

**Files:**
- Create: `svarupa/health/__init__.py`, `catalog.py`, `model.py`, `checks.py`, `duplication.py`, `score.py`
- Test: `tests/test_health.py`

**Interfaces:**
- Consumes: `Graph.functions`, `Graph.module_deps`, `Graph.edges`, `Graph.nodes`, `Graph.routes`, `Graph.architecture_paths`.
- Produces: `assess(graph: Graph, texts: Mapping[str, str]) -> Health`; `source_texts(scan: Scan, graph: Graph) -> dict[str, str]`; `Health(ncloc, debt_minutes, debt_ratio, ratings, violations)` with `.to_json()`, `.rating(area)`; `Violation(check, message, evidence, module, value, minutes, impact=0, symbol=None)`; `CATALOG`, `BY_ID`, `AREAS`.

- [ ] **Step 1: Failing tests**

`tests/test_health.py`:

```python
"""The health engine: checks, duplication and SQALE grading."""

from __future__ import annotations

from pathlib import Path

from svarupa.build import build
from svarupa.detect import detect
from svarupa.extract import declared_dependencies, extract
from svarupa.health import AREAS, BY_ID, CATALOG, assess, source_texts


def write(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf8")


def health(root: Path):
    scan = detect(root)
    graph = build(scan, extract(scan, declared_dependencies(scan)), strict=False)
    return assess(graph, source_texts(scan, graph))


def ids(h) -> list[str]:
    return [v.check for v in h.violations]


def complex_python(n: int) -> str:
    body = "".join(f"    if a == {i}:\n        return {i}\n" for i in range(n))
    return f"def f(a):\n{body}    return -1\n"


def test_catalog_matches_the_spec() -> None:
    assert [c.id for c in CATALOG] == [
        "complex-function",
        "long-function",
        "many-parameters",
        "deep-nesting",
        "large-file",
        "large-class",
        "duplicated-block",
        "module-cycle",
        "hub-module",
    ]
    assert all(c.area == "maintainability" and c.source for c in CATALOG)
    assert BY_ID["complex-function"].threshold == 10


def test_complexity_threshold_is_strictly_greater_than_ten(tmp_path: Path) -> None:
    write(tmp_path, "app/at.py", complex_python(9))  # complexity 10
    write(tmp_path, "app/over.py", complex_python(10).replace("def f", "def g"))  # 11
    h = health(tmp_path)
    found = [(v.check, v.evidence[0].file, v.value, v.minutes) for v in h.violations]
    assert ("complex-function", "app/over.py", 11, 11) in found
    assert not [v for v in h.violations if v.evidence[0].file == "app/at.py"]


def test_long_function_many_parameters_and_deep_nesting(tmp_path: Path) -> None:
    long_body = "".join(f"    x{i} = {i}\n" for i in range(51))
    write(tmp_path, "app/long.py", f"def long():\n{long_body}")
    write(tmp_path, "app/args.py", "def args(a, b, c, d, e, f):\n    return a\n")
    deep = "def deep(a):\n" + "".join("    " * (i + 1) + "if a:\n" for i in range(5)) + "    " * 6 + "pass\n"
    write(tmp_path, "app/deep.py", deep)
    assert sorted(ids(health(tmp_path))) == ["deep-nesting", "long-function", "many-parameters"]


def test_large_file(tmp_path: Path) -> None:
    write(tmp_path, "app/big.py", "".join(f"v{i} = {i}\n" for i in range(1001)))
    assert ids(health(tmp_path)) == ["large-file"]


def test_large_class_sums_method_complexity(tmp_path: Path) -> None:
    method = "    def m{n}(self, a):\n" + "".join(f"        if a == {i}:\n            return {i}\n" for i in range(9)) + "        return 0\n"
    cls = "class Big:\n" + "".join(method.replace("{n}", str(n)) for n in range(5))
    write(tmp_path, "app/big.py", cls)  # 5 methods x complexity 10 = 50 > 47
    h = health(tmp_path)
    assert [v.check for v in h.violations] == ["large-class"]
    assert h.violations[0].value == 50


def test_module_cycle_cites_the_imports(tmp_path: Path) -> None:
    write(tmp_path, "a/x.py", "from b.y import g\n\n\ndef f():\n    return g()\n")
    write(tmp_path, "b/y.py", "from a.x import f\n\n\ndef g():\n    return f()\n")
    h = health(tmp_path)
    cycle = [v for v in h.violations if v.check == "module-cycle"]
    assert len(cycle) == 1
    assert {e.file for e in cycle[0].evidence} == {"a/x.py", "b/y.py"}
    assert cycle[0].minutes == 60


def test_hub_module(tmp_path: Path) -> None:
    for i in range(10):
        write(tmp_path, f"user{i}/m.py", "from hub.core import run\n")
        write(tmp_path, f"dep{i}/m.py", "def run():\n    return 1\n")
    imports = "".join(f"from dep{i}.m import run as r{i}\n" for i in range(10))
    write(tmp_path, "hub/core.py", imports + "\n\ndef run():\n    return 1\n")
    hubs = [v for v in health(tmp_path).violations if v.check == "hub-module"]
    assert [v.module for v in hubs] == ["hub"]


DUP = "".join(f"    total = total + values[{i}] * weight\n" for i in range(12))


def test_duplicated_block_across_files(tmp_path: Path) -> None:
    write(tmp_path, "app/a.py", f"def a(values, weight):\n    total = 0\n{DUP}    return total\n")
    write(tmp_path, "app/b.py", f"def b(values, weight):\n    total = 0\n{DUP}    return total\n")
    dups = [v for v in health(tmp_path).violations if v.check == "duplicated-block"]
    assert len(dups) == 1
    assert {e.file for e in dups[0].evidence} == {"app/a.py", "app/b.py"}
    assert dups[0].value >= 12


def test_import_blocks_and_comments_are_not_duplication(tmp_path: Path) -> None:
    header = "".join(f"import mod{i}\n" for i in range(12)) + "".join(f"# note {i}\n" for i in range(12))
    write(tmp_path, "app/a.py", header + "\n\ndef a():\n    return 1\n")
    write(tmp_path, "app/b.py", header + "\n\ndef b():\n    return 2\n")
    assert not [v for v in health(tmp_path).violations if v.check == "duplicated-block"]


def test_debt_ratio_and_rating(tmp_path: Path) -> None:
    write(tmp_path, "app/args.py", "def args(a, b, c, d, e, f):\n    return a\n")
    h = health(tmp_path)
    assert h.ncloc == 2
    assert h.debt_minutes == 5
    assert h.debt_ratio == 5 / (2 * 30)
    assert h.rating("maintainability") == "B"


def test_no_source_means_no_debt(tmp_path: Path) -> None:
    write(tmp_path, "README.md", "# nothing\n")
    h = health(tmp_path)
    assert (h.ncloc, h.debt_minutes, h.debt_ratio, h.rating("maintainability")) == (0, 0, 0.0, "A")


def test_unassessed_areas_have_no_grade(tmp_path: Path) -> None:
    write(tmp_path, "app/m.py", "def f():\n    return 1\n")
    h = health(tmp_path)
    assert set(AREAS) == {"maintainability", "reliability", "security", "performance"}
    assert [h.rating(a) for a in ("reliability", "security", "performance")] == [None, None, None]


def test_violations_are_ranked_by_impact_and_deterministic(tmp_path: Path) -> None:
    write(tmp_path, "core/x.py", "def x(a, b, c, d, e, f):\n    return a\n")
    write(tmp_path, "leaf/y.py", "def y(a, b, c, d, e, f):\n    return a\n")
    for i in range(3):
        write(tmp_path, f"user{i}/m.py", "from core.x import x\n")
    first = health(tmp_path)
    assert [v.module for v in first.violations] == ["core", "leaf"]
    assert first.to_json() == health(tmp_path).to_json()


def test_test_files_are_not_assessed(tmp_path: Path) -> None:
    write(tmp_path, "tests/test_m.py", "def test(a, b, c, d, e, f):\n    return a\n")
    assert health(tmp_path).violations == ()
```

Run: `uv run pytest tests/test_health.py -q`
Expected: `ModuleNotFoundError: No module named 'svarupa.health'`.

- [ ] **Step 2: Catalog and model**

`svarupa/health/catalog.py`:

```python
"""The check catalog: what is measured, the limit, and where the limit comes from.

Thresholds are published defaults of the tools and papers named in
`source`, so a grade means the same thing in every repository. Remediation
minutes are this project's documented estimates (docs/health.md) and feed
the SQALE technical debt ratio.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["AREAS", "BY_ID", "CATALOG", "SEVERITIES", "Check"]

# ISO/IEC 25010 quality characteristics the grades are reported for.
AREAS = ("maintainability", "reliability", "security", "performance")
SEVERITIES = ("info", "minor", "major", "critical", "blocker")


@dataclass(frozen=True, slots=True)
class Check:
    id: str
    title: str
    threshold: int  # violated when the measured value is strictly greater
    source: str
    area: str
    severity: str
    minutes: int  # remediation estimate
    per_unit: int = 0  # extra minutes per unit over the threshold
    maturity: str = "experimental"


CATALOG: tuple[Check, ...] = (
    Check("complex-function", "Complex function", 10, "McCabe 1976; NIST SP 500-235", "maintainability", "major", 10, 1),
    Check("long-function", "Long function", 50, "Fowler, Refactoring: Long Method", "maintainability", "minor", 10),
    Check("many-parameters", "Too many parameters", 5, "pylint max-args default", "maintainability", "minor", 5),
    Check("deep-nesting", "Deep nesting", 4, "ESLint max-depth default", "maintainability", "minor", 10),
    Check("large-file", "Large file", 1000, "pylint max-module-lines default", "maintainability", "minor", 30),
    Check("large-class", "Large class", 47, "Lanza and Marinescu, WMC", "maintainability", "major", 60),
    Check("duplicated-block", "Duplicated block", 9, "SonarQube CPD default (10 lines)", "maintainability", "major", 15),
    Check("module-cycle", "Module import cycle", 0, "Martin, Acyclic Dependencies Principle", "maintainability", "major", 60),
    Check("hub-module", "Hub module", 9, "Arcan hub-like dependency", "maintainability", "major", 60),
)  # fmt: skip

BY_ID = {c.id: c for c in CATALOG}
```

(`duplicated-block` threshold 9 means "10 or more lines"; `hub-module` 9 means "10 or more" on both fan-in and fan-out; `module-cycle` 0 means "any cycle". If ruff format rewraps the catalog despite `# fmt: skip`, wrap it in `# fmt: off` / `# fmt: on` instead.)

`svarupa/health/model.py`:

```python
"""Violations and the health result."""

from __future__ import annotations

from dataclasses import dataclass

from svarupa.model import Evidence

__all__ = ["Health", "Violation"]


@dataclass(frozen=True, slots=True)
class Violation:
    check: str
    message: str
    evidence: tuple[Evidence, ...]  # never empty
    module: str
    value: int  # the measured value
    minutes: int  # remediation estimate
    impact: int = 0  # fan-in, plus 5 when the module serves a request path
    symbol: str | None = None  # graph node id when it is one definition

    def to_json(self) -> dict[str, object]:
        return {
            "check": self.check,
            "message": self.message,
            "evidence": [
                {"file": e.file, "start_line": e.start_line, "end_line": e.end_line}
                for e in self.evidence
            ],
            "module": self.module,
            "value": self.value,
            "minutes": self.minutes,
            "impact": self.impact,
            "symbol": self.symbol,
        }


@dataclass(frozen=True, slots=True)
class Health:
    ncloc: int
    debt_minutes: int
    debt_ratio: float
    ratings: tuple[tuple[str, str | None], ...]  # (area, A..E or None)
    violations: tuple[Violation, ...]  # highest priority first

    def rating(self, area: str) -> str | None:
        return dict(self.ratings)[area]

    def to_json(self) -> dict[str, object]:
        from svarupa.health.catalog import CATALOG

        return {
            "model": "SQALE",
            "ncloc": self.ncloc,
            "debt_minutes": self.debt_minutes,
            "debt_ratio": round(self.debt_ratio, 4),
            "ratings": dict(self.ratings),
            "violations": [v.to_json() for v in self.violations],
            "checks": [
                {
                    "id": c.id,
                    "title": c.title,
                    "threshold": c.threshold,
                    "source": c.source,
                    "area": c.area,
                    "severity": c.severity,
                    "minutes": c.minutes,
                    "per_unit": c.per_unit,
                    "maturity": c.maturity,
                }
                for c in CATALOG
            ],
        }
```

- [ ] **Step 3: Checks**

`svarupa/health/checks.py`:

```python
"""The checks that read the graph and the function metrics."""

from __future__ import annotations

from collections.abc import Mapping

import networkx as nx

from svarupa.build import Graph
from svarupa.extract.base import module_of
from svarupa.health.catalog import BY_ID
from svarupa.health.model import Violation
from svarupa.model import EdgeKind, Evidence

__all__ = ["class_checks", "file_checks", "function_checks", "module_checks"]


def _symbol_ids(graph: Graph) -> dict[tuple[str, int, str], str]:
    """(file, start line, label) -> node id, to link a measured function to
    the definition the diagrams draw."""
    out: dict[tuple[str, int, str], str] = {}
    for nid, node in graph.nodes.items():
        if "#" in nid and node.evidence:
            ev = node.evidence[0]
            out.setdefault((ev.file, ev.start_line, node.label), nid)
    return out


def function_checks(graph: Graph) -> list[Violation]:
    ids = _symbol_ids(graph)
    out: list[Violation] = []
    for fm in graph.functions:
        ev = fm.evidence
        symbol = ids.get((ev.file, ev.start_line, fm.name))
        for check_id, value, what in (
            ("complex-function", fm.complexity, "cyclomatic complexity"),
            ("long-function", fm.lines, "lines"),
            ("many-parameters", fm.params, "parameters"),
            ("deep-nesting", fm.nesting, "nesting levels"),
        ):
            check = BY_ID[check_id]
            if value <= check.threshold:
                continue
            out.append(
                Violation(
                    check=check_id,
                    message=f"{fm.name} has {value} {what} (limit {check.threshold})",
                    evidence=(ev,),
                    module=module_of(ev.file),
                    value=value,
                    minutes=check.minutes + check.per_unit * (value - check.threshold),
                    symbol=symbol,
                )
            )
    return out


def file_checks(texts: Mapping[str, str]) -> list[Violation]:
    check = BY_ID["large-file"]
    out: list[Violation] = []
    for path, text in sorted(texts.items()):
        lines = len(text.splitlines())
        if lines > check.threshold:
            out.append(
                Violation(
                    check="large-file",
                    message=f"{path} has {lines} lines (limit {check.threshold})",
                    evidence=(Evidence(path, 1, lines),),
                    module=module_of(path),
                    value=lines,
                    minutes=check.minutes,
                )
            )
    return out


def class_checks(graph: Graph) -> list[Violation]:
    """WMC: the sum of the complexity of a class's methods."""
    check = BY_ID["large-class"]
    owners: dict[tuple[str, str], int] = {}
    ids = _symbol_ids(graph)
    for fm in graph.functions:
        nid = ids.get((fm.evidence.file, fm.evidence.start_line, fm.name))
        node = graph.nodes.get(nid) if nid else None
        cls = dict(node.attrs).get("class") if node is not None else None
        if cls:
            key = (fm.evidence.file, cls)
            owners[key] = owners.get(key, 0) + fm.complexity
    classes = {
        (node.evidence[0].file, node.label): (nid, node.evidence[0])
        for nid, node in graph.nodes.items()
        if node.kind.value == "class" and node.evidence
    }
    out: list[Violation] = []
    for (file, cls), wmc in sorted(owners.items()):
        if wmc <= check.threshold or (file, cls) not in classes:
            continue
        nid, ev = classes[(file, cls)]
        out.append(
            Violation(
                check="large-class",
                message=f"{cls} has methods with total complexity {wmc} (limit {check.threshold})",
                evidence=(ev,),
                module=module_of(file),
                value=wmc,
                minutes=check.minutes,
                symbol=nid,
            )
        )
    return out


def module_checks(graph: Graph) -> list[Violation]:
    out: list[Violation] = []
    g: nx.DiGraph[str] = nx.DiGraph()
    g.add_edges_from(graph.module_deps)
    imports = sorted(
        (e for e in graph.edges if e.kind is EdgeKind.IMPORTS and e.evidence),
        key=lambda e: (e.src, e.dst),
    )
    cycle = BY_ID["module-cycle"]
    for scc in sorted(sorted(c) for c in nx.strongly_connected_components(g) if len(c) > 1):
        members = set(scc)
        cited = [
            e.evidence[0]
            for e in imports
            if module_of(e.src) in members
            and module_of(e.dst) in members
            and module_of(e.src) != module_of(e.dst)
        ]
        if not cited:
            continue
        out.append(
            Violation(
                check="module-cycle",
                message="modules import each other in a cycle: " + " -> ".join(scc),
                evidence=tuple(cited[:5]),
                module=scc[0],
                value=len(scc),
                minutes=cycle.minutes,
            )
        )
    hub = BY_ID["hub-module"]
    for module in sorted(g.nodes):
        fan_in, fan_out = g.in_degree(module), g.out_degree(module)
        if fan_in > hub.threshold and fan_out > hub.threshold:
            cited = [e.evidence[0] for e in imports if module_of(e.src) == module][:5]
            if cited:
                out.append(
                    Violation(
                        check="hub-module",
                        message=(
                            f"{module or '.'} is used by {fan_in} modules and uses {fan_out}"
                        ),
                        evidence=tuple(cited),
                        module=module,
                        value=fan_in + fan_out,
                        minutes=hub.minutes,
                    )
                )
    return out
```

`svarupa/health/duplication.py`:

```python
"""Duplicated blocks: the same 10 or more lines in more than one place.

Lines are compared after collapsing whitespace. Blank lines, comments,
punctuation-only lines and import or package lines are skipped, so shared
import blocks and license headers never count as copy-paste.
"""

from __future__ import annotations

from collections.abc import Mapping

from svarupa.extract.base import module_of
from svarupa.health.catalog import BY_ID
from svarupa.health.model import Violation
from svarupa.model import Evidence

__all__ = ["duplicated_blocks"]

WINDOW = 10
_SKIP_PREFIXES = ("#", "//", "/*", "*", "import ", "from ", "package ", "using ", "require(")


def _lines(text: str) -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    for number, raw in enumerate(text.splitlines(), 1):
        line = " ".join(raw.split())
        if not line or line.startswith(_SKIP_PREFIXES) or not any(ch.isalnum() for ch in line):
            continue
        out.append((number, line))
    return out


def duplicated_blocks(texts: Mapping[str, str]) -> list[Violation]:
    check = BY_ID["duplicated-block"]
    files = {path: _lines(text) for path, text in sorted(texts.items())}
    seen: dict[str, list[tuple[str, int]]] = {}
    for path, lines in files.items():
        for i in range(len(lines) - WINDOW + 1):
            key = "\n".join(text for _, text in lines[i : i + WINDOW])
            seen.setdefault(key, []).append((path, i))
    covered: set[tuple[str, int]] = set()
    out: list[Violation] = []
    for path, lines in files.items():
        for i in range(len(lines) - WINDOW + 1):
            if (path, i) in covered:
                continue
            key = "\n".join(text for _, text in lines[i : i + WINDOW])
            places = [
                p for p in seen[key] if p == (path, i) or p[0] != path or abs(p[1] - i) >= WINDOW
            ]
            if len(places) < 2:
                continue
            length = WINDOW
            while all(
                p[1] + length < len(files[p[0]])
                and files[p[0]][p[1] + length][1] == lines[i + length][1]
                for p in places
                if i + length < len(lines)
            ) and i + length < len(lines):
                length += 1
            evidence = []
            for p_path, p_i in sorted(places):
                block = files[p_path][p_i : p_i + length]
                evidence.append(Evidence(p_path, block[0][0], block[-1][0]))
                covered.update((p_path, p_i + k) for k in range(length))
            out.append(
                Violation(
                    check="duplicated-block",
                    message=f"{length} lines repeated in {len(places)} places",
                    evidence=tuple(evidence),
                    module=module_of(evidence[0].file),
                    value=length,
                    minutes=check.minutes,
                )
            )
    return out
```

- [ ] **Step 4: Scoring and the public entry**

`svarupa/health/score.py`:

```python
"""SQALE grading: technical debt ratio for maintainability, worst severity
for the other areas, and the order fixes are worth doing in."""

from __future__ import annotations

from collections.abc import Mapping

from svarupa.build import Graph
from svarupa.extract.base import module_of
from svarupa.health.catalog import AREAS, BY_ID, CATALOG, SEVERITIES
from svarupa.health.model import Health, Violation

__all__ = ["grade", "ncloc"]

MINUTES_PER_LINE = 30  # SQALE default development cost
_RATIO = ((0.05, "A"), (0.10, "B"), (0.20, "C"), (0.50, "D"))
_BY_SEVERITY = {"info": "A", "minor": "B", "major": "C", "critical": "D", "blocker": "E"}
_COMMENT = ("#", "//", "/*", "*", "--")


def ncloc(texts: Mapping[str, str]) -> int:
    """Lines of code: not blank and not comment-only."""
    return sum(
        1
        for text in texts.values()
        for raw in text.splitlines()
        if (line := raw.strip()) and not line.startswith(_COMMENT)
    )


def _ratio_rating(ratio: float) -> str:
    for limit, letter in _RATIO:
        if ratio <= limit:
            return letter
    return "E"


def _area_rating(area: str, violations: list[Violation]) -> str | None:
    if not any(c.area == area for c in CATALOG):
        return None  # not assessed yet: no grade is better than a free A
    severities = [BY_ID[v.check].severity for v in violations if BY_ID[v.check].area == area]
    if not severities:
        return "A"
    return _BY_SEVERITY[max(severities, key=SEVERITIES.index)]


def _impacts(graph: Graph) -> dict[str, int]:
    fan_in: dict[str, int] = {}
    for _, dst in graph.module_deps:
        fan_in[dst] = fan_in.get(dst, 0) + 1
    request = {module_of(r.file) for r in graph.routes}
    frontier = list(request)
    while frontier:
        here = frontier.pop()
        for src, dst in graph.module_deps:
            if src == here and dst not in request:
                request.add(dst)
                frontier.append(dst)
    modules = set(fan_in) | request | {m for d in graph.module_deps for m in d}
    return {m: fan_in.get(m, 0) + (5 if m in request else 0) for m in modules}


def grade(graph: Graph, texts: Mapping[str, str], violations: list[Violation]) -> Health:
    impacts = _impacts(graph)
    ranked = sorted(
        (
            Violation(
                v.check, v.message, v.evidence, v.module, v.value, v.minutes,
                impacts.get(v.module, 0), v.symbol,
            )
            for v in violations
        ),
        key=lambda v: (
            -v.impact, -v.minutes, v.check, v.evidence[0].file, v.evidence[0].start_line
        ),
    )  # fmt: skip
    lines = ncloc(texts)
    debt = sum(v.minutes for v in ranked if BY_ID[v.check].area == "maintainability")
    ratio = debt / (lines * MINUTES_PER_LINE) if lines else 0.0
    ratings = tuple(
        (area, _ratio_rating(ratio) if area == "maintainability" else _area_rating(area, ranked))
        for area in AREAS
    )
    return Health(lines, debt, ratio, ratings, tuple(ranked))
```

`svarupa/health/__init__.py`:

```python
"""Codebase health: checks against published limits, graded with SQALE.

`assess` is pure: it reads the graph and the source text it is given and
reads no file itself. `source_texts` is the one place that reads files, for
the CLI and the benchmark.
"""

from __future__ import annotations

from collections.abc import Mapping

from svarupa.build import Graph
from svarupa.detect import Scan, read_text
from svarupa.health.catalog import AREAS, BY_ID, CATALOG, Check
from svarupa.health.checks import class_checks, file_checks, function_checks, module_checks
from svarupa.health.duplication import duplicated_blocks
from svarupa.health.model import Health, Violation
from svarupa.health.score import grade

__all__ = [
    "AREAS",
    "BY_ID",
    "CATALOG",
    "Check",
    "Health",
    "Violation",
    "assess",
    "source_texts",
]

# Languages whose source is code a person maintains (SQL schemas are not).
_CODE = frozenset({"python", "typescript", "javascript", "go", "java"})


def source_texts(scan: Scan, graph: Graph) -> dict[str, str]:
    """Text of every architecture source file in an analyzed language."""
    out: dict[str, str] = {}
    for rec in scan.files:
        if rec.path in graph.architecture_paths and rec.lang in _CODE:
            try:
                out[rec.path] = read_text(scan.root, rec.path)
            except OSError:
                continue
    return out


def assess(graph: Graph, texts: Mapping[str, str]) -> Health:
    violations = [
        *function_checks(graph),
        *file_checks(texts),
        *class_checks(graph),
        *duplicated_blocks(texts),
        *module_checks(graph),
    ]
    return grade(graph, texts, violations)
```

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_health.py -q`
Expected: all pass. The two places most likely to need a ruling are `test_large_class_sums_method_complexity` (depends on method nodes carrying the `class` attr) and the duplication extension loop; fix the code, keep the tests, and ledger any test correction with the reason.

- [ ] **Step 6: Full check and commit**

Run: `uv run ruff check svarupa tests && uv run ruff format --check svarupa tests && uv run pyright svarupa && uv run pytest -q`
Expected: all pass (`tests/test_diagnostics.py::test_every_package_module_is_reachable_by_import` passes only after Task 3 wires `svarupa.health` into the CLI; if it fails here, run the suite with `--deselect` on that test and say so in the ledger).

```bash
git add svarupa/health tests/test_health.py
git commit -m "Add the health engine with nine maintainability checks"
```

---

### Task 3: Show health everywhere

**Files:**
- Modify: `svarupa/cli.py`, `svarupa/emit/__init__.py`, `svarupa/emit/data.py`, `svarupa/emit/report.py`, `svarupa/emit/viewer.py`, `svarupa/query/__init__.py`, `svarupa/query/cli.py`, `svarupa/query/mcp_server.py`, `svarupa/setup/skill.py`, `skills/svarupa/SKILL.md`, `README.md`
- Modify tests that pin tab ids and tool lists: `tests/test_cards.py:146`, `tests/test_graph_query.py:295`, `tests/test_waved.py:118-134`
- Test: `tests/test_health_outputs.py`

- [ ] **Step 1: Failing tests**

`tests/test_health_outputs.py`:

```python
"""Health reaches every output: graph.json, REPORT.md, the viewer, query, CLI."""

from __future__ import annotations

import json
from pathlib import Path

from svarupa.cli import main


def make_repo(root: Path) -> None:
    (root / "app").mkdir(parents=True)
    (root / "app" / "m.py").write_text(
        "def args(a, b, c, d, e, f):\n    return a\n", encoding="utf8"
    )


def test_cli_writes_health_everywhere(tmp_path: Path, capsys) -> None:
    make_repo(tmp_path)
    assert main([str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "health: maintainability B" in out
    assert "many-parameters" in out

    gj = json.loads((tmp_path / ".svarupa" / "graph.json").read_text(encoding="utf8"))
    assert gj["health"]["ratings"]["maintainability"] == "B"
    assert gj["health"]["ratings"]["security"] is None
    assert gj["health"]["violations"][0]["check"] == "many-parameters"

    report = (tmp_path / ".svarupa" / "REPORT.md").read_text(encoding="utf8")
    assert "## Health" in report
    assert "not assessed yet" in report
    assert "app/m.py:1" in report

    html = (tmp_path / ".svarupa" / "index.html").read_text(encoding="utf8")
    assert 'id="d-health"' in html and 'href="#d-health"' in html


def test_get_health_query(tmp_path: Path, capsys) -> None:
    make_repo(tmp_path)
    assert main([str(tmp_path)]) == 0
    capsys.readouterr()
    assert main(["query", str(tmp_path / ".svarupa"), "get_health"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["ratings"]["maintainability"] == "B"
    assert result["violations"][0]["check"] == "many-parameters"
```

Run: `uv run pytest tests/test_health_outputs.py -q`
Expected: failures (no health in outputs).

If `main(["query", ...])` is not how the query CLI is reached, read `svarupa/cli.py` `main` and `svarupa/query/cli.py` `query_main` and use the documented form (`svarupa query <artifact> <function>`); ledger the adjustment.

- [ ] **Step 2: graph.json and emit**

`svarupa/emit/data.py` `graph_json`: add the parameter `health: Health | None = None` (import `Health` from `svarupa.health` under `TYPE_CHECKING` or directly), turn the final `return { ... }` into `out = { ... }`, then:

```python
    if health is not None:
        out["health"] = health.to_json()
    return out
```

`svarupa/emit/__init__.py` `emit`: add the parameter `health: Health | None = None`; pass it as `graph_json(graph, rationale, head, dirty, health)`, as the last argument of `render_viewer(..., graph, health)`, and as `health=health` to `render_report`.

- [ ] **Step 3: REPORT.md section**

`svarupa/emit/report.py`: add the parameter `health: Health | None = None` to `render_report`, splice `*_health_section(health),` right after `*_environments_section(graph),`, and add:

```python
def _health_section(health: Health | None) -> list[str]:
    if health is None:
        return []
    from svarupa.health import BY_ID

    lines = [
        "## Health",
        "",
        "Graded with the SQALE model. Maintainability uses the technical debt "
        "ratio (remediation time over development time at 30 minutes per line); "
        "other areas use the worst severity found.",
        "",
        "| Area | Rating |",
        "|---|---|",
    ]
    for area, letter in health.ratings:
        lines.append(f"| {area} | {letter or 'not assessed yet'} |")
    lines += [
        "",
        f"- **{health.ncloc}** lines of code, **{health.debt_minutes}** minutes of "
        f"technical debt, debt ratio **{health.debt_ratio:.1%}**",
        "",
    ]
    if not health.violations:
        return [*lines, "No violations.", ""]
    lines += ["### Fix first", ""]
    for v in health.violations[:10]:
        ev = v.evidence[0]
        lines.append(
            f"- `{ev.file}:{ev.start_line}` {BY_ID[v.check].title}: {v.message} "
            f"({v.minutes} min, impact {v.impact})"
        )
    lines += ["", "### All violations", ""]
    lines += _listing(
        [
            f"`{v.evidence[0].file}:{v.evidence[0].start_line}` `{v.check}` {v.message}"
            for v in health.violations
        ]
    )
    return [*lines, ""]
```

Read `_listing` (report.py around line 31) first: if its signature differs (for example it takes items and returns lines with an "and N more" cap), adapt the call, keep the cap.

- [ ] **Step 4: Health tab**

`svarupa/emit/viewer.py`: add a `health: Health | None = None` parameter to `render_viewer`. In `nav`, after the diagram links and before the "not drawn" link, add `[tag("a", esc("health"), href="#d-health")] if health is not None else []`. In `body`, after `_unavailable(notes)`, add `_health_tab(health)`. Add:

```python
def _health_tab(health: Health | None) -> Markup:
    """Ratings, the fixes worth doing first, and every violation with its line."""
    if health is None:
        return raw("")
    from svarupa.health import BY_ID

    ratings = join(
        tag("li", join((tag("strong", esc(area)), esc(" " + (letter or "not assessed yet")))))
        for area, letter in health.ratings
    )
    items = join(
        tag(
            "li",
            join(
                (
                    tag("code", esc(f"{v.evidence[0].file}:{v.evidence[0].start_line}")),
                    esc(f" {BY_ID[v.check].title}: {v.message} ({v.minutes} min)"),
                )
            ),
        )
        for v in health.violations
    )
    return tag(
        "section",
        join(
            (
                tag("h2", esc("Health")),
                tag(
                    "p",
                    esc(
                        f"{health.ncloc} lines of code, {health.debt_minutes} minutes of "
                        f"technical debt, debt ratio {health.debt_ratio:.1%} (SQALE)."
                    ),
                    class_="meta",
                ),
                tag("ul", ratings),
                tag("h3", esc("Violations, highest impact first")),
                tag("ol", items) if health.violations else tag("p", esc("No violations.")),
            )
        ),
        class_="tab",
        id="d-health",
    )
```

Update the tab-id regexes that count diagram tabs: in `tests/test_cards.py:146` and `tests/test_graph_query.py:295` change `id="d-(?!unavailable)` to `id="d-(?!unavailable|health)`.

- [ ] **Step 5: get_health in query and MCP**

`svarupa/query/__init__.py`: add `"get_health"` to `__all__` and to the end of `FUNCTIONS`, and:

```python
def get_health(index: GraphIndex) -> dict[str, Any]:
    """Ratings, debt and violations; empty when the artifact predates health."""
    health = index.data.get("health")
    if not isinstance(health, dict):
        return {"available": False, "reason": "this artifact was built without health"}
    return {"available": True, **health}
```

`svarupa/query/cli.py`: import `get_health`; add `"get_health": (0, ""),` to `_ARITY`; in `run_query` add before the final return:

```python
    if function == "get_health":
        return get_health(index)
```

`svarupa/query/mcp_server.py`: change the docstring "seven" to "eight", and add before the `_ = (...)` line:

```python
    @server.tool(
        description="Codebase health: SQALE ratings per area, technical debt, and every violation with file:line, highest impact first."
    )
    def get_health() -> dict[str, Any]:
        return run_query(index, "get_health", [])
```

and add `get_health` to the `_ = (...)` tuple. Update `tests/test_waved.py` (the sorted tool-name list) to include `"get_health"`; update the tool lists in `README.md` (the query line) and in `svarupa/setup/skill.py` plus `skills/svarupa/SKILL.md` (they must stay byte-equal; there is a test).

- [ ] **Step 6: CLI computes health and prints a summary**

`svarupa/cli.py` `_scan`: after the graph integrity block (before "grouping"), add:

```python
    health = assess(graph, source_texts(scan, graph))
    print()
    print(
        "  health: "
        + ", ".join(f"{area} {letter or 'not assessed'}" for area, letter in health.ratings)
    )
    print(
        f"    {health.ncloc} lines of code, {health.debt_minutes} min debt, "
        f"ratio {health.debt_ratio:.1%}, {len(health.violations)} violation(s)"
    )
    for v in health.violations[:3]:
        ev = v.evidence[0]
        print(f"    {v.check:<18} {ev.file}:{ev.start_line}  {v.message}")
```

with `from svarupa.health import assess, source_texts` at the top, and pass `health=health` to `emit(...)`.

- [ ] **Step 7: Run and fix**

Run: `uv run pytest tests/test_health_outputs.py tests/test_waved.py tests/test_cards.py tests/test_graph_query.py -q`
Expected: pass.

Run: `uv run pytest -q`
Expected: all pass. Byte and determinism tests that build a full artifact now include health; they compare two runs, so they stay green. A test that pins an exact REPORT.md or index.html snapshot may need its expectation regenerated: only do so after reading the diff and confirming the only change is the health section/tab/key; name each such test in the commit body.

- [ ] **Step 8: Full check, docs, commit**

Create `docs/health.md`:

```markdown
# Health

Svarupa grades a codebase with the SQALE model, the method behind
SonarQube's ratings and aligned with ISO/IEC 25010.

## Ratings

- **Maintainability** uses the technical debt ratio: the minutes needed to
  fix every maintainability violation, divided by the minutes it took to
  write the code (30 minutes per line of code, SQALE's default).
  A: up to 5%. B: up to 10%. C: up to 20%. D: up to 50%. E: above 50%.
- **Reliability, security, performance** use the worst severity found
  (minor B, major C, critical D, blocker E). Until checks exist for an area
  it shows "not assessed yet", never a grade.

## Checks

| Id | Violated when | Source | Severity | Minutes |
|---|---|---|---|---|
| complex-function | cyclomatic complexity > 10 | McCabe 1976; NIST SP 500-235 | major | 10 + 1 per point over |
| long-function | function longer than 50 lines | Fowler, Refactoring: Long Method | minor | 10 |
| many-parameters | more than 5 parameters (`self`/`cls` not counted) | pylint max-args default | minor | 5 |
| deep-nesting | blocks nested more than 4 deep (`else if` does not nest) | ESLint max-depth default | minor | 10 |
| large-file | file longer than 1000 lines | pylint max-module-lines default | minor | 30 |
| large-class | sum of method complexity > 47 | Lanza and Marinescu, WMC | major | 60 |
| duplicated-block | 10 or more lines repeated | SonarQube CPD default | major | 15 |
| module-cycle | modules import each other in a cycle | Martin, Acyclic Dependencies Principle | major | 60 |
| hub-module | 10 or more modules depend on it and it depends on 10 or more | Arcan hub-like dependency | major | 60 |

Thresholds are the published defaults of the named sources. Remediation
minutes are this project's estimates. Every check starts experimental and is
promoted when the benchmark proves it (see `benchmark/`).

Test, generated, vendored and tooling files are not assessed.
```

Run: `uv run ruff check svarupa tests scripts && uv run ruff format --check svarupa tests && uv run pyright svarupa && uv run pytest -q && uv run python scripts/benchmark.py check`
Expected: all pass. Run `uv run svarupa . --lock` and commit any lockfile change (the new `svarupa/health` module is real architecture).

```bash
git add -A svarupa tests skills README.md docs/health.md .svarupa/architecture.lock
git commit -m "Show health in the CLI, report, viewer, graph.json and MCP"
```

---

### Task 4: Benchmark the checks

**Files:**
- Modify: `scripts/benchmark.py` (violation facts, `stable_checks`), `tests/test_benchmark.py`, `benchmark/README.md`, `benchmark/expected/*.toml`, `benchmark/scores.json`, `svarupa/health/catalog.py` (maturity per the rule)

- [ ] **Step 1: Failing tests**

Append to `tests/test_benchmark.py`:

```python
def test_violation_facts_are_parsed_with_their_check_as_kind(tmp_path: Path) -> None:
    _expected(
        tmp_path,
        "demo",
        '[[must]]\nviolation = "complex-function api/handlers.py:4"\nwhy = "api/handlers.py:4 x"\n',
    )
    must, _ = bm.load_expected("demo", tmp_path)
    assert must[0] == bm.Fact(
        "violation:complex-function api/handlers.py:4", "violation:complex-function", must[0].why
    )


def test_observe_reports_violations(tmp_path: Path) -> None:
    body = "".join(f"    if a == {i}:\n        return {i}\n" for i in range(11))
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "m.py").write_text(f"def f(a):\n{body}", encoding="utf8")
    resolved, _ = bm.observe(tmp_path)
    assert "violation:complex-function app/m.py:1" in resolved


def test_stable_checks_need_two_repos() -> None:
    accepted = {
        "a": {"by_kind": {"violation:complex-function": [3, 3]}, "fired": []},
        "b": {"by_kind": {"violation:complex-function": [9, 10]}, "fired": []},
        "c": {"by_kind": {"violation:large-file": [1, 1]}, "fired": []},
        "d": {
            "by_kind": {"violation:large-file": [1, 1]},
            "fired": ["violation:large-file x:1"],
        },
    }
    assert bm.stable_checks(accepted) == {"complex-function"}


def test_check_maturity_follows_the_benchmark() -> None:
    from svarupa.health import CATALOG

    scores = ROOT / "benchmark" / "scores.json"
    stable = bm.stable_checks(json.loads(scores.read_text(encoding="utf8")))
    wrong = {c.id: c.maturity for c in CATALOG if (c.maturity == "stable") != (c.id in stable)}
    assert not wrong, f"check maturity disagrees with the benchmark: {wrong}"
```

Run: `uv run pytest tests/test_benchmark.py -q`
Expected: the first three fail (no `violation` support, no `stable_checks`).

- [ ] **Step 2: Implement**

In `scripts/benchmark.py`:

`_fact`: before the `call` branch add

```python
    if "violation" in entry:
        text = str(entry["violation"]).strip()
        return Fact("violation:" + text, "violation:" + text.split(" ", 1)[0], why)
```

`observe`: import `from svarupa.health import assess, source_texts`, and before `return resolved, seen`:

```python
    for v in assess(graph, source_texts(scan, graph)).violations:
        ev = v.evidence[0]
        key = f"violation:{v.check} {ev.file}:{ev.start_line}"
        resolved.add(key)
        seen.add(key)
```

Add:

```python
def stable_checks(accepted: dict[str, dict[str, object]]) -> set[str]:
    """Checks with expected violations found at >= 90% on at least two repos,
    where no probe for that check fired."""
    good: dict[str, int] = {}
    for acc in accepted.values():
        fired = {str(k).split(" ", 1)[0] for k in acc.get("fired", [])}  # type: ignore[union-attr]
        for kind, pair in dict(acc.get("by_kind", {})).items():  # type: ignore[arg-type]
            if not kind.startswith("violation:"):
                continue
            found, total = pair
            if total and found / total >= STABLE_RECALL and kind not in fired:
                check = kind.removeprefix("violation:")
                good[check] = good.get(check, 0) + 1
    return {c for c, n in good.items() if n >= STABLE_REPOS}
```

(`fired` keys look like `violation:large-file x:1`; their first word is the kind.)

Add to `benchmark/README.md` under "Writing expected facts": `violation = "<check> <path>:<line>"` is a health violation; the line is where the violation's evidence starts (the function's first line, the class line, line 1 for a large file, the first import line of a cycle, the first line of the first copy of a duplicated block).

Run: `uv run pytest tests/test_benchmark.py -q -k "violation or stable_checks"`
Expected: pass. `test_check_maturity_follows_the_benchmark` passes (every check experimental, none stable yet) or is checked again in Step 5.

- [ ] **Step 3: Draft expected violations from source (agents)**

Dispatch four agents in parallel, one per language group, with the same independence rules as the fact drafting: read source only, never run Svarupa or import `svarupa.health`, edit only `benchmark/expected/<name>.toml` (append), no commits. Per repo they add:

- `[[must]]` violations only where the source makes them unambiguous by hand counting:
  - `complex-function`: a function whose decision points (if, elif, loops, case labels except default, catch/except, ternaries, `&&`/`||`/`and`/`or`) clearly total 12 or more; cite the function's first line.
  - `long-function`: clearly more than 55 lines from first to last line.
  - `many-parameters`: 7 or more parameters (`self`/`cls` not counted).
  - `deep-nesting`: 6 or more nested blocks (if, loops, switch, try, with; `else if` does not nest).
  - `large-file`: clearly more than 1100 lines; cite line 1.
  - `module-cycle`: two modules (directories) importing each other; cite the first import line in the alphabetically first file involved.
- `[[must_not]]` probes: two or three functions clearly under every threshold (complexity 3 or less, under 20 lines, 3 or fewer parameters) written as `violation = "complex-function <path>:<line>"` and similar, and one `module-cycle` that does not exist.
- If a repo has no clear violation of a kind, add none; never stretch a borderline case.

Validate citations: `uv run python scripts/benchmark.py validate`. Expected: `all expected facts cite real lines`.

- [ ] **Step 4: Score, review misses, accept**

Run: `uv run python scripts/benchmark.py run` and list misses and fired probes (as in 1b). A miss whose hand count is off by the documented counting rules is a fact error: correct it only with a ledger ruling citing the source. Otherwise it is a Svarupa finding: fix the metric with a failing test first if it is a clear bug in this task's code, else record it.

Run: `uv run python scripts/benchmark.py accept && uv run python scripts/benchmark.py check`
Expected: `check` exits 0.

- [ ] **Step 5: Check maturity by the rule**

Run: `uv run pytest tests/test_benchmark.py::test_check_maturity_follows_the_benchmark -q`
For each check it lists, set `maturity="stable"` in `svarupa/health/catalog.py` (or back to experimental). Ledger each change with the repos and recalls.

- [ ] **Step 6: Commit**

Run: `uv run ruff check svarupa tests scripts && uv run ruff format --check svarupa tests && uv run pyright svarupa && uv run pytest -q`
Expected: all pass.

```bash
git add -A benchmark scripts tests svarupa
git commit -m "Benchmark the health checks against hand-checked violations"
```

---

### Task 5: Mutation coverage, final review, pull request

- [ ] **Step 1: Mutations**

Append to `MUTATIONS` in `scripts/mutate_packs.py` and add `"tests/test_metrics.py"`, `"tests/test_health.py"`, `"tests/test_health_outputs.py"` to `SUITE`:

```python
    (
        "nested functions add to the outer complexity",
        "svarupa/extract/packs/walker.py",
        "            if kind in spec.function_types:\n                continue  # measured on its own\n",
        "",
    ),
    (
        "else if nests deeper again",
        "svarupa/extract/packs/walker.py",
        '                kind == "if_statement" and parent in spec.else_if_parents\n',
        "                False\n",
    ),
    (
        "the complexity limit is inclusive",
        "svarupa/health/checks.py",
        "            if value <= check.threshold:\n",
        "            if value < check.threshold:\n",
    ),
    (
        "import lines count as duplication",
        "svarupa/health/duplication.py",
        '"import ", "from ", "package ", "using ", "require(")',
        ")",
    ),
    (
        "unassessed areas get a free grade",
        "svarupa/health/score.py",
        "        return None  # not assessed yet: no grade is better than a free A\n",
        '        return "A"\n',
    ),
    (
        "debt ignores code size",
        "svarupa/health/score.py",
        "    ratio = debt / (lines * MINUTES_PER_LINE) if lines else 0.0\n",
        "    ratio = debt / MINUTES_PER_LINE if lines else 0.0\n",
    ),
```

(The import-lines mutation turns the prefix tuple into one that skips only comments; if the pattern does not match exactly, adjust the old text to the real line, never the code.)

Run: `uv run python scripts/mutate_packs.py`
Expected: every line `caught`, exit 0, `git status --short svarupa` empty.

- [ ] **Step 2: Commit, review, PR**

```bash
git add scripts/mutate_packs.py
git commit -m "Cover metrics and health with mutations"
```

Then the executing skill's final whole-branch review, the fix pass, and:

```bash
git push -u origin feat/health-1c1
gh pr create --base main --head feat/health-1c1 --title "Health 1c-1: metrics, nine checks, SQALE grades" --body-file -
```

PR body: what the user sees (CLI lines, REPORT section, tab, `get_health`), the catalog with sources, the benchmark table for violations, check maturity changes, findings, and that reliability, security and performance show "not assessed yet" until 1c-3. Do not merge.
