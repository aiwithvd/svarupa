"""Mutation check for Wave B: data flow, request flow, stage frames, the
not-derivable notes and the column-side corridor.

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
SUITE = ["tests/test_dataflow.py", "tests/test_conceptual_review16.py"]

MUTATIONS: list[tuple[str, str, str, str]] = [
    (
        "the domain reach ignores DOMAIN_DEPTH (whole codebase becomes the domain)",
        "svarupa/derive/dataflow.py",
        "    for hop in range(1, depth + 1):",
        "    for hop in range(1, depth + 2):",
    ),
    (
        "unstaged modules are dropped silently",
        "svarupa/derive/dataflow.py",
        "        if unstaged:\n            diags.append(",
        "        if False:\n            diags.append(",
    ),
    (
        "the data-flow stage frames are not drawn",
        "svarupa/derive/dataflow.py",
        "            regions=_stage_regions(nodes, anchor_ev),",
        "            regions=(),",
    ),
    (
        "the ingress box is a plain module",
        "svarupa/derive/dataflow.py",
        '        id=f"in:{module}",\n        label=shown if len(shown) <= 40 else shown[:37] + "…",\n        kind="endpoint",',
        '        id=f"in:{module}",\n        label=shown if len(shown) <= 40 else shown[:37] + "…",\n        kind="module",',
    ),
    (
        "the ingress box cites the module instead of the route lines",
        "svarupa/derive/dataflow.py",
        "        evidence=tuple(sorted({r.evidence for r in routes}))[:MAX_EVIDENCE_PER_BOX],",
        "        evidence=module_evidence(graph, module),",
    ),
    (
        "the ingress sublabel stops counting routes",
        "svarupa/derive/dataflow.py",
        "        sublabel=f\"{len(routes)} route{'s' if len(routes) != 1 else ''}\",",
        '        sublabel="routes",',
    ),
    (
        "data-flow import edges run in both directions",
        "svarupa/derive/dataflow.py",
        "                if b in drawn and a in drawn and hops.get(b, 0) > hops.get(a, 0):",
        "                if b in drawn and a in drawn and hops.get(b, 0) != hops.get(a, 0):",
    ),
    (
        "the domain hop is not recorded",
        "svarupa/derive/dataflow.py",
        '                        attrs=(*node.attrs, ("hop", str(hops[m]))),',
        '                        attrs=(*node.attrs, ("hop", "1")),',
    ),
    (
        "request flow claims call order",
        "svarupa/derive/dataflow.py",
        'f"hops; reachability, not call order"',
        'f"hops; in call order"',
    ),
    (
        "request-flow hops all read as the handler",
        "svarupa/derive/dataflow.py",
        '                    attrs=(("layer", str(hop)), ("hop", str(hop))),',
        '                    attrs=(("layer", str(hop)), ("hop", "0")),',
    ),
    (
        "request-flow hop frames are not drawn",
        "svarupa/derive/dataflow.py",
        "            regions=tuple(regions),\n        )\n        return sid",
        "            regions=(),\n        )\n        return sid",
    ),
    (
        "one request story is not one endpoint group",
        "svarupa/derive/dataflow.py",
        "            child = self._story(graph, m, imports, roles, specs, diags, arch)",
        "            child = None; self._story(graph, m, imports, roles, specs, diags, arch)",
    ),
    (
        "a deriver's findings stop reaching the report",
        "svarupa/emit/__init__.py",
        "        problems.extend(ds.diagnostics)\n",
        "",
    ),
    (
        "informational findings are counted but not named in the report",
        "svarupa/emit/report.py",
        '        _listing(_grouped(infos), "Nothing was left undrawn for an informational reason."),',
        '        _listing([], "Nothing was left undrawn for an informational reason."),',
    ),
    (
        "the unstaged root module is named as nothing",
        "svarupa/derive/dataflow.py",
        '                    subject=", ".join(m or "(repo root)" for m in unstaged[:5]),',
        '                    subject=", ".join(unstaged[:5]),',
    ),
    (
        "lifecycle and workflow vanish instead of being named as not derivable",
        "svarupa/derive/__init__.py",
        "    notes: list[str] = list(NOT_DERIVABLE)",
        "    notes: list[str] = []",
    ),
    (
        "the corridor drops at the target box's edge again",
        "svarupa/layout/engines.py",
        "                else (col_left[lb] - 10 - 4 * entries[dst].index(src))",
        "                else (b.x - 10 - 4 * entries[dst].index(src))",
    ),
    (
        "the corridor climbs at the source box's edge again",
        "svarupa/layout/engines.py",
        "            out_x = col_right[la] + 10 + 4 * (exits[src].index(dst))",
        "            out_x = a.right + 10 + 4 * (exits[src].index(dst))",
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
