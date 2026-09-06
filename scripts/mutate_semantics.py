"""Mutation check for the semantics wave (routes, tasks, entrypoints, roles).

Each entry deletes a property the wave's claims name; a test must go red.
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
PY = ROOT / ".venv" / "bin" / "python"
SUITE = ["tests/test_semantics.py", "tests/test_diagnostics.py", "tests/test_lock.py"]

MUTATIONS: list[tuple[str, str, str, str]] = [
    (
        "a route shape claims fastapi without the import",
        "svarupa/extract/semantics.py",
        '    fastapi = _imports_top(f, "fastapi")',
        "    fastapi = True",
    ),
    (
        "a task shape claims celery without the import",
        "svarupa/extract/semantics.py",
        '    if not _imports_top(f, "celery"):\n        return []',
        "    if False:\n        return []",
    ),
    (
        "flask loses its documented GET default",
        "svarupa/extract/semantics.py",
        '                for method in dec.methods if dec.methods is not None else ("GET",):',
        '                for method in dec.methods if dec.methods is not None else ("POST",):',
    ),
    (
        "the route claim cites the line below the decorator",
        "svarupa/extract/python.py",
        "        ev = self.evidence(path, node.start_point[0], node.end_point[0])",
        "        ev = self.evidence(path, node.start_point[0] + 1, node.end_point[0] + 1)",
    ),
    (
        "an f-string route path is guessed from its static parts",
        "svarupa/extract/python.py",
        '    if any(c.type == "interpolation" for c in node.children):',
        "    if False:",
    ),
    (
        "the manifest line scan ignores table scope",
        "svarupa/extract/semantics.py",
        "            current == table\n            and pattern.match(line)",
        "            pattern.match(line)",
    ),
    (
        "endpoint records key on the handler name, not the module",
        "svarupa/lock/build.py",
        '                Record("endpoint", (f"{r.method} {r.path}", spell(module_of(r.file))))',
        '                Record("endpoint", (f"{r.method} {r.path}", r.handler))',
    ),
    (
        "a route in a test file becomes an architectural fact",
        "svarupa/lock/build.py",
        "    arch_routes = [r for r in graph.routes if r.file in graph.architecture_paths]",
        "    arch_routes = list(graph.routes)",
    ),
    (
        "an entrypoint target is trusted instead of resolved",
        "svarupa/build.py",
        "    for cand in candidates:\n        if cand in graph.nodes:\n            return _module_of(cand)\n    return None",
        "    for cand in candidates:\n        return _module_of(cand)\n    return None",
    ),
    (
        "role records are dropped from the lockfile",
        "svarupa/lock/build.py",
        '            Record("role", (spell(m), role))',
        '            Record("role", (spell(m), role))\n            for _never in ()',
    ),
    (
        "the schema minor is not bumped for the new kinds",
        "svarupa/lock/grammar.py",
        "SCHEMA_MINOR = 5",
        "SCHEMA_MINOR = 4",
    ),
    (
        "boxes stop wearing their role",
        "svarupa/derive/architecture.py",
        "def _role_kind(roles: dict[str, tuple[str, ...]], module: str) -> str:\n    held = roles.get(module, ())",
        "def _role_kind(roles: dict[str, tuple[str, ...]], module: str) -> str:\n    held = ()",
    ),
    (
        "build stops re-verifying fact evidence",
        "svarupa/build.py",
        "    routes = tuple(r for r in extracted.routes if fact_ok(r.evidence, r.handler))",
        "    routes = tuple(extracted.routes)",
    ),
    (
        "worker roles vanish from the shared role map",
        "svarupa/build.py",
        '            roles.setdefault(_module_of(t.file), set()).add("worker")',
        "            pass",
    ),
    (
        "a present-but-dynamic flask methods falls back to GET again",
        "svarupa/extract/semantics.py",
        '                for method in dec.methods if dec.methods is not None else ("GET",):',
        '                for method in dec.methods or ("GET",):',
    ),
    (
        "a non-path decorator argument claims a route again",
        "svarupa/extract/semantics.py",
        '            if dec.arg is not None and dec.arg != "" and not dec.arg.startswith("/"):',
        "            if False:",
    ),
    (
        "fixture manifests mint committed records again",
        "svarupa/extract/semantics.py",
        "        if not rec.role.in_architecture:\n            continue",
        "        if False:\n            continue",
    ),
    (
        "python entrypoints resolve from the repo root again",
        "svarupa/build.py",
        "            posixpath.normpath(posixpath.join(folder, rel))",
        "            posixpath.normpath(rel)",
    ),
    (
        "the js bin target is unanchored from its manifest",
        "svarupa/build.py",
        "        candidates = [posixpath.normpath(posixpath.join(folder, target))]",
        "        candidates = [posixpath.normpath(target)]",
    ),
    (
        "the toml scan stops cross-checking the value",
        "svarupa/extract/semantics.py",
        "            and (f'\"{value}\"' in line or f\"'{value}'\" in line)",
        "            and True",
    ),
    (
        "the toml scan accepts only bare keys",
        "svarupa/extract/semantics.py",
        "    pattern = re.compile(rf'^\\s*(?:{re.escape(key)}|\"{re.escape(key)}\")\\s*=')",
        "    pattern = re.compile(rf'^\\s*{re.escape(key)}\\s*=')",
    ),
    (
        "the bin scan reads the whole document again",
        "svarupa/extract/semantics.py",
        "        for i in range(lo, hi + 1):",
        "        for i in range(1, len(lines) + 1):",
    ),
    (
        "a schema-minor step stops being attributed",
        "svarupa/lock/diff.py",
        "    if base.header.schema_minor != head.header.schema_minor:",
        "    if False:",
    ),
    (
        "role priority is inverted",
        "svarupa/derive/architecture.py",
        '_ROLE_PRIORITY = ("frontend", "api", "worker", "auth", "cli")',
        '_ROLE_PRIORITY = ("cli", "auth", "worker", "api", "frontend")',
    ),
    (
        "module_roles stops gating routes on architecture eligibility",
        "svarupa/build.py",
        '        if r.file in graph.architecture_paths:\n            roles.setdefault(_module_of(r.file), set()).add("api")',
        '        roles.setdefault(_module_of(r.file), set()).add("api")',
    ),
    (
        "semantic facts lose canonical order",
        "svarupa/extract/semantics.py",
        "        routes=tuple(sorted(set(routes))),",
        "        routes=tuple(routes),",
    ),
    (
        "role evidence stops being reserved under the cap",
        "svarupa/derive/architecture.py",
        "    trimmed = tuple(ev for ev in base if ev not in roles)[: max(0, keep)]\n    return trimmed + roles",
        "    return (base + roles)[:MAX_EVIDENCE_PER_BOX]",
    ),
    (
        "the report stops naming the framework boundary",
        "svarupa/emit/report.py",
        '        f"- routes, tasks and roles come from framework detection "',
        '        f"- routes, tasks and roles: "',
    ),
    (
        "express routes stop being receiver-scoped",
        "svarupa/extract/semantics.py",
        "        if call.receiver not in receivers or call.name not in _EXPRESS_METHODS:",
        "        if call.receiver is None or call.name not in _EXPRESS_METHODS:",
    ),
    (
        "an express path stops needing to be a path",
        "svarupa/extract/semantics.py",
        '        if call.first_str_arg is not None and call.first_str_arg.startswith("/"):',
        "        if call.first_str_arg is not None:",
    ),
    (
        "a substituted template literal is guessed from its static parts",
        "svarupa/extract/typescript.py",
        '    if node.type == "template_string" and any(',
        "    if False and any(",
    ),
    (
        "nest decorators claim routes without the nestjs import",
        "svarupa/extract/semantics.py",
        '        not imp.is_relative and imp.specifier.startswith("@nestjs/") for imp in f.imports',
        "        True for imp in f.imports",
    ),
    (
        "nest route methods outside a controller claim routes",
        "svarupa/extract/semantics.py",
        '        if s.kind != "method" or s.enclosing_class not in controllers:',
        '        if s.kind != "method":',
    ),
    (
        "the controller prefix stops composing",
        "svarupa/extract/semantics.py",
        "                    path=_nest_path(controllers[s.enclosing_class], dec.arg),",
        "                    path=_nest_path(None, dec.arg),",
    ),
    (
        "the export keyword consumes pending class decorators",
        "svarupa/extract/typescript.py",
        '                    if child.type in ("export", "default", ";"):',
        "                    if False:",
    ),
    (
        "a dynamic nest decorator argument composes as empty again",
        "svarupa/extract/semantics.py",
        "            if dec.arg_dynamic:",
        "            if False:",
    ),
    (
        "a dynamic controller prefix stops excluding the class",
        "svarupa/extract/semantics.py",
        '        and not any(\n            d.arg_dynamic for d in s.decorators if d.name.rsplit(".", 1)[-1] == "Controller"\n        )',
        "        and True",
    ),
    (
        "require() stops being an import",
        "svarupa/extract/typescript.py",
        '                    if callee is not None and _text(data, callee) == "require":',
        '                    if callee is not None and _text(data, callee) == "never":',
    ),
    (
        "a non-express bind stops poisoning the receiver name",
        "svarupa/extract/semantics.py",
        "    return frozenset(held - poisoned)",
        "    return frozenset(held)",
    ),
    (
        "escape sequences vanish from string values again",
        "svarupa/extract/typescript.py",
        '            if c.type in ("string_fragment", "escape_sequence")',
        '            if c.type in ("string_fragment",)',
    ),
    (
        "a later-position string becomes the decorator or call path",
        "svarupa/extract/typescript.py",
        '            if child.type in ("string", "template_string"):\n                return _ts_string(src, child)\n            return None',
        '            if child.type in ("string", "template_string"):\n                return _ts_string(src, child)\n            continue',
    ),
    (
        "controller prefixes stop normalizing slashes",
        "svarupa/extract/semantics.py",
        '    segments = [s.strip("/") for s in (prefix, sub) if s and s.strip("/")]',
        "    segments = [s for s in (prefix, sub) if s]",
    ),
    (
        "the declaration hop drops the exported flag again",
        "svarupa/extract/typescript.py",
        "                    visit(child, stack, cls, fn, exported=exported, depth=depth + 1)",
        "                    visit(child, stack, cls, fn, depth=depth + 1)",
    ),
    (
        "non-exported classes lose their own decorators",
        "svarupa/extract/typescript.py",
        "                decorators = decorators + tuple(",
        "                decorators = decorators + () and tuple(",
    ),
    (
        "a chain root other than route claims a route",
        "svarupa/extract/semantics.py",
        '            if root_method == "route" and root_path is not None and root_path.startswith("/"):',
        '            if root_path is not None and root_path.startswith("/"):',
    ),
    (
        "a dynamic chain root claims a route at the chain root",
        "svarupa/extract/semantics.py",
        '            if root_method == "route" and root_path is not None and root_path.startswith("/"):',
        '            if root_method == "route" and (root_path is None or root_path.startswith("/")):\n                root_path = root_path or "/"',
    ),
    (
        "mounts stop composing",
        "svarupa/extract/semantics.py",
        "            else [(_join_paths(p, path), via) for p, via in prefixes]",
        "            else [(path, via) for p, via in prefixes]",
    ),
    (
        "a dynamic mount prefix reads as empty again",
        "svarupa/extract/semantics.py",
        "        else:\n            prefix = None",
        '        else:\n            prefix = ""',
    ),
    (
        "nested router mounts stop composing through",
        "svarupa/extract/semantics.py",
        "            above = prefixes(host, (*path, router))",
        '            above = [("", ())]',
    ),
    (
        "the chain unwinder loses the root path",
        "svarupa/extract/typescript.py",
        "        return (_text(src, cur), _text(src, prop), self._first_str_arg(src, innermost))",
        "        return (_text(src, cur), _text(src, prop), None)",
    ),
    (
        "app.get(path, router) becomes a mount",
        "svarupa/extract/semantics.py",
        '        if call.name != "use" or call.receiver not in receivers:',
        '        if call.name not in ("use", "get") or call.receiver not in receivers:',
    ),
    (
        "a chain rooted at use() claims a route",
        "svarupa/extract/semantics.py",
        '            if root_method == "route" and root_path is not None and root_path.startswith("/"):',
        '            if root_method in ("route", "use") and root_path is not None and root_path.startswith("/"):',
    ),
    (
        "a slash-less mount prefix composes",
        "svarupa/extract/semantics.py",
        '            if pfx is None or (pfx and not pfx.startswith("/")):',
        "            if pfx is None:",
    ),
    (
        "the chain unwinder is capped below a real chain",
        "svarupa/extract/typescript.py",
        "        for _ in range(16):",
        "        for _ in range(2):",
    ),
    (
        "a router name bound twice composes as one object",
        "svarupa/extract/semantics.py",
        "    shadowed = frozenset(n for n, k in binds.items() if k > 1)",
        "    shadowed = frozenset()",
    ),
    (
        "mount cycles stop being detected",
        "svarupa/extract/semantics.py",
        "        if router in path:",
        "        if False:",
    ),
    (
        "composed routes lose their mount lines",
        "svarupa/extract/semantics.py",
        '                out.append((_join_paths(a, pfx) if (a or pfx) else "", (*via, ev)))',
        '                out.append((_join_paths(a, pfx) if (a or pfx) else "", ()))',
    ),
    (
        "member calls cite the chain start again",
        "svarupa/extract/typescript.py",
        "            ev = self.evidence(path, prop.start_point[0], prop.start_point[0])",
        "            pass",
    ),
    (
        "mounted routes lose their trailing-slash spelling",
        "svarupa/extract/semantics.py",
        '    tail = sub if sub.startswith("/") else "/" + sub',
        '    tail = ("/" + sub.strip("/")) if sub.strip("/") else ""',
    ),
    (
        "the upgrade attribution stops mentioning re-spelling",
        "svarupa/lock/diff.py",
        '                    "build newly emits, no longer emits, or spells differently come "',
        '                    "build newly emits or no longer emits come "',
    ),
]


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
        if not (ROOT / "svarupa").exists():  # pragma: no cover - safety net
            shutil.copytree(backup, ROOT / "svarupa")
    if failures:
        print("\nnot caught:")
        for f in failures:
            print(f"  {f}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
