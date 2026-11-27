"""Render the four Rofental winters and their paper-sized comparison from reviewed YAML."""

import argparse
from datetime import date
from pathlib import Path

from openamundsen_da.planner.tables import read_rows
from openamundsen_da.planner.timeline import TimelinePanel, plot_timelines


def render(root: Path) -> None:
    title = "Rofental assimilation schedules and withheld observations"
    panels = []
    for year in range(2019, 2023):
        directory = root / f"{year}_{year + 1}"
        winter = f"{year}/{str(year + 1)[-2:]}"
        schedules = {
            label: read_rows(directory / f"{label.lower()}.yml")
            for label in ("Monthly", "14-day", "7-day")
        }
        panel = TimelinePanel(
            winter, schedules, read_rows(directory / "withheld.yml"),
            bounds=(date(year, 10, 1), date(year + 1, 7, 1)),
        )
        panels.append(panel)
        plot_timelines([panel], directory / "timeline.png", title=f"{title}, {winter}")
    plot_timelines(panels, root / "timeline.png", title=title, columns=2)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", nargs="?", type=Path, default=Path(__file__).resolve().parent,
                        help="Planner directory containing the four winter directories")
    render(parser.parse_args().directory)
