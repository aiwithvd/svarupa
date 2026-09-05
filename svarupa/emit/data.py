"""`graph.json` and `diagrams/*.json`: the machine-readable half of the output.

The HTML is for a person; these are for an agent, a diff tool, and P2's graph
explorer. Both are written with the same canonical ordering the lockfile uses,
so two runs of the same commit produce identical bytes and `git diff` on a
regenerated artifact shows only what actually changed.

Nothing here contains an absolute path. A path in the output is always
repository-relative: an absolute one would leak the author's home directory
into a shared artifact and would make byte-identity impossible between a laptop
and CI, which is the property the whole pipeline is built around.
"""

from __future__ import annotations

import json
from pathlib import Path

from svarupa.build import Graph
from svarupa.derive.base import DiagramSet
from svarupa.layout.geometry import Canvas
from svarupa.model import Evidence

__all__ = ["JSON_INDENT", "canvas_json", "diagram_json", "graph_json", "write_json"]

# Indented rather than minified. These files are read by humans during
# debugging and diffed by CI, and a one-line JSON document diffs as one changed
# line no matter what changed inside it, which is the same failure the lockfile
# grammar exists to avoid.
JSON_INDENT = 2


def write_json(path: Path, data: object) -> int:
    """Serialize canonically and return the byte count.

    `sort_keys` and a trailing newline are the determinism contract in two
    flags. `ensure_ascii=False` keeps a non-ASCII module name readable rather
    than turning it into escapes; the file is written UTF-8 explicitly, so the
    platform's default encoding never enters into it.
    """
    text = json.dumps(data, indent=JSON_INDENT, sort_keys=True, ensure_ascii=False)
    payload = text + "\n"
    path.write_text(payload, encoding="utf8", newline="\n")
    return len(payload.encode("utf8"))


def _evidence(items: tuple[Evidence, ...]) -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    for e in sorted(items):
        item: dict[str, object] = {
            "file": e.file,
            "start_line": e.start_line,
            "end_line": e.end_line,
        }
        if e.rev:
            item["rev"] = e.rev
        out.append(item)
    return out


def graph_json(graph: Graph) -> dict[str, object]:
    """The whole graph, evidence included.

    Regenerated rather than committed, per design §7.1: it holds the line
    numbers the lockfile deliberately omits, so the PR bot can join a lockfile
    delta against a fresh graph and show a reviewer the exact lines.
    """
    return {
        "schema": 1,
        "nodes": [
            {
                "id": n.id,
                "kind": n.kind.value,
                "label": n.label,
                "qualified_name": n.qualified_name,
                "lang": n.lang,
                "evidence": _evidence(n.evidence),
                "attrs": dict(n.attrs),
            }
            for n in sorted(graph.nodes.values(), key=lambda n: n.id)
        ],
        "edges": [
            {
                "src": e.src,
                "dst": e.dst,
                "kind": e.kind.value,
                "resolution": e.resolution.value,
                "arity": e.arity,
                "evidence": _evidence(e.evidence),
                "attrs": dict(e.attrs),
            }
            for e in sorted(graph.edges, key=lambda e: (e.src, e.dst, e.kind.value))
        ],
        "modules": sorted(graph.modules),
        "module_deps": sorted([a, b] for a, b in graph.module_deps),
        "scorecard": graph.scorecard.to_json_obj(),
    }


def canvas_json(canvas: Canvas) -> dict[str, object]:
    """One positioned view, coordinates included.

    Coordinates ship so a consumer redraws the diagram identically rather than
    laying it out again and getting something else. That is the same reason
    they are integers.
    """
    return {
        "id": canvas.spec_id,
        "kind": canvas.kind.value,
        "engine": canvas.engine,
        "title": canvas.title,
        "subtitle": canvas.subtitle,
        "parent": canvas.parent,
        "width": canvas.width,
        "height": canvas.height,
        # Waypoints are bends in a line, not claims: they carry no evidence
        # and are never drawn. Exporting them as boxes would hand a consumer
        # "boxes" that violate the every-box-cites-a-line contract; the
        # geometry they encode is already in each route's points.
        "boxes": [
            {
                "id": b.id,
                "label": b.label,
                "full_label": b.full_label,
                "kind": b.kind,
                "x": b.x,
                "y": b.y,
                "w": b.w,
                "h": b.h,
                "child": b.child_spec,
                "evidence": _evidence(b.evidence),
                "attrs": dict(b.attrs),
            }
            for b in canvas.boxes
            if b.id not in canvas.waypoints
        ],
        "routes": [
            {
                "src": r.src,
                "dst": r.dst,
                "label": r.label,
                "weight": r.weight,
                "resolution": r.resolution.value,
                "points": [[x, y] for x, y in r.points],
                "evidence": _evidence(r.evidence),
            }
            for r in canvas.routes
        ],
        "bands": [
            {"label": b.label, "y": b.y, "h": b.h, "members": list(b.members)}
            for b in canvas.bands
        ],
    }


def diagram_json(ds: DiagramSet, canvases: dict[str, Canvas]) -> dict[str, object]:
    """One diagram kind: its root, every drawn view, and what was withheld.

    Withheld views are named rather than omitted. A consumer that finds a
    `child` pointing at a view it does not have needs to know the difference
    between a bug and a diagram this run declined to draw.
    """
    return {
        "schema": 1,
        "kind": ds.kind.value,
        "root": ds.root,
        "views": {sid: canvas_json(canvases[sid]) for sid in sorted(canvases)},
        "withheld": sorted(set(ds.specs) - set(canvases)),
        "diagnostics": [json.loads(d.to_json()) for d in ds.diagnostics],
    }
