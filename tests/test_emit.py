"""Stage 6b: the artifact.

The escaping guarantee is a property of the types, and a promoted decision says
an argument is not a check. So these tests attack it with names a real
filesystem accepts, rather than trusting the argument.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from html.parser import HTMLParser
from pathlib import Path

import pytest

from svarupa.build import build
from svarupa.cli import main
from svarupa.cluster import cluster
from svarupa.derive import DiagramKind, derive_all
from svarupa.detect import detect
from svarupa.diagnostics import Diagnostic, DiagnosticError
from svarupa.emit import MARKER, emit
from svarupa.emit.markup import attrs, esc, raw, tag
from svarupa.extract import declared_dependencies, extract
from svarupa.layout.geometry import Style

# A directory name a POSIX filesystem accepts. `/` is the one byte it will not
# take, which is why the payload is an attribute-injection rather than a
# closing tag; the closing-tag case is exercised directly against the escaping
# functions below, where the input can contain anything.
HOSTILE_DIR = "<img src=x onerror=alert(1)>"
HOSTILE_QUOTE = "q\"uo'te"


def build_repo(root: Path, extra: dict[str, str] | None = None) -> None:
    files = {
        "src/__init__.py": "",
        "src/api/__init__.py": "",
        "src/api/routes.py": "from ..core.model import Thing\n",
        "src/core/__init__.py": "",
        "src/core/model.py": "class Thing:\n    pass\n",
    }
    files.update(extra or {})
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf8")


def run(root: Path, out: Path, **kw: object) -> object:
    scan = detect(root)
    graph = build(scan, extract(scan, declared_dependencies(scan)), strict=False)
    produced, notes = derive_all(graph, cluster(graph))
    return emit(root, graph, produced, notes, out_dir=out, **kw)  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# Escaping, as a property of the types and then as behaviour
# --------------------------------------------------------------------------


def test_esc_neutralizes_every_character_that_can_break_out() -> None:
    """`'` as well as `"`: `esc` cannot see which quoting its caller used."""
    assert (
        esc("<a href=\"x\" onload='y'>&")
        == "&lt;a href=&quot;x&quot; onload=&#39;y&#39;&gt;&amp;"
    )


def test_esc_does_not_double_escape_its_own_output() -> None:
    """`&` must be replaced first. Replacing it last would turn `&lt;` into
    `&amp;lt;` and display the entity instead of the character."""
    assert esc("<") == "&lt;"
    assert esc("&lt;") == "&amp;lt;"


def test_attrs_drops_none_instead_of_writing_the_word() -> None:
    """`class="None"` is a silent bug that renders as a real class name."""
    assert str(attrs(a=None, b=False, c=True, d="x")) == ' c d="x"'


def test_attrs_escapes_its_values() -> None:
    assert '"&lt;b&gt;"' in str(attrs(title="<b>"))


def test_tag_composes_without_reopening_the_hole() -> None:
    assert str(tag("p", esc("<b>"), class_="x")) == '<p class="x">&lt;b&gt;</p>'
    assert str(tag("p", raw("<b>ok</b>"))) == "<p><b>ok</b></p>"


# --------------------------------------------------------------------------
# The same attack, through the whole pipeline, from a real filesystem
# --------------------------------------------------------------------------


@pytest.fixture
def hostile(tmp_path: Path) -> Path:
    build_repo(
        tmp_path,
        {
            f"src/{HOSTILE_DIR}/__init__.py": "",
            f"src/{HOSTILE_DIR}/mod.py": "from ..core.model import Thing\n",
            f"src/{HOSTILE_QUOTE}/__init__.py": "",
            f"src/{HOSTILE_QUOTE}/mod.py": "from ..core.model import Thing\n",
        },
    )
    return tmp_path


def test_the_hostile_name_reaches_the_artifact_at_all(hostile: Path, tmp_path: Path) -> None:
    """Guards every assertion below.

    If the name were dropped somewhere upstream, the escaping tests would pass
    while proving nothing, which is the shape of a test that cannot fail.
    """
    run(hostile, tmp_path / "out")
    html = (tmp_path / "out" / "index.html").read_text(encoding="utf8")
    assert "onerror=alert(1)" in html, "the hostile name never reached the document"


def test_a_hostile_directory_name_is_escaped_in_the_html(hostile: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    run(hostile, out)
    html = (out / "index.html").read_text(encoding="utf8")

    assert HOSTILE_DIR not in html, "an unescaped tag reached the document"
    assert "&lt;img src=x onerror=alert(1)&gt;" in html


def test_a_hostile_name_never_breaks_an_attribute(hostile: Path, tmp_path: Path) -> None:
    """The name is interpolated into `data-evidence` and `title`, so a bare
    quote would end the attribute and everything after it becomes markup."""
    out = tmp_path / "out"
    run(hostile, out)
    html = (out / "index.html").read_text(encoding="utf8")
    assert HOSTILE_QUOTE not in html
    assert "q&quot;uo&#39;te" in html


def test_the_emitted_html_has_no_stray_unescaped_angle_bracket_from_data(
    hostile: Path, tmp_path: Path
) -> None:
    """A structural check rather than a search for one payload.

    Every `<` in the document must open a tag whose name is a plain identifier.
    A `<` followed by anything else came from data that escaped.
    """
    out = tmp_path / "out"
    run(hostile, out)
    html = (out / "index.html").read_text(encoding="utf8")
    body = html[html.index("<body>") : html.index("</body>")]
    stray = [
        m.group(0) for m in re.finditer(r"<(?![/!]?[A-Za-z][A-Za-z0-9-]*[\s/>])..{0,20}", body)
    ]
    assert not stray, stray[:5]


def test_a_hostile_name_is_escaped_in_the_json_too(hostile: Path, tmp_path: Path) -> None:
    """The JSON files are parsed by other tools, so the requirement is that
    they round-trip exactly, not that they look escaped."""
    out = tmp_path / "out"
    run(hostile, out)
    data = json.loads((out / "diagrams" / "architecture.json").read_text(encoding="utf8"))
    ids = [b["id"] for view in data["views"].values() for b in view["boxes"]]
    assert any(HOSTILE_DIR in i for i in ids), "the hostile name never reached the JSON"


# --------------------------------------------------------------------------
# No absolute paths, anywhere
# --------------------------------------------------------------------------


def test_no_artifact_file_contains_an_absolute_path(tmp_path: Path) -> None:
    """An absolute path leaks a home directory into a shared artifact and makes
    byte-identity between a laptop and CI impossible."""
    repo = tmp_path / "repo"
    repo.mkdir()
    build_repo(repo)
    out = tmp_path / "out"
    run(repo, out)

    leaked: list[str] = []
    for path in sorted(out.rglob("*")):
        if path.is_file():
            text = path.read_text(encoding="utf8")
            if str(repo) in text or str(tmp_path) in text:
                leaked.append(path.name)
    assert not leaked, f"absolute path leaked into {leaked}"


def test_the_display_root_defaults_to_a_name_not_a_path(tmp_path: Path) -> None:
    repo = tmp_path / "myrepo"
    repo.mkdir()
    build_repo(repo)
    out = tmp_path / "out"
    run(repo, out)
    report = (out / "REPORT.md").read_text(encoding="utf8")
    assert "`myrepo`" in report
    assert str(repo) not in report


# --------------------------------------------------------------------------
# Works without JavaScript
# --------------------------------------------------------------------------


def test_the_diagram_is_in_the_html_not_built_from_json(tmp_path: Path) -> None:
    """Layout already computed every coordinate, so the browser has nothing to
    calculate and a reader with JS off still sees a diagram."""
    repo = tmp_path / "repo"
    repo.mkdir()
    build_repo(repo)
    out = tmp_path / "out"
    artifact = run(repo, out)
    html = (out / "index.html").read_text(encoding="utf8")

    assert "<svg" in html
    assert html.count("<rect") >= sum(
        len(c.boxes)
        for lo in artifact.laid_out.values()  # type: ignore[attr-defined]
        for c in lo.canvases.values()
    )


def test_every_box_carries_its_citations_into_the_document(tmp_path: Path) -> None:
    """The product's central promise, checked on the thing a person opens."""
    repo = tmp_path / "repo"
    repo.mkdir()
    build_repo(repo)
    out = tmp_path / "out"
    artifact = run(repo, out)
    html = (out / "index.html").read_text(encoding="utf8")

    checked = 0
    for lo in artifact.laid_out.values():  # type: ignore[attr-defined]
        for canvas in lo.canvases.values():
            for box in canvas.boxes:
                for ev in box.evidence:
                    assert esc(f"{ev.file}:{ev.start_line}") in html, (
                        f"{box.id} cites {ev.file}:{ev.start_line}, "
                        "which is not in the document"
                    )
                    checked += 1
    assert checked > 0, "no citations were checked, so this test proved nothing"


def test_citations_are_visible_without_javascript(tmp_path: Path) -> None:
    """`data-evidence` is invisible to a reader with JS off.

    The SVG `<title>` is the only affordance that works in that case, so the
    citation has to be in the title specifically, not merely somewhere in the
    document. Without this, removing the title from every box left the whole
    suite green, because the attribute still carried the text.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    build_repo(repo)
    out = tmp_path / "out"
    artifact = run(repo, out)
    html = (out / "index.html").read_text(encoding="utf8")

    titles = re.findall(r"<title>(.*?)</title>", html, re.S)
    assert titles, "the document has no titles at all"
    joined = "\n".join(titles)
    checked = 0
    for lo in artifact.laid_out.values():  # type: ignore[attr-defined]
        for canvas in lo.canvases.values():
            for box in canvas.boxes:
                for ev in box.evidence:
                    assert esc(f"{ev.file}:{ev.start_line}") in joined, (
                        f"{box.id} cites {ev.file}:{ev.start_line}, which a reader "
                        "with JavaScript off cannot see"
                    )
                    checked += 1
    assert checked > 0, "no citations were checked, so this test proved nothing"


def test_the_noscript_message_is_present(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    build_repo(repo)
    out = tmp_path / "out"
    run(repo, out)
    html = (out / "index.html").read_text(encoding="utf8")
    assert "<noscript>" in html
    assert "JavaScript is off" in html


# --------------------------------------------------------------------------
# The report is honest about what it does not know
# --------------------------------------------------------------------------


def test_the_report_states_what_the_scorecard_does_not_measure(tmp_path: Path) -> None:
    """A percentage without this sentence reads as an accuracy claim it cannot
    support."""
    repo = tmp_path / "repo"
    repo.mkdir()
    build_repo(repo)
    out = tmp_path / "out"
    run(repo, out)
    report = (out / "REPORT.md").read_text(encoding="utf8")
    assert "pinning, not correctness" in report
    assert "wrong edge counts as resolved" in report


def test_the_report_names_diagrams_that_were_not_drawn(tmp_path: Path) -> None:
    """An unexplained absence reads as a bug; an explained one is information."""
    repo = tmp_path / "repo"
    repo.mkdir()
    build_repo(repo)
    out = tmp_path / "out"
    run(repo, out)
    report = (out / "REPORT.md").read_text(encoding="utf8")
    assert "### Not drawn" in report
    assert "erd" in report, "the ERD is unavailable here and must be named"


def test_the_report_lists_withheld_views_with_a_reason(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    build_repo(repo)
    out = tmp_path / "out"
    scan = detect(repo)
    graph = build(scan, extract(scan, declared_dependencies(scan)), strict=False)
    produced, notes = derive_all(graph, cluster(graph))
    artifact = emit(
        repo,
        graph,
        produced,
        notes,
        out_dir=out,
        style=Style(box_min_width=1, box_max_width=1, box_pad_x=0),
    )
    assert artifact.withheld, "fixture drew everything, so nothing was tested"
    report = (out / "REPORT.md").read_text(encoding="utf8")
    assert "SVA-G-003" in report
    assert not artifact.ok


def test_the_scorecard_json_carries_its_own_caveat(tmp_path: Path) -> None:
    """A consumer reading `pinned_pct` out of a file has no other way to learn
    what it does not mean."""
    repo = tmp_path / "repo"
    repo.mkdir()
    build_repo(repo)
    out = tmp_path / "out"
    run(repo, out)
    data = json.loads((out / "graph.json").read_text(encoding="utf8"))
    assert "not whether the target is correct" in data["scorecard"]["measures"]


# --------------------------------------------------------------------------
# Determinism
# --------------------------------------------------------------------------


def test_two_runs_produce_identical_bytes(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    build_repo(repo, {f"src/{HOSTILE_DIR}/__init__.py": ""})
    first, second = tmp_path / "a", tmp_path / "b"
    run(repo, first)
    run(repo, second)

    names = sorted(p.relative_to(first) for p in first.rglob("*") if p.is_file())
    assert names, "nothing was written, so nothing was compared"
    for name in names:
        assert (first / name).read_bytes() == (second / name).read_bytes(), name


@pytest.mark.determinism
def test_the_artifact_is_identical_across_hash_seeds(tmp_path: Path) -> None:
    """The churn source an in-process repeat cannot see."""
    import os

    repo = tmp_path / "repo"
    repo.mkdir()
    build_repo(repo)
    script = (
        f"import sys; sys.path.insert(0, {str(Path(__file__).resolve().parents[1])!r})\n"
        "import hashlib, pathlib\n"
        "from svarupa.detect import detect\n"
        "from svarupa.extract import extract, declared_dependencies\n"
        "from svarupa.build import build\n"
        "from svarupa.cluster import cluster\n"
        "from svarupa.derive import derive_all\n"
        "from svarupa.emit import emit\n"
        f"root = pathlib.Path({str(repo)!r})\n"
        "s = detect(root)\n"
        "g = build(s, extract(s, declared_dependencies(s)), strict=False)\n"
        "p, n = derive_all(g, cluster(g))\n"
        f"a = emit(root, g, p, n, out_dir=pathlib.Path(sys.argv[1]))\n"
        "for f in sorted(pathlib.Path(sys.argv[1]).rglob('*')):\n"
        "    if f.is_file():\n"
        "        print(f.name, hashlib.sha256(f.read_bytes()).hexdigest())\n"
    )
    outputs: set[str] = set()
    for i, seed in enumerate(("0", "1", "4242")):
        target = tmp_path / f"seed{i}"
        result = subprocess.run(
            [sys.executable, "-c", script, str(target)],
            capture_output=True,
            text=True,
            env={**os.environ, "PYTHONHASHSEED": seed},
            check=True,
        )
        outputs.add(result.stdout)
    assert len(outputs) == 1, f"hash seed changed the artifact ({len(outputs)} variants)"
    assert next(iter(outputs)).strip(), "the subprocess wrote nothing to compare"


# --------------------------------------------------------------------------
# Refusing, rather than emitting a plausible nothing
# --------------------------------------------------------------------------


def test_a_missing_root_refuses_instead_of_emitting_an_empty_artifact(
    tmp_path: Path,
) -> None:
    """Measured before fixing: `svarupa /no/such/place` exited 0 and wrote a
    complete artifact describing an empty repository, which is
    indistinguishable from a real repository containing no code.

    This is not the "partial failure must degrade" case. That is about one
    input of many; here there is no input at all.
    """
    with pytest.raises(DiagnosticError) as exc:
        detect(tmp_path / "nope")
    assert exc.value.diagnostic.code == "SVA-D-007"
    assert "does not exist" in exc.value.diagnostic.message


def test_a_file_given_as_a_root_refuses_too(tmp_path: Path) -> None:
    target = tmp_path / "a.py"
    target.write_text("x = 1\n", encoding="utf8")
    with pytest.raises(DiagnosticError) as exc:
        detect(target)
    assert exc.value.diagnostic.code == "SVA-D-007"
    assert "not a directory" in exc.value.diagnostic.message


def test_the_cli_prints_the_refusal_and_exits_nonzero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A traceback would tell a user about our call stack instead of their
    input."""
    code = main([str(tmp_path / "nope"), "--out", str(tmp_path / "out")])
    assert code == 1
    err = capsys.readouterr().err
    assert "SVA-D-007" in err
    assert "Traceback" not in err


def test_the_cli_writes_an_artifact_and_reports_success(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    build_repo(repo)
    out = tmp_path / "out"
    assert main([str(repo), "--out", str(out)]) == 0
    assert (out / "index.html").is_file()
    assert (out / "REPORT.md").is_file()
    assert (out / "graph.json").is_file()
    assert (out / "diagrams" / f"{DiagramKind.ARCHITECTURE.value}.json").is_file()


# --------------------------------------------------------------------------
# The injection check, done with a real HTML parser rather than substrings
# --------------------------------------------------------------------------


class _Elements(HTMLParser):
    """Collect element names and text, so the check is about the parsed DOM.

    A substring assertion answers "does this string appear", which is not the
    question. The question is whether the browser builds an element out of
    repository text, and only a parser answers that. Verified against a real
    browser once (no `<img>` created, no global set, payload present as text);
    this reproduces that property in CI, where a browser is not available.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tags: list[str] = []
        self.text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        _ = attrs
        self.tags.append(tag)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        _ = attrs
        self.tags.append(tag)

    def handle_data(self, data: str) -> None:
        self.text.append(data)


PAYLOAD = "<img src=x onerror=window.__PWNED=1>"


@pytest.fixture
def attacked(tmp_path: Path) -> Path:
    build_repo(
        tmp_path,
        {
            f"src/{PAYLOAD}/__init__.py": "",
            f"src/{PAYLOAD}/mod.py": "from ...core.model import Thing\n",
        },
    )
    return tmp_path


def test_the_payload_survives_into_the_document_as_data(attacked: Path, tmp_path: Path) -> None:
    """Guards the parser test below, which would otherwise pass on an artifact
    that simply dropped the directory."""
    out = tmp_path / "out"
    run(attacked, out)
    parser = _Elements()
    parser.feed((out / "index.html").read_text(encoding="utf8"))
    assert any("onerror=window.__PWNED=1" in t for t in parser.text), (
        "the payload never reached the document, so nothing was tested"
    )


def test_a_payload_in_a_directory_name_never_becomes_an_element(
    attacked: Path, tmp_path: Path
) -> None:
    out = tmp_path / "out"
    run(attacked, out)
    parser = _Elements()
    parser.feed((out / "index.html").read_text(encoding="utf8"))

    assert "img" not in parser.tags, "repository text was parsed into an element"
    assert parser.tags.count("script") == 1, (
        f"expected exactly the viewer's own script, got {parser.tags.count('script')}"
    )


def test_the_document_parses_as_the_elements_it_was_built_from(
    attacked: Path, tmp_path: Path
) -> None:
    """Every element in the output must be one this stage writes.

    Stronger than checking for a known payload: it fails for any tag name that
    appears, including one from a future attack nobody wrote a case for.
    """
    out = tmp_path / "out"
    run(attacked, out)
    parser = _Elements()
    parser.feed((out / "index.html").read_text(encoding="utf8"))

    expected = {
        "html",
        "head",
        "meta",
        "title",
        "style",
        "body",
        "header",
        "h1",
        "small",
        "nav",
        "a",
        "div",
        "label",
        "input",
        "section",
        "h2",
        "h3",
        "p",
        "ul",
        "li",
        "aside",
        "ol",
        "hr",
        "strong",
        "noscript",
        "script",
        "svg",
        "defs",
        "marker",
        "path",
        "g",
        "rect",
        "text",
        "polyline",
        "button",
        "circle",
        "span",
    }
    unexpected = sorted(set(parser.tags) - expected)
    assert not unexpected, f"elements this stage never writes: {unexpected}"


def test_the_heading_separates_the_tool_name_from_the_repository(
    tmp_path: Path,
) -> None:
    """`<h1>svarupa<small>repo</small></h1>` reads as one word to a screen
    reader and in any text extraction. Found by reading the accessibility tree
    of the rendered page, which reported the heading as "svarupasvarupa"."""
    repo = tmp_path / "myrepo"
    repo.mkdir()
    build_repo(repo)
    out = tmp_path / "out"
    run(repo, out)
    html = (out / "index.html").read_text(encoding="utf8")
    assert "svarupa <small>myrepo</small>" in html


# --------------------------------------------------------------------------
# The URL sink: repository text reaches an href, so the scheme is checked
# --------------------------------------------------------------------------

NODE = shutil.which("node")


def extract_js(html: str) -> str:
    """The viewer's own script, as source."""
    start = html.rindex("<script>") + len("<script>")
    return html[start : html.index("</script>", start)]


def guard_source(html: str) -> str:
    """The scheme guard, taken from the shipped artifact rather than retyped.

    Extracted so it cannot drift from what actually ships. `link` itself is
    closed over inside an IIFE and unreachable from outside, so the guard is
    driven directly.
    """
    js = extract_js(html)
    return js[js.index("var SAFE") : js.index("function render()")]


PROGRAM = """
globalThis.document = { baseURI: 'https://example.test/artifact/index.html' };
const base = { value: '' };
__GUARD__
const cases = process.argv.slice(1).filter((a) => a !== '--');
console.log(JSON.stringify(cases.map((c) => {
  const parts = c.split(':');
  const line = parts.pop();
  const file = parts.join(':');
  return [c, safeHref(file + '#L' + line)];
})));
"""

HOSTILE_REFS = [
    "javascript:alert(1)//x.py:1",
    "javascript:window.__PWNED=1,void 0/mod.py:1",
    "data:text/html,<script>alert(1)</script>:1",
    "vbscript:msgbox(1):1",
    "JaVaScRiPt:alert(1)//x.py:1",
]


@pytest.mark.skipif(NODE is None, reason="node is not available")
def test_the_evidence_link_refuses_a_dangerous_scheme(tmp_path: Path) -> None:
    """A directory named `javascript:...` supplied the URL scheme itself.

    It was not executable, but only because the appended `#L<line>` happened to
    break the payload's syntax. An accident of an unrelated suffix is not a
    control, and HTML escaping is the wrong escaping for a URL context.

    Run under node, because the branch is in JavaScript and asserting that the
    source contains a guard would test only that the text is present.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    build_repo(repo)
    out = tmp_path / "out"
    run(repo, out)
    html = (out / "index.html").read_text(encoding="utf8")

    ordinary = "src/core/model.py:12"
    program = PROGRAM.replace("__GUARD__", guard_source(html))
    proc = subprocess.run(
        [NODE or "node", "-e", program, "--", ordinary, *HOSTILE_REFS],
        capture_output=True,
        text=True,
        check=True,
    )
    results = dict(json.loads(proc.stdout))

    assert results[ordinary] == "https://example.test/artifact/src/core/model.py#L12", (
        "an ordinary citation must still produce a working link"
    )
    for hostile in HOSTILE_REFS:
        assert results[hostile] is None, f"{hostile} produced a link"


@pytest.mark.skipif(NODE is None, reason="node is not available")
def test_the_scheme_guard_is_present_in_the_emitted_file(tmp_path: Path) -> None:
    """Guards the test above, which extracts the guard from the artifact.

    Without this, removing the guard would make that test *error* on a missing
    substring rather than fail on the security regression it is.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    build_repo(repo)
    out = tmp_path / "out"
    run(repo, out)
    js = extract_js((out / "index.html").read_text(encoding="utf8"))
    assert "safeHref" in js, "the URL scheme guard is not in the shipped viewer"
    assert "'javascript:'" not in js, "no dangerous scheme may be allow-listed"


def test_a_dangerous_path_still_appears_as_a_citation(tmp_path: Path) -> None:
    """Withholding the link must not withhold the evidence.

    The citation is the product's promise. What the guard removes is only the
    ability to click it, and a reader still needs to be told the path.
    """
    build_repo(
        tmp_path,
        {
            "src/javascript:alert(1)/__init__.py": "",
            "src/javascript:alert(1)/mod.py": "from ...core.model import Thing\n",
        },
    )
    out = tmp_path / "out"
    run(tmp_path, out)
    html = (out / "index.html").read_text(encoding="utf8")
    assert esc("src/javascript:alert(1)") in html, "the path was dropped rather than shown"


# --------------------------------------------------------------------------
# The artifact is a function of this run, not of run history
# --------------------------------------------------------------------------


def test_a_stale_diagram_from_a_previous_run_is_removed(tmp_path: Path) -> None:
    """Measured before fixing: `diagrams/erd.json` from a run where the ERD was
    drawable stayed byte-for-byte in place after a run where it was not, so an
    agent reading `diagrams/*.json` consumes a diagram this commit never
    produced."""
    repo = tmp_path / "repo"
    repo.mkdir()
    build_repo(repo)
    out = tmp_path / "out"
    run(repo, out)

    stale = out / "diagrams" / "erd.json"
    stale.write_text('{"stale": true}', encoding="utf8")
    run(repo, out)

    assert not stale.exists(), "a diagram this run did not produce survived"
    assert (out / "diagrams" / "architecture.json").is_file()


def test_a_renamed_view_does_not_leave_its_old_file_behind(tmp_path: Path) -> None:
    """View ids are repository-derived, so a renamed module changes them."""
    repo = tmp_path / "repo"
    repo.mkdir()
    build_repo(repo)
    out = tmp_path / "out"
    run(repo, out)
    (out / "diagrams" / "module-deps.json").write_text("{}", encoding="utf8")

    (repo / "src" / "api").rename(repo / "src" / "gateway")
    run(repo, out)
    written = sorted(p.name for p in (out / "diagrams").iterdir())
    for name in written:
        text = (out / "diagrams" / name).read_text(encoding="utf8")
        assert text != "{}", f"{name} is left over from the previous run"


def test_the_marker_names_the_directory_as_ours(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    build_repo(repo)
    out = tmp_path / "out"
    run(repo, out)
    marker = out / MARKER
    assert marker.is_file()
    assert "replaced on every run" in marker.read_text(encoding="utf8")


def test_writing_into_a_directory_with_foreign_files_is_refused(tmp_path: Path) -> None:
    """`--out` takes an arbitrary path from a command line, so clearing
    whatever is there would make a reasonable typo destructive."""
    repo = tmp_path / "repo"
    repo.mkdir()
    build_repo(repo)
    precious = tmp_path / "precious"
    precious.mkdir()
    (precious / "thesis.txt").write_text("years of work", encoding="utf8")

    with pytest.raises(DiagnosticError) as exc:
        run(repo, precious)
    assert exc.value.diagnostic.code == "SVA-E-001"
    assert "thesis.txt" in exc.value.diagnostic.message
    assert (precious / "thesis.txt").read_text(encoding="utf8") == "years of work"


def test_an_empty_directory_is_accepted(tmp_path: Path) -> None:
    """The refusal is about *foreign* contents, not about existing at all."""
    repo = tmp_path / "repo"
    repo.mkdir()
    build_repo(repo)
    out = tmp_path / "out"
    out.mkdir()
    run(repo, out)
    assert (out / "index.html").is_file()


def test_a_marked_directory_is_accepted_even_with_extra_files(tmp_path: Path) -> None:
    """Once marked, the directory is ours; the marker is the consent."""
    repo = tmp_path / "repo"
    repo.mkdir()
    build_repo(repo)
    out = tmp_path / "out"
    out.mkdir()
    (out / MARKER).write_text("svarupa\n", encoding="utf8")
    (out / "notes.txt").write_text("kept", encoding="utf8")
    run(repo, out)
    assert (out / "index.html").is_file()
    assert (out / "notes.txt").is_file(), "clearing is scoped to what this stage owns"


def test_a_file_where_the_output_directory_should_be_is_refused(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    build_repo(repo)
    blocker = tmp_path / "out"
    blocker.write_text("not a directory", encoding="utf8")
    with pytest.raises(DiagnosticError) as exc:
        run(repo, blocker)
    assert exc.value.diagnostic.code == "SVA-E-001"


def test_the_cli_refuses_before_doing_the_analysis(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A refusal after several minutes of scanning has already wasted the
    user's time, and scrolled past a page of output that read as success."""
    repo = tmp_path / "repo"
    repo.mkdir()
    build_repo(repo)
    precious = tmp_path / "precious"
    precious.mkdir()
    (precious / "thesis.txt").write_text("years of work", encoding="utf8")

    assert main([str(repo), "--out", str(precious)]) == 1
    captured = capsys.readouterr()
    assert "SVA-E-001" in captured.err
    assert "graph:" not in captured.out, "the analysis ran before the refusal"


def test_a_refusal_and_a_failed_run_share_one_exit_code(tmp_path: Path) -> None:
    """Exit codes are a machine contract and CI is the consumer.

    A refusal used to exit 2, which argparse reserves for usage errors, so it
    was indistinguishable from a mistyped flag while also differing from the
    tool's own failure code. One non-zero code now means "no usable answer".
    """
    assert main([str(tmp_path / "nope"), "--out", str(tmp_path / "out")]) == 1


# --------------------------------------------------------------------------
# Withheld reasons must not be attributed across prefix-related view ids
# --------------------------------------------------------------------------


def test_a_withheld_reason_is_not_taken_from_a_sibling_view() -> None:
    """Spec ids share a namespace prefix, so `spec_id in d.subject` matched
    `/spec/root/api` while searching for `/spec/root`, printing one view's
    failure as another's reason."""
    from svarupa.diagnostics import Severity
    from svarupa.emit.report import _first_problem
    from svarupa.layout import LaidOutDiagram

    problem = Diagnostic(
        code="SVA-G-001",
        severity=Severity.ERROR,
        message="belongs to the child view",
        subject="/spec/root/api",
    )
    lo = LaidOutDiagram(DiagramKind.ARCHITECTURE, "clustered", {}, {}, (problem,))

    child = _first_problem(lo, "/spec/root/api")
    assert "belongs to the child view" in child

    parent = _first_problem(lo, "/spec/root")
    assert "belongs to the child view" not in parent, (
        "the parent view was given its child's failure as its reason"
    )
    assert "SVA-G-001" in parent, "the fallback should still name what went wrong"


# --------------------------------------------------------------------------
# The stylesheet is code too, and it has been corrupted twice
# --------------------------------------------------------------------------


def stylesheet(html: str) -> str:
    return html[html.index("<style>") + len("<style>") : html.index("</style>")]


def test_every_colour_in_the_stylesheet_is_a_valid_hex_value(tmp_path: Path) -> None:
    """Twice now a CSS declaration has been written with corrupt text in it:
    once `#4a5<arabic>`, once `#3d4counting`.

    A browser drops a malformed declaration silently, so the diagram renders
    with a default colour and nothing anywhere says why. Cheap to check, and
    neither instance was caught by anything else.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    build_repo(repo)
    out = tmp_path / "out"
    run(repo, out)
    css = stylesheet((out / "index.html").read_text(encoding="utf8"))

    # Only values, not selectors: `#theme { ... }` is an id, not a colour.
    colours = re.findall(r":[^;{}]*?(#[0-9A-Za-z_-]+)", css)
    assert colours, "no colours found, so this test proved nothing"
    malformed = [c for c in colours if not re.fullmatch(r"#[0-9a-fA-F]{3,8}", c)]
    assert not malformed, f"malformed colour values: {malformed}"


def test_every_css_variable_used_is_defined(tmp_path: Path) -> None:
    """A `var(--typo)` renders as nothing at all, silently."""
    repo = tmp_path / "repo"
    repo.mkdir()
    build_repo(repo)
    out = tmp_path / "out"
    run(repo, out)
    css = stylesheet((out / "index.html").read_text(encoding="utf8"))

    defined = set(re.findall(r"(--[a-z-]+)\s*:", css))
    used = set(re.findall(r"var\((--[a-z-]+)", css))
    assert used, "no variables used, so this test proved nothing"
    assert used <= defined, f"undefined CSS variables: {sorted(used - defined)}"


def test_the_stylesheet_is_ascii(tmp_path: Path) -> None:
    """Both corruptions arrived as non-ASCII bytes inside a declaration."""
    repo = tmp_path / "repo"
    repo.mkdir()
    build_repo(repo)
    out = tmp_path / "out"
    run(repo, out)
    css = stylesheet((out / "index.html").read_text(encoding="utf8"))
    odd = sorted({f"U+{ord(c):04X}" for c in css if ord(c) > 127})
    assert not odd, f"non-ASCII in the stylesheet: {odd}"


def test_edge_labels_are_short_verbs_on_masks_never_counts(tmp_path: Path) -> None:
    """Every edge used to stamp its count at its polyline midpoint; on a real
    diagram they collapsed into `7122.26.62.5nimports`, and edge text was
    removed. It returns under two conditions Archify's diagrams meet: the
    text is a short semantic verb, never a number, and it sits on an opaque
    mask at a position the layout checked for collisions (labels that would
    collide are dropped, not smeared). The count stays in the tooltip.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    build_repo(repo)
    # An external store, so at least one arrow carries a semantic verb; the
    # structural import arrows say "imports" where the label fits, and their
    # count lives in the tooltip note, never on the mask.
    (repo / "src" / "store.py").write_text("import redis\n", encoding="utf8")
    out = tmp_path / "out"
    run(repo, out)
    html = (out / "index.html").read_text(encoding="utf8")
    import re

    labels = re.findall(r'class="sv-edge-label[^"]*"[^>]*>([^<]*)<', html)
    assert labels, "no edge labels drawn at all; the verb channel is broken"
    assert "imports" in labels, "structural import arrows say their verb where it fits"
    assert any(lb != "imports" for lb in labels), "the external arrow carries its own verb"
    for text in labels:
        assert not re.search(r"\d", text), f"an edge label carries a number: {text!r}"
        assert len(text) <= 16, f"an edge label is prose, not a verb: {text!r}"
    # Each label is preceded by its mask rect in the same route group.
    assert html.count('class="sv-edge-label') <= html.count('class="sv-mask"')


def test_waypoints_are_not_drawn(tmp_path: Path) -> None:
    """A waypoint is a bend in a line, not a claim about the codebase."""
    s = spec_with_long_edge()
    from svarupa.emit.svg import canvas_svg
    from svarupa.layout import lay_out
    from svarupa.layout.geometry import Style as S

    canvas = lay_out(s, S(), "layered")
    assert canvas.waypoints, "the fixture produced no long edge"
    svg = str(canvas_svg(canvas, S()))
    for wid in canvas.waypoints:
        assert wid not in svg, "a waypoint was drawn as a box"
    _ = tmp_path


def spec_with_long_edge():
    from svarupa.derive.base import DiagramEdge, DiagramKind, DiagramNode, DiagramSpec
    from svarupa.model import Evidence

    ev = (Evidence(file="a.py", start_line=1, end_line=1),)
    nodes = tuple(
        DiagramNode(id=n, label=n, kind="module", evidence=ev, attrs=(("layer", str(i)),))
        for i, n in enumerate(("a", "mid", "z"))
    )
    return DiagramSpec(
        kind=DiagramKind.MODULE_DEPS,
        id="/spec/root",
        title="t",
        nodes=nodes,
        edges=(DiagramEdge(src="a", dst="z", label="1", evidence=ev),),
    )


def test_exported_boxes_never_include_waypoints(tmp_path: Path) -> None:
    """A waypoint is a bend in a line: no evidence, never drawn.

    Exported as a box, it hands a JSON consumer a "box" that violates the
    every-box-cites-a-line contract. Found on svarupa itself: a component view
    reported 48 boxes of which 24 were bends.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    build_repo(
        repo,
        {
            "src/worker/__init__.py": "",
            # Two hops (worker -> api -> core) plus the shortcut (worker ->
            # core), so the shortcut spans two layers and needs a waypoint.
            "src/worker/job.py": (
                "from ..api.routes import Thing as T\nfrom ..core.model import Thing\n"
            ),
        },
    )
    out = tmp_path / "out"
    artifact = run(repo, out)

    total_waypoints = sum(
        len(c.waypoints)
        for lo in artifact.laid_out.values()  # type: ignore[attr-defined]
        for c in lo.canvases.values()
    )
    assert total_waypoints > 0, "the fixture produced no waypoints, so nothing was tested"

    for name in sorted((out / "diagrams").iterdir()):
        data = json.loads(name.read_text(encoding="utf8"))
        for view in data["views"].values():
            for box in view["boxes"]:
                assert box["evidence"], f"{box['id']!r} exported without evidence"
                assert box["kind"] != "waypoint"


# --------------------------------------------------------------------------
# Expand in place: pre-rendered, validated, and honest about its fallback
# --------------------------------------------------------------------------


def test_every_embeddable_drillable_box_has_an_expanded_variant(tmp_path: Path) -> None:
    """The interaction is expand-in-place, so each drillable box needs its
    pre-rendered expansion, and it must not leak into the JSON, which exports
    facts rather than presentation states."""
    repo = tmp_path / "repo"
    repo.mkdir()
    build_repo(repo)
    out = tmp_path / "out"
    artifact = run(repo, out)
    html = (out / "index.html").read_text(encoding="utf8")

    drillable = [
        (sid, n.id, n.child_spec)
        for lo in artifact.laid_out.values()  # type: ignore[attr-defined]
        for sid, c in lo.canvases.items()
        for n in c.boxes
        if n.child_spec is not None
    ]
    assert drillable, "the fixture has no drill-down, so nothing was tested"
    # The expansion is named for the HOST view and the box, so a child
    # shared by two views has one expansion per view (review #17 F1).
    for sid, box_id, child in drillable:
        assert f'data-view="{esc(sid)}//{esc(box_id)}//expanded"' in html, (
            f"no in-place expansion for {box_id} in {sid} ({child})"
        )

    for name in sorted((out / "diagrams").iterdir()):
        assert "//expanded" not in name.read_text(encoding="utf8")


def test_expansion_is_composition_not_new_geometry(tmp_path: Path) -> None:
    """The child keeps its own validated coordinates and is drawn translated.

    If composition changed the child's geometry, the validation it passed
    would no longer be about what is drawn.
    """
    from svarupa.layout import ENGINE_FOR_KIND, lay_out_set
    from svarupa.layout.compose import expand
    from svarupa.layout.geometry import Style

    repo = tmp_path / "repo"
    repo.mkdir()
    build_repo(repo)
    scan = detect(repo)
    graph = build(scan, extract(scan, declared_dependencies(scan)), strict=False)
    produced, _ = derive_all(graph, cluster(graph))
    ds = produced[DiagramKind.ARCHITECTURE]
    lo = lay_out_set(ds)

    sid, node = next(
        (sid, n)
        for sid, c in lo.canvases.items()
        for n in ds.specs[sid].nodes
        if n.child_spec in lo.canvases
    )
    child = lo.canvases[node.child_spec]
    exp = expand(ds.specs[sid], node.id, child, Style(), ENGINE_FOR_KIND[ds.kind])
    assert exp is not None, "the fixture's child was too large to embed"

    assert exp.child is child, "composition rebuilt the child instead of embedding it"
    host = exp.canvas.box(node.id)
    assert host is not None
    assert host.w >= child.width, "the container does not fit its child"
    assert host.h >= child.height, "the container does not fit its child"
    ox, oy = exp.offset
    assert host.x < ox and host.y < oy, "the child is not drawn inside the container"
    assert ox + child.width <= host.right and oy + child.height <= host.bottom


def test_an_oversized_child_gets_no_variant_rather_than_a_scaled_one(
    tmp_path: Path,
) -> None:
    """Scaling a child to fit would shrink labels below legibility, which is
    the shrink-to-fit failure again inside a box. The fallback is the child's
    own view, a behaviour a reader can see rather than a silent scale-down."""
    from svarupa.layout import ENGINE_FOR_KIND, lay_out_set
    from svarupa.layout.compose import EMBED_MAX_W, expand
    from svarupa.layout.geometry import Style

    repo = tmp_path / "repo"
    repo.mkdir()
    build_repo(repo)
    scan = detect(repo)
    graph = build(scan, extract(scan, declared_dependencies(scan)), strict=False)
    produced, _ = derive_all(graph, cluster(graph))
    ds = produced[DiagramKind.ARCHITECTURE]
    lo = lay_out_set(ds)

    sid, node = next(
        (sid, n)
        for sid, c in lo.canvases.items()
        for n in ds.specs[sid].nodes
        if n.child_spec in lo.canvases
    )
    import dataclasses

    child = lo.canvases[node.child_spec]
    huge = dataclasses.replace(child, width=EMBED_MAX_W + 1)
    assert expand(ds.specs[sid], node.id, huge, Style(), ENGINE_FOR_KIND[ds.kind]) is None


def test_component_boxes_carry_no_file_paths_as_labels(tmp_path: Path) -> None:
    """The user's direction: components read as parts, not as filenames. The
    path stays one click away in the citation."""
    repo = tmp_path / "repo"
    repo.mkdir()
    build_repo(
        repo,
        {
            "src/core/extra.py": "from .model import Thing\n\n\ndef helper():\n    return Thing\n"
        },
    )
    scan = detect(repo)
    graph = build(scan, extract(scan, declared_dependencies(scan)), strict=False)
    produced, _ = derive_all(graph, cluster(graph))
    ds = produced[DiagramKind.ARCHITECTURE]

    flows = [spec for sid, spec in ds.specs.items() if sid.endswith("//flow")]
    assert flows, "no component-flow level was derived, so nothing was tested"
    for spec in flows:
        for n in spec.nodes:
            assert "/" not in n.label, f"{n.label!r} is a path, not a component name"
            assert not n.label.endswith(".py"), f"{n.label!r} is a filename"
            assert n.evidence, "a component without a citation"


def test_the_drill_chain_reaches_code_level(tmp_path: Path) -> None:
    """architecture -> module -> component flow -> classes and functions."""
    repo = tmp_path / "repo"
    repo.mkdir()
    build_repo(
        repo,
        {
            "src/core/extra.py": (
                "from .model import Thing\n\n\n"
                "class Extra(Thing):\n    pass\n\n\n"
                "def helper():\n    return Extra\n"
            )
        },
    )
    scan = detect(repo)
    graph = build(scan, extract(scan, declared_dependencies(scan)), strict=False)
    produced, _ = derive_all(graph, cluster(graph))
    ds = produced[DiagramKind.ARCHITECTURE]

    code = [spec for sid, spec in ds.specs.items() if sid.endswith("//code")]
    assert code, "no code level was derived"
    kinds = {n.kind for spec in code for n in spec.nodes}
    assert "class" in kinds and "function" in kinds
    assert ds.depth() >= 3, f"the drill chain is only {ds.depth()} deep"


@pytest.mark.determinism
def test_the_whole_artifact_is_byte_identical_at_clustering_scale(
    tmp_path: Path,
) -> None:
    """The gate that was missing, stated with its reason.

    Every prior cross-seed gate passed while the property was false on the
    first real repository tried, because every fixture was below the size
    where Louvain's multi-level aggregation phase engages, and networkx
    iterates sets of node names inside it. A determinism gate is scoped not
    just to shapes but to scale.

    This fixture is generated to force aggregation: six dense clusters of
    eight modules with sparse bridges. The test asserts both halves: that
    clustering actually grouped (otherwise the gate is running below scale and
    proving nothing), and that every artifact byte matches across seeds.
    """
    import os
    import subprocess
    import sys

    repo = tmp_path / "repo"
    for c in range(6):
        for m in range(8):
            mod = repo / f"c{c}" / f"m{m}"
            mod.mkdir(parents=True, exist_ok=True)
            (mod / "__init__.py").write_text("", encoding="utf8")
            body = "".join(f"from ..m{o} import core as core_{o}\n" for o in range(8) if o != m)
            (mod / "core.py").write_text(body + "VALUE = 1\n", encoding="utf8")
        (repo / f"c{c}" / "__init__.py").write_text("", encoding="utf8")
        if c:
            bridge = repo / f"c{c}" / "bridge.py"
            bridge.write_text(f"from ..c{c - 1}.m0 import core\n", encoding="utf8")

    script = (
        f"import sys; sys.path.insert(0, {str(Path(__file__).resolve().parents[1])!r})\n"
        "import hashlib, pathlib\n"
        "from svarupa.detect import detect\n"
        "from svarupa.extract import extract, declared_dependencies\n"
        "from svarupa.build import build\n"
        "from svarupa.cluster import cluster\n"
        "from svarupa.derive import derive_all\n"
        "from svarupa.emit import emit\n"
        f"root = pathlib.Path({str(repo)!r})\n"
        "s = detect(root)\n"
        "g = build(s, extract(s, declared_dependencies(s)), strict=False)\n"
        "c = cluster(g)\n"
        "print('MODULES', len(g.modules), 'COMMUNITIES', len(c.communities))\n"
        "p, n = derive_all(g, c)\n"
        "emit(root, g, p, n, out_dir=pathlib.Path(sys.argv[1]))\n"
        "for f in sorted(pathlib.Path(sys.argv[1]).rglob('*')):\n"
        "    if f.is_file():\n"
        "        print(f.name, hashlib.sha256(f.read_bytes()).hexdigest())\n"
    )
    outputs: set[str] = set()
    grouped = None
    for i, seed in enumerate(("0", "7", "4242")):
        out = tmp_path / f"seed{i}"
        proc = subprocess.run(
            [sys.executable, "-c", script, str(out)],
            capture_output=True,
            text=True,
            env={**os.environ, "PYTHONHASHSEED": seed},
            check=True,
        )
        first, _, rest = proc.stdout.partition("\n")
        outputs.add(rest)
        parts = first.split()
        grouped = (int(parts[1]), int(parts[3]))

    assert grouped is not None
    modules, communities = grouped
    assert modules >= 40, f"only {modules} modules; the gate is running below scale"
    assert communities < modules // 2, (
        f"{communities} communities for {modules} modules: clustering never "
        "aggregated, so this gate proves nothing about the phase that churns"
    )
    assert len(outputs) == 1, f"the artifact differed across hash seeds ({len(outputs)})"
