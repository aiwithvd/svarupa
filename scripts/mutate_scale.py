"""Mutation check for the scale wave: caps, reachability honesty, layout
demand-sizing, and the validate rewrite's equivalence guards.

Same discipline as the sibling harnesses (review #9's lesson): the list is
written against the *claims the wave made* — the stage cap and its stated
remainder, reach-from-all-handlers, progressive hop eligibility, demand-driven
gap sizing with the strip kept inside the canvas, the wrapped fan, conditional
residue alignment, the expansion host cap, and the route-overlap sweep's
endpoint exemption. Each entry disables exactly one claim; a test must go red.
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
    "tests/test_layout.py",
    "tests/test_emit.py",
]

MUTATIONS: list[tuple[str, str, str, str]] = [
    (
        "the stage cap keeps everything",
        "svarupa/derive/dataflow.py",
        "    kept = ranked[:MAX_STAGE_BOXES]",
        "    kept = ranked",
    ),
    (
        "the data-flow subtitle hides the omitted remainder",
        "svarupa/derive/dataflow.py",
        "                + _remainder(clauses)",
        '                + ""',
    ),
    (
        "reach is computed from capped handlers again (review #26 F1)",
        "svarupa/derive/dataflow.py",
        "        hops, entry = _reach(set(all_handlers), imports, DOMAIN_DEPTH)",
        "        hops, entry = _reach(set(handlers), imports, DOMAIN_DEPTH)",
    ),
    (
        "a hop-N box needs no drawn parent again (review #26 F3)",
        "svarupa/derive/dataflow.py",
        "            eligible = [m for m in members if parents.get(m, set()) & kept]",
        "            eligible = members",
    ),
    (
        "cap selection prefers the least connected",
        "svarupa/derive/dataflow.py",
        "    ranked = sorted(members, key=lambda m: (-degree.get(m, 0), m))",
        "    ranked = sorted(members, key=lambda m: (-degree.get(m, 0), m), reverse=True)",
    ),
    (
        "the widened canvas forgets the environment strip again (review #26 F2)",
        "svarupa/layout/engines.py",
        "        width = max(x - gap_w[-1] + style.margin + pad + style.lane_gutter, strip_right)",
        "        width = x - gap_w[-1] + style.margin + pad + style.lane_gutter",
    ),
    (
        "column gaps never widen",
        "svarupa/layout/engines.py",
        "    if any(w != col_gap for w in gap_w):",
        "    if False:",
    ),
    (
        "the exit fan walks off a too-short box again",
        "svarupa/layout/engines.py",
        "        return b.y + max(1, step) * (1 + (nth - 1) % cycle)",
        "        return b.y + max(1, step) * (1 + (nth - 1) % slots)",
    ),
    (
        "residue alignment applies even when it collapses ports",
        "svarupa/layout/engines.py",
        "        if step < 4:\n            return x",
        "        if False:\n            return x",
    ),
    (
        "in-place expansions are pre-rendered for every host again",
        "svarupa/emit/viewer.py",
        "        if sum(1 for b in canvas.boxes if b.id not in canvas.waypoints) > MAX_EXPANSION_HOST_BOXES:\n            continue",
        "        if False:\n            continue",
    ),
    (
        "a bundle into one box is flagged as two arrows on one line",
        "svarupa/layout/validate.py",
        "                    or dsts[p] == dsts[q]",
        "",
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
