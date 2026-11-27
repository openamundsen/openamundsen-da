"""Plot editable observation schedules and common evaluation acquisitions."""

from __future__ import annotations

import calendar
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from string import ascii_lowercase

from .selection import Row


@dataclass
class TimelinePanel:
    """Schedules and withheld observations for one planning period."""

    name: str
    schedules: dict[str, list[Row]]
    withheld: list[Row] | None
    bounds: tuple[date, date] | None = None


def _panel_data(panel: TimelinePanel):
    if not panel.schedules:
        raise ValueError("supply at least one schedule to plot")
    groups = defaultdict(list)
    labels = []
    series = list(panel.schedules.items())
    if panel.withheld is not None:
        series.append(("Withheld", panel.withheld))
    for i, (name, rows) in enumerate(series):
        count = 0
        for number, row in enumerate(rows, 1):
            variable = str(row.get("variable") or "").strip()
            actual = row.get("date")
            day = actual or row.get("target_date")
            if not day or not variable or (i == len(panel.schedules) and not actual):
                raise ValueError(f"timeline row {number} in {name} requires a date and variable")
            count += bool(actual)
            plotted_day = date.fromisoformat(str(day))
            if panel.bounds and not panel.bounds[0] <= plotted_day < panel.bounds[1]:
                raise ValueError(f"timeline row {number} in {name}: {plotted_day} outside {panel.name}")
            groups[(i, variable, bool(actual))].append(plotted_day)
        labels.append(f"{name} ({count})")
    return groups, labels


def plot_timeline(
    schedules: dict[str, list[Row]], holdouts: list[Row], output: Path, *, title: str | None = None
) -> None:
    """Render a single period from the supplied editable schedule rows."""
    plot_timelines([TimelinePanel("", schedules, holdouts)], output, title=title)


def plot_timelines(
    panels: list[TimelinePanel], output: Path, *, title: str | None = None, columns: int = 1
) -> None:
    """Arrange planning periods in row-major order with a shared variable key and count caption."""
    import matplotlib.pyplot as plt
    from matplotlib.dates import AutoDateLocator, ConciseDateFormatter
    from matplotlib.lines import Line2D

    from openamundsen_da.benchmark.render.common import VARIABLE_STYLES, variable_label
    from openamundsen_da.methods.viz.common import save_figure_png, set_matplotlib_text_black
    from openamundsen_da.methods.viz.plots.common import apply_month_interval_axis_labels
    from openamundsen_da.methods.viz.plots.theme import GRID_ALPHA, GRID_LS, GRID_LW
    from openamundsen_da.methods.viz.theme import (
        DA_VARIABLE_STYLES,
        FIGHEIGHT_OVERVIEW_ROW,
        FIGWIDTH_OVERVIEW_PAPER,
        canonical_da_variable,
    )

    if not panels:
        raise ValueError("supply at least one timeline panel")
    if Path(output).suffix.lower() != ".png":
        raise ValueError("timeline output must have a .png extension")
    if columns < 1 or columns > len(panels):
        raise ValueError("columns must be between one and the number of panels")
    data = [_panel_data(panel) for panel in panels]
    variables = sorted({var for groups, _ in data for _, var, _ in groups})
    colors = {}
    offsets = {}
    for i, variable in enumerate(variables):
        offsets[variable] = 0.0 if len(variables) == 1 else 0.42 * (i / (len(variables) - 1) - 0.5)
        style = DA_VARIABLE_STYLES.get(canonical_da_variable(variable))
        colors[variable] = style["line"] if style else f"C{i % 10}"

    combined = len(panels) > 1
    compact = columns > 1
    row_scale = 0.32 if compact else 0.48
    heights = [
        max(FIGHEIGHT_OVERVIEW_ROW, row_scale * max(len(labels) for _, labels in data[i:i + columns]))
        for i in range(0, len(data), columns)
    ]
    height = sum(heights) + (0.65 if compact else 1.35)
    with plt.rc_context():
        set_matplotlib_text_black(plt.matplotlib)
        fig, axes = plt.subplots(
            len(heights), columns, squeeze=False, height_ratios=heights,
            figsize=(FIGWIDTH_OVERVIEW_PAPER, height), layout="constrained",
        )
        try:
            for unused in axes.flat[len(panels):]:
                fig.delaxes(unused)
            for index, (panel, (groups, labels)) in enumerate(zip(panels, data, strict=True)):
                ax = axes.flat[index]
                for (i, variable, actual), dates in groups.items():
                    ax.scatter(
                        dates, [i + offsets[variable]] * len(dates), color=colors[variable],
                        marker="o" if actual else "x", s=8 if compact else 19,
                        linewidths=0.8 if compact else 1.1, zorder=3, clip_on=False,
                    )
                ax.set_yticks(range(len(labels)), labels)
                ax.set_ylim(len(labels) - 0.5, -0.5)
                ax.grid(True, linestyle=GRID_LS, linewidth=GRID_LW, alpha=GRID_ALPHA)
                bounds = panel.bounds
                if groups and bounds is None:
                    days = [day for dates in groups.values() for day in dates]
                    first, last = min(days), max(days)
                    next_month = last.replace(day=calendar.monthrange(last.year, last.month)[1]) + timedelta(days=1)
                    bounds = (first.replace(day=1), next_month)
                if bounds and panel.bounds and (bounds[1] - bounds[0]).days < 28:
                    locator = AutoDateLocator(minticks=2, maxticks=5)
                    ax.xaxis.set_major_locator(locator)
                    ax.xaxis.set_major_formatter(ConciseDateFormatter(locator))
                    ax.set_xlim(*bounds)
                    ax.tick_params(axis="x", labelsize=7 if compact else 8.4)
                elif bounds:
                    apply_month_interval_axis_labels(
                        ax, bounds, include_year_on_first=not combined, labelsize=7 if compact else 8.4
                    )
                else:
                    ax.set_xticks([])
                ax.tick_params(axis="y", length=0, labelsize=7 if compact else 8)
                if combined:
                    letter = ascii_lowercase[index] if index < len(ascii_lowercase) else str(index + 1)
                    ax.set_title(f"({letter}) {panel.name}", loc="left", fontsize=9 if compact else 10)

            caption = "Parentheses give the number of scheduled updates or withheld observations. Crosses mark missed targets."
            if all(panel.withheld is None for panel in panels):
                caption = "Parentheses give the number of scheduled updates. Crosses mark missed targets."
            if not compact:
                axes[-1, 0].set_xlabel(caption, loc="left", fontsize=7)
            handles = [
                Line2D([], [], linestyle="", marker="o", color=colors[var], markersize=5,
                       label=variable_label(var) if var in VARIABLE_STYLES else var.replace("_", " "))
                for var in variables
            ]
            if any(not actual for groups, _ in data for _, _, actual in groups):
                handles.append(Line2D([], [], linestyle="", marker="x", color="black", label="Missed target"))
            if handles:
                options = dict(handles=handles, ncol=min(len(handles), 4), frameon=False, fontsize=8)
                if combined:
                    fig.legend(loc="outside lower center", title=caption if compact else None,
                               title_fontsize=7, **options)
                else:
                    axes[0, 0].legend(loc="lower center", bbox_to_anchor=(0.5, 1.01), **options)
            elif compact:
                fig.supxlabel(caption, fontsize=7)
            fig.suptitle(title or "Assimilation schedules and withheld observations", fontsize=11)
            Path(output).parent.mkdir(parents=True, exist_ok=True)
            save_figure_png(fig, output)
        finally:
            plt.close(fig)
