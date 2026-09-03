"""The diagnostic code registry is the machine contract, so it is tested.

Design section 11 promises an agent can consume codes and act on
`suggested_fixes` rather than parsing prose. That promise is only as good as
the registry, and a registry checked in one direction rots in the other.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from svarupa.diagnostics import CODES, DYNAMIC_MESSAGE_CODES, Diagnostic, Severity

PACKAGE = Path(__file__).resolve().parents[1] / "svarupa"
_CODE = re.compile(r"^SVA-[A-Z]-\d+$")


def _docstrings(tree: ast.Module) -> set[int]:
    """`id()` of every node that is a docstring, so prose is not an emission."""
    out: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            doc = node.body[0] if node.body else None
            if (
                isinstance(doc, ast.Expr)
                and isinstance(doc.value, ast.Constant)
                and isinstance(doc.value.value, str)
            ):
                out.add(id(doc.value))
    return out


def _module_name(path: Path) -> str:
    rel = path.relative_to(PACKAGE.parent).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def live_modules() -> set[str]:
    """Package modules reachable by import from the entry points.

    Scanning every `.py` under the package is not enough. Commit `9d7d422`
    accidentally committed two nested stale copies of the whole layout package
    (`svarupa/layout/layout/...`), created by a `cp -r src dst` where `dst`
    already existed. A file-count scan certified those dead copies as live
    emitters, so the dead-code direction of the registry check, the direction
    that commit existed to add, could already be satisfied entirely by files
    nothing imports.

    Reachability is computed from the import graph rather than from the
    filesystem, because the filesystem is what was wrong.
    """
    seen: set[str] = set()
    queue = ["svarupa", "svarupa.cli"]
    while queue:
        name = queue.pop()
        if name in seen:
            continue
        path = PACKAGE.parent / Path(*name.split("."))
        file = path / "__init__.py" if path.is_dir() else path.with_suffix(".py")
        if not file.exists():
            continue
        seen.add(name)
        tree = ast.parse(file.read_text(encoding="utf8"), filename=str(file))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                if node.module.split(".")[0] == "svarupa":
                    queue.append(node.module)
                    queue.extend(f"{node.module}.{a.name}" for a in node.names)
            elif isinstance(node, ast.Import):
                queue.extend(a.name for a in node.names if a.name.split(".")[0] == "svarupa")
    return seen


def emitted_codes() -> dict[str, list[str]]:
    """Every code literal in a **live** package module, mapped to its emitters.

    Parsed rather than grepped, for the reason a promoted decision already
    gives about manifests. The first version matched `code="SVA-..."` textually
    and missed every geometry code, because `validate.py` passes the code
    positionally through a helper. The meta-test then reported eight codes as
    dead while they were being emitted on every run.

    Comments are skipped for free by parsing; docstrings are skipped
    explicitly, so a module explaining a code does not count as producing it.
    """
    live = live_modules()
    found: dict[str, list[str]] = {}
    for path in sorted(PACKAGE.rglob("*.py")):
        if path.name == "diagnostics.py":
            continue  # the registry itself is the other side of the comparison
        if _module_name(path) not in live:
            continue
        tree = ast.parse(path.read_text(encoding="utf8"), filename=str(path))
        skip = _docstrings(tree)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and id(node) not in skip
                and _CODE.match(node.value)
            ):
                found.setdefault(node.value, []).append(str(path.relative_to(PACKAGE)))
    return found


def test_every_package_module_is_reachable_by_import() -> None:
    """No orphan modules in the package.

    Directly the check that would have caught `svarupa/layout/layout/`. An
    unreachable module is dead code that still satisfies any scan counting
    files, and the file list of a commit is part of the change, not packaging
    noise.
    """
    # Stages built ahead of the CLI that wires them. Listed by name rather
    # than pattern-matched, so wiring one up and forgetting to remove it here
    # fails, and so does adding a new orphan. `svarupa.lock` is P1-7.
    NOT_WIRED_YET = {"svarupa.lock"}

    live = live_modules()
    orphans = {
        _module_name(p)
        for p in PACKAGE.rglob("*.py")
        if _module_name(p) not in live and "__pycache__" not in p.parts
    }
    assert not orphans - NOT_WIRED_YET, (
        f"in the package but imported by nothing: {sorted(orphans - NOT_WIRED_YET)}"
    )
    assert orphans >= NOT_WIRED_YET, (
        f"{sorted(NOT_WIRED_YET - orphans)} is wired up now; remove it from NOT_WIRED_YET"
    )


def test_the_scan_finds_emissions_at_all() -> None:
    """Guards the two tests below.

    Both compare against `emitted_codes()`. If the regex stopped matching, both
    would pass vacuously while agreeing on nothing, which is the failure mode
    a promoted decision names: a test whose expectation is derived from
    machinery that can silently return empty.
    """
    found = emitted_codes()
    assert len(found) > 20, f"only found {len(found)} emissions; the scan is broken"
    assert len(live_modules()) > 10, "the reachability walk found almost nothing"
    assert {"detect.py", "cluster.py"} <= {f for files in found.values() for f in files}
    # Positional emissions specifically. The textual version of this scan
    # matched only `code="..."` and silently missed every code passed through
    # a helper, which is the whole geometry series.
    assert "layout/validate.py" in found.get("SVA-G-001", [])


def test_every_emitted_code_is_registered() -> None:
    unregistered = {c: f for c, f in emitted_codes().items() if c not in CODES}
    assert not unregistered, (
        f"emitted but not in the registry, so an agent cannot look them up: {unregistered}"
    )


def test_every_registered_code_is_emitted() -> None:
    """The direction that was previously missing.

    Three lock codes were declared and never emitted, with constant names that
    disagreed with their own values. A code in the registry that nothing
    produces is an attribute in the output that was never extracted.
    """
    found = emitted_codes()
    dead = sorted(c for c in CODES if c not in found)
    assert not dead, f"registered but never emitted: {dead}"


def test_code_numbers_are_contiguous_within_a_prefix() -> None:
    """A gap is indistinguishable from a code removed without telling anyone.

    This is what caught the lock series, which ran 001, 002, 004, 007.
    """
    by_prefix: dict[str, list[int]] = {}
    for code in CODES:
        prefix, number = code.rsplit("-", 1)
        by_prefix.setdefault(prefix, []).append(int(number))
    for prefix, numbers in sorted(by_prefix.items()):
        assert sorted(numbers) == list(range(1, len(numbers) + 1)), (
            f"{prefix} is not contiguous: {sorted(numbers)}"
        )


def test_registry_descriptions_are_usable() -> None:
    for code, text in sorted(CODES.items()):
        assert text and text[0].islower(), f"{code}: {text!r} should read as a clause"
        assert not text.endswith("."), f"{code}: {text!r} should not end in a period"
        assert len(text) < 80, f"{code}: {text!r} is too long for a one-line lookup"


@pytest.mark.parametrize("severity", list(Severity))
def test_render_and_json_round_trip_every_severity(severity: Severity) -> None:
    d = Diagnostic(
        code="SVA-D-001",
        severity=severity,
        message="a message",
        subject="a/subject",
        location="a/file.py:3",
        suggested_fixes=("do the thing",),
    )
    rendered = d.render()
    assert severity.value in rendered
    assert "a/file.py:3" in rendered
    assert "fix: do the thing" in rendered
    assert '"code": "SVA-D-001"' in d.to_json()


# --------------------------------------------------------------------------
# The check that catches a description which lies
# --------------------------------------------------------------------------

_STOPWORDS = frozenset(
    [
        "a",
        "an",
        "the",
        "and",
        "or",
        "of",
        "to",
        "in",
        "for",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "it",
        "its",
        "this",
        "that",
        "not",
        "no",
        "any",
        "all",
        "one",
        "two",
        "with",
        "from",
        "into",
        "on",
        "at",
        "by",
        "as",
        "so",
        "than",
        "then",
        "their",
        "there",
        "here",
        "what",
        "which",
        "who",
        "whom",
        "when",
        "where",
        "how",
    ]
)


def _content_words(text: str) -> set[str]:
    return {
        w for w in re.findall(r"[a-z]+", text.lower()) if w not in _STOPWORDS and len(w) > 2
    }


def _literal_text(node: ast.expr) -> list[str]:
    """The static text of a `message=` value, with dynamic slots dropped.

    Handles the shapes actually used: a plain string, an implicitly concatenated
    f-string, a conditional, and `+`. A shape this does not handle yields no
    text, which surfaces as a failure rather than as a pass, so an unhandled
    shape gets noticed instead of quietly exempting a code.
    """
    if isinstance(node, ast.Constant):
        return [node.value] if isinstance(node.value, str) else []
    if isinstance(node, ast.JoinedStr):
        return [t for v in node.values for t in _literal_text(v)]
    if isinstance(node, ast.IfExp):
        return _literal_text(node.body) + _literal_text(node.orelse)
    if isinstance(node, ast.BinOp):
        return _literal_text(node.left) + _literal_text(node.right)
    return []


def emitted_messages() -> dict[str, list[str]]:
    """Literal message text per code, gathered from live modules.

    Codes are matched both as `code=` keywords and as the first positional
    argument, because `layout/validate.py` passes them positionally through a
    helper. That shape is what defeated the first version of the emission scan.
    """
    live = live_modules()
    out: dict[str, list[str]] = {}
    for path in sorted(PACKAGE.rglob("*.py")):
        if path.name == "diagnostics.py" or _module_name(path) not in live:
            continue
        tree = ast.parse(path.read_text(encoding="utf8"), filename=str(path))
        for call in (n for n in ast.walk(tree) if isinstance(n, ast.Call)):
            kw = {k.arg: k.value for k in call.keywords if k.arg}
            code = None
            node = kw.get("code")
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                code = node.value
            elif (
                call.args
                and isinstance(call.args[0], ast.Constant)
                and isinstance(call.args[0].value, str)
                and _CODE.match(call.args[0].value)
            ):
                code = call.args[0].value
            if not code or not _CODE.match(code):
                continue
            message = kw.get("message")
            if message is None and len(call.args) >= 3:
                message = call.args[2]
            if message is not None:
                out.setdefault(code, []).extend(_literal_text(message))
    return out


def test_the_message_scan_finds_text_at_all() -> None:
    """Guards the drift check below.

    If the scan returned nothing, the drift check would pass vacuously for
    every code, which is the shape of a test that cannot fail.
    """
    found = emitted_messages()
    assert len(found) > 20, f"only found messages for {len(found)} codes"
    assert any("clustering backend" in t for t in found.get("SVA-C-001", []))


def test_no_registry_description_contradicts_what_its_code_emits() -> None:
    """The check that was missing, and that a whole wave of wrong descriptions
    got past.

    Existence and contiguity were checked; agreement was not. Measured when
    this was first written: **eleven** descriptions shared no content word with
    the message they label. The entire `SVA-B-*` series was off by several
    positions, so `SVA-B-001` was documented as "an element reached the graph
    without evidence" while emitting "two different nodes claim the same id",
    and that text described `SVA-B-007`.

    One shared content word is a floor, not a proof. It cannot tell a good
    description from a mediocre one, and it is not meant to: it catches a
    description that is about something else entirely, which is the failure
    that actually happened, twice.
    """
    messages = emitted_messages()
    wrong: list[str] = []
    for code, description in sorted(CODES.items()):
        if code in DYNAMIC_MESSAGE_CODES:
            continue
        emitted = " ".join(messages.get(code, []))
        shared = _content_words(description) & _content_words(emitted)
        if not shared:
            wrong.append(
                f"{code}\n    described as: {description!r}\n    emits:        {emitted[:120]!r}"
            )
    assert not wrong, "descriptions that do not describe what they label:\n" + "\n".join(wrong)


def test_every_dynamic_message_code_really_has_no_literal() -> None:
    """The allow-list is asserted in both directions.

    A code listed as dynamic that has gained a literal message is no longer
    exempt, and leaving it on the list would carve a permanent hole in the
    check.
    """
    messages = emitted_messages()
    for code in sorted(DYNAMIC_MESSAGE_CODES):
        assert code in CODES, f"{code} is allow-listed but not registered"
        text = " ".join(messages.get(code, [])).strip()
        assert not text, (
            f"{code} is allow-listed as dynamic but now emits the literal {text[:80]!r}; "
            "remove it from DYNAMIC_MESSAGE_CODES so its description is checked"
        )
