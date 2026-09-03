"""The escaping guarantee is a type-level claim, so the type checker is tested.

Review #9 found the claim false: `tag(name, body: object)` and
`join(parts: object)` accepted a bare `str` and spliced it in verbatim with
zero pyright complaints, so safety rested on every call site remembering
`esc`, which is the discipline the types were supposed to replace.

A promoted decision says a docstring arguing a check is unnecessary is a check
that does not exist. The same applies to a signature: the check the prose
describes has to be the check the type expresses. So this runs pyright and
asserts it complains, because nothing else in the suite can tell the difference
between "the types enforce this" and "no call site happens to violate it yet".
"""

from __future__ import annotations

import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PYRIGHT = ROOT / ".venv" / "bin" / "pyright"


def pyright_errors(source: str, tmp_path: Path) -> list[str]:
    """Type-check a snippet against the real package and return the messages."""
    target = tmp_path / "snippet.py"
    target.write_text(textwrap.dedent(source), encoding="utf8")
    proc = subprocess.run(
        [str(PYRIGHT), "--outputjson", "--project", str(ROOT), str(target)],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    payload = json.loads(proc.stdout)
    return [d["message"] for d in payload["generalDiagnostics"] if d["severity"] == "error"]


pytestmark = pytest.mark.skipif(
    not PYRIGHT.exists(), reason="pyright is not installed in this environment"
)


def test_pyright_accepts_correct_usage(tmp_path: Path) -> None:
    """The baseline.

    Without it, every assertion below would pass on a snippet that fails to
    type-check for an unrelated reason, such as a bad import path.
    """
    errors = pyright_errors(
        """
        from svarupa.emit.markup import esc, join, tag

        hostile: str = "<img src=x>"
        a = tag("p", esc(hostile), class_="x")
        b = join([esc(hostile), a])
        print(a, b)
        """,
        tmp_path,
    )
    assert errors == [], errors


def test_a_bare_string_body_is_a_type_error(tmp_path: Path) -> None:
    """`tag("text", node.label)` used to compile clean and inject."""
    errors = pyright_errors(
        """
        from svarupa.emit.markup import tag

        hostile: str = "<img src=x onerror=alert(1)>"
        print(tag("p", hostile))
        """,
        tmp_path,
    )
    assert any("Markup" in e for e in errors), errors


def test_a_bare_string_in_join_is_a_type_error(tmp_path: Path) -> None:
    """The `# type: ignore[union-attr]` that used to sit on `join` was the
    signal that its signature was wrong."""
    errors = pyright_errors(
        """
        from svarupa.emit.markup import join

        hostile: str = "<img src=x onerror=alert(1)>"
        print(join([hostile]))
        """,
        tmp_path,
    )
    assert any("Markup" in e for e in errors), errors


def test_attrs_stays_wide_because_it_escapes_what_it_is_given(tmp_path: Path) -> None:
    """`attrs` is the deliberate exception, and the reason is behavioural.

    It escapes its values rather than trusting them, so passing raw repository
    text is the intended use. Asserted so that narrowing it later, by analogy
    with `tag` and `join`, is a considered change rather than a reflex.
    """
    errors = pyright_errors(
        """
        from svarupa.emit.markup import attrs

        hostile: str = "<img src=x>"
        print(attrs(title=hostile, count=3, flag=True, absent=None))
        """,
        tmp_path,
    )
    assert errors == [], errors


def test_the_emit_package_still_type_checks_under_the_narrow_types(
    tmp_path: Path,
) -> None:
    """Narrowing caught three real call sites passing `""`.

    Pinning this means a future widening of `tag` or `join` cannot be hidden by
    the package continuing to pass.
    """
    _ = tmp_path
    proc = subprocess.run(
        [str(PYRIGHT), "--outputjson", str(ROOT / "svarupa" / "emit")],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    payload = json.loads(proc.stdout)
    assert payload["summary"]["errorCount"] == 0, payload["generalDiagnostics"]


def test_pyright_is_available_where_this_suite_runs() -> None:
    """A promoted decision: a platform-conditional test is unverified until CI
    proves it.

    Every test above is skipped when pyright is missing, so this one records
    loudly whether the guarantee is actually being checked here rather than
    letting a whole file of skips read as passes.
    """
    assert shutil.which("pyright") or PYRIGHT.exists(), (
        "pyright is absent, so the type-level escaping guarantee is unverified "
        "in this environment; CI must install it"
    )
