"""Query the knowledge graph: Graphify's function names, exact answers.

Graphify's MCP tools (`query_graph`, `get_node`, `get_neighbors`,
`shortest_path`, `affected`, `god_nodes`, `graph_stats`) are what agents
already know how to call, so the names are kept. Two deliberate differences,
both from its teardown:

* **Exact match by default, with an explicit ambiguity list.** Graphify's
  fuzzy substitution answered `path "to_json" ...` for `to_html()`. Here a
  label that matches several nodes returns the candidates and no answer, and
  a label that matches nothing says so.
* **Structured output.** Every function returns a JSON object with the
  evidence on each hit, never prose an agent must parse. Truncation is
  announced with a banner and the count.

The index is built from `graph.json`, so a query needs an artifact, not a
rescan, and answers exactly what the artifact shows.
"""

from __future__ import annotations

import json
import re
from collections import deque
from pathlib import Path
from typing import Any

from svarupa.diagnostics import Diagnostic, DiagnosticError, Severity

__all__ = [
    "FUNCTIONS",
    "GraphIndex",
    "affected",
    "get_neighbors",
    "get_node",
    "god_nodes",
    "graph_stats",
    "query_graph",
    "shortest_path",
]

FUNCTIONS = (
    "query_graph",
    "get_node",
    "get_neighbors",
    "shortest_path",
    "affected",
    "god_nodes",
    "graph_stats",
)

Node = dict[str, Any]
Edge = dict[str, Any]


class GraphIndex:
    """`graph.json` with the lookups a query needs, built once."""

    def __init__(self, data: dict[str, Any]) -> None:
        self.data = data
        self.nodes: dict[str, Node] = {n["id"]: n for n in data.get("nodes", [])}
        self.edges: list[Edge] = list(data.get("edges", []))
        self.out: dict[str, list[Edge]] = {}
        self.inc: dict[str, list[Edge]] = {}
        for e in self.edges:
            self.out.setdefault(e["src"], []).append(e)
            self.inc.setdefault(e["dst"], []).append(e)
        self.by_label: dict[str, list[str]] = {}
        self.by_qualified: dict[str, list[str]] = {}
        for nid, n in self.nodes.items():
            self.by_label.setdefault(str(n.get("label", "")), []).append(nid)
            self.by_qualified.setdefault(str(n.get("qualified_name", "")), []).append(nid)

    @classmethod
    def load(cls, path: Path) -> GraphIndex:
        """From an artifact directory or a `graph.json` path; refuses otherwise."""
        candidate = path / "graph.json" if path.is_dir() else path
        if not candidate.is_file():
            raise DiagnosticError(
                Diagnostic(
                    code="SVA-Q-001",
                    severity=Severity.ERROR,
                    message="no graph.json here; run `svarupa <repo>` first and query its artifact",
                    subject=str(path),
                    suggested_fixes=(
                        "svarupa <repo> --out <dir> writes <dir>/graph.json",
                        "pass the artifact directory or the graph.json path",
                    ),
                )
            )
        return cls(json.loads(candidate.read_text(encoding="utf8")))

    def resolve(self, label: str) -> list[str]:
        """Node ids an exact label names: id, then qualified name, then label."""
        if label in self.nodes:
            return [label]
        if label in self.by_qualified:
            return sorted(self.by_qualified[label])
        return sorted(self.by_label.get(label, []))


def _brief(n: Node) -> Node:
    return {
        "id": n["id"],
        "kind": n.get("kind"),
        "label": n.get("label"),
        "qualified_name": n.get("qualified_name"),
        "evidence": n.get("evidence", []),
    }


def _edge_brief(e: Edge) -> Edge:
    return {
        "src": e["src"],
        "dst": e["dst"],
        "kind": e.get("kind"),
        "context": e.get("context"),
        "resolution": e.get("resolution"),
        "evidence": e.get("evidence", []),
    }


def _one(index: GraphIndex, label: str, role: str) -> tuple[str | None, dict[str, Any]]:
    """Exactly one node for `label`, or the structured reason there is not."""
    ids = index.resolve(label)
    if len(ids) == 1:
        return ids[0], {}
    if not ids:
        return None, {
            role: label,
            "match": None,
            "reason": "no node has this id, qualified name or label",
        }
    return None, {
        role: label,
        "match": None,
        "reason": f"{len(ids)} nodes share this label; pass one of these ids",
        "ambiguous": [_brief(index.nodes[i]) for i in ids],
    }


def get_node(index: GraphIndex, label: str) -> dict[str, Any]:
    nid, err = _one(index, label, "query")
    if nid is None:
        return err
    n = index.nodes[nid]
    return {
        "query": label,
        "match": {**_brief(n), "lang": n.get("lang"), "attrs": n.get("attrs", {})},
        "out_degree": len(index.out.get(nid, [])),
        "in_degree": len(index.inc.get(nid, [])),
    }


def get_neighbors(
    index: GraphIndex, label: str, relation: str | None = None, direction: str = "both"
) -> dict[str, Any]:
    nid, err = _one(index, label, "query")
    if nid is None:
        return err

    def keep(e: Edge) -> bool:
        return relation is None or relation in (e.get("kind"), e.get("context"))

    outgoing = [e for e in index.out.get(nid, []) if keep(e)] if direction != "in" else []
    incoming = [e for e in index.inc.get(nid, []) if keep(e)] if direction != "out" else []
    return {
        "query": label,
        "node": _brief(index.nodes[nid]),
        "relation": relation,
        "outgoing": [
            {"edge": _edge_brief(e), "node": _brief(index.nodes[e["dst"]])}
            for e in sorted(outgoing, key=lambda e: (e["dst"], str(e.get("kind"))))
            if e["dst"] in index.nodes
        ],
        "incoming": [
            {"edge": _edge_brief(e), "node": _brief(index.nodes[e["src"]])}
            for e in sorted(incoming, key=lambda e: (e["src"], str(e.get("kind"))))
            if e["src"] in index.nodes
        ],
    }


def shortest_path(
    index: GraphIndex, source: str, target: str, max_hops: int = 6, undirected: bool = False
) -> dict[str, Any]:
    s, err = _one(index, source, "source")
    if s is None:
        return err
    t, err = _one(index, target, "target")
    if t is None:
        return err
    prev: dict[str, tuple[str, Edge] | None] = {s: None}
    queue: deque[tuple[str, int]] = deque([(s, 0)])
    while queue:
        cur, d = queue.popleft()
        if cur == t:
            break
        if d >= max_hops:
            continue
        steps = [(e["dst"], e) for e in index.out.get(cur, [])]
        if undirected:
            steps += [(e["src"], e) for e in index.inc.get(cur, [])]
        for nxt, e in sorted(steps, key=lambda p: p[0]):
            if nxt not in prev and nxt in index.nodes:
                prev[nxt] = (cur, e)
                queue.append((nxt, d + 1))
    if t not in prev:
        return {
            "source": s,
            "target": t,
            "path": None,
            "reason": f"no {'undirected ' if undirected else ''}path within {max_hops} hops",
        }
    hops: list[Edge] = []
    cur = t
    while True:
        step = prev[cur]
        if step is None:
            break
        back, e = step
        hops.append(_edge_brief(e))
        cur = back
    hops.reverse()
    return {"source": s, "target": t, "hops": len(hops), "path": hops}


def affected(
    index: GraphIndex, label: str, relation: str | None = None, depth: int = 3
) -> dict[str, Any]:
    """What depends on `label`, transitively over incoming edges: the blast
    radius of changing it."""
    nid, err = _one(index, label, "query")
    if nid is None:
        return err
    seen: dict[str, int] = {nid: 0}
    frontier = [nid]
    for hop in range(1, depth + 1):
        nxt: list[str] = []
        for cur in frontier:
            for e in index.inc.get(cur, []):
                if relation is not None and relation not in (e.get("kind"), e.get("context")):
                    continue
                if e["src"] not in seen and e["src"] in index.nodes:
                    seen[e["src"]] = hop
                    nxt.append(e["src"])
        frontier = sorted(nxt)
    hits = [
        {**_brief(index.nodes[i]), "hops": h}
        for i, h in sorted(seen.items(), key=lambda kv: (kv[1], kv[0]))
        if i != nid
    ]
    return {"query": label, "node": _brief(index.nodes[nid]), "depth": depth, "affected": hits}


def god_nodes(index: GraphIndex, top_n: int = 10) -> dict[str, Any]:
    degree = {
        nid: len(index.out.get(nid, [])) + len(index.inc.get(nid, [])) for nid in index.nodes
    }
    ranked = sorted(degree.items(), key=lambda kv: (-kv[1], kv[0]))[:top_n]
    return {
        "top_n": top_n,
        "nodes": [
            {
                **_brief(index.nodes[nid]),
                "degree": d,
                "in_degree": len(index.inc.get(nid, [])),
                "out_degree": len(index.out.get(nid, [])),
            }
            for nid, d in ranked
        ],
    }


def graph_stats(index: GraphIndex) -> dict[str, Any]:
    kinds: dict[str, int] = {}
    for n in index.nodes.values():
        kinds[str(n.get("kind"))] = kinds.get(str(n.get("kind")), 0) + 1
    edge_kinds: dict[str, int] = {}
    contexts: dict[str, int] = {}
    for e in index.edges:
        edge_kinds[str(e.get("kind"))] = edge_kinds.get(str(e.get("kind")), 0) + 1
        contexts[str(e.get("context"))] = contexts.get(str(e.get("context")), 0) + 1
    return {
        "schema": index.data.get("schema"),
        "built_at_commit": index.data.get("built_at_commit"),
        "nodes": len(index.nodes),
        "edges": len(index.edges),
        "node_kinds": dict(sorted(kinds.items())),
        "edge_kinds": dict(sorted(edge_kinds.items())),
        "edge_contexts": dict(sorted(contexts.items())),
        "modules": len(index.data.get("modules", [])),
        "hyperedges": len(index.data.get("hyperedges", [])),
    }


_WORD = re.compile(r"[a-z0-9]+")
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
# Words of a question that name nothing in a codebase. A two-letter word
# matched as a substring ("to" in "history") returned half the graph.
_STOP = frozenset({
    "a", "an", "and", "are", "as", "at", "be", "by", "do", "does", "for", "from", "how",
    "in", "is", "it", "its", "of", "on", "or", "that", "the", "this", "to", "what", "where",
    "which", "who", "whom", "why", "with", "talks", "talk", "uses", "use", "call", "calls",
})  # fmt: skip


def _tokens(text: str, keep_whole: bool = False) -> list[str]:
    """Identifier-aware words: `getThreadHistory` and `get_thread_history`
    both yield get, thread, history. With `keep_whole`, the unsplit words
    stay too, so `postgres` still finds `PostgreSQL`."""
    spaced = _CAMEL.sub(" ", text).replace("_", " ")
    words = _WORD.findall(spaced.lower())
    if keep_whole:
        words += _WORD.findall(text.replace("_", " ").lower())
    return [w for w in words if len(w) > 1 and w not in _STOP]


def _matches(word: str, tokens: set[str]) -> bool:
    """A word matches a token exactly, or as a substring when it is long
    enough to mean something (`mongo` in `mongodb`)."""
    if word in tokens:
        return True
    return len(word) >= 4 and any(word in tok for tok in tokens)


def query_graph(
    index: GraphIndex, question: str, depth: int = 1, token_budget: int = 2000
) -> dict[str, Any]:
    """Keyword search over labels, qualified names and rationale text, then
    the neighbourhood of the hits to `depth`, cut to `token_budget`.

    Not semantic: a word of the question has to appear in a node. That is
    stated in the answer, because an agent that thinks this understands its
    question will trust a miss.
    """
    words = _tokens(question)
    scored: list[tuple[int, str]] = []
    for nid, n in index.nodes.items():
        hay = " ".join(str(n.get(k, "")) for k in ("label", "qualified_name", "id"))
        hay += " " + str(n.get("attrs", {}).get("text", ""))
        tokens = set(_tokens(hay, keep_whole=True))
        score = sum(1 for w in words if _matches(w, tokens))
        if score:
            scored.append((score, nid))
    scored.sort(key=lambda p: (-p[0], p[1]))
    hits = [nid for _, nid in scored]
    included: dict[str, int] = dict.fromkeys(hits, 0)
    frontier = list(hits)
    for hop in range(1, depth + 1):
        nxt: list[str] = []
        for cur in frontier:
            for e in index.out.get(cur, []) + index.inc.get(cur, []):
                other = e["dst"] if e["src"] == cur else e["src"]
                if other not in included and other in index.nodes:
                    included[other] = hop
                    nxt.append(other)
        frontier = sorted(nxt)
    ordered = list(hits) + sorted(
        (nid for nid, h in included.items() if h > 0), key=lambda nid: (included[nid], nid)
    )
    edges = [
        _edge_brief(e) for e in index.edges if e["src"] in included and e["dst"] in included
    ]
    result: dict[str, Any] = {
        "question": question,
        "matching": "keyword (a word of the question appears in the node), not semantic",
        "words": words,
        "hits": len(hits),
        "nodes": [],
        "edges": [],
        "truncated": None,
    }
    # Budget in approximate tokens (4 characters each), nodes first.
    used = len(json.dumps(result)) // 4
    shown_nodes: list[Node] = []
    for nid in ordered:
        item = {**_brief(index.nodes[nid]), "hops": included[nid]}
        cost = len(json.dumps(item)) // 4
        if used + cost > token_budget:
            break
        shown_nodes.append(item)
        used += cost
    kept = {n["id"] for n in shown_nodes}
    shown_edges: list[Edge] = []
    for e in edges:
        if e["src"] not in kept or e["dst"] not in kept:
            continue
        cost = len(json.dumps(e)) // 4
        if used + cost > token_budget:
            break
        shown_edges.append(e)
        used += cost
    result["nodes"] = shown_nodes
    result["edges"] = shown_edges
    if len(shown_nodes) < len(ordered) or len(shown_edges) < len(edges):
        result["truncated"] = (
            f"TRUNCATED to {token_budget} tokens: showing {len(shown_nodes)} of "
            f"{len(ordered)} nodes and {len(shown_edges)} of {len(edges)} edges"
        )
    return result
