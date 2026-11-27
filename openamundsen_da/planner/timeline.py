"""Plot the editable observation schedules and common evaluation acquisitions."""

from __future__ import annotations

import calendar
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

from .selection import Row


def plot_timeline(
    schedules: dict[str, list[Row]], holdouts: list[Row], output: Path, *, title: str | None = None
) -> None:
    """Render the supplied schedule rows, including targets left empty by an editor."""
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D

    from openamundsen_da.benchmark.render.common import VARIABLE_STYLES, variable_label
    from openamundsen_da.methods.viz.common import save_figure_png, set_matplotlib_text_black
    from openamundsen_da.methods.viz.plots.common import apply_month_interval_axis_labels
    from openamundsen_da.methods.viz.plots.theme import GRID_ALPHA, GRID_LS, GRID_LW
    from openamundsen_da.methods.viz.theme import (
        DA_VARIABLE_STYLES,
        FIGWIDTH_OVERVIEW_PAPER,
        canonical_da_variable,
    )

    if not schedules:
        raise ValueError("supply at least one schedule to plot")
    if Path(output).suffix.lower() != ".png":
        raise ValueError("timeline output must have a .png extension")
    groups = defaultdict(list)
    variables = set()
    labels = []
    for i, (name, rows) in enumerate([*schedules.items(), ("Withheld", holdouts)]):
        is_withheld = i == len(schedules)
        count = 0
        for number, row in enumerate(rows, 1):
            variable = str(row.get("variable") or "").strip()
            actual = row.get("date")
            day = actual or row.get("target_date")
            if not day or not variable or (is_withheld and not actual):
                raise ValueError(
                    f"timeline row {number} in {name} requires a date and variable"
                )
            if actual:
                count += 1
            variables.add(variable)
            groups[(i, variable, bool(actual))].append(date.fromisoformat(str(day)))
        labels.append(f"{name} ({count})")
    colors = {}
    offsets = {}
    for i, variable in enumerate(sorted(variables)):
        offsets[variable] = 0.0 if len(variables) == 1 else 0.42 * (i / (len(variables) - 1) - 0.5)
        style = DA_VARIABLE_STYLES.get(canonical_da_variable(variable))
        colors[variable] = style["line"] if style else f"C{i % 10}"
    height = 1.35 + len(labels) * 0.48
    with plt.rc_context():
        set_matplotlib_text_black(plt.matplotlib)
        fig, ax = plt.subplots(figsize=(FIGWIDTH_OVERVIEW_PAPER, height), layout="constrained")
        try:
            for (i, variable, actual), dates in groups.items():
                ax.scatter(
                    dates,
                    [i + offsets[variable]] * len(dates),
                    color=colors[variable],
                    marker="o" if actual else "x",
                    s=19,
                    linewidths=1.1,
                    zorder=3,
                    clip_on=False,
                )
            ax.set_yticks(range(len(labels)), labels)
            ax.set_ylim(len(labels) - 0.5, -0.5)
            ax.grid(True, linestyle=GRID_LS, linewidth=GRID_LW, alpha=GRID_ALPHA)
            if groups:
                dates = [day for days in groups.values() for day in days]
                first, last = min(dates), max(dates)
                start = first.replace(day=1)
                end = last.replace(day=calendar.monthrange(last.year, last.month)[1]) + timedelta(days=1)
                apply_month_interval_axis_labels(ax, (start, end))
            else:
                ax.set_xticks([])
            ax.set_xlabel(
                "Parentheses give the number of scheduled updates or withheld observations. Crosses mark missed targets.",
                loc="left",
                fontsize=7,
            )
            ax.tick_params(axis="y", length=0, labelsize=8)
            handles = [
                Line2D(
                    [],
                    [],
                    linestyle="",
                    marker="o",
                    color=colors[var],
                    markersize=5,
                    label=variable_label(var) if var in VARIABLE_STYLES else var.replace("_", " "),
                )
                for var in sorted(variables)
            ]
            if any(not actual for _, _, actual in groups):
                handles.append(Line2D([], [], linestyle="", marker="x", color="black", label="Missed target"))
            if handles:
                ax.legend(
                    handles=handles,
                    loc="lower center",
                    bbox_to_anchor=(0.5, 1.01),
                    ncol=min(len(handles), 4),
                    frameon=False,
                    fontsize=8,
                )
            if title:
                fig.suptitle(title)
            Path(output).parent.mkdir(parents=True, exist_ok=True)
            save_figure_png(fig, output)
        finally:
            plt.close(fig)
