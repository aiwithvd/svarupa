"""The system view: services, datastores, queues, and who depends on whom.

This is the diagram people mean when they say "architecture diagram": not
folders and imports but the deployed shape of the system, read left to right
as a story. Archify draws this beautifully from boxes an agent typed; here the
boxes come out of docker-compose with the line that declares each one, which
is the product's promise applied to the level where architecture is actually
written down.

The bridge to the code is a service's `build.context`: a service built from
this repository drills into the structural architecture of the subtree it
builds, so the story view and the evidence view are one navigation.
"""

from __future__ import annotations

from svarupa.build import Graph, build_context_of, module_of, module_roles, modules_shipped
from svarupa.cluster import Clustering
from svarupa.derive.architecture import external_nodes_and_edges
from svarupa.derive.base import (
    MAX_EVIDENCE_PER_BOX,
    ROOT,
    Deriver,
    DiagramEdge,
    DiagramKind,
    DiagramNode,
    DiagramSet,
    DiagramSpec,
)
from svarupa.diagnostics import Diagnostic
from svarupa.extract.vocabulary import GENERIC_FAMILIES, image_label
from svarupa.model import EdgeKind, Evidence, NodeKind

__all__ = ["SystemDeriver"]

_SYSTEM_KINDS = (NodeKind.SERVICE, NodeKind.DATASTORE, NodeKind.QUEUE)


class SystemDeriver(Deriver):
    """One box per deployed thing, one arrow per declared dependency."""

    kind = DiagramKind.DEPLOY_TOPOLOGY
    title = "Deploy topology"

    def derive(self, graph: Graph, clustering: Clustering) -> DiagramSet | None:
        _ = clustering
        members = {nid: node for nid, node in graph.nodes.items() if node.kind in _SYSTEM_KINDS}
        if not members:
            return DiagramSet(
                self.kind,
                ROOT,
                {},
                (
                    self.unavailable(
                        "no deployment configuration was found; a docker-compose "
                        "file is what names the services, datastores and queues",
                        fixes=(
                            "Add a compose file, or run against the repository that has one.",
                        ),
                    ),
                ),
            )

        diags: list[Diagnostic] = []
        # Two compose files can both define a `web`. Ids stay distinct, and the
        # human-facing label gains the file's directory when names collide,
        # because two identical boxes force the reader to click to tell them
        # apart, which is the label failing at its one job.
        by_label: dict[str, int] = {}
        for node in members.values():
            by_label[node.label] = by_label.get(node.label, 0) + 1

        roles = module_roles(graph)
        # Archify's vocabulary for the deployed things: a datastore is a
        # database, a queue is a message bus. A service is typed by the code
        # it BUILDS (frontend if that code has a frontend role, security if
        # auth is all it has, backend otherwise); an image-only service
        # (grafana, opensearch-dashboards) builds no code here and stays a
        # plain `service`: giving it the code sigil claimed code with zero
        # evidence of any.
        archetype = {"datastore": "database", "queue": "messagebus", "service": "service"}
        nodes: list[DiagramNode] = []
        modules_of_service: dict[str, set[str]] = {}
        copy_line_of: dict[str, dict[str, Evidence]] = {}
        canonical: dict[str, str] = {}  # vocabulary label -> compose node id
        for nid, node in sorted(members.items()):
            label = node.label
            if by_label[label] > 1:
                where = nid.split("#", 1)[0].rsplit("/", 1)[0] or "."
                label = f"{label} ({where})"
            kind = archetype.get(node.kind.value, node.kind.value)
            image = node.attr("image") or ""
            sublabel = image
            if node.kind in (NodeKind.DATASTORE, NodeKind.QUEUE) and image:
                store = image_label(image)
                if store:
                    canonical.setdefault(store, nid)
                    sublabel = f"{store} · {image}"
            ctx = build_context_of(graph, nid)
            shipped = modules_shipped(graph, nid)
            if ctx is not None and shipped is not None:
                mods, copy_lines = shipped
                modules_of_service[nid] = mods
                copy_line_of[nid] = copy_lines
                narrowed = bool(ctx) or bool(node.attr("dockerfile"))
                held = {r for m in mods for r in roles.get(m, ())}
                kind = "backend"
                if "frontend" in held:
                    kind = "frontend"
                elif "auth" in held and not ({"api", "worker"} & held):
                    kind = "security"
                what: list[str] = []
                # A root build context means "everything": two services built
                # from `.` would each claim the whole repository's routes,
                # which is a wrong per-service number. Counts are claimed only
                # for a context that names a subtree, or a Dockerfile that
                # names what the image copies.
                if "api" in held and narrowed:
                    n_routes = sum(
                        1
                        for r in graph.routes
                        if r.file in graph.architecture_paths and module_of(r.file) in mods
                    )
                    what.append(f"{n_routes} route{'s' if n_routes != 1 else ''}")
                if "worker" in held and narrowed:
                    what.append("workers")
                # What the image holds, from its Dockerfile when one was read
                # (`ships src/`), else where the build may read from.
                shipped_dirs = sorted(
                    {
                        (entry.rpartition(":")[0] or ".")
                        for entry in (node.attr("ships") or "").split("\n")
                        if (entry and entry.rpartition(":")[0] in mods)
                        or any(m.startswith(entry.rpartition(":")[0] + "/") for m in mods)
                    }
                )
                if node.attr("dockerfile") and shipped_dirs:
                    where = (
                        "ships "
                        + ", ".join(
                            (d + "/" if d != "." else "the repository")
                            for d in shipped_dirs[:3]
                        )
                        + (" …" if len(shipped_dirs) > 3 else "")
                    )
                else:
                    where = "built from " + (ctx + "/" if ctx else "the repository root")
                sublabel = where + (" · " + ", ".join(what) if what else "")
            elif node.attr("build_context"):
                # A build context that escapes the repository builds nothing
                # in the tree; say so rather than claim a subtree.
                sublabel = "built outside this repository"
            nodes.append(
                DiagramNode(
                    id=nid,
                    label=label,
                    kind=kind,
                    sublabel=sublabel,
                    evidence=node.evidence,
                    # No drill link yet: this diagram set holds only its own
                    # root, and a child_spec into another set would rightly
                    # fail navigability. The build context is recorded as a
                    # fact instead, so the panel can say which code a built
                    # service comes from; cross-diagram navigation is P2.
                    attrs=tuple(
                        (k, v)
                        for k, v in (
                            ("image", image),
                            ("build_context", ctx if ctx is not None else ""),
                        )
                        if v
                    ),
                )
            )

        # What each built service talks to outside the codebase, from the
        # imports of the code it builds. An import-derived store that compose
        # also declares is ONE thing: `psycopg` talks to the `postgres`
        # service, so the arrow goes to the compose box and no second
        # PostgreSQL is drawn ("6 databases" for three was this). A generic
        # family label (`sqlalchemy` says SQL, not which) attaches to a compose
        # store of that family when there is exactly one, else stays its own
        # honest box.
        # A module stands for every service whose IMAGE holds it: the
        # Dockerfile's COPY sources when one was read, else the build context
        # (review #20 M2 gave the demo's `api` no database; review #21 N1
        # showed the context alone then drew `api -> Gemini API` from a file
        # only the agent image copies). Each arrow cites the import lines and
        # the COPY line that puts that code in the image.
        stand_in: dict[str, str | tuple[str, ...]] = {}
        holders: dict[str, list[str]] = {}
        for svc, mods in sorted(modules_of_service.items()):
            for m in mods:
                holders.setdefault(m, []).append(svc)
        for m, svcs in holders.items():
            stand_in[m] = tuple(sorted(svcs))
        ext_nodes, ext_edges = external_nodes_and_edges(graph, set(stand_in), stand_in)
        ext_edges = [_with_copy_line(e, copy_line_of) for e in ext_edges]
        redirect: dict[str, str] = {}
        for n in ext_nodes:
            target = canonical.get(n.label)
            if target is None and n.label in GENERIC_FAMILIES:
                family = [canonical[f] for f in GENERIC_FAMILIES[n.label] if f in canonical]
                if len(family) == 1:
                    target = family[0]
            if target is not None:
                redirect[n.id] = target
        nodes.extend(n for n in ext_nodes if n.id not in redirect)
        ext_edges = [
            DiagramEdge(
                src=e.src,
                dst=redirect.get(e.dst, e.dst),
                label=e.label,
                note=e.note,
                evidence=e.evidence,
                weight=e.weight,
                variant=e.variant,
            )
            for e in ext_edges
        ]
        ext_edges = [e for e in ext_edges if e.src != e.dst]
        edges = tuple(
            sorted(
                [
                    DiagramEdge(
                        src=e.src,
                        dst=e.dst,
                        label="depends on",
                        evidence=e.evidence,
                        variant="emphasis",
                    )
                    for e in graph.edges
                    if e.kind is EdgeKind.DEPENDS_ON and e.src in members and e.dst in members
                ]
                + ext_edges
            )
        )
        tally: dict[str, int] = {}
        for n in nodes:
            tally[n.kind] = tally.get(n.kind, 0) + 1
        subtitle = ", ".join(
            f"{count} {kind}{'s' if count != 1 else ''}"
            for kind, count in tally.items()
            if count
        )
        spec = DiagramSpec(
            kind=self.kind,
            id=ROOT,
            title=self.title,
            subtitle=subtitle,
            nodes=tuple(sorted(nodes)),
            edges=edges,
        )
        return DiagramSet(self.kind, ROOT, {ROOT: spec}, tuple(diags))


def _with_copy_line(
    edge: DiagramEdge, copy_line_of: dict[str, dict[str, Evidence]]
) -> DiagramEdge:
    """Add the Dockerfile line that ships the importing module to a store
    arrow's evidence, so the claim "this service talks to MongoDB" cites both
    the import and the COPY that puts the import in the image."""
    lines = copy_line_of.get(edge.src, {})
    extra = sorted(
        {lines[module_of(ev.file)] for ev in edge.evidence if module_of(ev.file) in lines}
    )
    if not extra:
        return edge
    return DiagramEdge(
        src=edge.src,
        dst=edge.dst,
        label=edge.label,
        note=edge.note,
        evidence=(*edge.evidence[: MAX_EVIDENCE_PER_BOX - len(extra)], *extra),
        weight=edge.weight,
        variant=edge.variant,
    )
