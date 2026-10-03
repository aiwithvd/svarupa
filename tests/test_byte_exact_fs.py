"""Linux file lookup, reproduced on any machine.

`detect` records paths in NFC, because the lockfile must not depend on which
Unicode form a tool happened to write. The bytes on disk keep whatever form
they were created with. macOS (APFS) finds a file by either form, so reopening
`root / nfc_path` works there; Linux (ext4) matches bytes exactly, so the same
call raises FileNotFoundError and the file was dropped without a word.

That is the whole cross-platform byte gate failure (`compare-bytes` in CI): the
Linux lockfile lost `module src/café` and its `dep`. A macOS-only test run
could never see it, so this suite makes every file open byte-exact.
"""

from __future__ import annotations

import errno
import importlib.util
import io
import os
import pathlib
import unicodedata
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from svarupa.build import build
from svarupa.detect import detect, read_bytes, read_text
from svarupa.extract import declared_dependencies, extract
from svarupa.lock import build_lock

pytestmark = pytest.mark.determinism

ROOT = Path(__file__).resolve().parent.parent


def _fixture_module() -> Any:
    spec = importlib.util.spec_from_file_location(
        "render_fixture_lock", ROOT / "scripts" / "render_fixture_lock.py"
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _byte_exact(base: Path) -> Callable[..., Any]:
    """An `io.open` that, under `base`, reads a file only by its exact bytes.

    Writes are let through: a file being created is in no listing yet.
    """
    real_open = io.open

    def fake_open(file: Any, *args: Any, **kwargs: Any) -> Any:
        mode = str(args[0] if args else kwargs.get("mode", "r"))
        reading = not any(c in mode for c in "wax+")
        if reading and isinstance(file, (str, os.PathLike)):
            p = Path(os.fspath(file)).absolute()
            if p.is_relative_to(base) and p != base:
                cur = base
                for part in p.relative_to(base).parts:
                    if part not in {c.name for c in cur.iterdir()}:
                        raise FileNotFoundError(
                            errno.ENOENT, "No such file (byte-exact)", str(p)
                        )
                    cur = cur / part
        return real_open(file, *args, **kwargs)

    return fake_open


def _install(monkeypatch: pytest.MonkeyPatch, base: Path) -> None:
    """Patch every route `Path.read_*` takes to `io.open`.

    Python 3.10's pathlib opens through `_NormalAccessor.open`, bound to
    `io.open` at import, so patching `io.open` alone is a no-op there (the
    guard test below caught exactly that on macOS 3.10).
    """
    fake = _byte_exact(base)
    monkeypatch.setattr(io, "open", fake)
    accessor = getattr(pathlib, "_NormalAccessor", None)
    if accessor is not None:
        monkeypatch.setattr(accessor, "open", staticmethod(fake))


@pytest.fixture
def linux_lookup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    base = tmp_path.resolve()
    _install(monkeypatch, base)
    yield base


def _render(root: Path) -> str:
    scan = detect(root)
    graph = build(scan, extract(scan, declared_dependencies(scan)), strict=False)
    return build_lock(graph, "test").lockfile.render()


def test_the_simulation_rejects_an_nfc_name_for_an_nfd_file(linux_lookup: Path) -> None:
    """Guard the guard: without this, a simulation that never fires would
    let every test below pass for the wrong reason."""
    nfd = unicodedata.normalize("NFD", "café")
    (linux_lookup / nfd).mkdir()
    (linux_lookup / nfd / "x.py").write_bytes(b"x = 1\n")
    with pytest.raises(FileNotFoundError):
        (linux_lookup / unicodedata.normalize("NFC", "café") / "x.py").read_bytes()
    assert (linux_lookup / nfd / "x.py").read_bytes() == b"x = 1\n"


def test_fixture_lockfile_is_identical_under_byte_exact_lookup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The CI `compare-bytes` gate, run on one machine."""
    fixture = _fixture_module()
    root = tmp_path / "fixture"
    root.mkdir()
    fixture.build_tree(root)

    normal = _render(root)
    _install(monkeypatch, tmp_path.resolve())
    exact = _render(root.resolve())

    assert "module\tsrc/café" in normal
    assert exact == normal


def test_read_bytes_finds_an_nfd_file_by_its_nfc_id(linux_lookup: Path) -> None:
    nfd = unicodedata.normalize("NFD", "café")
    (linux_lookup / "src" / nfd).mkdir(parents=True)
    (linux_lookup / "src" / nfd / "order.py").write_bytes(b"x = 1\n")
    nfc_id = "src/" + unicodedata.normalize("NFC", "café") + "/order.py"
    assert read_bytes(linux_lookup, nfc_id) == b"x = 1\n"


def test_read_bytes_still_raises_for_a_missing_file(linux_lookup: Path) -> None:
    with pytest.raises(FileNotFoundError):
        read_bytes(linux_lookup, "nowhere/x.py")


def test_a_file_unreadable_at_extract_time_is_reported_not_dropped(tmp_path: Path) -> None:
    """The silent `except OSError: continue` hid the Linux failure. A file
    that detect saw and extract could not read is a finding."""
    (tmp_path / "a.py").write_text("import b\n", encoding="utf8")
    (tmp_path / "b.py").write_text("x = 1\n", encoding="utf8")
    scan = detect(tmp_path)
    (tmp_path / "b.py").unlink()
    result = extract(scan, declared_dependencies(scan))
    codes = {(d.code, d.subject) for d in result.diagnostics}
    assert ("SVA-X-011", "b.py") in codes


def test_read_text_translates_newlines_like_path_read_text(tmp_path: Path) -> None:
    """Moving a call site from `Path.read_text` to `read_text` must not change
    the lines a CRLF or CR file yields, or its evidence lines would shift."""
    (tmp_path / "m.toml").write_bytes(b"a = 1\r\nb = 2\rc = 3\n")
    assert read_text(tmp_path, "m.toml") == (tmp_path / "m.toml").read_text(encoding="utf8")
