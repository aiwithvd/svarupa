"""ERD deriver.

Currently always unavailable, and deliberately explicit about why.

Design §4.3 names `*.sql` the source of truth for the data model, and P1
classifies SQL files, but there is no SQL extractor yet, so no tables or
foreign keys exist in the graph to draw. Per the promoted decision that
"excluding a file type starves whatever derives from it", the honest behaviour
is to say the input is missing rather than return a bare None that reads as
"this repository has no database".

The distinction matters to a user: "you have no SQL" and "I cannot read SQL
yet" are different facts, and only one of them is about their codebase.
"""

from __future__ import annotations

from svarupa.build import Graph
from svarupa.cluster import Clustering
from svarupa.derive.base import ROOT, Deriver, DiagramKind, DiagramSet
from svarupa.model import NodeKind


class ErdDeriver(Deriver):
    kind = DiagramKind.ERD
    title = "Entity relationships"

    def derive(
        self,
        graph: Graph,
        clustering: Clustering,  # noqa: ARG002 - no grouping until there are tables to group
    ) -> DiagramSet | None:
        tables = [n for n in graph.nodes.values() if n.kind is NodeKind.TABLE]
        if tables:  # pragma: no cover - reachable once the SQL extractor lands
            raise NotImplementedError(
                "table nodes exist but the ERD deriver has not been written; "
                "this is a wiring bug, not a missing input"
            )

        # From the scan census, not the graph: with no SQL extractor there are
        # no SQL nodes, so the graph cannot distinguish an absent input from an
        # unread one.
        sql_count = dict(graph.file_languages).get("sql", 0)
        if sql_count:
            reason = (
                f"{sql_count} SQL file(s) were found but cannot be read yet: "
                "the SQL extractor is not implemented, so no tables or keys exist "
                "in the graph to draw"
            )
            fixes = ("Tracked as a P1 gap; the diagram is absent, not empty.",)
        else:
            reason = "no SQL schema or ORM models were found in this repository"
            fixes = ()
        return DiagramSet(self.kind, ROOT, {}, (self.unavailable(reason, fixes),))
