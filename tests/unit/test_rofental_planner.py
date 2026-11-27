"""Replay the four shipped winter selections without opening the external raster archive."""

from pathlib import Path

import pytest

from openamundsen_da.planner.holdouts import generate_holdouts
from openamundsen_da.planner.selection import compile_rules, generate_schedule, matches_holdout
from openamundsen_da.planner.tables import read_rows, write_rows

PLANNER = Path(__file__).resolve().parents[2] / "examples" / "rofental" / "planner"


@pytest.mark.parametrize(("year", "reserved", "expected"), [
    (2019, 6, (14, 23, 43)),
    (2020, 6, (14, 25, 47)),
    (2021, 5, (15, 24, 45)),
    (2022, 6, (14, 26, 42)),
])
def test_reviewed_winter_is_reproducible(tmp_path, year, reserved, expected):
    winter = PLANNER / f"{year}_{year + 1}"
    inventory = read_rows(winter / "inventory.yml")
    rules = compile_rules(read_rows(PLANNER / "rules.yml"), year)
    held = generate_holdouts(inventory, rules)
    assert len(held) == reserved
    path = tmp_path / "held.yml"
    write_rows(path, held)
    assert read_rows(path) == read_rows(winter / "withheld.yml")

    for cadence, count in zip(("monthly", "14-day", "7-day"), expected, strict=True):
        rows = generate_schedule(inventory, rules, cadence, held)
        path = tmp_path / f"{cadence}.yml"
        write_rows(path, rows)
        assert read_rows(path) == read_rows(winter / path.name)
        updates = [row for row in rows if row["date"]]
        assert len(updates) == count
        assert not any(matches_holdout(row, excluded) for row in updates for excluded in held)


def test_four_panel_timeline_matches_reviewed_winter_plots(monkeypatch):
    import runpy
    from datetime import date

    from matplotlib.dates import num2date

    from openamundsen_da.methods.viz import common
    from openamundsen_da.methods.viz.theme import FIGWIDTH_OVERVIEW_PAPER

    single_rows = {}
    combined_titles = []

    def inspect(fig, output):
        assert fig.get_figwidth() == pytest.approx(FIGWIDTH_OVERVIEW_PAPER)
        if output.parent != PLANNER:
            axis, = fig.axes
            single_rows[output.parent.name] = [label.get_text() for label in axis.get_yticklabels()]
            return
        assert output == PLANNER / "timeline.png"
        assert fig.get_figheight() < 4.5
        assert len(fig.legends) == 1
        for year, axis in enumerate(fig.axes, 2019):
            spec = axis.get_subplotspec()
            assert spec.get_geometry()[:2] == (2, 2)
            assert (spec.rowspan.start, spec.colspan.start) == divmod(year - 2019, 2)
            combined_titles.append(axis.get_title(loc="left"))
            assert [num2date(value).date() for value in axis.get_xlim()] == [
                date(year, 10, 1), date(year + 1, 7, 1)
            ]
            assert [label.get_text() for label in axis.get_yticklabels()] == single_rows[f"{year}_{year + 1}"]
        assert "Parentheses" in fig.legends[0].get_title().get_text()

    monkeypatch.setattr(common, "save_figure_png", inspect)
    runpy.run_path(str(PLANNER / "plot_timelines.py"))["render"](PLANNER)
    assert combined_titles == ["(a) 2019/20", "(b) 2020/21", "(c) 2021/22", "(d) 2022/23"]
