"""Risk facts: what the walker records for the security checks.

A sink is recorded only when an argument is built from strings in the same
function; a parameterized call never is.
"""

from __future__ import annotations

from pathlib import Path

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

    assert {"login_required", "get_current_user", "CurrentUser"} <= python.RISKS.auth_markers
