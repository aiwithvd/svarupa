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
