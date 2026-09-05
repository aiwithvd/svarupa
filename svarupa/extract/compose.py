"""docker-compose: the file where the architecture is written down.

Design §4.3: architecture lives in these files more than in the code. A compose
file names the services, says which are databases and queues, and declares who
depends on whom. That is the semantic layer a structural graph cannot supply,
and it is what turns "here are your folders" into "here is your system".

Evidence is real lines, not the file. `yaml.safe_load` discards positions, so
this walks `yaml.compose()`'s node tree, where every mapping key carries its
line. A service box that cites `docker-compose.yml:12` lands the reader on the
service definition, which is the product's promise applied to configuration.

`yaml.safe_load`-equivalent safety: only `compose` with the SafeLoader, never
`load`. A compose file is repository input and gets the same hostile-input
posture as everything else: a malformed file degrades to a diagnostic, and an
alias bomb is refused by depth.
"""

from __future__ import annotations

from dataclasses import dataclass

import yaml

from svarupa.detect import Scan
from svarupa.diagnostics import Diagnostic, Severity
from svarupa.model import Edge, EdgeKind, Evidence, Node, NodeKind, Resolution

__all__ = ["ComposeFacts", "extract_compose"]

# Image name (before any tag) to the kind of thing it is. Declarative, so
# adding one is a data change. Prefix-matched on the image's final path
# segment: `bitnami/postgresql:16` is still postgres.
_DATASTORES = (
    "postgres",
    "mysql",
    "mariadb",
    "mongo",
    "redis",
    "elasticsearch",
    "clickhouse",
    "cassandra",
    "influxdb",
    "minio",
    "sqlite",
)
_QUEUES = ("rabbitmq", "kafka", "nats", "activemq", "mosquitto", "celery")

_MAX_YAML_DEPTH = 60


@dataclass(frozen=True, slots=True)
class ComposeFacts:
    nodes: tuple[Node, ...]
    edges: tuple[Edge, ...]
    diagnostics: tuple[Diagnostic, ...]


def _kind_for(image: str) -> NodeKind:
    name = image.split(":", 1)[0].rsplit("/", 1)[-1].lower()
    if any(name.startswith(p) for p in _DATASTORES):
        return NodeKind.DATASTORE
    if any(name.startswith(p) for p in _QUEUES):
        return NodeKind.QUEUE
    return NodeKind.SERVICE


def _depth(node: yaml.Node, level: int = 0) -> int:
    if level > _MAX_YAML_DEPTH:
        return level
    if isinstance(node, yaml.CollectionNode):
        return max((_depth(v, level + 1) for v in _children(node)), default=level)
    return level


def _children(node: yaml.Node) -> list[yaml.Node]:
    if isinstance(node, yaml.MappingNode):
        return [n for pair in node.value for n in pair]
    if isinstance(node, yaml.SequenceNode):
        return list(node.value)
    return []


def _mapping(node: yaml.Node | None) -> dict[str, yaml.Node]:
    if not isinstance(node, yaml.MappingNode):
        return {}
    out: dict[str, yaml.Node] = {}
    for key, value in node.value:
        if isinstance(key, yaml.ScalarNode) and isinstance(key.value, str):
            out[key.value] = value
    return out


def _scalar(node: yaml.Node | None) -> str | None:
    if isinstance(node, yaml.ScalarNode) and isinstance(node.value, str):
        return node.value
    return None


def _line(node: yaml.Node) -> int:
    return int(node.start_mark.line) + 1


def extract_compose(scan: Scan) -> ComposeFacts:
    """Every compose file in the scan, as evidenced nodes and edges.

    One file's failure costs that file: a compose file is committed, merged by
    humans and hand-edited, so malformed input is the expected case here just
    as it is for the lockfile.
    """
    nodes: dict[str, Node] = {}
    edges: list[Edge] = []
    diags: list[Diagnostic] = []

    for rec in scan.files:
        if rec.config_kind != "compose":
            continue
        path = rec.path
        try:
            text = (scan.root / path).read_text(encoding="utf8")
            # PyYAML ships no stubs, so its return is cast at this one
            # boundary and everything below works on `yaml.Node`.
            from typing import cast

            root = cast(
                "yaml.Node | None",
                yaml.compose(text, Loader=yaml.SafeLoader),  # pyright: ignore[reportUnknownMemberType]
            )
        except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
            diags.append(
                Diagnostic(
                    code="SVA-X-005",
                    severity=Severity.WARNING,
                    message=f"compose file could not be parsed ({type(exc).__name__})",
                    subject=path,
                )
            )
            continue
        if root is None:
            continue
        if _depth(root) > _MAX_YAML_DEPTH:
            diags.append(
                Diagnostic(
                    code="SVA-X-005",
                    severity=Severity.WARNING,
                    message=f"compose file nests deeper than {_MAX_YAML_DEPTH}; skipped",
                    subject=path,
                )
            )
            continue

        services = _mapping(_mapping(root).get("services"))
        for name, body_node in sorted(services.items()):
            body = _mapping(body_node)
            image = _scalar(body.get("image")) or ""
            build = body.get("build")
            build_context = _scalar(build) or _scalar(_mapping(build).get("context"))
            evidence = (
                Evidence(file=path, start_line=_line(body_node), end_line=_line(body_node)),
            )
            kind = _kind_for(image) if image else NodeKind.SERVICE
            attrs: list[tuple[str, str]] = []
            if image:
                attrs.append(("image", image))
            if build_context:
                # A service built from this repository is the bridge between
                # the deployment picture and the code: its context names the
                # subtree that becomes the container.
                attrs.append(("build_context", build_context))
            node_id = f"{path}#service.{name}"
            nodes[node_id] = Node(
                id=node_id,
                kind=kind,
                label=name,
                qualified_name=name,
                evidence=evidence,
                lang="compose",
                attrs=tuple(attrs),
                producer="compose.services",
            )

        # depends_on, in both spellings (list and mapping with conditions).
        for name, body_node in sorted(services.items()):
            body = _mapping(body_node)
            deps = body.get("depends_on")
            targets: list[tuple[str, int]] = []
            if isinstance(deps, yaml.SequenceNode):
                targets = [
                    (v.value, _line(v))
                    for v in deps.value
                    if isinstance(v, yaml.ScalarNode) and isinstance(v.value, str)
                ]
            elif isinstance(deps, yaml.MappingNode):
                targets = [
                    (k.value, _line(k))
                    for k, _ in deps.value
                    if isinstance(k, yaml.ScalarNode) and isinstance(k.value, str)
                ]
            for target, line in targets:
                src = f"{path}#service.{name}"
                dst = f"{path}#service.{target}"
                if dst not in nodes:
                    # depends_on naming a service the file does not define is a
                    # real config bug worth surfacing, not silently dropping.
                    diags.append(
                        Diagnostic(
                            code="SVA-X-006",
                            severity=Severity.WARNING,
                            message=(
                                f"service {name!r} depends on {target!r}, which this "
                                "file does not define"
                            ),
                            subject=path,
                            location=f"{path}:{line}",
                        )
                    )
                    continue
                edges.append(
                    Edge(
                        src=src,
                        dst=dst,
                        kind=EdgeKind.DEPENDS_ON,
                        evidence=(Evidence(file=path, start_line=line, end_line=line),),
                        resolution=Resolution.RESOLVED,
                        producer="compose.depends_on",
                    )
                )
    return ComposeFacts(tuple(nodes.values()), tuple(edges), tuple(diags))
