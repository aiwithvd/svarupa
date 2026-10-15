# Security Checks (step 1c-3a) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fill the security health area with ten checks Svarupa proves from code (committed secrets, injection, unsafe settings, endpoint without auth), each cited at `file:line` and tied to a CWE.

**Architecture:** Each language pack gets a data-only `RiskSpec` (sinks, unsafe patterns, string-building shapes, auth markers). The walker runs one more iterative pass per file and records `RiskFact`s, which flow `FileFacts.risks` → `ExtractResult.risks` → `Graph.risks` exactly like function metrics. The health engine turns facts into violations; a text scanner finds secrets in every non-ignored file and never stores the value; the auth check reads routes, handler spans and file text.

**Tech Stack:** Python 3.12, tree-sitter (pinned grammars), pytest, ruff, pyright strict.

**Spec:** `docs/superpowers/specs/2026-10-10-security-scaling-1c3-design.md` (1c-3a section; 1c-3b gets its own plan).

## Global Constraints

- Rules are data in each pack (`RiskSpec`); the walker never names a language.
- Same function, no taint tracking: a sink fires only when an argument is built by concatenation, interpolation or a format call, inline or through a variable assigned that way earlier in the same function.
- A parameterized call (literal query plus separate values) is never recorded.
- Secret values are never stored, printed or written: outputs carry the rule name, `file:line` and `sha256(value)[:12]` only.
- Placeholders are skipped: empty values, `changeme`, `example`, `xxx`, `<...>`, `${...}`, `{{...}}`, values read from the environment.
- `endpoint-without-auth`: POST, PUT, PATCH, DELETE (and ALL) routes only; not reported for a unit with no auth marker anywhere, and the report says so.
- `design.yaml` gains `auth_markers` and `exceptions` with `at: path` or `at: path:line`; a reason is required; an excepted violation is listed as accepted, not counted.
- All new checks start `experimental`; security is rated by worst severity (blocker E, critical D, major C, minor B, none A).
- Test fixtures must never contain a literal that matches a real key format (GitHub push protection): build fake keys by concatenation at runtime.
- Commits: SSH-signed (repo-local config already set), imperative subject under 72 characters, no AI attribution, no em dashes.
- Run `uv run ruff check .` and `uv run pyright` without piping, before each commit.

## Review Focus

1. A route protected by a router-level or class-level marker (`APIRouter(dependencies=[Depends(get_current_user)])`, `@UseGuards` on the controller) must not fire: Task 8 pins both.
2. An import line that names a marker (`from app.deps import get_current_user`) must not count as protection: Task 8 pins it.
3. A secret in a `.env` or YAML file (not a source language) must be found: Task 7 pins it, which needs the detect change.
4. A reassigned variable (`q = "a" + x` then `q = "SELECT 1"`) must stop counting as built: Task 1 pins it.
5. `subprocess.run(cmd)` without `shell=True` must not fire while `shell=True` does: Task 2 pins it.

---

## File Structure

| File | Responsibility |
|---|---|
| `svarupa/extract/packs/model.py` | `Sink`, `Unsafe`, `RiskSpec`, `Pack.risks` |
| `svarupa/extract/base.py` | `RiskFact`, `FileFacts.risks`, `ExtractResult.risks` |
| `svarupa/extract/packs/walker.py` | `Ctx.scan_risks` (the generic engine) |
| `svarupa/extract/packs/{python,typescript,go,java}.py` | each pack's `RISKS` data and callee hook |
| `svarupa/extract/__init__.py`, `svarupa/build.py` | carry risks to `Graph.risks` |
| `svarupa/detect.py` | `Scan.others`: non-ignored files of no analyzed language |
| `svarupa/health/catalog.py` | ten security checks |
| `svarupa/health/security.py` | risk facts to violations |
| `svarupa/health/secrets.py` | the secret scanner and `secret_texts` |
| `svarupa/health/auth.py` | `endpoint-without-auth` |
| `svarupa/health/__init__.py`, `model.py` | `assess` wiring, `Health.accepted`, `Health.notes` |
| `svarupa/design/file.py`, `design/model.py`, `design/__init__.py`, `health/checks.py` | `at` exceptions, `auth_markers` |
| `svarupa/cli.py`, `scripts/benchmark.py`, `svarupa/emit/report.py` | pass the new inputs; show notes and accepted |
| `tests/test_risks.py`, `tests/test_security.py`, `tests/test_secrets.py`, `tests/test_auth.py` | new tests |
| `docs/health.md`, `benchmark/expected/*.toml`, `scripts/mutate_packs.py` | docs, benchmark facts, mutations |

---

### Task 1: Risk facts from the walker (engine, plumbing, Python SQL sinks)

**Files:**
- Modify: `svarupa/extract/packs/model.py`, `svarupa/extract/base.py`, `svarupa/extract/packs/walker.py`, `svarupa/extract/packs/python.py`, `svarupa/extract/__init__.py`, `svarupa/build.py`
- Test: `tests/test_risks.py`

**Interfaces:**
- Produces: `Sink(tag, callee, requires=None)`, `Unsafe(check, label, node_types, pattern)`, `RiskSpec(...)`, `Pack.risks: RiskSpec | None`; `RiskFact(kind, tag, detail, evidence, function)`; `FileFacts.risks`, `ExtractResult.risks`, `Graph.risks: tuple[RiskFact, ...]`; `Ctx.scan_risks(root) -> list[RiskFact]`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_risks.py
"""Risk facts: what the walker records for the security checks.

A sink is recorded only when an argument is built from strings in the same
function; a parameterized call never is.
"""

from __future__ import annotations

from svarupa.extract.packs import extractor


def risks(lang: str, path: str, src: str) -> list[tuple[str, str, int, str]]:
    f = extractor(lang).parse(path, src.encode())
    return [(r.kind, r.tag, r.evidence.start_line, r.function) for r in f.risks]


def test_python_sql_built_inline_is_recorded() -> None:
    src = (
        "def f(cur, x):\n"
        "    cur.execute(f\"SELECT {x}\")\n"
        "    cur.execute(\"SELECT \" + x)\n"
        "    cur.execute(\"SELECT %s\" % x)\n"
        "    cur.execute(\"SELECT {}\".format(x))\n"
    )
    assert risks("python", "m.py", src) == [
        ("sink-built", "sql", 2, "f"),
        ("sink-built", "sql", 3, "f"),
        ("sink-built", "sql", 4, "f"),
        ("sink-built", "sql", 5, "f"),
    ]


def test_python_parameterized_query_is_not_recorded() -> None:
    src = (
        "def f(cur, x):\n"
        "    cur.execute(\"SELECT * FROM t WHERE id = %s\", (x,))\n"
        "    q = \"SELECT 1\"\n"
        "    cur.execute(q)\n"
    )
    assert risks("python", "m.py", src) == []


def test_built_variable_counts_until_reassigned() -> None:
    src = (
        "def f(cur, x):\n"
        "    q = \"SELECT \" + x\n"
        "    cur.execute(q)\n"
        "    q = \"SELECT 1\"\n"
        "    cur.execute(q)\n"
        "    q += x\n"
        "    cur.execute(q)\n"
    )
    assert risks("python", "m.py", src) == [
        ("sink-built", "sql", 3, "f"),
        ("sink-built", "sql", 7, "f"),
    ]


def test_scopes_do_not_leak_between_functions() -> None:
    src = (
        "q = \"SELECT \" + X\n"
        "def f(cur):\n"
        "    cur.execute(q)\n"
        "def g(cur, x):\n"
        "    q = \"SELECT \" + x\n"
        "    def inner(cur):\n"
        "        cur.execute(q)\n"
        "cur.execute(q)\n"
    )
    assert risks("python", "m.py", src) == [("sink-built", "sql", 8, "")]


def test_risks_reach_the_graph(tmp_path) -> None:
    from svarupa.build import build
    from svarupa.detect import detect
    from svarupa.extract import declared_dependencies, extract

    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "m.py").write_text(
        "def f(cur, x):\n    cur.execute(\"SELECT \" + x)\n", encoding="utf8"
    )
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_m.py").write_text(
        "def t(cur, x):\n    cur.execute(\"SELECT \" + x)\n", encoding="utf8"
    )
    scan = detect(tmp_path)
    graph = build(scan, extract(scan, declared_dependencies(scan)), strict=False)
    assert [(r.tag, r.evidence.file, r.evidence.start_line) for r in graph.risks] == [
        ("sql", "app/m.py", 2)
    ]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_risks.py -q`
Expected: FAIL with `AttributeError: 'FileFacts' object has no attribute 'risks'`

- [ ] **Step 3: Add the model**

In `svarupa/extract/packs/model.py` add `import re` and, after `MetricsSpec`:

```python
CalleeHook = Callable[["Ctx", TSNode], str | None]


@dataclass(frozen=True, slots=True)
class Sink:
    """A call that runs SQL, a shell or code from its argument."""

    tag: str  # "sql" | "shell" | "code"
    callee: re.Pattern[str]  # searched in the callee text
    requires: re.Pattern[str] | None = None  # searched in the argument list text


@dataclass(frozen=True, slots=True)
class Unsafe:
    """A node whose whole text (whitespace collapsed) is an unsafe setting."""

    check: str  # catalog id, e.g. "tls-verify-off"
    label: str  # what the evidence shows, e.g. "verify=False"; never a value
    node_types: frozenset[str]
    pattern: re.Pattern[str]  # fullmatch


@dataclass(frozen=True, slots=True)
class RiskSpec:
    """Which syntax builds strings, which calls are sinks, what is unsafe."""

    call_types: frozenset[str]
    callee: CalleeHook
    string_types: frozenset[str]
    # (string node type, interpolation child type): f"{x}", `${x}`.
    interpolations: tuple[tuple[str, str], ...]
    # (binary node type, operator tokens that join strings).
    concat: tuple[tuple[str, frozenset[str]], ...]
    format_callee: re.Pattern[str]  # "x".format(...), fmt.Sprintf, String.format
    # (node type, left field, right field) for plain and augmented assignment.
    assigns: tuple[tuple[str, str, str], ...]
    # Wrappers whose last named child is the value: (x), await x, kw=x.
    unwrap: frozenset[str] = frozenset()
    sinks: tuple[Sink, ...] = ()
    unsafe: tuple[Unsafe, ...] = ()
    auth_markers: frozenset[str] = frozenset()
```

Add `"CalleeHook", "RiskSpec", "Sink", "Unsafe"` to `__all__`, and to `Pack` add the field `risks: RiskSpec | None = None` after `metrics`.

In `svarupa/extract/base.py`, after `FunctionMetrics`:

```python
@dataclass(frozen=True, slots=True)
class RiskFact:
    """A security-relevant shape in one function, before any judgement.

    `detail` is the callee or the unsafe rule's label, never a literal from
    the source, so a fact can be printed anywhere.
    """

    kind: str  # "sink-built" | "unsafe"
    tag: str  # sink tag (sql/shell/code) or the unsafe check id
    detail: str
    evidence: Evidence
    function: str  # enclosing function name, "" at module level
```

Add `"RiskFact"` to `__all__`; add `risks: tuple[RiskFact, ...] = ()` as the last field of `FileFacts` (comment: `# Security facts in document order; empty for packs without a RiskSpec.`) and of `ExtractResult`.

- [ ] **Step 4: Add the engine to the walker**

In `svarupa/extract/packs/walker.py`: add `import re`; import `RiskFact` from base and `RiskSpec` from model; add module constants and methods on `Ctx`:

```python
_IDENT = re.compile(r"[A-Za-z_$][\w$]*")
_MAX_NODE = 2000  # bytes: longer nodes are never an unsafe one-liner
```

```python
    def scan_risks(self, root: TSNode) -> list[RiskFact]:
        """Sinks fed by built strings and unsafe settings, per function.

        Each function is its own scope: a variable built in one function is
        never the same variable in another. Iterative, like `measure`.
        """
        spec = self.pack.risks
        if spec is None or self.too_deep:
            return []
        fn_types = self.pack.metrics.function_types if self.pack.metrics else frozenset()
        by_type: dict[str, list[Unsafe]] = {}
        for rule in spec.unsafe:
            for t in rule.node_types:
                by_type.setdefault(t, []).append(rule)
        out: list[RiskFact] = []
        scopes: list[tuple[TSNode, str]] = [(root, "")]
        while scopes:
            scope, name = scopes.pop()
            built: set[str] = set()
            work = list(reversed(scope.children))
            while work:
                node = work.pop()
                if node.is_named and node.type in fn_types:
                    scopes.append((node, self._function_name(node)))
                    continue
                self._risk_node(node, spec, by_type, built, name, out)
                work.extend(reversed(node.children))
        return sorted(out, key=lambda r: (r.evidence.start_line, r.kind, r.tag, r.detail))

    def _risk_node(
        self,
        node: TSNode,
        spec: RiskSpec,
        by_type: dict[str, list[Unsafe]],
        built: set[str],
        scope: str,
        out: list[RiskFact],
    ) -> None:
        t = node.type
        for kind, left_field, right_field in spec.assigns:
            if t != kind:
                continue
            left = node.child_by_field_name(left_field)
            right = node.child_by_field_name(right_field)
            if left is None or right is None or not _IDENT.fullmatch(self.text(left)):
                break
            name = self.text(left)
            augment = any(not c.is_named and c.type == "+=" for c in node.children)
            if self._built(right, spec, built) or (
                augment and right.type not in spec.string_types
            ):
                built.add(name)
            elif not augment:
                built.discard(name)
            break
        if t in spec.call_types:
            callee = spec.callee(self, node)
            args = node.child_by_field_name("arguments")
            if callee is not None and args is not None:
                for sink in spec.sinks:
                    if not sink.callee.search(callee):
                        continue
                    if sink.requires is not None and not sink.requires.search(self.text(args)):
                        continue
                    if any(self._built(a, spec, built) for a in args.named_children):
                        out.append(
                            RiskFact("sink-built", sink.tag, callee[:80],
                                     self.node_evidence(node), scope)
                        )
                    break
        rules = by_type.get(t)
        if rules and node.end_byte - node.start_byte <= _MAX_NODE:
            text = " ".join(self.text(node).split())
            for rule in rules:
                if rule.pattern.fullmatch(text):
                    out.append(
                        RiskFact("unsafe", rule.check, rule.label, self.node_evidence(node), scope)
                    )

    def _built(self, node: TSNode, spec: RiskSpec, built: set[str]) -> bool:
        """Whether this expression is a string built at runtime."""
        t = node.type
        if t in spec.unwrap:
            kids = node.named_children
            return bool(kids) and self._built(kids[-1], spec, built)
        if t in spec.string_types:
            inner = dict(spec.interpolations).get(t)
            return any(c.type == inner for c in node.children) or any(
                self._built(c, spec, built) for c in node.named_children if c.type in spec.string_types
            )
        ops = dict(spec.concat).get(t)
        if ops is not None and any(not c.is_named and c.type in ops for c in node.children):
            return any(
                c.type in spec.string_types or self._built(c, spec, built)
                for c in node.named_children
            )
        if t in spec.call_types:
            callee = spec.callee(self, node)
            return callee is not None and spec.format_callee.search(callee) is not None
        if t == "identifier":
            return self.text(node) in built
        return False
```

(Format the `RiskFact(...)` call normally; ruff format decides line breaks.) Import `Unsafe` from model too. In `run`, after `functions = self.measure(tree.root_node)` add `risks = self.scan_risks(tree.root_node)` and pass `risks=tuple(risks)` to `FileFacts`.

- [ ] **Step 5: Python pack data (SQL sinks only for now)**

In `svarupa/extract/packs/python.py` add `import re`, import `RiskSpec, Sink` from model, and before `PACK`:

```python
def risk_callee(ctx: Ctx, node: TSNode) -> str | None:
    fn = node.child_by_field_name("function")
    if fn is None or fn.end_byte - fn.start_byte > 200:
        return None
    return ctx.text(fn)


RISKS = RiskSpec(
    call_types=frozenset({"call"}),
    callee=risk_callee,
    string_types=frozenset({"string", "concatenated_string"}),
    interpolations=(("string", "interpolation"),),
    concat=(("binary_operator", frozenset({"+", "%"})),),
    format_callee=re.compile(r"\.format$"),
    assigns=(("assignment", "left", "right"), ("augmented_assignment", "left", "right")),
    unwrap=frozenset({"parenthesized_expression", "keyword_argument", "await"}),
    sinks=(
        Sink("sql", re.compile(r"(?:^|\.)(?:execute|executemany|executescript|raw|extra|text)$")),
    ),
)
```

and `risks=RISKS,` in `PACK` after `metrics=METRICS,`.

- [ ] **Step 6: Carry risks to the graph**

`svarupa/extract/__init__.py`, in the final `replace(...)`: add `risks=tuple(r for f in facts for r in f.risks),` after `functions=...`.

`svarupa/build.py`: import `RiskFact`; in `Graph` after `functions` add:

```python
    # Security facts of architecture files, for health checks.
    risks: tuple[RiskFact, ...] = ()
```

and in the `Graph(...)` construction after `functions=...`:

```python
        risks=tuple(
            sorted(
                (r for r in extracted.risks if r.evidence.file in eligible),
                key=lambda r: (r.evidence.file, r.evidence.start_line, r.kind, r.tag),
            )
        ),
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `uv run pytest tests/test_risks.py -q`
Expected: 5 passed

- [ ] **Step 8: Full suite, lint, types, commit**

Run: `uv run pytest -q -x`, `uv run ruff check .`, `uv run pyright`
Expected: all green (golden facts unchanged: `FileFacts` equality is not part of any golden file; if `tests/test_golden_facts.py` compares whole `FileFacts`, ledger a ruling and exclude `risks` the same way `functions` is handled).

```bash
git add svarupa/extract svarupa/build.py tests/test_risks.py
git commit -m "Record security risk facts in the pack walker"
```

---

### Task 2: Python risk data (shell, code, unsafe settings, auth markers)

**Files:**
- Modify: `svarupa/extract/packs/python.py`
- Test: `tests/test_risks.py`

**Interfaces:**
- Consumes: `RiskSpec`, `Sink`, `Unsafe` (Task 1).
- Produces: unsafe facts tagged `tls-verify-off`, `unsafe-deserialize`, `weak-hash`, `debug-enabled`, `cors-any-origin`; `RISKS.auth_markers`.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_risks.py`)

```python
def tags(lang: str, path: str, src: str) -> list[tuple[str, int]]:
    return [(r.tag, r.evidence.start_line) for r in extractor(lang).parse(path, src.encode()).risks]


PY_TRUE = """import subprocess, os, pickle, yaml, hashlib, requests
def f(x, data):
    subprocess.run("ls " + x, shell=True)
    os.system(f"ls {x}")
    eval("1 + " + x)
    requests.get(x, verify=False)
    pickle.loads(data)
    yaml.load(data)
    hashlib.md5(data)
    app.run(debug=True)
    CORS(app)
DEBUG = True
CORS_ALLOW_ALL_ORIGINS = True
"""

PY_SAFE = """import subprocess, os, pickle, yaml, hashlib, requests
def f(x, data):
    subprocess.run(["ls", x])
    subprocess.run("ls " + x)
    requests.get(x, verify=True)
    yaml.load(data, Loader=yaml.SafeLoader)
    yaml.safe_load(data)
    hashlib.md5(data, usedforsecurity=False)
    hashlib.sha256(data)
    app.run(debug=False)
    CORS(app, origins=["https://a.example"])
DEBUG = False
"""


def test_python_true_cases_are_recorded() -> None:
    assert tags("python", "m.py", PY_TRUE) == [
        ("shell", 3),
        ("shell", 4),
        ("code", 5),
        ("tls-verify-off", 6),
        ("unsafe-deserialize", 7),
        ("unsafe-deserialize", 8),
        ("weak-hash", 9),
        ("debug-enabled", 10),
        ("cors-any-origin", 11),
        ("debug-enabled", 12),
        ("cors-any-origin", 13),
    ]


def test_python_safe_twins_are_not_recorded() -> None:
    assert tags("python", "m.py", PY_SAFE) == []


def test_python_pack_names_auth_markers() -> None:
    from svarupa.extract.packs import python

    assert python.RISKS.auth_markers is not None
    assert {"login_required", "get_current_user", "CurrentUser"} <= python.RISKS.auth_markers
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_risks.py -q -k python_true_cases or python_safe or auth_markers`
Expected: `test_python_true_cases_are_recorded` FAIL (only SQL sinks exist), auth markers FAIL.

- [ ] **Step 3: Extend `RISKS` in `python.py`**

Replace `sinks=` and add `unsafe=` and `auth_markers=`:

```python
_KW = frozenset({"keyword_argument"})
_CALL = frozenset({"call"})
_ASSIGN = frozenset({"assignment"})


def _p(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern, re.DOTALL)
```

```python
    sinks=(
        Sink("sql", re.compile(r"(?:^|\.)(?:execute|executemany|executescript|raw|extra|text)$")),
        Sink("shell", re.compile(r"^os\.(?:system|popen)$|^subprocess\.(?:getoutput|getstatusoutput)$")),
        Sink(
            "shell",
            re.compile(r"^subprocess\.(?:run|call|check_call|check_output|Popen)$"),
            re.compile(r"shell\s*=\s*True"),
        ),
        Sink("code", re.compile(r"^(?:eval|exec)$")),
    ),
    unsafe=(
        Unsafe("tls-verify-off", "verify=False", _KW, _p(r"verify\s*=\s*False")),
        Unsafe("tls-verify-off", "unverified SSL context", _CALL, _p(r"ssl\._create_unverified_context\(.*")),
        Unsafe("unsafe-deserialize", "pickle load", _CALL, _p(r"(?:c?[pP]ickle|dill|marshal|shelve|jsonpickle)\.(?:loads?|open|decode)\(.*")),
        Unsafe("unsafe-deserialize", "yaml.load without a safe loader", _CALL, _p(r"yaml\.(?:unsafe_)?load(?:_all)?\((?!.*Safe).*")),
        Unsafe("weak-hash", "MD5 or SHA-1", _CALL, _p(r"hashlib\.(?:md5|sha1)\((?!.*usedforsecurity\s*=\s*False).*")),
        Unsafe("weak-hash", "MD5 or SHA-1", _CALL, _p(r"hashlib\.new\(\s*[\"'](?:md5|sha1)[\"'](?!.*usedforsecurity\s*=\s*False).*")),
        Unsafe("debug-enabled", "DEBUG = True", _ASSIGN, _p(r"DEBUG\s*=\s*True")),
        Unsafe("debug-enabled", "debug=True", _KW, _p(r"debug\s*=\s*True")),
        Unsafe("cors-any-origin", "CORS allows every origin", _KW, _p(r"(?:allow_origins|origins)\s*=\s*(?:\[\s*)?[\"']\*[\"']\s*\]?")),
        Unsafe("cors-any-origin", "CORS allows every origin", _ASSIGN, _p(r"CORS_(?:ORIGIN_ALLOW_ALL|ALLOW_ALL_ORIGINS)\s*=\s*True")),
        Unsafe("cors-any-origin", "CORS with default origins (*)", _CALL, _p(r"CORS\(\s*\w+\s*\)")),
    ),
    auth_markers=frozenset(
        {
            "login_required", "permission_required", "user_passes_test", "jwt_required",
            "auth_required", "requires_auth", "token_required", "fresh_jwt_required",
            "get_current_user", "get_current_active_user", "get_current_active_superuser",
            "CurrentUser", "OAuth2PasswordBearer", "HTTPBearer", "APIKeyHeader",
            "Security", "permission_classes", "IsAuthenticated", "LoginRequiredMixin",
            "PermissionRequiredMixin", "verify_token",
        }
    ),
```

Add `Unsafe` to the model import. Wrap long lines with `# fmt: off` / `# fmt: on` around the `unsafe=` tuple, as the catalog does with `# fmt: skip`.

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/test_risks.py -q`
Expected: all pass

- [ ] **Step 5: Lint, types, commit**

```bash
git add svarupa/extract/packs/python.py tests/test_risks.py
git commit -m "Add Python sinks, unsafe settings and auth markers"
```

---

### Task 3: TypeScript and JavaScript risk data

**Files:**
- Modify: `svarupa/extract/packs/typescript.py` (JavaScript inherits it through `replace`)
- Test: `tests/test_risks.py`

**Interfaces:**
- Consumes: Task 1 model. Produces: `typescript.RISKS`.

- [ ] **Step 1: Write the failing tests**

```python
JS_TRUE = """const cp = require('child_process');
function f(x, db) {
  db.query(`SELECT * FROM t WHERE id = ${x}`);
  let q = "SELECT " + x;
  db.query(q);
  cp.exec("ls " + x);
  eval("1+" + x);
  new Function("return " + x);
  https.request({ host: x, rejectUnauthorized: false });
  crypto.createHash('md5');
  app.use(cors());
  app.use(cors({ origin: '*' }));
  res.setHeader('Access-Control-Allow-Origin', '*');
}
"""

JS_SAFE = """function f(x, db) {
  db.query('SELECT * FROM t WHERE id = $1', [x]);
  cp.execFile('ls', [x]);
  cp.spawn('ls', [x]);
  https.request({ host: x, rejectUnauthorized: true });
  crypto.createHash('sha256');
  app.use(cors({ origin: 'https://a.example' }));
  /a/.exec(x);
}
"""


def test_javascript_true_cases_are_recorded() -> None:
    assert tags("javascript", "m.js", JS_TRUE) == [
        ("sql", 3), ("sql", 5), ("shell", 6), ("code", 7), ("code", 8),
        ("tls-verify-off", 9), ("weak-hash", 10), ("cors-any-origin", 11),
        ("cors-any-origin", 12), ("cors-any-origin", 13),
    ]


def test_javascript_safe_twins_are_not_recorded() -> None:
    assert tags("javascript", "m.js", JS_SAFE) == []


def test_typescript_shares_the_rules() -> None:
    src = "async function f(x: string) {\n  await db.query(`SELECT ${x}`);\n}\n"
    assert tags("typescript", "m.ts", src) == [("sql", 2)]
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_risks.py -q -k "javascript or typescript"`
Expected: FAIL, `[] != [...]`

- [ ] **Step 3: Add `RISKS` to `typescript.py`**

```python
def risk_callee(ctx: Ctx, node: TSNode) -> str | None:
    if node.type == "new_expression":
        ctor = node.child_by_field_name("constructor")
        return f"new {ctx.text(ctor)}" if ctor is not None else None
    fn = node.child_by_field_name("function")
    if fn is None or fn.end_byte - fn.start_byte > 200:
        return None
    return ctx.text(fn)


def _p(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern, re.DOTALL)


_CALL = frozenset({"call_expression"})
_PAIR = frozenset({"pair"})

# fmt: off
RISKS = RiskSpec(
    call_types=frozenset({"call_expression", "new_expression"}),
    callee=risk_callee,
    string_types=frozenset({"string", "template_string"}),
    interpolations=(("template_string", "template_substitution"),),
    concat=(("binary_expression", frozenset({"+"})),),
    format_callee=re.compile(r"(?:^|\.)format$"),
    assigns=(
        ("variable_declarator", "name", "value"),
        ("assignment_expression", "left", "right"),
        ("augmented_assignment_expression", "left", "right"),
    ),
    unwrap=frozenset({"parenthesized_expression", "await_expression"}),
    sinks=(
        Sink("sql", re.compile(r"(?:^|\.)(?:query|execute|raw|\$queryRawUnsafe|\$executeRawUnsafe|unsafe)$")),
        Sink("shell", re.compile(r"^(?:child_process\.|cp\.)?(?:exec|execSync)$")),
        Sink("shell", re.compile(r"^(?:child_process\.|cp\.)?spawn(?:Sync)?$"), re.compile(r"shell\s*:\s*true")),
        Sink("code", re.compile(r"^eval$|^new Function$|^vm\.run(?:InNewContext|InThisContext|InContext)$")),
    ),
    unsafe=(
        Unsafe("tls-verify-off", "rejectUnauthorized: false", _PAIR, _p(r"rejectUnauthorized\s*:\s*false")),
        Unsafe("tls-verify-off", "NODE_TLS_REJECT_UNAUTHORIZED = 0", frozenset({"assignment_expression"}), _p(r"process\.env\.NODE_TLS_REJECT_UNAUTHORIZED\s*=\s*[\"']?0[\"']?")),
        Unsafe("unsafe-deserialize", "node-serialize unserialize", _CALL, _p(r"\w+\.unserialize\(.*")),
        Unsafe("weak-hash", "MD5 or SHA-1", _CALL, _p(r"(?:crypto\.)?createHash\(\s*[\"'](?:md5|sha1)[\"']\s*\)")),
        Unsafe("cors-any-origin", "CORS with default origins (*)", _CALL, _p(r"cors\(\s*\)")),
        Unsafe("cors-any-origin", "CORS origin '*'", _PAIR, _p(r"origin\s*:\s*[\"']\*[\"']")),
        Unsafe("cors-any-origin", "Access-Control-Allow-Origin: *", _CALL, _p(r"\w+\.(?:setHeader|header|set)\(\s*[\"']Access-Control-Allow-Origin[\"']\s*,\s*[\"']\*[\"']\s*\)")),
    ),
    auth_markers=frozenset({
        "authenticate", "isAuthenticated", "ensureAuthenticated", "ensureLoggedIn",
        "requireAuth", "requireLogin", "verifyToken", "checkJwt", "jwtCheck", "auth",
        "protect", "authorize", "UseGuards", "AuthGuard", "JwtAuthGuard", "GqlAuthGuard",
        "withAuth", "getServerSession", "currentUser",
    }),
)
# fmt: on
```

Add `import re` and the model imports (`RiskSpec`, `Sink`, `Unsafe`), then `risks=RISKS,` in `PACK` after `metrics=METRICS,`. `javascript.PACK` is `replace(typescript.PACK, ...)`, so it inherits `risks`.

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/test_risks.py -q`
Expected: all pass. If a node type differs from the probe (for example `pair` is `property_assignment` in the TSX grammar), fix the data, not the test, and ledger a ruling.

- [ ] **Step 5: Lint, types, commit**

```bash
git add svarupa/extract/packs/typescript.py tests/test_risks.py
git commit -m "Add TypeScript and JavaScript sinks and unsafe settings"
```

---

### Task 4: Go risk data

**Files:**
- Modify: `svarupa/extract/packs/go.py`
- Test: `tests/test_risks.py`

- [ ] **Step 1: Write the failing tests**

```python
GO_TRUE = """package m
func f(x string) {
\tdb.Query("SELECT * FROM t WHERE id = " + x)
\tq := fmt.Sprintf("SELECT %s", x)
\tdb.Exec(q)
\texec.Command("sh", "-c", "ls "+x)
\tc := &tls.Config{InsecureSkipVerify: true}
\th := md5.Sum([]byte(x))
\tgin.SetMode(gin.DebugMode)
\tcfg := cors.Config{AllowAllOrigins: true}
}
"""

GO_SAFE = """package m
func f(x string) {
\tdb.Query("SELECT * FROM t WHERE id = $1", x)
\texec.Command("ls", x)
\tc := &tls.Config{InsecureSkipVerify: false}
\th := sha256.Sum256([]byte(x))
\tgin.SetMode(gin.ReleaseMode)
}
"""


def test_go_true_cases_are_recorded() -> None:
    assert tags("go", "m.go", GO_TRUE) == [
        ("sql", 3), ("sql", 5), ("shell", 6), ("tls-verify-off", 7),
        ("weak-hash", 8), ("debug-enabled", 9), ("cors-any-origin", 10),
    ]


def test_go_safe_twins_are_not_recorded() -> None:
    assert tags("go", "m.go", GO_SAFE) == []
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_risks.py -q -k go_`
Expected: FAIL

- [ ] **Step 3: Add `RISKS` to `go.py`**

```python
def risk_callee(ctx: Ctx, node: TSNode) -> str | None:
    fn = node.child_by_field_name("function")
    if fn is None or fn.end_byte - fn.start_byte > 200:
        return None
    return ctx.text(fn)


def _p(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern, re.DOTALL)


_KEYED = frozenset({"keyed_element"})
_CALL = frozenset({"call_expression"})

# fmt: off
RISKS = RiskSpec(
    call_types=frozenset({"call_expression"}),
    callee=risk_callee,
    string_types=frozenset({"interpreted_string_literal", "raw_string_literal"}),
    interpolations=(),
    concat=(("binary_expression", frozenset({"+"})),),
    format_callee=re.compile(r"^fmt\.Sprintf$"),
    assigns=(
        ("short_var_declaration", "left", "right"),
        ("assignment_statement", "left", "right"),
        ("var_spec", "name", "value"),
    ),
    unwrap=frozenset({"parenthesized_expression", "expression_list"}),
    sinks=(
        Sink("sql", re.compile(r"\.(?:Query|QueryRow|QueryContext|QueryRowContext|Exec|ExecContext|Raw|Prepare|PrepareContext)$")),
        Sink("shell", re.compile(r"^exec\.Command(?:Context)?$"), re.compile(r"\"(?:/bin/)?(?:sh|bash|zsh)\"\s*,\s*\"-c\"")),
    ),
    unsafe=(
        Unsafe("tls-verify-off", "InsecureSkipVerify: true", _KEYED, _p(r"InsecureSkipVerify\s*:\s*true")),
        Unsafe("weak-hash", "MD5 or SHA-1", _CALL, _p(r"(?:md5|sha1)\.(?:New|Sum)\(.*")),
        Unsafe("debug-enabled", "gin debug mode", _CALL, _p(r"gin\.SetMode\(\s*gin\.DebugMode\s*\)")),
        Unsafe("cors-any-origin", "CORS allows every origin", _KEYED, _p(r"AllowAllOrigins\s*:\s*true|Allow(?:ed)?Origins\s*:\s*\[\]string\{\s*\"\*\"\s*\}")),
    ),
)
# fmt: on
```

Go has no route extraction yet, so `auth_markers` stays empty. Add imports and `risks=RISKS,` to `PACK`.

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/test_risks.py -q`
Expected: all pass. Note: `exec.Command("sh", "-c", "ls "+x)`: the built argument is the third; the engine checks every argument, so it fires.

- [ ] **Step 5: Lint, types, commit**

```bash
git add svarupa/extract/packs/go.py tests/test_risks.py
git commit -m "Add Go sinks and unsafe settings"
```

---

### Task 5: Java risk data

**Files:**
- Modify: `svarupa/extract/packs/java.py`
- Test: `tests/test_risks.py`

- [ ] **Step 1: Write the failing tests**

```python
JAVA_TRUE = """class A {
  void f(String x) throws Exception {
    stmt.executeQuery("SELECT * FROM t WHERE id = " + x);
    String q = String.format("SELECT %s", x);
    jdbc.queryForList(q);
    Runtime.getRuntime().exec("ls " + x);
    new ObjectInputStream(in);
    MessageDigest.getInstance("MD5");
    new NoopHostnameVerifier();
    registry.addMapping("/**").allowedOrigins("*");
  }
  @CrossOrigin
  void g() {}
}
"""

JAVA_SAFE = """class A {
  void f(String x) throws Exception {
    PreparedStatement p = conn.prepareStatement("SELECT * FROM t WHERE id = ?");
    p.setString(1, x);
    new ProcessBuilder("ls", x);
    MessageDigest.getInstance("SHA-256");
    registry.addMapping("/**").allowedOrigins("https://a.example");
  }
  @CrossOrigin(origins = "https://a.example")
  void g() {}
}
"""


def test_java_true_cases_are_recorded() -> None:
    assert tags("java", "A.java", JAVA_TRUE) == [
        ("sql", 3), ("sql", 5), ("shell", 6), ("unsafe-deserialize", 7),
        ("weak-hash", 8), ("tls-verify-off", 9), ("cors-any-origin", 10),
        ("cors-any-origin", 12),
    ]


def test_java_safe_twins_are_not_recorded() -> None:
    assert tags("java", "A.java", JAVA_SAFE) == []
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_risks.py -q -k java_`
Expected: FAIL

- [ ] **Step 3: Add `RISKS` to `java.py`**

```python
def risk_callee(ctx: Ctx, node: TSNode) -> str | None:
    if node.type == "object_creation_expression":
        typ = node.child_by_field_name("type")
        return f"new {ctx.text(typ)}" if typ is not None else None
    name = node.child_by_field_name("name")
    if name is None:
        return None
    obj = node.child_by_field_name("object")
    if obj is not None and obj.end_byte - obj.start_byte > 200:
        return None
    return f"{ctx.text(obj)}.{ctx.text(name)}" if obj is not None else ctx.text(name)


def _p(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern, re.DOTALL)


_INVOKE = frozenset({"method_invocation"})
_NEW = frozenset({"object_creation_expression"})

# fmt: off
RISKS = RiskSpec(
    call_types=frozenset({"method_invocation", "object_creation_expression"}),
    callee=risk_callee,
    string_types=frozenset({"string_literal"}),
    interpolations=(),
    concat=(("binary_expression", frozenset({"+"})),),
    format_callee=re.compile(r"^String\.format$|\.formatted$"),
    assigns=(
        ("variable_declarator", "name", "value"),
        ("assignment_expression", "left", "right"),
    ),
    unwrap=frozenset({"parenthesized_expression"}),
    sinks=(
        Sink("sql", re.compile(r"(?:^|\.)(?:executeQuery|executeUpdate|executeLargeUpdate|execute|addBatch|prepareStatement|prepareCall|createQuery|createNativeQuery|queryForList|queryForObject|queryForMap|query|update|batchUpdate)$")),
        Sink("shell", re.compile(r"getRuntime\(\)\.exec$|^new ProcessBuilder$")),
        Sink("code", re.compile(r"(?:^|\.)(?:eval|parseExpression)$")),
    ),
    unsafe=(
        Unsafe("unsafe-deserialize", "ObjectInputStream", _NEW, _p(r"new (?:ObjectInputStream|XMLDecoder)\(.*")),
        Unsafe("weak-hash", "MD5 or SHA-1", _INVOKE, _p(r"MessageDigest\.getInstance\(\s*\"(?:MD5|MD2|SHA-?1)\".*")),
        Unsafe("tls-verify-off", "hostname verification off", _NEW, _p(r"new (?:NoopHostnameVerifier|TrustAllStrategy)\(.*")),
        Unsafe("cors-any-origin", "CORS origin '*'", _INVOKE, _p(r".*\.allowedOrigins\(\s*\"\*\"\s*\)")),
        Unsafe("cors-any-origin", "@CrossOrigin allows every origin", frozenset({"marker_annotation"}), _p(r"@CrossOrigin")),
        Unsafe("cors-any-origin", "@CrossOrigin allows every origin", frozenset({"annotation"}), _p(r"@CrossOrigin\(\s*(?:origins\s*=\s*)?\"\*\"\s*\)")),
    ),
    auth_markers=frozenset({"PreAuthorize", "Secured", "RolesAllowed", "PostAuthorize"}),
)
# fmt: on
```

Add imports and `risks=RISKS,` to `PACK`.

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/test_risks.py -q`
Expected: all pass. The Java `.allowedOrigins("*")` invocation is chained, so the whole-node text starts with `registry`; the pattern opens with `.*` for that reason.

- [ ] **Step 5: Lint, types, commit**

```bash
git add svarupa/extract/packs/java.py tests/test_risks.py
git commit -m "Add Java sinks and unsafe settings"
```

---

### Task 6: Security catalog, checks from risk facts, `at` exceptions

**Files:**
- Modify: `svarupa/health/catalog.py`, `svarupa/health/model.py`, `svarupa/health/__init__.py`, `svarupa/health/checks.py`, `svarupa/design/file.py`, `svarupa/design/model.py`, `svarupa/design/__init__.py`, `svarupa/emit/report.py`, `tests/test_health.py`, `tests/test_health_outputs.py`
- Create: `svarupa/health/security.py`, `tests/test_security.py`

**Interfaces:**
- Consumes: `Graph.risks` (Task 1).
- Produces: `security_checks(graph) -> list[Violation]`; `DesignException.at: str = ""`; `DesignFile.auth_markers: list[str]`; `Health.accepted: tuple[Violation, ...] = ()`, `Health.notes: tuple[str, ...] = ()`; `assess(graph, texts, design=None, exceptions=(), *, secrets=None, auth_markers=())`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_security.py
"""Security checks from risk facts, and exceptions by location."""

from __future__ import annotations

from pathlib import Path

from svarupa.build import build
from svarupa.design.file import DesignException, load_design
from svarupa.detect import detect
from svarupa.extract import declared_dependencies, extract
from svarupa.health import BY_ID, assess, source_texts
from tests.test_health import write


def run(root: Path, exceptions=()):
    scan = detect(root)
    graph = build(scan, extract(scan, declared_dependencies(scan)), strict=False)
    return assess(graph, source_texts(scan, graph), None, exceptions)


def test_security_checks_are_in_the_catalog() -> None:
    for cid, sev in (
        ("hardcoded-secret", "blocker"), ("sql-injection", "critical"),
        ("command-injection", "critical"), ("code-injection", "critical"),
        ("tls-verify-off", "critical"), ("unsafe-deserialize", "critical"),
        ("weak-hash", "major"), ("debug-enabled", "major"),
        ("cors-any-origin", "major"), ("endpoint-without-auth", "major"),
    ):
        check = BY_ID[cid]
        assert (check.area, check.severity, check.maturity) == ("security", sev, "experimental")
        assert "CWE-" in check.source


def test_injection_is_reported_with_its_cwe_and_rates_security_d(tmp_path: Path) -> None:
    write(tmp_path, "app/m.py", "def f(cur, x):\n    cur.execute(\"SELECT \" + x)\n")
    h = run(tmp_path)
    v = next(v for v in h.violations if v.check == "sql-injection")
    assert (v.evidence[0].file, v.evidence[0].start_line) == ("app/m.py", 2)
    assert "cur.execute" in v.message and "CWE-89" in v.message
    assert h.rating("security") == "D"


def test_clean_code_rates_security_a(tmp_path: Path) -> None:
    write(tmp_path, "app/m.py", "def f():\n    return 1\n")
    assert run(tmp_path).rating("security") == "A"


def test_at_exception_moves_a_violation_to_accepted(tmp_path: Path) -> None:
    write(tmp_path, "app/m.py", "import hashlib\ndef f(d):\n    return hashlib.md5(d)\n")
    exc = (DesignException("weak-hash", "", "", "cache key, not a password", at="app/m.py:3"),)
    h = run(tmp_path, exc)
    assert "weak-hash" not in [v.check for v in h.violations]
    assert [v.check for v in h.accepted] == ["weak-hash"]
    assert h.rating("security") == "A"
    assert h.to_json()["accepted"][0]["check"] == "weak-hash"


def test_design_yaml_reads_at_exceptions_and_auth_markers(tmp_path: Path) -> None:
    write(
        tmp_path,
        ".svarupa/design.yaml",
        "units: {}\n"
        "auth_markers: [require_session]\n"
        "exceptions:\n"
        "  - rule: hardcoded-secret\n    at: tests/fixtures/key.pem\n    reason: fake key\n"
        "  - rule: weak-hash\n    at: app/m.py:3\n",
    )
    loaded, problem = load_design(tmp_path / ".svarupa" / "design.yaml")
    assert problem == "" and loaded is not None
    assert loaded.auth_markers == ["require_session"]
    assert [(e.rule, e.at) for e in loaded.exceptions] == [
        ("hardcoded-secret", "tests/fixtures/key.pem")
    ]  # the second has no reason, so it is dropped
```

In `tests/test_health.py` change `test_catalog_matches_the_spec` to compare only the maintainability ids (`[c.id for c in CATALOG if c.area == "maintainability"]`) and drop the `all(c.area == "maintainability" ...)` assertion in favour of `all(c.source for c in CATALOG)`; change `test_unassessed_areas_have_no_grade` to expect `[None, "A", None]` for reliability, security, performance and rename it `test_only_assessed_areas_have_a_grade`. In `tests/test_health_outputs.py` change the security assertion to `== "A"` and add `assert gj["health"]["ratings"]["reliability"] is None`.

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_security.py tests/test_health.py tests/test_health_outputs.py -q`
Expected: FAIL with `KeyError: 'hardcoded-secret'` and `TypeError` on `at=`.

- [ ] **Step 3: Catalog**

Append to `CATALOG` in `svarupa/health/catalog.py`:

```python
    Check("hardcoded-secret", "Secret committed in the repository", 0, "CWE-798; OWASP A07:2021; gitleaks default rules", "security", "blocker", 30),
    Check("sql-injection", "SQL built from strings", 0, "CWE-89; OWASP A03:2021", "security", "critical", 30),
    Check("command-injection", "Shell command built from strings", 0, "CWE-78; OWASP A03:2021", "security", "critical", 30),
    Check("code-injection", "Code built from strings and run", 0, "CWE-94; OWASP A03:2021", "security", "critical", 30),
    Check("tls-verify-off", "TLS certificate check turned off", 0, "CWE-295; OWASP A02:2021", "security", "critical", 10),
    Check("unsafe-deserialize", "Unsafe deserialization", 0, "CWE-502; OWASP A08:2021", "security", "critical", 30),
    Check("weak-hash", "Weak hash (MD5 or SHA-1)", 0, "CWE-327; OWASP A02:2021", "security", "major", 10),
    Check("debug-enabled", "Debug mode turned on", 0, "CWE-489; OWASP A05:2021", "security", "major", 5),
    Check("cors-any-origin", "CORS allows every origin", 0, "CWE-942; OWASP A05:2021", "security", "major", 10),
    Check("endpoint-without-auth", "Write endpoint without auth", 0, "CWE-306; OWASP A01:2021", "security", "major", 20),
```

- [ ] **Step 4: Checks from risk facts**

```python
# svarupa/health/security.py
"""Security checks from the risk facts the walker recorded."""

from __future__ import annotations

from svarupa.build import Graph
from svarupa.extract.base import module_of
from svarupa.health.catalog import BY_ID
from svarupa.health.model import Violation

__all__ = ["security_checks"]

_SINKS = {
    "sql": ("sql-injection", "SQL", "pass values as query parameters"),
    "shell": ("command-injection", "a shell command", "pass arguments as a list, without a shell"),
    "code": ("code-injection", "code", "do not run code built from data"),
}


def _cwe(check_id: str) -> str:
    return BY_ID[check_id].source.split(";", 1)[0]


def security_checks(graph: Graph) -> list[Violation]:
    out: list[Violation] = []
    for r in graph.risks:
        where = f" in {r.function}" if r.function else ""
        if r.kind == "sink-built":
            check_id, what, fix = _SINKS[r.tag]
            message = f"{r.detail}{where} runs {what} built from strings; {fix} ({_cwe(check_id)})"
        elif r.kind == "unsafe" and r.tag in BY_ID:
            check_id = r.tag
            message = f"{r.detail}{where}: {BY_ID[check_id].title.lower()} ({_cwe(check_id)})"
        else:
            continue
        out.append(
            Violation(
                check=check_id,
                message=message,
                evidence=(r.evidence,),
                module=module_of(r.evidence.file),
                value=1,
                minutes=BY_ID[check_id].minutes,
            )
        )
    return out
```

- [ ] **Step 5: `Health.accepted` and `Health.notes`**

In `svarupa/health/model.py` add to `Health`:

```python
    accepted: tuple[Violation, ...] = ()  # excepted in design.yaml, not counted
    notes: tuple[str, ...] = ()  # what a check could not judge, and why
```

and in `to_json` add `"accepted": [v.to_json() for v in self.accepted],` and `"notes": list(self.notes),` after `"violations"`.

- [ ] **Step 6: `at` exceptions and `auth_markers` in design.yaml**

`svarupa/design/file.py`:

```python
@dataclass(frozen=True)
class DesignException:
    rule: str
    src: str
    dst: str
    reason: str
    at: str = ""  # "path" or "path:line": excepts any check at that place


@dataclass(frozen=True)
class DesignFile:
    units: dict[str, UnitSpec]
    exceptions: list[DesignException]
    auth_markers: list[str] = field(default_factory=list[str])
```

In `load_design`, build each exception with `at=str(e.get("at", ""))`, read markers:

```python
    raw_markers: object = data.get("auth_markers") or []
    markers = (
        [str(m) for m in cast("list[object]", raw_markers)]
        if isinstance(raw_markers, list)
        else []
    )
    return DesignFile(units, exceptions, markers), ""
```

In `dump_design`, write each kept exception as `{"rule", "at", "reason"}` when `e.at` else the existing `from`/`to` form, and emit `auth_markers` (only when `keep` has any) between `units` and `exceptions`:

```python
    doc: dict[str, object] = {"units": units}
    if keep and keep.auth_markers:
        doc["auth_markers"] = list(keep.auth_markers)
    doc["exceptions"] = exceptions
    return header + yaml.safe_dump(doc, sort_keys=False, allow_unicode=True)
```

with

```python
    exceptions = [
        {"rule": e.rule, "at": e.at, "reason": e.reason}
        if e.at
        else {"rule": e.rule, "from": e.src, "to": e.dst, "reason": e.reason}
        for e in (keep.exceptions if keep else [])
    ]
```

In `svarupa/design/model.py` `Design.to_json`, emit `{"rule", "at", "reason"}` for exceptions with `at`, the existing form otherwise. In `svarupa/design/__init__.py` build `excused` from `e for e in excepted if not e.at`, and in `svarupa/health/checks.py` `design_checks` the same filter (`for e in exceptions if not e.at`), so a location exception never matches a design import pair.

- [ ] **Step 7: Wire `assess`**

```python
def _excepted(v: Violation, exceptions: Sequence[DesignException]) -> bool:
    ev = v.evidence[0]
    places = {ev.file, f"{ev.file}:{ev.start_line}"}
    return any(e.at and e.rule == v.check and e.at in places for e in exceptions)


def assess(
    graph: Graph,
    texts: Mapping[str, str],
    design: Design | None = None,
    exceptions: Sequence[DesignException] = (),
    *,
    secrets: Mapping[str, str] | None = None,
    auth_markers: Sequence[str] = (),
) -> Health:
    found = [
        *function_checks(graph),
        *file_checks(texts),
        *class_checks(graph),
        *duplicated_blocks(texts),
        *module_checks(graph),
        *design_checks(design, exceptions),
        *security_checks(graph),
    ]
    kept = [v for v in found if not _excepted(v, exceptions)]
    accepted = [v for v in found if _excepted(v, exceptions)]
    health = grade(graph, texts, kept)
    accepted_sorted = tuple(
        sorted(accepted, key=lambda v: (v.check, v.evidence[0].file, v.evidence[0].start_line))
    )
    return replace(health, accepted=accepted_sorted)
```

(`secrets` and `auth_markers` are accepted now and used by Tasks 7 and 8; pyright strict will flag them unused only if `reportUnusedParameter` is on; if it is, prefix nothing and add them in those tasks instead, and ledger the ruling.) Import `replace` from `dataclasses` and `security_checks`.

- [ ] **Step 8: Report shows accepted and notes**

In `svarupa/emit/report.py` `_health_section`, after the debt bullet and before `if not health.violations`:

```python
    for note in health.notes:
        lines += [f"- Note: {note}"]
    if health.accepted:
        lines += [f"- {len(health.accepted)} violation(s) accepted as exceptions in design.yaml", ""]
```

- [ ] **Step 9: Run tests to verify they pass**

Run: `uv run pytest tests/test_security.py tests/test_health.py tests/test_health_outputs.py tests/test_design*.py -q`
Expected: all pass

- [ ] **Step 10: Full suite, lint, types, commit**

```bash
git add svarupa tests
git commit -m "Grade security from injection and unsafe setting checks"
```

---

### Task 7: Committed secrets

**Files:**
- Modify: `svarupa/detect.py`, `svarupa/health/__init__.py`, `svarupa/cli.py`, `scripts/benchmark.py`
- Create: `svarupa/health/secrets.py`, `tests/test_secrets.py`

**Interfaces:**
- Produces: `Scan.others: tuple[str, ...]` (non-ignored files of no analyzed language, under the size limit); `secret_texts(scan) -> dict[str, str]`; `secret_checks(texts) -> list[Violation]`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_secrets.py
"""Committed secrets: found in any file, and the value never leaves it.

Fake keys are assembled at runtime so this file never holds a string that a
secret scanner (or GitHub push protection) would flag.
"""

from __future__ import annotations

import json
from pathlib import Path

from svarupa.cli import main
from svarupa.health.secrets import secret_checks

AWS = "AKIA" + "Q7XZ" + "M2PL" + "W9RT" + "K4VB"
GH = "ghp" + "_" + "a1B2c3D4e5F6g7H8i9J0" + "k1L2m3N4o5P6"
PASSWORD = "Zq8" + "rT2vLp9w"


def hits(texts: dict[str, str]) -> list[tuple[str, int, str]]:
    return [(v.evidence[0].file, v.evidence[0].start_line, v.message) for v in secret_checks(texts)]


def test_key_formats_and_assignments_are_found_by_line() -> None:
    texts = {
        "app/settings.py": f"A = 1\nAWS_KEY = '{AWS}'\n",
        "deploy/.env": f"GITHUB_TOKEN={GH}\nDB_PASSWORD={PASSWORD}\n",
        "app/conf.yaml": f"db:\n  password: \"{PASSWORD}\"\n",
    }
    found = hits(texts)
    assert [(f, line) for f, line, _ in found] == [
        ("app/conf.yaml", 2), ("app/settings.py", 2), ("deploy/.env", 1), ("deploy/.env", 2),
    ]
    assert found[1][2].startswith("AWS access key")


def test_placeholders_and_env_reads_are_skipped() -> None:
    texts = {
        "a.py": (
            "password = 'changeme123'\n"
            "API_KEY = os.environ['API_KEY']\n"
            "token = '${TOKEN_VALUE}'\n"
            "secret = '<your-secret-here>'\n"
            "aws = 'AKIAIOSFODNN7EXAMPLE'\n"
            "password = ''\n"
        ),
        ".env.example": "DB_PASSWORD=your_password_here\n",
    }
    assert hits(texts) == []


def test_message_has_a_fingerprint_and_never_the_value() -> None:
    (v,) = secret_checks({"a.py": f"key = '{AWS}'\n"})
    assert AWS not in v.message and AWS[:8] not in v.message
    assert "fingerprint" in v.message


def test_secret_value_reaches_no_output(tmp_path: Path, capsys) -> None:
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "m.py").write_text(f"KEY = '{AWS}'\n", encoding="utf8")
    (tmp_path / ".env").write_text(f"DB_PASSWORD={PASSWORD}\n", encoding="utf8")
    assert main([str(tmp_path)]) == 0
    out = capsys.readouterr().out
    gj = json.loads((tmp_path / ".svarupa" / "graph.json").read_text(encoding="utf8"))
    checks = [v["check"] for v in gj["health"]["violations"]]
    assert checks.count("hardcoded-secret") == 2
    assert gj["health"]["ratings"]["security"] == "E"
    assert main(["query", str(tmp_path / ".svarupa"), "get_health", "--json"]) == 0
    out += capsys.readouterr().out
    for path in (tmp_path / ".svarupa").rglob("*"):
        if path.is_file():
            data = path.read_text(encoding="utf8", errors="replace")
            assert AWS not in data and PASSWORD not in data, path.name
    assert AWS not in out and PASSWORD not in out
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_secrets.py -q`
Expected: FAIL with `ModuleNotFoundError: svarupa.health.secrets`

- [ ] **Step 3: `Scan.others` in detect**

In `Scan` add, after `skipped`:

```python
    # Non-ignored files of no analyzed language (.env, YAML, properties),
    # under the size limit. Only the secret scanner reads them.
    others: tuple[str, ...] = ()
```

In `detect`, create `others: list[str] = []` next to `files`; replace the unrecognized branch with:

```python
            if lang is None and config_kind is None:
                skipped.hit("unrecognized-type")
                try:
                    small = entry.stat(follow_symlinks=False).st_size <= limits.max_file_bytes
                except OSError:
                    small = False
                if small and len(others) < limits.max_files:
                    others.append(unicodedata.normalize("NFC", rel))
                continue
```

and pass `others=tuple(sorted(others))` to `Scan(...)`. (`rel` is already NFC if the walk normalizes names before this point; if so drop the `normalize` call. Check with the existing `ordered` construction and match it.)

- [ ] **Step 4: The scanner**

```python
# svarupa/health/secrets.py
"""Committed secrets: known key formats and secret-named assignments.

The value is never kept: a violation carries the rule, the line and the
first 12 hex characters of the value's SHA-256, enough to tell two findings
apart and to check a rotated key, and useless to anyone reading the report.
Formats follow the gitleaks default rules.
"""

from __future__ import annotations

import bisect
import hashlib
import math
import re
from collections.abc import Mapping

from svarupa.detect import FileRole, Scan, read_text
from svarupa.extract.base import module_of
from svarupa.health.catalog import BY_ID
from svarupa.health.model import Violation
from svarupa.model import Evidence

__all__ = ["secret_checks", "secret_texts"]

_KEYS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("AWS access key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("GitHub token", re.compile(r"\b(?:gh[pousr]_[0-9A-Za-z]{36}|github_pat_[0-9A-Za-z_]{82})\b")),
    ("Slack token", re.compile(r"\bxox[abprs]-[0-9A-Za-z-]{10,}")),
    ("Stripe live key", re.compile(r"\b[rs]k_live_[0-9A-Za-z]{24,}\b")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("Anthropic API key", re.compile(r"\bsk-ant-[0-9A-Za-z_-]{32,}")),
    ("OpenAI API key", re.compile(r"\bsk-(?:proj-)?[0-9A-Za-z_-]{40,}")),
    ("Private key", re.compile(r"-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----[A-Za-z0-9+/=\s]{0,64}")),
    ("JSON Web Token", re.compile(r"\beyJ[0-9A-Za-z_-]{10,}\.eyJ[0-9A-Za-z_-]{10,}\.[0-9A-Za-z_-]{10,}")),
)
_NAME = (
    r"[\w.-]*?(?:password|passwd|pwd|secret|secret[_-]?key|api[_-]?key|access[_-]?key"
    r"|auth[_-]?token|access[_-]?token|private[_-]?key|client[_-]?secret|token)"
)
_QUOTED = re.compile(
    rf"(?i)(?<![\w.-])({_NAME})[\"']?\s*(?:=|:|=>|:=)\s*([\"'])([^\"'\s]{{8,200}})\2"
)
_BARE = re.compile(
    rf"(?im)^[ \t]*(?:export[ \t]+)?({_NAME})[ \t]*[=:][ \t]*([A-Za-z0-9_\-+/=.@!%*^~]{{8,200}})[ \t]*(?:#.*)?$"
)
_CONFIG = (".env", ".properties", ".ini", ".cfg", ".conf", ".yaml", ".yml")
_PLACEHOLDER = re.compile(
    r"(?i)change.?me|example|sample|dummy|placeholder|your|xxx|\*\*\*|redacted|fake|test"
    r"|todo|^none$|^null$|^undefined$|^<|^\$|^\{\{|^%\(|environ|getenv"
)
_SKIP_NAMES = frozenset(
    {"package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "uv.lock", "go.sum", "Cargo.lock"}
)
_MIN_ENTROPY = 3.0
_MAX_BYTES = 1_000_000


def _entropy(value: str) -> float:
    counts = {c: value.count(c) for c in set(value)}
    return -sum(n / len(value) * math.log2(n / len(value)) for n in counts.values())


def _is_config(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return name.startswith(".env") or name.endswith(_CONFIG)


def secret_texts(scan: Scan) -> dict[str, str]:
    """Every file a secret could be committed in: tracked source and config of
    any role but generated or vendored, plus files of no analyzed language."""
    paths = [
        f.path for f in scan.files if f.role not in (FileRole.GENERATED, FileRole.VENDORED)
    ] + list(scan.others)
    out: dict[str, str] = {}
    for path in sorted(set(paths)):
        if path.startswith(".svarupa/") or path.rsplit("/", 1)[-1] in _SKIP_NAMES:
            continue
        try:
            text = read_text(scan.root, path)
        except OSError:
            continue
        if len(text) <= _MAX_BYTES and "\x00" not in text:
            out[path] = text
    return out


def _found(path: str, text: str) -> list[tuple[int, str, str]]:
    """(offset, rule, value) for each secret in one file."""
    hits: list[tuple[int, str, str]] = []
    taken: list[tuple[int, int]] = []
    for rule, rx in _KEYS:
        for m in rx.finditer(text):
            if any(a < m.end() and m.start() < b for a, b in taken):
                continue
            if _PLACEHOLDER.search(m.group(0)):
                continue
            taken.append((m.start(), m.end()))
            hits.append((m.start(), rule, m.group(0)))
    patterns = [(_QUOTED, 3)] + ([(_BARE, 2)] if _is_config(path) else [])
    for rx, group in patterns:
        for m in rx.finditer(text):
            value = m.group(group)
            start = m.start(group)
            if any(a <= start < b for a, b in taken):
                continue
            if _PLACEHOLDER.search(value) or _entropy(value) < _MIN_ENTROPY:
                continue
            taken.append((start, m.end(group)))
            hits.append((start, f"{m.group(1)} assigned a literal", value))
    return sorted(hits)


def secret_checks(texts: Mapping[str, str]) -> list[Violation]:
    check = BY_ID["hardcoded-secret"]
    out: list[Violation] = []
    for path, text in sorted(texts.items()):
        newlines = [i for i, c in enumerate(text) if c == "\n"]
        for offset, rule, value in _found(path, text):
            line = bisect.bisect_left(newlines, offset) + 1
            fingerprint = hashlib.sha256(value.encode()).hexdigest()[:12]
            out.append(
                Violation(
                    check="hardcoded-secret",
                    message=(
                        f"{rule} (fingerprint {fingerprint}); move it to a secret store "
                        f"and rotate it (CWE-798)"
                    ),
                    evidence=(Evidence(path, line, line),),
                    module=module_of(path),
                    value=1,
                    minutes=check.minutes,
                )
            )
    return out
```

The quoted-assignment rule label uses `m.group(1)`, the variable name (for example `DB_PASSWORD assigned a literal`); a name is not a secret. Check `Evidence`'s constructor arguments against `svarupa/model.py` and adjust if it takes keywords.

- [ ] **Step 5: Wire into `assess`, the CLI and the benchmark**

In `assess`, add `*secret_checks(secrets or {}),` to `found`. In `svarupa/cli.py` change the health call to:

```python
    health = assess(
        graph,
        source_texts(scan, graph),
        design,
        design.exceptions,
        secrets=secret_texts(scan),
        auth_markers=tuple(accepted.auth_markers) if accepted else (),
    )
```

and export `secret_texts` from `svarupa.health`. In `scripts/benchmark.py` `observe`, pass `secrets=secret_texts(scan)`.

- [ ] **Step 6: Run to verify they pass**

Run: `uv run pytest tests/test_secrets.py -q`
Expected: 4 passed. If the no-leak test fails on a file other than graph.json, that output embeds source text; stop and treat it as a finding (do not weaken the test).

- [ ] **Step 7: Full suite, lint, types; dogfood; commit**

Run: `uv run pytest -q`, `uv run ruff check .`, `uv run pyright`, then `uv run svarupa . && grep -c hardcoded-secret .svarupa/graph.json` on this repository. Every hit must be a real secret or a test fixture; a false positive in our own code is a bug in the rules, fixed with a test first.

```bash
git add svarupa scripts/benchmark.py tests/test_secrets.py
git commit -m "Find committed secrets without storing their values"
```

---

### Task 8: Endpoint without auth

**Files:**
- Create: `svarupa/health/auth.py`, `tests/test_auth.py`
- Modify: `svarupa/health/__init__.py`

**Interfaces:**
- Consumes: `Graph.routes`, `Graph.functions`, `Graph.nodes`; packs' `RISKS.auth_markers`; `Design.units` (`u.unit.id`).
- Produces: `auth_checks(graph, texts, markers, units) -> tuple[list[Violation], list[str]]`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_auth.py
"""Write endpoints without an auth marker on the handler, router or middleware."""

from __future__ import annotations

from pathlib import Path

from svarupa.build import build
from svarupa.detect import detect
from svarupa.extract import declared_dependencies, extract
from svarupa.health import assess, source_texts
from tests.test_health import write

DEPS = "def get_current_user():\n    return 1\n"


def run(root: Path, markers=()):
    scan = detect(root)
    graph = build(scan, extract(scan, declared_dependencies(scan)), strict=False)
    return assess(graph, source_texts(scan, graph), auth_markers=markers)


def flagged(h) -> list[tuple[str, int]]:
    return [
        (v.evidence[0].file, v.evidence[0].start_line)
        for v in h.violations
        if v.check == "endpoint-without-auth"
    ]


def test_fastapi_write_route_without_auth_is_flagged(tmp_path: Path) -> None:
    write(tmp_path, "app/deps.py", DEPS)
    write(
        tmp_path,
        "app/items.py",
        "from fastapi import APIRouter, Depends\n"
        "from app.deps import get_current_user\n"
        "router = APIRouter()\n"
        "@router.post('/items')\n"
        "def create(item: dict):\n"
        "    return item\n"
        "@router.delete('/items/{i}')\n"
        "def remove(i: int, user=Depends(get_current_user)):\n"
        "    return i\n"
        "@router.get('/items')\n"
        "def read():\n"
        "    return []\n"
        "@router.post('/login/access-token')\n"
        "def login():\n"
        "    return 1\n",
    )
    assert flagged(run(tmp_path)) == [("app/items.py", 4)]


def test_router_level_dependency_protects_every_route(tmp_path: Path) -> None:
    write(tmp_path, "app/deps.py", DEPS)
    write(
        tmp_path,
        "app/items.py",
        "from fastapi import APIRouter, Depends\n"
        "from app.deps import get_current_user\n"
        "router = APIRouter(dependencies=[Depends(get_current_user)])\n"
        "@router.post('/items')\n"
        "def create(item: dict):\n"
        "    return item\n",
    )
    assert flagged(run(tmp_path)) == []


def test_express_middleware_in_the_route_call_protects_it(tmp_path: Path) -> None:
    write(
        tmp_path,
        "src/routes.js",
        "const express = require('express');\n"
        "const { requireAuth } = require('./auth');\n"
        "const router = express.Router();\n"
        "router.post('/a', requireAuth, (req, res) => res.send(1));\n"
        "router.post('/b', (req, res) => res.send(2));\n"
        "module.exports = router;\n",
    )
    assert flagged(run(tmp_path)) == [("src/routes.js", 5)]


def test_nest_controller_guard_protects_its_methods(tmp_path: Path) -> None:
    write(
        tmp_path,
        "src/a.controller.ts",
        "import { Controller, Post, UseGuards } from '@nestjs/common';\n"
        "@UseGuards(JwtAuthGuard)\n"
        "@Controller('a')\n"
        "export class A {\n"
        "  @Post()\n"
        "  create() { return 1; }\n"
        "}\n",
    )
    assert flagged(run(tmp_path)) == []


def test_unit_without_any_marker_is_not_judged(tmp_path: Path) -> None:
    write(
        tmp_path,
        "app/items.py",
        "from fastapi import APIRouter\nrouter = APIRouter()\n"
        "@router.post('/items')\ndef create(item: dict):\n    return item\n",
    )
    h = run(tmp_path)
    assert flagged(h) == []
    assert any("no auth marker" in n for n in h.notes)


def test_team_markers_from_design_yaml_count(tmp_path: Path) -> None:
    write(
        tmp_path,
        "app/items.py",
        "from fastapi import APIRouter\nfrom app.s import require_session\n"
        "router = APIRouter()\n"
        "@router.post('/items')\n@require_session\ndef create(item: dict):\n    return item\n"
        "@router.post('/other')\ndef other(item: dict):\n    return item\n",
    )
    assert flagged(run(tmp_path, ("require_session",))) == [("app/items.py", 8)]
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_auth.py -q`
Expected: the first test FAILs (`[] != [("app/items.py", 4)]`); the "not judged" test FAILs on the note.

- [ ] **Step 3: Implement `svarupa/health/auth.py`**

```python
"""Write endpoints with no auth marker on the handler, its router or the file.

A marker is a known decorator, dependency, guard or middleware name (each
pack lists its own; teams add theirs in design.yaml). A route counts as
protected when a marker appears in its own lines (the route declaration and
handler) or in the file's top-level code (a router built with a dependency,
a guard on the controller class), never in an import line. A unit with no
marker anywhere is not judged: Svarupa cannot tell "no auth" from "auth it
does not know", and says so instead.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence

from svarupa.build import Graph
from svarupa.extract.base import RouteFact, module_of
from svarupa.extract.packs import PACKS
from svarupa.health.catalog import BY_ID
from svarupa.health.model import Violation

__all__ = ["auth_checks"]

_WRITES = frozenset({"POST", "PUT", "PATCH", "DELETE", "ALL"})
_PUBLIC = re.compile(
    r"(?i)(?:^|/)(?:login|logout|signin|sign-in|signup|sign-up|register|token|refresh|oauth"
    r"|callback|webhooks?|health|healthz|password-recovery|reset-password|forgot-password"
    r"|verify-email)(?:/|$|-|\{)"
)
_IMPORT = re.compile(
    r"^[ \t]*from[ \t]+[\w.]+[ \t]+import[ \t]*\([^)]*\)"
    r"|^[ \t]*import[ \t]*(?:type[ \t]+)?\{[^}]*\}[^\n]*"
    r"|^[ \t]*import[ \t]*\([^)]*\)"
    r"|^[ \t]*(?:from[ \t]+[\w.]+[ \t]+)?import[ \t][^\n]*"
    r"|^[^\n]*\brequire\([^)]*\)[^\n]*",
    re.MULTILINE,
)


def _defaults() -> frozenset[str]:
    return frozenset(m for p in PACKS if p.risks is not None for m in p.risks.auth_markers)


def _blank_imports(text: str) -> list[str]:
    """The file's lines with every import statement blanked, newlines kept."""
    return _IMPORT.sub(lambda m: "\n" * m.group(0).count("\n"), text).split("\n")


def _unit_of(path: str, units: Sequence[str]) -> str:
    best = ""
    for u in units:
        if u and (path == u or path.startswith(u + "/")) and len(u) > len(best):
            best = u
    return best


def _window(graph: Graph, r: RouteFact) -> tuple[int, int]:
    start, end = r.evidence.start_line, r.evidence.end_line
    node = graph.nodes.get(f"{r.file}#{r.handler}") if r.framework != "express" else None
    if node is not None and node.evidence:
        start = min(start, node.evidence[0].start_line)
        end = max(end, node.evidence[0].end_line)
    return start, end


def auth_checks(
    graph: Graph,
    texts: Mapping[str, str],
    markers: Iterable[str],
    units: Sequence[str] = ("",),
) -> tuple[list[Violation], list[str]]:
    names = sorted(_defaults() | set(markers), key=len, reverse=True)
    rx = re.compile(r"(?<![\w$])(?:" + "|".join(map(re.escape, names)) + r")(?![\w$])")
    lines = {path: _blank_imports(text) for path, text in texts.items()}
    spans: dict[str, list[tuple[int, int]]] = {}
    for fm in graph.functions:
        spans.setdefault(fm.evidence.file, []).append(
            (fm.evidence.start_line, fm.evidence.end_line)
        )
    routes = [r for r in graph.routes if r.file in texts]
    windows = {r: _window(graph, r) for r in routes}
    by_file: dict[str, list[tuple[int, int]]] = {}
    for r, w in windows.items():
        by_file.setdefault(r.file, []).append(w)

    def top_level(path: str) -> str:
        covered = spans.get(path, []) + by_file.get(path, [])
        return "\n".join(
            text
            for n, text in enumerate(lines[path], start=1)
            if not any(a <= n <= b for a, b in covered)
        )

    check = BY_ID["endpoint-without-auth"]
    out: list[Violation] = []
    notes: list[str] = []
    for unit in sorted({_unit_of(r.file, units) for r in routes}):
        unit_routes = [r for r in routes if _unit_of(r.file, units) == unit]
        files = [p for p in texts if _unit_of(p, units) == unit]
        if not any(rx.search("\n".join(lines[p])) for p in files):
            notes.append(
                f"{unit or '.'}: no auth marker found, so endpoint-without-auth was not "
                "checked; add your markers to design.yaml under auth_markers"
            )
            continue
        for r in sorted(unit_routes):
            if r.method not in _WRITES or _PUBLIC.search(r.path):
                continue
            a, b = windows[r]
            own = "\n".join(lines[r.file][a - 1 : b])
            if rx.search(own) or rx.search(top_level(r.file)):
                continue
            node_id = f"{r.file}#{r.handler}"
            out.append(
                Violation(
                    check="endpoint-without-auth",
                    message=(
                        f"{r.method} {r.path} changes data but has no auth marker on its "
                        "handler, router or middleware (CWE-306)"
                    ),
                    evidence=(r.evidence,),
                    module=module_of(r.file),
                    value=1,
                    minutes=check.minutes,
                    symbol=node_id if node_id in graph.nodes else None,
                )
            )
    return out, notes
```

In `assess`:

```python
    unit_ids = [u.unit.id for u in design.units] if design is not None else [""]
    auth, notes = auth_checks(graph, texts, auth_markers, unit_ids or [""])
```

add `*auth` to `found` and `notes=tuple(notes)` to the final `replace`.

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/test_auth.py -q`
Expected: 6 passed. If the NestJS or Express route is not extracted in the fixture (no `package.json`), add a minimal `package.json` with the framework dependency to the fixture and ledger it.

- [ ] **Step 5: Full suite, lint, types, commit**

```bash
git add svarupa/health tests/test_auth.py
git commit -m "Flag write endpoints with no auth marker"
```

---

### Task 9: Docs, benchmark facts, mutations

**Files:**
- Modify: `docs/health.md`, `docs/design.md` (design.yaml keys), `benchmark/expected/*.toml`, `benchmark/scores.json` (via `accept`), `scripts/mutate_packs.py`

- [ ] **Step 1: Docs**

In `docs/health.md` add a "Security" section: the ten checks as a table (id, fires on, severity, minutes, source), the rating rule, the secret policy (never the value; fingerprint; placeholders; files scanned: every non-ignored file except generated, vendored, lockfiles and `.svarupa/`), the injection boundary (same function, no taint), the auth rule (write routes, public paths skipped, markers, unit with no marker not judged, routes known for FastAPI, Flask, Express and NestJS only), and the `at` exception form. In `docs/design.md` document `auth_markers` and `at`.

- [ ] **Step 2: Draft expected security facts from source, before running**

For each pinned checkout in `~/.cache/svarupa-bench/<name>`, search the source by hand:

```bash
cd ~/.cache/svarupa-bench/<name>
grep -rnE "execute\(|\.query\(|Query\(|verify=False|InsecureSkipVerify|DEBUG = True|md5|sha1|pickle|yaml\.load|cors|CORS|CrossOrigin|AKIA|password|SECRET" --include=*.{py,js,ts,go,java,yaml,yml,env,properties} . | grep -v node_modules | head -100
```

Read each hit. For a real violation by the spec's rules, add `[[must]] violation = "<check> <path>:<line>"` with a `why` starting `path:line`. For a safe twin (a parameterized query, `yaml.safe_load`, a write route with a marker), add a `[[must_not]]` probe with the reason. Aim for at least two repos with expected facts per check where the corpus has them (for example Django settings `DEBUG = True` and `SECRET_KEY` literals, FastAPI routes guarded by `CurrentUser`, express-boilerplate routes with `auth(...)`). Write these before running Svarupa on the repo (README rule 1).

- [ ] **Step 3: Run the benchmark and record**

Run: `uv run python scripts/benchmark.py validate` then `uv run python scripts/benchmark.py run <name>` for each changed repo.
Expected: citations valid; misses are findings. Fix a miss only with a general rule change plus a test (never a repo-specific name). Then `uv run python scripts/benchmark.py accept` and `uv run python scripts/benchmark.py check`.

- [ ] **Step 4: Mutations**

Add to `MUTATIONS` in `scripts/mutate_packs.py` (and `tests/test_risks.py`, `tests/test_security.py`, `tests/test_secrets.py`, `tests/test_auth.py` to `SUITE`):

```python
    (
        "a reassigned variable stays built",
        "svarupa/extract/packs/walker.py",
        "            elif not augment:\n                built.discard(name)\n",
        "",
    ),
    (
        "interpolated strings are not built",
        "svarupa/extract/packs/walker.py",
        "            return any(c.type == inner for c in node.children) or any(",
        "            return False or any(",
    ),
    (
        "a sink's required argument is ignored",
        "svarupa/extract/packs/walker.py",
        "                    if sink.requires is not None and not sink.requires.search(self.text(args)):\n                        continue\n",
        "",
    ),
    (
        "placeholders count as secrets",
        "svarupa/health/secrets.py",
        "            if _PLACEHOLDER.search(value) or _entropy(value) < _MIN_ENTROPY:\n",
        "            if False:\n",
    ),
    (
        "import lines count as auth",
        "svarupa/health/auth.py",
        "    return _IMPORT.sub(lambda m: \"\\n\" * m.group(0).count(\"\\n\"), text).split(\"\\n\")\n",
        "    return text.split(\"\\n\")\n",
    ),
    (
        "location exceptions are ignored",
        "svarupa/health/__init__.py",
        "    return any(e.at and e.rule == v.check and e.at in places for e in exceptions)\n",
        "    return False\n",
    ),
```

Match each `old` string to the committed code exactly (formatting may have moved lines; update the strings, not the code).

Run: `uv run python scripts/mutate_packs.py`
Expected: every mutation caught.

- [ ] **Step 5: Full suite, lint, types, commit**

```bash
git add docs benchmark scripts
git commit -m "Benchmark and document the security checks"
```
