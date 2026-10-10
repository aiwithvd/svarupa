"""The benchmark runner, tested offline against a local git repository."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "benchmark", ROOT / "scripts" / "benchmark.py"
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    # Registered first: dataclasses resolve string annotations through it.
    sys.modules["benchmark"] = mod
    spec.loader.exec_module(mod)
    return mod


bm = _load()

FILES = {
    "api/handlers.py": "from store.db import save\n\n\ndef create():\n    return save()\n",
    "store/db.py": "def save():\n    return 1\n",
}


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def upstream(tmp_path: Path) -> tuple[str, str]:
    """A local repository the runner can fetch a commit from by sha."""
    repo = tmp_path / "upstream"
    for rel, text in FILES.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf8")
    _git(repo, "init", "-q")
    _git(repo, "config", "uploadpack.allowAnySHA1InWant", "true")
    _git(repo, "add", ".")
    _git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init")
    return repo.as_uri(), _git(repo, "rev-parse", "HEAD")


def _expected(directory: Path, name: str, text: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{name}.toml").write_text(text, encoding="utf8")


EXPECTED = """
[[must]]
record = ["dep", "api", "store"]
why = "api/handlers.py:1 from store.db import save"

[[must]]
call = "api/handlers.py -> store/db.py#save"
why = "api/handlers.py:5 save()"

[[must]]
record = ["datastore", "postgres"]
why = "store/db.py:1 (deliberately absent: a fact Svarupa should miss)"

[[must_not]]
record = ["datastore", "mongodb"]
why = "no Mongo client anywhere"
"""


def test_corpus_is_pinned_and_licensed() -> None:
    corpus = bm.load_corpus(ROOT / "benchmark" / "corpus.toml")
    assert len(corpus) == 11
    for repo in corpus:
        assert len(repo.sha) == 40 and repo.url.startswith("https://github.com/")
        assert repo.license and repo.languages


def test_checkout_fetches_the_pinned_commit(upstream: tuple[str, str], tmp_path: Path) -> None:
    url, sha = upstream
    repo = bm.Repo("demo", url, sha, "MIT", ("python",))
    root = bm.checkout(repo, tmp_path / "cache")
    assert (root / "api" / "handlers.py").is_file()
    assert _git(root, "rev-parse", "HEAD") == sha
    assert bm.checkout(repo, tmp_path / "cache") == root  # cached


def test_a_failed_checkout_is_retried_from_scratch(
    upstream: tuple[str, str], tmp_path: Path
) -> None:
    url, sha = upstream
    cache = tmp_path / "cache"
    broken = bm.Repo("demo", url, "0" * 40, "MIT", ("python",))
    with pytest.raises(bm.BenchmarkError):
        bm.checkout(broken, cache)
    good = bm.Repo("demo", url, sha, "MIT", ("python",))
    assert (bm.checkout(good, cache) / "store" / "db.py").is_file()


def test_scoring_counts_found_missed_and_fired(
    upstream: tuple[str, str], tmp_path: Path
) -> None:
    url, sha = upstream
    root = bm.checkout(bm.Repo("demo", url, sha, "MIT", ("python",)), tmp_path / "cache")
    _expected(tmp_path / "exp", "demo", EXPECTED)
    must, must_not = bm.load_expected("demo", tmp_path / "exp")
    resolved, seen = bm.observe(root)
    s = bm.score("demo", must, must_not, resolved, seen)
    assert (s.found, s.total) == (2, 3)
    assert s.missed == ["record:datastore\tpostgres"]
    assert s.fired == []
    assert s.by_kind == {"call": (1, 1), "datastore": (0, 1), "dep": (1, 1)}


def test_candidates_fire_probes_but_do_not_satisfy_facts() -> None:
    fact = bm.Fact("call:a.py -> b.py#f", "call", "a.py:1")
    s = bm.score("x", [fact], [fact], resolved=set(), seen={"call:a.py -> b.py#f"})
    assert s.missed == ["call:a.py -> b.py#f"]
    assert s.fired == ["call:a.py -> b.py#f"]


def _score(missed: list[str], fired: list[str], total: int = 3) -> object:
    return bm.Score("demo", total, missed, fired, {})


def test_a_newly_missed_fact_is_a_regression() -> None:
    accepted = bm.to_json({"demo": _score([], [])})
    assert bm.regressions({"demo": _score(["record:dep\ta\tb"], [])}, accepted) == [
        "demo: newly missed record:dep\ta\tb"
    ]


def test_a_newly_fired_probe_is_a_regression() -> None:
    accepted = bm.to_json({"demo": _score([], [])})
    assert bm.regressions({"demo": _score([], ["record:datastore\tmongodb"])}, accepted) == [
        "demo: probe now fires record:datastore\tmongodb"
    ]


def test_a_fact_missing_from_the_accepted_scores_is_a_regression() -> None:
    accepted = bm.to_json({"demo": _score([], [], total=2)})
    current = {"demo": _score(["record:dep\tnew\tfact"], [], total=3)}
    assert bm.regressions(current, accepted) == ["demo: newly missed record:dep\tnew\tfact"]


def test_improvements_are_not_regressions() -> None:
    accepted = bm.to_json({"demo": _score(["record:dep\ta\tb"], ["record:datastore\tx"])})
    assert bm.regressions({"demo": _score([], [])}, accepted) == []


def test_a_repo_without_accepted_scores_is_a_regression() -> None:
    assert bm.regressions({"demo": _score([], [])}, {}) == [
        "demo: no accepted score; run scripts/benchmark.py accept"
    ]


def test_validate_reports_a_citation_to_a_missing_line(
    upstream: tuple[str, str], tmp_path: Path
) -> None:
    url, sha = upstream
    repo = bm.Repo("demo", url, sha, "MIT", ("python",))
    root = bm.checkout(repo, tmp_path / "cache")
    _expected(
        tmp_path / "exp",
        "demo",
        '[[must]]\nrecord = ["dep", "api", "store"]\nwhy = "api/handlers.py:99 nope"\n'
        '[[must]]\ncall = "x -> y#z"\nwhy = "api/missing.py:1 nope"\n'
        '[[must_not]]\nrecord = ["datastore", "x"]\nwhy = ""\n',
    )
    problems = bm.validate_repo(repo, root, tmp_path / "exp", minimum=(2, 1))
    assert problems == [
        "demo: record:dep\tapi\tstore cites api/handlers.py:99 past the end of the file",
        "demo: call:x -> y#z cites api/missing.py, which is not in the pinned commit",
        "demo: record:datastore\tx has no reason",
    ]


def test_validate_requires_enough_facts(upstream: tuple[str, str], tmp_path: Path) -> None:
    url, sha = upstream
    repo = bm.Repo("demo", url, sha, "MIT", ("python",))
    root = bm.checkout(repo, tmp_path / "cache")
    _expected(tmp_path / "exp", "demo", EXPECTED)
    assert bm.validate_repo(repo, root, tmp_path / "exp") == [
        "demo: 3 must facts, at least 15 needed",
        "demo: 1 must_not probes, at least 4 needed",
    ]


def test_stable_needs_two_repos_at_ninety_percent_with_no_probe() -> None:
    corpus = [
        bm.Repo("a", "u", "s", "MIT", ("go",)),
        bm.Repo("b", "u", "s", "MIT", ("go",)),
        bm.Repo("c", "u", "s", "MIT", ("java",)),
        bm.Repo("d", "u", "s", "MIT", ("java",)),
    ]
    accepted = bm.to_json(
        {
            "a": bm.Score("a", 10, ["m"], [], {}),  # 90%
            "b": bm.Score("b", 10, [], [], {}),
            "c": bm.Score("c", 10, [], [], {}),
            "d": bm.Score("d", 10, [], ["p"], {}),  # a probe fired
        }
    )
    assert bm.stable_languages(corpus, accepted) == {"go"}


def test_pack_maturity_follows_the_benchmark() -> None:
    from svarupa.extract.packs import PACKS
    from svarupa.extract.packs.model import Maturity

    scores = ROOT / "benchmark" / "scores.json"
    if not scores.exists():
        pytest.skip("no accepted scores yet")
    stable = bm.stable_languages(
        bm.load_corpus(ROOT / "benchmark" / "corpus.toml"),
        json.loads(scores.read_text(encoding="utf8")),
    )
    wrong = {
        p.lang: p.maturity.value
        for p in PACKS
        if (p.maturity is Maturity.STABLE) != (p.lang in stable)
    }
    assert not wrong, f"maturity labels disagree with the benchmark: {wrong}"


def test_changed_expected_facts_require_accept() -> None:
    # A new fact that Svarupa happens to find never shows up as missed, so
    # without this the accepted totals and recall would silently go stale.
    accepted = bm.to_json({"demo": bm.Score("demo", 2, [], [], {}, expected="old")})
    current = {"demo": bm.Score("demo", 3, [], [], {}, expected="new")}
    assert bm.regressions(current, accepted) == [
        "demo: expected facts changed; run scripts/benchmark.py accept"
    ]


def test_expected_fingerprint_follows_the_facts() -> None:
    a = bm.Fact("record:module\tx", "module", "x.py:1")
    b = bm.Fact("record:module\ty", "module", "y.py:1")
    one = bm.score("r", [a], [], set(), set()).expected
    assert one == bm.score("r", [a], [], {"record:module\tx"}, set()).expected
    assert one != bm.score("r", [a, b], [], set(), set()).expected


def test_an_unknown_repo_name_is_an_error() -> None:
    with pytest.raises(bm.BenchmarkError, match="not in the corpus: gin-realwrld"):
        bm._score_all(["gin-realwrld"])


def test_violation_facts_are_parsed_with_their_check_as_kind(tmp_path: Path) -> None:
    _expected(
        tmp_path,
        "demo",
        '[[must]]\nviolation = "complex-function api/handlers.py:4"\nwhy = "api/handlers.py:4 x"\n',
    )
    must, _ = bm.load_expected("demo", tmp_path)
    assert must[0] == bm.Fact(
        "violation:complex-function api/handlers.py:4",
        "violation:complex-function",
        must[0].why,
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


def test_design_facts_are_parsed(tmp_path: Path) -> None:
    _expected(tmp_path, "demo", '[[must]]\ndesign = ". layered"\nwhy = "api/handlers.py:1 x"\n')
    must, _ = bm.load_expected("demo", tmp_path)
    assert must[0].key == "design:. layered" and must[0].kind == "design"


def test_observe_reports_the_chosen_design(tmp_path: Path) -> None:
    from tests.test_design import layered

    layered(tmp_path)
    resolved, _ = bm.observe(tmp_path)
    assert "design:. layered" in resolved


def test_stable_styles_need_two_repos() -> None:
    accepted = {
        "a": {"missed": [], "fired": [], "design_found": ["layered"]},
        "b": {"missed": [], "fired": [], "design_found": ["layered", "clean"]},
    }
    assert bm.stable_styles(accepted) == {"layered"}


def test_style_maturity_follows_the_benchmark() -> None:
    from svarupa.design import CATALOG as STYLES

    scores = ROOT / "benchmark" / "scores.json"
    stable = bm.stable_styles(json.loads(scores.read_text(encoding="utf8")))
    wrong = {s.id: s.maturity for s in STYLES if (s.maturity == "stable") != (s.id in stable)}
    assert not wrong, f"style maturity disagrees with the benchmark: {wrong}"
