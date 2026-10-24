"""svarupa check: what a change added, matched so line shifts never count."""

from __future__ import annotations

from svarupa.check import Finding, Snapshot, compare

RATINGS = (
    ("maintainability", "A"),
    ("reliability", "A"),
    ("security", "A"),
    ("performance", "A"),
)


def f(
    check: str = "many-parameters",
    line: int = 1,
    symbol: str | None = "a.py#m.f",
    message: str = "f has 6 parameters (limit 5)",
    file: str = "a.py",
) -> Finding:
    return Finding(check, file, line, symbol, message)


def snap(*found: Finding, ratings=RATINGS) -> Snapshot:
    return Snapshot(tuple(found), ratings)


def test_a_moved_finding_is_not_new() -> None:
    d = compare(snap(f(line=1)), snap(f(line=40)))
    assert d.new == () and d.fixed == ()


def test_a_second_copy_is_new_and_a_removed_one_is_fixed() -> None:
    d = compare(snap(f()), snap(f(), f(line=9)))
    assert [x.line for x in d.new] == [9]
    d = compare(snap(f(), f(check="deep-nesting")), snap(f()))
    assert [x.check for x in d.fixed] == ["deep-nesting"]


def test_digits_in_messages_do_not_break_matching() -> None:
    base = f(check="large-file", symbol=None, message="a.py has 1200 lines (limit 1000)")
    head = f(check="large-file", symbol=None, message="a.py has 1300 lines (limit 1000)")
    assert compare(snap(base), snap(head)).new == ()


def test_a_grade_drop_fails_alone() -> None:
    worse = (
        ("maintainability", "A"),
        ("reliability", "A"),
        ("security", "C"),
        ("performance", "A"),
    )
    d = compare(snap(), snap(ratings=worse))
    assert d.new == () and d.worse() == ["security"] and d.failed("minor")
    assert d.grades == (("security", "A", "C"),)


def test_a_newly_graded_area_is_not_worse() -> None:
    before = (
        ("maintainability", "A"),
        ("reliability", None),
        ("security", "A"),
        ("performance", None),
    )
    d = compare(snap(ratings=before), snap())
    assert d.worse() == [] and not d.failed("minor")


def test_min_severity_ignores_small_new_findings() -> None:
    d = compare(snap(), snap(f()))  # many-parameters is minor
    assert d.failed("minor") and not d.failed("major")


def test_json_and_text_say_the_same() -> None:
    d = compare(snap(f(check="deep-nesting")), snap(f()))
    j = d.to_json(d.failed("minor"))
    assert j["failed"] is True and j["new"][0]["check"] == "many-parameters"
    assert j["fixed"][0]["check"] == "deep-nesting"
    text = d.render()
    assert text.startswith("1 new, 1 fixed")
    assert "a.py:1 many-parameters: f has 6 parameters (limit 5)" in text
    assert "fix: Group the values that travel together" in text


import json  # noqa: E402
import shutil  # noqa: E402
import subprocess  # noqa: E402
from pathlib import Path  # noqa: E402

import pytest  # noqa: E402

from svarupa.cli import main  # noqa: E402
from tests.test_health import write  # noqa: E402

CLEAN = "def ok(a):\n    return a\n"
SIX = "def six(a, b, c, d, e, f):\n    return a\n"


def git_repo(root: Path) -> None:
    if not shutil.which("git"):
        pytest.skip("git is not installed")

    def git(*args: str) -> None:
        subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)

    git("init", "-q")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "t")
    git("config", "commit.gpgsign", "false")
    write(root, "app/m.py", CLEAN)
    git("add", ".")
    git("commit", "-q", "-m", "base")


def worktrees(root: Path) -> int:
    out = subprocess.run(
        ["git", "-C", str(root), "worktree", "list"], capture_output=True, text=True, check=True
    )
    return len(out.stdout.strip().splitlines())


def test_check_against_a_graph_json(tmp_path: Path, capsys) -> None:
    write(tmp_path, "app/m.py", CLEAN)
    assert main([str(tmp_path)]) == 0
    base = tmp_path / "base.json"
    shutil.copy(tmp_path / ".svarupa" / "graph.json", base)
    write(tmp_path, "app/n.py", SIX)
    capsys.readouterr()
    assert main(["check", str(tmp_path), "--base", str(base), "--json"]) == 3
    out = json.loads(capsys.readouterr().out)
    assert [v["check"] for v in out["new"]] == ["many-parameters"] and out["failed"] is True


def test_check_against_a_git_ref(tmp_path: Path, capsys) -> None:
    git_repo(tmp_path)
    write(tmp_path, "app/m.py", CLEAN + "\n\n" + SIX)
    assert main(["check", str(tmp_path), "--base", "HEAD"]) == 3
    out = capsys.readouterr().out
    assert out.startswith("1 new, 0 fixed") and "fix: Group the values" in out
    assert worktrees(tmp_path) == 1


def test_check_with_no_change_passes(tmp_path: Path, capsys) -> None:
    git_repo(tmp_path)
    assert main(["check", str(tmp_path), "--base", "HEAD"]) == 0
    assert capsys.readouterr().out.startswith("0 new, 0 fixed")


def test_check_in_a_subfolder_of_the_checkout(tmp_path: Path, capsys) -> None:
    git_repo(tmp_path)
    write(tmp_path, "svc/m.py", SIX)
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-q", "-m", "svc"], check=True)
    write(tmp_path, "app/n.py", SIX)
    assert main(["check", str(tmp_path / "app"), "--base", "HEAD", "--json"]) == 3
    out = json.loads(capsys.readouterr().out)
    assert [(v["check"], v["file"]) for v in out["new"]] == [("many-parameters", "n.py")]
    assert out["fixed"] == []


def test_unknown_ref_exits_1_and_leaves_no_worktree(tmp_path: Path, capsys) -> None:
    git_repo(tmp_path)
    assert main(["check", str(tmp_path), "--base", "no-such-ref"]) == 1
    assert "unknown ref" in capsys.readouterr().err
    assert worktrees(tmp_path) == 1


def test_a_base_without_health_exits_1(tmp_path: Path, capsys) -> None:
    write(tmp_path, "app/m.py", CLEAN)
    base = tmp_path / "old.json"
    base.write_text(json.dumps({"schema": 2, "nodes": []}), encoding="utf8")
    assert main(["check", str(tmp_path), "--base", str(base)]) == 1
    assert "no health" in capsys.readouterr().err


def test_an_excepted_violation_is_not_new(tmp_path: Path) -> None:
    git_repo(tmp_path)
    write(tmp_path, "app/n.py", SIX)
    write(
        tmp_path,
        ".svarupa/design.yaml",
        "units: {}\nexceptions:\n  - rule: many-parameters\n    at: app/n.py\n    reason: generated adapter\n",
    )
    assert main(["check", str(tmp_path), "--base", "HEAD"]) == 0


def test_min_severity_major_ignores_a_new_minor(tmp_path: Path) -> None:
    git_repo(tmp_path)
    write(tmp_path, "app/n.py", SIX)
    assert main(["check", str(tmp_path), "--base", "HEAD", "--min-severity", "major"]) == 0


@pytest.mark.parametrize(
    "health",
    [
        [],
        {"violations": ["x"]},
        {"violations": [{"message": "m"}]},
        {
            "violations": [
                {"check": "many-parameters", "evidence": [{"file": "a.py", "start_line": "x"}]}
            ]
        },
        {"violations": [], "ratings": {"security": "Z"}},
    ],
)
def test_a_malformed_base_exits_1_with_a_message(tmp_path: Path, capsys, health) -> None:
    write(tmp_path, "app/m.py", CLEAN)
    base = tmp_path / "bad.json"
    base.write_text(json.dumps({"schema": 2, "health": health}), encoding="utf8")
    assert main(["check", str(tmp_path), "--base", str(base)]) == 1
    assert "malformed" in capsys.readouterr().err


@pytest.mark.parametrize("content", ["[]", "null", '"x"'])
def test_a_base_that_is_not_an_object_exits_1(tmp_path: Path, capsys, content) -> None:
    write(tmp_path, "app/m.py", CLEAN)
    base = tmp_path / "bad.json"
    base.write_text(content, encoding="utf8")
    assert main(["check", str(tmp_path), "--base", str(base)]) == 1
    assert "malformed" in capsys.readouterr().err
