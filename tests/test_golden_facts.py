"""Golden facts: the exact pass-1 output for a fixed set of source files.

Extraction is moving from hand-written extractors to language packs. The
whole-artifact byte tests would notice a change but not say which fact
moved. These files pin every fact, in order, so a port either matches them
byte for byte or shows the exact difference.

After an intended change, regenerate and review the diff like code:

    SVARUPA_UPDATE_GOLDEN=1 uv run pytest tests/test_golden_facts.py
"""

from __future__ import annotations

import dataclasses
import enum
import json
import os
from collections.abc import Mapping
from pathlib import Path

import pytest

from svarupa.detect import LANG_BY_EXT
from svarupa.extract import _EXTRACTORS
from svarupa.extract.base import FileFacts

GOLDEN = Path(__file__).parent / "golden"
CASES = sorted(GOLDEN.glob("*/*.txt"))


def _jsonable(value: object) -> object:
    # Mappings become pair lists, not objects: `alias_of` is read in
    # insertion order downstream, and a JSON object would hide a reorder.
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: _jsonable(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, Mapping):
        return [[_jsonable(k), _jsonable(v)] for k, v in value.items()]
    if isinstance(value, (tuple, list)):
        return [_jsonable(v) for v in value]
    return value


def render(facts: FileFacts) -> str:
    return json.dumps(_jsonable(facts), indent=1, ensure_ascii=False) + "\n"


def load_case(case: Path) -> tuple[str, bytes]:
    header, sep, body = case.read_bytes().partition(b"\n---\n")
    assert sep, f"{case}: missing the '---' line after the path header"
    virtual = header.decode("utf8").removeprefix("path: ").strip()
    return virtual, body


def test_the_case_set_is_complete() -> None:
    # Guards the parametrized test below against passing vacuously.
    assert len(CASES) == 15, [c.name for c in CASES]


@pytest.mark.parametrize("case", CASES, ids=lambda c: f"{c.parent.name}/{c.stem}")
def test_facts_match_golden(case: Path) -> None:
    virtual, src = load_case(case)
    lang = LANG_BY_EXT[Path(virtual).suffix]
    got = render(_EXTRACTORS[lang].parse(virtual, src))
    golden = case.with_suffix(".json")
    if os.environ.get("SVARUPA_UPDATE_GOLDEN") == "1":
        golden.write_text(got, encoding="utf8")
        return
    assert golden.exists(), f"no golden file; run with SVARUPA_UPDATE_GOLDEN=1: {golden}"
    assert got == golden.read_text(encoding="utf8")
