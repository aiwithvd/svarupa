"""Fail-closed enforcement tests.

The product's entire claim is that nothing renders without provenance. These
tests exist to make that claim mechanically checkable rather than aspirational.
"""

from __future__ import annotations

import unicodedata

import pytest

from svarupa.model import (
    Confidence,
    Edge,
    EdgeKind,
    Evidence,
    MissingEvidenceError,
    Node,
    NodeKind,
    Resolution,
    norm_path,
)

EV = Evidence("src/a.py", 10, 12)
EV2 = Evidence("src/b.py", 3, 3)


def node(**kw: object) -> Node:
    base: dict[str, object] = {
        "id": "n1",
        "kind": NodeKind.FUNCTION,
        "label": "f",
        "qualified_name": "src.a.f",
        "evidence": (EV,),
    }
    base.update(kw)
    return Node(**base)  # type: ignore[arg-type]


def edge(**kw: object) -> Edge:
    base: dict[str, object] = {
        "src": "n1",
        "dst": "n2",
        "kind": EdgeKind.CALLS,
        "evidence": (EV,),
    }
    base.update(kw)
    return Edge(**base)  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# Fail-closed
# --------------------------------------------------------------------------


def test_node_without_evidence_is_rejected() -> None:
    with pytest.raises(MissingEvidenceError):
        node(evidence=())


def test_edge_without_evidence_is_rejected() -> None:
    with pytest.raises(MissingEvidenceError):
        edge(evidence=())


def test_error_names_the_producing_extractor() -> None:
    """A diagnostic must name a culprit, not a symptom."""
    with pytest.raises(MissingEvidenceError) as exc:
        node(evidence=(), producer="python.calls")
    assert "python.calls" in str(exc.value)
    assert exc.value.producer == "python.calls"


def test_candidate_edge_needs_both_sites() -> None:
    """A candidate claims 'one of N impls'.

    Without the definition site as well as the call site, a reader cannot check
    the claim and the fail-closed promise is hollow.
    """
    with pytest.raises(MissingEvidenceError) as exc:
        edge(evidence=(EV,), resolution=Resolution.CANDIDATE, arity=3)
    assert "definition-site" in str(exc.value)


def test_candidate_edge_with_both_sites_is_accepted() -> None:
    e = edge(evidence=(EV, EV2), resolution=Resolution.CANDIDATE, arity=3)
    assert e.arity == 3
    assert len(e.evidence) == 2


def test_candidate_arity_below_two_is_incoherent() -> None:
    with pytest.raises(ValueError, match="2 or more"):
        edge(evidence=(EV, EV2), resolution=Resolution.CANDIDATE, arity=1)


def test_there_is_no_inferred_confidence_tier() -> None:
    """An edge nobody can point at does not exist."""
    assert {c.value for c in Confidence} == {"EXTRACTED", "RESOLVED"}
    assert not hasattr(Confidence, "INFERRED")


def test_scorecard_has_three_honest_bins_plus_candidate() -> None:
    """Two bins would launder resolver failures as externals."""
    assert {r.value for r in Resolution} == {"resolved", "candidate", "external", "unresolved"}


# --------------------------------------------------------------------------
# Evidence validity
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("start", "end"),
    [(0, 5), (-1, 3), (10, 9)],
)
def test_invalid_line_ranges_rejected(start: int, end: int) -> None:
    with pytest.raises(ValueError):
        Evidence("src/a.py", start, end)


def test_empty_file_rejected() -> None:
    with pytest.raises(ValueError):
        Evidence("", 1, 1)


def test_evidence_renders_as_clickable_reference() -> None:
    assert str(Evidence("src/a.py", 4, 4)) == "src/a.py:4"
    assert str(Evidence("src/a.py", 4, 9)) == "src/a.py:4-9"


# --------------------------------------------------------------------------
# Determinism hygiene
# --------------------------------------------------------------------------


def test_paths_are_nfc_normalized_at_construction() -> None:
    """See norm_path: APFS preserves NFD, so byte form varies by machine."""
    nfd = unicodedata.normalize("NFD", "café/a.py")
    assert not unicodedata.is_normalized("NFC", nfd)
    assert unicodedata.is_normalized("NFC", Evidence(nfd, 1, 1).file)


def test_windows_separators_normalized() -> None:
    assert norm_path("src\\pkg\\a.py") == "src/pkg/a.py"


def test_evidence_is_sorted_regardless_of_input_order() -> None:
    a = node(evidence=(EV2, EV))
    b = node(evidence=(EV, EV2))
    assert a.evidence == b.evidence


def test_attrs_are_sorted_regardless_of_input_order() -> None:
    a = node(attrs={"z": "1", "a": "2"})
    b = node(attrs={"a": "2", "z": "1"})
    assert a.attrs == b.attrs == (("a", "2"), ("z", "1"))


def test_attrs_accept_mapping_or_pairs_and_stay_hashable() -> None:
    """Stored as a tuple, not a dict.

    Nodes live in sets during dedup and graph assembly; a dict field would
    make the frozen dataclass unhashable and fail late in build.
    """
    from_map = node(attrs={"framework": "fastapi"})
    from_pairs = node(attrs=(("framework", "fastapi"),))
    assert from_map == from_pairs
    assert len({from_map, from_pairs}) == 1
    assert from_map.attr("framework") == "fastapi"
    assert from_map.attr("missing", "fallback") == "fallback"
    assert from_map.attrs_dict == {"framework": "fastapi"}


def test_elements_are_hashable_and_frozen() -> None:
    """Immutability is what lets stages share elements without defensive copies."""
    n = node()
    assert hash(n) == hash(node())
    with pytest.raises((AttributeError, TypeError)):
        n.id = "mutated"  # type: ignore[misc]


def test_evidence_sorts_when_some_have_a_git_rev_and_some_do_not() -> None:
    """Regression: mixed rev is the normal case, not an edge case.

    `rev` is a git blob sha "when available", so one element routinely carries
    both pinned and unpinned evidence. A generated `order=True` comparison
    would compare None against str and raise TypeError at Node construction.
    """
    pinned = Evidence("src/a.py", 1, 1, rev="abc123")
    unpinned = Evidence("src/a.py", 1, 1, rev=None)

    assert sorted([pinned, unpinned]) == [unpinned, pinned]
    n = node(evidence=(pinned, unpinned))
    assert len(n.evidence) == 2

    e = edge(evidence=(pinned, unpinned), resolution=Resolution.CANDIDATE, arity=2)
    assert len(e.evidence) == 2


def test_evidence_ordering_is_total_across_all_fields() -> None:
    items = [
        Evidence("src/b.py", 1, 1),
        Evidence("src/a.py", 9, 9),
        Evidence("src/a.py", 1, 5),
        Evidence("src/a.py", 1, 1, rev="zzz"),
        Evidence("src/a.py", 1, 1),
    ]
    ordered = sorted(items)
    shuffled = list(reversed(ordered))
    assert sorted(shuffled) == ordered
    assert [str(x) for x in ordered[:2]] == ["src/a.py:1", "src/a.py:1"]


def test_evidence_comparison_with_foreign_type_is_not_silently_true() -> None:
    with pytest.raises(TypeError):
        _ = Evidence("src/a.py", 1, 1) < "not evidence"  # type: ignore[operator]
