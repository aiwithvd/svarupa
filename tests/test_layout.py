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


def test_validation_is_near_linear_on_a_dense_canvas() -> None:
    """_check_route_overlap compared every route pair's every segment pair:
    on the acceptance repo's 1608-edge module-deps view that was 1.3 million
    pairs and ~35 minutes. Bucketed by line it is a fraction of a second. The
    budget here is asymptotic, not a stopwatch: the old loop does not finish
    this canvas inside it, the new one needs a small part of it."""
    import time

    boxes = tuple(
        box(f"m{r}_{c}", 20 + c * 240, 20 + r * 160) for r in range(10) for c in range(30)
    )
    routes = []
    for k in range(1500):
        a, b = boxes[k % len(boxes)], boxes[(k + 1) % len(boxes)]
        lane = 33 + 37 * k  # a distinct x per route, like real waypoint columns
        points = (
            (a.right, a.bottom),
            (lane, a.bottom),
            (lane, b.y),
            (b.x, b.y),
        )
        routes.append(Route(src=a.id, dst=b.id, label="", points=points, evidence=EV))
    dense = canvas(*boxes, routes=tuple(routes), size=(8000, 2000))
    start = time.monotonic()
    validate(dense, STYLE)
    elapsed = time.monotonic() - start
    assert elapsed < 30, f"validation took {elapsed:.1f}s on a dense canvas"


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


def test_in_a_cycle_names_the_cycle_alone_and_never_what_hangs_below_it() -> None:
    """Review #21 N6: "what Kahn could not drain" is the cycle plus everything
    downstream, and labelled a leaf `schema` module "in a cycle"."""
    s = spec(
        node("root"),
        node("p"),
        node("q"),
        node("leaf"),
        edges=(edge("root", "p"), edge("p", "q"), edge("q", "p"), edge("q", "leaf")),
    )
    c = lay_out(s, STYLE, "clustered")
    band_of = {m: b.label for b in c.bands for m in b.members}
    assert "cycle" in band_of["p"] and band_of["p"] == band_of["q"], band_of
    # The fallback floor holds the cycle and the leaf below it in one band:
    # that band says a cycle is inside, never that every box is in one.
    assert band_of["leaf"] != "in a cycle", band_of
    if band_of["leaf"] == band_of["p"]:
        assert band_of["p"].endswith("· cycle inside")
    assert validate(c, STYLE) == ()


def test_the_externals_band_is_labelled_external_never_in_a_cycle() -> None:
    """Externals sink below every level after the cycle fallback has run, so
    a band of stores read "IN A CYCLE" on both acceptance repos (review #20
    S1). Externals only receive arrows and cannot be in one."""
    s = spec(
        node("p"),
        node("q"),
        node("ext:database:Redis", external="database"),
        edges=(edge("p", "q"), edge("q", "p"), edge("p", "ext:database:Redis")),
    )
    c = lay_out(s, STYLE, "clustered")
    by_label = {b.label: set(b.members) for b in c.bands}
    assert by_label == {"in a cycle": {"p", "q"}, "external": {"ext:database:Redis"}}, by_label


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

    The copy used to enumerate fields by hand, and when a field was added it
    silently dropped it: every box downstream lost that data and nothing
    failed, because the empty value was legal. It is `dataclasses.replace`
    now, and this walks the dataclass's own field list, so a *future* field
    cannot be dropped either.
    """
    import dataclasses

    s = spec(node("src/api", "api", modules="7"))
    c = lay_out(s, STYLE, "clustered")
    placed = c.box("src/api")
    assert placed is not None

    from svarupa.layout.engines import _boxes

    original = _boxes(s, STYLE)[0]
    checked = 0
    for f in dataclasses.fields(type(original)):
        if f.name in ("x", "y"):
            continue
        assert getattr(placed, f.name) == getattr(original, f.name), (
            f"placement changed or dropped Box.{f.name}"
        )
        checked += 1
    assert checked >= 8, "the field walk covered almost nothing"


# --------------------------------------------------------------------------
# Review #11 F4: the wave's named properties get tests that can fail
# --------------------------------------------------------------------------


def crossings_between(rows: list[list[str]], links: list[tuple[str, str]]) -> int:
    """Count edge crossings between adjacent rows, the quantity the sweep
    exists to reduce."""
    pos = {nid: (i, j) for i, row in enumerate(rows) for j, nid in enumerate(row)}
    count = 0
    spans = [(pos[a], pos[b]) for a, b in links if a in pos and b in pos]
    for i, ((r1, c1), (_, d1)) in enumerate(spans):
        for (r2, c2), (_, d2) in spans[i + 1 :]:
            if r1 == r2 and ((c1 - c2) * (d1 - d2) < 0):
                count += 1
    return count


def test_the_barycentric_sweep_actually_reduces_crossings() -> None:
    """Deleting the entire sweep passed all 539 tests: nothing anywhere
    asserted that the ordering does anything. This fixture is built so the
    id-sorted seed order crosses maximally, and the assertion is on the
    quantity the commit message named.
    """
    from svarupa.layout.sugiyama import layer_out

    # Two rows of six; edges connect a_i to b_(5-i), so id order gives the
    # maximum 15 crossings and the correct order gives zero.
    ids = [f"a{i}" for i in range(6)] + [f"b{i}" for i in range(6)]
    edges = [(f"a{i}", f"b{5 - i}") for i in range(6)]
    layer = {**{f"a{i}": 0 for i in range(6)}, **{f"b{i}": 1 for i in range(6)}}

    seed_rows = [
        sorted(n for n in ids if layer[n] == 0),
        sorted(n for n in ids if layer[n] == 1),
    ]
    seed_crossings = crossings_between(seed_rows, edges)
    assert seed_crossings == 15, "the fixture no longer crosses under seed order"

    out = layer_out(ids, edges, layer)
    swept = [list(r) for r in out.rows]
    after = crossings_between(swept, edges)
    assert after == 0, f"the sweep left {after} of {seed_crossings} crossings"


def test_the_sweep_is_deterministic_across_processes() -> None:
    """The sweep iterates dicts built from edges, so this is the churn source
    that in-process repetition cannot see."""
    import json as jsonlib
    import os
    import subprocess
    import sys

    script = (
        f"import sys; sys.path.insert(0, {str(Path(__file__).resolve().parents[1])!r})\n"
        "import json\n"
        "from svarupa.layout.sugiyama import layer_out\n"
        "ids = [f'n{i:02d}' for i in range(30)]\n"
        "edges = [(f'n{i:02d}', f'n{(i * 7 + 3) % 30:02d}') for i in range(30)]\n"
        "layer = {f'n{i:02d}': i % 5 for i in range(30)}\n"
        "out = layer_out(ids, edges, layer)\n"
        "print(json.dumps([list(r) for r in out.rows]))\n"
    )
    results = set()
    for seed in ("0", "1", "999"):
        proc = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            env={**os.environ, "PYTHONHASHSEED": seed},
            check=True,
        )
        results.add(proc.stdout)
    assert len(results) == 1, "row ordering changed with the hash seed"
    assert jsonlib.loads(next(iter(results))), "the sweep produced nothing to compare"


# --------------------------------------------------------------------------
# The flow engine, per branch
# --------------------------------------------------------------------------


def flow_spec_of(*edges_: tuple[str, str], layers: dict[str, str]) -> DiagramSpec:
    nodes = tuple(node(nid, layer=level) for nid, level in layers.items())
    return DiagramSpec(
        kind=DiagramKind.DEPLOY_TOPOLOGY,
        id="/spec/root",
        title="t",
        nodes=nodes,
        edges=tuple(edge(a, b) for a, b in edges_),
    )


def test_flow_routes_an_adjacent_hop_through_the_shared_gap() -> None:
    s = flow_spec_of(("api", "db"), layers={"api": "0", "db": "1"})
    c = lay_out(s, STYLE, "flow")
    assert validate(c, STYLE) == (), [d.render() for d in validate(c, STYLE)]
    (r,) = c.routes
    a, b = c.box("api"), c.box("db")
    assert a is not None and b is not None
    assert r.points[0][0] == a.right and r.points[-1][0] == b.x
    assert a.right < b.x, "columns do not read left to right"


def test_flow_sends_a_skipping_edge_through_the_corridor_not_through_boxes() -> None:
    """Routing every skip straight across passed the whole suite before this
    existed; the corridor is the claim, so the assertion is on crossings."""
    s = flow_spec_of(
        ("web", "api"),
        ("api", "db"),
        ("web", "db"),
        layers={"web": "0", "api": "1", "db": "2"},
    )
    c = lay_out(s, STYLE, "flow")
    assert crossings(c) == [], crossings(c)
    skip = next(r for r in c.routes if r.src == "web" and r.dst == "db")
    mid = c.box("api")
    assert mid is not None
    assert any(y < mid.y for _, y in skip.points), (
        "the skipping edge never entered the corridor above the boxes"
    )


def test_flow_survives_a_backward_edge_between_adjacent_columns() -> None:
    """This exact shape raised a raw ValueError: skips were collected with
    abs(diff) != 1 while routing branched on diff == 1, and a backward-adjacent
    edge fell between the two definitions."""
    s = flow_spec_of(
        ("a", "b"),
        ("b", "a"),
        layers={"a": "0", "b": "1"},
    )
    c = lay_out(s, STYLE, "flow")  # must not raise
    assert validate(c, STYLE) == ()
    back = next(r for r in c.routes if r.src == "b" and r.dst == "a")
    target = c.box("a")
    assert target is not None
    assert back.points[-1][0] == target.right, (
        "a backward edge must enter its target from the right, or the "
        "arrowhead points with the flow it actually opposes"
    )


def test_flow_tracks_are_distinct_within_one_gap() -> None:
    """Track x used to be budgeted against the whole diagram's edge count, so
    edges through one gap skewed left and could collide."""
    layers = {"src": "0", **{f"t{i}": "1" for i in range(4)}}
    s = flow_spec_of(*[("src", f"t{i}") for i in range(4)], layers=layers)
    c = lay_out(s, STYLE, "flow")
    xs = sorted(r.points[1][0] for r in c.routes)
    assert len(set(xs)) == len(xs), f"tracks collided: {xs}"
    # Distinctness is not enough: budgeting tracks against the whole diagram's
    # edge count kept them distinct while cramming them into the left sixth of
    # the gap, which collides at higher density. The tracks must use the gap.
    a, b = c.box("src"), c.box("t0")
    assert a is not None and b is not None
    gap_span = b.x - a.right
    spread = xs[-1] - xs[0]
    assert spread >= gap_span // 2, (
        f"four tracks span {spread}px of a {gap_span}px gap: budgeted against "
        "the wrong denominator"
    )
    assert validate(c, STYLE) == ()


def test_flow_runs_a_clear_skip_straight_and_shares_one_trunk_per_target() -> None:
    """Three sources skip a one-box middle column into one sink. The middle
    box sits at the column's centre, so the middle source's run is blocked
    and takes the corridor; the top and bottom runs are clear, go straight at
    their own height, and drop on ONE shared x (a bundle, not a rail). The
    lanes reserved for the two straight edges are given back above."""
    s = flow_spec_of(
        ("a1", "x"),
        ("a2", "x"),
        ("a3", "x"),
        ("a2", "m"),
        ("m", "x"),
        layers={"a1": "0", "a2": "0", "a3": "0", "m": "1", "x": "2"},
    )
    c = lay_out(s, STYLE, "flow")
    assert validate(c, STYLE) == (), [d.render() for d in validate(c, STYLE)]
    assert crossings(c) == [], crossings(c)
    by = {(r.src, r.dst): r for r in c.routes}
    top_of_boxes = min(b.y for b in c.boxes)
    m, x = c.box("m"), c.box("x")
    assert m is not None and x is not None
    for src in ("a1", "a3"):
        r = by[(src, "x")]
        assert len(r.points) == 4, (src, r.points)
        assert all(y >= top_of_boxes for _, y in r.points), "a clear run never climbs"
        assert m.right < r.points[1][0] < x.x, (
            "the drop is in the gap left of the sink's column"
        )
    assert by[("a1", "x")].points[1][0] == by[("a3", "x")].points[1][0], "one trunk per target"
    blocked = by[("a2", "x")]
    assert len(blocked.points) == 6 and any(y < top_of_boxes for _, y in blocked.points), (
        "the run through the middle box takes the corridor"
    )
    assert top_of_boxes == STYLE.margin + 12 + 12 * 1, (
        "the corridor holds one lane: the two straight edges gave theirs back"
    )


def test_two_different_edges_on_one_line_are_a_finding() -> None:
    """Review #17 F2: 65 collinear distinct edges on one canvas, zero
    findings. Shared endpoints (a bundle into one box) and a mutual pair are
    allowed; anything else is reported."""
    a, b, c, d = box("a", 0, 0), box("b", 300, 0), box("c", 0, 200), box("d", 300, 200)
    shared = Route(
        src="a",
        dst="b",
        label="",
        points=((96, 22), (150, 22), (150, 222), (300, 222)),
        evidence=EV,
    )
    other = Route(
        src="c",
        dst="d",
        label="",
        points=((96, 222), (150, 222), (150, 100), (300, 100)),
        evidence=EV,
    )
    cv = canvas(a, b, c, d, routes=(shared, other))
    codes = [x.code for x in validate(cv, STYLE)]
    assert "SVA-G-015" in codes
    found = next(x for x in validate(cv, STYLE) if x.code == "SVA-G-015")
    assert found.severity.value == "ERROR" and "for 122px" in found.message
    bundle = Route(
        src="c",
        dst="b",
        label="",
        points=((96, 222), (150, 222), (150, 22), (300, 22)),
        evidence=EV,
    )
    assert "SVA-G-015" not in [
        x.code for x in validate(canvas(a, b, c, d, routes=(shared, bundle)), STYLE)
    ]


def test_flow_corridor_climbs_from_one_column_take_different_x() -> None:
    """Indexed per source, two sources with the same exit rank climbed on one
    line (review #17 F2)."""
    # Two boxes per column: rows align, so every straight run from a1 or a2
    # meets m1 or m2 and the skips take the corridor. The upper source is
    # wider than the lower one, so a climb at the lower box's own right
    # edge would cut through the upper box.
    s = flow_spec_of(
        ("a1_with_a_long_name", "m1"),
        ("a1_with_a_long_name", "m2"),
        ("a2", "m1"),
        ("a2", "m2"),
        ("a1_with_a_long_name", "x"),
        ("a2", "y"),
        ("m2", "x"),
        ("m2", "y"),
        layers={
            "a1_with_a_long_name": "0",
            "a2": "0",
            "m1": "1",
            "m2": "1",
            "x": "2",
            "y": "2",
        },
    )
    c = lay_out(s, STYLE, "flow")
    assert not [x for x in validate(c, STYLE) if x.severity.value == "ERROR"]
    corridor = [r for r in c.routes if len(r.points) == 6]
    assert len(corridor) >= 2, "the fixture must send at least two edges up the corridor"
    climbs = [r.points[1][0] for r in corridor]
    assert len(set(climbs)) == len(climbs), climbs
    assert "SVA-G-015" not in [x.code for x in validate(c, STYLE)]


def test_flow_entry_heights_do_not_meet_exit_heights_across_a_gap() -> None:
    """Boxes in adjacent columns share row bands, so a single exit at mid
    height met a single entry at mid height and two edges shared the gap's
    horizontal. Entries sit between exits."""
    s = flow_spec_of(("a", "d"), ("b", "c"), layers={"a": "0", "b": "0", "c": "1", "d": "1"})
    c = lay_out(s, STYLE, "flow")
    assert validate(c, STYLE) == (), [x.render() for x in validate(c, STYLE)]


def test_layered_hops_through_one_gap_take_different_tracks() -> None:
    """Only same-row edges had their own track; every downward hop and every
    reversed edge ran at the gap's midpoint, so 842 pairs of different edges
    shared a line on one acceptance canvas (SVA-G-015, review #17 F2). Three
    edges from row 0 to row 1 plus a same-row edge all
    cross the first gap: every horizontal run in it has its own y."""
    s = spec(
        node("a"),
        node("b"),
        node("c"),
        node("d"),
        node("e"),
        node("f"),
        edges=(edge("a", "e"), edge("b", "f"), edge("c", "d"), edge("a", "b")),
    )
    c = lay_out(s, STYLE, "layered")
    assert not [x for x in validate(c, STYLE) if x.severity.value == "ERROR"]
    codes = [x.code for x in validate(c, STYLE)]
    assert "SVA-G-015" not in codes, [
        x.render() for x in validate(c, STYLE) if x.code == "SVA-G-015"
    ]
    top = c.box("a")
    assert top is not None
    horizontals = {
        (r.src, r.dst): y1
        for r in c.routes
        for (x1, y1), (x2, y2) in zip(r.points, r.points[1:], strict=False)
        if y1 == y2 and x1 != x2 and y1 > top.bottom
    }
    assert len(horizontals) >= 4, horizontals
    assert len(set(horizontals.values())) == len(horizontals), "one y per edge in the gap"


def test_a_route_label_on_a_band_label_is_a_finding() -> None:
    """Review #20 S5: six band-label collisions with route verbs survived
    every gate because no gate knew where a band label was."""
    from svarupa.layout.geometry import Band, band_label_rect

    a, b = box("a", 100, 120), box("b", 400, 120)
    band = Band(label="level 1", y=120, h=44, members=("a", "b"))
    x, y, w, h = band_label_rect(band, STYLE)
    on_band = Route(
        src="a",
        dst="b",
        label="reads/writes",
        points=((196, y + h // 2), (400, y + h // 2)),
        evidence=EV,
        label_at=(x + w // 2, y + h // 2),
        label_w=60,
    )
    cv = Canvas(
        spec_id="/spec/root",
        kind=DiagramKind.MODULE_DEPS,
        engine="clustered",
        title="t",
        subtitle="",
        width=600,
        height=300,
        boxes=(a, b),
        routes=(on_band,),
        bands=(band,),
    )
    found = [d for d in validate(cv, STYLE) if d.code == "SVA-G-013"]
    assert found and "band label level 1" in found[0].message, [
        d.message for d in validate(cv, STYLE)
    ]
    # The settle keeps a verb off the band label when it lays the view out.
    s = spec(
        node("p"),
        node("q"),
        node("ext:database:Redis", external="database"),
        edges=(edge("p", "q"), edge("q", "p"), edge("p", "ext:database:Redis")),
    )
    c = lay_out(s, STYLE, "clustered")
    assert validate(c, STYLE) == ()


def test_flow_columns_align_at_the_top_once_the_tallest_exceeds_a_screen() -> None:
    """Review #20 S6: one ingress centred against a 24-box column put the
    story 700px below the fold. Short flows still centre."""
    tall = [node(f"d{i:02d}", layer="1") for i in range(12)]
    s = spec(node("in", layer="0"), *tall, edges=tuple(edge("in", t.id) for t in tall))
    c = lay_out(s, STYLE, "flow")
    by_id = {b.id: b for b in c.boxes}
    assert by_id["in"].y == min(b.y for b in c.boxes), "the single source box sits at the top"
    short = spec(
        node("a", layer="0"),
        node("b1", layer="1"),
        node("b2", layer="1"),
        edges=(edge("a", "b1"), edge("a", "b2")),
    )
    c2 = lay_out(short, STYLE, "flow")
    by2 = {b.id: b for b in c2.boxes}
    assert by2["b1"].y < by2["a"].y < by2["b2"].bottom, "a short flow centres its columns"


def test_flow_widens_a_gap_whose_corridor_climbs_would_leave_it() -> None:
    """Each corridor climb out of a column sits at col_right + 10 + 4k, and
    the gap was a fixed 84px: climb 19 ran through the next column's boxes.
    This is the SVA-G-011 shape that withheld the 4,434-edge acceptance
    repo's data-flow root."""
    layers = {"src": "0", **{f"m{i}": "1" for i in range(25)}, **{f"t{i}": "2" for i in range(25)}}
    s = flow_spec_of(*[("src", f"t{i}") for i in range(25)], layers=layers)
    c = lay_out(s, STYLE, "flow")
    assert validate(c, STYLE) == (), [d.render() for d in validate(c, STYLE)]


def test_flow_extends_the_trailing_lane_past_the_last_column() -> None:
    """Climbs and backward drops around the LAST column ran off the canvas
    (SVA-G-005): the width reserved one fixed gutter no matter how many
    edges rounded that side."""
    layers = {"hub": "0", **{f"a{i}": "1" for i in range(30)}}
    s = flow_spec_of(*[(f"a{i}", "hub") for i in range(30)], layers=layers)
    c = lay_out(s, STYLE, "flow")
    assert validate(c, STYLE) == (), [d.render() for d in validate(c, STYLE)]


def test_flow_widening_keeps_the_canvas_wide_enough_for_the_env_strip() -> None:
    """Review #26 F2: the demand-driven gap-widening branch dropped the
    `strip_right` term from the canvas width, so a wide environment strip
    plus one congested gap produced a canvas narrower than its own strip."""
    layers = {"src": "0", **{f"m{i}": "1" for i in range(25)}, **{f"t{i}": "2" for i in range(25)}}
    envs = tuple(
        DiagramNode(
            id=f"env:{e}",
            label=f"{e}-production-environment",
            kind="environment",
            evidence=EV,
        )
        for e in ("us", "eu", "apac", "staging", "qa", "dev", "sandbox", "preview")
    )
    s = DiagramSpec(
        kind=DiagramKind.DEPLOY_TOPOLOGY,
        id="/spec/root",
        title="t",
        nodes=tuple(node(nid, layer=level) for nid, level in layers.items()) + envs,
        edges=tuple(edge("src", f"t{i}") for i in range(25)),
    )
    c = lay_out(s, STYLE, "flow")
    assert validate(c, STYLE) == (), [d.render() for d in validate(c, STYLE)]
    strip_right = max(b.right for b in c.boxes if b.kind == "environment")
    assert strip_right <= c.width, f"strip ends at {strip_right}, canvas is {c.width} wide"


def test_flow_fan_wrap_separates_exits_from_backward_entries_on_a_full_side() -> None:
    """Review #26 F4: the wrapped fan maps port k and port k+cycle to one y.
    Exits and backward entries share the right-side port list, so with more
    same-side ports than the box is tall (43 at 44px), a wrapped exit stub
    and a wrapped backward-entry stub leave the box's right edge at the SAME
    height — two different edges, no shared endpoint, SVA-G-015. The fan
    keeps exits even and backward entries odd once the wrap is active, which
    is the parity the forward-entry side already had."""
    layers = {"hub": "1", **{f"t{i}": "2" for i in range(41)}, **{f"s{i}": "2" for i in range(4)}}
    edges = [("hub", f"t{i}") for i in range(41)] + [(f"s{i}", "hub") for i in range(4)]
    s = flow_spec_of(*edges, layers=layers)
    c = lay_out(s, STYLE, "flow")
    assert validate(c, STYLE) == (), [d.render() for d in validate(c, STYLE)]


def test_flow_fan_heights_wrap_inside_a_box_with_more_ports_than_pixels() -> None:
    """With more exits than the box is tall the fan step collapses to 1px
    and an unwrapped fan walks off the bottom edge (SVA-G-005 on a module
    with 70+ outgoing connections)."""
    layers = {"hub": "0", **{f"s{i}": "1" for i in range(70)}}
    s = flow_spec_of(*[("hub", f"s{i}") for i in range(70)], layers=layers)
    c = lay_out(s, STYLE, "flow")
    assert validate(c, STYLE) == (), [d.render() for d in validate(c, STYLE)]
    hub = c.box("hub")
    assert hub is not None
    for r in c.routes:
        assert hub.y - 4 <= r.points[0][1] <= hub.bottom + 4, r.points[0]


def test_layered_ports_stay_distinct_when_the_residue_grid_is_too_coarse() -> None:
    """A hub in a mutual dependency with every child carries both directions
    on one side. The residue alignment (& ~3, | 2) keeps exits, entries and
    waypoint centres apart, but below a 4px fan step it collapsed four
    adjacent ports onto one x and their stubs overlapped exactly
    (SVA-G-015 on the acceptance repo's module-deps tree of its job
    framework)."""
    children = [node(f"c{i:02d}", layer="1") for i in range(40)]
    edges = tuple(
        e for t in children for e in (edge("hub", t.id), edge(t.id, "hub"))
    )
    s = spec(node("hub", layer="0"), *children, edges=edges)
    c = lay_out(s, STYLE, "layered")
    assert validate(c, STYLE) == (), [d.render() for d in validate(c, STYLE)]


def test_sublabels_truncate_from_the_tail_and_paths_from_the_head() -> None:
    """Review #20 C4: `…r · FastAPI · 2 routes` on a story box, the ellipsis
    having eaten the word a reader needed."""
    long_sub = "FastAPI · 7 routes · Celery · 3 tasks · JWT · React · more words"
    cut = truncate(long_sub, STYLE.sublabel_font_size, 120, keep_tail=False)
    assert cut.startswith("FastAPI · 7") and cut.endswith("…"), cut
    assert truncate("src/services/billing/invoice", STYLE.font_size, 90).startswith("…")
    s = spec(DiagramNode(id="m", label="m", kind="module", evidence=EV, sublabel=long_sub))
    c = lay_out(s, STYLE, "clustered")
    assert c.boxes[0].sublabel.startswith("FastAPI"), c.boxes[0].sublabel


def test_a_verb_far_from_both_of_its_boxes_is_dropped_not_stranded() -> None:
    """Review #21 N16: `reads/writes` sat on the top lane of a corridor whose
    ends were 1000px apart. A verb is drawn within MAX_VERB_DISTANCE of one of
    its boxes or not at all; the passport still says it."""
    from svarupa.layout.engines import MAX_VERB_DISTANCE, _settle_labels

    a, b = box("a", 0, 500), box("b", 1200, 500)
    far = Route(
        src="a",
        dst="b",
        label="reads/writes",
        points=((96, 522), (110, 522), (110, 40), (1190, 40), (1190, 522), (1200, 522)),
        evidence=EV,
        label_at=(650, 40),
        label_w=80,
    )
    near = Route(
        src="a",
        dst="b",
        label="calls",
        points=((96, 530), (1200, 530)),
        evidence=EV,
        label_at=(648, 530),
        label_w=40,
    )
    settled = {r.label: r for r in _settle_labels([far, near], [a, b], STYLE, frozenset())}
    # The verb slides along the climb toward its box instead of sitting on the
    # lane (review #23 F4: dropping it left deploy arrows without a verb).
    at = settled["reads/writes"].label_at
    assert at is not None and at[0] == 110 and 300 < at[1] < 522, at
    assert settled["calls"].label_at is not None
    assert MAX_VERB_DISTANCE >= 200
    # With the climb blocked, the verb may take the lane only within reach of
    # an end, or not at all.
    # The climb is blocked from the box up to the reach limit, so the only free
    # positions on it are farther than 240px from either end.
    tall = box("t", 60, 522 - MAX_VERB_DISTANCE, w=100, h=MAX_VERB_DISTANCE)
    settled2 = {r.label: r for r in _settle_labels([far], [a, b, tall], STYLE, frozenset())}
    at2 = settled2["reads/writes"].label_at
    # Within 240px along the route of an end: on the climb or the drop, in
    # their lower part; never on the lane, which is 482px from either box.
    assert at2 is None or (at2[0] in (110, 1190) and at2[1] >= 522 - MAX_VERB_DISTANCE), at2


def test_a_verb_moves_off_a_stub_onto_a_segment_with_visible_line() -> None:
    """The e-kisanmitra visual review: an 89px stub holding a 79px mask hid
    every pixel of the line under it, so the verb read as floating text. A
    segment must show line on both sides of the mask; the long climb within
    reach of the box takes the verb instead, with the margin kept."""
    from svarupa.layout.engines import _LABEL_LINE_MARGIN, _settle_labels

    a, b = box("a", 0, 480), box("b", 300, 100)
    r = Route(
        src="a",
        dst="b",
        label="depends on",
        # The sip -> livekit shape: short stub right, long climb, short entry.
        points=((96, 502), (185, 502), (185, 122), (300, 122)),
        evidence=EV,
        label_at=(140, 502),
        label_w=79,
    )
    (out,) = _settle_labels([r], [a, b], STYLE, frozenset())
    assert out.label_at is not None
    assert out.label_at[0] == 185, "the verb sits on the climb, not the stub"
    # The mask keeps the margin: the corner is clear of it by exactly the
    # line margin, so the line visibly enters the mask from below.
    h = STYLE.label_font_size + STYLE.label_pad
    assert 502 - (out.label_at[1] + h // 2) == _LABEL_LINE_MARGIN


def test_the_corridor_lane_still_does_not_take_the_verb() -> None:
    """The two rules compose: the longest visible segment takes the verb,
    UNLESS that segment is the corridor lane above every box (review #21
    N16), in which case the climb or drop keeps it."""
    from svarupa.layout.engines import _settle_labels

    a, b = box("a", 0, 500), box("b", 1200, 500)
    r = Route(
        src="a",
        dst="b",
        label="reads/writes",
        points=((96, 522), (110, 522), (110, 40), (1190, 40), (1190, 522), (1200, 522)),
        evidence=EV,
        label_at=(650, 40),
        label_w=80,
    )
    (out,) = _settle_labels([r], [a, b], STYLE, frozenset())
    assert out.label_at is not None
    assert out.label_at[1] != 40, "not on the lane"
    assert out.label_at[0] in (110, 1190), "on the climb or the drop"


def test_names_truncate_from_the_tail_and_paths_from_the_head() -> None:
    """Review #23 C4: `…rson_employment_type_changed` lost the prefix that
    grouped the function with its siblings."""
    s = spec(
        DiagramNode(
            id="f",
            label="rule_person_employment_type_changed_again_and_again",
            kind="function",
            evidence=EV,
        )
    )
    c = lay_out(s, STYLE, "clustered")
    assert c.boxes[0].label.startswith("rule_person") and c.boxes[0].label.endswith("…")
    s2 = spec(
        DiagramNode(
            id="p",
            label="src/very/long/path/to/services/billing/invoices/x",
            kind="module",
            evidence=EV,
        )
    )
    c2 = lay_out(s2, STYLE, "clustered")
    assert c2.boxes[0].label.startswith("…") and c2.boxes[0].label.endswith("/x")
