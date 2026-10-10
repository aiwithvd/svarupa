"""The health engine: checks, duplication and SQALE grading."""

from __future__ import annotations

from pathlib import Path

import pytest

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
    # The two fixtures are copies of each other, so a duplicated-block is
    # expected too; this test is about the complexity limit only.
    assert not [
        v
        for v in h.violations
        if v.check == "complex-function" and v.evidence[0].file == "app/at.py"
    ]


def test_long_function_many_parameters_and_deep_nesting(tmp_path: Path) -> None:
    long_body = "".join(f"    x{i} = {i}\n" for i in range(51))
    write(tmp_path, "app/long.py", f"def long():\n{long_body}")
    write(tmp_path, "app/args.py", "def args(a, b, c, d, e, f):\n    return a\n")
    deep = (
        "def deep(a):\n"
        + "".join("    " * (i + 1) + "if a:\n" for i in range(5))
        + "    " * 6
        + "pass\n"
    )
    write(tmp_path, "app/deep.py", deep)
    assert sorted(ids(health(tmp_path))) == ["deep-nesting", "long-function", "many-parameters"]


def test_large_file(tmp_path: Path) -> None:
    write(tmp_path, "app/big.py", "".join(f"v{i} = {i}\n" for i in range(1001)))
    assert ids(health(tmp_path)) == ["large-file"]


def test_large_class_sums_method_complexity(tmp_path: Path) -> None:
    method = (
        "    def m{n}(self, a):\n"
        + "".join(f"        if a == {i}:\n            return {i}\n" for i in range(9))
        + "        return 0\n"
    )
    cls = "class Big:\n" + "".join(method.replace("{n}", str(n)) for n in range(5))
    write(tmp_path, "app/big.py", cls)  # 5 methods x complexity 10 = 50 > 47
    # The five methods are copies, so duplicated-block fires as well.
    big = [v for v in health(tmp_path).violations if v.check == "large-class"]
    assert [v.value for v in big] == [50]


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
    write(
        tmp_path, "app/a.py", f"def a(values, weight):\n    total = 0\n{DUP}    return total\n"
    )
    write(
        tmp_path, "app/b.py", f"def b(values, weight):\n    total = 0\n{DUP}    return total\n"
    )
    dups = [v for v in health(tmp_path).violations if v.check == "duplicated-block"]
    assert len(dups) == 1
    assert {e.file for e in dups[0].evidence} == {"app/a.py", "app/b.py"}
    assert dups[0].value >= 12


def test_import_blocks_and_comments_are_not_duplication(tmp_path: Path) -> None:
    header = "".join(f"import mod{i}\n" for i in range(12)) + "".join(
        f"# note {i}\n" for i in range(12)
    )
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
    assert (h.ncloc, h.debt_minutes, h.debt_ratio, h.rating("maintainability")) == (
        0,
        0,
        0.0,
        "A",
    )


def test_unassessed_areas_have_no_grade(tmp_path: Path) -> None:
    write(tmp_path, "app/m.py", "def f():\n    return 1\n")
    h = health(tmp_path)
    assert set(AREAS) == {"maintainability", "reliability", "security", "performance"}
    assert [h.rating(a) for a in ("reliability", "security", "performance")] == [
        None,
        None,
        None,
    ]


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


def test_cycle_evidence_is_ordered_by_file_and_line(tmp_path: Path) -> None:
    write(tmp_path, "a/x.py", "import os\nfrom b.z import h\nfrom b.y import g\n")
    write(tmp_path, "b/y.py", "def g():\n    return 1\n")
    write(tmp_path, "b/z.py", "from a.x import g\n\n\ndef h():\n    return 2\n")
    cycle = next(v for v in health(tmp_path).violations if v.check == "module-cycle")
    assert [(e.file, e.start_line) for e in cycle.evidence] == [
        ("a/x.py", 2),
        ("a/x.py", 3),
        ("b/z.py", 1),
    ]


LICENSE = (
    "/*\nCopyright 2024 The Authors.\n\nLicensed under the Apache License, Version 2.0 (the\n"
    '"License"); you may not use this file except in compliance with the License.\n'
    "You may obtain a copy of the License at\n\n    http://www.apache.org/licenses/LICENSE-2.0\n\n"
    "Unless required by applicable law or agreed to in writing, software\n"
    'distributed under the License is distributed on an "AS IS" BASIS,\n'
    "WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.\n"
    "See the License for the specific language governing permissions and\n"
    "limitations under the License.\n*/\n\n"
)
GO_IMPORTS = "import (\n" + "".join(f'\t"github.com/acme/pkg{i}"\n' for i in range(11)) + ")\n"
PY_IMPORTS = "from x import (\n" + "".join(f"    name{i},\n" for i in range(11)) + ")\n"
TS_IMPORTS = "import {\n" + "".join(f"  Name{i},\n" for i in range(11)) + "} from './x';\n"
PY_DOC = (
    '"""Module.\n\n'
    + "".join(f"Line {i} of a shared license text.\n" for i in range(11))
    + '"""\n'
)


@pytest.mark.parametrize(
    "ext,head",
    [
        ("go", LICENSE + "package foo\n\n"),
        ("go", "package foo\n\n" + GO_IMPORTS),
        ("py", PY_IMPORTS),
        ("ts", TS_IMPORTS),
        ("py", PY_DOC),
    ],
)
def test_headers_and_import_groups_are_not_duplication(
    tmp_path: Path, ext: str, head: str
) -> None:
    write(tmp_path, f"app/a.{ext}", head + "\n")
    write(tmp_path, f"app/b.{ext}", head + "\n")
    from svarupa.health.duplication import duplicated_blocks

    texts = {
        p: (tmp_path / p).read_text(encoding="utf8") for p in (f"app/a.{ext}", f"app/b.{ext}")
    }
    assert duplicated_blocks(texts) == []
