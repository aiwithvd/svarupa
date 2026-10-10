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
* a pom that declares entities is refused.
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
    "tests/test_golden_repos.py",
    "tests/test_detect.py",
    "tests/test_metrics.py",
    "tests/test_health.py",
    "tests/test_health_outputs.py",
]

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
        "            if kind in spec.function_types and node.is_named:\n                continue  # measured on its own\n",
        "",
    ),
    (
        "else if nests deeper again",
        "svarupa/extract/packs/walker.py",
        '                kind == "if_statement" and parent in spec.else_if_parents\n',
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
