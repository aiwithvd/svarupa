"""Spike 0 decision gate: does identity survive small code changes?

Applies synthetic PRs to a repo and measures, for each:
  - lockfile churn (structural module identity)  <- must be ~0 for intra-module edits
  - Leiden community assignment flips            <- expected to be unstable (F2)

If structural identity churns on intra-module edits, the lockfile model is wrong.
If Leiden flips a lot, that confirms why it must stay presentation-only.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from probe import build, cluster, lockfile  # noqa: E402


def snapshot(root: Path):
    files, edges, stats, _fe = build(root)
    modules = sorted({f.module for f in files})
    return lockfile(modules, edges), cluster(modules, edges), stats


def churn(a: str, b: str) -> int:
    la_, lb = a.splitlines(), b.splitlines()
    return len(set(la_) ^ set(lb))


def flips(a: dict, b: dict) -> tuple[int, int]:
    """Community assignment changes, ignoring pure id relabeling."""
    common = sorted(set(a) & set(b))
    if not common:
        return 0, 0
    # canonical: map each community to its lexicographically smallest member
    def canon(d):
        rep: dict[int, str] = {}
        for m in sorted(d):
            rep.setdefault(d[m], m)
        return {m: rep[d[m]] for m in d}
    ca, cb = canon(a), canon(b)
    return sum(1 for m in common if ca[m] != cb[m]), len(common)


PERTURBATIONS = {
    "rename-local-var": lambda p: _edit(p, "*.py", lambda s: s.replace(
        "    result =", "    outcome =").replace("    return result", "    return outcome")),
    "add-docstring": lambda p: _edit(p, "*.py", lambda s: s.replace(
        "\ndef ", '\ndef ', 1) if False else _add_docstring(s)),
    "reformat-blank-lines": lambda p: _edit(p, "*.py", lambda s: s.replace("\n\n\n", "\n\n\n\n")),
    "add-new-file-in-existing-module": lambda p: _add_file(p),
    "add-cross-module-import": lambda p: _add_import(p),
}


def _add_docstring(s: str) -> str:
    lines = s.splitlines(keepends=True)
    for i, ln in enumerate(lines):
        if ln.startswith("def ") and i + 1 < len(lines):
            lines.insert(i + 1, '    """Spike-added docstring."""\n')
            break
    return "".join(lines)


def _edit(root: Path, glob: str, fn) -> str:
    targets = sorted(p for p in root.rglob(glob)
                     if ".git" not in p.parts and "test" not in str(p).lower())
    for p in targets[:6]:
        try:
            s = p.read_text(encoding="utf8")
        except (OSError, UnicodeDecodeError):
            continue
        n = fn(s)
        if n != s:
            p.write_text(n, encoding="utf8")
            return f"edited {p.name}"
    return "no-op"


def _add_file(root: Path) -> str:
    pkgs = sorted({p.parent for p in root.rglob("*.py")
                   if ".git" not in p.parts and "test" not in str(p).lower()})
    if not pkgs:
        return "no-op"
    tgt = pkgs[len(pkgs) // 2] / "spike_new_helper.py"
    tgt.write_text("import os\n\n\ndef helper():\n    return os.getcwd()\n")
    return f"added {tgt.relative_to(root)}"


def _add_import(root: Path) -> str:
    pys = sorted(p for p in root.rglob("*.py")
                 if ".git" not in p.parts and "test" not in str(p).lower()
                 and p.stat().st_size > 500)
    if len(pys) < 2:
        return "no-op"
    src, dst = pys[0], pys[-1]
    try:
        rel = dst.relative_to(root).with_suffix("").as_posix().replace("/", ".")
    except ValueError:
        return "no-op"
    s = src.read_text(encoding="utf8")
    src.write_text(f"import {rel}  # spike cross-module import\n{s}")
    return f"{src.name} -> {rel}"


def run(repo: str) -> None:
    origin = Path(repo).resolve()
    print(f"\n{'='*66}\n{origin.name}\n{'='*66}")

    with tempfile.TemporaryDirectory() as td:
        base = Path(td) / "base"
        shutil.copytree(origin, base, symlinks=True,
                        ignore=shutil.ignore_patterns(".git"))
        lock0, com0, stats0 = snapshot(base)
        print(f"baseline: {stats0['files']} files, {len(lock0.splitlines())} lock lines, "
              f"{len(set(com0.values()))} communities")
        print(f"\n{'perturbation':<34} {'lock churn':>11} {'leiden flips':>14}")
        print("-" * 62)

        for name, fn in PERTURBATIONS.items():
            with tempfile.TemporaryDirectory() as td2:
                head = Path(td2) / "head"
                shutil.copytree(base, head, symlinks=True)
                note = fn(head)
                lock1, com1, _ = snapshot(head)
                c = churn(lock0, lock1)
                f, tot = flips(com0, com1)
                pct = f / tot * 100 if tot else 0
                mark = "OK " if c == 0 else "!! "
                print(f"{mark}{name:<31} {c:>8} lines {f:>6}/{tot} ({pct:.0f}%)")
                if note != "no-op":
                    print(f"   └─ {note}")


if __name__ == "__main__":
    for r in sys.argv[1:]:
        run(r)
