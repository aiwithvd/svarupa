"""Pack self-checks: a broken pack fails here, never on a user's machine."""

from __future__ import annotations

from importlib.metadata import version
from pathlib import Path

import pytest
from tree_sitter import Language

from svarupa.detect import LANG_BY_EXT
from svarupa.extract.packs import (
    ANALYZED_ELSEWHERE,
    BY_DETECTED,
    PACKS,
    load_extractors,
)
from svarupa.extract.packs.model import Decorated, Define, Pack
from svarupa.extract.packs.walker import language_for

ROOT = Path(__file__).resolve().parents[1]


def _languages(pack: Pack) -> list[Language]:
    paths = ["x"] + [f"x{suffix}" for suffix, _ in pack.grammar.by_suffix]
    return [language_for(pack.grammar, p) for p in paths]


@pytest.mark.parametrize("pack", PACKS, ids=lambda p: p.lang)
def test_grammar_pin_matches_the_installed_wheel(pack: Pack) -> None:
    assert version(pack.grammar.distribution) == pack.grammar.version


@pytest.mark.parametrize("pack", PACKS, ids=lambda p: p.lang)
def test_grammar_is_pinned_exactly_in_pyproject(pack: Pack) -> None:
    pin = f'"{pack.grammar.distribution}=={pack.grammar.version}"'
    assert pin in (ROOT / "pyproject.toml").read_text(encoding="utf8")


@pytest.mark.parametrize("pack", PACKS, ids=lambda p: p.lang)
def test_every_rule_names_a_real_node_type(pack: Pack) -> None:
    for lang in _languages(pack):
        unknown = [t for t in pack.rules if lang.id_for_node_kind(t, True) is None]
        assert not unknown, f"{pack.lang}: not node types in this grammar: {unknown}"


@pytest.mark.parametrize("pack", PACKS, ids=lambda p: p.lang)
def test_every_field_a_rule_reads_exists(pack: Pack) -> None:
    fields: set[str] = set()
    for rule in pack.rules.values():
        if isinstance(rule, Define):
            fields.add(rule.name_field)
            if rule.body_field is not None:
                fields.add(rule.body_field)
        elif isinstance(rule, Decorated):
            fields.add(rule.definition_field)
    for lang in _languages(pack):
        missing = sorted(f for f in fields if lang.field_id_for_name(f) is None)
        assert not missing, f"{pack.lang}: unknown fields {missing}"


def test_every_detected_language_has_a_pack_or_a_reason() -> None:
    detected = set(LANG_BY_EXT.values())
    unanalyzed = detected - set(BY_DETECTED) - ANALYZED_ELSEWHERE
    # These are reported as SVA-X-012. The list shrinks as packs land.
    assert unanalyzed == {"go", "rust", "java"}


def test_all_shipped_packs_load() -> None:
    ready, missing = load_extractors()
    assert missing == {}
    assert set(ready) == set(BY_DETECTED)
