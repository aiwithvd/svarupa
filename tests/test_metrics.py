"""Function metrics: cyclomatic complexity, length, parameters, nesting.

Complexity is McCabe's: 1 plus one per decision point (if, elif, loop,
case, catch, ternary, each boolean operator). A nested function is measured
on its own and adds nothing to the function around it.
"""

from __future__ import annotations

import pytest

from svarupa.extract.packs import extractor


def metrics(lang: str, path: str, src: str) -> dict[str, tuple[int, int, int, int]]:
    f = extractor(lang).parse(path, src.encode())
    return {m.name: (m.complexity, m.params, m.nesting, m.lines) for m in f.functions}


PY = """def f(a, b, *args, **kw):
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
"""


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
    assert got["f"] == (8, 2, 1, 14)
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


CSHARP = """class A {
  int M(int a, string b) {
    if (a > 0 || b == null) {
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
    var c = b ?? "x";
    return a > 1 ? 1 : 0;
  }
}
"""


def test_csharp_metrics() -> None:
    assert metrics("csharp", "A.cs", CSHARP)["M"] == (9, 2, 2, 17)


def test_csharp_else_if_adds_no_nesting() -> None:
    src = "class A { void M(int a) {\n if (a > 0) {\n } else if (a > 1) {\n } else if (a > 2) {\n }\n} }"
    assert metrics("csharp", "A.cs", src)["M"] == (4, 1, 1, 6)


def test_csharp_switch_expression_arms_count_but_the_discard_arm_does_not() -> None:
    src = "class A { int M(int a) => a switch { 1 => 2, 2 => 3, _ => 4 }; }"
    assert metrics("csharp", "A.cs", src)["M"] == (3, 1, 1, 1)


def test_csharp_stacked_case_labels_each_count() -> None:
    src = "class A { void M(int a) { switch (a) { case 1: case 2: break; default: break; } } }"
    assert metrics("csharp", "A.cs", src)["M"][0] == 3


def test_csharp_constructor_lambda_and_local_function_are_measured_on_their_own() -> None:
    src = """class A {
  A(int x, int y) { if (x > 0) { } }
  void M(int a) {
    Func<int, int> f = v => v > 0 ? 1 : 2;
    int L(int q) { if (q > 0) { return 1; } return 0; }
    if (a > 0) { }
  }
}
"""
    got = metrics("csharp", "A.cs", src)
    assert got["A"][:2] == (2, 2)
    assert got["M"][0] == 2
    assert got["L"][:2] == (2, 1)
    assert got["f"][:2] == (2, 1)


def test_csharp_lambda_parameters() -> None:
    src = "class A { void M() { F(x => x); F((a, b) => a); F(() => 1); } }"
    f = extractor("csharp").parse("A.cs", src.encode())
    assert [m.params for m in f.functions if m.name != "M"] == [1, 2, 0]


def test_csharp_accessors_with_bodies_are_functions_and_auto_accessors_are_not() -> None:
    src = """class A {
  int P { get; set; }
  int Q {
    get { return 1; }
    set { if (value > 0) { x = value; } }
  }
}
"""
    f = extractor("csharp").parse("A.cs", src.encode())
    assert [m.complexity for m in f.functions] == [1, 2]


@pytest.mark.parametrize(
    ("body", "want"),
    [
        ("for (int i = 0; i < a; i++) { }", (2, 1)),
        ("foreach (var x in a) { }", (2, 1)),
        ("while (a > 0) { }", (2, 1)),
        ("do { } while (a > 0);", (2, 1)),
        ("if (a > 0) { }", (2, 1)),
        ("switch (a) { case 1: break; }", (2, 1)),
        ("try { } catch (Exception e) { }", (2, 1)),
        ("try { } finally { }", (1, 1)),
        ("var t = a > 0 && a < 5;", (2, 0)),
        ("var t = a > 0 || a < 5;", (2, 0)),
        ("var t = b ?? 1;", (2, 0)),
        ("b ??= 1;", (2, 0)),
        ("var t = a > 0 ? 1 : 2;", (2, 0)),
        ("var t = a + 1;", (1, 0)),
    ],
)
def test_csharp_each_construct_on_its_own(body: str, want: tuple[int, int]) -> None:
    src = f"class A {{ void M(int a, int? b) {{ {body} }} }}"
    got = metrics("csharp", "A.cs", src)["M"]
    assert (got[0], got[2]) == want


def test_csharp_accessors_are_named_after_their_property() -> None:
    src = """class A {
  int P { get { return 1; } set { x = value; } }
  int Q => 1;
  int this[int i] { get { return i; } }
  ~A() { }
}
"""
    f = extractor("csharp").parse("A.cs", src.encode())
    assert [m.name for m in f.functions] == ["P.get", "P.set", "Q.get", "this.get", "A"]


def test_csharp_remaining_members_are_measured() -> None:
    src = """class A {
  int Q => 1 > 0 ? 1 : 2;
  public static A operator +(A l, A r) { if (l == null) { } return l; }
  public static implicit operator int(A a) { return a == null ? 0 : 1; }
  ~A() { if (x) { } }
  void M() { Action f = delegate (int q) { if (q > 0) { } }; }
}
"""
    f = extractor("csharp").parse("A.cs", src.encode())
    got = {m.name: m.complexity for m in f.functions}
    assert got["Q.get"] == 2
    assert got["+"] == 2
    assert got["A"] == 2
    assert got["M"] == 1
    assert sorted(m.complexity for m in f.functions) == [1, 2, 2, 2, 2, 2]


def test_csharp_a_method_arrow_body_is_not_a_second_function() -> None:
    f = extractor("csharp").parse("A.cs", b"class A { int M(int a) => a > 0 ? 1 : 2; }")
    assert [(m.name, m.complexity) for m in f.functions] == [("M", 2)]


def test_csharp_bodyless_accessors_leave_risk_scanning_alone() -> None:
    src = b'class A { int P { get; set; } void M(string i) { Db.ExecuteSqlRaw("x" + i); } }'
    f = extractor("csharp").parse("A.cs", src)
    assert f.functions[0].name == "M"


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


def test_a_python_lambda_is_measured_once() -> None:
    f = extractor("python").parse("m.py", b"g = lambda x: x if x else 0\n")
    assert [(m.name, m.complexity) for m in f.functions] == [("(anonymous)", 2)]


def test_a_js_generator_expression_is_its_own_function() -> None:
    got = metrics(
        "javascript",
        "m.js",
        "function outer(a) {\n  const ge = function* (b) { if (b) {} };\n  return ge;\n}\n",
    )
    assert got["outer"][0] == 1
    assert got["ge"][0] == 2


def test_every_metrics_node_type_exists_in_its_grammar() -> None:
    from svarupa.extract.packs import PACKS
    from svarupa.extract.packs.walker import language_for

    for pack in PACKS:
        spec = pack.metrics
        if spec is None:
            continue
        names = (
            spec.function_types
            | spec.branch_types
            | spec.nesting_types
            | {t for t, _ in spec.boolean_ops}
            | {t for t, _ in spec.not_branch}
        )
        for path in ["x", *(f"x{s}" for s, _ in pack.grammar.by_suffix)]:
            lang = language_for(pack.grammar, path)
            missing = sorted(n for n in names if lang.id_for_node_kind(n, True) is None)
            assert not missing, (pack.lang, path, missing)


@pytest.mark.parametrize(
    ("body", "want"),
    [
        ("if a > 0 { }", (2, 1)),
        ("if a > 0 { } else if a < 0 { }", (3, 1)),
        ("if a > 0 { } else if a < 0 { } else { }", (3, 1)),
        ("if let Some(x) = o { }", (2, 1)),
        ("while a > 0 { }", (2, 1)),
        ("while let Some(x) = o { }", (2, 1)),
        ("loop { break; }", (2, 1)),
        ("for x in o { }", (2, 1)),
        ("match a { 1 => {} 2 => {} _ => {} }", (3, 1)),
        ("match a { 1 | 2 => {} _ => {} }", (2, 1)),
        ("match a { x if x > 5 => {} _ => {} }", (2, 1)),
        ("match a { _ if a > 5 => {} _ => {} }", (2, 1)),
        ("match a { Some(_) => {} None => {} }", (3, 1)),
        ("let t = a > 0 && a < 5;", (2, 0)),
        ("let t = a > 0 || a < 5;", (2, 0)),
        ("let t = a + 1;", (1, 0)),
        ("let t = f(a)?;", (1, 0)),
        ("let t = o.ok_or(1)?.max(2);", (1, 0)),
        ("if a > 0 { if a > 1 { } }", (3, 2)),
        ("for x in o { while a > 0 { loop { } } }", (4, 3)),
    ],
)
def test_rust_each_construct_on_its_own(body: str, want: tuple[int, int]) -> None:
    src = f"fn m(a: i32, o: Option<i32>) {{ {body} }}"
    got = metrics("rust", "a.rs", src)["m"]
    assert (got[0], got[2]) == want


def test_rust_self_is_not_a_parameter() -> None:
    src = """impl A {
    fn a(&self, x: i32) {}
    fn b(&mut self) {}
    fn c(self: Box<Self>, x: i32, (y, z): (i32, i32)) {}
    fn d(mut self) {}
    fn e() {}
}
"""
    got = metrics("rust", "a.rs", src)
    assert [got[f"A.{n}"][1] for n in "abcde"] == [1, 0, 2, 0, 0]


def test_rust_methods_are_named_after_their_type() -> None:
    src = """struct S;
trait T { fn sig(&self); fn dflt(&self) {} }
impl S { fn new() -> S { S } }
impl<X> T for Wrap<X> { fn sig(&self) {} }
mod inner { pub fn free() {} }
fn top() {}
"""
    names = [m.name for m in extractor("rust").parse("a.rs", src.encode()).functions]
    assert names == ["T.dflt", "S.new", "Wrap.sig", "free", "top"]


def test_rust_trait_signatures_without_a_body_are_not_functions() -> None:
    src = 'trait T { fn sig(&self, a: i32); }\nextern "C" { fn c(a: i32); }\n'
    assert extractor("rust").parse("a.rs", src.encode()).functions == ()


def test_rust_closures_are_measured_on_their_own() -> None:
    src = """fn outer(a: i32) {
    let c = |p, q| { if p > q { 1 } else { 2 } };
    let d = |x| x;
}
"""
    f = extractor("rust").parse("a.rs", src.encode())
    assert [(m.name, m.complexity, m.params) for m in f.functions] == [
        ("outer", 1, 1),
        ("(anonymous)", 2, 2),
        ("(anonymous)", 1, 1),
    ]
