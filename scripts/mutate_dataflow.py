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
SUITE = [
    "tests/test_dataflow.py",
    "tests/test_conceptual_review16.py",
    "tests/test_layout.py",
    "tests/test_emit.py",
]

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
        "            regions=_stage_regions(nodes, cite),",
        "            regions=(),",
    ),
    (
        "the ingress box is a plain module",
        "svarupa/derive/dataflow.py",
        '        id=f"in:{module}",\n        label=_cut(paths),\n        kind="endpoint",',
        '        id=f"in:{module}",\n        label=_cut(paths),\n        kind="module",',
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
        "                elif hops.get(b, 0) < hops.get(a, 0):\n                    upstream += 1\n                else:\n                    edges.append(_import_edge(a, b, ev, via))\n        edges.extend(e for e in ext_edges if e.src in drawn)",
        "                elif False:\n                    upstream += 1\n                else:\n                    edges.append(_import_edge(a, b, ev, via))\n        edges.extend(e for e in ext_edges if e.src in drawn)",
    ),
    (
        "imports against the flow vanish without a count",
        "svarupa/derive/dataflow.py",
        '    return f"; {\' and \'.join(parts)} not drawn" if parts else ""',
        '    return ""',
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
        '                    attrs=(*node.attrs, ("hop", str(hop))),',
        '                    attrs=(*node.attrs, ("hop", "0")),',
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
        "            in_x = col_left[lb] - 10 - 4 * drop_k[(src, dst)]\n            points = ((a.right, ay), (in_x, ay), (in_x, by), (b.x, by))",
        "            in_x = b.x - 10 - 4 * drop_k[(src, dst)]\n            points = ((a.right, ay), (in_x, ay), (in_x, by), (b.x, by))",
    ),
    (
        "the corridor climbs at the source box's edge again",
        "svarupa/layout/engines.py",
        "            out_x = col_right[la] + 10 + 4 * climb_k[(src, dst)]",
        "            out_x = a.right + 10 + 4 * climb_k[(src, dst)]",
    ),
    # --- review #17: the reviewer's surviving mutations, now on the list ---
    (
        "data-flow handler boxes lose their drill door",
        "svarupa/derive/dataflow.py",
        "                    sublabel=handler.sublabel,\n                    child_spec=arch.components_for(graph, m, ROOT, specs, diags),",
        "                    sublabel=handler.sublabel,\n                    child_spec=None,",
    ),
    (
        "data-flow domain boxes lose their drill door",
        "svarupa/derive/dataflow.py",
        "                        sublabel=node.sublabel,\n                        child_spec=arch.components_for(graph, m, ROOT, specs, diags),",
        "                        sublabel=node.sublabel,\n                        child_spec=None,",
    ),
    (
        "request-story boxes lose their drill door",
        "svarupa/derive/dataflow.py",
        "                    child_spec=arch.components_for(graph, m, sid, specs, diags),",
        "                    child_spec=None,",
    ),
    (
        "the handles edge cites the module instead of the route lines",
        "svarupa/derive/dataflow.py",
        '                        label="handles",\n                        evidence=ingress.evidence,',
        '                        label="handles",\n                        evidence=module_evidence(graph, m),',
    ),
    (
        "the handles edge loses its emphasis",
        "svarupa/derive/dataflow.py",
        '                        variant="emphasis",',
        '                        variant="default",',
    ),
    (
        "ingress labels are cut mid-segment",
        "svarupa/derive/dataflow.py",
        '    cut = max(keep.rfind(", "), keep.rfind("/", 1))',
        "    cut = -1",
    ),
    (
        "lateral imports are drawn as flow arrows",
        "svarupa/derive/dataflow.py",
        "                if hops.get(b, 0) == hops.get(a, 0):\n                    lateral += 1\n                elif hops.get(b, 0) < hops.get(a, 0):\n                    upstream += 1\n                else:\n                    edges.append(_import_edge(a, b, ev, via))\n        edges.extend(e for e in ext_edges if e.src in drawn)",
        "                if False:\n                    lateral += 1\n                elif hops.get(b, 0) < hops.get(a, 0):\n                    upstream += 1\n                else:\n                    edges.append(_import_edge(a, b, ev, via))\n        edges.extend(e for e in ext_edges if e.src in drawn)",
    ),
    (
        "handler boxes lose the backend kind",
        "svarupa/derive/dataflow.py",
        '        else "backend"\n        if {"api", "worker", "cli"} & set(held)',
        '        else "module"\n        if {"api", "worker", "cli"} & set(held)',
    ),
    (
        "SVA-R-007 becomes a warning",
        "svarupa/derive/dataflow.py",
        '                    code="SVA-R-007",\n                    severity=Severity.INFO,',
        '                    code="SVA-R-007",\n                    severity=Severity.WARNING,',
    ),
    (
        "stage frames cite the first member's line instead of the placing line",
        "svarupa/derive/dataflow.py",
        "            cited: list[Evidence] = []\n            for m in members:\n                cited.extend(cite.get(m, ())[:1])",
        "            cited: list[Evidence] = []\n            for m in members:\n                cited.extend(next(n for n in nodes if n.id == m).evidence[:1])",
    ),
    (
        "hop frames cite the member's line instead of the import that reached it",
        "svarupa/derive/dataflow.py",
        "                cited: list[Evidence] = []\n                for m in members:\n                    cited.extend(cite.get(m, ())[:1])",
        "                cited: list[Evidence] = []\n                for m in members:\n                    cited.extend(next(n for n in nodes if n.id == m).evidence[:1])",
    ),
    (
        "ingress routes stop being gated on architecture eligibility",
        "svarupa/derive/dataflow.py",
        "        if r.file in graph.architecture_paths and module_of(r.file) == module",
        "        if module_of(r.file) == module",
    ),
    (
        "externals sit in a fixed column again",
        "svarupa/derive/dataflow.py",
        "        ext_layer = 2 + max((hops[m] for m in domain if m in drawn), default=0)",
        "        ext_layer = 3",
    ),
    (
        "request-story boxes drop their roles and stage",
        "svarupa/derive/dataflow.py",
        '                    attrs=(*node.attrs, ("hop", str(hop))),',
        '                    attrs=(("layer", str(hop)), ("hop", str(hop))),',
    ),
    (
        "hop sublabel prefix dropped",
        "svarupa/derive/dataflow.py",
        '                    sublabel=(f"hop {hop} · " if hop else "handler · ") + node.sublabel,',
        "                    sublabel=node.sublabel,",
    ),
    (
        "an edge through generated code hides what it went through",
        "svarupa/derive/dataflow.py",
        '        note += " via " + ", ".join(via)',
        "        pass",
    ),
    (
        "reachability stops at generated code",
        "svarupa/derive/dataflow.py",
        "        elif c not in seen:\n            _walk(raw, modules, origin, c, ev, (*via, c), seen | {c}, found)",
        "        elif False:\n            _walk(raw, modules, origin, c, ev, (*via, c), seen | {c}, found)",
    ),
    (
        "identical ingress labels stay identical",
        "svarupa/derive/dataflow.py",
        '            if n.kind == "endpoint" and seen_labels[n.label] > 1:',
        "            if False:",
    ),
    (
        "expansion ids name the child instead of the host view",
        "svarupa/layout/compose.py",
        '        id=f"{parent.id}//{host_id}{EXPANDED_SUFFIX}",',
        '        id=f"{child.spec_id}{EXPANDED_SUFFIX}",',
    ),
    (
        "stage frames vanish from diagrams JSON",
        "svarupa/emit/data.py",
        "            for g in canvas.regions",
        "            for g in ()",
    ),
    (
        "every frame is a boundary again in the passport",
        "svarupa/emit/svg.py",
        "        data_kind=region.kind,",
        '        data_kind="boundary",',
    ),
    (
        "two edges on one line are not a finding",
        "svarupa/layout/validate.py",
        "    out.extend(_check_route_overlap(canvas))\n",
        "",
    ),
    (
        "corridor climbs from one column share an x again",
        "svarupa/layout/engines.py",
        "            out_x = col_right[la] + 10 + 4 * climb_k[(src, dst)]",
        "            out_x = col_right[la] + 10",
    ),
    (
        "entry heights meet exit heights across a gap again",
        "svarupa/layout/engines.py",
        "        return entry_fan_y(placed[dst], left.index(src) + 1, len(left))",
        "        return fan_y(placed[dst], left.index(src) + 1, len(left))",
    ),
    (
        "the report shows one finding per code",
        "svarupa/emit/report.py",
        "        for d in group[:3]:",
        "        for d in group[:1]:",
    ),
    (
        "the report's trailing count counts lines",
        "svarupa/emit/report.py",
        "    rest = len(diags) - sum(n for _, n in kept)",
        "    rest = len(lines) - len(kept)",
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
