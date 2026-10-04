"""Language packs: one walker, one small pack per language.

`model.py` defines what a pack may say, `walker.py` is the only code that
turns a syntax tree into facts, and each `<lang>.py` is one pack. This
module says which pack serves which language label from `detect`.
"""

from __future__ import annotations

from collections.abc import Mapping

from svarupa.extract.packs.model import Pack
from svarupa.extract.packs.walker import PackExtractor

__all__ = ["BY_DETECTED", "PACKS", "extractor", "load_extractors"]

PACKS: tuple[Pack, ...] = ()

# detect.LANG_BY_EXT label -> pack.
BY_DETECTED: dict[str, Pack] = {}


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
