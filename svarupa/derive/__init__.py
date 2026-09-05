"""Stage 5: turn graph facts into diagram specs."""

from __future__ import annotations

from svarupa.build import Graph
from svarupa.cluster import Clustering
from svarupa.derive.architecture import ArchitectureDeriver, ModuleDepsDeriver
from svarupa.derive.base import (
    MAX_TOP_BOXES,
    ROOT,
    Deriver,
    DiagramEdge,
    DiagramKind,
    DiagramNode,
    DiagramSet,
    DiagramSpec,
    UnnavigableDiagramSet,
)
from svarupa.derive.erd import ErdDeriver
from svarupa.derive.system import SystemDeriver

__all__ = [
    "DERIVERS",
    "MAX_TOP_BOXES",
    "ROOT",
    "ArchitectureDeriver",
    "Deriver",
    "DiagramEdge",
    "DiagramKind",
    "DiagramNode",
    "DiagramSet",
    "DiagramSpec",
    "ErdDeriver",
    "ModuleDepsDeriver",
    "SystemDeriver",
    "UnnavigableDiagramSet",
    "derive_all",
]

DERIVERS: tuple[Deriver, ...] = (
    SystemDeriver(),
    ArchitectureDeriver(),
    ModuleDepsDeriver(),
    ErdDeriver(),
)


def derive_all(
    graph: Graph, clustering: Clustering
) -> tuple[dict[DiagramKind, DiagramSet], tuple[str, ...]]:
    """Run every deriver, keeping the ones that produced something.

    A deriver that raises does not take the run down with it: the diagram is
    reported as failed and the others still render. Partial failure degrades,
    it does not disable.
    """
    produced: dict[DiagramKind, DiagramSet] = {}
    notes: list[str] = []
    for deriver in DERIVERS:
        try:
            result = deriver.derive(graph, clustering)
        except Exception as exc:  # a broken deriver must not lose the others
            notes.append(f"{deriver.kind.value}: failed ({type(exc).__name__}: {exc})")
            continue
        if result is None:
            notes.append(f"{deriver.kind.value}: no diagram")
            continue
        if not result.specs:
            for d in result.diagnostics:
                notes.append(f"{deriver.kind.value}: {d.message}")
            continue

        # The navigation invariant is enforced here, not in the DiagramSet
        # constructor. Derivers build sets incrementally, so a constructor
        # check would force each one to build a mutable shadow type first.
        # Same reasoning as `build` re-validating evidence at insertion rather
        # than trusting `Node.__post_init__`: the model check is a
        # convenience, the pipeline check is the contract.
        #
        # A broken set is dropped, not repaired. Guessing at the missing link
        # would put an invented drill-down in front of a reader.
        broken = result.validate()
        if broken:
            for d in broken:
                notes.append(f"{deriver.kind.value}: unnavigable, dropped ({d.render()})")
            continue
        produced[deriver.kind] = result
    return produced, tuple(notes)
