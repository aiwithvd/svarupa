"""`REPORT.md`: what was found, and just as importantly what was not.

The report is where the tool is honest in prose. Anyone can print the numbers
that went well. The sections that matter are the ones naming what could not be
resolved, which diagrams were not drawn and why, and what the headline
percentage does not mean.

Markdown, not HTML, because this file is meant to be read in a pull request and
in a terminal as often as in a browser.
"""

from __future__ import annotations

from svarupa.build import Graph
from svarupa.derive.base import DiagramKind, DiagramSet
from svarupa.diagnostics import Diagnostic, Severity
from svarupa.layout import LaidOutDiagram, fits

__all__ = ["MAX_LISTED", "render_report"]

# Long lists are truncated with a count, never silently. A report that shows
# ten of four hundred problems while looking like it shows all of them is worse
# than one that shows none.
MAX_LISTED = 15


def _fence(text: str) -> str:
    return f"```\n{text}\n```"


def _listing(items: list[str], empty: str) -> str:
    if not items:
        return empty
    shown = items[:MAX_LISTED]
    rest = len(items) - len(shown)
    out = "\n".join(f"- {i}" for i in shown)
    if rest:
        out += f"\n- *... and {rest} more*"
    return out


def _grouped(diags: list[Diagnostic]) -> list[str]:
    """Informational findings, grouped by code.

    One code can fire once per view (a capped component list in each of a
    hundred request stories), and a hundred near-identical lines hide the
    one finding a reader needs. Each code shows its first instances and a
    count of the rest. The trailing count is of FINDINGS, never of lines:
    `_listing`'s own "and N more" counted the "more of this code" lines as
    findings (review #17 F12).
    """
    by_code: dict[str, list[Diagnostic]] = {}
    for d in diags:
        by_code.setdefault(d.code, []).append(d)
    lines: list[tuple[str, int]] = []  # (text, findings the line stands for)
    for code in sorted(by_code):
        group = by_code[code]
        for d in group[:3]:
            lines.append((f"`{code}` {d.subject or ''} {d.message}".strip(), 1))
        if len(group) > 3:
            lines.append(
                (f"`{code}` *... and {len(group) - 3} more of this code*", len(group) - 3)
            )
    if len(lines) <= MAX_LISTED:
        return [text for text, _ in lines]
    kept = lines[: MAX_LISTED - 1]
    rest = len(diags) - sum(n for _, n in kept)
    return [text for text, _ in kept] + [
        f"*... and {rest} more finding(s) of these codes; the build prints every one*"
    ]


def _semantics_scope(graph: Graph) -> list[str]:
    """Say which languages semantic extraction covers, when it matters.

    A JavaScript service showing no `api` role must read as "not extracted",
    never as "no API": absence and blindness are different facts, and only
    one of them is about the user's codebase.
    """
    from svarupa.extract.semantics import SEMANTIC_FRAMEWORKS, SEMANTIC_LANGS

    uncovered = sorted(
        lang for lang, n in graph.file_languages if n and lang not in SEMANTIC_LANGS
    )
    lines = [
        f"- routes, tasks and roles come from framework detection "
        f"({', '.join(SEMANTIC_FRAMEWORKS)}) in this build "
        f"({len(graph.routes)} routes, {len(graph.tasks)} tasks, "
        f"{len(graph.entrypoints)} declared entrypoints). A service on another "
        f"framework shows as modules, not as a missing API",
    ]
    if uncovered:
        lines.append(
            f"- **{', '.join(uncovered)} files were scanned but not semantically "
            "analyzed**: a missing api/worker role there means not-yet-extracted, "
            "not absent"
        )
    lines.append("")
    return lines


def render_report(
    root: str,
    graph: Graph,
    produced: dict[DiagramKind, DiagramSet],
    laid_out: dict[DiagramKind, LaidOutDiagram],
    notes: tuple[str, ...],
    diagnostics: tuple[Diagnostic, ...],
) -> str:
    """The whole report.

    Takes `root` as a display string rather than reading the filesystem, so the
    caller decides what is safe to print. An absolute path in a committed or
    shared report leaks a home directory, and the caller is the only layer that
    knows whether this run is going into CI.
    """
    parts: list[str] = [
        "# Svarupa report",
        "",
        f"**Repository:** `{root}`",
        "",
        "Every box and every arrow in this artifact cites a source location. "
        "Anything that could not be pinned to one is listed below rather than "
        "drawn.",
        "",
        "## Graph",
        "",
        f"- **{len(graph.nodes)}** nodes, **{len(graph.edges)}** edges",
        f"- **{len(graph.modules)}** modules, **{len(graph.module_deps)}** module dependencies",
        "",
        *_semantics_scope(graph),
        "## Resolution",
        "",
        "This table measures **pinning, not correctness.** A confidently wrong "
        "edge counts as resolved, so a high percentage means references were "
        "attached to a target, not that the target is right. Only decoy "
        "fixtures and spot audits can tell those apart, and a rising number is "
        "never on its own evidence that resolution improved.",
        "",
        _fence(graph.scorecard.render()),
        "",
        "`pinned` excludes known-external references from its denominator: a "
        "third-party import is not a resolution failure, and counting it as one "
        "would flatter or deflate the number depending on how framework-heavy "
        "the repository is.",
        "",
    ]

    unresolved: list[str] = []
    for (lang, kind), samples in sorted(graph.scorecard.samples.items()):
        for s in sorted(samples):
            unresolved.append(f"`{s}` ({lang} {kind})")
    parts += [
        "### Could not be resolved",
        "",
        _listing(
            unresolved,
            "Nothing was left unresolved in a bin that samples, which is worth "
            "treating with suspicion on a large repository rather than as a "
            "clean bill of health.",
        ),
        "",
        "## Diagrams",
        "",
    ]

    if not produced:
        parts += [
            "None were produced. Each reason is listed under *Not drawn* below. "
            "An empty diagram is never fabricated to fill a tab.",
            "",
        ]
    else:
        parts += [
            "| Diagram | Views | Boxes | Withheld | Depth | Canvas fits |",
            "|---|---:|---:|---:|---:|---|",
        ]
        for kind in sorted(produced, key=lambda k: k.value):
            ds, lo = produced[kind], laid_out[kind]
            boxes = sum(len(c.boxes) for c in lo.canvases.values())
            root_canvas = lo.canvases.get(ds.root)
            # The smallest of the four reference viewports the root view fits
            # without scrolling, or "scrolls": Archify's containment check,
            # as arithmetic on the canvas.
            fit = fits(root_canvas) if root_canvas is not None else ()
            if root_canvas is None:
                fits_in = "withheld"
            elif fit:
                fits_in = f"{fit[0][0]}x{fit[0][1]}"
            else:
                fits_in = "scrolls"
            parts.append(
                f"| {kind.value} | {len(lo.canvases)} | {boxes} | "
                f"{len(lo.withheld)} | {ds.depth()} | {fits_in} |"
            )
        parts.append(
            "*Canvas fits*: the smallest of four reference viewports (1440x900 to "
            "2048x1320) in which the root canvas is fully visible below the page "
            "header without scrolling; the legend and cards below it may still "
            "scroll. `scrolls` means the canvas itself exceeds every one."
        )
        parts.append("")

    withheld: list[str] = [
        f"`{kind.value}` view `{sid}`: {_first_problem(laid_out[kind], sid)}"
        for kind in sorted(laid_out, key=lambda k: k.value)
        for sid in sorted(laid_out[kind].withheld)
    ]
    parts += [
        "### Withheld",
        "",
        _listing(
            withheld,
            "None. Every view that was derived also passed geometry validation.",
        ),
        "",
        "A withheld view failed a geometric check, which is a defect in this "
        "tool rather than in the repository. It is named here instead of being "
        "drawn wrong.",
        "",
        "### Not drawn",
        "",
        _listing(
            [f"{n}" for n in notes],
            "Every diagram kind this build supports produced something.",
        ),
        "",
    ]

    errors = [d for d in diagnostics if d.severity is Severity.ERROR]
    warnings = [d for d in diagnostics if d.severity is Severity.WARNING]
    infos = [d for d in diagnostics if d.severity is Severity.INFO]
    parts += [
        "## Diagnostics",
        "",
        f"**{len(errors)}** error(s), **{len(warnings)}** warning(s), "
        f"**{len(infos)}** informational.",
        "",
        _listing(
            [f"`{d.code}` {d.subject or ''} {d.message}".strip() for d in errors + warnings],
            "No errors or warnings.",
        ),
        "",
        "### Informational",
        "",
        "What was left undrawn on purpose, and why: a finding here is a fact about "
        "the repository or a stated limit of this tool, not a failure.",
        "",
        _listing(_grouped(infos), "Nothing was left undrawn for an informational reason."),
        "",
        "Codes are stable and machine-readable (`SVA-<stage>-<n>`); the build "
        "prints every finding with its `fix:` lines, and `svarupa query` "
        "answers what the report does not.",
        "",
        "## What this artifact does not claim",
        "",
        "- **Community grouping is presentation only.** Boxes are grouped for "
        "readability, and that grouping is deliberately never used as an "
        "identity, because a single added import can reassign a quarter of it.",
        "- **A missing edge is not proof of no relationship.** Dynamic dispatch, "
        "dependency injection and reflection are not statically resolvable, and "
        "an edge that cannot be pinned to a source location is not drawn.",
        "- **Line numbers are current as of this build.** They live here and in "
        "`graph.json`, never in the lockfile, because any edit above a record "
        "would shift them and churn a committed file.",
        "",
    ]
    return "\n".join(parts).rstrip() + "\n"


def _first_problem(lo: LaidOutDiagram, spec_id: str) -> str:
    """The first geometry problem naming this view, or an honest fallback.

    Matched by **equality**, not by substring. Spec ids share a namespace
    prefix, so `spec_id in d.subject` matched `/spec/root/api` while searching
    for `/spec/root` and printed one view's failure as another's reason. `in`
    on identifiers that can be prefixes of one another is a false-match
    generator; the same lesson as running collision detection on structured
    pre-images rather than on concatenated text.

    A view can also be withheld by a problem whose subject is a *box*, so an
    empty result means the message is elsewhere rather than that nothing was
    wrong. Saying so beats printing nothing and letting a reader conclude the
    view was withheld for no reason.
    """
    for d in lo.problems:
        if d.subject == spec_id:
            return f"`{d.code}` {d.message}"
    codes = sorted({d.code for d in lo.problems})
    return f"failed geometry validation ({', '.join(codes) or 'no code recorded'})"
