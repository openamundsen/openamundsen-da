"""Discover dated schedules within declared planning periods."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from statistics import mean

from .layout import read_period, table_path
from .tables import read_rows, read_schedule
from .timeline import TimelinePanel, plot_timelines


def read_plot_schedule(path: Path):
    """Combine current assignments with the target grid recorded during generation."""
    label, rows = read_schedule(path)
    stem = f"{path.stem}_targets"
    directories = [path.parent]
    if path.parent.name == "schedules":
        directories.append(path.parent.parent / "reports")
    reports = []
    for directory in directories:
        matching = directory / f"{stem}{path.suffix}"
        candidate = matching if matching.is_file() else table_path(directory, stem, required=False)
        if candidate:
            reports.append(candidate)
    if len(reports) > 1:
        raise ValueError(f"{path}: found two target reports: {reports[0]} and {reports[1]}")
    report = reports[0] if reports else None
    if report is None or any(not row.get("date") and row.get("target_date") for row in rows):
        return label, rows

    targets = {}
    original = {}
    for entry in read_rows(report):
        variable = entry.get("variable")
        target = entry.get("target_date")
        if not variable or not target or (variable, target) in targets:
            raise ValueError(f"{report}: expected distinct variable and target_date for each target")
        key = (variable, target)
        try:
            target_day = date.fromisoformat(target)
            tolerance = int(entry.get("tolerance_days", ""))
            if tolerance < 0:
                raise ValueError
        except ValueError as exc:
            raise ValueError(f"{report}: expected a target date and nonnegative tolerance_days for {key}") from exc
        targets[key] = (target_day, tolerance)
        if entry.get("date"):
            original[variable, entry["date"]] = key

    # Retain known target assignments before matching newly edited dates to open targets.
    assigned = {}
    edited = []
    for row in rows:
        variable, day = row.get("variable"), row.get("date")
        key = (variable, row["target_date"]) if row.get("target_date") else original.get((variable, day))
        if key in targets and key not in assigned:
            assigned[key] = row
        else:
            edited.append(row)

    extra = []
    for row in edited:
        try:
            day = date.fromisoformat(row.get("date", ""))
        except ValueError as exc:
            raise ValueError(f"{path}: expected an assigned date for {row.get('variable', '')}") from exc
        nearby = []
        for key, (target_day, tolerance) in targets.items():
            if key in assigned or key[0] != row.get("variable") or row.get("target_date"):
                continue
            distance = abs((day - target_day).days)
            if distance <= tolerance:
                nearby.append((distance, key))
        nearby.sort()
        if nearby and (len(nearby) == 1 or nearby[0][0] < nearby[1][0]):
            assigned[nearby[0][1]] = row
        else:
            extra.append(row)

    plotted = [
        assigned.get(key, {"variable": key[0], "target_date": key[1]}) for key in targets
    ]
    return label, plotted + extra


def _read_panel(directory: Path) -> TimelinePanel | None:
    period = read_period(directory)
    schedules = {}
    for path in sorted((directory / "schedules").glob("*")):
        if not path.is_file() or path.suffix.lower() not in {".yml", ".yaml", ".csv"}:
            continue
        label = path.stem[:1].upper() + path.stem[1:]
        if label in schedules:
            raise ValueError(f"{directory}: duplicate schedule files for {label!r}")
        _, schedules[label] = read_plot_schedule(path)
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
