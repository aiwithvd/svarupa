"""`svarupa query <artifact> <function> [args] [options]`.

The same seven functions the MCP server will expose, on the command line.
Output is JSON with `--json`, a compact text rendering otherwise. The exit
code is a signal an agent or script can branch on: 0 when the question was
answered (including "these nodes are affected: none"), 1 when the answer is
"no such node", "ambiguous" or "no path", or when there is no graph to ask.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, cast

from svarupa.diagnostics import DiagnosticError
from svarupa.query import (
    FUNCTIONS,
    GraphIndex,
    affected,
    get_neighbors,
    get_node,
    god_nodes,
    graph_stats,
    query_graph,
    shortest_path,
)

__all__ = ["query_main", "run_query"]

_ARITY = {
    "query_graph": (1, "question"),
    "get_node": (1, "label"),
    "get_neighbors": (1, "label"),
    "shortest_path": (2, "source target"),
    "affected": (1, "label"),
    "god_nodes": (0, ""),
    "graph_stats": (0, ""),
}


def run_query(
    index: GraphIndex,
    function: str,
    args: list[str],
    *,
    depth: int | None = None,
    relation: str | None = None,
    max_hops: int = 6,
    undirected: bool = False,
    top: int = 10,
    budget: int = 2000,
) -> dict[str, Any]:
    """Dispatch one function; the same entry the MCP server uses."""
    if function == "query_graph":
        return query_graph(
            index, args[0], depth=1 if depth is None else depth, token_budget=budget
        )
    if function == "get_node":
        return get_node(index, args[0])
    if function == "get_neighbors":
        return get_neighbors(index, args[0], relation=relation)
    if function == "shortest_path":
        return shortest_path(index, args[0], args[1], max_hops=max_hops, undirected=undirected)
    if function == "affected":
        return affected(index, args[0], relation=relation, depth=3 if depth is None else depth)
    if function == "god_nodes":
        return god_nodes(index, top_n=top)
    return graph_stats(index)


def _unanswered(result: dict[str, Any]) -> bool:
    return ("match" in result and result["match"] is None) or (
        "path" in result and result["path"] is None
    )


def _text(value: Any, indent: int = 0) -> list[str]:
    pad = "  " * indent
    out: list[str] = []
    if isinstance(value, dict):
        d = cast(dict[str, Any], value)
        if "id" in d and "evidence" in d and isinstance(d.get("evidence"), list):
            ev: list[dict[str, Any]] = d["evidence"]
            where = ", ".join(f"{e['file']}:{e['start_line']}" for e in ev[:3])
            extra = {
                k: v
                for k, v in d.items()
                if k not in ("id", "evidence", "label", "qualified_name")
                and v not in (None, {}, [])
            }
            tail = "  " + " ".join(f"{k}={v}" for k, v in extra.items()) if extra else ""
            out.append(f"{pad}{d['id']}  [{where}]{tail}")
            return out
        if "src" in d and "dst" in d and "evidence" in d:
            ev2: list[dict[str, Any]] = d["evidence"]
            where = ", ".join(f"{e['file']}:{e['start_line']}" for e in ev2[:3])
            out.append(
                f"{pad}{d['src']} -> {d['dst']}  ({d.get('context') or d.get('kind')})  [{where}]"
            )
            return out
        for k, v in d.items():
            if isinstance(v, (dict, list)) and v:
                out.append(f"{pad}{k}:")
                out.extend(_text(v, indent + 1))
            else:
                out.append(f"{pad}{k}: {v}")
        return out
    if isinstance(value, list):
        items = cast(list[Any], value)
        for item in items:
            if isinstance(item, (dict, list)):
                out.extend(_text(item, indent))
            else:
                out.append(f"{pad}- {item}")
        return out
    out.append(f"{pad}{value}")
    return out


def query_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="svarupa query",
        description=(
            "Ask the knowledge graph in an artifact. Labels match exactly (id, "
            "qualified name or label); several matches are listed, never guessed."
        ),
    )
    parser.add_argument("artifact", help="the artifact directory or its graph.json")
    parser.add_argument("function", choices=FUNCTIONS)
    parser.add_argument("args", nargs="*", help="question | label | source target")
    parser.add_argument(
        "--json", action="store_true", help="JSON output (the text form otherwise)"
    )
    parser.add_argument(
        "--depth", type=int, default=None, help="hops (query_graph 1, affected 3)"
    )
    parser.add_argument("--relation", default=None, help="edge kind or context filter")
    parser.add_argument("--max-hops", type=int, default=6)
    parser.add_argument("--undirected", action="store_true")
    parser.add_argument("--top", type=int, default=10, help="god_nodes: how many")
    parser.add_argument("--budget", type=int, default=2000, help="query_graph: token budget")
    ns = parser.parse_args(argv)
    need, names = _ARITY[ns.function]
    if len(ns.args) != need:
        parser.error(f"{ns.function} takes {need} argument(s): {names}".rstrip(": "))
    try:
        index = GraphIndex.load(Path(ns.artifact))
    except DiagnosticError as exc:
        print(exc.diagnostic.render(), file=sys.stderr)
        return 1
    result = run_query(
        index,
        ns.function,
        ns.args,
        depth=ns.depth,
        relation=ns.relation,
        max_hops=ns.max_hops,
        undirected=ns.undirected,
        top=ns.top,
        budget=ns.budget,
    )
    if ns.json:
        print(json.dumps(result, indent=2, ensure_ascii=False, sort_keys=False))
    else:
        print("\n".join(_text(result)))
    return 1 if _unanswered(result) else 0
