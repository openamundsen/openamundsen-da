"""Elevation-ranked station bars, usable independently or in a project map."""

from __future__ import annotations

from collections.abc import Mapping

import numpy as np
import pandas as pd
from matplotlib.axes import Axes
from matplotlib.ticker import MaxNLocator

from openamundsen_da.methods.viz.maps.station_markers import (
    FORCING_STATION_COLOR,
    SNOW_STATION_COLOR,
)
from openamundsen_da.methods.viz.plots.theme import GRID_ALPHA, GRID_LS, GRID_LW


def prepare_station_elevations(
    forcing_stations: pd.DataFrame | None,
    snow_stations: pd.DataFrame | None,
    *,
    source: str = "both",
    extent: tuple[float, float, float, float] | None = None,
) -> dict[str, pd.DataFrame]:
    """Select and sort station records without pooling colocated observations.

    Coordinates must use the extent CRS; extent order is xmin, xmax, ymin, ymax.
    Elevations come from metadata ``alt`` in metres, never from a sampled DEM.
    Missing/invalid metadata and empty requested networks are errors. No temporal
    availability screening is performed here: the caller owns its inventory.
    """
    if source not in {"forcing", "snow", "both"}:
        raise ValueError("station_elevations source must be forcing, snow or both")
    if extent is not None:
        if (
            len(extent) != 4
            or not np.isfinite(extent).all()
            or extent[0] >= extent[1]
            or extent[2] >= extent[3]
        ):
            raise ValueError(
                "station_elevations extent must be finite xmin < xmax, ymin < ymax"
            )
    result = {}
    for network, table, id_column in (
        ("forcing", forcing_stations, "id"),
        ("snow", snow_stations, "station_id"),
    ):
        if source not in {network, "both"}:
            continue
        if table is None or table.empty:
            raise ValueError(f"No {network} stations available for station_elevations")
        required = {id_column, "alt"} | ({"x", "y"} if extent is not None else set())
        missing = required - set(table.columns)
        if missing:
            raise ValueError(
                f"{network} station metadata missing columns: {', '.join(sorted(missing))}"
            )
        selected = table.copy()
        ids = selected[id_column].astype("string").str.strip()
        if ids.isna().any() or ids.eq("").any():
            raise ValueError(f"{network} station metadata contains missing IDs")
        duplicates = ids[ids.duplicated(keep=False)].unique().tolist()
        if duplicates:
            raise ValueError(
                f"Duplicate {network} station IDs: {', '.join(duplicates)}"
            )
        selected["station_id"] = ids
        if extent is not None:
            coordinates = selected[["x", "y"]].apply(pd.to_numeric, errors="coerce")
            invalid = ~np.isfinite(coordinates.to_numpy(dtype=float)).all(axis=1)
            if invalid.any():
                raise ValueError(
                    f"Invalid {network} station coordinates: {', '.join(ids[invalid])}"
                )
            inside = coordinates.x.between(
                extent[0], extent[1]
            ) & coordinates.y.between(extent[2], extent[3])
            selected = selected.loc[inside].copy()
        if selected.empty:
            raise ValueError(f"No {network} stations inside station_elevations extent")
        selected["alt"] = pd.to_numeric(selected["alt"], errors="coerce")
        invalid = ~np.isfinite(selected.alt.to_numpy(dtype=float))
        if invalid.any():
            bad_ids = ", ".join(selected.loc[invalid, "station_id"])
            raise ValueError(f"Invalid {network} station elevations: {bad_ids}")
        selected = selected.sort_values(
            ["alt", "station_id"], kind="stable"
        ).reset_index(drop=True)
        selected["rank"] = np.arange(1, len(selected) + 1)
        result[network] = selected
    return result


def draw_station_elevations(
    ax: Axes,
    networks: Mapping[str, pd.DataFrame],
    *,
    label_size: float = 8.0,
    tick_size: float = 7.0,
    show_grid: bool = True,
) -> tuple[Axes, ...]:
    """Draw equal-width sorted-bar groups with shared limits and separate counts.

    The host axes owns the panel title; child axes follow it during figure resizing.
    Returned child axes allow composition and inspection without saving a figure.
    """
    if not networks or set(networks) - {"forcing", "snow"}:
        raise ValueError("Expected nonempty forcing and/or snow elevation groups")
    if any(table.empty for table in networks.values()):
        raise ValueError("Cannot draw an empty station elevation group")
    lower = min(0.0, min(float(table.alt.min()) for table in networks.values()))
    upper = max(1.0, max(float(table.alt.max()) for table in networks.values()))
    span = upper - lower
    ax.set_axis_off()
    groups = [name for name in ("forcing", "snow") if name in networks]
    # Reserve space inside this panel for its numeric y ticks and axis label;
    # neighboring map columns do not reserve room for a chart's decorations.
    left = 0.23
    gap = 0.14 if len(groups) == 2 else 0.0
    width = (0.98 - left - gap) / len(groups)
    children = []
    for index, name in enumerate(groups):
        table = networks[name]
        child = ax.inset_axes([left + index * (width + gap), 0.0, width, 0.9])
        color = FORCING_STATION_COLOR if name == "forcing" else SNOW_STATION_COLOR
        child.bar(
            table["rank"],
            table.alt,
            width=0.85,
            color=color,
            linewidth=0,
            rasterized=True,
        )
        child.set_xlim(0.5, len(table) + 0.5)
        child.set_ylim(lower, upper + 0.06 * span)
        child.set_title(f"{name.title()} (n={len(table)})", fontsize=tick_size, pad=3)
        ticks = sorted({1, max(1, round(len(table) / 2)), len(table)})
        child.set_xticks(ticks)
        child.yaxis.set_major_locator(MaxNLocator(nbins=4, integer=True))
        child.tick_params(labelsize=tick_size, length=2, pad=2, labelleft=index == 0)
        if index == 0:
            child.set_ylabel("Elevation [m]", fontsize=label_size, labelpad=3)
        if show_grid:
            child.set_axisbelow(True)
            child.grid(axis="y", linestyle=GRID_LS, linewidth=GRID_LW, alpha=GRID_ALPHA)
        for spine in child.spines.values():
            spine.set_linewidth(0.7)
        children.append(child)
    ax.text(
        (left + 0.98) / 2,
        -0.17,
        "Station count",
        transform=ax.transAxes,
        ha="center",
        va="top",
        fontsize=label_size,
    )
    return tuple(children)
