"""Spike 0 - throwaway. Validates the riskiest assumption before P1.

Question: does structural module identity stay stable under small code changes,
and does Leiden grouping over it produce something an engineer recognizes?

Extracts imports only. No calls, no frameworks. Python + TypeScript.
"""
from __future__ import annotations

import hashlib
import json
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import igraph as ig
import leidenalg as la
import tree_sitter_python as tsp
import tree_sitter_typescript as tst
from tree_sitter import Language, Parser

PY_LANG = Language(tsp.language())
TS_LANG = Language(tst.language_typescript())

CODE_EXT = {".py": PY_LANG, ".ts": TS_LANG, ".tsx": TS_LANG}

# S5: these dominate the graph and import everything. Excluded from architecture.
EXCLUDE_DIRS = {
    ".git", ".venv", "venv", "node_modules", "vendor", "__pycache__",
    "dist", "build", ".tox", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    "migrations", "__generated__", ".next", "target", "site-packages",
}
EXCLUDE_FILE_HINTS = ("_test.", ".test.", ".spec.", "_pb2.", ".pb.")
EXCLUDE_PATH_PARTS = {"tests", "test", "testing", "e2e", "fixtures", "conftest.py"}

MAX_FILE_BYTES = 2_000_000


def norm(p: str) -> str:
    """F4: macOS returns NFD, Linux NFC. Normalize at the boundary."""
    return unicodedata.normalize("NFC", p)


@dataclass(frozen=True)
class FileRec:
    rel: str      # repo-relative, posix, NFC
    module: str   # structural module id (F2: directory/package based)
    lang: str


def structural_module(rel: str) -> str:
    """F2: identity comes from declared structure, not discovered communities.

    A module is the directory path. This only changes when a file actually
    moves, which is the stability property the lockfile needs.
    """
    parts = rel.split("/")
    return "/".join(parts[:-1]) if len(parts) > 1 else "."


def is_excluded(rel: str) -> bool:
    parts = rel.split("/")
    if any(p in EXCLUDE_DIRS for p in parts):
        return True
    if any(p in EXCLUDE_PATH_PARTS for p in parts[:-1]):
        return True
    name = parts[-1]
    if name in EXCLUDE_PATH_PARTS:
        return True
    return any(h in name for h in EXCLUDE_FILE_HINTS)


def walk(root: Path) -> list[FileRec]:
    out: list[FileRec] = []
    for p in sorted(root.rglob("*")):
        if not p.is_file() or p.is_symlink():
            continue
        if p.suffix not in CODE_EXT:
            continue
        try:
            rel = norm(p.relative_to(root).as_posix())
        except ValueError:
            continue
        if is_excluded(rel):
            continue
        try:
            if p.stat().st_size > MAX_FILE_BYTES:
                continue
        except OSError:
            continue
        out.append(FileRec(rel=rel, module=structural_module(rel), lang=p.suffix))
    return sorted(out, key=lambda f: f.rel)


IMPORT_NODES = {
    "import_statement", "import_from_statement",           # python
    "import_declaration", "export_statement",              # ts
}


def extract_imports(root: Path, f: FileRec) -> list[tuple[str, int]]:
    """Return (raw_import_text, line). Evidence is the line, per the design."""
    lang = CODE_EXT[f.lang]
    parser = Parser(lang)
    try:
        src = (root / f.rel).read_bytes()
    except OSError:
        return []
    tree = parser.parse(src)
    found: list[tuple[str, int]] = []
    stack = [tree.root_node]
    while stack:
        n = stack.pop()
        if n.type in IMPORT_NODES:
            text = src[n.start_byte:n.end_byte].decode("utf8", "replace")
            found.append((" ".join(text.split())[:200], n.start_point[0] + 1))
        stack.extend(n.children)
    return sorted(found)


def _hit(cand: str, files: set[str]) -> str | None:
    """Map a dotted/slashed target to a real FILE in the repo."""
    for suffix in (".py", "/__init__.py", ".ts", ".tsx", "/index.ts", "/index.tsx"):
        if (c := cand + suffix) in files:
            return c
    return cand if cand in files else None


def resolve_python(imp: str, f: FileRec, files: set[str], roots: list[str]) -> str | None:
    """Resolve to a FILE. Directory granularity loses all intra-package edges."""
    toks = imp.split()
    if len(toks) < 2:
        return None
    if toks[0] == "from":
        target = toks[1]
    elif toks[0] == "import":
        target = toks[1].split(",")[0]
    else:
        return None

    if target.startswith("."):
        up = len(target) - len(target.lstrip("."))
        rest = target.lstrip(".").replace(".", "/")
        base = f.rel.split("/")[:-1]                    # dir of the importing file
        anchor = base[: len(base) - (up - 1)] if up > 1 else base
        cand = "/".join([*anchor, rest]) if rest else "/".join(anchor)
        return _hit(cand, files)

    dotted = target.replace(".", "/")
    # try each source root (src/, ., pkg parents) so `import flask.json` resolves
    for r in roots:
        cand = f"{r}/{dotted}" if r else dotted
        while cand:
            if (h := _hit(cand, files)):
                return h
            cand = "/".join(cand.split("/")[:-1])
            if r and not cand.startswith(r):
                break
    return None


def resolve_ts(imp: str, f: FileRec, files: set[str], roots: list[str]) -> str | None:
    spec = None
    for q in ("'", '"'):
        if q in imp:
            spec = imp.split(q)[1]
            break
    if not spec or not spec.startswith("."):
        return None
    base = f.rel.split("/")[:-1]
    parts = spec.split("/")
    cur = list(base)
    for p in parts:
        if p == ".":
            continue
        if p == "..":
            cur = cur[:-1]
        else:
            cur.append(p)
    return _hit("/".join(cur), files)


def source_roots(files: list[FileRec]) -> list[str]:
    """Directories that look like import roots (contain a top-level package)."""
    roots = {""}
    for f in files:
        parts = f.rel.split("/")
        for i in range(len(parts) - 1):
            if parts[i] in ("src", "lib", "app", "packages"):
                roots.add("/".join(parts[: i + 1]))
    return sorted(roots, key=len, reverse=True)


def build(root: Path):
    files = walk(root)
    fileset = {f.rel for f in files}
    roots = source_roots(files)
    mod_of = {f.rel: f.module for f in files}

    file_edges: dict[tuple[str, str], int] = {}
    stats = {"files": len(files), "imports": 0, "resolved": 0, "external": 0}

    for f in files:
        for imp, _line in extract_imports(root, f):
            stats["imports"] += 1
            tgt = (resolve_python(imp, f, fileset, roots) if f.lang == ".py"
                   else resolve_ts(imp, f, fileset, roots))
            if tgt and tgt != f.rel:
                stats["resolved"] += 1
                file_edges[(f.rel, tgt)] = file_edges.get((f.rel, tgt), 0) + 1
            elif not tgt:
                stats["external"] += 1

    # aggregate file-level edges up to module level for the lockfile
    edges: dict[tuple[str, str], int] = {}
    for (a, b), w in file_edges.items():
        ma, mb = mod_of.get(a), mod_of.get(b)
        if ma and mb and ma != mb:
            edges[(ma, mb)] = edges.get((ma, mb), 0) + w
    stats["file_edges"] = len(file_edges)
    return files, edges, stats, file_edges


def cluster(modules: list[str], edges: dict[tuple[str, str], int], seed: int = 42):
    """Leiden for PRESENTATION only (F2). Never reaches the lockfile."""
    idx = {m: i for i, m in enumerate(modules)}
    # canonical ordering is mandatory for reproducibility
    el = sorted({(min(idx[a], idx[b]), max(idx[a], idx[b]))
                 for (a, b) in edges if a in idx and b in idx})
    g = ig.Graph(n=len(modules), edges=el)
    part = la.find_partition(g, la.ModularityVertexPartition, seed=seed)
    return {modules[i]: c for i, c in enumerate(part.membership)}


def lockfile(modules: list[str], edges: dict[tuple[str, str], int]) -> str:
    """F1: facts only. No line numbers. No community ids."""
    lines = ["# svarupa-spike", "# schema 0"]
    dep: dict[str, set[str]] = {m: set() for m in modules}
    for (a, b) in edges:
        if a in dep:
            dep[a].add(b)
    for m in sorted(modules):
        d = ", ".join(sorted(dep[m]))
        lines.append(f"module {m}" + (f" -> {d}" if d else ""))
    return "\n".join(lines) + "\n"


def main(target: str) -> None:
    root = Path(target).resolve()
    files, edges, stats, file_edges = build(root)
    modules = sorted({f.module for f in files})
    comms = cluster(modules, edges)
    lock = lockfile(modules, edges)

    ncom = len(set(comms.values()))
    rate = stats["resolved"] / stats["imports"] * 100 if stats["imports"] else 0.0
    print(f"\n=== {root.name} ===")
    print(f"files {stats['files']}  modules {len(modules)}  "
          f"file-edges {stats['file_edges']}  module-edges {len(edges)}  "
          f"communities {ncom}")
    print(f"imports {stats['imports']}  intra-repo-resolved {stats['resolved']} "
          f"({rate:.1f}%)  external {stats['external']}")
    print(f"lockfile sha256 {hashlib.sha256(lock.encode()).hexdigest()[:16]}  "
          f"{len(lock)} bytes")

    groups: dict[int, list[str]] = {}
    for m, c in sorted(comms.items()):
        groups.setdefault(c, []).append(m)
    for c, ms in sorted(groups.items(), key=lambda kv: -len(kv[1]))[:8]:
        print(f"  community {c}: {', '.join(ms[:6])}"
              + (f" (+{len(ms)-6})" if len(ms) > 6 else ""))

    Path(f"/tmp/spike-{root.name}.lock").write_text(lock)
    Path(f"/tmp/spike-{root.name}.json").write_text(json.dumps(
        {"modules": modules, "edges": {f"{a}|{b}": v for (a, b), v in sorted(edges.items())},
         "communities": comms, "stats": stats}, indent=1, sort_keys=True))


if __name__ == "__main__":
    for t in sys.argv[1:]:
        main(t)
