"""Mutation check for the language pack walker.

Each entry breaks one promise of the walker; a test must go red:

* children are walked in document order;
* a definition walks only its body, never default arguments or bases;
* the depth cap stops the walk instead of recursing on;
* a decorated definition receives its decorators;
* member decorators pair with the next member only;
* JavaScript facts carry the javascript label;
* languages without a pack are reported;
* a package import points at every file in the package;
* Go test files are never import targets;
* a dotless Go module path is never the standard library;
* Maven placeholders are never dependencies;
* a pom that declares entities is refused;
* a reassigned variable stops counting as a built string;
* interpolated strings count as built;
* a sink that needs `shell=True` fires only with it;
* placeholders are never secrets;
* an import line never counts as auth;
* a location exception moves a violation to accepted;
* only a loop's body runs per item;
* an async function is async;
* a catch with code in it is not empty;
* a forEach/map callback runs per item;
* one codebase is one owner of a datastore;
* a hub must be on every request path;
* function checks need an example in every language;
* a JavaScript finding shows the TypeScript example;
* a box counts only its own findings;
* moved code is never a new finding;
* a grade drop fails the check;
* a C# namespace qualifies its symbols;
* `System.*` is external in C#;
* the C# `async` marker is found under `modifier`;
* `FromSqlInterpolated` is not a raw sink;
* a nested C# block namespace keeps its parent;
* an impl method belongs to its type in Rust;
* `std` is external in Rust;
* the Rust `async` marker is found under `function_modifiers`;
* a `sqlx::query!` macro is not a raw sink;
* `mod.rs` names its directory;
* `pub use` re-exports in Rust;
* a plain `use` does not re-export in Rust;
* locals are ignored by the resolver;
* a local does not shadow a field;
* Rust `&T` is not unwrapped;
* a generic parameter counts as a type;
* the innermost scope does not win;
* a "" binding falls back to fields;
* an externally imported type resolves to a same-named repo class.
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
PY = ROOT / ".venv" / "bin" / "python"
SUITE = [
    "tests/test_pack_walker.py",
    "tests/test_golden_facts.py",
    "tests/test_extract.py",
    "tests/test_extract_typescript.py",
    "tests/test_packs.py",
    "tests/test_module_resolvers.py",
    "tests/test_extract_go.py",
    "tests/test_extract_java.py",
    "tests/test_extract_csharp.py",
    "tests/test_extract_rust.py",
    "tests/test_golden_repos.py",
    "tests/test_detect.py",
    "tests/test_metrics.py",
    "tests/test_health.py",
    "tests/test_health_outputs.py",
    "tests/test_design.py",
    "tests/test_design_cli.py",
    "tests/test_design_outputs.py",
    "tests/test_risks.py",
    "tests/test_security.py",
    "tests/test_secrets.py",
    "tests/test_auth.py",
    "tests/test_perf_reliability.py",
    "tests/test_compose.py",
    "tests/test_explain.py",
    "tests/test_about.py",
    "tests/test_check.py",
]

# A few mutations need a second edit to take effect. Both edits are applied
# together: the name is the key, each extra edit is (file, old, new).
EXTRA_EDITS: dict[str, tuple[str, str, str]] = {
    # The walker finds no arguments on a macro, so the sink pattern alone is
    # inert. Give a macro its token tree as arguments too.
    "a sqlx query! macro counts as a raw sink": (
        "svarupa/extract/packs/walker.py",
        '            callee = spec.callee(self, node)\n            args = node.child_by_field_name("arguments")\n            self._append(',
        '            callee = spec.callee(self, node)\n            args = node.child_by_field_name("arguments")\n            if args is None and t == "macro_invocation":\n                args = node.named_children[-1] if node.named_children else None\n            self._append(',
    ),
}

MUTATIONS: list[tuple[str, str, str, str]] = [
    (
        "generic children are walked in reverse",
        "svarupa/extract/packs/walker.py",
        "        plain = frame.plain()\n        for child in node.children:\n            self.visit(child, plain, depth + 1)\n\n    def _define",
        "        plain = frame.plain()\n        for child in reversed(node.children):\n            self.visit(child, plain, depth + 1)\n\n    def _define",
    ),
    (
        "a definition walks all children, not only its body",
        "svarupa/extract/packs/walker.py",
        "        body = node.child_by_field_name(rule.body_field)\n        if body is None:\n            return\n",
        "        body = node\n",
    ),
    (
        "the depth cap no longer stops the walk",
        "svarupa/extract/packs/walker.py",
        "            self.too_deep = True\n            return\n",
        "            self.too_deep = True\n",
    ),
    (
        "decorated definitions lose their decorators",
        "svarupa/extract/packs/walker.py",
        "self.visit(definition, replace(frame, decorators=decs), depth + 1)",
        "self.visit(definition, frame, depth + 1)",
    ),
    (
        "member decorators are never cleared",
        "svarupa/extract/packs/walker.py",
        "            self.visit(child, replace(inner, decorators=tuple(pending)), depth + 1)\n            pending = []\n",
        "            self.visit(child, replace(inner, decorators=tuple(pending)), depth + 1)\n",
    ),
    (
        "javascript facts are labelled typescript again",
        "svarupa/extract/packs/javascript.py",
        '    lang="javascript",\n',
        '    lang="typescript",\n',
    ),
    (
        "languages without a pack are skipped silently",
        "svarupa/extract/__init__.py",
        "            if rec.lang and rec.lang not in ANALYZED_ELSEWHERE:\n",
        "            if False:\n",
    ),
    (
        "a package import points at only its first file",
        "svarupa/extract/resolve.py",
        "            for dst in others:\n",
        "            for dst in others[:1]:\n",
    ),
    (
        "go test files become import targets",
        "svarupa/extract/packs/modules.py",
        'if path.endswith(".go") and not path.endswith("_test.go"):',
        'if path.endswith(".go"):',
    ),
    (
        "a dotless go module path counts as standard library",
        "svarupa/extract/packs/modules.py",
        "        if self._module_of(spec) is not None:\n            return False\n",
        "",
    ),
    (
        "a pom that declares entities is parsed anyway",
        "svarupa/extract/__init__.py",
        '    if "<!DOCTYPE" in text or "<!ENTITY" in text:\n        return set()\n',
        "",
    ),
    (
        "maven placeholders become dependencies",
        "svarupa/extract/__init__.py",
        '    if not group or "$" in group or "{" in group:\n        return None\n',
        "    if not group:\n        return None\n",
    ),
    (
        "alembic migrations count as architecture again",
        "svarupa/detect.py",
        '    if lang == "python" and _is_alembic_migration(data):\n        return FileRole.GENERATED\n',
        "",
    ),
    (
        "go bare calls match other packages again",
        "svarupa/extract/resolve.py",
        "            if module is not None and module.package_scoped_bare:",
        "            if False:",
    ),
    (
        "python calls through an imported submodule stay unresolved",
        "svarupa/extract/resolve.py",
        '                if found == [] and f.lang == "python":',
        "                if False:",
    ),
    (
        "java field calls without this stay unresolved",
        "svarupa/extract/resolve.py",
        "                and module.fields_without_this\n",
        "                and False\n",
    ),
    (
        "nested functions add to the outer complexity",
        "svarupa/extract/packs/walker.py",
        "                and (spec.is_function is None or spec.is_function(node))\n            ):\n                continue  # measured on its own\n",
        "            ):\n                pass  # measured on its own\n",
    ),
    (
        "else if nests deeper again",
        "svarupa/extract/packs/walker.py",
        "                kind in spec.if_types and parent in spec.else_if_parents\n",
        "                False\n",
    ),
    (
        "the complexity limit is inclusive",
        "svarupa/health/checks.py",
        "            if value <= check.threshold:\n",
        "            if value < check.threshold:\n",
    ),
    (
        "import lines count as duplication",
        "svarupa/health/duplication.py",
        '"import ", "from ", "package ", "using ", "require(")',
        ")",
    ),
    (
        "unassessed areas get a free grade",
        "svarupa/health/score.py",
        "        return None  # not assessed yet: no grade is better than a free A\n",
        '        return "A"\n',
    ),
    (
        "debt ignores code size",
        "svarupa/health/score.py",
        "    ratio = debt / (lines * MINUTES_PER_LINE) if lines else 0.0\n",
        "    ratio = debt / MINUTES_PER_LINE if lines else 0.0\n",
    ),
    (
        "upward imports are allowed",
        "svarupa/design/match.py",
        "order[dst.part] < order[src.part]",
        "order[dst.part] > order[src.part] + 99",
    ),
    (
        "exceptions stop being honored",
        "svarupa/design/match.py",
        "            if (rule, a, b) in excused:",
        "            if False:",
    ),
    (
        "a low fit is proposed anyway",
        "svarupa/design/__init__.py",
        "        if fits and fits[0].fit >= MIN_FIT:",
        "        if fits:",
    ),
    (
        "design writes the file without a terminal",
        "svarupa/cli.py",
        "        if not sys.stdin.isatty():\n",
        "        if False:\n",
    ),
    (
        "utility folders count against a style again",
        "svarupa/design/match.py",
        "        if _neutral(segs, style):\n            continue\n",
        "",
    ),
    (
        "a reassigned variable stays built",
        "svarupa/extract/packs/walker.py",
        "            elif not augment:\n                built.discard(name)\n",
        "",
    ),
    (
        "interpolated strings are not built",
        "svarupa/extract/packs/walker.py",
        "            return any(c.type == inner for c in node.children) or any(",
        "            return False or any(",
    ),
    (
        "a sink's required argument is ignored",
        "svarupa/extract/packs/walker.py",
        "                    if sink.requires is not None and not sink.requires.search(self.text(args)):\n                        continue\n",
        "",
    ),
    (
        "placeholders count as secrets",
        "svarupa/health/secrets.py",
        "            if _PLACEHOLDER.search(value) or _entropy(value) < _MIN_ENTROPY:\n",
        "            if False:\n",
    ),
    (
        "import lines count as auth",
        "svarupa/health/auth.py",
        '    return _IMPORT.sub(lambda m: "\\n" * m.group(0).count("\\n"), text).split("\\n")\n',
        '    return text.split("\\n")\n',
    ),
    (
        "location exceptions are ignored",
        "svarupa/health/__init__.py",
        "    return any(e.at and e.rule == v.check and e.at in places for e in exceptions)\n",
        "    return False\n",
    ),
    (
        "a loop's iterable counts as its body",
        "svarupa/extract/packs/walker.py",
        "                    (c, in_loop or (body is not None and c.id == body.id))\n",
        "                    (c, in_loop or body is not None)\n",
    ),
    (
        "async functions are never async",
        "svarupa/extract/packs/walker.py",
        "        marker = spec.async_marker\n        if marker is None:\n            return False\n",
        "        marker = spec.async_marker\n        if True:\n            return False\n",
    ),
    (
        "a catch that does something counts as empty",
        "svarupa/extract/packs/walker.py",
        "        return body is not None and all(c.type in spec.quiet for c in body.named_children)\n",
        "        return body is not None\n",
    ),
    (
        "callbacks of forEach and map are not per item",
        "svarupa/extract/packs/walker.py",
        "                    per_item = in_loop or self._per_item(node, spec)\n",
        "                    per_item = in_loop\n",
    ),
    (
        "one codebase with two services counts as shared",
        "svarupa/health/performance.py",
        "        if len(codebases) <= check.threshold:\n",
        "        if len(apps) <= check.threshold:\n",
    ),
    (
        "a hub off some request paths is still flagged",
        "svarupa/health/performance.py",
        "        common &= _reach(uses, m)\n",
        "        common |= _reach(uses, m)\n",
    ),
    (
        "function checks need no examples",
        "svarupa/health/explain.py",
        "        if check_id in _FUNCTION_CHECKS and pack.metrics is not None:\n",
        "        if False:\n",
    ),
    (
        "javascript loses the typescript example",
        "svarupa/health/explain.py",
        '_SAME_AS = {"javascript": "typescript"}\n',
        "_SAME_AS: dict[str, str] = {}\n",
    ),
    (
        "a box takes every finding",
        "svarupa/derive/about.py",
        "        return list(ix.by_module.get(module, []))\n",
        "        return [v for vs in ix.by_module.values() for v in vs]\n",
    ),
    (
        "line numbers decide whether a finding is new",
        "svarupa/check.py",
        '        return (self.check, self.file, self.symbol or _DIGITS.sub("#", self.message))\n',
        '        return (self.check, self.file, f"{self.line}")\n',
    ),
    (
        "a grade drop does not fail the check",
        "svarupa/check.py",
        "        return bool(self.worse()) or any(\n",
        "        return False or any(\n",
    ),
    (
        "a csharp namespace no longer qualifies symbols",
        "svarupa/extract/packs/csharp.py",
        "    ctx.prefix = full\n    if not block:",
        "    if not block:",
    ),
    (
        "System.* stops being external in csharp",
        "svarupa/extract/packs/modules.py",
        '[0] in ("System", "Microsoft")',
        '[0] in ("Microsoft",)',
    ),
    (
        "the csharp async marker is not found under modifier",
        "svarupa/extract/packs/csharp.py",
        '    async_holder="modifier",\n',
        '    async_holder="modifiers",\n',
    ),
    (
        "FromSqlInterpolated is treated as a raw sink",
        "svarupa/extract/packs/csharp.py",
        "(?:ExecuteSqlRaw|ExecuteSqlRawAsync|FromSqlRaw|SqlQueryRaw)",
        "(?:ExecuteSqlRaw|ExecuteSqlRawAsync|FromSqlRaw|FromSqlInterpolated|SqlQueryRaw)",
    ),
    (
        "a nested csharp block namespace loses its parent",
        "svarupa/extract/packs/csharp.py",
        "            parts.append(ctx.text(outer))\n",
        "            pass\n",
    ),
    (
        "a rust impl method loses its type",
        "svarupa/extract/packs/rust.py",
        "visit_items(ctx, body.children, Frame((*frame.stack, owner), owner, frame.fn), depth)",
        "visit_items(ctx, body.children, frame, depth)",
    ),
    (
        "std stops being external in rust",
        "svarupa/extract/packs/modules.py",
        "        if head in _RUST_STD:\n            return True",
        "        if False:\n            return True",
    ),
    (
        "the rust async marker is not found under function_modifiers",
        "svarupa/extract/packs/rust.py",
        '    async_holder="function_modifiers",\n',
        '    async_holder="modifiers",\n',
    ),
    (
        "a sqlx query! macro counts as a raw sink",
        "svarupa/extract/packs/rust.py",
        'Sink("sql", re.compile(r"^" + _SQLX_NAME + r"$")),',
        'Sink("sql", re.compile(r"^" + _SQLX + r"$")),',
    ),
    (
        "mod.rs stops naming its directory",
        "svarupa/extract/packs/rust.py",
        '    if parts and parts[-1] == "mod":\n        parts = parts[:-1]\n',
        "",
    ),
    (
        "pub use no longer re-exports in rust",
        "svarupa/extract/packs/rust.py",
        "refs = (replace(r, is_reexport=True) for r in refs)",
        "refs = (r for r in refs)",
    ),
    (
        "a plain use re-exports in rust",
        "svarupa/extract/packs/rust.py",
        'return vis is not None and "".join',
        'return vis is None or "".join',
    ),
    (
        "locals are ignored by the resolver",
        "svarupa/extract/resolve.py",
        "        line = call.evidence.start_line\n        found = [",
        "        line = call.evidence.start_line\n        return None\n        found = [",
    ),
    (
        "a local does not shadow a field",
        "svarupa/extract/resolve.py",
        "            if type_name is not None:\n                # A parameter or typed local shadows",
        "            if type_name is not None and (\n                f.path,\n                call.enclosing_class,\n                recv,\n            ) not in self.idx.field_types:\n                # A parameter or typed local shadows",
    ),
    (
        "rust &T is not unwrapped",
        "svarupa/extract/packs/rust.py",
        '    if ty.type == "reference_type":\n        return _type_name(ty.child_by_field_name("type"), unwrap)\n',
        '    if ty.type == "reference_type":\n        return None\n',
    ),
    (
        "a generic parameter counts as a type",
        "svarupa/extract/packs/rust.py",
        "    return None if name is None or name in generics else name",
        "    return None if name is None else name",
    ),
    (
        "the innermost scope does not win",
        "svarupa/extract/resolve.py",
        "inner = min(_span(loc) for loc in found)",
        "inner = max(_span(loc) for loc in found)",
    ),
    (
        'a "" binding falls back to fields',
        "svarupa/extract/resolve.py",
        "            if type_name is not None:\n                # A parameter or typed local shadows",
        "            if type_name:\n                # A parameter or typed local shadows",
    ),
    (
        "an externally imported type resolves to a same-named repo class",
        "svarupa/extract/resolve.py",
        "        if self._type_is_external(facts, type_name) and not any(",
        "        if False and self._type_is_external(facts, type_name) and not any(",
    ),
]


def _apply_extra(name: str) -> None:
    """Apply the second edit of a mutation that needs one. A missing pattern
    stops the run: the mutation would be weaker than it claims."""
    extra = EXTRA_EDITS.get(name)
    if extra is None:
        return
    other = ROOT / extra[0]
    body = other.read_text(encoding="utf8")
    if extra[1] not in body:
        raise SystemExit(f"extra pattern not found: {name}")
    other.write_text(body.replace(extra[1], extra[2], 1), encoding="utf8")


def main() -> int:
    failures: list[str] = []
    backup = pathlib.Path(tempfile.mkdtemp()) / "src"
    shutil.copytree(ROOT / "svarupa", backup)
    try:
        for name, rel, old, new in MUTATIONS:
            path = ROOT / rel
            text = path.read_text(encoding="utf8")
            if old not in text:
                print(f"SKIP (pattern not found)  {name}")
                failures.append(f"{name}: pattern not found")
                continue
            path.write_text(text.replace(old, new, 1), encoding="utf8")
            _apply_extra(name)
            proc = subprocess.run(
                [str(PY), "-m", "pytest", *SUITE, "-q", "-x"],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            shutil.rmtree(ROOT / "svarupa")
            shutil.copytree(backup, ROOT / "svarupa")
            if proc.returncode == 0:
                print(f"SURVIVED  {name}")
                failures.append(f"{name}: no test failed")
            else:
                caught = [
                    ln.split("::")[-1]
                    for ln in proc.stdout.splitlines()
                    if ln.startswith("FAILED")
                ]
                print(f"caught    {name}  ->  {caught[0] if caught else 'error'}")
    finally:
        # Restore unconditionally. An interrupt during pytest leaves the tree
        # present but mutated, and a presence check would keep the mutation.
        shutil.rmtree(ROOT / "svarupa", ignore_errors=True)
        shutil.copytree(backup, ROOT / "svarupa")
    if failures:
        print("\nnot caught:")
        for f in failures:
            print(f"  {f}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
