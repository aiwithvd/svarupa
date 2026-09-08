"""Mutation check for the review #20 fixes: each entry undoes one fix; a test
must go red. The suite list is the union of the files that pin them.
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
    "tests/test_graph_query.py",
    "tests/test_conceptual.py",
    "tests/test_derive.py",
    "tests/test_dataflow.py",
    "tests/test_cards.py",
    "tests/test_layout.py",
    "tests/test_emit.py",
    "tests/test_viewer_js.py",
]

MUTATIONS: list[tuple[str, str, str, str]] = [
    (
        "M1: lines read from the node's points again",
        "svarupa/extract/rationale.py",
        '    start = data.count(b"\\n", 0, node.start_byte) + 1\n    end = start + data.count(b"\\n", node.start_byte, node.end_byte)',
        "    start = node.start_point.row + 1\n    end = node.end_point.row + 1",
    ),
    (
        "M2: a module stands for one service only",
        "svarupa/derive/system.py",
        "            stand_in[m] = tuple(sorted(svcs))",
        "            stand_in[m] = tuple(sorted(svcs))[:1]",
    ),
    (
        "M3: a group borrows its anchor's id",
        "svarupa/derive/architecture.py",
        '    return anchor if len(members) == 1 else f"group:{anchor}"',
        "    return anchor",
    ),
    (
        "M3: a group is named for its anchor alone",
        "svarupa/derive/architecture.py",
        '            else f"{_label(anchor)} +{len(members) - 1}"',
        "            else _label(anchor)",
    ),
    (
        "M4: the chevron click no longer drills (JS)",
        "svarupa/emit/viewer.py",
        "    if (child && (ev.detail === 2 || ev.target.closest('.sv-drill'))) { openView(node, child); return; }",
        "    if (child && ev.detail === 2) { openView(node, child); return; }",
    ),
    (
        "M4: the passport hides its Open button (JS)",
        "svarupa/emit/viewer.py",
        "    openBtn.hidden = !child;\n    openBtn.onclick = child ? function () { openView(node, child); } : null;",
        "    openBtn.hidden = true;",
    ),
    (
        "M5: structural arrows say imports again",
        "svarupa/derive/architecture.py",
        "        label=\"\",\n        note=f\"{w} import{'s' if w != 1 else ''}\",",
        "        label=\"imports\",\n        note=f\"{w} import{'s' if w != 1 else ''}\",",
    ),
    (
        "M5: module deps stay flat past the budget",
        "svarupa/derive/architecture.py",
        "        if len(known) > MAX_TOP_BOXES:",
        "        if False:",
    ),
    (
        "M5: a dependency inside a part is also drawn between parts",
        "svarupa/derive/architecture.py",
        "            if ba == bb:\n                continue  # drawn one level down, where the two are distinct boxes",
        "            if False:\n                continue",
    ),
    (
        "S1: the externals band claims a cycle again",
        "svarupa/layout/engines.py",
        '                "external"\n                if all(b.id in external for b in members)\n                else "in a cycle"',
        '                "in a cycle"',
    ),
    (
        "S5: the settle ignores band labels",
        "svarupa/layout/engines.py",
        "        [band_label_rect(b, style) for b in bands]\n        + [region_label_rect(r, style) for r in regions],",
        "        [],",
    ),
    (
        "S4: an empty route path is joined into the label",
        "svarupa/derive/dataflow.py",
        "    paths = sorted({r.path for r in routes if r.path})",
        "    paths = sorted({r.path for r in routes})",
    ),
    (
        "S5: the label gate ignores band labels",
        "svarupa/layout/validate.py",
        '    fixed = [("band label " + b.label, *band_label_rect(b, style)) for b in canvas.bands] + [',
        "    fixed = [] + [",
    ),
    (
        "S6: flow columns always centre",
        "svarupa/layout/engines.py",
        "    if tallest <= FLOW_CENTRE_MAX_H:",
        "    if True:",
    ),
    (
        "S7: directory modules leave the graph again",
        "svarupa/emit/data.py",
        "        + why_nodes\n        + mod_nodes,",
        "        + why_nodes,",
    ),
    (
        "S8: chapters star image-only services only",
        "svarupa/emit/cards.py",
        '            or n.kind in ("service", "backend", "frontend", "security", "endpoint")',
        '            or n.kind in ("service", "endpoint")',
    ),
    (
        "S9: Escape does nothing (JS)",
        "svarupa/emit/viewer.py",
        "    if (ev.key === 'Escape') {\n      panel.classList.remove('is-open'); clearPins(); clearFocus();\n      return;\n    }",
        "",
    ),
    (
        "S9: boxes are not focusable",
        "svarupa/emit/svg.py",
        '        tabindex="0",\n',
        "",
    ),
    (
        "C1: the theme button names the target again (JS)",
        "svarupa/emit/viewer.py",
        "      themeBtn.textContent = '☾ dark';",
        "      themeBtn.textContent = '☀ light';",
    ),
    (
        "C3: only the first store arrow carries its verb",
        "svarupa/derive/architecture.py",
        "        for src, evs in sorted(by_src.items()):",
        "        for k, (src, evs) in enumerate(sorted(by_src.items())):\n            if k:\n                continue",
    ),
    (
        "C4: sublabels are cut from the head",
        "svarupa/layout/engines.py",
        "            sanitize(n.sublabel), style.sublabel_font_size, style.text_budget, keep_tail=False",
        "            sanitize(n.sublabel), style.sublabel_font_size, style.text_budget",
    ),
    (
        "C6: an empty directory is analyzed",
        "svarupa/detect.py",
        '    if not ordered and not any(p.name != ".git" for p in root_path.iterdir()):',
        "    if False:",
    ),
    (
        "C10: one story still hides behind a menu",
        "svarupa/derive/dataflow.py",
        "        if len(top) == 1 and top[0].child_spec in specs:",
        "        if False:",
    ),
    (
        "C12: connection rows show ids, not labels (JS)",
        "svarupa/emit/viewer.py",
        "    return t ? t.textContent.split('\\\\n')[0] : id;",
        "    return id;",
    ),
    (
        "S11: the repository is a bare word in the heading again",
        "svarupa/emit/viewer.py",
        '                    join((tag("small", esc("repo")), esc(root))),',
        "                    esc(root),",
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
                [str(PY), "-m", "pytest", *SUITE, "-q", "-x", "-p", "no:cacheprovider"],
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
