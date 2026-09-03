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

import shutil
from dataclasses import dataclass
from pathlib import Path

from svarupa import __version__
from svarupa.build import Graph
from svarupa.derive.base import DiagramKind, DiagramSet
from svarupa.diagnostics import Diagnostic, DiagnosticError, Severity
from svarupa.emit.data import diagram_json, graph_json, write_json
from svarupa.emit.report import render_report
from svarupa.emit.viewer import render_viewer
from svarupa.layout import LaidOutDiagram, lay_out_set
from svarupa.layout.geometry import Style

__all__ = ["MARKER", "OUTPUT_DIR", "Artifact", "claim", "emit"]

OUTPUT_DIR = ".svarupa"

# A marker naming this directory as ours, so clearing it is a decision about
# files we wrote rather than a guess. Without it, `--out ~/Documents` would be
# a destructive command.
MARKER = ".svarupa-artifact"

# Exactly what this stage owns and will therefore replace. Declared rather than
# derived from what a run happens to produce: the whole bug is that a *previous*
# run produced something this one does not, so the set has to include names
# this run will not write.
OWNED_FILES = ("index.html", "REPORT.md", "graph.json", MARKER)
OWNED_DIRS = ("diagrams",)


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
    claim(directory)
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


def claim(directory: Path) -> None:
    """Take ownership of `directory`, then clear what this stage manages.

    Public and idempotent so the CLI can call it **before** the analysis. A
    refusal that arrives after several minutes of scanning has already wasted
    the user's time, and on a large repository the error scrolled past a full
    page of output that looked like success.

    Two things go wrong without this, both measured. Stale files survive: a
    `diagrams/erd.json` from a run where the ERD *was* drawable stayed
    byte-for-byte in place after a run where it was not, so an agent reading
    `diagrams/*.json` consumes a diagram this commit never produced. And view
    ids are repository-derived, so a renamed module leaves its old view behind
    next to the new one.

    The artifact is contracted to be a pure function of the run that wrote it.
    Writing into a directory without owning its whole contents makes it a
    function of run history instead, which is the cross-run nondeterminism the
    byte-identity work exists to remove.

    Clearing is scoped to `OWNED_FILES` and `OWNED_DIRS`, and an existing
    directory with other contents and no marker is **refused**. `--out` takes
    an arbitrary path from a command line, so a blind delete of everything
    inside it would make a reasonable typo destructive.
    """
    if directory.exists() and not directory.is_dir():
        raise DiagnosticError(
            Diagnostic(
                code="SVA-E-001",
                severity=Severity.ERROR,
                message="exists but is not a directory",
                subject=str(directory),
            )
        )
    if directory.is_dir() and not (directory / MARKER).exists():
        owned = set(OWNED_FILES) | set(OWNED_DIRS)
        foreign = sorted(p.name for p in directory.iterdir() if p.name not in owned)
        if foreign:
            raise DiagnosticError(
                Diagnostic(
                    code="SVA-E-001",
                    severity=Severity.ERROR,
                    message=(
                        f"holds {len(foreign)} file(s) this tool does not own "
                        f"({', '.join(foreign[:4])}); refusing to write here, "
                        "because doing so would mean deleting them on the next run"
                    ),
                    subject=str(directory),
                    suggested_fixes=(
                        "Point --out at a new or empty directory.",
                        f"Or, if this really is a svarupa artifact, create {MARKER} in it.",
                    ),
                )
            )

    directory.mkdir(parents=True, exist_ok=True)
    for name in OWNED_DIRS:
        target = directory / name
        if target.is_dir():
            shutil.rmtree(target)
    for name in OWNED_FILES:
        target = directory / name
        if target.is_file():
            target.unlink()
    (directory / MARKER).write_text(
        f"svarupa {__version__}\nThis directory is managed by svarupa; "
        "its contents are replaced on every run.\n",
        encoding="utf8",
        newline="",
    )


def _write_text(path: Path, text: str) -> int:
    """UTF-8 and LF, always.

    `newline=""` stops Python translating LF to CRLF on Windows, which would
    make the artifact differ byte for byte by platform and break the one
    property the whole pipeline is built around.
    """
    path.write_text(text, encoding="utf8", newline="")
    return len(text.encode("utf8"))
