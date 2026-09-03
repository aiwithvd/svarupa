"""Stage 6b: write the artifact.

Everything upstream produced facts with source locations attached. This turns
them into files, and it is the first stage whose output a person opens rather
than a test reads.

Two rules shape it:

* **Nothing repository-derived is trusted as markup.** A directory named
  `</script><img src=x onerror=alert(1)>` is scannable on POSIX, and the person
  who opens this artifact is the person who ran the tool. Escaping is enforced
  by the type system in `markup.py` rather than by remembering.
* **Nothing absolute is written.** Paths in the output are repository-relative,
  so the artifact is byte-identical between a laptop and CI and never carries
  someone's home directory into a shared file.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from svarupa import __version__
from svarupa.build import Graph
from svarupa.derive.base import DiagramKind, DiagramSet
from svarupa.diagnostics import Diagnostic, Severity
from svarupa.emit.data import diagram_json, graph_json, write_json
from svarupa.emit.report import render_report
from svarupa.emit.viewer import render_viewer
from svarupa.layout import LaidOutDiagram, lay_out_set
from svarupa.layout.geometry import Style

__all__ = ["OUTPUT_DIR", "Artifact", "emit"]

OUTPUT_DIR = ".svarupa"


@dataclass(frozen=True, slots=True)
class Artifact:
    """What was written, so a caller can report it without re-reading disk."""

    directory: Path
    files: tuple[tuple[str, int], ...]
    laid_out: dict[DiagramKind, LaidOutDiagram]
    diagnostics: tuple[Diagnostic, ...]

    @property
    def withheld(self) -> int:
        return sum(len(lo.withheld) for lo in self.laid_out.values())

    @property
    def ok(self) -> bool:
        return not self.withheld and not any(
            d.severity is Severity.ERROR for d in self.diagnostics
        )


def emit(
    root: Path,
    graph: Graph,
    produced: dict[DiagramKind, DiagramSet],
    notes: tuple[str, ...],
    out_dir: Path | None = None,
    style: Style | None = None,
    display_root: str | None = None,
) -> Artifact:
    """Lay every diagram out and write the artifact directory.

    `display_root` defaults to the directory name rather than the full path.
    An absolute path is the caller's to opt into: this function is used by CI,
    where the checkout path is both meaningless to a reader and a small
    information leak in a published artifact.
    """
    style = style or Style()
    directory = out_dir or (root / OUTPUT_DIR)
    diagrams_dir = directory / "diagrams"
    diagrams_dir.mkdir(parents=True, exist_ok=True)

    laid_out: dict[DiagramKind, LaidOutDiagram] = {}
    problems: list[Diagnostic] = []
    for kind, ds in produced.items():
        lo = lay_out_set(ds, style)
        laid_out[kind] = lo
        problems.extend(lo.problems)

    written: list[tuple[str, int]] = []
    written.append(("graph.json", write_json(directory / "graph.json", graph_json(graph))))
    for kind in sorted(produced, key=lambda k: k.value):
        name = f"diagrams/{kind.value}.json"
        size = write_json(
            diagrams_dir / f"{kind.value}.json",
            diagram_json(produced[kind], laid_out[kind].canvases),
        )
        written.append((name, size))

    shown = display_root if display_root is not None else root.name
    html = render_viewer(shown, produced, laid_out, notes, style, __version__)
    written.append(("index.html", _write_text(directory / "index.html", html)))

    report = render_report(shown, graph, produced, laid_out, notes, tuple(problems))
    written.append(("REPORT.md", _write_text(directory / "REPORT.md", report)))

    return Artifact(directory, tuple(written), laid_out, tuple(problems))


def _write_text(path: Path, text: str) -> int:
    """UTF-8 and LF, always.

    `newline=""` stops Python translating LF to CRLF on Windows, which would
    make the artifact differ byte for byte by platform and break the one
    property the whole pipeline is built around.
    """
    path.write_text(text, encoding="utf8", newline="")
    return len(text.encode("utf8"))
