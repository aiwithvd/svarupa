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

from svarupa.diagnostics import CODES, Diagnostic, Severity

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


def emitted_codes() -> dict[str, list[str]]:
    """Every code literal in the package, mapped to the files emitting it.

    Parsed rather than grepped, for the reason a promoted decision already
    gives about manifests. The first version matched `code="SVA-..."` textually
    and missed every geometry code, because `validate.py` passes the code
    positionally through a helper. The meta-test then reported eight codes as
    dead while they were being emitted on every run.

    Comments are skipped for free by parsing; docstrings are skipped
    explicitly, so a module explaining a code does not count as producing it.
    """
    found: dict[str, list[str]] = {}
    for path in sorted(PACKAGE.rglob("*.py")):
        if path.name == "diagnostics.py":
            continue  # the registry itself is the other side of the comparison
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


def test_the_scan_finds_emissions_at_all() -> None:
    """Guards the two tests below.

    Both compare against `emitted_codes()`. If the regex stopped matching, both
    would pass vacuously while agreeing on nothing, which is the failure mode
    a promoted decision names: a test whose expectation is derived from
    machinery that can silently return empty.
    """
    found = emitted_codes()
    assert len(found) > 20, f"only found {len(found)} emissions; the scan is broken"
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
