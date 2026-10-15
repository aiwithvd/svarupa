# Performance and Reliability Checks (1c-3b) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Grade the performance and reliability areas with seven checks Svarupa can prove from code: shared datastore, query in a loop, blocking call in async code, hub module on the request path, no timeout, swallowed error and no retry.

**Architecture:** The tree-sitter walker already records `RiskFact`s per function (1c-3a). It gains three new fact kinds (`in-loop`, `blocking-async`, `empty-catch`), driven by new data fields on each pack's `RiskSpec`. Timeouts, Go's empty `if err != nil {}` and turned-off retries are plain `Unsafe` rules, the same mechanism as `verify=False`. Two checks read the graph instead of the walker: `shared-datastore` (compose services) and `hub-on-request-path` (module dependencies plus routes).

**Tech Stack:** Python 3.10+, tree-sitter packs (Python, TypeScript/JavaScript, Go, Java), PyYAML compose nodes, networkx, pytest, ruff, pyright.

**Spec:** `docs/superpowers/specs/2026-10-10-security-scaling-1c3-design.md` (section "1c-3b: performance and reliability")

## Global Constraints

- All new checks start `experimental`; a check becomes `stable` only through the benchmark rule (found on at least 2 repos, no probe firing; `tests/test_benchmark.py::test_check_maturity_follows_the_benchmark`).
- Performance and reliability are rated by the worst severity (minor B, major C, critical D, blocker E); the SQALE debt ratio stays maintainability only.
- Severities and minutes, from the spec: `shared-datastore` major 240, `query-in-loop` major 20, `blocking-in-async` major 15, `hub-on-request-path` major 60, `no-timeout` major 10, `swallowed-error` major 10, `no-retry` minor 15.
- A fact's `detail` is a callee or a rule label, never a literal from the source (`_plain(callee)`).
- Each function is its own scope; no tracking across functions.
- Deep synchronous chains between services are out of scope.
- Rules are data in each pack (`RiskSpec`); the walker never names a language.
- Writing: no em dashes, simple English. Commits: SSH-signed (repo-local config), imperative subject under 72 characters, no AI attribution or co-author lines.

## Review Focus

1. A query in the *iterable* of a loop (`for u in User.objects.all():`) runs once; only the loop body counts. Pinned in Task 1 (`once`) and Task 2 (Java and JS `for` over a repository call).
2. A function defined inside an async function is not async, and a function defined after a loop is not in it. Pinned in Task 1 (`test_nested_functions_have_their_own_loop_and_async_state`).
3. An `except` that logs or re-raises is not swallowed; one with only `pass` or a comment is. Pinned in Task 1 and Task 2.
4. Two compose services built from the same codebase (an app and its migration job) are one owner of the datastore, not two. Pinned in Task 5 (`test_one_codebase_with_two_services_is_not_shared`).
5. Existing safe-twin fixtures must stay clean once the timeout rules land (`requests.get(x, verify=True)` has no timeout). Task 3 adds `timeout=5` to the fixtures whose subject is not the timeout, and the full suite run in each task catches any other fixture.

---

## File Structure

- Modify `svarupa/extract/packs/model.py`: `RiskSpec` gains `loops`, `loop_callee`, `datastore_calls`, `async_marker`, `blocking`, `catch_types`, `quiet`.
- Modify `svarupa/extract/base.py`: `RiskFact.kind` docs list the new kinds.
- Modify `svarupa/extract/packs/walker.py`: `scan_risks` tracks loop bodies and async scopes; new `_per_item`, `_runtime_node`, `_quiet`.
- Modify `svarupa/extract/packs/python.py`, `typescript.py`, `go.py`, `java.py`: the data.
- Rename `svarupa/health/security.py` to `svarupa/health/risks.py` (`security_checks` becomes `risk_checks`): it now maps every risk fact kind to a check.
- Create `svarupa/health/performance.py`: `shared_datastores(graph)`, `hub_on_request_path(graph)`.
- Modify `svarupa/health/catalog.py`, `svarupa/health/__init__.py`.
- Modify `svarupa/extract/compose.py`: service attr `connects` from connection settings and `links`.
- Tests: `tests/test_risks.py` (walker facts), new `tests/test_perf_reliability.py` (checks), `tests/test_compose.py`, `tests/test_health.py`, `tests/test_health_outputs.py`.
- `scripts/mutate_packs.py`, `docs/health.md`, `benchmark/expected/*.toml`, `benchmark/scores.json`.

---

### Task 1: Loop, async and catch facts in the walker (Python data)

**Files:**
- Modify: `svarupa/extract/packs/model.py` (class `RiskSpec`)
- Modify: `svarupa/extract/base.py` (class `RiskFact`, the `kind` comment)
- Modify: `svarupa/extract/packs/walker.py` (`scan_risks`, new helpers after `_risk_node`)
- Modify: `svarupa/extract/packs/python.py` (`RISKS`)
- Test: `tests/test_risks.py`

**Interfaces:**
- Consumes: `RiskSpec`, `RiskFact(kind, tag, detail, evidence, function)`, `Ctx._function_name`, `_plain` (all exist).
- Produces: `RiskSpec.loops: tuple[tuple[str, str], ...]`, `RiskSpec.loop_callee: re.Pattern[str] | None`, `RiskSpec.datastore_calls: re.Pattern[str] | None`, `RiskSpec.async_marker: str | None`, `RiskSpec.blocking: re.Pattern[str] | None`, `RiskSpec.catch_types: frozenset[str]`, `RiskSpec.quiet: frozenset[str]`. New facts: `RiskFact("in-loop", "query", callee, ...)`, `RiskFact("blocking-async", "io", callee, ...)`, `RiskFact("empty-catch", "catch", "empty except" | "empty catch", ...)`.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_risks.py`)

```python
PY_RUNTIME = """def load(ids, User):
    for i in ids:
        User.objects.get(id=i)
    return [db.session.get(User, i) for i in ids]


def once(cur, ids):
    for row in cur.execute("SELECT 1"):
        print(row)


async def handler(url):
    requests.get(url, timeout=5)
    time.sleep(1)


def sync(url):
    requests.get(url, timeout=5)


def quiet():
    try:
        run()
    except ValueError:
        pass
    try:
        run()
    except KeyError:  # nothing to do
        pass
    try:
        run()
    except OSError:
        log.warning("failed")
"""


def test_python_runtime_facts_are_recorded() -> None:
    assert risks("python", "m.py", PY_RUNTIME) == [
        ("in-loop", "query", 3, "load"),
        ("in-loop", "query", 4, "load"),
        ("blocking-async", "io", 13, "handler"),
        ("blocking-async", "io", 14, "handler"),
        ("empty-catch", "catch", 24, "quiet"),
        ("empty-catch", "catch", 28, "quiet"),
    ]


def test_nested_functions_have_their_own_loop_and_async_state() -> None:
    src = (
        "async def outer(ids):\n"
        "    def helper():\n"
        "        time.sleep(1)\n"
        "    for i in ids:\n"
        "        print(i)\n"
        "    def later():\n"
        "        session.query(X)\n"
        "    return helper, later\n"
    )
    assert risks("python", "m.py", src) == []


def test_runtime_detail_is_the_callee() -> None:
    f = extractor("python").parse("m.py", b"def f(ids):\n    for i in ids:\n        User.objects.get(id=i)\n")
    assert [r.detail for r in f.risks] == ["User.objects.get"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_risks.py -q -k "runtime or nested_functions"`
Expected: FAIL, `test_python_runtime_facts_are_recorded` gets `[]`; `test_runtime_detail_is_the_callee` gets `[]`. (`test_nested_functions...` passes already; it pins the scope rule once the feature exists.)

- [ ] **Step 3: Add the fields to `RiskSpec`** (in `svarupa/extract/packs/model.py`, after `auth_markers`)

```python
    # (loop node type, body field): only the body runs once per item, never
    # the iterable (`for u in User.objects.all():` queries once).
    loops: tuple[tuple[str, str], ...] = ()
    # A function passed to one of these runs once per item (`xs.forEach(f)`).
    loop_callee: re.Pattern[str] | None = None
    datastore_calls: re.Pattern[str] | None = None  # a query on a database client
    async_marker: str | None = None  # keyword token that makes a function async
    blocking: re.Pattern[str] | None = None  # sync I/O that stalls an event loop
    catch_types: frozenset[str] = frozenset()  # except/catch clauses
    quiet: frozenset[str] = frozenset()  # statements that do nothing: pass, comments
```

In `svarupa/extract/base.py`, change the `kind` line of `RiskFact` to:

```python
    kind: str  # "sink-built" | "unsafe" | "in-loop" | "blocking-async" | "empty-catch"
```

and its `tag` line to:

```python
    tag: str  # sink tag (sql/shell/code), unsafe check id, or query/io/catch
```

- [ ] **Step 4: Track loops and async scopes in `scan_risks`** (`svarupa/extract/packs/walker.py`)

Replace the body of `scan_risks` from `out: list[RiskFact] = []` down to (not including) the dedupe comment with:

```python
        out: list[RiskFact] = []
        loop_bodies = dict(spec.loops)
        # (scope node, function name, whether it runs once per loop item)
        scopes: list[tuple[TSNode, str, bool]] = [(root, "", False)]
        while scopes:
            scope, name, looped = scopes.pop()
            built: set[str] = set()
            is_async = spec.async_marker is not None and any(
                not c.is_named and c.type == spec.async_marker for c in scope.children
            )
            work = [(c, looped) for c in reversed(scope.children)]
            while work:
                node, in_loop = work.pop()
                if node.is_named and node.type in fn_types:
                    per_item = in_loop or self._per_item(node, spec)
                    scopes.append((node, self._function_name(node), per_item))
                    continue
                self._risk_node(node, spec, by_type, built, name, out)
                self._runtime_node(node, spec, in_loop, is_async, name, out)
                field = loop_bodies.get(node.type)
                body = node.child_by_field_name(field) if field else None
                work.extend(
                    (c, in_loop or (body is not None and c.id == body.id))
                    for c in reversed(node.children)
                )
```

Update the docstring's first line to: `"""Risk facts per function: built strings into sinks, unsafe settings, queries in loops, blocking calls in async code and empty catch blocks.`

- [ ] **Step 5: Add the helpers** (after `_risk_node` in `walker.py`)

```python
    def _per_item(self, fn: TSNode, spec: RiskSpec) -> bool:
        """Whether this function is passed to a per-item call (`xs.map(f)`)."""
        args = fn.parent
        call = args.parent if args is not None else None
        if spec.loop_callee is None or call is None or call.type not in spec.call_types:
            return False
        got = call.child_by_field_name("arguments")
        if got is None or args is None or got.id != args.id:
            return False
        callee = spec.callee(self, call)
        return callee is not None and spec.loop_callee.search(callee) is not None

    def _runtime_node(
        self,
        node: TSNode,
        spec: RiskSpec,
        in_loop: bool,
        is_async: bool,
        scope: str,
        out: list[RiskFact],
    ) -> None:
        """Queries in a loop body, blocking calls in async code, empty catches."""
        t = node.type
        if t in spec.catch_types and self._quiet(node, spec):
            keyword = node.children[0].type if node.children else "catch"
            out.append(
                RiskFact(
                    "empty-catch", "catch", f"empty {keyword}", self.node_evidence(node), scope
                )
            )
            return
        if t not in spec.call_types or not (in_loop or is_async):
            return
        callee = spec.callee(self, node)
        if callee is None:
            return
        if in_loop and spec.datastore_calls is not None and spec.datastore_calls.search(callee):
            out.append(
                RiskFact("in-loop", "query", _plain(callee), self.node_evidence(node), scope)
            )
        if is_async and spec.blocking is not None and spec.blocking.search(callee):
            out.append(
                RiskFact("blocking-async", "io", _plain(callee), self.node_evidence(node), scope)
            )

    def _quiet(self, clause: TSNode, spec: RiskSpec) -> bool:
        """An except/catch whose body does nothing: empty, `pass` or comments."""
        body = clause.child_by_field_name("body")
        if body is None:  # Python: the block is the last named child
            kids = clause.named_children
            body = kids[-1] if kids else None
        return body is not None and all(c.type in spec.quiet for c in body.named_children)
```

- [ ] **Step 6: Add the Python data** (`svarupa/extract/packs/python.py`, inside `RISKS = RiskSpec(`, after `auth_markers=...`)

```python
    loops=(
        ("for_statement", "body"),
        ("while_statement", "body"),
        ("list_comprehension", "body"),
        ("set_comprehension", "body"),
        ("dictionary_comprehension", "body"),
        ("generator_expression", "body"),
    ),
    datastore_calls=re.compile(
        r"(?:^|\.)objects\.\w+$"
        r"|\.query\.(?:get|get_or_404|filter|filter_by|first|all|one|count)$"
        r"|(?:^|\.)(?:session|db|cursor|cur|conn|connection|engine|collection)\.(?:query|execute|executemany|get|scalar|scalars|scalar_one|first|find|find_one|insert_one|update_one|delete_one|aggregate|fetch|fetchrow|fetchval)$"
    ),
    async_marker="async",
    blocking=re.compile(
        r"^(?:requests\.(?:get|post|put|patch|delete|head|options|request)|time\.sleep"
        r"|urllib\.request\.urlopen|urlopen|subprocess\.(?:run|call|check_call|check_output)"
        r"|os\.system|psycopg2\.connect|sqlite3\.connect)$"
    ),
    catch_types=frozenset({"except_clause"}),
    quiet=frozenset({"pass_statement", "comment"}),
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `uv run pytest tests/test_risks.py -q`
Expected: PASS (all, including the 1c-3a tests).

- [ ] **Step 8: Run the full suite and the golden facts**

Run: `uv run pytest -q -x 2>&1 | tail -5`
Expected: PASS. If `tests/test_golden_facts.py` or `tests/test_golden_repos.py` fail only because new `in-loop`/`blocking-async`/`empty-catch` entries appear under `"risks"`, read each new entry against its fixture source; if every one is true, regenerate with the project's golden update command (see the test module docstring) and ledger a ruling. Any other diff is a bug.

- [ ] **Step 9: Lint, type check, commit**

```bash
uv run ruff format svarupa tests && uv run ruff check svarupa tests && uv run pyright svarupa
git add svarupa/extract tests/test_risks.py
git commit -m "Record queries in loops, blocking async calls and empty catches"
```

---

### Task 2: Loop, async and catch data for TypeScript, JavaScript, Go and Java

**Files:**
- Modify: `svarupa/extract/packs/typescript.py` (`RISKS`; JavaScript reuses it)
- Modify: `svarupa/extract/packs/go.py` (`RISKS`)
- Modify: `svarupa/extract/packs/java.py` (`RISKS`)
- Test: `tests/test_risks.py`

**Interfaces:**
- Consumes: the `RiskSpec` fields from Task 1.
- Produces: data only.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_risks.py`)

```python
JS_RUNTIME = """async function load(ids) {
  for (const id of ids) {
    await prisma.user.findUnique({ where: { id } });
  }
  await Promise.all(ids.map((id) => User.findById(id)));
  for (const u of await this.userRepository.find()) {
    console.log(u);
  }
  const data = fs.readFileSync(path);
  try {
    run();
  } catch (e) {
    // ignore
  }
  try {
    run();
  } catch (e) {
    log(e);
  }
}
function sync() {
  return fs.readFileSync(path);
}
"""


def test_javascript_runtime_facts_are_recorded() -> None:
    assert risks("javascript", "m.js", JS_RUNTIME) == [
        ("in-loop", "query", 3, "load"),
        ("in-loop", "query", 5, "(anonymous)"),
        ("blocking-async", "io", 9, "load"),
        ("empty-catch", "catch", 12, "load"),
    ]


JAVA_RUNTIME = """class S {
  void load(List<Long> ids) {
    for (Long id : ids) {
      userRepository.findById(id);
    }
    ids.forEach(id -> this.orderRepository.findByUserId(id));
    for (User u : userRepository.findAll()) {
      log(u);
    }
    try {
      run();
    } catch (Exception e) {
      // ignore
    }
    try {
      run();
    } catch (Exception e) {
      throw new RuntimeException(e);
    }
  }
}
"""


def test_java_runtime_facts_are_recorded() -> None:
    assert risks("java", "S.java", JAVA_RUNTIME) == [
        ("in-loop", "query", 4, "load"),
        ("in-loop", "query", 6, "(anonymous)"),
        ("empty-catch", "catch", 12, "load"),
    ]


GO_RUNTIME = """package s

func load(db *sql.DB, ids []int) {
\tfor _, id := range ids {
\t\tdb.QueryRow("SELECT 1 WHERE id = $1", id)
\t}
\trows, _ := db.Query("SELECT 1")
\tfor rows.Next() {
\t\tfmt.Println(rows)
\t}
}
"""


def test_go_runtime_facts_are_recorded() -> None:
    assert risks("go", "s.go", GO_RUNTIME) == [("in-loop", "query", 5, "load")]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_risks.py -q -k "runtime_facts"`
Expected: FAIL for javascript, java and go (they record `[]`); python passes.

- [ ] **Step 3: TypeScript data** (inside `RISKS = RiskSpec(` in `typescript.py`, after `auth_markers=...`)

```python
    loops=(
        ("for_statement", "body"),
        ("for_in_statement", "body"),
        ("while_statement", "body"),
        ("do_statement", "body"),
    ),
    loop_callee=re.compile(r"\.(?:forEach|map|flatMap)$"),
    datastore_calls=re.compile(
        r"(?:^|\.)(?:prisma|db|tx|client)\.\w+\.(?:find\w*|create\w*|update\w*|upsert|delete\w*|count|aggregate|groupBy)$"
        r"|(?:^|\.)\w*(?:[Rr]epo|[Rr]epository|[Mm]odel)\.(?:find\w*|save|insert|update|delete|remove|count\w*|exists\w*|query|create)$"
        r"|^[A-Z]\w*\.(?:find|findOne|findById|findOneAndUpdate|updateOne|updateMany|deleteOne|deleteMany|countDocuments|aggregate|exists)$"
        r"|(?:^|\.)(?:pool|client|db|conn|connection|knex|sql|database|pg)\.(?:query|execute|one|many|any|oneOrNone|manyOrNone)$"
    ),
    async_marker="async",
    blocking=re.compile(
        r"^(?:(?:fs|child_process|cp|bcrypt|bcryptjs|crypto|zlib)\.\w+Sync"
        r"|readFileSync|writeFileSync|existsSync|readdirSync|statSync|mkdirSync"
        r"|execSync|execFileSync|spawnSync|hashSync|compareSync|pbkdf2Sync|scryptSync)$"
    ),
    catch_types=frozenset({"catch_clause"}),
    quiet=frozenset({"comment", "empty_statement"}),
```

- [ ] **Step 4: Go data** (inside `RISKS = RiskSpec(` in `go.py`, after `unsafe=(...)`)

```python
    loops=(("for_statement", "body"),),
    datastore_calls=re.compile(
        r"\.(?:Query|QueryContext|QueryRow|QueryRowContext|ExecContext)$"
        r"|(?i:(?:^|\.)\w*(?:db|tx|conn|pool|repo|store|gorm))\.(?:Exec|Get|Select|First|Find|Take|Create|Save|Delete|Updates?|Where)$"
    ),
```

Go has no `async`, no try/catch: its swallowed error is an `Unsafe` rule in Task 3.

- [ ] **Step 5: Java data** (inside `RISKS = RiskSpec(` in `java.py`, after `auth_markers=...`)

```python
    loops=(
        ("for_statement", "body"),
        ("enhanced_for_statement", "body"),
        ("while_statement", "body"),
        ("do_statement", "body"),
    ),
    loop_callee=re.compile(r"\.(?:forEach|map|flatMap)$"),
    datastore_calls=re.compile(
        r"(?:^|\.)\w*(?:[Rr]epository|[Rr]epo|[Dd]ao|[Mm]apper)\.(?:find\w*|get\w*|save\w*|select\w*|insert\w*|update\w*|delete\w*|count\w*|exists\w*)$"
        r"|(?i:(?:^|\.)\w*(?:jdbctemplate|template|entitymanager|em|session))\.(?:query\w*|find|get|createQuery|createNativeQuery|update|execute|persist|merge)$"
        r"|(?:^|\.)(?:executeQuery|executeUpdate)$"
    ),
    catch_types=frozenset({"catch_clause"}),
    quiet=frozenset({"line_comment", "block_comment"}),
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/test_risks.py -q`
Expected: PASS.

- [ ] **Step 7: Full suite, lint, commit**

```bash
uv run pytest -q -x 2>&1 | tail -3
uv run ruff format svarupa tests && uv run ruff check svarupa tests && uv run pyright svarupa
git add svarupa/extract/packs tests/test_risks.py
git commit -m "Add loop, async and catch data for TypeScript, Go and Java"
```

Expected: suite PASS (same golden rule as Task 1 Step 8).

---

### Task 3: Timeout, swallowed-error and retry rules

**Files:**
- Modify: `svarupa/extract/packs/python.py`, `typescript.py`, `go.py`, `java.py` (`unsafe=(...)` tuples)
- Test: `tests/test_risks.py` (new tests; `PY_TRUE` and `PY_SAFE` get `timeout=5`)

**Interfaces:**
- Consumes: `Unsafe(check, label, node_types, pattern)` (exists; `pattern` is a fullmatch on whitespace-collapsed node text).
- Produces: `RiskFact("unsafe", "no-timeout" | "swallowed-error" | "no-retry", label, ...)`. Task 4 maps them to checks by tag.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_risks.py`)

```python
PY_RELIABILITY = """import requests, pika, aio_pika
def f(url):
    requests.get(url)
    requests.post(url, json={}, timeout=5)
    urlopen(url)
    urlopen(url, timeout=3)
    pika.ConnectionParameters(host="mq")
    pika.ConnectionParameters(host="mq", connection_attempts=5)
    aio_pika.connect(url)
    aio_pika.connect_robust(url)
"""


def test_python_reliability_rules() -> None:
    assert tags("python", "m.py", PY_RELIABILITY) == [
        ("no-timeout", 3),
        ("no-timeout", 5),
        ("no-retry", 7),
        ("no-retry", 9),
    ]


JS_RELIABILITY = """async function f(url) {
  await axios.get(url);
  await axios.get(url, { timeout: 5000 });
  const api = axios.create({ baseURL: url });
  const ok = axios.create({ baseURL: url, timeout: 5000 });
  await fetch(url);
}
"""


def test_javascript_reliability_rules() -> None:
    assert tags("javascript", "m.js", JS_RELIABILITY) == [("no-timeout", 2), ("no-timeout", 4)]


GO_RELIABILITY = """package s

func f() {
\thttp.Get(u)
\tc := &http.Client{}
\tok := &http.Client{Timeout: 5 * time.Second}
\thttp.DefaultClient.Do(req)
\t_, err := run()
\tif err != nil {
\t}
\tif err != nil {
\t\treturn
\t}
\to := redis.Options{MaxRetries: -1}
}
"""


def test_go_reliability_rules() -> None:
    assert tags("go", "s.go", GO_RELIABILITY) == [
        ("no-timeout", 4),
        ("no-timeout", 5),
        ("no-timeout", 7),
        ("swallowed-error", 9),
        ("no-retry", 14),
    ]


JAVA_RELIABILITY = """class C {
  void f() {
    HttpClient a = HttpClient.newHttpClient();
    RestTemplate r = new RestTemplate();
    RestTemplate ok = new RestTemplate(factory);
    factory.setAutomaticRecoveryEnabled(false);
  }
}
"""


def test_java_reliability_rules() -> None:
    assert tags("java", "C.java", JAVA_RELIABILITY) == [
        ("no-timeout", 3),
        ("no-timeout", 4),
        ("no-retry", 6),
    ]
```

In `PY_TRUE` change `    requests.get(x, verify=False)` to `    requests.get(x, verify=False, timeout=5)`; in `PY_SAFE` change `    requests.get(x, verify=True)` to `    requests.get(x, verify=True, timeout=5)`. Those fixtures test TLS, not timeouts.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_risks.py -q -k reliability`
Expected: FAIL, each records `[]`.

- [ ] **Step 3: Python rules** (append inside `unsafe=(...)` in `python.py`)

```python
        Unsafe("no-timeout", "requests call without timeout", _CALL, _p(r"requests\.(?:get|post|put|patch|delete|head|options|request)\((?!.*\btimeout\s*=).*")),
        Unsafe("no-timeout", "urlopen without timeout", _CALL, _p(r"(?:urllib\.request\.)?urlopen\((?!.*\btimeout\s*=).*")),
        Unsafe("no-retry", "pika connection tries once", _CALL, _p(r"pika\.(?:ConnectionParameters|URLParameters)\((?!.*connection_attempts).*")),
        Unsafe("no-retry", "aio_pika.connect does not reconnect (use connect_robust)", _CALL, _p(r"aio_pika\.connect\(.*")),
```

- [ ] **Step 4: TypeScript rules** (append inside `unsafe=(...)` in `typescript.py`)

```python
        Unsafe("no-timeout", "axios call without timeout", _CALL, _p(r"axios(?:\.(?:get|post|put|patch|delete|head|options|request))?\((?!.*\btimeout\s*:).*")),
        Unsafe("no-timeout", "axios instance without timeout", _CALL, _p(r"axios\.create\((?!.*\btimeout\s*:).*")),
```

`fetch` is left out on purpose: Node's fetch (undici) has 300-second header and body timeouts by default, and a browser user can cancel a request.

- [ ] **Step 5: Go rules** (append inside `unsafe=(...)` in `go.py`)

```python
        Unsafe("no-timeout", "http default client has no timeout", _CALL, _p(r"http\.(?:Get|Post|Head|PostForm)\(.*|http\.DefaultClient\.\w+\(.*")),
        Unsafe("no-timeout", "http.Client without Timeout", frozenset({"composite_literal"}), _p(r"http\.Client\{(?!.*\bTimeout\s*:).*\}")),
        Unsafe("swallowed-error", "empty if err != nil block", frozenset({"if_statement"}), _p(r"if\s+(?:[^{]*;\s*)?\w*[eE]rr\w*\s*!=\s*nil\s*\{\s*(?://[^}]*)?\}")),
        Unsafe("no-retry", "Redis retries turned off", _KEYED, _p(r"MaxRetries\s*:\s*-1")),
```

- [ ] **Step 6: Java rules** (append inside `unsafe=(...)` in `java.py`)

```python
        Unsafe("no-timeout", "HttpClient without connect timeout", _INVOKE, _p(r"HttpClient\.newHttpClient\(\s*\)")),
        Unsafe("no-timeout", "RestTemplate without timeouts", _NEW, _p(r"new RestTemplate\(\s*\)")),
        Unsafe("no-retry", "RabbitMQ automatic recovery turned off", _INVOKE, _p(r".*\.setAutomaticRecoveryEnabled\(\s*false\s*\)")),
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `uv run pytest tests/test_risks.py -q`
Expected: PASS.

- [ ] **Step 8: Full suite, lint, commit**

```bash
uv run pytest -q -x 2>&1 | tail -3
uv run ruff format svarupa tests && uv run ruff check svarupa tests && uv run pyright svarupa
git add svarupa/extract/packs tests/test_risks.py
git commit -m "Find calls without timeouts, swallowed Go errors and retries off"
```

Expected: suite PASS. A fixture in another test file that now records `no-timeout` gets `timeout=` added only if its subject is not timeouts; ledger each such edit as a ruling.

---

### Task 4: Catalog entries and checks from the new facts

**Files:**
- Rename: `svarupa/health/security.py` to `svarupa/health/risks.py` (`git mv`)
- Modify: `svarupa/health/catalog.py`, `svarupa/health/__init__.py`
- Modify: `tests/test_health.py::test_only_assessed_areas_have_a_grade`, `tests/test_health_outputs.py::test_cli_writes_health_everywhere`
- Create: `tests/test_perf_reliability.py`

**Interfaces:**
- Consumes: risk facts from Tasks 1 to 3.
- Produces: `risk_checks(graph: Graph) -> list[Violation]` in `svarupa.health.risks`; catalog ids `shared-datastore`, `query-in-loop`, `blocking-in-async`, `hub-on-request-path`, `no-timeout`, `swallowed-error`, `no-retry`. Tasks 5 and 6 read `BY_ID["shared-datastore"]` and `BY_ID["hub-on-request-path"]`.

- [ ] **Step 1: Write the failing tests** (create `tests/test_perf_reliability.py`)

```python
"""Performance and reliability checks."""

from __future__ import annotations

from pathlib import Path

from svarupa.health import BY_ID
from tests.test_health import write
from tests.test_security import run


def test_perf_and_reliability_checks_are_in_the_catalog() -> None:
    for cid, area, sev, minutes in (
        ("shared-datastore", "performance", "major", 240),
        ("query-in-loop", "performance", "major", 20),
        ("blocking-in-async", "performance", "major", 15),
        ("hub-on-request-path", "performance", "major", 60),
        ("no-timeout", "reliability", "major", 10),
        ("swallowed-error", "reliability", "major", 10),
        ("no-retry", "reliability", "minor", 15),
    ):
        check = BY_ID[cid]
        assert (check.area, check.severity, check.minutes, check.maturity) == (
            area,
            sev,
            minutes,
            "experimental",
        )


def test_runtime_facts_become_violations(tmp_path: Path) -> None:
    write(
        tmp_path,
        "app/m.py",
        "import requests\n"
        "def load(ids):\n"
        "    for i in ids:\n"
        "        User.objects.get(id=i)\n"
        "async def handler(url):\n"
        "    requests.get(url)\n"
        "def quiet():\n"
        "    try:\n"
        "        run()\n"
        "    except ValueError:\n"
        "        pass\n",
    )
    h = run(tmp_path)
    found = sorted((v.check, v.evidence[0].start_line) for v in h.violations)
    assert found == [
        ("blocking-in-async", 6),
        ("no-timeout", 6),
        ("query-in-loop", 4),
        ("swallowed-error", 10),
    ]
    by = {v.check: v.message for v in h.violations}
    assert by["query-in-loop"].startswith("User.objects.get in load runs a query on every pass")
    assert "(CWE-390)" in by["swallowed-error"]
    assert "(CWE-1088)" in by["no-timeout"]
    assert (h.rating("performance"), h.rating("reliability")) == ("C", "C")


def test_clean_code_rates_performance_and_reliability_a(tmp_path: Path) -> None:
    write(tmp_path, "app/m.py", "def f():\n    return 1\n")
    h = run(tmp_path)
    assert (h.rating("performance"), h.rating("reliability")) == ("A", "A")
```

In `tests/test_health.py::test_only_assessed_areas_have_a_grade`, rename it to `test_every_area_has_a_grade` and change the assertion to:

```python
    assert [h.rating(a) for a in ("reliability", "security", "performance")] == ["A", "A", "A"]
```

In `tests/test_health_outputs.py::test_cli_writes_health_everywhere`, replace `assert gj["health"]["ratings"]["reliability"] is None` with `assert gj["health"]["ratings"]["reliability"] == "A"`, and replace `assert "not assessed yet" in report` with `assert "| performance | A |" in report`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_perf_reliability.py tests/test_health.py tests/test_health_outputs.py -q`
Expected: FAIL with `KeyError: 'shared-datastore'` and ratings `None`.

- [ ] **Step 3: Add the catalog rows** (`svarupa/health/catalog.py`, append to `CATALOG` after `endpoint-without-auth`)

```python
    Check("shared-datastore", "Datastore shared by services", 1, "Newman, Building Microservices: database per service", "performance", "major", 240),
    Check("query-in-loop", "Query in a loop (N+1)", 0, "N+1 query anti-pattern; Fowler, PoEAA", "performance", "major", 20),
    Check("blocking-in-async", "Blocking call in async code", 0, "Python asyncio docs: running blocking code; Node.js: Don't block the event loop", "performance", "major", 15),
    Check("hub-on-request-path", "Hub module on every request path", 2, "Arcan hub-like dependency on the request path", "performance", "major", 60),
    Check("no-timeout", "Outbound call without a timeout", 0, "CWE-1088; Nygard, Release It!: Timeouts", "reliability", "major", 10),
    Check("swallowed-error", "Error caught and ignored", 0, "CWE-390; Bandit B110", "reliability", "major", 10),
    Check("no-retry", "Connection without retry", 0, "Nygard, Release It!: retries", "reliability", "minor", 15),
```

- [ ] **Step 4: Rename and extend the fact checks**

```bash
git mv svarupa/health/security.py svarupa/health/risks.py
```

Write `svarupa/health/risks.py`:

```python
"""Checks from the risk facts the walker recorded: security, performance
and reliability."""

from __future__ import annotations

from svarupa.build import Graph
from svarupa.extract.base import module_of
from svarupa.health.catalog import BY_ID
from svarupa.health.model import Violation

__all__ = ["risk_checks"]

_SINKS = {
    "sql": ("sql-injection", "SQL", "pass values as query parameters"),
    "shell": (
        "command-injection",
        "a shell command",
        "pass arguments as a list, without a shell",
    ),
    "code": ("code-injection", "code", "do not run code built from data"),
}

# Fact kind -> (check, what happens and what to do).
_RUNTIME = {
    "in-loop": (
        "query-in-loop",
        "runs a query on every pass of a loop; load what the loop needs in one query before it",
    ),
    "blocking-async": (
        "blocking-in-async",
        "blocks the event loop inside an async function; use an async client or run it in a worker thread",
    ),
    "empty-catch": (
        "swallowed-error",
        "hides the error; log it, handle it or let it raise",
    ),
}


def _ref(check_id: str) -> str:
    """The first source of a check: its CWE, or the book or rule it comes from."""
    return BY_ID[check_id].source.split(";", 1)[0]


def risk_checks(graph: Graph) -> list[Violation]:
    out: list[Violation] = []
    for r in graph.risks:
        where = f" in {r.function}" if r.function else ""
        if r.kind == "sink-built" and r.tag in _SINKS:
            check_id, what, fix = _SINKS[r.tag]
            message = (
                f"{r.detail}{where} runs {what} built from strings; {fix} ({_ref(check_id)})"
            )
        elif r.kind == "unsafe" and r.tag in BY_ID:
            check_id = r.tag
            message = f"{r.detail}{where}: {BY_ID[check_id].title.lower()} ({_ref(check_id)})"
        elif r.kind in _RUNTIME:
            check_id, what = _RUNTIME[r.kind]
            message = f"{r.detail}{where} {what} ({_ref(check_id)})"
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

In `svarupa/health/__init__.py`, replace `from svarupa.health.security import security_checks` with `from svarupa.health.risks import risk_checks`, and `*security_checks(graph),` with `*risk_checks(graph),`.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_perf_reliability.py tests/test_health.py tests/test_health_outputs.py tests/test_security.py -q`
Expected: PASS.

- [ ] **Step 6: Full suite, lint, commit**

```bash
uv run pytest -q -x 2>&1 | tail -3
uv run ruff format svarupa tests && uv run ruff check svarupa tests && uv run pyright svarupa
git add svarupa/health tests
git commit -m "Grade performance and reliability from the new risk facts"
```

Expected: suite PASS.

---

### Task 5: Shared datastore from compose

**Files:**
- Modify: `svarupa/extract/compose.py` (new `_HOST`, `_env_items`, `_connects`; service attr `connects`)
- Create: `svarupa/health/performance.py`
- Modify: `svarupa/health/__init__.py` (call `shared_datastores`)
- Test: `tests/test_compose.py`, `tests/test_perf_reliability.py`

**Interfaces:**
- Consumes: compose service nodes (`NodeKind.SERVICE` / `DATASTORE`, attr `build_context`), `DEPENDS_ON` edges, `build_context_of(graph, service_id)`, `BY_ID["shared-datastore"]`.
- Produces: service node attr `connects` (newline-joined, sorted service names); `shared_datastores(graph: Graph) -> list[Violation]` in `svarupa.health.performance`.

- [ ] **Step 1: Write the failing compose test** (append to `tests/test_compose.py`)

```python
def test_connection_settings_and_links_are_recorded(tmp_path: Path) -> None:
    write(
        tmp_path,
        "docker-compose.yml",
        "x-env: &env\n"
        "  DB_HOST: db\n"
        "services:\n"
        "  api:\n"
        "    build: .\n"
        "    environment:\n"
        "      <<: *env\n"
        "      CACHE_URL: redis://cache:6379/0\n"
        "  worker:\n"
        "    build: ./w\n"
        "    environment:\n"
        "      - DATABASE_URL=postgres://u@db/app\n"
        "    links:\n"
        "      - \"mq:queue\"\n"
        "  db: {image: postgres}\n"
        "  cache: {image: redis}\n"
        "  mq: {image: rabbitmq}\n",
    )
    by = {n.label: n.attr("connects") for n in facts(tmp_path).nodes}
    assert by["api"] == "cache\ndb"
    assert by["worker"] == "db\nmq"
    assert by["db"] is None
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_compose.py -q -k connection_settings`
Expected: FAIL, `by["api"]` is `None`.

- [ ] **Step 3: Record `connects`** (`svarupa/extract/compose.py`)

Add `import re` to the imports. After `_MAX_COMPOSE_BYTES`, add:

```python
# A host inside a connection URL: `postgres://u:pw@db:5432/app`, `redis://cache`.
_HOST = re.compile(r"(?:@|//)([A-Za-z0-9_.-]+?)(?::\d|/|\?|$)")
_HOST_KEYS = ("HOST", "SERVER", "ADDR")
```

After `_key_lines`, add:

```python
def _env_items(node: yaml.Node | None) -> list[tuple[str, str]]:
    """(key, value) pairs of an `environment:` block, list form and merge keys too."""
    out: list[tuple[str, str]] = []
    if isinstance(node, yaml.MappingNode):
        for k, v in node.value:
            if not isinstance(k, yaml.ScalarNode):
                continue
            if k.value == "<<":
                for m in v.value if isinstance(v, yaml.SequenceNode) else [v]:
                    out.extend(_env_items(m))
            elif isinstance(v, yaml.ScalarNode):
                out.append((str(k.value), str(v.value)))
    elif isinstance(node, yaml.SequenceNode):
        for item in node.value:
            if isinstance(item, yaml.ScalarNode) and "=" in str(item.value):
                key, _, value = str(item.value).partition("=")
                out.append((key, value))
    return out


def _connects(body: dict[str, yaml.Node], others: set[str]) -> list[str]:
    """Services this one reaches through its connection settings or `links`."""
    found: set[str] = set()
    for key, value in _env_items(body.get("environment")):
        found.update(_HOST.findall(value))
        if key.upper().endswith(_HOST_KEYS):
            found.add(value.strip())
    links = body.get("links")
    if isinstance(links, yaml.SequenceNode):
        for v in links.value:
            if isinstance(v, yaml.ScalarNode):
                found.add(str(v.value).split(":", 1)[0])
    return sorted(found & others)
```

In the service loop, after the `if build_context:` block that appends build attrs (just before `node_id = f"{path}#service.{name}"`), add:

```python
            connects = _connects(body, set(services) - {name})
            if connects:
                attrs.append(("connects", "\n".join(connects)))
```

- [ ] **Step 4: Run it to verify it passes**

Run: `uv run pytest tests/test_compose.py -q`
Expected: PASS.

- [ ] **Step 5: Write the failing check tests** (append to `tests/test_perf_reliability.py`)

```python
SHARED = (
    "services:\n"
    "  api:\n"
    "    build: ./api\n"
    "    depends_on: [db]\n"
    "  worker:\n"
    "    build: ./worker\n"
    "    environment:\n"
    "      DATABASE_URL: postgres://app:${PW}@db:5432/app\n"
    "  admin:\n"
    "    image: adminer\n"
    "    depends_on: [db]\n"
    "  db:\n"
    "    image: postgres:16\n"
)


def test_two_codebases_on_one_datastore_are_flagged(tmp_path: Path) -> None:
    write(tmp_path, "docker-compose.yml", SHARED)
    shared = [v for v in run(tmp_path).violations if v.check == "shared-datastore"]
    assert len(shared) == 1
    v = shared[0]
    assert [e.start_line for e in v.evidence] == [12, 2, 5]
    assert v.value == 2
    assert v.message.startswith("db is used by 2 services (api, worker)")


def test_one_codebase_with_two_services_is_not_shared(tmp_path: Path) -> None:
    write(
        tmp_path,
        "docker-compose.yml",
        SHARED.replace("build: ./worker", "build: ./api"),
    )
    assert [v for v in run(tmp_path).violations if v.check == "shared-datastore"] == []
```

- [ ] **Step 6: Run them to verify they fail**

Run: `uv run pytest tests/test_perf_reliability.py -q -k "datastore or codebase"`
Expected: FAIL, `len(shared) == 0` for the first test.

- [ ] **Step 7: Write `svarupa/health/performance.py`**

```python
"""Performance checks that read the graph: datastores shared by services and
hubs that every request path goes through."""

from __future__ import annotations

from svarupa.build import Graph, build_context_of
from svarupa.extract.base import module_of
from svarupa.health.catalog import BY_ID
from svarupa.health.model import Violation
from svarupa.model import EdgeKind, NodeKind

__all__ = ["shared_datastores"]


def _app(graph: Graph, nid: str) -> bool:
    """A compose service built from this repository (not an image like adminer)."""
    node = graph.nodes.get(nid)
    return node is not None and node.kind is NodeKind.SERVICE and bool(node.attr("build_context"))


def _datastore(graph: Graph, nid: str) -> bool:
    node = graph.nodes.get(nid)
    return node is not None and node.kind is NodeKind.DATASTORE


def shared_datastores(graph: Graph) -> list[Violation]:
    """A datastore that services from two or more codebases use."""
    check = BY_ID["shared-datastore"]
    users: dict[str, set[str]] = {}
    for e in graph.edges:
        if e.kind is EdgeKind.DEPENDS_ON and _app(graph, e.src) and _datastore(graph, e.dst):
            users.setdefault(e.dst, set()).add(e.src)
    for nid in sorted(graph.nodes):
        if not _app(graph, nid):
            continue
        compose_file = nid.split("#", 1)[0]
        for name in (graph.nodes[nid].attr("connects") or "").split("\n"):
            dst = f"{compose_file}#service.{name}"
            if name and _datastore(graph, dst):
                users.setdefault(dst, set()).add(nid)
    out: list[Violation] = []
    for dst, apps in sorted(users.items()):
        codebases = {build_context_of(graph, a) or graph.nodes[a].attr("build_context") for a in apps}
        if len(codebases) <= check.threshold:
            continue
        store = graph.nodes[dst]
        names = sorted(graph.nodes[a].label for a in apps)
        ordered = sorted(apps, key=lambda a: graph.nodes[a].evidence[0].start_line)
        out.append(
            Violation(
                check="shared-datastore",
                message=(
                    f"{store.label} is used by {len(names)} services ({', '.join(names)}); "
                    "each service should own its data so one cannot slow or break another "
                    "(Newman, Building Microservices)"
                ),
                evidence=(store.evidence[0], *(graph.nodes[a].evidence[0] for a in ordered)),
                module=module_of(dst.split("#", 1)[0]),
                value=len(codebases),
                minutes=check.minutes,
            )
        )
    return out
```

In `svarupa/health/__init__.py`, add `from svarupa.health.performance import shared_datastores` and add `*shared_datastores(graph),` to `found` after `*risk_checks(graph),`.

- [ ] **Step 8: Run the tests to verify they pass**

Run: `uv run pytest tests/test_perf_reliability.py tests/test_compose.py -q`
Expected: PASS.

- [ ] **Step 9: Full suite, lint, commit**

```bash
uv run pytest -q -x 2>&1 | tail -3
uv run ruff format svarupa tests && uv run ruff check svarupa tests && uv run pyright svarupa
git add svarupa tests
git commit -m "Flag a datastore that services from two codebases share"
```

Expected: suite PASS. If byte-identity or golden tests change only because compose services gained `connects`, check each value against its compose file, regenerate, and ledger a ruling.

---

### Task 6: Hub module on the request path

**Files:**
- Modify: `svarupa/health/performance.py` (add `hub_on_request_path`)
- Modify: `svarupa/health/__init__.py`
- Test: `tests/test_perf_reliability.py`

**Interfaces:**
- Consumes: `graph.routes` (`RouteFact.file`), `graph.module_deps`, `graph.edges` (`EdgeKind.IMPORTS`), `BY_ID["hub-module"].threshold` (9), `BY_ID["hub-on-request-path"].threshold` (2).
- Produces: `hub_on_request_path(graph: Graph) -> list[Violation]`.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_perf_reliability.py`)

```python
ROUTE = (
    "from fastapi import APIRouter\n"
    "from hub.core import run\n"
    "router = APIRouter()\n"
    "@router.get('/x')\n"
    "def x():\n"
    "    return run()\n"
)


def hub_repo(root: Path, route_modules: int) -> None:
    for i in range(10):
        write(root, f"user{i}/m.py", "from hub.core import run\n")
        write(root, f"dep{i}/m.py", "def run():\n    return 1\n")
    imports = "".join(f"from dep{i}.m import run as r{i}\n" for i in range(10))
    write(root, "hub/core.py", imports + "\n\ndef run():\n    return 1\n")
    for i in range(route_modules):
        write(root, f"api{i}/routes.py", ROUTE)


def test_hub_reached_from_every_route_module_is_flagged(tmp_path: Path) -> None:
    hub_repo(tmp_path, 3)
    found = [v for v in run(tmp_path).violations if v.check == "hub-on-request-path"]
    assert [(v.module, v.value) for v in found] == [("hub", 3)]
    assert found[0].evidence[0].file == "hub/core.py"
    assert "every request path" in found[0].message


def test_two_route_modules_are_not_enough(tmp_path: Path) -> None:
    hub_repo(tmp_path, 2)
    assert [v for v in run(tmp_path).violations if v.check == "hub-on-request-path"] == []


def test_a_route_module_that_avoids_the_hub_clears_it(tmp_path: Path) -> None:
    hub_repo(tmp_path, 3)
    write(tmp_path, "api9/routes.py", ROUTE.replace("from hub.core import run\n", "run = len\n"))
    assert [v for v in run(tmp_path).violations if v.check == "hub-on-request-path"] == []
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_perf_reliability.py -q -k "hub or route_modules"`
Expected: the first test FAILS (`[]`); the other two pass already and pin the limits.

- [ ] **Step 3: Implement** (append to `svarupa/health/performance.py`; add `import networkx as nx` and `from svarupa.model import Evidence` to the imports and `"hub_on_request_path"` to `__all__`)

```python
def _first_imports(graph: Graph, module: str) -> tuple[Evidence, ...]:
    """The module's own import lines, in file and line order, at most five."""
    lines = {
        e.evidence[0]
        for e in graph.edges
        if e.kind is EdgeKind.IMPORTS and e.evidence and module_of(e.src) == module
    }
    return tuple(sorted(lines, key=lambda ev: (ev.file, ev.start_line, ev.end_line))[:5])


def hub_on_request_path(graph: Graph) -> list[Violation]:
    """A hub module that every route module reaches through its imports."""
    check, hub = BY_ID["hub-on-request-path"], BY_ID["hub-module"]
    route_modules = sorted({module_of(r.file) for r in graph.routes})
    if len(route_modules) <= check.threshold:
        return []
    g: nx.DiGraph[str] = nx.DiGraph()
    g.add_nodes_from(route_modules)
    g.add_edges_from(graph.module_deps)
    common = set.intersection(*(nx.descendants(g, m) | {m} for m in route_modules))
    out: list[Violation] = []
    for module in sorted(common):
        fan_in, fan_out = g.in_degree(module), g.out_degree(module)
        if fan_in <= hub.threshold or fan_out <= hub.threshold:
            continue
        cited = _first_imports(graph, module)
        if not cited:
            continue
        out.append(
            Violation(
                check="hub-on-request-path",
                message=(
                    f"{module or '.'} is a hub (used by {fan_in} modules, uses {fan_out}) "
                    f"on every request path, from {len(route_modules)} route modules; "
                    "a slow or broken change there reaches every endpoint"
                ),
                evidence=cited,
                module=module,
                value=len(route_modules),
                minutes=check.minutes,
            )
        )
    return out
```

In `svarupa/health/__init__.py`, import `hub_on_request_path` next to `shared_datastores` and add `*hub_on_request_path(graph),` to `found`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_perf_reliability.py -q`
Expected: PASS.

- [ ] **Step 5: Full suite, lint, commit**

```bash
uv run pytest -q -x 2>&1 | tail -3
uv run ruff format svarupa tests && uv run ruff check svarupa tests && uv run pyright svarupa
git add svarupa/health tests/test_perf_reliability.py
git commit -m "Flag a hub module that every request path goes through"
```

---

### Task 7: Mutations and docs

**Files:**
- Modify: `scripts/mutate_packs.py` (docstring list, `SUITE`, `MUTATIONS`)
- Modify: `docs/health.md`

- [ ] **Step 1: Add the mutations** (append to `MUTATIONS`, and add `"tests/test_perf_reliability.py"` and `"tests/test_compose.py"` to `SUITE`)

```python
    (
        "a loop's iterable counts as its body",
        "svarupa/extract/packs/walker.py",
        "                    (c, in_loop or (body is not None and c.id == body.id))\n",
        "                    (c, in_loop or body is not None)\n",
    ),
    (
        "async functions are never async",
        "svarupa/extract/packs/walker.py",
        "            is_async = spec.async_marker is not None and any(\n",
        "            is_async = False and any(\n",
    ),
    (
        "a catch that does something counts as empty",
        "svarupa/extract/packs/walker.py",
        "        return body is not None and all(c.type in spec.quiet for c in body.named_children)\n",
        "        return body is not None\n",
    ),
    (
        "callbacks of forEach and map are not per item",
        "svarupa/extract/packs/walker.py",
        "                    per_item = in_loop or self._per_item(node, spec)\n",
        "                    per_item = in_loop\n",
    ),
    (
        "one codebase with two services counts as shared",
        "svarupa/health/performance.py",
        "        if len(codebases) <= check.threshold:\n",
        "        if len(apps) <= check.threshold:\n",
    ),
    (
        "a hub off some request paths is still flagged",
        "svarupa/health/performance.py",
        "    common = set.intersection(*(nx.descendants(g, m) | {m} for m in route_modules))\n",
        "    common = set.union(*(nx.descendants(g, m) | {m} for m in route_modules))\n",
    ),
```

Add to the docstring list:

```
* only a loop's body runs per item;
* an async function is async;
* a catch with code in it is not empty;
* a forEach/map callback runs per item;
* one codebase is one owner of a datastore;
* a hub must be on every request path.
```

- [ ] **Step 2: Run the mutation check**

Run: `uv run python scripts/mutate_packs.py 2>&1 | tail -5`
Expected: every mutation caught (39/39).

- [ ] **Step 3: Update `docs/health.md`**

Replace the ratings bullet's last sentence ("Security is assessed; reliability and performance come next.") with "All four areas are assessed."

After the Security checks section (before `## Exceptions`), add:

```markdown
## Performance checks

| Id | Fires on | Source | Severity | Minutes |
|---|---|---|---|---|
| shared-datastore | services built from two or more codebases use the same compose datastore (`depends_on`, `links`, or a connection setting that names it) | Newman, Building Microservices: database per service | major | 240 |
| query-in-loop | a database query inside a loop body, or inside a `forEach`/`map` callback | N+1 query anti-pattern; Fowler, PoEAA | major | 20 |
| blocking-in-async | a blocking call (`requests`, `time.sleep`, `fs.readFileSync`, `bcrypt.hashSync`...) inside an async function | Python asyncio docs; Node.js "Don't block the event loop" | major | 15 |
| hub-on-request-path | a hub module (the `hub-module` limits) that every route module reaches, with at least 3 route modules | Arcan hub-like dependency | major | 60 |

How each one decides:

- **Loops.** Only the loop body counts: `for u in User.objects.all():` runs
  one query. A function passed to `forEach`, `map` or `flatMap` runs once
  per item. A query is a call on a database client, ORM model or
  repository (`cursor.execute`, `User.objects.get`, `prisma.user.findMany`,
  `userRepository.findById`, `db.QueryRow`).
- **Async.** A function marked `async` (Python, JavaScript, TypeScript). A
  function defined inside it is judged on its own.
- **Shared datastore.** Only services built from this repository count, so
  an admin image like adminer is never an owner. Two services built from
  the same folder (an app and its migration job) are one owner.

## Reliability checks

| Id | Fires on | Source | Severity | Minutes |
|---|---|---|---|---|
| no-timeout | `requests` or `urlopen` without `timeout=`, axios without `timeout`, Go's default HTTP client, `http.Client{}` without `Timeout`, Java `HttpClient.newHttpClient()` and `new RestTemplate()` | CWE-1088; Nygard, Release It! | major | 10 |
| swallowed-error | an `except` or `catch` whose body is empty, `pass` or comments; Go `if err != nil {}` | CWE-390; Bandit B110 | major | 10 |
| no-retry | a client built with retries off or a single attempt: pika without `connection_attempts`, `aio_pika.connect` (not `connect_robust`), Redis `MaxRetries: -1`, RabbitMQ automatic recovery off | Nygard, Release It! | minor | 15 |

`fetch` is not flagged: Node's fetch has 300-second timeouts by default and
a browser user can cancel a request. Clients that retry by default (most
Redis, MongoDB and Kafka clients) are not flagged either.
```

- [ ] **Step 4: Commit**

```bash
git add scripts/mutate_packs.py docs/health.md
git commit -m "Cover the new checks with mutations and document them"
```

---

### Task 8: Benchmark facts

**Files:**
- Modify: `benchmark/expected/<repo>.toml` (repos where real cases exist)
- Modify: `benchmark/scores.json` (via `accept`)
- Modify: `svarupa/health/catalog.py` (maturity, only if the benchmark proves it)

- [ ] **Step 1: Find candidates by reading the pinned source** (cache: `~/.cache/svarupa-bench`)

```bash
cd ~/.cache/svarupa-bench
grep -rnE "except[^:]*:\s*$" --include=*.py -A1 . | grep -B1 -E "^\S+-[0-9]+-\s+pass\s*$" | head -40
grep -rnE "catch \([^)]*\) \{\s*\}" --include=*.ts --include=*.js --include=*.java . | grep -v node_modules | head -20
grep -rnE "requests\.(get|post|put|delete)\(" --include=*.py . | grep -v timeout | head -20
grep -rnE "http\.(Get|Post)\(|http\.Client\{" --include=*.go . | head -20
grep -rnE "^\s*async def" --include=*.py -l . | head -20
```

For each candidate, open the file at the line and confirm it by reading: a query inside a loop body, a blocking call inside an `async def`, an empty `except`/`catch`, an HTTP call with no timeout. Write each confirmed one as a `[[must]]` fact: `violation = "<check> <path>:<line>"` with `why = "<path>:<line> <what the line shows>"`. Write at least one `[[must_not]]` per check where a near miss exists (a query in a loop's iterable, an `except` that logs, a request with `timeout=`). Write the facts before running Svarupa (benchmark README rule 1).

- [ ] **Step 2: Validate and score**

Run: `uv run python scripts/benchmark.py validate && for r in $(ls benchmark/expected | sed 's/.toml//'); do uv run python scripts/benchmark.py run $r | tail -2; done`
Expected: citations valid. A missed fact is a finding: debug it (systematic-debugging), never edit the fact to match (README rule 2). A probe that fires is a false positive: fix the rule with a test first.

- [ ] **Step 3: Accept and set maturity**

Run: `uv run python scripts/benchmark.py accept && uv run pytest tests/test_benchmark.py -q`
Expected: `test_check_maturity_follows_the_benchmark` names any check the benchmark now proves; set `maturity="stable"` on exactly those in `catalog.py` and rerun until PASS.

- [ ] **Step 4: Full suite, mutation check, commit**

```bash
uv run pytest -q 2>&1 | tail -3
uv run python scripts/mutate_packs.py 2>&1 | tail -2
git add benchmark svarupa/health/catalog.py
git commit -m "Benchmark the performance and reliability checks"
```

Expected: suite PASS, all mutations caught.
