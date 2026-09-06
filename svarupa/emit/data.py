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
from svarupa.extract.rationale import RationaleFact
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


# Graphify's typed sub-relation, derived from the edge kind. A consumer
# filters on `context` without knowing thirteen kinds.
_CONTEXT: dict[str, str] = {
    "imports": "import",
    "calls": "call",
    "inherits": "inherit",
    "implements": "inherit",
    "references": "reference",
    "exposes": "route",
    "reads": "store",
    "writes": "store",
    "depends_on": "depends_on",
    "publishes": "message",
    "consumes": "message",
    "contains": "contain",
    "deploys": "deploy",
}

_EXTERNAL_KIND = {"database": "datastore", "messagebus": "queue", "cloud": "resource"}
_EXTERNAL_CONTEXT = {"database": "store", "messagebus": "message", "cloud": "cloud"}


def _fact_nodes_and_edges(
    graph: Graph,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    """Routes and externals as graph nodes, so "what talks to MongoDB" and
    "which module serves /orders" are graph queries. Gated on architecture
    eligibility like the diagrams; each cites the declaring or importing line."""
    nodes: list[dict[str, object]] = []
    edges: list[dict[str, object]] = []
    for r in sorted(graph.routes):
        if r.file not in graph.architecture_paths:
            continue
        rid = f"{r.file}#route:{r.method} {r.path}"
        nodes.append(
            {
                "id": rid,
                "kind": "endpoint",
                "label": f"{r.method} {r.path}",
                "qualified_name": rid,
                "lang": None,
                "evidence": _evidence((r.evidence, *r.via)),
                "attrs": {
                    "framework": r.framework,
                    "handler": r.handler,
                    "method": r.method,
                    "path": r.path,
                },
            }
        )
        edges.append(
            {
                "src": r.file,
                "dst": rid,
                "kind": "exposes",
                "context": "route",
                "resolution": "resolved",
                "arity": 1,
                "evidence": _evidence((r.evidence,)),
                "attrs": {},
            }
        )
    seen_ext: dict[str, list[Evidence]] = {}
    ext_edges: dict[tuple[str, str], list[Evidence]] = {}
    packages: dict[str, set[str]] = {}
    for x in sorted(graph.externals):
        if x.file not in graph.architecture_paths or x.category not in _EXTERNAL_KIND:
            continue
        xid = f"ext:{x.category}:{x.label}"
        seen_ext.setdefault(xid, []).append(x.evidence)
        packages.setdefault(xid, set()).add(x.package)
        ext_edges.setdefault((x.file, xid), []).append(x.evidence)
    for xid in sorted(seen_ext):
        category = xid.split(":")[1]
        nodes.append(
            {
                "id": xid,
                "kind": _EXTERNAL_KIND[category],
                "label": xid.split(":", 2)[2],
                "qualified_name": xid,
                "lang": None,
                "evidence": _evidence(tuple(seen_ext[xid])),
                "attrs": {"category": category, "packages": ", ".join(sorted(packages[xid]))},
            }
        )
    for (file, xid), ev in sorted(ext_edges.items()):
        edges.append(
            {
                "src": file,
                "dst": xid,
                "kind": "depends_on",
                "context": _EXTERNAL_CONTEXT[xid.split(":")[1]],
                "resolution": "resolved",
                "arity": 1,
                "evidence": _evidence(tuple(ev)),
                "attrs": {},
            }
        )
    return nodes, edges


def _rationale_nodes_and_edges(
    graph: Graph, rationale: tuple[RationaleFact, ...]
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    """Docstrings and marker comments as nodes, each `rationale_for` the
    innermost definition that contains its line, else the module. A fact in
    a file with no module node is dropped: no anchor, no claim."""
    ranges: dict[str, list[tuple[int, int, str]]] = {}
    for n in graph.nodes.values():
        if n.kind.value in ("function", "class", "method") and n.evidence:
            e = n.evidence[0]
            ranges.setdefault(e.file, []).append((e.start_line, e.end_line, n.id))
    nodes: list[dict[str, object]] = []
    edges: list[dict[str, object]] = []
    for f in rationale:
        # The innermost definition whose range holds the line: a class
        # docstring sits inside its class, a NOTE inside its function.
        inner = [(e - s, nid) for s, e, nid in ranges.get(f.file, []) if s <= f.line <= e]
        target: str | None = min(inner)[1] if inner else None
        if target is None and f.file in graph.nodes:
            target = f.file
        if target is None:
            continue
        rid = f"{f.file}#rationale:{f.line}"
        nodes.append(
            {
                "id": rid,
                "kind": "rationale",
                "label": f.text if len(f.text) <= 60 else f.text[:59] + "…",
                "qualified_name": f"{f.file}:{f.line}",
                "lang": None,
                "evidence": _evidence((f.evidence,)),
                "attrs": {"kind": f.kind, "text": f.text},
            }
        )
        edges.append(
            {
                "src": rid,
                "dst": target,
                "kind": "rationale_for",
                "context": "rationale",
                "resolution": "resolved",
                "arity": 1,
                "evidence": _evidence((f.evidence,)),
                "attrs": {},
            }
        )
    return nodes, edges


def graph_json(
    graph: Graph,
    rationale: tuple[RationaleFact, ...] = (),
    built_at_commit: str | None = None,
) -> dict[str, object]:
    """The whole graph, evidence included.

    Regenerated rather than committed, per design §7.1: it holds the line
    numbers the lockfile deliberately omits, so the PR bot can join a lockfile
    delta against a fresh graph and show a reviewer the exact lines.

    Schema 2 (design section 4, Graphify-class): every edge carries a typed
    `context`; routes and externals are nodes; docstrings and marker comments
    are `rationale` nodes; `built_at_commit` names the checkout; `hyperedges`
    is reserved and empty. Communities are deliberately absent from nodes:
    they are presentation and would churn every node on one added import.
    """
    fact_nodes, fact_edges = _fact_nodes_and_edges(graph)
    why_nodes, why_edges = _rationale_nodes_and_edges(graph, rationale)
    return {
        "schema": 2,
        "built_at_commit": built_at_commit,
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
        ]
        + fact_nodes
        + why_nodes,
        "edges": [
            {
                "src": e.src,
                "dst": e.dst,
                "kind": e.kind.value,
                "context": _CONTEXT.get(e.kind.value, e.kind.value),
                "resolution": e.resolution.value,
                "arity": e.arity,
                "evidence": _evidence(e.evidence),
                "attrs": dict(e.attrs),
            }
            for e in sorted(graph.edges, key=lambda e: (e.src, e.dst, e.kind.value))
        ]
        + fact_edges
        + why_edges,
        "hyperedges": [],
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
                "sublabel": b.sublabel,
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
                "note": r.note,
                "variant": r.variant,
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
        # Boundaries and stage frames are claims with evidence, so they ship
        # like boxes do. Without them the JSON of a data-flow view was a
        # dependency graph with a `layer` attr: the stages existed only in
        # the SVG (review #17 F4).
        "regions": [
            {
                "id": g.id,
                "label": g.label,
                "kind": g.kind,
                "x": g.x,
                "y": g.y,
                "w": g.w,
                "h": g.h,
                "members": list(g.members),
                "evidence": _evidence(g.evidence),
            }
            for g in canvas.regions
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
