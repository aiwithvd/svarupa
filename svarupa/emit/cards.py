"""Cards and guided views: Archify's two surfaces around the canvas, computed.

Archify's author writes three cards under a diagram and a strip of guided
views above it. Ours are derived (design section 2.4): the cards from facts
the graph already holds (routes and entrypoints, stores, external APIs, and
the unresolved bin so the honest number sits next to the pretty picture),
each item citing its lines; the guided views from the root spec of each
diagram, one chapter per api module or service, its focus being the box plus
everything the drawn arrows connect it to. Deterministic, and every chapter
is a subgraph the reader can already click.
"""

from __future__ import annotations

from dataclasses import dataclass

from svarupa.build import Graph, module_of
from svarupa.derive.base import DiagramSpec
from svarupa.emit.markup import Markup, esc, join, raw, tag
from svarupa.emit.svg import EVIDENCE_ATTR, evidence_ref
from svarupa.model import Evidence, NodeKind, Resolution

__all__ = [
    "Card",
    "CardItem",
    "Chapter",
    "cards_for",
    "chapters_for",
    "render_cards",
    "render_guided",
]

MAX_ITEMS = 4
MAX_CHAPTERS = 6


@dataclass(frozen=True, slots=True)
class CardItem:
    text: str
    evidence: tuple[Evidence, ...]


@dataclass(frozen=True, slots=True)
class Card:
    title: str
    tone: str  # cyan | violet | amber | rose
    items: tuple[CardItem, ...]
    more: int  # items beyond MAX_ITEMS, stated rather than dropped
    empty: str  # what to say when there is nothing, so absence is a sentence


@dataclass(frozen=True, slots=True)
class Chapter:
    anchor: str  # node id the story starts from
    title: str
    sublabel: str
    focus: tuple[str, ...]  # node ids lit by the chapter, anchor included


def _card(title: str, tone: str, items: list[CardItem], empty: str) -> Card:
    return Card(title, tone, tuple(items[:MAX_ITEMS]), max(0, len(items) - MAX_ITEMS), empty)


def cards_for(graph: Graph) -> tuple[Card, ...]:
    """Four cards from the graph's facts; only architecture-eligible files count."""
    eligible = graph.architecture_paths

    by_module: dict[str, list[str]] = {}
    route_ev: dict[str, list[Evidence]] = {}
    for r in sorted(graph.routes):
        if r.file not in eligible:
            continue
        m = module_of(r.file)
        by_module.setdefault(m, []).append(f"{r.method} {r.path}")
        route_ev.setdefault(m, []).append(r.evidence)
    entries: list[CardItem] = []
    for m in sorted(by_module, key=lambda m: (-len(by_module[m]), m)):
        paths = sorted(set(by_module[m]))
        shown = ", ".join(paths[:3]) + (" …" if len(paths) > 3 else "")
        label = m or "(repo root)"
        entries.append(
            CardItem(f"{label}: {len(by_module[m])} routes · {shown}", tuple(route_ev[m][:8]))
        )
    for ep in sorted(graph.entrypoints):
        if ep.file in eligible or ep.file.endswith(("pyproject.toml", "package.json")):
            entries.append(CardItem(f"{ep.name} → {ep.target} ({ep.lang})", (ep.evidence,)))

    stores: dict[str, list[Evidence]] = {}
    apis: dict[str, list[Evidence]] = {}
    for x in sorted(graph.externals):
        if x.file not in eligible:
            continue
        if x.category in ("database", "messagebus"):
            stores.setdefault(f"{x.label} via {x.package}", []).append(x.evidence)
        elif x.category == "cloud":
            apis.setdefault(f"{x.label} via {x.package}", []).append(x.evidence)
    for n in sorted(graph.nodes.values(), key=lambda n: n.id):
        if n.kind in (NodeKind.DATASTORE, NodeKind.QUEUE) and n.evidence:
            image = n.attr("image") or ""
            stores.setdefault(f"{n.label} (compose{', ' + image if image else ''})", []).extend(
                n.evidence
            )
    store_items = [CardItem(k, tuple(v[:8])) for k, v in sorted(stores.items())]
    api_items = [CardItem(k, tuple(v[:8])) for k, v in sorted(apis.items())]

    unresolved: list[CardItem] = []
    for (lang, kind, res), n in sorted(graph.scorecard.counts.items()):
        if res is Resolution.UNRESOLVED and n:
            samples = sorted(graph.scorecard.samples.get((lang, kind), []))[:3]
            tail = f" · e.g. {', '.join(samples)}" if samples else ""
            unresolved.append(CardItem(f"{lang} {kind}: {n} unresolved{tail}", ()))

    return (
        _card("Entry points", "cyan", entries, "no routes or declared entrypoints were found"),
        _card(
            "Data stores",
            "violet",
            store_items,
            "no database or message bus is imported or composed",
        ),
        _card("External APIs", "amber", api_items, "no cloud or SaaS SDK is imported"),
        _card(
            "Unresolved",
            "rose",
            unresolved,
            "every reference in a sampled bin was pinned; treat that with suspicion on a large repository",
        ),
    )


def chapters_for(spec: DiagramSpec) -> tuple[Chapter, ...]:
    """One chapter per api module or service in the root view, else the most
    connected boxes; the focus is the box and what the drawn arrows connect
    it to. Nothing here is authored: a chapter is a click the reader could
    make, named for the box it starts from."""
    degree: dict[str, int] = {}
    neighbours: dict[str, set[str]] = {}
    for e in spec.edges:
        degree[e.src] = degree.get(e.src, 0) + 1
        degree[e.dst] = degree.get(e.dst, 0) + 1
        neighbours.setdefault(e.src, set()).add(e.dst)
        neighbours.setdefault(e.dst, set()).add(e.src)
    starred = [
        n
        for n in spec.nodes
        if "api" in (n.attr("roles") or "").split(",") or n.kind in ("service", "endpoint")
    ]
    if not starred:
        starred = [n for n in spec.nodes if degree.get(n.id)]
    ranked = sorted(starred, key=lambda n: (-degree.get(n.id, 0), n.id))[:MAX_CHAPTERS]
    return tuple(
        Chapter(
            anchor=n.id,
            title=n.label,
            sublabel=n.sublabel,
            focus=(n.id, *sorted(neighbours.get(n.id, set()))),
        )
        for n in ranked
    )


def render_cards(cards: tuple[Card, ...]) -> Markup:
    out: list[Markup] = []
    for c in cards:
        items = [
            tag(
                "li",
                join((esc(i.text), _src(i.evidence))),
                class_="card-item" + (" link" if i.evidence else ""),
                **(
                    {EVIDENCE_ATTR: evidence_ref(i.evidence), "data_kind": "fact"}
                    if i.evidence
                    else {}
                ),
            )
            for i in c.items
        ]
        if c.more:
            items.append(tag("li", esc(f"… and {c.more} more"), class_="card-more"))
        body = tag("ul", join(items)) if items else tag("p", esc(c.empty), class_="card-empty")
        out.append(
            tag(
                "div",
                join(
                    (
                        tag(
                            "div",
                            join(
                                (
                                    tag("span", raw(""), class_=f"card-dot {c.tone}"),
                                    esc(c.title),
                                )
                            ),
                            class_="card-header",
                        ),
                        body,
                    )
                ),
                class_="card",
            )
        )
    return tag("div", join(out), class_="cards")


def _src(evidence: tuple[Evidence, ...]) -> Markup:
    if not evidence:
        return raw("")
    return tag("span", esc(f"SRC {len(evidence)}"), class_="card-src")


def render_guided(chapters: tuple[Chapter, ...], root: str) -> Markup:
    if not chapters:
        return raw("")
    items = [
        tag(
            "li",
            join(
                (
                    tag("span", esc(f"{i + 1:02d}"), class_="num"),
                    tag("span", esc(ch.title), class_="title"),
                    tag("span", esc(f"{len(ch.focus)} boxes"), class_="counts"),
                )
            ),
            class_="chapter",
            data_anchor=ch.anchor,
            data_focus="\n".join(ch.focus),
            title=ch.sublabel or None,
        )
        for i, ch in enumerate(chapters)
    ]
    return tag(
        "div",
        join(
            (
                tag(
                    "div",
                    join(
                        (
                            tag(
                                "span",
                                join(
                                    (
                                        esc("Guided views "),
                                        tag(
                                            "span",
                                            esc(f"0 / {len(chapters)}"),
                                            class_="progress",
                                        ),
                                    )
                                ),
                                class_="eyebrow",
                            ),
                            tag("strong", esc("Explore this system")),
                            raw('<button class="play" type="button">▶ Play story</button>'),
                        )
                    ),
                    class_="guided-head",
                ),
                tag("ol", join(items), class_="chapters"),
            )
        ),
        class_="guided",
        data_root=root,
    )
