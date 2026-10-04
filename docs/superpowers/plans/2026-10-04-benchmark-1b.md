# Benchmark 1b Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Score Svarupa against hand-checked facts on 11 pinned public repositories, fail CI on regression, and set each language pack's maturity from the scores.

**Architecture:** `scripts/benchmark.py` clones each repo at its pinned SHA into a cache, runs the same pipeline the CLI runs (detect, extract, build, build_lock), turns the lockfile records and resolved call edges into fact keys, and compares them with `benchmark/expected/<repo>.toml`. `benchmark/scores.json` holds the accepted result; `check` fails when a found fact goes missing or a probe starts firing.

**Tech Stack:** Python 3.10 to 3.13, git, pytest, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-10-04-benchmark-design.md` and the parent `docs/superpowers/specs/2026-10-03-system-health-umbrella-design.md`.

## Global Constraints

- Expected facts are drafted by reading the repo source **before** running Svarupa on that repo. After a run, a fact may change only when its own `why` citation is shown wrong by the source; every such change is a ledger ruling and listed in the PR.
- Every `must` fact has `why = "path:line ..."` pointing at the pinned commit; every `must_not` has a reason. At least 15 `must` and 4 `must_not` per repo.
- Nothing from the benchmark repos is committed except the facts and the paths and lines they cite.
- No new runtime dependency. Python 3.10 compatible (TOML via `svarupa.detect.load_toml`).
- `uv run ruff check svarupa tests scripts` and `uv run ruff format --check svarupa tests` pass; `uv run pyright svarupa` passes.
- Branch `feat/benchmark-1b`, one commit per task, PR at the end, never push to `main`, no AI attribution, plain English without em dashes, commit subjects under 72 characters.

## Review Focus

1. A clone that fails half way (network drop): the next run must not reuse the broken directory. Pinned by Task 1 `test_a_failed_checkout_is_retried_from_scratch`.
2. A new expected fact added in a PR: `check` must fail until `accept` records it, so nobody silently lowers the bar. Pinned by Task 1 `test_a_fact_missing_from_the_accepted_scores_is_a_regression`.
3. A resolved call versus a candidate call: only a resolved edge satisfies a `must` call, but a candidate fires a `must_not` probe. Pinned by Task 1 `test_candidates_fire_probes_but_do_not_satisfy_facts`.
4. A repo whose language has no stable evidence: the maturity test must demote, not just promote. Pinned by Task 1 `test_stable_needs_two_repos_at_ninety_percent_with_no_probe`.
5. `why` citing a path that does not exist at the pinned commit: `validate` fails with the repo and fact. Pinned by Task 1 `test_validate_reports_a_citation_to_a_missing_line`.

---

## File Structure

| File | Responsibility |
|---|---|
| `benchmark/corpus.toml` | The 11 repos: name, url, sha, license, languages. |
| `benchmark/expected/<name>.toml` | Expected facts per repo. |
| `benchmark/scores.json` | Accepted scores (written by `accept`). |
| `benchmark/README.md` | How facts are written and how to accept a change. |
| `scripts/benchmark.py` | Load, clone, observe, score, compare, validate, maturity rule, CLI. |
| `tests/test_benchmark.py` | Offline tests with a local git fixture; maturity consistency test. |
| `.github/workflows/ci.yml` | New `benchmark` job. |

---

### Task 1: Benchmark runner, corpus and offline tests

**Files:**
- Create: `benchmark/corpus.toml`, `benchmark/README.md`, `scripts/benchmark.py`, `tests/test_benchmark.py`
- Modify: `.gitignore` (nothing to add: the cache lives outside the repo)

**Interfaces:**
- Produces (used by Tasks 2 to 6): `scripts/benchmark.py` with `Repo`, `Fact`, `Score`, `load_corpus(path) -> list[Repo]`, `load_expected(name, directory) -> tuple[list[Fact], list[Fact]]`, `checkout(repo, cache) -> Path`, `observe(root) -> tuple[set[str], set[str]]` (resolved keys, any keys), `score(name, must, must_not, resolved, seen) -> Score`, `regressions(current: dict[str, Score], accepted: dict[str, dict]) -> list[str]`, `validate_repo(repo, root, directory) -> list[str]`, `stable_languages(corpus, accepted) -> set[str]`, `to_json(scores) -> dict`, CLI `run | check | accept | validate`.

- [ ] **Step 1: Create the branch and the corpus**

```bash
git switch main && git pull --ff-only && git switch -c feat/benchmark-1b
```

`benchmark/corpus.toml`:

```toml
# Public repositories the benchmark scores Svarupa on, each pinned to one
# commit. Changing a sha means re-checking that repo's expected facts in the
# same pull request.

[[repo]]
name = "fastapi-template"
url = "https://github.com/fastapi/full-stack-fastapi-template"
sha = "1762adac607a1b29cfc4da129557780beea71616"
license = "MIT"
languages = ["python", "typescript"]

[[repo]]
name = "microblog"
url = "https://github.com/miguelgrinberg/microblog"
sha = "a975ef64864354867c88e0ed3a17ba7d17dca752"
license = "MIT"
languages = ["python"]

[[repo]]
name = "bakerydemo"
url = "https://github.com/wagtail/bakerydemo"
sha = "c8f8255593c0efcfab5fef2fb19d60895227748a"
license = "BSD-3-Clause"
languages = ["python"]

[[repo]]
name = "nestjs-prisma"
url = "https://github.com/notiz-dev/nestjs-prisma-starter"
sha = "225e5a906865df237a681c1ad228a3ecf2909326"
license = "MIT"
languages = ["typescript"]

[[repo]]
name = "express-boilerplate"
url = "https://github.com/hagopj13/node-express-boilerplate"
sha = "179ae84efec61b14206d0305d941daed6c6d07f9"
license = "MIT"
languages = ["javascript"]

[[repo]]
name = "taxonomy"
url = "https://github.com/shadcn-ui/taxonomy"
sha = "298a8857c7128a0d121e7f699dfd729f23b3966d"
license = "MIT"
languages = ["typescript"]

[[repo]]
name = "gin-realworld"
url = "https://github.com/gothinkster/golang-gin-realworld-example-app"
sha = "626c372d259472148d93303f74aa9b9a1cdcef24"
license = "MIT"
languages = ["go"]

[[repo]]
name = "go-clean-arch"
url = "https://github.com/bxcodec/go-clean-arch"
sha = "e06c6d0cb37069b0ef56e3df67f80ca130a1ab82"
license = "MIT"
languages = ["go"]

[[repo]]
name = "go-clean-template"
url = "https://github.com/evrone/go-clean-template"
sha = "d40c828aa650497453db13ac3d8539c4086b96d9"
license = "MIT"
languages = ["go"]

[[repo]]
name = "petclinic"
url = "https://github.com/spring-projects/spring-petclinic"
sha = "500158f732419217507c7656904b8e6aa1bcc0d6"
license = "Apache-2.0"
languages = ["java"]

[[repo]]
name = "spring-realworld"
url = "https://github.com/gothinkster/spring-boot-realworld-example-app"
sha = "ee17e31aafe733d98c4853c8b9a74d7f2f6c924a"
license = "MIT"
languages = ["java"]
```

- [ ] **Step 2: Write the failing tests**

`tests/test_benchmark.py`:

```python
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
    spec = importlib.util.spec_from_file_location("benchmark", ROOT / "scripts" / "benchmark.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    # Registered first: dataclasses resolve string annotations through it.
    sys.modules["benchmark"] = mod
    spec.loader.exec_module(mod)
    return mod


bm = _load()

FILES = {
    "app/__init__.py": "",
    "app/api.py": "from app import store\n\n\ndef create():\n    return store.save()\n",
    "app/store.py": "def save():\n    return 1\n",
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
record = ["dep", "app", "app"]
why = "app/api.py:1 from app import store"

[[must]]
call = "app/api.py -> app/store.py#save"
why = "app/api.py:5 store.save()"

[[must]]
record = ["datastore", "postgres"]
why = "app/store.py:1 (deliberately absent: a fact Svarupa should miss)"

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
    assert (root / "app" / "api.py").is_file()
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
    assert (bm.checkout(good, cache) / "app" / "store.py").is_file()


def test_scoring_counts_found_missed_and_fired(upstream: tuple[str, str], tmp_path: Path) -> None:
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
        '[[must]]\nrecord = ["dep", "app", "app"]\nwhy = "app/api.py:99 nope"\n'
        '[[must]]\ncall = "x -> y#z"\nwhy = "app/missing.py:1 nope"\n'
        '[[must_not]]\nrecord = ["datastore", "x"]\nwhy = ""\n',
    )
    problems = bm.validate_repo(repo, root, tmp_path / "exp", minimum=(2, 1))
    assert problems == [
        "demo: record:dep\tapp\tapp cites app/api.py:99 past the end of the file",
        "demo: call:x -> y#z cites app/missing.py, which is not in the pinned commit",
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
```

Run: `uv run pytest tests/test_benchmark.py -q`
Expected: collection error, `FileNotFoundError` for `scripts/benchmark.py`.

- [ ] **Step 3: Write the runner**

`scripts/benchmark.py`:

```python
"""Score Svarupa against hand-checked facts on pinned public repositories.

    scripts/benchmark.py run [NAME ...]   score repos and print a table
    scripts/benchmark.py check            score all; exit 1 on any regression
    scripts/benchmark.py accept           score all; write benchmark/scores.json
    scripts/benchmark.py validate         check facts cite real lines; exit 1 if not

Repos are cloned once per commit into $SVARUPA_BENCH_CACHE (default
~/.cache/svarupa-bench). Expected facts are written by reading each repo's
source, never from Svarupa's output: see benchmark/README.md.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from svarupa import __version__
from svarupa.build import build
from svarupa.detect import detect, load_toml
from svarupa.extract import declared_dependencies, extract
from svarupa.lock import build_lock
from svarupa.model import EdgeKind, Resolution

ROOT = Path(__file__).resolve().parents[1]
BENCH = ROOT / "benchmark"
CACHE = Path(os.environ.get("SVARUPA_BENCH_CACHE", Path.home() / ".cache" / "svarupa-bench"))
STABLE_RECALL = 0.9
STABLE_REPOS = 2


class BenchmarkError(Exception):
    pass


@dataclass(frozen=True)
class Repo:
    name: str
    url: str
    sha: str
    license: str
    languages: tuple[str, ...]


@dataclass(frozen=True)
class Fact:
    key: str  # "record:<kind>\t<field>..." or "call:<src> -> <dst>#<name>"
    kind: str  # the record kind, or "call"
    why: str


@dataclass
class Score:
    name: str
    total: int
    missed: list[str]
    fired: list[str]
    by_kind: dict[str, tuple[int, int]]

    @property
    def found(self) -> int:
        return self.total - len(self.missed)

    @property
    def recall(self) -> float:
        return self.found / self.total if self.total else 0.0


def _toml(path: Path) -> dict[str, object]:
    try:
        data = load_toml(path.read_text(encoding="utf8"))
    except OSError as exc:
        raise BenchmarkError(f"cannot read {path}: {exc}") from exc
    if data is None:
        raise BenchmarkError(f"{path} is not valid TOML")
    return data


def load_corpus(path: Path = BENCH / "corpus.toml") -> list[Repo]:
    entries = _toml(path).get("repo", [])
    assert isinstance(entries, list)
    return [
        Repo(e["name"], e["url"], e["sha"], e["license"], tuple(e["languages"]))
        for e in entries
    ]


def _fact(entry: dict[str, object]) -> Fact:
    why = str(entry.get("why", "")).strip()
    if "record" in entry:
        fields = [str(x) for x in entry["record"]]  # type: ignore[union-attr]
        return Fact("record:" + "\t".join(fields), fields[0], why)
    if "call" in entry:
        return Fact("call:" + str(entry["call"]).strip(), "call", why)
    raise BenchmarkError(f"a fact needs `record` or `call`: {entry}")


def load_expected(name: str, directory: Path = BENCH / "expected") -> tuple[list[Fact], list[Fact]]:
    data = _toml(directory / f"{name}.toml")
    must = data.get("must", [])
    must_not = data.get("must_not", [])
    assert isinstance(must, list) and isinstance(must_not, list)
    return [_fact(e) for e in must], [_fact(e) for e in must_not]


def checkout(repo: Repo, cache: Path = CACHE) -> Path:
    """The repo at its pinned commit, cloned once. A marker written only after
    a complete checkout means a half-finished clone is never reused."""
    target = cache / f"{repo.name}@{repo.sha[:12]}"
    done = cache / f"{repo.name}@{repo.sha[:12]}.ok"
    if done.exists() and target.is_dir():
        return target
    shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True)
    git = ["git", "-C", str(target)]
    try:
        for args in (
            ["init", "-q"],
            ["fetch", "-q", "--depth", "1", repo.url, repo.sha],
            ["checkout", "-q", "FETCH_HEAD"],
        ):
            subprocess.run([*git, *args], check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        shutil.rmtree(target, ignore_errors=True)
        raise BenchmarkError(
            f"{repo.name}: cannot check out {repo.sha} from {repo.url}: {exc.stderr.strip()}"
        ) from exc
    done.write_text(repo.sha + "\n", encoding="utf8")
    return target


def observe(root: Path) -> tuple[set[str], set[str]]:
    """Fact keys Svarupa produces: (resolved, any). Runs exactly what the CLI
    runs. Records are facts outright; a call is resolved only when its edge is
    resolved, while any edge, candidates included, can fire a probe."""
    scan = detect(root)
    graph = build(scan, extract(scan, declared_dependencies(scan)), strict=False)
    records = {
        "record:" + "\t".join((r.kind, *r.fields)) for r in build_lock(graph, __version__).lockfile.records
    }
    resolved = set(records)
    seen = set(records)
    for e in graph.edges:
        if e.kind is not EdgeKind.CALLS or "#" not in e.dst:
            continue
        dst_file, dst_qual = e.dst.split("#", 1)
        key = f"call:{e.src.split('#', 1)[0]} -> {dst_file}#{dst_qual.rsplit('.', 1)[-1]}"
        seen.add(key)
        if e.resolution is Resolution.RESOLVED:
            resolved.add(key)
    return resolved, seen


def score(
    name: str, must: list[Fact], must_not: list[Fact], resolved: set[str], seen: set[str]
) -> Score:
    by_kind: dict[str, tuple[int, int]] = {}
    for f in must:
        found, total = by_kind.get(f.kind, (0, 0))
        by_kind[f.kind] = (found + (f.key in resolved), total + 1)
    return Score(
        name,
        len(must),
        sorted(f.key for f in must if f.key not in resolved),
        sorted(f.key for f in must_not if f.key in seen),
        dict(sorted(by_kind.items())),
    )


def to_json(scores: dict[str, Score]) -> dict[str, dict[str, object]]:
    return {
        name: {
            "recall": round(s.recall, 4),
            "found": s.found,
            "total": s.total,
            "missed": s.missed,
            "fired": s.fired,
            "by_kind": {k: list(v) for k, v in s.by_kind.items()},
        }
        for name, s in sorted(scores.items())
    }


def regressions(current: dict[str, Score], accepted: dict[str, dict[str, object]]) -> list[str]:
    out: list[str] = []
    for name, s in sorted(current.items()):
        acc = accepted.get(name)
        if acc is None:
            out.append(f"{name}: no accepted score; run scripts/benchmark.py accept")
            continue
        old_missed = set(acc.get("missed", []))  # type: ignore[arg-type]
        old_fired = set(acc.get("fired", []))  # type: ignore[arg-type]
        out.extend(f"{name}: newly missed {k}" for k in s.missed if k not in old_missed)
        out.extend(f"{name}: probe now fires {k}" for k in s.fired if k not in old_fired)
    return out


def validate_repo(
    repo: Repo,
    root: Path,
    directory: Path = BENCH / "expected",
    minimum: tuple[int, int] = (15, 4),
) -> list[str]:
    must, must_not = load_expected(repo.name, directory)
    problems: list[str] = []
    for f in must:
        cite = f.why.split(" ", 1)[0]
        path, _, line = cite.partition(":")
        target = root / path
        if not path or not line.isdigit():
            problems.append(f"{repo.name}: {f.key} has no path:line citation")
        elif not target.is_file():
            problems.append(f"{repo.name}: {f.key} cites {path}, which is not in the pinned commit")
        elif int(line) > len(target.read_text(encoding="utf8", errors="replace").splitlines()):
            problems.append(f"{repo.name}: {f.key} cites {cite} past the end of the file")
    problems.extend(f"{repo.name}: {f.key} has no reason" for f in must_not if not f.why)
    if len(must) < minimum[0]:
        problems.append(f"{repo.name}: {len(must)} must facts, at least {minimum[0]} needed")
    if len(must_not) < minimum[1]:
        problems.append(
            f"{repo.name}: {len(must_not)} must_not probes, at least {minimum[1]} needed"
        )
    return problems


def stable_languages(corpus: list[Repo], accepted: dict[str, dict[str, object]]) -> set[str]:
    """Languages with at least two repos at >= 90% recall and no fired probe."""
    good: dict[str, int] = {}
    for repo in corpus:
        acc = accepted.get(repo.name)
        if acc is None or acc.get("fired") or float(acc.get("recall", 0)) < STABLE_RECALL:  # type: ignore[arg-type]
            continue
        for lang in repo.languages:
            good[lang] = good.get(lang, 0) + 1
    return {lang for lang, n in good.items() if n >= STABLE_REPOS}


def table(scores: dict[str, Score]) -> str:
    lines = ["| repo | recall | found | probes fired | weakest kind |", "|---|---:|---:|---:|---|"]
    for name, s in sorted(scores.items()):
        weakest = min(s.by_kind.items(), key=lambda kv: kv[1][0] / kv[1][1], default=None)
        weak = f"{weakest[0]} {weakest[1][0]}/{weakest[1][1]}" if weakest else ""
        lines.append(f"| {name} | {s.recall:.0%} | {s.found}/{s.total} | {len(s.fired)} | {weak} |")
    return "\n".join(lines)


def _score_all(names: list[str]) -> dict[str, Score]:
    out: dict[str, Score] = {}
    for repo in load_corpus():
        if names and repo.name not in names:
            continue
        root = checkout(repo)
        problems = validate_repo(repo, root)
        if problems:
            raise BenchmarkError("\n".join(problems))
        must, must_not = load_expected(repo.name)
        out[repo.name] = score(repo.name, must, must_not, *observe(root))
    return out


def main(argv: list[str]) -> int:
    command, names = (argv[0], argv[1:]) if argv else ("", [])
    try:
        if command == "validate":
            problems = [p for r in load_corpus() for p in validate_repo(r, checkout(r))]
            print("\n".join(problems) or "all expected facts cite real lines")
            return 1 if problems else 0
        if command not in ("run", "check", "accept"):
            print(__doc__, file=sys.stderr)
            return 2
        scores = _score_all(names if command == "run" else [])
    except BenchmarkError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    report = table(scores)
    print(report)
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with Path(summary).open("a", encoding="utf8") as fh:
            fh.write("## Benchmark\n\n" + report + "\n")
    if command == "accept":
        (BENCH / "scores.json").write_text(
            json.dumps(to_json(scores), indent=1, ensure_ascii=False) + "\n", encoding="utf8"
        )
        print("wrote benchmark/scores.json")
        return 0
    if command == "check":
        path = BENCH / "scores.json"
        accepted = json.loads(path.read_text(encoding="utf8")) if path.exists() else {}
        found = regressions(scores, accepted)
        for line in found:
            print("REGRESSION " + line)
        return 1 if found else 0
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
```

The `# type: ignore` comments are for readers only (pyright does not check `scripts/`); keep them, they mark the places that trust the TOML shape.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_benchmark.py -q`
Expected: all pass, `test_pack_maturity_follows_the_benchmark` skipped (no scores yet).

If `test_scoring_counts_found_missed_and_fired` misses the `dep app app` record, print `observe(root)` and check the record format; a module depending on itself may not be a record. In that case replace that fact in `EXPECTED` with `["module", "app"]` and adjust the expected `by_kind`, and ledger the ruling.

- [ ] **Step 5: Benchmark README**

`benchmark/README.md`:

```markdown
# Benchmark

Svarupa is scored on the public repositories in `corpus.toml`, each pinned to
one commit, against facts a person checked by reading the source.

## Writing expected facts

`expected/<name>.toml` holds two lists:

- `[[must]]`: facts Svarupa must produce. `record = [...]` is a lockfile
  record (`module`, `dep`, `endpoint`, `datastore`, `service`, `queue`,
  `entrypoint`, `role`, `environment`). `call = "src -> dst#name"` is a
  resolved file-level call. `why` starts with `path:line` in the pinned
  commit and says what that line shows.
- `[[must_not]]`: probes that must not appear, with the reason.

Rules:

1. Read the source first. Write the facts before running Svarupa on the repo.
2. After a run, change a fact only if its own citation is shown wrong by the
   source. Never change a fact because Svarupa disagrees; a miss is a finding.
3. At least 15 `must` and 4 `must_not` per repo.

## Commands

    uv run python scripts/benchmark.py validate   # citations point at real lines
    uv run python scripts/benchmark.py run NAME   # score one repo
    uv run python scripts/benchmark.py check      # what CI runs
    uv run python scripts/benchmark.py accept     # record new scores

`check` fails when a fact that was found goes missing or a probe starts
firing. If that change is intended (or you added facts), run `accept` and
explain why in the pull request.
```

- [ ] **Step 6: Full check and commit**

Run: `uv run ruff check svarupa tests scripts && uv run ruff format --check svarupa tests && uv run pyright svarupa && uv run pytest -q`
Expected: all pass. If ruff flags `scripts/benchmark.py`, fix the lint (formatting `scripts/` is not checked by CI, but run `uv run ruff format scripts/benchmark.py` anyway).

```bash
git add docs/superpowers benchmark scripts/benchmark.py tests/test_benchmark.py
git commit -m "Add the benchmark runner, corpus and plan 1b"
```

---

### Tasks 2 to 5: Expected facts, one language group per task

Each of these four tasks follows the same procedure for its repos:

| Task | Repos | Commit subject |
|---|---|---|
| 2 | fastapi-template, microblog, bakerydemo | `Add benchmark facts for the Python repos` |
| 3 | nestjs-prisma, express-boilerplate, taxonomy | `Add benchmark facts for the TypeScript and JavaScript repos` |
| 4 | gin-realworld, go-clean-arch, go-clean-template | `Add benchmark facts for the Go repos` |
| 5 | petclinic, spring-realworld | `Add benchmark facts for the Java repos` |

**Files per repo:** create `benchmark/expected/<name>.toml`.

**Interfaces:** consumes `scripts/benchmark.py` (Task 1). Produces expected files that `check` and `accept` read.

For each repo in the task, in order:

- [ ] **Step A: Check out the pinned commit (no Svarupa run yet)**

```bash
uv run python -c "import importlib.util,sys;s=importlib.util.spec_from_file_location('b','scripts/benchmark.py');m=importlib.util.module_from_spec(s);sys.modules['b']=m;s.loader.exec_module(m);r=[x for x in m.load_corpus() if x.name=='NAME'][0];print(m.checkout(r))"
```

Expected: a path under `~/.cache/svarupa-bench/NAME@<sha12>`. Call it `$R`.

- [ ] **Step B: Read the source and draft the facts**

Read, in this order, using file reads (never Svarupa output): the README; the manifests (`pyproject.toml`, `requirements*.txt`, `package.json`, `go.mod`, `pom.xml`, `build.gradle*`); `docker-compose*.yml` and env/config files; the entry point; the route/controller layer; the service/business layer; the data layer.

Write `benchmark/expected/NAME.toml` with, at minimum:

- 3 to 6 `module` records for the source directories a person would draw (directories holding application source; never tests, migrations, vendored or generated code).
- 4 to 8 `dep` records between those modules, each cited at the import line that creates it, including at least one in each direction a layered app has (handlers to services, services to data).
- 3 to 8 `endpoint` records where the framework is one Svarupa supports for routes (FastAPI, Flask, Express, NestJS); the record is `["endpoint", "<METHOD> <path as declared>", "<handler module>"]`, cited at the decorator or route call. Skip endpoints for frameworks without route support (Django, Next.js, Gin, Spring): those are recorded as `must` only if the README or spec of Svarupa promises them; otherwise leave them out and note it in the file's top comment.
- `datastore`, `service`, `queue` records for what docker-compose or the code declares (cite the compose line or the client import line).
- 3 to 5 `call` facts for key cross-file calls (handler to service, service to repository), cited at the call line.
- At least 4 `must_not` probes: a datastore the project does not use; a reverse dependency that does not exist (data layer to handler); a test or migration directory as a `module`; an endpoint path that does not exist; for Go/Java, a call that would only exist if a method were confused with a function.

Write a top comment in each file: the repo, what was read, and which Svarupa capabilities are deliberately not expected (for example "Django routes: not supported, not expected").

Record formats must match the lockfile exactly. Check the grammar in `docs/lockfile-grammar.md` and the record producer in `svarupa/lock/build.py` for the field formats (module ids are directory paths relative to the repo root; `dep` is `from to`; endpoint method and path are one field joined by a space).

- [ ] **Step C: Validate the citations**

Run: `uv run python scripts/benchmark.py validate 2>&1 | grep "^NAME:" || echo "NAME ok"`
Expected: `NAME ok`. (Other repos without facts yet print errors; ignore those lines.)

- [ ] **Step D: Only now, score it**

Run: `uv run python scripts/benchmark.py run NAME`
Expected: a table row. Read every missed fact and fired probe:

- If the source proves the fact wrong (the cited line does not show what `why` claims, or the record format was wrong), fix the fact and write a ledger ruling `Task N: Ruling: NAME fact <key> corrected — <what the source shows> — cost if wrong: <...>`.
- Otherwise the miss is a Svarupa finding. Leave the fact. Add a line to the ledger: `Task N: finding: NAME misses <key> — <likely cause>`.

Never edit a fact to match Svarupa's output.

After all repos of the task:

- [ ] **Step E: Commit**

Run: `uv run ruff check svarupa tests scripts && uv run pytest tests/test_benchmark.py -q`
Expected: pass.

```bash
git add benchmark/expected
git commit -m "<commit subject from the table above>"
```

---

### Task 6: Accept scores, set maturity, CI job, pull request

**Files:**
- Create: `benchmark/scores.json` (via `accept`)
- Modify: `svarupa/extract/packs/{python,typescript,javascript,go,java}.py` (maturity, only where the rule says so)
- Modify: `.github/workflows/ci.yml` (new job), `README.md` (benchmark line)

- [ ] **Step 1: Accept the first scores**

Run: `uv run python scripts/benchmark.py validate && uv run python scripts/benchmark.py accept`
Expected: `all expected facts cite real lines`, the score table, `wrote benchmark/scores.json`.

Run: `uv run python scripts/benchmark.py check; echo "exit=$?"`
Expected: the same table and `exit=0`.

- [ ] **Step 2: Watch the maturity test decide**

Run: `uv run pytest tests/test_benchmark.py::test_pack_maturity_follows_the_benchmark -q`
Expected: either pass, or fail listing packs whose label disagrees, for example `{'python': 'stable'}` if Python does not have two repos at 90%.

For each disagreeing pack, change `maturity=Maturity.STABLE` to `Maturity.EXPERIMENTAL` (or the reverse) in that pack file, with no other change. Re-run until it passes. Ledger each change: `Task 6: Ruling: <lang> maturity <old> -> <new> — benchmark: <repos and recalls> — cost if wrong: a wrong label in reports`.

- [ ] **Step 3: CI job**

Append to `.github/workflows/ci.yml` under `jobs:` (same indentation as `lint:`):

```yaml
  # Scores Svarupa on pinned public repositories against hand-checked facts
  # (benchmark/). Fails when a found fact goes missing or a probe fires.
  benchmark:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
        with:
          enable-cache: true
      - run: uv sync --dev
      - uses: actions/cache@v4
        with:
          path: ~/.cache/svarupa-bench
          key: svarupa-bench-${{ hashFiles('benchmark/corpus.toml') }}
      - name: benchmark
        run: uv run python scripts/benchmark.py check
```

- [ ] **Step 4: README**

In `README.md`, in the "Measured, not assumed." paragraph's section (Design principles), add after that paragraph:

```markdown
**Benchmarked.** Every pull request is scored on 11 pinned public
repositories (Python, TypeScript, JavaScript, Go, Java) against facts checked
by reading their source; a change that loses a found fact fails CI. A
language pack is labelled stable only when two of its repositories reach 90%
recall with no false fact. See `benchmark/`.
```

- [ ] **Step 5: Full check, commit, PR**

Run: `uv run ruff check svarupa tests scripts && uv run ruff format --check svarupa tests && uv run pyright svarupa && uv run pytest -q`
Expected: all pass.

```bash
git add -A benchmark svarupa .github/workflows/ci.yml README.md
git commit -m "Accept benchmark scores, set pack maturity and add the CI job"
git push -u origin feat/benchmark-1b
gh pr create --base main --head feat/benchmark-1b --title "Benchmark 1b: score Svarupa on 11 public repos" --body-file -
```

PR body: the score table from `check`, the list of maturity changes, the count of findings (misses) per language, the list of any fact corrections with their reasons, and a note that the `benchmark` check should be added to branch protection once green (`gh api -X POST repos/aiwithvd/svarupa/branches/main/protection/required_status_checks/contexts -f 'contexts[]=benchmark'` run by the maintainer).

Expected: PR URL. Do not merge.
