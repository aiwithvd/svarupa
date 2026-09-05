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
    the picture.

    The `validate == ()` leg of this test used to pass only because the
    crossing check was missing: a cycle puts both nodes in one row, and the
    same-row route ran horizontally through the target's interior. The fixture
    that should have caught the bug certified it instead.
    """
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


def test_grid_rows_wrap_within_the_stated_bound_including_gaps() -> None:
    """Wrapping belongs to `grid` only.

    Counting box widths without the gaps made the threshold and the produced
    width two different numbers: 26 boxes wrapped into a 1515px row against a
    stated 1280.
    """
    s = spec(*[node(f"n{i:02d}") for i in range(40)])
    c = lay_out(s, STYLE, "grid")
    content = c.width - 2 * STYLE.margin
    assert content <= MAX_ROW_WIDTH, f"content row is {content}px against {MAX_ROW_WIDTH}"
    assert validate(c, STYLE) == ()


def test_a_layered_diagram_is_as_wide_as_its_widest_layer() -> None:
    """Layered engines deliberately do not wrap.

    A long edge owns one waypoint per *layer*. Wrapping turns one layer into
    several rows, so a hop that was between adjacent rows spans three of them
    and runs through whatever is in between. Measured on a 200-edge fixture
    before this was removed: 1,900 route-through-box violations.

    The answer to a very wide layer is hierarchy, not folding, which is the
    drill-down the architecture view has and module-deps still needs.
    """
    s = spec(*[node(f"n{i:02d}", layer="0") for i in range(40)])
    c = lay_out(s, STYLE, "layered")
    assert len({b.y for b in c.boxes}) == 1, "a single layer was folded into rows"
    assert c.width > MAX_ROW_WIDTH, "the fixture is not wide enough to test this"
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
    assert {d.code for d in impossible.problems} == {"SVA-G-003", "SVA-R-005"}


def test_withholding_a_sub_view_reports_the_dangling_drill_down(tmp_path: Path) -> None:
    """`DiagramSet.validate` proved the set navigable, but withholding changes
    the set.

    Reported rather than repaired: pruning the link would turn a drillable
    group into a leaf, telling a reader the group has no internal structure.
    """
    real_repo(tmp_path)
    scan = detect(tmp_path)
    graph = build(scan, extract(scan, declared_dependencies(scan)), strict=False)
    produced, _ = derive_all(graph, cluster(graph))
    ds = produced[DiagramKind.ARCHITECTURE]
    drillable = [n for n in ds.root_spec.nodes if n.child_spec is not None]
    assert drillable, "fixture has no drill-down, so nothing was tested"

    victim = drillable[0].child_spec
    assert victim is not None
    result = lay_out_set(ds)
    assert result.ok, "fixture must start clean for the withholding to be the cause"

    # Withhold exactly one sub-view, by hand, and re-run the check.
    good = dict(result.canvases)
    bad = {victim: good.pop(victim)}
    from svarupa.layout import _navigability_after_withholding

    found = _navigability_after_withholding(ds, good, bad)
    assert [d.code for d in found] == ["SVA-R-005"]
    assert found[0].subject == victim


def test_withholding_the_root_reports_the_missing_entry_point(tmp_path: Path) -> None:
    real_repo(tmp_path)
    scan = detect(tmp_path)
    graph = build(scan, extract(scan, declared_dependencies(scan)), strict=False)
    produced, _ = derive_all(graph, cluster(graph))
    ds = produced[DiagramKind.ARCHITECTURE]
    result = lay_out_set(ds)
    good = dict(result.canvases)
    bad = {ds.root: good.pop(ds.root)}

    from svarupa.layout import _navigability_after_withholding

    found = _navigability_after_withholding(ds, good, bad)
    assert any("no entry point" in d.message for d in found)


def test_a_clean_set_is_not_told_it_is_unnavigable(tmp_path: Path) -> None:
    """The baseline for the two tests above."""
    real_repo(tmp_path)
    scan = detect(tmp_path)
    graph = build(scan, extract(scan, declared_dependencies(scan)), strict=False)
    produced, _ = derive_all(graph, cluster(graph))
    for ds in produced.values():
        assert not [d for d in lay_out_set(ds).problems if d.code == "SVA-R-005"]


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


# --------------------------------------------------------------------------
# Review #8: routing must miss every box it does not connect
# --------------------------------------------------------------------------


def crossings(c: Canvas) -> list[str]:
    return [d.message for d in validate(c, STYLE) if d.code == "SVA-G-011"]


def test_the_crossing_check_can_fire() -> None:
    """Guards every no-crossing assertion below.

    Without this, a check that never fires would make all of them pass while
    proving nothing. Design section 6.1 names this invariant; it was previously
    argued away in a routing docstring rather than implemented.
    """
    through = Route(
        src="a",
        dst="z",
        label="",
        points=((80, 76), (80, 154), (80, 232)),
        evidence=EV,
    )
    c = canvas(
        box("a", 32, 32),
        box("mid", 32, 132),
        box("z", 32, 232),
        routes=(through,),
        size=(800, 600),
    )
    assert crossings(c), "a segment straight through a box was not reported"


def test_an_edge_skipping_a_layer_does_not_cross_the_row_between() -> None:
    """Demonstrated failure: the horizontal run was placed at the midpoint of
    the whole vertical span, which for a skipping edge lands inside the
    intervening row."""
    s = spec(
        node("a", layer="0"),
        node("mid", layer="1"),
        node("z", layer="2"),
        edges=(edge("a", "mid"), edge("mid", "z"), edge("a", "z")),
    )
    c = lay_out(s, STYLE, "layered")
    assert crossings(c) == []
    assert validate(c, STYLE) == ()


def test_a_two_node_cycle_does_not_draw_through_its_own_boxes() -> None:
    """Cycles have same-row edges by definition, so every cycle used to draw
    arrows through boxes."""
    s = spec(node("p"), node("q"), edges=(edge("p", "q"), edge("q", "p")))
    c = lay_out(s, STYLE, "layered")
    assert crossings(c) == []


def test_a_long_backward_edge_crosses_nothing_and_points_the_right_way() -> None:
    """A backward edge is flipped to make the graph acyclic, then flipped back.

    The polyline is built in layer order and reversed, so it occupies the same
    pixels but travels from the real source. Without that, every edge in a
    dependency cycle draws its arrowhead at the wrong end, which is a wrong
    claim rather than an ugly one.
    """
    s = spec(
        *[node(f"n{i}", layer=str(i)) for i in range(5)],
        edges=(*[edge(f"n{i}", f"n{i + 1}") for i in range(4)], edge("n4", "n0")),
    )
    c = lay_out(s, STYLE, "layered")
    back = next(r for r in c.routes if r.src == "n4" and r.dst == "n0")

    start, finish = c.box("n4"), c.box("n0")
    assert start is not None and finish is not None
    assert start.x <= back.points[0][0] <= start.right, (
        "the arrow does not start on the module that does the depending"
    )
    assert finish.x <= back.points[-1][0] <= finish.right, (
        "the arrowhead is not on the module being depended upon"
    )
    assert crossings(c) == []


def test_no_route_crosses_a_box_on_a_real_repository(tmp_path: Path) -> None:
    """The shapes that broke it were ordinary, so the check runs end to end."""
    real_repo(tmp_path)
    scan = detect(tmp_path)
    graph = build(scan, extract(scan, declared_dependencies(scan)), strict=False)
    produced, _ = derive_all(graph, cluster(graph))
    assert produced
    total_routes = 0
    for ds in produced.values():
        result = lay_out_set(ds)
        for c in {**result.canvases, **result.withheld}.values():
            total_routes += len(c.routes)
            assert crossings(c) == [], (c.spec_id, crossings(c)[:3])
    assert total_routes > 0, "no routes were drawn, so nothing was checked"


# --------------------------------------------------------------------------
# Review #8: fail-closed and non-finite at the gate
# --------------------------------------------------------------------------


def test_an_evidence_free_box_is_reported() -> None:
    """`Box` has no constructor check, so this gate is the only enforcement of
    the product's central promise on the drawn form."""
    naked = Box(
        id="a", label="x", full_label="x", kind="module", x=32, y=32, w=96, h=44, evidence=()
    )
    assert "SVA-G-009" in codes(canvas(naked))


def test_an_evidence_free_route_is_reported() -> None:
    r = Route(src="a", dst="b", label="", points=((80, 76), (80, 132), (208, 132)), evidence=())
    assert "SVA-G-009" in codes(canvas(box("a", 32, 32), box("b", 176, 132), routes=(r,)))


def test_a_nan_coordinate_is_reported() -> None:
    """The check the plan names as "non-finite", and the one ordered
    comparisons cannot make: `nan < 0` and `nan > width` are both False, so a
    box at (nan, nan) passed every other check on the canvas."""
    nan = float("nan")
    bad = Box(
        id="a", label="x", full_label="x", kind="module", x=nan, y=nan, w=96, h=44, evidence=EV
    )  # type: ignore[arg-type]
    found = codes(canvas(bad))
    assert "SVA-G-010" in found


def test_a_float_coordinate_is_reported_even_when_it_looks_fine() -> None:
    """Byte-identity across platforms rests on integers, and 32.0 renders
    identically to 32 while serializing differently."""
    bad = Box(
        id="a", label="x", full_label="x", kind="module", x=32.0, y=32, w=96, h=44, evidence=EV
    )  # type: ignore[arg-type]
    assert "SVA-G-010" in codes(canvas(bad))


def test_a_bool_is_not_an_int_here() -> None:
    """`isinstance(True, int)` is True in Python, so the check is on `type`."""
    bad = Box(
        id="a", label="x", full_label="x", kind="module", x=True, y=32, w=96, h=44, evidence=EV
    )  # type: ignore[arg-type]
    assert "SVA-G-010" in codes(canvas(bad))


# --------------------------------------------------------------------------
# Review #8: labels that claim a fact must be computed from that fact
# --------------------------------------------------------------------------


def test_bands_are_one_per_level_and_never_per_row() -> None:
    """Bands were labelled by row index while claiming a dependency level.

    Forty boxes all at depth 0 produced "level 1" through "level 4": four
    confident wrong claims, each geometrically contained so validation said
    nothing. Layered engines no longer wrap, so the two can only diverge again
    if someone reintroduces folding, which is exactly when this must fail.
    """
    s = spec(*[node(f"n{i:02d}") for i in range(40)])
    c = lay_out(s, STYLE, "clustered")
    assert len(c.bands) == 1, [b.label for b in c.bands]
    assert c.bands[0].label == "level 1"
    assert set(c.bands[0].members) == {b.id for b in c.boxes}
    assert validate(c, STYLE) == ()


def test_a_cycle_band_says_so_instead_of_claiming_a_level() -> None:
    """A cyclic node's depth is a placement decision, not a measured fact."""
    s = spec(
        node("root"),
        node("p"),
        node("q"),
        edges=(edge("root", "p"), edge("p", "q"), edge("q", "p")),
    )
    c = lay_out(s, STYLE, "clustered")
    labels = [b.label for b in c.bands]
    assert "in a cycle" in labels, labels
    cyclic = next(b for b in c.bands if b.label == "in a cycle")
    assert set(cyclic.members) == {"p", "q"}


# --------------------------------------------------------------------------
# Review #8: silent drops and fan-out collapse
# --------------------------------------------------------------------------


def test_an_edge_to_a_node_that_is_not_in_the_spec_is_diagnosed() -> None:
    """A `continue` here turned a derive defect into a clean canvas, hiding it
    from the code that exists to catch it."""
    s = spec(node("a"), edges=(edge("a", "ghost"),))
    c = lay_out(s, STYLE, "layered")
    assert c.routes == ()
    assert [d.code for d in c.diagnostics] == ["SVA-G-004"]
    assert "ghost" in c.diagnostics[0].subject


def test_fan_out_stays_distinct_past_the_old_slot_budget() -> None:
    """Seven edges from one box previously yielded five exit points, with
    edges six and seven landing exactly on one and two. Real out-degrees pass
    five routinely."""
    for degree in (4, 6, 7, 12):
        s = spec(
            node("a", layer="0"),
            *[node(f"t{i:02d}", layer="1") for i in range(degree)],
            edges=tuple(edge("a", f"t{i:02d}") for i in range(degree)),
        )
        c = lay_out(s, STYLE, "layered")
        exits = {r.points[0] for r in c.routes if r.src == "a"}
        assert len(exits) == degree, f"degree {degree} collapsed to {len(exits)} exits"


def test_fan_points_stay_inside_a_box_that_is_too_narrow_for_its_degree() -> None:
    """Stacking at the centre is honest about crowding; inventing points
    outside the box is not."""
    s = spec(
        node("a", layer="0"),
        *[node(f"t{i:03d}", layer="1") for i in range(200)],
        edges=tuple(edge("a", f"t{i:03d}") for i in range(200)),
    )
    c = lay_out(s, STYLE, "layered")
    a = c.box("a")
    assert a is not None
    for r in c.routes:
        if r.src == "a":
            assert a.x <= r.points[0][0] <= a.right
    assert crossings(c) == []


# --------------------------------------------------------------------------
# Review #8: Style values the validator would co-compute with
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kwargs",
    [
        {"box_pad_x": -20},
        {"gap_x": -1},
        {"gap_y": -1},
        {"margin": -1},
        {"lane_gutter": -1},
    ],
)
def test_negative_spacing_refuses_to_exist(kwargs: dict[str, int]) -> None:
    """A negative pad produced a 202px box holding a 242px label, blessed by
    the geometry check, because `budget = w - 2 * pad` grew instead of
    shrinking. When producer and checker read the same tunable, a corrupt value
    makes them agree on a falsehood."""
    with pytest.raises(ValueError, match="cannot be negative"):
        Style(**kwargs)


def test_a_style_leaving_no_room_for_text_refuses_to_exist() -> None:
    with pytest.raises(ValueError, match="no room for text"):
        Style(box_min_width=10, box_max_width=20, box_pad_x=10)


def test_truncation_never_orphans_a_combining_mark_onto_the_ellipsis() -> None:
    """A cut landing between a base character and its marks left an accent
    floating on the ellipsis.

    Written with explicit escapes. The first version of this test used a
    literal "e-acute", which the editor stored precomposed as U+00E9, so the
    string contained no combining marks and the test could not fail. Same
    lesson as the invisible-character deny-list: when the exact codepoint is
    the subject, write the codepoint.
    """
    import unicodedata

    decomposed = "module/caf" + "e\u0301" * 20
    assert any(unicodedata.combining(c) for c in decomposed), "fixture is not decomposed"
    out = truncate(decomposed, 13, 40)
    assert out.startswith("\u2026")
    assert not unicodedata.combining(out[1]), (
        f"a combining mark is stacked on the ellipsis: {[hex(ord(c)) for c in out[:3]]}"
    )


def test_a_route_running_along_a_box_edge_is_touching_not_crossing() -> None:
    """The crossing test is on the box **interior**.

    A closed-interval version of the check passes every fixture here, because
    nothing in them grazes a box, so the open/closed distinction was asserted
    in a docstring and tested nowhere. It matters: a route legitimately leaves
    the edge of the box it starts on, and gap-routed segments can share a y
    with a row boundary.
    """
    grazing = Route(
        src="a",
        dst="c",
        label="",
        points=((80, 76), (80, 132), (400, 132), (400, 232)),
        evidence=EV,
    )
    # `b` sits in the row whose top edge is exactly y=132, so the horizontal
    # run lies along its top edge without entering it.
    c = canvas(
        box("a", 32, 32),
        box("b", 200, 132),
        box("c", 352, 232),
        routes=(grazing,),
    )
    assert crossings(c) == [], crossings(c)


def test_a_same_row_edge_is_not_drawn_across_its_own_row() -> None:
    """The original shape, reconstructed exactly.

    A two-node cycle put both boxes in one row and the route ran from the
    source's right edge to the target's **right** edge, straight through the
    target's whole interior at mid-height. A version ending at the target's
    left edge would have grazed instead of crossed, so the reconstruction has
    to use the coordinates that failed.
    """
    a, b = box("p", 32, 32, w=96), box("q", 176, 32, w=96)
    original = Route(
        src="p",
        dst="q",
        label="",
        points=((a.right, a.y + a.h // 2), (b.right, b.y + b.h // 2)),
        evidence=EV,
    )
    # Sanity: that polyline does cross, so the engine assertion below is not
    # passing because the check is blind to this shape.
    third = box("r", 176, 32, w=96)
    assert crossings(canvas(a, box("q", 400, 32), third, routes=(original,))) != []

    s = spec(node("p"), node("q"), edges=(edge("p", "q"), edge("q", "p")))
    laid = lay_out(s, STYLE, "layered")
    for r in laid.routes:
        assert r.points[-1][1] in (
            laid.box(r.dst).bottom,  # type: ignore[union-attr]
            laid.box(r.dst).y,  # type: ignore[union-attr]
        ), "a same-row arrow entered its target from the side, across the row"
    assert crossings(laid) == []


def test_placement_preserves_every_field_of_a_box() -> None:
    """Placement copies a box to give it coordinates.

    The copy used to enumerate fields by hand, and when `caption` was added it
    silently dropped it: every box downstream lost its second line and nothing
    failed, because an empty caption is legal. Compared field-by-field against
    the dataclass definition, so a *future* field cannot be dropped either.
    """
    import dataclasses

    s = spec(node("src/api", "api", modules="7"))
    c = lay_out(s, STYLE, "clustered")
    placed = c.box("src/api")
    assert placed is not None
    assert placed.caption == "7 modules", "the caption was dropped in placement"

    from svarupa.layout.engines import _boxes

    original = _boxes(s, STYLE)[0]
    for f in dataclasses.fields(type(original)):
        if f.name in ("x", "y"):
            continue
        assert getattr(placed, f.name) == getattr(original, f.name), (
            f"placement changed or dropped Box.{f.name}"
        )
