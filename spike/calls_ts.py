"""Spike 0c - TypeScript call + import resolution. Throwaway.

Second-riskiest assumption after the Python call gate: TS ships in P1 and was
never exercised. Same methodology as calls.py so the numbers are comparable.

TS-specific things Python did not have:
  - tsconfig `paths` / `baseUrl` aliases (measured separately: ignoring them
    would silently score every aliased intra-repo import as "external")
  - barrel re-exports via index.ts (the index.ts analogue of R2-4's __init__.py)
  - `this.x` where x is a constructor-injected, type-annotated field. NestJS
    DI is explicit and typed, unlike FastAPI's Depends(), so this may resolve
    far better than Python's equivalent.
"""
from __future__ import annotations

import collections
import json
import re
import sys
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

import tree_sitter_typescript as tst
from tree_sitter import Language, Parser

TS = Language(tst.language_typescript())
TSX = Language(tst.language_tsx())

EXCLUDE_DIRS = {
    ".git", "node_modules", "dist", "build", "out", ".next", "coverage",
    "vendor", "__generated__", "generated", ".turbo", "docs", "examples",
}
EXCLUDE_HINTS = (".test.", ".spec.", ".d.ts", ".config.", ".stories.")
EXCLUDE_PARTS = {"test", "tests", "__tests__", "e2e", "fixtures", "benchmarks"}

BUILTIN_OBJS = {
    "console", "JSON", "Math", "Object", "Array", "String", "Number", "Boolean",
    "Promise", "Date", "RegExp", "Map", "Set", "WeakMap", "WeakSet", "Symbol",
    "Reflect", "Proxy", "process", "Buffer", "globalThis", "window", "document",
    "localStorage", "navigator", "crypto", "performance", "URL", "Error",
}
BUILTIN_FNS = {
    "require", "parseInt", "parseFloat", "isNaN", "String", "Number", "Boolean",
    "Array", "Object", "Symbol", "BigInt", "encodeURIComponent", "decodeURIComponent",
    "setTimeout", "setInterval", "clearTimeout", "clearInterval", "fetch",
    "structuredClone", "queueMicrotask", "atob", "btoa",
}


def norm(s: str) -> str:
    return unicodedata.normalize("NFC", s)


@dataclass
class Sym:
    qual: str
    name: str
    kind: str          # function | method | class | interface
    file: str
    line: int
    cls: str | None = None
    bases: tuple[str, ...] = ()


@dataclass
class CallSite:
    file: str
    line: int
    enclosing: str | None
    enclosing_cls: str | None
    shape: str         # bare | this | attr | module | super
    name: str
    recv: str | None


@dataclass
class Repo:
    root: Path
    files: list[str] = field(default_factory=list)
    syms: list[Sym] = field(default_factory=list)
    by_name: dict[str, list[Sym]] = field(default_factory=dict)
    by_file: dict[str, list[Sym]] = field(default_factory=dict)
    classes: dict[str, Sym] = field(default_factory=dict)
    imports: dict[str, dict[str, str]] = field(default_factory=dict)
    field_types: dict[str, dict[str, str]] = field(default_factory=dict)  # cls -> field -> type
    calls: list[CallSite] = field(default_factory=list)
    deps: set[str] = field(default_factory=set)
    aliases: dict[str, str] = field(default_factory=dict)   # tsconfig paths
    import_stats: collections.Counter = field(default_factory=collections.Counter)


def walk(root: Path) -> list[str]:
    out = []
    for p in sorted(root.rglob("*")):
        if p.suffix not in (".ts", ".tsx") or p.is_symlink() or not p.is_file():
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


def _strip_jsonc(raw: str) -> str:
    """Strip // and /* */ comments, string-aware.

    A regex cannot do this. tsconfig `paths` values always contain glob
    patterns like "@/*": ["src/*"], and the `/*` inside those strings makes
    a naive block-comment regex eat the rest of the file. Measured: this
    silently produced 0 aliases on a real Electron project, which scored
    241 intra-repo imports as unresolved.
    """
    out: list[str] = []
    i, n = 0, len(raw)
    in_str = False
    while i < n:
        ch = raw[i]
        if in_str:
            out.append(ch)
            if ch == "\\" and i + 1 < n:
                out.append(raw[i + 1])
                i += 2
                continue
            if ch == '"':
                in_str = False
            i += 1
            continue
        if ch == '"':
            in_str = True
            out.append(ch)
            i += 1
            continue
        if ch == "/" and i + 1 < n:
            nxt = raw[i + 1]
            if nxt == "/":
                while i < n and raw[i] != "\n":
                    i += 1
                continue
            if nxt == "*":
                i += 2
                while i + 1 < n and not (raw[i] == "*" and raw[i + 1] == "/"):
                    i += 1
                i += 2
                continue
        out.append(ch)
        i += 1
    return "".join(out)


def _read_jsonc(p: Path) -> dict | None:
    try:
        raw = p.read_text(errors="replace")
    except OSError:
        return None
    s = _strip_jsonc(raw)
    s = re.sub(r",(\s*[}\]])", r"\1", s)   # trailing commas
    try:
        return json.loads(s)
    except Exception:
        return None


def load_tsconfig(root: Path) -> tuple[dict[str, str], set[str]]:
    """Follow `extends` and `references`.

    A root tsconfig with `"files": []` plus `references` is the standard
    project-references layout; the aliases live in the referenced configs.
    Reading only the root scores every aliased import as unresolved
    (measured: munder-difflin, 241 specifiers).
    """
    aliases: dict[str, str] = {}
    seen: set[Path] = set()
    queue = [root / n for n in ("tsconfig.json", "tsconfig.base.json")]
    queue += [p for p in root.glob("tsconfig.*.json")]
    queue += [p for p in root.glob("*/tsconfig.json")]
    queue += [p for p in root.glob("packages/*/tsconfig.json")]

    while queue:
        p = queue.pop(0)
        try:
            p = p.resolve()
        except OSError:
            continue
        if p in seen or not p.exists():
            continue
        seen.add(p)
        cfg = _read_jsonc(p)
        if cfg is None:
            continue

        for ref in cfg.get("references") or []:
            if isinstance(ref, dict) and ref.get("path"):
                rp = (p.parent / str(ref["path"])).resolve()
                queue.append(rp / "tsconfig.json" if rp.is_dir() else rp)
        ext = cfg.get("extends")
        for e in ([ext] if isinstance(ext, str) else (ext or [])):
            if isinstance(e, str) and e.startswith("."):
                ep = (p.parent / e).resolve()
                queue.append(ep if ep.suffix else ep.with_suffix(".json"))

        co = cfg.get("compilerOptions", {}) or {}
        # alias targets are relative to the config that declares them
        try:
            prefix = p.parent.relative_to(root).as_posix()
        except ValueError:
            prefix = ""
        prefix = "" if prefix == "." else prefix
        base = co.get("baseUrl", None)
        for k, v in (co.get("paths") or {}).items():
            if isinstance(v, list) and v:
                tgt = str(v[0]).rstrip("/*").lstrip("./")
                if base and base not in (".", "./"):
                    tgt = f"{base.strip('./')}/{tgt}".strip("/")
                if prefix:
                    tgt = f"{prefix}/{tgt}".strip("/")
                aliases.setdefault(k.rstrip("/*"), tgt)
        if base is not None:
            b = "" if base in (".", "./") else base.strip("./")
            if prefix:
                b = f"{prefix}/{b}".strip("/") if b else prefix
            aliases.setdefault("__baseUrl__", b)
    deps: set[str] = set()
    pj = root / "package.json"
    if pj.exists():
        try:
            j = json.loads(pj.read_text(errors="replace"))
            for k in ("dependencies", "devDependencies", "peerDependencies"):
                deps |= set((j.get(k) or {}).keys())
        except Exception:
            pass
    return aliases, deps


def parse_file(repo: Repo, rel: str) -> None:
    lang = TSX if rel.endswith(".tsx") else TS
    parser = Parser(lang)
    try:
        src = (repo.root / rel).read_bytes()
    except OSError:
        return
    tree = parser.parse(src)
    mod = rel.rsplit(".", 1)[0]
    imports: dict[str, str] = {}

    def qual(stack): return ".".join([mod, *stack]) if stack else mod

    def visit(n, stack, cls, fn):
        t = n.type

        if t == "import_statement":
            srcn = n.child_by_field_name("source")
            spec = txt(src, srcn).strip("'\"") if srcn is not None else ""
            for c in n.children:
                if c.type == "import_clause":
                    for d in c.children:
                        if d.type == "identifier":
                            imports[txt(src, d)] = spec
                        elif d.type == "named_imports":
                            for e in d.children:
                                if e.type == "import_specifier":
                                    al = e.child_by_field_name("alias")
                                    nm = e.child_by_field_name("name")
                                    key = txt(src, al or nm) if (al or nm) else None
                                    if key:
                                        imports[key] = spec
                        elif d.type == "namespace_import":
                            for e in d.children:
                                if e.type == "identifier":
                                    imports[txt(src, e)] = spec

        elif t in ("class_declaration", "abstract_class_declaration"):
            nm = n.child_by_field_name("name")
            if nm is None:
                return
            cn = txt(src, nm)
            bases = []
            for c in n.children:
                if c.type == "class_heritage":
                    for d in c.children:
                        if d.type in ("extends_clause", "implements_clause"):
                            for e in d.children:
                                if e.type in ("identifier", "type_identifier",
                                              "generic_type", "member_expression"):
                                    bases.append(txt(src, e).split("<")[0].split(".")[-1])
            s = Sym(qual(stack + [cn]), cn, "class", rel, n.start_point[0] + 1,
                    bases=tuple(bases))
            repo.syms.append(s)
            repo.classes.setdefault(cn, s)
            body = n.child_by_field_name("body")
            if body is not None:
                for c in body.children:
                    visit(c, stack + [cn], cn, fn)
            return

        elif t == "interface_declaration":
            nm = n.child_by_field_name("name")
            if nm is not None:
                cn = txt(src, nm)
                s = Sym(qual(stack + [cn]), cn, "interface", rel, n.start_point[0] + 1)
                repo.syms.append(s)
                repo.classes.setdefault(cn, s)

        elif t == "method_definition":
            nm = n.child_by_field_name("name")
            if nm is None:
                return
            mn = txt(src, nm)
            q = qual(stack + [mn])
            repo.syms.append(Sym(q, mn, "method", rel, n.start_point[0] + 1, cls=cls))
            # constructor-injected, type-annotated fields: NestJS DI
            if mn == "constructor" and cls:
                params = n.child_by_field_name("parameters")
                if params is not None:
                    for pnode in params.children:
                        collect_field(src, repo, cls, pnode)
            body = n.child_by_field_name("body")
            if body is not None:
                for c in body.children:
                    visit(c, stack + [mn], cls, q)
            return

        elif t in ("public_field_definition", "property_signature"):
            if cls:
                collect_field(src, repo, cls, n)

        elif t == "function_declaration":
            nm = n.child_by_field_name("name")
            if nm is not None:
                fnm = txt(src, nm)
                q = qual(stack + [fnm])
                repo.syms.append(Sym(q, fnm, "function", rel, n.start_point[0] + 1))
                body = n.child_by_field_name("body")
                if body is not None:
                    for c in body.children:
                        visit(c, stack + [fnm], cls, q)
                return

        elif t == "variable_declarator":
            nm = n.child_by_field_name("name")
            val = n.child_by_field_name("value")
            if nm is not None and val is not None and val.type in (
                    "arrow_function", "function_expression"):
                fnm = txt(src, nm)
                q = qual(stack + [fnm])
                repo.syms.append(Sym(q, fnm, "function", rel, n.start_point[0] + 1))
                for c in val.children:
                    visit(c, stack + [fnm], cls, q)
                return

        elif t == "call_expression":
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


def collect_field(src: bytes, repo: Repo, cls: str, node) -> None:
    """Record `private readonly x: SomeType` so this.x.method() can resolve."""
    nm = ty = None
    for c in node.children:
        if c.type in ("property_identifier", "identifier") and nm is None:
            nm = txt(src, c)
        if c.type == "type_annotation":
            ty = txt(src, c).lstrip(":").strip().split("<")[0].split("|")[0].strip()
        if c.type in ("required_parameter", "optional_parameter"):
            collect_field(src, repo, cls, c)
    if nm and ty:
        repo.field_types.setdefault(cls, {})[nm] = ty.split(".")[-1]


def classify(src: bytes, f) -> tuple[str, str, str | None]:
    if f.type == "identifier":
        return "bare", txt(src, f), None
    if f.type == "member_expression":
        obj = f.child_by_field_name("object")
        prop = f.child_by_field_name("property")
        if prop is None:
            return "attr", "", None
        name = txt(src, prop)
        if obj is None:
            return "attr", name, None
        ot = txt(src, obj)
        if obj.type == "this":
            return "this", name, "this"
        if obj.type == "super":
            return "super", name, "super"
        if obj.type == "member_expression" and ot.startswith("this."):
            return "this", name, ot.split(".")[1] if "." in ot else None
        if obj.type == "identifier":
            return "module", name, ot
        return "attr", name, ot
    return "other", "", None


def mro(repo: Repo, cls: str, seen=None) -> list[str]:
    seen = seen or set()
    if cls in seen:
        return []
    seen.add(cls)
    out = [cls]
    s = repo.classes.get(cls)
    if s:
        for b in s.bases:
            out.extend(mro(repo, b, seen))
    return out


def is_external_spec(repo: Repo, spec: str) -> bool:
    if spec.startswith("."):
        return False
    for a in repo.aliases:
        if a != "__baseUrl__" and spec.startswith(a):
            return False
    top = spec.split("/")[0]
    if top.startswith("@"):
        top = "/".join(spec.split("/")[:2])
    return top in repo.deps or spec.startswith("node:") or top in {
        "fs", "path", "os", "http", "https", "crypto", "url", "util", "events",
        "stream", "child_process", "zlib", "buffer", "assert", "net"}


def resolve_import(repo: Repo, rel: str, spec: str) -> str | None:
    fileset = set(repo.files)

    def hit(cand: str) -> str | None:
        # ESM TypeScript writes "./foo.js" for a file that is foo.ts on disk.
        # Without this remap, every import in a modern ESM TS project scores
        # as unresolved (measured: zod went 0.0% -> see spike 0c notes).
        for ext in (".js", ".mjs", ".cjs", ".jsx"):
            if cand.endswith(ext):
                stem = cand[: -len(ext)]
                for suf in (".ts", ".tsx", ".mts", ".cts"):
                    if (c := stem + suf) in fileset:
                        return c
                cand = stem
                break
        for suf in (".ts", ".tsx", ".mts", ".cts",
                    "/index.ts", "/index.tsx", ""):
            if (c := cand + suf) in fileset:
                return c
        return None

    if spec.startswith("."):
        cur = rel.split("/")[:-1]
        for part in spec.split("/"):
            if part == ".":
                continue
            if part == "..":
                cur = cur[:-1]
            else:
                cur.append(part)
        return hit("/".join(cur))
    # tsconfig path alias
    for a, target in repo.aliases.items():
        if a == "__baseUrl__":
            continue
        if spec == a or spec.startswith(a + "/"):
            rest = spec[len(a):].lstrip("/")
            return hit(f"{target}/{rest}" if rest else target)
    base = repo.aliases.get("__baseUrl__")
    if base is not None:
        if (h := hit(f"{base}/{spec}" if base else spec)):
            return h
    return None


def resolve_call(repo: Repo, c: CallSite) -> tuple[str, int]:
    name, imports = c.name, repo.imports.get(c.file, {})
    local = repo.by_file.get(c.file, [])

    if c.shape == "bare":
        if name in BUILTIN_FNS and not any(s.name == name for s in local):
            return "external", 1
        hits = [s for s in local if s.name == name and s.cls is None]
        if len(hits) == 1:
            return "resolved", 1
        if len(hits) > 1:
            return "candidate", len(hits)
        if name in imports:
            if is_external_spec(repo, imports[name]):
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

    if c.shape in ("this", "super"):
        if not c.enclosing_cls:
            return "unknown", 0
        # this.someField.method()  -> use the recorded field type (NestJS DI)
        if c.recv and c.recv not in ("this", "super"):
            ftype = repo.field_types.get(c.enclosing_cls, {}).get(c.recv)
            if ftype:
                if ftype in repo.classes:
                    chain = mro(repo, ftype)
                    hits = [s for s in repo.syms
                            if s.name == name and s.cls in chain]
                    if len(hits) == 1:
                        return "resolved", 1
                    if len(hits) > 1:
                        return "candidate", len(hits)
                    return "unknown", 0
                return "external", 1     # field typed to a third-party class
            return "unknown", 0
        chain = mro(repo, c.enclosing_cls)
        if c.shape == "super":
            chain = chain[1:]
        hits = [s for s in repo.syms
                if s.name == name and s.cls in chain and s.kind == "method"]
        if len(hits) == 1:
            return "resolved", 1
        if len(hits) > 1:
            return "candidate", len(hits)
        return "unknown", 0

    if c.shape == "module":
        recv = c.recv or ""
        if recv in BUILTIN_OBJS:
            return "external", 1
        if recv in imports:
            if is_external_spec(repo, imports[recv]):
                return "external", 1
            tgt = resolve_import(repo, c.file, imports[recv])
            if tgt:
                hits = [s for s in repo.by_file.get(tgt, []) if s.name == name]
                if len(hits) == 1:
                    return "resolved", 1
                if len(hits) > 1:
                    return "candidate", len(hits)
            g = repo.by_name.get(name, [])
            if len(g) == 1:
                return "resolved", 1
            if len(g) > 1:
                return "candidate", len(g)
            return "unknown", 0
        if recv in repo.classes:
            chain = mro(repo, recv)
            hits = [s for s in repo.syms if s.name == name and s.cls in chain]
            if len(hits) == 1:
                return "resolved", 1
            if len(hits) > 1:
                return "candidate", len(hits)
        hits = [s for s in repo.syms if s.name == name and s.kind == "method"]
        if len(hits) == 1:
            return "resolved", 1
        if 1 < len(hits) <= 8:
            return "candidate", len(hits)
        return "unknown", len(hits)

    hits = [s for s in repo.syms if s.name == name and s.kind == "method"]
    if len(hits) == 1:
        return "resolved", 1
    if 1 < len(hits) <= 8:
        return "candidate", len(hits)
    if not hits:
        return "external", 1
    return "unknown", len(hits)


def depth(edges, start, limit=12) -> int:
    best, stack, seen = 0, [(start, {start}, 0)], 0
    while stack and seen < 30000:
        n, path, d = stack.pop()
        seen += 1
        best = max(best, d)
        if d >= limit:
            continue
        for nx in sorted(edges.get(n, ()))[:12]:
            if nx not in path:
                stack.append((nx, path | {nx}, d + 1))
    return best


def run(target: str) -> dict:
    root = Path(target).resolve()
    repo = Repo(root=root)
    repo.aliases, repo.deps = load_tsconfig(root)
    repo.files = walk(root)
    for rel in repo.files:
        parse_file(repo, rel)
    for s in repo.syms:
        repo.by_name.setdefault(s.name, []).append(s)
        repo.by_file.setdefault(s.file, []).append(s)

    # --- imports ---
    file_edges: dict[tuple[str, str], int] = {}
    ali = collections.Counter()
    for rel, imps in repo.imports.items():
        for spec in set(imps.values()):
            if is_external_spec(repo, spec):
                repo.import_stats["external"] += 1
                continue
            tgt = resolve_import(repo, rel, spec)
            if tgt and tgt != rel:
                repo.import_stats["resolved"] += 1
                file_edges[(rel, tgt)] = 1
                if not spec.startswith("."):
                    ali["alias_resolved"] += 1
            else:
                repo.import_stats["unresolved"] += 1
                if not spec.startswith("."):
                    ali["alias_unresolved"] += 1

    # --- calls ---
    bins = collections.Counter()
    by_shape = collections.defaultdict(collections.Counter)
    call_edges: dict[str, set[str]] = {}
    for c in repo.calls:
        b, ar = resolve_call(repo, c)
        bins[b] += 1
        by_shape[c.shape][b] += 1
        if b in ("resolved", "candidate") and c.enclosing:
            g = repo.by_name.get(c.name, [])
            if g:
                call_edges.setdefault(c.enclosing, set()).add(g[0].qual)

    total = sum(bins.values())
    intra = bins["resolved"] + bins["candidate"] + bins["unknown"]
    pin = (bins["resolved"] + bins["candidate"]) / intra * 100 if intra else 0

    imp_tot = sum(repo.import_stats.values())
    imp_intra = repo.import_stats["resolved"] + repo.import_stats["unresolved"]
    imp_rate = repo.import_stats["resolved"] / imp_intra * 100 if imp_intra else 0

    fadj: dict[str, set[str]] = {}
    for (a, b) in file_edges:
        fadj.setdefault(a, set()).add(b)
    fd = sorted((depth(fadj, f) for f in repo.files), reverse=True)
    cd = sorted((depth(call_edges, s.qual) for s in repo.syms
                 if s.kind in ("method", "function")), reverse=True)

    print(f"\n{'='*72}\n{root.name}  ({len(repo.files)} files, {len(repo.syms)} symbols, "
          f"{total} calls, {imp_tot} imports)\n{'='*72}")
    print(f"tsconfig aliases: {len([a for a in repo.aliases if a!='__baseUrl__'])}"
          f"  baseUrl={repo.aliases.get('__baseUrl__','-')!r}  deps={len(repo.deps)}")
    print(f"IMPORTS  resolved {repo.import_stats['resolved']}  "
          f"unresolved {repo.import_stats['unresolved']}  "
          f"external {repo.import_stats['external']}   "
          f"-> intra-repo rate {imp_rate:.1f}%")
    if ali:
        print(f"  non-relative (alias/baseUrl) specifiers: "
              f"resolved {ali['alias_resolved']}, unresolved {ali['alias_unresolved']}")
    print(f"CALLS    resolved {bins['resolved']}  candidate {bins['candidate']}  "
          f"external {bins['external']}  unknown {bins['unknown']}   "
          f"-> PINNED {pin:.1f}% of intra-repo")
    for shape in ("bare", "this", "attr", "module", "super"):
        cc = by_shape[shape]
        t = sum(cc.values())
        if not t:
            continue
        loc = cc["resolved"] + cc["candidate"] + cc["unknown"]
        r = (cc["resolved"] + cc["candidate"]) / loc * 100 if loc else 0
        print(f"  {shape:<7} n={t:<6} pinned {r:>5.1f}%  (res {cc['resolved']}, "
              f"cand {cc['candidate']}, ext {cc['external']}, unk {cc['unknown']})")
    print(f"CHAINS   call-depth max {cd[0] if cd else 0} "
          f"(>=3: {sum(1 for d in cd if d>=3)}/{len(cd)})   |   "
          f"import-depth max {fd[0] if fd else 0} "
          f"(>=3: {sum(1 for d in fd if d>=3)}/{len(fd)})")

    return {"repo": root.name, "import_rate": imp_rate, "call_pinned": pin,
            "call_depth": cd[:5], "import_depth": fd[:5],
            "alias_unresolved": ali["alias_unresolved"]}


if __name__ == "__main__":
    out = [run(t) for t in sys.argv[1:]]
    Path("/tmp/spike-calls-ts.json").write_text(json.dumps(out, indent=1))
