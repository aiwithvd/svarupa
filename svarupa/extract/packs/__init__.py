"""Language packs: one walker, one small pack per language.

`model.py` defines what a pack may say, `walker.py` is the only code that
turns a syntax tree into facts, and each `<lang>.py` is one pack. This
module says which pack serves which language label from `detect`.
"""

from __future__ import annotations

from collections.abc import Mapping

from svarupa.extract.packs import javascript, python, typescript
from svarupa.extract.packs.model import Pack
from svarupa.extract.packs.walker import PackExtractor

__all__ = ["ANALYZED_ELSEWHERE", "BY_DETECTED", "PACKS", "extractor", "load_extractors"]

PACKS: tuple[Pack, ...] = (python.PACK, typescript.PACK, javascript.PACK)

# detect.LANG_BY_EXT label -> pack.
# JavaScript: TypeScript hooks, own label, TSX grammar (see javascript.py).
BY_DETECTED: dict[str, Pack] = {
    "python": python.PACK,
    "typescript": typescript.PACK,
    "javascript": javascript.PACK,
}

# Detected languages another extractor reads, so no pack is missing: SQL
# feeds the ERD from the scan's file languages, not from a syntax walk.
ANALYZED_ELSEWHERE: frozenset[str] = frozenset({"sql"})


def load_extractors(
    by_detected: Mapping[str, Pack] = BY_DETECTED,
) -> tuple[dict[str, PackExtractor], dict[str, str]]:
    """Extractors whose grammar loads, and the reason for each that does not."""
    ready: dict[str, PackExtractor] = {}
    missing: dict[str, str] = {}
    for lang, pack in by_detected.items():
        ex = PackExtractor(pack)
        reason = ex.available()
        if reason is None:
            ready[lang] = ex
        else:
            missing[lang] = reason
    return ready, missing


def extractor(lang: str) -> PackExtractor:
    """The extractor for a detected language label. KeyError if none."""
    return PackExtractor(BY_DETECTED[lang])
