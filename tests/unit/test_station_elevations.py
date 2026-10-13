"""Contracts for ranked station elevations and their project-map integration."""

from types import SimpleNamespace

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import pytest

from openamundsen_da.methods.viz.maps.config import (
    MapDefaults,
    load_project_maps_config,
)
from openamundsen_da.methods.viz.maps.render import RenderRuntimeCache, _render_panel
from openamundsen_da.methods.viz.plots.station_elevations import (
    draw_station_elevations,
    prepare_station_elevations,
)


@pytest.fixture
def stations():
    forcing = pd.DataFrame(
        {
            "id": ["b", "a", "outside"],
            "x": [1, 1, 20],
            "y": [1, 1, 20],
            "alt": [1200, 1200, 3000],
        }
    )
    snow = pd.DataFrame(
        {"station_id": ["s2", "s1"], "x": [1, 2], "y": [1, 2], "alt": [2000, 1000]}
    )
    return forcing, snow


def test_extent_sorting_colocated_records_and_input_preservation(stations):
    forcing, snow = stations
    original = forcing.copy(deep=True)
    data = prepare_station_elevations(forcing, snow, extent=(0, 10, 0, 10))
    assert data["forcing"].station_id.tolist() == ["a", "b"]
    assert data["snow"].station_id.tolist() == ["s1", "s2"]
    assert data["forcing"]["rank"].tolist() == [1, 2]
    pd.testing.assert_frame_equal(original, forcing)


@pytest.mark.parametrize("source", ["forcing", "snow", "both"])
def test_source_selection(stations, source):
    data = prepare_station_elevations(*stations, source=source)
    assert set(data) == ({"forcing", "snow"} if source == "both" else {source})


@pytest.mark.parametrize("bad", [np.nan, np.inf, "unknown"])
def test_invalid_elevation_identifies_station(stations, bad):
    forcing, snow = stations
    forcing = forcing.astype({"alt": object})
    forcing.loc[0, "alt"] = bad
    with pytest.raises(ValueError, match="Invalid forcing station elevations: b"):
        prepare_station_elevations(forcing, snow)


def test_missing_elevation_column(stations):
    with pytest.raises(ValueError, match="missing columns: alt"):
        prepare_station_elevations(
            stations[0].drop(columns="alt"), None, source="forcing"
        )


def test_duplicate_ids_fail_but_cross_network_ids_are_retained(stations):
    forcing, snow = stations
    with pytest.raises(ValueError, match="Duplicate forcing station IDs: b"):
        prepare_station_elevations(pd.concat([forcing, forcing.iloc[[0]]]), snow)
    snow.loc[0, "station_id"] = "b"
    assert len(prepare_station_elevations(forcing, snow)["snow"]) == 2


@pytest.mark.parametrize("extent", [(50, 60, 50, 60), (0, 0, 0, 1), (0, np.inf, 0, 1)])
def test_empty_selection_or_invalid_extent(stations, extent):
    with pytest.raises(ValueError, match="extent"):
        prepare_station_elevations(*stations, extent=extent)


def test_explicitly_missing_network_fails(stations):
    with pytest.raises(ValueError, match="No snow stations"):
        prepare_station_elevations(stations[0], None)
    assert set(prepare_station_elevations(stations[0], None, source="forcing")) == {
        "forcing"
    }


def test_bars_have_independent_ranks_shared_limits_and_follow_resize(stations):
    data = prepare_station_elevations(*stations)
    fig, host = plt.subplots()
    try:
        axes = draw_station_elevations(host, data)
        fig.canvas.draw()
        assert [len(ax.patches) for ax in axes] == [3, 2]
        assert [p.get_height() for p in axes[0].patches] == [1200, 1200, 3000]
        assert axes[0].get_ylim() == axes[1].get_ylim()
        assert axes[0].get_xlim() != axes[1].get_xlim()
        assert np.isclose(axes[0].get_position().width, axes[1].get_position().width)
        assert axes[0].get_ylabel() == "Elevation [m]"
        fig.set_size_inches(8, 3)
        host.set_position([0.2, 0.2, 0.6, 0.6])
        fig.canvas.draw()
        assert np.isclose(
            axes[0].get_position().x0,
            host.get_position().x0 + 0.23 * host.get_position().width,
        )
    finally:
        plt.close(fig)


@pytest.mark.parametrize("source", [None, "forcing", "snow", "both"])
def test_map_config_and_render_without_events(tmp_path, stations, source):
    extra = f"\n        source: {source}" if source else ""
    path = tmp_path / "maps.yml"
    path.write_text(
        "maps:\n  elevations:\n    title: Elevations\n    layout: {nrows: 1, ncols: 1}\n    panels:\n"
        "      - row: 0\n        col: 0\n        kind: station_elevations"
        + extra
        + "\n"
    )
    recipe = load_project_maps_config(path).maps[0]
    fig, ax = plt.subplots()
    try:
        artifact = _render_panel(
            ax=ax,
            panel=recipe.panels[0],
            context=SimpleNamespace(stations=stations[0], snow_stations=stations[1]),
            label="c",
            defaults=MapDefaults(),
            extent=(0, 10, 0, 10),
            cache=RenderRuntimeCache(),
            figure_horizontal_default=False,
        )
        assert set(artifact["station_elevations"]) == (
            {"forcing", "snow"} if source in (None, "both") else {source}
        )
        assert ax.get_title(loc="left") == "(c) Elevations of marked stations"
        assert len(ax._oa_child_axes) == len(artifact["axes"])
    finally:
        plt.close(fig)


def test_invalid_source_in_map_recipe(tmp_path):
    path = tmp_path / "maps.yml"
    path.write_text(
        "maps:\n  bad:\n    title: Invalid source\n    layout: {nrows: 1, ncols: 1}\n    panels:\n"
        "      - {row: 0, col: 0, kind: station_elevations, source: ensemble_mean}\n"
    )
    with pytest.raises(ValueError, match="forcing, snow or both"):
        load_project_maps_config(path)


def test_viridis_palette_regular_ticks_and_full_frames(stations):
    from matplotlib.colors import to_rgba
    from openamundsen_da.methods.viz.maps.station_markers import (
        FORCING_STATION_COLOR,
        SNOW_STATION_COLOR,
    )

    fig, host = plt.subplots()
    try:
        axes = draw_station_elevations(
            host, prepare_station_elevations(*stations), fit_frame=True
        )
        fig.canvas.draw()
        for child, color in zip(
            axes, [FORCING_STATION_COLOR, SNOW_STATION_COLOR], strict=True
        ):
            np.testing.assert_allclose(child.patches[0].get_facecolor(), to_rgba(color))
            ticks = child.get_xticks()
            assert np.allclose(np.diff(ticks), np.diff(ticks)[0])
            assert child.get_position().y0 == pytest.approx(host.get_position().y0)
            assert child.get_position().y1 == pytest.approx(host.get_position().y1)
        assert FORCING_STATION_COLOR == "#dce317"
        assert SNOW_STATION_COLOR == "#32648e"
    finally:
        plt.close(fig)


def test_spanning_frame_layout_and_extent_reference(tmp_path):
    from openamundsen_da.methods.viz.maps.frames import configure_frames

    path = tmp_path / "maps.yml"
    path.write_text("""maps:
  study:
    title: Study
    layout: {nrows: 2, ncols: 2, width_ratios: [2, 1], alignment: frames}
    row_views:
      - {row: 0, center: [0, 0], zoom: 10, center_crs: 'EPSG:25832'}
      - {row: 1, center: [0, 0], zoom: 10, center_crs: 'EPSG:25832'}
    panels:
      - {row: 0, col: 0, rowspan: 2, kind: dem, name: domain, field_extent: full}
      - {row: 0, col: 1, kind: overview, scale: 6500000, overview_style: gray, extent_panel: domain, show_subdomain_boundaries: false}
      - {row: 1, col: 1, kind: station_elevations}
""")
    recipe = load_project_maps_config(path).maps[0]
    fig = plt.figure()
    try:
        positions = configure_frames(
            fig, recipe, {0: (0, 200, 0, 100), 1: (0, 200, 0, 100)}
        )
        a, b, c = positions
        assert a[1] + a[3] == pytest.approx(b[1] + b[3])
        assert a[1] == pytest.approx(c[1])
        assert b[3] == pytest.approx(c[3])
        assert a[2] / b[2] == pytest.approx(2)
        w, h = fig.get_size_inches()
        assert a[3] * h / (a[2] * w) == pytest.approx(0.5)
        assert w * 2.54 == pytest.approx(17.4)
        assert recipe.panels[1].show_subdomain_boundaries is False
    finally:
        plt.close(fig)
    path.write_text(
        path.read_text().replace("extent_panel: domain", "extent_panel: missing")
    )
    with pytest.raises(ValueError, match="extent_panel"):
        load_project_maps_config(path)
