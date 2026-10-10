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
