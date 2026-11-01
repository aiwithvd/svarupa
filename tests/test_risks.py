"""Risk facts: what the walker records for the security checks.

A sink is recorded only when an argument is built from strings in the same
function; a parameterized call never is.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from svarupa.extract.packs import extractor


def risks(lang: str, path: str, src: str) -> list[tuple[str, str, int, str]]:
    f = extractor(lang).parse(path, src.encode())
    return [(r.kind, r.tag, r.evidence.start_line, r.function) for r in f.risks]


def test_python_sql_built_inline_is_recorded() -> None:
    src = (
        "def f(cur, x):\n"
        '    cur.execute(f"SELECT {x}")\n'
        '    cur.execute("SELECT " + x)\n'
        '    cur.execute("SELECT %s" % x)\n'
        '    cur.execute("SELECT {}".format(x))\n'
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
        '    cur.execute("SELECT * FROM t WHERE id = %s", (x,))\n'
        '    q = "SELECT 1"\n'
        "    cur.execute(q)\n"
    )
    assert risks("python", "m.py", src) == []


def test_built_variable_counts_until_reassigned() -> None:
    src = (
        "def f(cur, x):\n"
        '    q = "SELECT " + x\n'
        "    cur.execute(q)\n"
        '    q = "SELECT 1"\n'
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
        'q = "SELECT " + X\n'
        "def f(cur):\n"
        "    cur.execute(q)\n"
        "def g(cur, x):\n"
        '    q = "SELECT " + x\n'
        "    def inner(cur):\n"
        "        cur.execute(q)\n"
        "cur.execute(q)\n"
    )
    assert risks("python", "m.py", src) == [("sink-built", "sql", 8, "")]


def test_risks_reach_the_graph(tmp_path: Path) -> None:
    from svarupa.build import build
    from svarupa.detect import detect
    from svarupa.extract import declared_dependencies, extract

    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "m.py").write_text(
        'def f(cur, x):\n    cur.execute("SELECT " + x)\n', encoding="utf8"
    )
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_m.py").write_text(
        'def t(cur, x):\n    cur.execute("SELECT " + x)\n', encoding="utf8"
    )
    scan = detect(tmp_path)
    graph = build(scan, extract(scan, declared_dependencies(scan)), strict=False)
    assert [(r.tag, r.evidence.file, r.evidence.start_line) for r in graph.risks] == [
        ("sql", "app/m.py", 2)
    ]


def tags(lang: str, path: str, src: str) -> list[tuple[str, int]]:
    return [
        (r.tag, r.evidence.start_line) for r in extractor(lang).parse(path, src.encode()).risks
    ]


PY_TRUE = """import subprocess, os, pickle, yaml, hashlib, requests
def f(x, data):
    subprocess.run("ls " + x, shell=True)
    os.system(f"ls {x}")
    eval("1 + " + x)
    requests.get(x, verify=False, timeout=5)
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
    requests.get(x, verify=True, timeout=5)
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

    assert {"login_required", "get_current_user", "CurrentUser"} <= python.RISKS.auth_markers


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
        ("sql", 3),
        ("sql", 5),
        ("shell", 6),
        ("code", 7),
        ("code", 8),
        ("tls-verify-off", 9),
        ("weak-hash", 10),
        ("cors-any-origin", 11),
        ("cors-any-origin", 12),
        ("cors-any-origin", 13),
    ]


def test_javascript_safe_twins_are_not_recorded() -> None:
    assert tags("javascript", "m.js", JS_SAFE) == []


def test_typescript_shares_the_rules() -> None:
    src = "async function f(x: string) {\n  await db.query(`SELECT ${x}`);\n}\n"
    assert tags("typescript", "m.ts", src) == [("sql", 2)]


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
        ("sql", 3),
        ("sql", 5),
        ("shell", 6),
        ("tls-verify-off", 7),
        ("weak-hash", 8),
        ("debug-enabled", 9),
        ("cors-any-origin", 10),
    ]


def test_go_safe_twins_are_not_recorded() -> None:
    assert tags("go", "m.go", GO_SAFE) == []


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
        ("sql", 3),
        ("sql", 5),
        ("shell", 6),
        ("unsafe-deserialize", 7),
        ("weak-hash", 8),
        ("tls-verify-off", 9),
        ("cors-any-origin", 10),
        ("cors-any-origin", 12),
    ]


def test_java_safe_twins_are_not_recorded() -> None:
    assert tags("java", "A.java", JAVA_SAFE) == []


def test_java_cors_configuration_with_every_origin_is_recorded() -> None:
    src = """class A {
  CorsConfigurationSource f() {
    configuration.setAllowedOrigins(asList("*"));
    configuration.addAllowedOrigin("*");
    configuration.setAllowedOrigins(List.of("https://a.example"));
    return null;
  }
}
"""
    assert tags("java", "A.java", src) == [("cors-any-origin", 3), ("cors-any-origin", 4)]


def test_detail_never_carries_a_literal_from_the_callee() -> None:
    pw = "Sup3r" + "S3cretPw"
    src = f'def f(name):\n    psycopg2.connect("postgresql://admin:{pw}@db/x").cursor().execute("SELECT " + name)\n'
    (fact,) = extractor("python").parse("m.py", src.encode()).risks
    assert pw not in fact.detail and fact.detail.endswith("execute")


def test_plotting_text_is_not_a_sql_sink() -> None:
    src = (
        "def f(ax, st, v, sql, sa, name):\n"
        '    ax.text(0.5, 0.5, f"{v:.2f}")\n'
        '    st.text(f"Hello {name}")\n'
        '    sa.text("SELECT " + sql)\n'
        '    User.objects.raw("SELECT " + sql)\n'
    )
    assert tags("python", "m.py", src) == [("sql", 4), ("sql", 5)]


def test_a_chained_call_is_reported_once() -> None:
    assert tags(
        "python", "m.py", "import hashlib\ndef f(d):\n    return hashlib.md5(d).hexdigest()\n"
    ) == [("weak-hash", 3)]


def test_constant_concatenation_is_not_built() -> None:
    py = 'def f(cur, x):\n    cur.execute("SELECT 1 " + "FROM t")\n    cur.execute("SELECT 1 " + "FROM t " + x)\n'
    java = (
        'class A { void f(String x) {\n  stmt.executeQuery("SELECT a " +\n    "FROM t");\n} }\n'
    )
    assert tags("python", "m.py", py) == [("sql", 3)]
    assert tags("java", "A.java", java) == []


def test_java_sql_sinks_need_a_database_receiver() -> None:
    src = """class A {
  void f(String id, Object v) {
    cache.update("k" + id, v);
    map.query("k" + id);
    jdbcTemplate.update("DELETE FROM t WHERE id = " + id);
    stmt.execute("DELETE FROM t WHERE id = " + id);
  }
}
"""
    assert tags("java", "A.java", src) == [("sql", 5), ("sql", 6)]


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
    src = b"def f(ids):\n    for i in ids:\n        User.objects.get(id=i)\n"
    f = extractor("python").parse("m.py", src)
    assert [r.detail for r in f.risks] == ["User.objects.get"]


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
        ("catch", 9),
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


def test_go_error_block_with_a_comment_and_code_is_not_swallowed() -> None:
    src = (
        "package s\n\n"
        "func f() error {\n"
        "\tif err != nil {\n"
        "\t\t// the file may not exist yet\n"
        "\t\treturn nil\n"
        "\t}\n"
        "\tif err := g(); err != nil {\n"
        "\t\t// retry later\n"
        "\t\tlog.Println(err)\n"
        "\t}\n"
        "\tif err != nil {\n"
        "\t\t// ignored on purpose\n"
        "\t}\n"
        "\treturn nil\n"
        "}\n"
    )
    assert risks("go", "s.go", src) == [("empty-catch", "catch", 12, "f")]


def test_java_optional_map_is_not_a_loop() -> None:
    src = """class S {
  void f(String u, List<Long> ids) {
    userRepository.findByUsername(u).map(t -> userRepository.saveRelation(t));
    ids.stream().map(id -> userRepository.findById(id));
  }
}
"""
    assert risks("java", "S.java", src) == [("in-loop", "query", 4, "(anonymous)")]


def test_non_database_calls_in_loops_are_not_queries() -> None:
    py = (
        "def f(urls, request):\n"
        "    for u in urls:\n"
        "        session.get(u)\n"
        "        self.session.get(u)\n"
        "        request.query.get('x')\n"
        "        User.query.get(u)\n"
        "        db.session.get(User, u)\n"
    )
    assert [r[2] for r in risks("python", "m.py", py)] == [6, 7]
    go = (
        "package s\n\n"
        "func f() {\n"
        "\tfor _, x := range xs {\n"
        "\t\tr.URL.Query()\n"
        "\t\tbufPool.Get()\n"
        '\t\tsessionStore.Get(r, "s")\n'
        '\t\tctx.Get("k")\n'
        '\t\tdb.QueryRow("SELECT 1")\n'
        "\t\ts.db.Where(x).First(&u)\n"
        "\t}\n"
        "}\n"
    )
    assert [r[2] for r in risks("go", "s.go", go)] == [9, 10]
    java = """class S {
  void f(List<Item> items) {
    for (Item item : items) {
      item.get();
      em.find(Item.class, 1);
    }
  }
}
"""
    assert [r[2] for r in risks("java", "S.java", java)] == [5]
    js = """async function f(xs) {
  for (const x of xs) {
    await client.messages.create({});
    await prisma.user.create({});
  }
}
"""
    assert [r[2] for r in risks("javascript", "m.js", js)] == [4]


CS_TRUE = """class A {
  void F(string id, string name) {
    db.Orders.FromSqlRaw("select * from t where id = " + id);
    db.Orders.FromSqlRaw($"select * from t where id = {id}");
    db.Database.ExecuteSqlRaw("delete from t where n = '" + name + "'");
    var cmd = new SqlCommand("select * from t where id = " + id, conn);
    conn.Query("select * from t where id = " + id);
    Process.Start("sh", "-c " + name);
    new ProcessStartInfo("cmd", $"/c {name}");
    CSharpScript.EvaluateAsync($"1 + {name}");
    handler.ServerCertificateCustomValidationCallback = (m, c, ch, e) => true;
    var h = HttpClientHandler.DangerousAcceptAnyServerCertificateValidator;
    new BinaryFormatter();
    settings.TypeNameHandling = TypeNameHandling.All;
    MD5.Create();
    new SHA1CryptoServiceProvider();
    policy.AllowAnyOrigin();
  }
}
"""

CS_SAFE = """class A {
  void F(string id, string name) {
    db.Orders.FromSqlInterpolated($"select * from t where id = {id}");
    db.Database.ExecuteSqlInterpolated($"delete from t where id = {id}");
    db.Orders.FromSqlRaw("select * from t where id = 1");
    var cmd = new SqlCommand("select * from t where id = @id", conn);
    conn.Query("select * from t where id = @id", new { id });
    cache.Query("a" + id);
    Process.Start("ls");
    new ProcessStartInfo("ls", "-l");
    CSharpScript.EvaluateAsync("1 + 1");
    handler.ServerCertificateCustomValidationCallback = (m, c, ch, e) => e == SslPolicyErrors.None;
    var f = new JsonSerializer();
    settings.TypeNameHandling = TypeNameHandling.None;
    SHA256.Create();
    new SHA256Managed();
    policy.WithOrigins("https://a.example");
    app.UseDeveloperExceptionPage();
    var c = new HttpClient();
  }
}
"""


def test_csharp_true_cases_are_recorded() -> None:
    assert tags("csharp", "A.cs", CS_TRUE) == [
        ("sql", 3),
        ("sql", 4),
        ("sql", 5),
        ("sql", 6),
        ("sql", 7),
        ("shell", 8),
        ("shell", 9),
        ("code", 10),
        ("tls-verify-off", 11),
        ("tls-verify-off", 12),
        ("unsafe-deserialize", 13),
        ("unsafe-deserialize", 14),
        ("weak-hash", 15),
        ("weak-hash", 16),
        ("cors-any-origin", 17),
    ]


def test_csharp_safe_twins_are_not_recorded() -> None:
    assert tags("csharp", "A.cs", CS_SAFE) == []


def test_csharp_unsafe_detail_carries_no_source_literal() -> None:
    src = 'class A { void F(string i) { db.Orders.FromSqlRaw("select " + i); '
    src += "x.TypeNameHandling = TypeNameHandling.Auto; } }"
    for r in extractor("csharp").parse("A.cs", src.encode()).risks:
        assert "select" not in r.detail and "Auto" not in r.detail


def test_csharp_null_conditional_call_is_a_sink() -> None:
    src = 'class A { void F(string i) { db?.Orders.FromSqlRaw("select " + i); } }'
    assert tags("csharp", "A.cs", src) == [("sql", 1)]


def test_csharp_built_variable_reaches_a_sink() -> None:
    src = """class A {
  void F(string id) {
    var q = $"select * from t where id = {id}";
    conn.Query(q);
    var ok = "select 1";
    conn.Query(ok);
  }
}
"""
    assert tags("csharp", "A.cs", src) == [("sql", 4)]


CS_RUNTIME = """class S {
  async Task Load(List<int> ids) {
    foreach (var id in ids) {
      await _db.Users.FindAsync(id);
    }
    for (var i = 0; i < 3; i++) {
      _orderRepository.FindById(i);
    }
    ids.ForEach(id => _orderRepository.FindByUser(id));
    foreach (var u in _userRepository.FindAll()) {
      Log(u);
    }
    Thread.Sleep(10);
    task.Wait();
    task.GetAwaiter().GetResult();
    var t = File.ReadAllText(path);
    var r = task.Result;
    await Task.Delay(10);
    await File.ReadAllTextAsync(path);
    try {
      Run();
    } catch (Exception e) {
      // ignore
    }
    try {
      Run();
    } catch (Exception e) {
      throw;
    }
  }
  void Sync() {
    Thread.Sleep(10);
    File.ReadAllText(path);
  }
}
"""


def test_csharp_runtime_facts_are_recorded() -> None:
    assert risks("csharp", "S.cs", CS_RUNTIME) == [
        ("in-loop", "query", 4, "Load"),
        ("in-loop", "query", 7, "Load"),
        ("in-loop", "query", 9, "(anonymous)"),
        ("blocking-async", "io", 13, "Load"),
        ("blocking-async", "io", 14, "Load"),
        ("blocking-async", "io", 15, "Load"),
        ("blocking-async", "io", 16, "Load"),
        ("empty-catch", "catch", 22, "Load"),
    ]


def test_csharp_async_lambda_and_local_function_are_async() -> None:
    src = """class S {
  void Run() {
    var f = async () => { Thread.Sleep(1); };
    async Task L() { Thread.Sleep(1); }
    var g = () => { Thread.Sleep(1); };
  }
}
"""
    assert [r[2] for r in risks("csharp", "S.cs", src)] == [3, 4]


def test_csharp_null_conditional_query_in_a_loop() -> None:
    src = """class S {
  void F(List<int> ids) {
    foreach (var id in ids) {
      _db?.Users.Find(id);
    }
  }
}
"""
    assert risks("csharp", "S.cs", src) == [("in-loop", "query", 4, "F")]


def test_csharp_cannot_fire_endpoint_without_auth_yet() -> None:
    from svarupa.health.explain import required_languages

    assert "csharp" not in required_languages("endpoint-without-auth")


CS_SAFE_CASES = {
    "http-context-in-loop": (
        "class S { void F(List<int> ids) {\n"
        "  foreach (var i in ids) { httpContext.Items.Add(i, i); context.Items.Add(i, i); }\n"
        "} }\n"
    ),
    "add-in-loop-then-save": (
        "class S { async Task F(List<int> ids) {\n"
        "  foreach (var i in ids) { _db.Orders.Add(i); _db.Orders.Remove(i); _db.Update(i); }\n"
        "  await _db.SaveChangesAsync();\n"
        "} }\n"
    ),
    "block-lambda-validator-checks": (
        "class A { void F() {\n"
        "  h.ServerCertificateCustomValidationCallback = (m, c, ch, e) => { return e == 0; };\n"
        "} }\n"
    ),
    "constant-concat-into-raw": 'class A { void F() { db.Orders.FromSqlRaw("a" + "b"); } }\n',
    "sync-wait": "class A { void F() { semaphore.Wait(); } }\n",
}


def test_csharp_safe_twins_by_name() -> None:
    for name, src in CS_SAFE_CASES.items():
        assert tags("csharp", "A.cs", src) == [], name


def test_csharp_block_bodied_validators_that_accept_all_are_recorded() -> None:
    src = """class A {
  void F() {
    h.ServerCertificateCustomValidationCallback = (m, c, ch, e) => { return true; };
    h.ServerCertificateCustomValidationCallback = delegate { return true; };
  }
}
"""
    assert tags("csharp", "A.cs", src) == [("tls-verify-off", 3), ("tls-verify-off", 4)]


def test_csharp_context_queries_in_a_loop_still_fire() -> None:
    src = """class S {
  void F(List<int> ids) {
    foreach (var i in ids) {
      _db.Orders.Find(i);
      _dbContext.Orders.First(i);
      _orderRepo.FindById(i);
    }
  }
}
"""
    assert [r[2] for r in risks("csharp", "S.cs", src)] == [4, 5, 6]


def test_csharp_common_data_receivers_query_in_a_loop() -> None:
    for recv in (
        "_context.Orders",
        "context.Orders",
        "_repository",
        "_repo",
        "orderRepo",
        "_catalogDbContext.Items",
    ):
        src = f"class S {{ void F(List<int> ids) {{ foreach (var i in ids) {{ {recv}.FindById(i); }} }} }}\n"
        assert risks("csharp", "S.cs", src) == [("in-loop", "query", 1, "F")], recv


def test_csharp_http_context_in_a_loop_stays_safe() -> None:
    for recv in ("httpContext", "HttpContext", "_httpContext", "context"):
        src = f"class S {{ void F(List<int> ids) {{ foreach (var i in ids) {{ {recv}.Items.Add(i, i); }} }} }}\n"
        assert risks("csharp", "S.cs", src) == [], recv


# Rust. Each case is one function so a failure names the rule.
def _rs(body: str, head: str = "fn f()") -> str:
    return f"{head} {{\n{body}\n}}\n"


RS_FIRES = {
    "sqlx-format-ref": (_rs('  sqlx::query(&format!("select {}", id));'), [("sql", 2)]),
    "sqlx-format-inline-capture": (
        _rs('  sqlx::query(&format!("select {id}"));'),
        [("sql", 2)],
    ),
    "sqlx-chain-await": (
        _rs('  sqlx::query(&format!("select {}", id)).bind(1).fetch_all(&pool).await?;'),
        [("sql", 2)],
    ),
    "sqlx-built-variable": (
        _rs('  let sql = format!("select {}", id);\n  sqlx::query(&sql).execute(&pool);'),
        [("sql", 3)],
    ),
    "sqlx-mut-variable-push-str": (
        _rs("  let mut sql = String::new();\n  sql.push_str(&name);\n  sqlx::query(&sql);"),
        [("sql", 4)],
    ),
    "sqlx-plus-concat": (
        _rs('  let sql = "select ".to_string() + &name;\n  sqlx::query(&sql);'),
        [("sql", 3)],
    ),
    "sqlx-plus-assign": (
        _rs('  let mut sql = String::from("select ");\n  sql += &name;\n  sqlx::query(&sql);'),
        [("sql", 4)],
    ),
    "sqlx-query-as-generic": (
        _rs('  sqlx::query_as::<_, User>(&format!("select {}", id));'),
        [("sql", 2)],
    ),
    "diesel-sql-query": (
        _rs('  diesel::sql_query(format!("select {}", id));'),
        [("sql", 2)],
    ),
    "connection-execute": (
        _rs('  conn.execute(&format!("delete {}", id), &[]);'),
        [("sql", 2)],
    ),
    "self-client-query": (
        _rs('  self.client.query(&format!("select {}", id), &[]);'),
        [("sql", 2)],
    ),
    "shell-built-program": (
        _rs('  Command::new(&format!("{}/run", dir));'),
        [("shell", 2)],
    ),
    "shell-sh-c-built-arg": (
        _rs('  Command::new("sh").arg("-c").arg(format!("ls {}", dir)).output();'),
        [("shell", 2)],
    ),
    "shell-bash-variable": (
        _rs('  let cmd = format!("ls {}", dir);\n  Command::new("bash").arg("-c").arg(&cmd);'),
        [("shell", 3)],
    ),
    "tls-certs": (
        _rs("  Client::builder().danger_accept_invalid_certs(true).build();"),
        [("tls-verify-off", 2)],
    ),
    "tls-hostnames": (
        _rs("  Client::builder().danger_accept_invalid_hostnames( true ).build();"),
        [("tls-verify-off", 2)],
    ),
    "weak-md5-compute": (_rs("  let d = md5::compute(data);"), [("weak-hash", 2)]),
    "weak-md5-new": (_rs("  let h = Md5::new();"), [("weak-hash", 2)]),
    "weak-sha1-new": (_rs("  let h = Sha1::new();"), [("weak-hash", 2)]),
    "weak-sha1-path": (_rs("  let h = sha1::Sha1::new();"), [("weak-hash", 2)]),
    "weak-sha1-digest": (_rs("  let h = Sha1::digest(data);"), [("weak-hash", 2)]),
    "weak-md5-digest": (_rs("  let h = md5::Md5::digest(data);"), [("weak-hash", 2)]),
    "weak-sha1-default": (_rs("  let h = Sha1::default();"), [("weak-hash", 2)]),
    "weak-hmac-sha1-type": ("type HmacSha1 = Hmac<Sha1>;\n", [("weak-hash", 1)]),
    "sqlx-as-str": (
        _rs('  let sql = format!("select {}", id);\n  sqlx::query(sql.as_str());'),
        [("sql", 3)],
    ),
    "sqlx-clone": (
        _rs('  let sql = format!("select {}", id);\n  sqlx::query(&sql.clone());'),
        [("sql", 3)],
    ),
    "sqlx-index": (
        _rs('  let sql = format!("select {}", id);\n  sqlx::query(&sql[..]);'),
        [("sql", 3)],
    ),
    "sqlx-field-push-str": (
        _rs("  self.sql.push_str(&name);\n  sqlx::query(&self.sql);"),
        [("sql", 3)],
    ),
    "shell-args-array": (
        _rs(
            '  Command::new("sh").args(["-c", &cmd_text(x)]).output();'.replace(
                "&cmd_text(x)", '&format!("ls {}", d)'
            )
        ),
        [("shell", 2)],
    ),
    "shell-args-ref-array-variable": (
        _rs(
            '  let cmd = format!("ls {}", d);\n  Command::new("bash").args(&["-c", &cmd]).output();'
        ),
        [("shell", 3)],
    ),
    "cors-permissive": (_rs("  let c = CorsLayer::permissive();"), [("cors-any-origin", 2)]),
    "cors-very-permissive": (
        _rs("  let c = CorsLayer::very_permissive();"),
        [("cors-any-origin", 2)],
    ),
    "cors-allow-origin-any": (
        _rs("  let c = CorsLayer::new().allow_origin(Any);"),
        [("cors-any-origin", 2)],
    ),
    "cors-allow-origin-any-fn": (
        _rs("  let c = CorsLayer::new().allow_origin(AllowOrigin::any());"),
        [("cors-any-origin", 2)],
    ),
    "cors-actix-permissive": (_rs("  let c = Cors::permissive();"), [("cors-any-origin", 2)]),
    "cors-actix-any": (
        _rs("  let c = Cors::default().allow_any_origin();"),
        [("cors-any-origin", 2)],
    ),
}


RS_QUIET = {
    "sqlx-bind": _rs(
        '  sqlx::query("select * from t where id = $1").bind(id).fetch_all(&pool);'
    ),
    "sqlx-literal-only": _rs('  sqlx::query("select 1");'),
    "sqlx-format-without-args": _rs('  sqlx::query(&format!("select 1"));'),
    "sqlx-bind-built-value": _rs('  sqlx::query("select $1").bind(format!("%{}%", q));'),
    "sqlx-query-macro": _rs('  sqlx::query!("select * from t where id = $1", id);'),
    "sqlx-query-as-macro-with-format": _rs(
        '  sqlx::query_as!(User, "select * from t where id = $1", format!("{}", id));'
    ),
    "sqlx-query-macro-with-built-name": _rs(
        '  let sql = format!("select {}", id);\n  sqlx::query!(sql);'
    ),
    "sqlx-macro-chain": _rs('  sqlx::query!("select 1").fetch_one(&pool).await?;'),
    "diesel-literal": _rs('  diesel::sql_query("select 1");'),
    "concat-of-literals": _rs('  sqlx::query(concat!("select ", "1"));'),
    "reassigned-literal": _rs(
        '  let mut sql = format!("select {}", id);\n  sql = "select 1".to_string();\n  sqlx::query(&sql);'
    ),
    "as-str-of-literal": _rs('  let sql = "select 1";\n  sqlx::query(sql.as_str());'),
    "push-str-literal": _rs(
        '  let mut sql = String::new();\n  sql.push_str("select 1");\n  sqlx::query(&sql);'
    ),
    "http-client-query": _rs('  self.http.get(&format!("https://x/{}", id));'),
    "shell-literal-program": _rs('  Command::new("git").arg(name).output();'),
    "shell-literal-chain": _rs('  Command::new("git").arg("-C").arg(format!("{}", dir));'),
    "shell-sh-literal-args": _rs('  Command::new("sh").arg("-c").arg("ls");'),
    "tls-verify-on": _rs("  Client::builder().danger_accept_invalid_certs(false).build();"),
    "strong-hash": _rs("  let h = Sha256::new();"),
    "cors-explicit-origin": _rs("  let c = CorsLayer::new().allow_origin(origin);"),
    "cors-permissive-name-only": _rs("  let permissive = 1;"),
    "sync-sleep": _rs("  std::thread::sleep(d);"),
    "bare-sync-sleep": _rs("  thread::sleep(d);"),
    "sync-fs": _rs("  let s = std::fs::read_to_string(p);"),
    "import-sha1": "use sha1::Sha1;\n",
    "import-md5": "use md5::Md5;\n",
    "hmac-sha256-type": "type H = Hmac<Sha256>;\n",
    "shell-args-array-literal": _rs('  Command::new("sh").args(["-c", "ls"]).output();'),
    "git-args-array-built": _rs(
        '  Command::new("git").args(["-C", &format!("{}", d)]).output();'
    ),
    "async-tokio-fs-use-bare": _rs("  fs::read_to_string(p).await?;", "async fn f()"),
    "async-tokio-fs-write-bare": _rs("  fs::write(p, s).await?;", "async fn f()"),
    "async-tokio-fs-read-bare": _rs("  fs::read(p).await;", "async fn f()"),
    "async-tokio-sleep": _rs("  tokio::time::sleep(d).await;", "async fn f()"),
    "async-tokio-fs": _rs("  let s = tokio::fs::read_to_string(p).await?;", "async fn f()"),
    "async-spawn-blocking": _rs(
        "  tokio::task::spawn_blocking(|| std::thread::sleep(d)).await;", "async fn f()"
    ),
    "any-in-one-query": _rs(
        '  sqlx::query("select * from t where id = ANY($1)").bind(ids).fetch_all(&pool).await?;'
    ),
    "query-outside-loop": _rs(
        '  let r = sqlx::query("select 1").fetch_one(&pool).await?;\n  for i in ids { total += i; }'
    ),
    "iterable-query": _rs(
        '  for r in sqlx::query("select 1").fetch_all(&pool).await? { println!("{:?}", r); }'
    ),
    "plain-first-in-loop": _rs("  for i in ids { let a = v.first(); let b = it.load(); }"),
}


@pytest.mark.parametrize("name", list(RS_FIRES))
def test_rust_true_case_is_recorded(name: str) -> None:
    src, want = RS_FIRES[name]
    assert tags("rust", "src/a.rs", src) == want


@pytest.mark.parametrize("name", list(RS_QUIET))
def test_rust_safe_twin_is_quiet(name: str) -> None:
    assert tags("rust", "src/a.rs", RS_QUIET[name]) == []


RS_RUNTIME = {
    "async-thread-sleep": (
        _rs("  std::thread::sleep(d);", "async fn f()"),
        [("blocking-async", "io", 2, "f")],
    ),
    "pub-async-thread-sleep-bare": (
        _rs("  thread::sleep(d);", "pub async fn f()"),
        [("blocking-async", "io", 2, "f")],
    ),
    "async-unsafe-modifiers": (
        _rs("  thread::sleep(d);", "pub async unsafe fn f()"),
        [("blocking-async", "io", 2, "f")],
    ),
    "async-std-fs": (
        _rs("  let s = std::fs::read_to_string(p);", "async fn f()"),
        [("blocking-async", "io", 2, "f")],
    ),
    "async-bare-fs-write": (
        _rs("  fs::write(p, b);", "async fn f()"),
        [("blocking-async", "io", 2, "f")],
    ),
    "async-blocking-lock": (
        _rs("  let g = m.blocking_lock();", "async fn f()"),
        [("blocking-async", "io", 2, "f")],
    ),
    "async-reqwest-blocking": (
        _rs("  let r = reqwest::blocking::get(u);", "async fn f()"),
        [("blocking-async", "io", 2, "f")],
    ),
    "async-block-in-async-fn": (
        _rs("  tokio::spawn(async move { std::thread::sleep(d); });", "async fn f()"),
        [("blocking-async", "io", 2, "f")],
    ),
    "async-method": (
        "impl S {\n  async fn f(&self) {\n    std::thread::sleep(d);\n  }\n}\n",
        [("blocking-async", "io", 3, "S.f")],
    ),
    "sqlx-fetch-one-in-for": (
        _rs(
            '  for id in ids {\n    sqlx::query("select 1 where id = $1").bind(id).fetch_one(&pool).await?;\n  }'
        ),
        [("in-loop", "query", 3, "f")],
    ),
    "sqlx-query-as-in-while": (
        _rs("  while go {\n    sqlx::query_as::<_, U>(Q).fetch_optional(&pool).await?;\n  }"),
        [("in-loop", "query", 3, "f")],
    ),
    "sqlx-macro-fetch-in-loop": (
        _rs('  loop {\n    sqlx::query!("select 1").fetch_one(&pool).await?;\n  }'),
        [("in-loop", "query", 3, "f")],
    ),
    "diesel-load-in-for": (
        _rs(
            "  for id in ids {\n    users::table.filter(users::id.eq(id)).load(&mut conn)?;\n  }"
        ),
        [("in-loop", "query", 3, "f")],
    ),
    "conn-execute-in-for": (
        _rs("  for id in ids {\n    conn.execute(Q, &[&id])?;\n  }"),
        [("in-loop", "query", 3, "f")],
    ),
    "closure-for-each": (
        _rs('  ids.iter().for_each(|id| {\n    sqlx::query("q").fetch_one(&pool);\n  });'),
        [("in-loop", "query", 3, "(anonymous)")],
    ),
}


@pytest.mark.parametrize("name", list(RS_RUNTIME))
def test_rust_runtime_fact_is_recorded(name: str) -> None:
    src, want = RS_RUNTIME[name]
    assert risks("rust", "src/a.rs", src) == want


def test_rust_risk_detail_carries_no_source_literal() -> None:
    src = _rs('  Command::new("sh").arg("secret-flag").arg(format!("x {}", a));')
    for r in extractor("rust").parse("src/a.rs", src.encode()).risks:
        assert "secret-flag" not in r.detail and '"' not in r.detail


def test_rust_cannot_fire_swallowed_error_or_endpoint_without_auth() -> None:
    from svarupa.health.explain import required_languages

    assert "rust" not in required_languages("swallowed-error")
    assert "rust" not in required_languages("endpoint-without-auth")


def test_rust_blocking_call_in_async_still_fires_without_await() -> None:
    src = _rs("  fs::write(p, s);", "async fn f()")
    assert risks("rust", "src/a.rs", src) == [("blocking-async", "io", 2, "f")]


def test_async_marker_is_a_whole_word_in_a_multi_word_holder() -> None:
    src = "class S { public static async Task N() {\n  Thread.Sleep(1);\n} }\n"
    assert risks("csharp", "S.cs", src) == [("blocking-async", "io", 2, "N")]


def test_async_marker_is_not_matched_inside_a_longer_word() -> None:
    src = "class S { public static Task asyncThing() {\n  Thread.Sleep(1);\n} }\n"
    assert risks("csharp", "S.cs", src) == []
    src = "pub fn f() {\n  std::thread::sleep(d);\n}\n"
    assert risks("rust", "src/a.rs", src) == []


_RS_LOOP = "  for id in ids {\n    sqlx::query(\"q\").fetch_one(&pool);\n  }\n"


def test_rust_test_code_has_no_risks_but_production_code_does() -> None:
    prod = f"fn work() {{\n{_RS_LOOP}}}\n"
    assert risks("rust", "src/a.rs", prod) == [("in-loop", "query", 3, "work")]
    in_test_fn = f"#[test]\nfn t() {{\n{_RS_LOOP}}}\n"
    assert risks("rust", "src/a.rs", in_test_fn) == []
    in_tokio = f"#[tokio::test]\nasync fn t() {{\n{_RS_LOOP}}}\n"
    assert risks("rust", "src/a.rs", in_tokio) == []
    in_mod = f"#[cfg(test)]\nmod tests {{\n  fn helper() {{\n{_RS_LOOP}}}\n}}\n"
    assert risks("rust", "src/a.rs", in_mod) == []
    in_plain_mod = f"mod tests {{\n  fn helper() {{\n{_RS_LOOP}}}\n}}\n"
    assert len(risks("rust", "src/a.rs", in_plain_mod)) == 1
