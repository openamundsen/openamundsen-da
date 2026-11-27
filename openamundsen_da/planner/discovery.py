"""Discover dated schedules within declared planning periods."""

from __future__ import annotations

from collections import defaultdict
from datetime import timedelta
from pathlib import Path
from statistics import mean

from .layout import read_period, table_path
from .tables import read_rows, read_schedule
from .timeline import TimelinePanel, plot_timelines


def _read_panel(directory: Path) -> TimelinePanel | None:
    period = read_period(directory)
    schedules = {}
    for path in sorted((directory / "schedules").glob("*")):
        if not path.is_file() or path.suffix.lower() not in {".yml", ".yaml", ".csv"}:
            continue
        label = path.stem[:1].upper() + path.stem[1:]
        if label in schedules:
            raise ValueError(f"{directory}: duplicate schedule files for {label!r}")
        _, schedules[label] = read_schedule(path)
    if not schedules:
        return None
    withheld = table_path(directory, "withheld", required=False)
    return TimelinePanel(
        period.name, schedules, read_rows(withheld) if withheld else None,
        bounds=(period.start, period.end + timedelta(days=1)),
    )


def plot_periods(directory: Path, output: Path, *, single_period: bool, title: str | None) -> None:
    """Render available schedules, preserving manual edits and declared calendar bounds."""
    directories = [directory] if single_period else sorted(
        path for path in (directory / "periods").glob("*")
        if path.is_dir() and ((path / "period.yml").exists() or (path / "schedules").is_dir())
    )
    panels = []
    lengths = defaultdict(list)
    for path in directories:
        panel = _read_panel(path)
        if panel is not None:
            panels.append(panel)
            for label, rows in panel.schedules.items():
                lengths[label].append(len(rows))
    if not panels:
        raise ValueError(f"{directory}: no dated schedules found in declared planning periods")
    panels.sort(key=lambda panel: panel.bounds[0])
    order = sorted(lengths, key=lambda label: (mean(lengths[label]), label))
    for panel in panels:
        panel.schedules = {label: panel.schedules[label] for label in order if label in panel.schedules}
    if not title:
        title = "Assimilation schedules"
        if any(panel.withheld is not None for panel in panels):
            title += " and withheld observations"
        if len(panels) == 1:
            title += f", {panels[0].name}"
    plot_timelines(panels, output, title=title, columns=min(2, len(panels)))
