"""Mutation check for Wave A of the conceptual redesign.

Each entry deletes a property the wave names; a test must go red.
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
    "tests/test_conceptual.py",
    "tests/test_conceptual_review16.py",
    "tests/test_emit.py",
    "tests/test_layout.py",
]

MUTATIONS: list[tuple[str, str, str, str]] = [
    (
        "the dotted-prefix vocabulary match is dropped (fastapi.security reads as nothing)",
        "svarupa/extract/vocabulary.py",
        "    if candidates:\n        key, category, label = max(candidates, key=lambda c: len(c[0]))\n        return (category, label, key)",
        "    if False:\n        key, category, label = max(candidates, key=lambda c: len(c[0]))\n        return (category, label, key)",
    ),
    (
        "auth imports stop giving the auth role",
        "svarupa/build.py",
        '        if x.category == "security":\n            roles.setdefault(_module_of(x.file), set()).add("auth")',
        '        if False:\n            roles.setdefault(_module_of(x.file), set()).add("auth")',
    ),
    (
        "roles stop mapping to archetypes",
        "svarupa/derive/architecture.py",
        '    return _ARCHETYPE[role] if role else "module"',
        '    return role if role else "module"',
    ),
    (
        "a plain module's sublabel becomes its path",
        "svarupa/derive/architecture.py",
        "            parts.append(f\"{mod.file_count} file{'s' if mod.file_count != 1 else ''}\")",
        "            parts.append(module)",
    ),
    (
        "security and frontend imports become external boxes",
        "svarupa/derive/architecture.py",
        "        if x.file not in graph.architecture_paths or x.category not in _EXTERNAL_VERB:",
        "        if x.file not in graph.architecture_paths:",
    ),
    (
        "external edges lose their dashed variant",
        "svarupa/derive/architecture.py",
        '                    variant="dashed",\n                )\n            )\n    return nodes, edges',
        '                    variant="default",\n                )\n            )\n    return nodes, edges',
    ),
    (
        "root-context services wrap everything",
        "svarupa/derive/architecture.py",
        "        if not ctx:\n            continue\n        inside = modules_under(graph, ctx)",
        "        if ctx is None:\n            continue\n        inside = modules_under(graph, ctx)",
    ),
    (
        "a boundary is drawn around outsiders",
        "svarupa/layout/engines.py",
        "        if intruders or overlapping:",
        "        if overlapping:",
    ),
    (
        "route labels stop settling and stamp over each other",
        "svarupa/layout/engines.py",
        "            if clear(cx, cy, r.label_w, r):\n                chosen = (cx, cy)\n                break",
        "            chosen = (cx, cy)\n            break",
    ),
    (
        "structural import arrows fall silent again",
        "svarupa/derive/architecture.py",
        '        label="imports",\n        note=f"{w} import',
        '        label="",\n        note=f"{w} import',
    ),
    (
        "external boxes stop sinking to the bottom layer",
        "svarupa/layout/engines.py",
        "    if sink_externals and external and len(external) < len(levels):",
        "    if False:",
    ),
    (
        "every external arrow draws its verb",
        "svarupa/derive/architecture.py",
        '                    label=_EXTERNAL_VERB[category] if k == 0 else "",',
        "                    label=_EXTERNAL_VERB[category],",
    ),
    (
        "root-context services claim the whole repository's routes",
        "svarupa/derive/system.py",
        '                if "api" in held and ctx:',
        '                if "api" in held:',
    ),
    (
        "rows stop grouping by boundary",
        "svarupa/layout/engines.py",
        "    return [sorted(row, key=lambda b: rank.get(b.id, last)) for row in rows]",
        "    return rows",
    ),
    (
        "externals sink in the flow engine again",
        "svarupa/layout/engines.py",
        "    levels = _levels(spec, boxes, sink_externals=False)",
        "    levels = _levels(spec, boxes)",
    ),
    (
        "an INFO diagnostic withholds the canvas again",
        "svarupa/layout/__init__.py",
        "        if any(d.severity is Severity.ERROR for d in found):",
        "        if found:",
    ),
    (
        "waypoints count as boundary intruders again",
        "svarupa/layout/engines.py",
        "            and b.id not in waypoints\n            and b.x < right",
        "            and b.x < right",
    ),
    (
        "a vocabulary hit stops asking the resolver",
        "svarupa/extract/semantics.py",
        "        if resolves is not None and resolves(imp.specifier, f.path, imp.level, f.lang):",
        "        if False:",
    ),
    (
        "type-only imports talk to stores again",
        "svarupa/extract/semantics.py",
        "        if imp.type_only:\n            continue",
        "        if False:\n            continue",
    ),
    (
        "externals dedupe by bare root again (google.* collapses)",
        "svarupa/extract/semantics.py",
        "        if key not in out:\n            out[key] = ExternalFact(",
        '        if imp.specifier.split(".")[0] not in out:\n            out[imp.specifier.split(".")[0]] = ExternalFact(',
    ),
    (
        "build contexts are stripped instead of anchored",
        "svarupa/build.py",
        '    joined = posixpath.normpath(posixpath.join(folder, raw.replace("\\\\", "/")))',
        '    joined = raw.lstrip("./").rstrip("/") or "."',
    ),
    (
        "an escaping build context wraps the in-repo decoy",
        "svarupa/build.py",
        '    if joined.startswith("..") or posixpath.isabs(joined):',
        "    if False:",
    ),
    (
        "a group with outsiders sits inside a boundary",
        "svarupa/derive/architecture.py",
        "        members = sorted(box for box, mods in represented.items() if mods and mods <= inside)",
        "        members = sorted(box for box, mods in represented.items() if mods & inside)",
    ),
    (
        "compose stores and import stores stop merging",
        "svarupa/derive/system.py",
        "            target = canonical.get(n.label)",
        "            target = None",
    ),
    (
        "image-only services become backends again",
        "svarupa/derive/system.py",
        '        archetype = {"datastore": "database", "queue": "messagebus", "service": "service"}',
        '        archetype = {"datastore": "database", "queue": "messagebus", "service": "backend"}',
    ),
    (
        "the kind class leaves the node group",
        "svarupa/emit/svg.py",
        '        + f" sv-kind-{_slug(box.kind)}"\n',
        '        + ""\n',
    ),
    (
        "expanded views lose their scopes",
        "svarupa/emit/svg.py",
        '                tag("g", embedded, data_scope="child"),',
        "                embedded,",
    ),
    (
        "a boundary cites the service key instead of build:",
        "svarupa/derive/architecture.py",
        "            if build_line and build_line.isdigit():",
        "            if False:",
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
