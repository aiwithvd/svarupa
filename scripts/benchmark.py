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


def load_expected(
    name: str, directory: Path = BENCH / "expected"
) -> tuple[list[Fact], list[Fact]]:
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
        "record:" + "\t".join((r.kind, *r.fields))
        for r in build_lock(graph, __version__).lockfile.records
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
            problems.append(
                f"{repo.name}: {f.key} cites {path}, which is not in the pinned commit"
            )
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
    lines = [
        "| repo | recall | found | probes fired | weakest kind |",
        "|---|---:|---:|---:|---|",
    ]
    for name, s in sorted(scores.items()):
        weakest = min(s.by_kind.items(), key=lambda kv: kv[1][0] / kv[1][1], default=None)
        weak = f"{weakest[0]} {weakest[1][0]}/{weakest[1][1]}" if weakest else ""
        lines.append(
            f"| {name} | {s.recall:.0%} | {s.found}/{s.total} | {len(s.fired)} | {weak} |"
        )
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
