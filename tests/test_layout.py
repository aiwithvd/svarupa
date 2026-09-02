"""Stage 6a: coordinates, text metrics, and geometry validation.

The validator passed on every real repository the first time it ran. Per a
promoted decision, a gate that has never been seen to fail is not evidence, so
most of what follows constructs the violation and asserts the specific code,
and the engine tests are mutation-checked in the commit that adds them.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from svarupa.build import build
from svarupa.cluster import cluster
from svarupa.derive import DiagramKind, derive_all
from svarupa.derive.base import DiagramEdge, DiagramNode, DiagramSpec
from svarupa.detect import detect
from svarupa.extract import declared_dependencies, extract
from svarupa.layout import ENGINE_FOR_KIND, Style, lay_out, lay_out_set, validate
from svarupa.layout.engines import MAX_ROW_WIDTH, _inferred_layers
from svarupa.layout.geometry import Band, Box, Canvas, Route
from svarupa.layout.text import (
    ADVANCE_RATIO,
    FONT_STACK,
    REPLACEMENT,
    advance,
    cells,
    sanitize,
    truncate,
)
from svarupa.model import Evidence

EV = (Evidence(file="a.py", start_line=1, end_line=2),)
STYLE = Style()


def node(nid: str, label: str | None = None, **attrs: str) -> DiagramNode:
    return DiagramNode(
        id=nid,
        label=label if label is not None else nid,
        kind="module",
        evidence=EV,
        attrs=tuple(sorted(attrs.items())),
    )


def spec(*nodes: DiagramNode, edges: tuple[DiagramEdge, ...] = ()) -> DiagramSpec:
    return DiagramSpec(
        kind=DiagramKind.MODULE_DEPS,
        id="/spec/root",
        title="t",
        nodes=nodes,
        edges=edges,
    )


def edge(src: str, dst: str) -> DiagramEdge:
    return DiagramEdge(src=src, dst=dst, label="1", evidence=EV)


def box(bid: str, x: int, y: int, w: int = 96, h: int = 44, label: str = "x") -> Box:
    return Box(
        id=bid, label=label, full_label=label, kind="module", x=x, y=y, w=w, h=h, evidence=EV
    )


def canvas(
    *boxes: Box,
    routes: tuple[Route, ...] = (),
    bands: tuple[Band, ...] = (),
    size: tuple[int, int] = (800, 600),
) -> Canvas:
    return Canvas(
        spec_id="/spec/root",
        kind=DiagramKind.MODULE_DEPS,
        engine="layered",
        title="t",
        subtitle="",
        width=size[0],
        height=size[1],
        boxes=boxes,
        routes=routes,
        bands=bands,
    )


# --------------------------------------------------------------------------
# Text metrics
# --------------------------------------------------------------------------


def test_the_font_stack_ends_in_the_monospace_generic() -> None:
    """The width model is only valid for a monospace font.

    A stack that could resolve to a proportional font makes every fits-in-box
    verdict meaningless, so the assumption is asserted rather than trusted to
    a comment.
    """
    assert FONT_STACK.strip().endswith("monospace")


def test_the_advance_ratio_exceeds_every_font_in_the_stack() -> None:
    """Measured ratios, widest first. 0.62 must be above all of them so the
    model over-estimates and a passing box has slack."""
    measured = {
        "DejaVu Sans Mono": 0.6023,
        "Menlo": 0.6021,
        "SF Mono": 0.600,
        "Courier New": 0.600,
        "Consolas": 0.550,
    }
    assert max(measured.values()) < ADVANCE_RATIO


def test_a_right_to_left_override_is_replaced() -> None:
    """U+202E in a filename renders the label reversed, so a reader sees a
    different module name from the one the box points at."""
    assert sanitize("safe‮gnp.exe") == "safe" + REPLACEMENT + "gnp.exe"


def test_a_newline_in_a_path_is_replaced() -> None:
    """Legal in a POSIX filename, and it ends the SVG text run."""
    assert sanitize("src/od\nd") == "src/od" + REPLACEMENT + "d"


def test_joiners_needed_by_real_scripts_survive() -> None:
    """The deny-list is per-character, not the whole Cf category.

    U+200D is required by Indic scripts and emoji sequences; banning the
    category would mangle legitimate module names.
    """
    assert sanitize("a‍b") == "a‍b"
    assert sanitize("स्वरूप") == "स्वरूप"


def test_wide_characters_measure_two_cells_and_marks_measure_none() -> None:
    """Without both, a CJK name measures at half its drawn width and overflows
    a box the validator called fine."""
    assert cells("日本") == 4
    assert cells("ab") == 2
    assert cells("é") == 1, "a combining acute does not advance"


def test_advance_rounds_up_never_to_nearest() -> None:
    """Rounding down lets a label that overflows by a fraction pass."""
    exact = 1 * 13 * ADVANCE_RATIO
    assert exact != int(exact), "pick a case where rounding direction matters"
    assert advance("a", 13) == 9 > exact


def test_truncation_keeps_the_tail_because_labels_are_paths() -> None:
    out = truncate("src/services/billing/invoice", 13, 90)
    assert out.startswith("…")
    assert out.endswith("invoice")
    assert advance(out, 13) <= 90


def test_truncation_of_a_wide_string_respects_cell_width() -> None:
    out = truncate("日本語" * 8, 13, 60)
    assert advance(out, 13) <= 60


# --------------------------------------------------------------------------
# Geometry primitives
# --------------------------------------------------------------------------


def test_touching_boxes_do_not_overlap_but_a_gap_demands_clearance() -> None:
    a, b = box("a", 0, 0, w=100), box("b", 100, 0, w=100)
    assert not a.overlaps(b), "shared edges are not an overlap"
    assert a.overlaps(b, gap=1), "a required gap is not satisfied by touching"


def test_a_style_with_impossible_widths_refuses_to_exist() -> None:
    with pytest.raises(ValueError, match="exceeds"):
        Style(box_min_width=300, box_max_width=100)


# --------------------------------------------------------------------------
# Validation: one test per code, each breaking exactly one thing
# --------------------------------------------------------------------------


def codes(c: Canvas) -> list[str]:
    return sorted({d.code for d in validate(c, STYLE)})


def test_a_clean_canvas_reports_nothing() -> None:
    """The baseline. Without it every test below could pass on a validator
    that always complains."""
    assert codes(canvas(box("a", 32, 32), box("b", 160, 32))) == []


def test_overlapping_boxes_are_reported() -> None:
    assert "SVA-G-001" in codes(canvas(box("a", 32, 32), box("b", 40, 32)))


def test_boxes_closer_than_the_minimum_gap_are_reported() -> None:
    """Two boxes one pixel apart read as one merged box."""
    assert "SVA-G-001" in codes(canvas(box("a", 32, 32, w=100), box("b", 133, 32)))


def test_a_box_outside_the_canvas_is_reported() -> None:
    assert "SVA-G-002" in codes(canvas(box("a", 780, 32), size=(800, 600)))
    assert "SVA-G-002" in codes(canvas(box("a", -1, 32)))


def test_a_label_too_wide_for_its_box_is_reported() -> None:
    wide = box("a", 32, 32, w=96, label="x" * 40)
    assert "SVA-G-003" in codes(canvas(wide))


def test_an_unsanitized_label_is_reported() -> None:
    """A label carrying a control character was measured as something other
    than what gets drawn."""
    assert "SVA-G-008" in codes(canvas(box("a", 32, 32, label="a\nb")))


def test_a_route_to_a_box_that_is_not_here_is_reported() -> None:
    r = Route(src="a", dst="ghost", label="", points=((80, 76), (80, 200)), evidence=EV)
    assert "SVA-G-004" in codes(canvas(box("a", 32, 32), routes=(r,)))


def test_a_route_that_does_not_touch_its_boxes_is_reported() -> None:
    """Geometrically fine and still pointing at the wrong pair is a wrong
    claim, not an ugly one."""
    r = Route(src="a", dst="b", label="", points=((400, 400), (500, 500)), evidence=EV)
    assert "SVA-G-005" in codes(canvas(box("a", 32, 32), box("b", 160, 32), routes=(r,)))


def test_a_degenerate_route_is_reported() -> None:
    r = Route(src="a", dst="b", label="", points=((80, 76),), evidence=EV)
    assert "SVA-G-005" in codes(canvas(box("a", 32, 32), box("b", 160, 32), routes=(r,)))


def test_a_route_leaving_the_canvas_is_reported() -> None:
    r = Route(src="a", dst="b", label="", points=((80, 76), (80, 9999), (200, 32)), evidence=EV)
    assert "SVA-G-005" in codes(canvas(box("a", 32, 32), box("b", 160, 32), routes=(r,)))


def test_a_band_that_does_not_contain_its_members_is_reported() -> None:
    """A band label is a claim about its members. A band labelled level 1
    visually holding a level 3 box says something false."""
    b = Band(label="level 1", y=0, h=20, members=("a",))
    assert "SVA-G-006" in codes(canvas(box("a", 32, 200), bands=(b,)))


def test_a_band_claiming_a_box_that_is_not_here_is_reported() -> None:
    b = Band(label="level 1", y=0, h=200, members=("ghost",))
    assert "SVA-G-006" in codes(canvas(box("a", 32, 32), bands=(b,)))


def test_duplicate_box_ids_are_reported() -> None:
    """The viewer keys boxes by id, so a duplicate silently loses one."""
    assert "SVA-G-007" in codes(canvas(box("a", 32, 32), box("a", 160, 32)))


def test_an_empty_id_is_a_value_not_an_absence() -> None:
    """The repository root is a real module and its id is `""`.

    Asserted through a route and a band, not just a bare box, because those are
    where the id is looked up: a membership test written as `if not r.src`
    would report every arrow touching the root box as dangling. A canvas that
    merely *contains* an empty-id box cannot tell the difference.
    """
    r = Route(
        src="",
        dst="b",
        label="",
        points=((80, 76), (80, 104), (224, 104), (224, 132)),
        evidence=EV,
    )
    band = Band(label="level 1", y=32, h=44, members=("",))
    c = canvas(box("", 32, 32), box("b", 176, 132), routes=(r,), bands=(band,))
    assert codes(c) == []


# --------------------------------------------------------------------------
# Engines
# --------------------------------------------------------------------------


def test_the_declared_layer_attribute_is_read_not_recomputed() -> None:
    """Pass 2 must never re-derive a pass 1 fact.

    The rows here contradict what the edges imply, so an engine recomputing
    depth would place them differently from `derive`.
    """
    s = spec(
        node("a", layer="2"),
        node("b", layer="0"),
        edges=(edge("a", "b"),),
    )
    c = lay_out(s, STYLE, "layered")
    a, b = c.box("a"), c.box("b")
    assert a is not None and b is not None
    assert b.y < a.y, "the declared layer lost to a recomputed one"


def test_a_partially_declared_layer_is_not_mixed_with_an_inferred_one() -> None:
    """Two incomparable scales on one axis would place boxes by coincidence."""
    s = spec(node("a", layer="5"), node("b"), edges=(edge("a", "b"),))
    c = lay_out(s, STYLE, "layered")
    a, b = c.box("a"), c.box("b")
    assert a is not None and b is not None
    assert a.y < b.y, "inference should have run for every node, following the edge"


def test_a_dependency_cycle_still_lays_out() -> None:
    """Refusing a cyclic graph means refusing the repositories that most need
    the picture."""
    s = spec(
        node("a"), node("b"), node("c"), edges=(edge("a", "b"), edge("b", "a"), edge("b", "c"))
    )
    c = lay_out(s, STYLE, "layered")
    assert {b.id for b in c.boxes} == {"a", "b", "c"}
    assert validate(c, STYLE) == ()


def test_a_node_in_a_cycle_is_placed_below_everything_resolved() -> None:
    d = _inferred_layers(
        spec(
            node("r"),
            node("a"),
            node("b"),
            edges=(edge("r", "a"), edge("a", "b"), edge("b", "a")),
        )
    )
    assert d["r"] == 0
    assert d["a"] > 0 and d["b"] > 0


def test_rows_wrap_within_the_stated_bound_including_gaps() -> None:
    """The bound must be the number the constant states.

    Counting only box widths made the threshold and the produced width two
    different numbers: 26 boxes wrapped into a 1515px row against a stated
    1280.
    """
    s = spec(*[node(f"n{i:02d}", layer="0") for i in range(40)])
    c = lay_out(s, STYLE, "layered")
    assert c.width - 2 * STYLE.margin <= MAX_ROW_WIDTH
    assert validate(c, STYLE) == ()


def test_every_coordinate_is_an_integer() -> None:
    """A float differs in its last bits across platforms and the artifact is
    promised byte-identical."""
    s = spec(node("a"), node("b"), node("c"), edges=(edge("a", "b"), edge("a", "c")))
    c = lay_out(s, STYLE, "clustered")
    numbers = [v for b in c.boxes for v in (b.x, b.y, b.w, b.h)]
    numbers += [v for r in c.routes for p in r.points for v in p]
    numbers += [v for bd in c.bands for v in (bd.y, bd.h)]
    numbers += [c.width, c.height]
    assert numbers, "fixture produced nothing to check"
    assert all(type(v) is int for v in numbers)


def test_fan_out_spreads_so_parallel_edges_do_not_stack() -> None:
    """Several edges leaving one box sharing an exit point render as one thick
    line, hiding how many there are."""
    s = spec(
        node("a", layer="0"),
        *[node(f"t{i}", layer="1") for i in range(4)],
        edges=tuple(edge("a", f"t{i}") for i in range(4)),
    )
    c = lay_out(s, STYLE, "layered")
    exits = {r.points[0] for r in c.routes if r.src == "a"}
    assert len(exits) == 4, f"four edges shared {len(exits)} exit point(s)"


def test_bands_cover_every_box_on_a_clustered_canvas() -> None:
    s = spec(node("a"), node("b"), node("c"), edges=(edge("a", "b"), edge("b", "c")))
    c = lay_out(s, STYLE, "clustered")
    banded = {m for band in c.bands for m in band.members}
    assert banded == {b.id for b in c.boxes}
    assert validate(c, STYLE) == ()


def test_grid_does_not_imply_an_order_the_data_lacks() -> None:
    """An edgeless spec laid out by depth would put everything in one row."""
    s = spec(*[node(f"t{i:02d}") for i in range(30)])
    c = lay_out(s, STYLE, "grid")
    assert len({b.y for b in c.boxes}) > 1
    assert validate(c, STYLE) == ()


def test_the_engine_reads_the_style_rather_than_baking_numbers_in() -> None:
    s = spec(node("a"), node("b"))
    small = lay_out(s, Style(), "layered")
    large = lay_out(s, Style(box_height=200, gap_y=200, margin=100), "layered")
    assert large.height > small.height
    assert all(b.h == 200 for b in large.boxes)


def test_an_unknown_engine_refuses_rather_than_substituting() -> None:
    """A silent substitution produces a diagram in the wrong shape while the
    run still looks successful."""
    with pytest.raises(KeyError, match="unknown layout engine"):
        lay_out(spec(node("a")), STYLE, "forcedirected")


def test_every_diagram_kind_has_a_declared_engine() -> None:
    """A kind with no entry is a bug, not a default. Falling back to a generic
    layout is how a flow diagram ends up drawn as a dependency tree."""
    assert set(ENGINE_FOR_KIND) == set(DiagramKind)


# --------------------------------------------------------------------------
# End to end
# --------------------------------------------------------------------------


def real_repo(root: Path) -> None:
    for pkg in ("", "api", "api/routes", "api/views", "worker", "worker/tasks"):
        p = root / "src" / pkg / "__init__.py"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("", encoding="utf8")
    (root / "src/api/views/render.py").write_text("def show():\n    pass\n", encoding="utf8")
    (root / "src/api/routes/handler.py").write_text(
        "from ..views.render import show\n", encoding="utf8"
    )
    (root / "src/worker/tasks/job.py").write_text(
        "from ...api.views.render import show\n", encoding="utf8"
    )


def test_a_real_repository_lays_out_and_validates(tmp_path: Path) -> None:
    real_repo(tmp_path)
    scan = detect(tmp_path)
    graph = build(scan, extract(scan, declared_dependencies(scan)), strict=False)
    produced, _ = derive_all(graph, cluster(graph))
    assert produced, "fixture produced no diagrams, so nothing below ran"
    for ds in produced.values():
        result = lay_out_set(ds)
        assert result.canvases, "a diagram set produced no canvases"
        assert result.ok, [d.render() for d in result.problems]


def test_a_withheld_canvas_does_not_lose_the_others(tmp_path: Path) -> None:
    """Partial failure degrades. One geometry defect loses one diagram."""
    real_repo(tmp_path)
    scan = detect(tmp_path)
    graph = build(scan, extract(scan, declared_dependencies(scan)), strict=False)
    produced, _ = derive_all(graph, cluster(graph))
    ds = produced[DiagramKind.ARCHITECTURE]
    assert len(ds.specs) > 1, "fixture needs more than one spec for this to mean anything"

    # A box budget nothing can satisfy: every label overflows, every spec fails.
    impossible = lay_out_set(ds, Style(box_min_width=1, box_max_width=1, box_pad_x=0))
    assert not impossible.ok
    assert impossible.withheld and not impossible.canvases
    assert {d.code for d in impossible.problems} == {"SVA-G-003"}


@pytest.mark.determinism
def test_layout_is_identical_across_hash_seeds(tmp_path: Path) -> None:
    """The churn source an in-process repeat cannot see.

    Coordinates are integers precisely so this can be a byte comparison rather
    than a tolerance, so the comparison is made byte-for-byte.
    """
    import os
    import subprocess
    import sys

    real_repo(tmp_path)
    script = (
        f"import sys; sys.path.insert(0, {str(Path(__file__).resolve().parents[1])!r})\n"
        "from svarupa.detect import detect\n"
        "from svarupa.extract import extract, declared_dependencies\n"
        "from svarupa.build import build\n"
        "from svarupa.cluster import cluster\n"
        "from svarupa.derive import derive_all\n"
        "from svarupa.layout import lay_out_set\n"
        f"s = detect({str(tmp_path)!r})\n"
        "g = build(s, extract(s, declared_dependencies(s)), strict=False)\n"
        "p, _ = derive_all(g, cluster(g))\n"
        "for k in sorted(p, key=lambda k: k.value):\n"
        "    r = lay_out_set(p[k])\n"
        "    for sid in sorted(r.canvases):\n"
        "        c = r.canvases[sid]\n"
        "        print(k.value, sid, c.width, c.height,\n"
        "              [(b.id, b.label, b.x, b.y, b.w, b.h) for b in c.boxes],\n"
        "              [(x.src, x.dst, x.points) for x in c.routes],\n"
        "              [(d.label, d.y, d.h, d.members) for d in c.bands])\n"
    )
    outputs = {
        subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            env={**os.environ, "PYTHONHASHSEED": seed},
            check=True,
        ).stdout
        for seed in ("0", "1", "4242")
    }
    assert len(outputs) == 1, f"hash seed changed the layout ({len(outputs)} variants)"
    assert next(iter(outputs)).strip(), "the subprocess produced no canvases to compare"
