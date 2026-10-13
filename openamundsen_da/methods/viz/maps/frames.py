"""Opt-in physical frames for mixed geographic and chart recipes."""

from __future__ import annotations


def configure_frames(fig, recipe, row_extents):
    """Size the canvas from the first detail map, preserving its data aspect.

    Gaps reserve physical room for map colorbars, chart labels and panel titles.
    Existing native layouts never call this helper.
    """
    reference = next(
        (
            p
            for p in recipe.panels
            if p.kind not in {"overview", "station_elevations", "legend", "colorbar"}
        ),
        None,
    )
    if reference is None:
        raise ValueError("Frame alignment requires a geographic detail panel")
    widths = recipe.layout.width_ratios or (1.0,) * recipe.layout.ncols
    heights = recipe.layout.height_ratios or (1.0,) * recipe.layout.nrows
    width = recipe.layout.width_cm / 2.54
    left, right, bottom, top = 0.46, 0.13, 0.43, 0.34
    column_gap, row_gap = recipe.layout.column_gap_in, 0.62
    unit_width = (width - left - right - column_gap * (len(widths) - 1)) / sum(widths)
    reference_width = unit_width * sum(
        widths[reference.col : reference.col + reference.colspan]
    ) + column_gap * (reference.colspan - 1)
    xmin, xmax, ymin, ymax = row_extents[reference.row]
    reference_height = reference_width * (ymax - ymin) / (xmax - xmin)
    unit_height = (reference_height - row_gap * (reference.rowspan - 1)) / sum(
        heights[reference.row : reference.row + reference.rowspan]
    )
    if min(unit_width, unit_height) <= 0:
        raise ValueError("Frame width is too small for the requested layout")
    height = bottom + top + unit_height * sum(heights) + row_gap * (len(heights) - 1)
    fig.set_size_inches(width, height, forward=True)
    positions = []
    for panel in recipe.panels:
        x = left + unit_width * sum(widths[: panel.col]) + column_gap * panel.col
        panel_top = (
            height - top - unit_height * sum(heights[: panel.row]) - row_gap * panel.row
        )
        w = unit_width * sum(
            widths[panel.col : panel.col + panel.colspan]
        ) + column_gap * (panel.colspan - 1)
        h = unit_height * sum(
            heights[panel.row : panel.row + panel.rowspan]
        ) + row_gap * (panel.rowspan - 1)
        positions.append((x / width, (panel_top - h) / height, w / width, h / height))
    return positions


def align_bottom_annotations(fig, axes):
    """Align count subtitles with horizontal colorbars on matching frame bottoms."""
    fig.canvas.draw()
    bars = [
        (ax, child)
        for ax in axes
        for child in ax.child_axes
        if child.get_gid() == "oa_da_horizontal_colorbar"
    ]
    for ax in axes:
        for label in ax.texts:
            if label.get_gid() != "oa_da_station_count":
                continue
            for parent, bar in bars:
                if abs(parent.get_position().y0 - ax.get_position().y0) > 1e-6:
                    continue
                center = bar.get_position().y0 + bar.get_position().height / 2
                label.set_y((center - ax.get_position().y0) / ax.get_position().height)
                label.set_verticalalignment("center")
                break
