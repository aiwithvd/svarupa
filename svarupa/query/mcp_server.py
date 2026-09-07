"""`svarupa mcp <artifact>`: the query functions as an MCP server (Wave E).

Graphify's tool names, so an agent already trained on them needs no
relearning; svarupa's answers, so a label that matches several nodes returns
the candidates and never a guess, and every hit carries its citations. The
server is a thin skin over `run_query`: the CLI and the MCP tools are one
implementation, tested once.

The MCP SDK is an optional dependency (`svarupa[mcp]`). Without it the
command refuses with a structured diagnostic instead of a traceback.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from svarupa.diagnostics import Diagnostic, DiagnosticError, Severity
from svarupa.query import GraphIndex
from svarupa.query.cli import run_query

__all__ = ["build_server", "mcp_main"]


def _sdk() -> Any:
    try:
        from mcp.server.mcpserver import MCPServer
    except ImportError as exc:
        raise DiagnosticError(
            Diagnostic(
                code="SVA-Q-002",
                severity=Severity.ERROR,
                message="the MCP server needs the optional mcp dependency",
                subject="svarupa mcp",
                suggested_fixes=(
                    "pip install 'svarupa[mcp]'  (the SDK 2.x; 1.x has a different module layout)",
                    "or: uv tool install 'svarupa[mcp]'",
                ),
            )
        ) from exc
    return MCPServer


def build_server(index: GraphIndex, name: str = "svarupa") -> Any:
    """An MCPServer exposing the seven query tools over `index`."""
    server = _sdk()(
        name=name,
        instructions=(
            "Answers come from svarupa's graph.json: every node and edge cites file:line. "
            "Labels match exactly (id, qualified name or label); an ambiguous label returns "
            "candidates under 'ambiguous' and no answer. shortest_path and affected follow "
            "dependency edges only. query_graph is keyword search, not semantic."
        ),
    )

    @server.tool(
        description="Keyword search over names and rationale text, then the neighbourhood of the hits."
    )
    def query_graph(question: str, depth: int = 1, token_budget: int = 2000) -> dict[str, Any]:
        return run_query(
            index, "query_graph", [question], depth=max(0, depth), budget=max(50, token_budget)
        )

    @server.tool(
        description="One node by exact id, qualified name or label, with its citations."
    )
    def get_node(label: str) -> dict[str, Any]:
        return run_query(index, "get_node", [label])

    @server.tool(
        description="Incoming and outgoing edges of a node, optionally filtered by relation (edge kind or context)."
    )
    def get_neighbors(label: str, relation_filter: str | None = None) -> dict[str, Any]:
        return run_query(index, "get_neighbors", [label], relation=relation_filter)

    @server.tool(description="Shortest dependency path between two nodes, each hop cited.")
    def shortest_path(
        source: str, target: str, max_hops: int = 6, undirected: bool = False
    ) -> dict[str, Any]:
        return run_query(
            index,
            "shortest_path",
            [source, target],
            max_hops=max(1, max_hops),
            undirected=undirected,
        )

    @server.tool(
        description="What depends on a node, transitively, with the hop count and the relation each was reached by."
    )
    def affected(label: str, relation: str | None = None, depth: int = 3) -> dict[str, Any]:
        return run_query(index, "affected", [label], relation=relation, depth=max(0, depth))

    @server.tool(description="The most connected nodes.")
    def god_nodes(top_n: int = 10) -> dict[str, Any]:
        return run_query(index, "god_nodes", [], top=max(1, top_n))

    @server.tool(
        description="Counts by node kind, edge kind and context; the commit and dirty flag of the tree."
    )
    def graph_stats() -> dict[str, Any]:
        return run_query(index, "graph_stats", [])

    # Registered through the decorator; named here so a type checker sees
    # them used and a reader sees the whole tool list in one place.
    _ = (query_graph, get_node, get_neighbors, shortest_path, affected, god_nodes, graph_stats)
    return server


def mcp_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="svarupa mcp",
        description="Serve the knowledge graph in an artifact over MCP (stdio).",
    )
    parser.add_argument("artifact", help="the artifact directory or its graph.json")
    parser.add_argument("--transport", choices=("stdio", "streamable-http"), default="stdio")
    ns = parser.parse_args(argv)
    try:
        index = GraphIndex.load(Path(ns.artifact))
        server = build_server(index)
    except DiagnosticError as exc:
        print(exc.diagnostic.render(), file=sys.stderr)
        return 1
    server.run(transport=ns.transport)
    return 0
