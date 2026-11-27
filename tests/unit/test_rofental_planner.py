"""Replay the four reviewed winters without opening the external raster archive."""

from datetime import date
from pathlib import Path
from shutil import copyfile, copytree

import pytest

from openamundsen_da.cli import main
from openamundsen_da.planner.selection import matches_holdout
from openamundsen_da.planner.tables import read_rows

PLANNER = Path(__file__).resolve().parents[2] / "examples" / "rofental" / "planner"
TITLE = "Rofental assimilation schedules and withheld observations"
CADENCES = ("monthly", "14-day", "7-day")
REVIEWED_COUNTS = [
    (2019, 6, (14, 23, 43)),
    (2020, 6, (14, 25, 47)),
    (2021, 5, (15, 24, 45)),
    (2022, 6, (14, 26, 42)),
]


@pytest.mark.parametrize(("year", "reserved", "expected"), REVIEWED_COUNTS)
def test_reviewed_winter_is_reproducible(tmp_path, monkeypatch, year, reserved, expected):
    period = Path("periods") / f"{year}_{year + 1}"
    reviewed = PLANNER / period
    monkeypatch.chdir(tmp_path)
    copytree(PLANNER / "schedules", "schedules")
    copyfile(PLANNER / "evaluation.yml", "evaluation.yml")
    period.mkdir(parents=True)
    for filename in ("period.yml", "inventory.yml"):
        copyfile(reviewed / filename, period / filename)

    assert main(["planner", "holdouts", "--period", str(period)]) == 0
    assert (period / "withheld.yml").read_bytes() == (reviewed / "withheld.yml").read_bytes()
    held = read_rows(period / "withheld.yml")
    assert len(held) == reserved
    for cadence, count in zip(CADENCES, expected, strict=True):
        assert main(["planner", "generate", "--period", str(period), "--schedule", cadence]) == 0
        table = Path("schedules") / f"{cadence}.yml"
        assert (period / table).read_bytes() == (reviewed / table).read_bytes()
        updates = [row for row in read_rows(period / table) if row["date"]]
        assert len(updates) == count
        assert not any(matches_holdout(row, excluded) for row in updates for excluded in held)

    reports = {
        f"{name}_{kind}.yml"
        for name in ("withheld", *CADENCES)
        for kind in ("summary", "exclusions")
    }
    assert {p.name for p in (period / "reports").iterdir()} == reports
    for filename in reports:
        report = Path("reports") / filename
        assert (period / report).read_bytes() == (reviewed / report).read_bytes()


def test_four_panel_timeline_matches_reviewed_winter_plots(monkeypatch):
    from matplotlib.dates import num2date

    from openamundsen_da.methods.viz import common
    from openamundsen_da.methods.viz.theme import FIGWIDTH_OVERVIEW_PAPER

    monkeypatch.chdir(PLANNER)
    single_rows = {}
    combined_titles = []

    def inspect(fig, output):
        assert fig.get_figwidth() == pytest.approx(FIGWIDTH_OVERVIEW_PAPER)
        output = Path(output).resolve()
        assert output.name == "timeline.png"
        if output.parent != PLANNER:
            axis, = fig.axes
            period = output.parent
            assert period.parent == PLANNER / "periods"
            year, next_year = map(int, period.name.split("_"))
            assert fig.get_suptitle() == f"{TITLE}, {year}/{next_year % 100:02d}"
            single_rows[year] = [label.get_text() for label in axis.get_yticklabels()]
            return
        assert fig.get_suptitle() == TITLE
        assert fig.get_figheight() < 4.5
        assert len(fig.axes) == 4
        assert len(fig.legends) == 1
        for (year, withheld, counts), axis in zip(REVIEWED_COUNTS, fig.axes, strict=True):
            spec = axis.get_subplotspec()
            assert spec.get_geometry()[:2] == (2, 2)
            assert (spec.rowspan.start, spec.colspan.start) == divmod(year - 2019, 2)
            combined_titles.append(axis.get_title(loc="left"))
            assert [num2date(value).date() for value in axis.get_xlim()] == [
                date(year, 10, 1), date(year + 1, 7, 1)
            ]
            labels = [label.get_text() for label in axis.get_yticklabels()]
            assert labels == single_rows[year]
            assert labels == [
                f"Monthly ({counts[0]})", f"14-day ({counts[1]})",
                f"7-day ({counts[2]})", f"Withheld ({withheld})",
            ]
        assert "Parentheses" in fig.legends[0].get_title().get_text()

    monkeypatch.setattr(common, "save_figure_png", inspect)
    for year, _, _ in REVIEWED_COUNTS:
        assert main([
            "planner", "plot", "--period", f"periods/{year}_{year + 1}",
            "--title", f"{TITLE}, {year}/{(year + 1) % 100:02d}",
        ]) == 0
    assert main(["planner", "plot", "--planner-dir", ".", "--title", TITLE]) == 0
    assert combined_titles == ["(a) 2019/20", "(b) 2020/21", "(c) 2021/22", "(d) 2022/23"]
