"""Publication export, annotation spacing and station-layer contracts."""

from types import SimpleNamespace

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.cm import ScalarMappable
from matplotlib.colors import Normalize
import numpy as np
import pandas as pd
from PIL import Image
import pytest

from openamundsen_da.methods.viz.common import save_figure_png
from openamundsen_da.methods.viz.maps import annotations, layout, panel_renderers
from openamundsen_da.methods.viz.maps.config import LegendItemSpec, MapPanelSpec
from openamundsen_da.methods.viz.maps.station_markers import StationMarker
from openamundsen_da.methods.viz.plots.station_elevations import draw_station_elevations


def test_tight_width_export_preserves_aspect_and_trims_canvas(tmp_path):
    fig, ax = plt.subplots(figsize=(7, 4))
    try:
        ax.plot([0, 2], [0, 1])
        ax.set_aspect("equal")
        ax.set_title("Single-line title", loc="left")
        ax.set_ylabel("Visible y label")
        ratio = fig.get_figwidth() / fig.get_figheight()
        output = tmp_path / "tight.png"
        save_figure_png(
            fig, output, dpi=150, bbox_inches="tight", pad_inches=0, target_width_in=5
        )
        assert fig.get_figwidth() / fig.get_figheight() == pytest.approx(ratio)
        with Image.open(output) as image:
            assert image.width == 750
            pixels = np.asarray(image.convert("RGB"))
            rows, cols = np.where(np.any(pixels < 245, axis=2))
            assert (
                max(
                    cols.min(),
                    rows.min(),
                    image.width - 1 - cols.max(),
                    image.height - 1 - rows.max(),
                )
                <= 5
            )
        assert ax.get_data_ratio() == pytest.approx(0.5)
    finally:
        plt.close(fig)


@pytest.mark.parametrize("size", [6, 12])
@pytest.mark.parametrize("compact", [False, True])
def test_vertical_colorbar_centered_header_and_visible_endpoints(
    monkeypatch, size, compact
):
    monkeypatch.setattr(layout, "_POSTER_VERTICAL_COLORBAR_UNIT_HEADER_ENABLED", True)
    monkeypatch.setattr(layout, "_COLORBAR_TITLE_SIZE", size)
    monkeypatch.setattr(layout, "_COLORBAR_TICK_SIZE", size)
    fig, ax = plt.subplots(figsize=(6, 4))
    try:
        layout.attach_colorbar(
            ax,
            ScalarMappable(norm=Normalize(0, 4000), cmap="Greys_r"),
            label="Elevation [m]",
            ticks=(0, 1000, 2000, 3000, 4000),
            layout="vertical",
            compact=compact,
        )
        fig.canvas.draw()
        bar = ax.child_axes[-1]
        ticks = bar.get_yticklabels()
        assert ticks[0].get_visible() and ticks[-1].get_visible()
        assert ticks[-1].get_text() == "4000"
        renderer = fig.canvas.get_renderer()
        header = bar.texts[-1].get_window_extent(renderer)
        box = bar.get_window_extent(renderer)
        assert (header.x0 + header.x1) / 2 == pytest.approx((box.x0 + box.x1) / 2)
        assert header.y0 - box.y1 == pytest.approx(3 * fig.dpi / 72)
        assert not header.overlaps(ticks[-1].get_window_extent(renderer))
    finally:
        plt.close(fig)


@pytest.mark.parametrize("include_holdout", [False, True])
def test_station_legend_uses_only_requested_rows(include_holdout):
    fig, ax = plt.subplots(figsize=(5, 3))
    try:
        item = LegendItemSpec(
            kind="station_categories",
            source=None if include_holdout else "networks",
            placement="inside",
        )
        panel = MapPanelSpec(kind="dem", row=0, col=0, legend_items=(item,))
        annotations.draw_panel_inside_items(
            ax, panel=panel, artifacts={}, legend_source_handles_getter=lambda *args: []
        )
        fig.canvas.draw()
        legend = ax.child_axes[-1]
        texts = legend.texts
        assert len(texts) == (4 if include_holdout else 3)
        renderer = fig.canvas.get_renderer()
        bounds = legend.get_window_extent(renderer)
        top = bounds.y1 - texts[0].get_window_extent(renderer).y1
        bottom = texts[-1].get_window_extent(renderer).y0 - bounds.y0
        assert top == pytest.approx(bottom, abs=0.1)
        assert 0 <= top < 5
        other = fig.add_axes([0.1, 0.1, 0.5, 0.2])
        annotations.draw_station_categories_below(
            other, y=0.8, include_holdout=include_holdout
        )
        assert len(other.texts) == len(texts)
    finally:
        plt.close(fig)


@pytest.mark.parametrize("reverse", [False, True])
def test_snow_and_colocated_stations_render_above_forcing(monkeypatch, reverse):
    kinds = ["forcing", "snow", "both", "holdout"]
    if reverse:
        kinds.reverse()
    markers = [
        StationMarker(kind=k, x=1, y=1, station_id=k, name=k, alt=1000) for k in kinds
    ]
    monkeypatch.setattr(
        panel_renderers, "classify_station_markers", lambda *args, **kw: markers
    )
    context = SimpleNamespace(stations=pd.DataFrame(), snow_stations=pd.DataFrame())
    fig, ax = plt.subplots()
    try:
        panel_renderers.draw_stations_overlay(
            ax,
            context,
            (0, 2, 0, 2),
            show_station_marker=True,
            show_stations_name=False,
            show_stations_elev=False,
            station_marker_mode="sources_and_roles",
        )
        cursor = 0
        levels = {}
        for kind in kinds:
            count = 2 if kind == "both" else 1
            levels[kind] = [
                c.get_zorder() for c in ax.collections[cursor : cursor + count]
            ]
            cursor += count
        assert min(levels["snow"] + levels["both"]) > max(levels["forcing"])
        assert min(levels["holdout"]) > max(levels["snow"] + levels["both"])
    finally:
        plt.close(fig)


def test_elevation_label_can_be_hidden():
    fig, ax = plt.subplots()
    try:
        table = pd.DataFrame({"rank": [1, 2], "alt": [1000, 2000]})
        children = draw_station_elevations(
            ax, {"forcing": table}, show_elevation_label=False
        )
        assert children[0].get_ylabel() == ""
        assert len(children[0].patches) == 2
        assert children[0].get_yticklabels()
    finally:
        plt.close(fig)


@pytest.mark.parametrize(
    "extra",
    ["extent_source: unknown", "extent_source: model_grid, extent_panel: domain"],
)
def test_overview_extent_source_rejects_ambiguous_or_unknown_config(tmp_path, extra):
    from openamundsen_da.methods.viz.maps.config import load_project_maps_config

    path = tmp_path / "maps.yml"
    path.write_text(
        "maps:\n  map:\n    title: Map\n    layout: {nrows: 1, ncols: 1}\n    panels:\n"
        "      - {kind: overview, row: 0, col: 0, scale: 4000000, " + extra + "}\n"
    )
    with pytest.raises(ValueError, match="extent_source"):
        load_project_maps_config(path)


def test_horizontal_bar_aligns_count_subtitle_after_tight_export(tmp_path):
    from openamundsen_da.methods.viz.maps.frames import align_bottom_annotations

    fig = plt.figure(figsize=(7, 4))
    detail = fig.add_axes([0.08, 0.2, 0.5, 0.65])
    chart = fig.add_axes([0.7, 0.2, 0.25, 0.3])
    try:
        layout.attach_colorbar(
            detail,
            ScalarMappable(norm=Normalize(0, 4000), cmap="Greys_r"),
            label="Elevation [m]",
            ticks=(0, 2000, 4000),
            layout="horizontal",
        )
        draw_station_elevations(
            chart,
            {"snow": pd.DataFrame({"rank": [1, 2], "alt": [1000, 2000]})},
            fit_frame=True,
            show_elevation_label=False,
        )
        align_bottom_annotations(fig, [detail, chart])
        save_figure_png(
            fig,
            tmp_path / "aligned.png",
            dpi=150,
            bbox_inches="tight",
            pad_inches=0,
            target_width_in=6,
        )
        fig.canvas.draw()
        bar = next(
            child
            for child in detail.child_axes
            if child.get_gid() == "oa_da_horizontal_colorbar"
        )
        label = next(
            text for text in chart.texts if text.get_gid() == "oa_da_station_count"
        )
        assert label.get_text() == "Station count [-]"
        renderer = fig.canvas.get_renderer()
        bbox = bar.get_window_extent(renderer)
        text_bbox = label.get_window_extent(renderer)
        assert (bbox.y0 + bbox.y1) / 2 == pytest.approx(
            (text_bbox.y0 + text_bbox.y1) / 2
        )
        assert bbox.y1 < detail.get_window_extent(renderer).y0
    finally:
        plt.close(fig)


def test_frame_column_gap_is_opt_in():
    from openamundsen_da.methods.viz.maps.config import _parse_layout

    assert (
        _parse_layout({"nrows": 1, "ncols": 1}, context="layout").column_gap_in == 0.82
    )
    assert (
        _parse_layout(
            {"nrows": 1, "ncols": 1, "alignment": "frames", "column_gap_in": 0.35},
            context="layout",
        ).column_gap_in
        == 0.35
    )
    with pytest.raises(ValueError):
        _parse_layout({"nrows": 1, "ncols": 1, "column_gap_in": -1}, context="layout")


@pytest.mark.parametrize("offset", [0.10, -0.026])
def test_compact_network_and_boundary_legend_has_even_rows_and_configured_background(
    offset,
):
    fig, ax = plt.subplots(figsize=(6, 4))
    try:
        panel = MapPanelSpec(
            kind="dem",
            row=0,
            col=0,
            inside_legend_alpha=0.94,
            inside_legend_offset_x=offset,
            legend_items=(
                LegendItemSpec(
                    kind="station_categories", source="networks", placement="inside"
                ),
                LegendItemSpec(kind="subdomain_boundary", placement="inside"),
            ),
        )
        annotations.draw_panel_inside_items(
            ax,
            panel=panel,
            artifacts={},
            legend_source_handles_getter=lambda *args: [],
        )
        fig.canvas.draw()
        legend = ax.child_axes[-1]
        assert [text.get_text() for text in legend.texts] == [
            "Forcing station",
            "Snow observation station",
            "Forcing + snow station",
            "Subdomain boundary",
        ]
        assert [text.get_position()[1] for text in legend.texts] == pytest.approx(
            [0.875, 0.625, 0.375, 0.125]
        )
        assert legend.get_facecolor() == pytest.approx((1, 1, 1, 0.94))
        actual_offset = (
            legend.get_position().x0 - ax.get_position().x0
        ) / ax.get_position().width
        assert actual_offset == pytest.approx(0.026 + offset)
        assert legend.lines[0].get_color() == panel_renderers._SUBDOMAIN_BOUNDARY_COLOR
        assert (
            legend.lines[0].get_linewidth() == panel_renderers._SUBDOMAIN_BOUNDARY_WIDTH
        )
        assert len(legend.lines[0].get_path_effects()) == 2
    finally:
        plt.close(fig)


def test_smaller_row_gap_enlarges_both_right_frames_without_distorting_detail():
    from dataclasses import replace
    from openamundsen_da.methods.viz.maps.config import LayoutSpec
    from openamundsen_da.methods.viz.maps.frames import configure_frames

    fig = plt.figure()
    recipe = SimpleNamespace(
        layout=LayoutSpec(nrows=2, ncols=2, width_ratios=(2, 1), alignment="frames"),
        panels=(
            MapPanelSpec(kind="dem", row=0, col=0, rowspan=2),
            MapPanelSpec(kind="overview", row=0, col=1),
            MapPanelSpec(kind="station_elevations", row=1, col=1),
        ),
    )
    try:
        before = configure_frames(fig, recipe, {0: (0, 2, 0, 1.4)})
        recipe.layout = replace(recipe.layout, row_gap_in=0.30)
        after = configure_frames(fig, recipe, {0: (0, 2, 0, 1.4)})
        assert after[0] == pytest.approx(before[0])
        assert after[1][3] > before[1][3]
        assert after[2][3] == pytest.approx(after[1][3])
        assert after[0][1] == pytest.approx(after[2][1])
    finally:
        plt.close(fig)


@pytest.mark.parametrize("key", ["inside_legend_alpha", "inside_legend_offset_x"])
def test_inside_legend_controls_reject_invalid_values(key):
    from openamundsen_da.methods.viz.maps.config import _parse_panel

    with pytest.raises(ValueError, match="must be between"):
        _parse_panel({"kind": "dem", "row": 0, "col": 0, key: 1.1}, context="panel")


def test_frame_row_gap_defaults_and_validation():
    from openamundsen_da.methods.viz.maps.config import _parse_layout

    base = {"nrows": 2, "ncols": 2}
    assert _parse_layout(base, context="layout").row_gap_in == 0.62
    assert (
        _parse_layout({**base, "row_gap_in": 0.34}, context="layout").row_gap_in == 0.34
    )
    with pytest.raises(ValueError):
        _parse_layout({**base, "row_gap_in": -0.1}, context="layout")
