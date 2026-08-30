"""Spike 0b - BLOCKING GATE for P1-2. Throwaway.

Question: does call-edge resolution work well enough to support the sequence
deriver, trace_calls, and impact_of_change?

Import resolution measured 37-47%. Calls are much harder. Every headline
capability lives on call edges, so if this number is bad, scope moves to the
config-and-import-backed diagrams that Spike 0 validated.

Two measurements:
  1. Scorecard: resolved / candidate / known-external / unresolved-unknown
     (three-bin honesty per review finding R2-3, plus a candidate tier per F3)
  2. Chain depth: how many hops can we trace from a detected entry point?
     This is what a sequence diagram actually needs. A 2-hop ceiling means
     two participants, which reads as "the tool does not understand my code".
"""
from __future__ import annotations

import collections
import json
import sys
import sysconfig
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

import tree_sitter_python as tsp
from tree_sitter import Language, Parser

PY = Language(tsp.language())

EXCLUDE_DIRS = {
    ".git", ".venv", "venv", "node_modules", "vendor", "__pycache__",
    "dist", "build", ".tox", ".mypy_cache", ".pytest_cache", "site-packages",
    "migrations", "__generated__", "target", "docs",
}
EXCLUDE_HINTS = ("_test.", ".test.", "_pb2.", "conftest.py", "setup.py")
EXCLUDE_PARTS = {"tests", "test", "testing", "e2e", "fixtures", "examples", "benchmarks"}

STDLIB = set(sys.stdlib_module_names)
BUILTINS = set(dir(__builtins__)) if isinstance(__builtins__, dict) is False else set(__builtins__)
try:
    BUILTINS = set(dir(sys.modules["builtins"]))
except Exception:
    pass


def norm(s: str) -> str:
    return unicodedata.normalize("NFC", s)


@dataclass
class Sym:
    """A definition: function, method, or class."""
    qual: str          # dotted qualified name, e.g. "flask.app.Flask.route"
    name: str          # bare name, e.g. "route"
    kind: str          # function | method | class
    file: str
    line: int
    cls: str | None = None      # enclosing class bare name, if a method
    bases: tuple[str, ...] = ()  # base class bare names, if a class


@dataclass
class CallSite:
    file: str
    line: int
    enclosing: str | None      # qualified name of containing function/method
    enclosing_cls: str | None
    shape: str                 # bare | self | attr | module | super
    name: str                  # the called name (last attribute)
    recv: str | None           # receiver text for attr/module shapes


@dataclass
class Repo:
    root: Path
    files: list[str] = field(default_factory=list)
    syms: list[Sym] = field(default_factory=list)
    by_name: dict[str, list[Sym]] = field(default_factory=dict)
    by_file: dict[str, list[Sym]] = field(default_factory=dict)
    classes: dict[str, Sym] = field(default_factory=dict)
    imports: dict[str, dict[str, str]] = field(default_factory=dict)  # file -> alias -> spec
    calls: list[CallSite] = field(default_factory=list)
    third_party: set[str] = field(default_factory=set)


def walk(root: Path) -> list[str]:
    out = []
    for p in sorted(root.rglob("*.py")):
        if p.is_symlink() or not p.is_file():
            continue
        rel = norm(p.relative_to(root).as_posix())
        parts = rel.split("/")
        if any(x in EXCLUDE_DIRS for x in parts):
            continue
        if any(x in EXCLUDE_PARTS for x in parts[:-1]):
            continue
        if any(h in parts[-1] for h in EXCLUDE_HINTS):
            continue
        try:
            if p.stat().st_size > 2_000_000:
                continue
        except OSError:
            continue
        out.append(rel)
    return out


def txt(src: bytes, n) -> str:
    return src[n.start_byte:n.end_byte].decode("utf8", "replace")


def mod_of(rel: str) -> str:
    m = rel[:-3] if rel.endswith(".py") else rel
    if m.endswith("/__init__"):
        m = m[: -len("/__init__")]
    return m.replace("/", ".")


def parse_file(repo: Repo, rel: str) -> None:
    parser = Parser(PY)
    try:
        src = (repo.root / rel).read_bytes()
    except OSError:
        return
    tree = parser.parse(src)
    mod = mod_of(rel)
    imports: dict[str, str] = {}

    def qual(stack: list[str]) -> str:
        return ".".join([mod, *stack]) if stack else mod

    def visit(n, stack: list[str], cls: str | None, fn: str | None) -> None:
        t = n.type

        if t == "import_statement":
            for c in n.children:
                if c.type == "dotted_name":
                    nm = txt(src, c)
                    imports[nm.split(".")[0]] = nm
                elif c.type == "aliased_import":
                    d = c.child_by_field_name("name")
                    a = c.child_by_field_name("alias")
                    if d is not None and a is not None:
                        imports[txt(src, a)] = txt(src, d)

        elif t == "import_from_statement":
            mnode = n.child_by_field_name("module_name")
            base = txt(src, mnode) if mnode is not None else ""
            for c in n.children:
                if c is mnode:
                    continue
                if c.type == "dotted_name":
                    nm = txt(src, c)
                    imports[nm.split(".")[-1]] = f"{base}.{nm}"
                elif c.type == "aliased_import":
                    d = c.child_by_field_name("name")
                    a = c.child_by_field_name("alias")
                    if d is not None and a is not None:
                        imports[txt(src, a)] = f"{base}.{txt(src, d)}"

        elif t == "class_definition":
            nm_node = n.child_by_field_name("name")
            if nm_node is None:
                return
            nm = txt(src, nm_node)
            bases: list[str] = []
            sup = n.child_by_field_name("superclasses")
            if sup is not None:
                for c in sup.children:
                    if c.type in ("identifier", "attribute"):
                        bases.append(txt(src, c).split(".")[-1])
            s = Sym(qual(stack + [nm]), nm, "class", rel,
                    n.start_point[0] + 1, bases=tuple(bases))
            repo.syms.append(s)
            repo.classes.setdefault(nm, s)
            body = n.child_by_field_name("body")
            if body is not None:
                for c in body.children:
                    visit(c, stack + [nm], nm, fn)
            return

        elif t == "function_definition":
            nm_node = n.child_by_field_name("name")
            if nm_node is None:
                return
            nm = txt(src, nm_node)
            q = qual(stack + [nm])
            repo.syms.append(Sym(q, nm, "method" if cls else "function", rel,
                                 n.start_point[0] + 1, cls=cls))
            body = n.child_by_field_name("body")
            if body is not None:
                for c in body.children:
                    visit(c, stack + [nm], cls, q)
            return

        elif t == "call":
            f = n.child_by_field_name("function")
            if f is not None:
                shape, name, recv = classify(src, f)
                if name:
                    repo.calls.append(CallSite(rel, n.start_point[0] + 1,
                                               fn, cls, shape, name, recv))

        for c in n.children:
            visit(c, stack, cls, fn)

    visit(tree.root_node, [], None, None)
    repo.imports[rel] = imports


def classify(src: bytes, f) -> tuple[str, str, str | None]:
    """What shape is this call? Determines which resolver applies."""
    if f.type == "identifier":
        return "bare", txt(src, f), None
    if f.type == "attribute":
        obj = f.child_by_field_name("object")
        attr = f.child_by_field_name("attribute")
        if attr is None:
            return "attr", "", None
        name = txt(src, attr)
        if obj is None:
            return "attr", name, None
        otext = txt(src, obj)
        if otext == "self":
            return "self", name, "self"
        if obj.type == "call" and txt(src, obj).startswith("super("):
            return "super", name, "super"
        if obj.type == "identifier":
            return "module", name, otext
        return "attr", name, otext
    return "other", "", None


def mro_names(repo: Repo, cls: str, seen: set[str] | None = None) -> list[str]:
    seen = seen or set()
    if cls in seen:
        return []
    seen.add(cls)
    out = [cls]
    s = repo.classes.get(cls)
    if s:
        for b in s.bases:
            out.extend(mro_names(repo, b, seen))
    return out


def resolve(repo: Repo, c: CallSite) -> tuple[str, int]:
    """Return (bin, arity). Bins: resolved | candidate | external | unknown."""
    name = c.name
    imports = repo.imports.get(c.file, {})
    local = repo.by_file.get(c.file, [])

    # known-external: stdlib or a declared third-party dependency
    def is_external(root_name: str) -> bool:
        spec = imports.get(root_name)
        top = (spec or root_name).split(".")[0]
        return top in STDLIB or top in repo.third_party

    if c.shape == "bare":
        if name in BUILTINS and name not in {s.name for s in local}:
            return "external", 1
        hits = [s for s in local if s.name == name and s.cls is None]
        if len(hits) == 1:
            return "resolved", 1
        if len(hits) > 1:
            return "candidate", len(hits)
        if name in imports:
            spec = imports[name]
            top = spec.split(".")[0]
            if top in STDLIB or top in repo.third_party:
                return "external", 1
            g = repo.by_name.get(name, [])
            if len(g) == 1:
                return "resolved", 1
            if len(g) > 1:
                return "candidate", len(g)
            return "unknown", 0
        g = repo.by_name.get(name, [])
        if len(g) == 1:
            return "resolved", 1
        if len(g) > 1:
            return "candidate", len(g)
        return "unknown", 0

    if c.shape in ("self", "super"):
        if not c.enclosing_cls:
            return "unknown", 0
        chain = mro_names(repo, c.enclosing_cls)
        if c.shape == "super":
            chain = chain[1:]
        hits = [s for s in repo.syms
                if s.name == name and s.cls in chain and s.kind == "method"]
        if len(hits) == 1:
            return "resolved", 1
        if len(hits) > 1:
            return "candidate", len(hits)
        # method not found in the known MRO: base class is likely third-party
        return "unknown", 0

    if c.shape == "module":
        recv = c.recv or ""
        if recv in imports:
            spec = imports[recv]
            top = spec.split(".")[0]
            if top in STDLIB or top in repo.third_party:
                return "external", 1
            hits = [s for s in repo.syms
                    if s.name == name and mod_of(s.file).endswith(spec.split(".")[-1])]
            if len(hits) == 1:
                return "resolved", 1
            if len(hits) > 1:
                return "candidate", len(hits)
            return "unknown", 0
        if recv in repo.classes:            # ClassName.method()
            chain = mro_names(repo, recv)
            hits = [s for s in repo.syms if s.name == name and s.cls in chain]
            if len(hits) == 1:
                return "resolved", 1
            if len(hits) > 1:
                return "candidate", len(hits)
        if is_external(recv):
            return "external", 1
        # unknown local variable receiver: fall through to attr handling
        hits = [s for s in repo.syms if s.name == name and s.kind == "method"]
        if len(hits) == 1:
            return "resolved", 1
        if 1 < len(hits) <= 8:
            return "candidate", len(hits)
        return "unknown", len(hits)

    # attr: receiver type unknown (the hard case)
    hits = [s for s in repo.syms if s.name == name and s.kind == "method"]
    if len(hits) == 1:
        return "resolved", 1
    if 1 < len(hits) <= 8:
        return "candidate", len(hits)
    if not hits:
        return "external", 1        # no such method anywhere in repo
    return "unknown", len(hits)     # too ambiguous to be useful


def third_party(root: Path) -> set[str]:
    out: set[str] = set()
    for fn in ("pyproject.toml", "requirements.txt", "setup.cfg"):
        p = root / fn
        if not p.exists():
            continue
        try:
            for ln in p.read_text(errors="replace").splitlines():
                ln = ln.strip().strip('",\'')
                if not ln or ln.startswith("#"):
                    continue
                tok = ln.split("=")[0].split("[")[0].split(">")[0].split("<")[0]
                tok = tok.split(";")[0].strip().replace("-", "_")
                if tok.isidentifier():
                    out.add(tok)
        except OSError:
            pass
    return out


ENTRY_HINTS = ("main", "run", "handle", "dispatch", "__call__", "wsgi_app",
               "handler", "serve", "start", "execute", "process")


def chain_depth(repo: Repo, edges: dict[str, set[str]], start: str,
                limit: int = 12) -> int:
    """Longest simple path from an entry point. This is what sequence needs."""
    best = 0
    stack = [(start, {start}, 0)]
    seen_states = 0
    while stack and seen_states < 20000:
        node, path, d = stack.pop()
        seen_states += 1
        best = max(best, d)
        if d >= limit:
            continue
        for nxt in sorted(edges.get(node, ()))[:12]:
            if nxt not in path:
                stack.append((nxt, path | {nxt}, d + 1))
    return best


def run(target: str) -> dict:
    root = Path(target).resolve()
    repo = Repo(root=root)
    repo.third_party = third_party(root)
    repo.files = walk(root)
    for rel in repo.files:
        parse_file(repo, rel)

    for s in repo.syms:
        repo.by_name.setdefault(s.name, []).append(s)
        repo.by_file.setdefault(s.file, []).append(s)

    bins = collections.Counter()
    by_shape = collections.defaultdict(collections.Counter)
    arities = []
    call_edges: dict[str, set[str]] = {}

    for c in repo.calls:
        b, ar = resolve(repo, c)
        bins[b] += 1
        by_shape[c.shape][b] += 1
        if b == "candidate":
            arities.append(ar)
        if b in ("resolved", "candidate") and c.enclosing:
            tgt = None
            if c.shape in ("self", "super") and c.enclosing_cls:
                chain = mro_names(repo, c.enclosing_cls)
                h = [s for s in repo.syms
                     if s.name == c.name and s.cls in chain and s.kind == "method"]
                if h:
                    tgt = h[0].qual
            else:
                h = repo.by_name.get(c.name, [])
                if h:
                    tgt = h[0].qual
            if tgt:
                call_edges.setdefault(c.enclosing, set()).add(tgt)

    total = sum(bins.values())
    intra = bins["resolved"] + bins["candidate"] + bins["unknown"]

    entries = [s.qual for s in repo.syms
               if s.name in ENTRY_HINTS or s.name.startswith("test_") is False
               and s.name in ENTRY_HINTS]
    entries = sorted({s.qual for s in repo.syms if s.name in ENTRY_HINTS})
    depths = [chain_depth(repo, call_edges, e) for e in entries[:40]]
    depths.sort(reverse=True)

    print(f"\n{'='*70}\n{root.name}  ({len(repo.files)} files, "
          f"{len(repo.syms)} symbols, {total} call sites)\n{'='*70}")
    print(f"{'bin':<22}{'count':>8}{'% all':>9}{'% intra-repo':>15}")
    print("-" * 54)
    for b, label in (("resolved", "resolved (1 target)"),
                     ("candidate", "candidate (N targets)"),
                     ("external", "known-external"),
                     ("unknown", "unresolved-unknown")):
        n = bins[b]
        pi = f"{n/intra*100:>13.1f}%" if intra and b != "external" else " " * 14
        print(f"{label:<22}{n:>8}{n/total*100:>8.1f}%{pi}")
    print("-" * 54)
    pin = (bins["resolved"] + bins["candidate"]) / intra * 100 if intra else 0
    print(f"{'PINNED (res+cand)':<22}{bins['resolved']+bins['candidate']:>8}"
          f"{'':>9}{pin:>13.1f}%")
    if arities:
        arities.sort()
        print(f"  candidate arity: median {arities[len(arities)//2]}, "
              f"max {arities[-1]}")

    print("\nby call shape:")
    for shape in ("bare", "self", "attr", "module", "super"):
        c = by_shape[shape]
        t = sum(c.values())
        if not t:
            continue
        loc = c["resolved"] + c["candidate"] + c["unknown"]
        r = (c["resolved"] + c["candidate"]) / loc * 100 if loc else 0
        print(f"  {shape:<8} n={t:<6} pinned {r:>5.1f}%   "
              f"(res {c['resolved']}, cand {c['candidate']}, "
              f"ext {c['external']}, unk {c['unknown']})")

    print(f"\nchain depth from {len(entries)} entry points "
          f"(sequence diagram viability):")
    if depths:
        print(f"  max {depths[0]}, median {depths[len(depths)//2]}, "
              f"top5 {depths[:5]}")
        print(f"  entry points reaching depth>=3: "
              f"{sum(1 for d in depths if d >= 3)}/{len(depths)}")
    else:
        print("  no entry points detected")

    return {"repo": root.name, "bins": dict(bins), "pinned_pct": pin,
            "depths": depths[:10], "total_calls": total}


if __name__ == "__main__":
    res = [run(t) for t in sys.argv[1:]]
    Path("/tmp/spike-calls.json").write_text(json.dumps(res, indent=1))
