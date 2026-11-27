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
    inventory = read_rows(winter / "inventory.csv")
    rules = compile_rules(read_rows(PLANNER / "rules.csv"), year)
    held = generate_holdouts(inventory, rules)
    assert len(held) == reserved
    path = tmp_path / "held.csv"
    write_rows(path, held)
    assert read_rows(path) == read_rows(winter / "withheld.csv")

    for cadence, count in zip(("monthly", "14-day", "7-day"), expected, strict=True):
        rows = generate_schedule(inventory, rules, cadence, held)
        path = tmp_path / f"{cadence}.csv"
        write_rows(path, rows)
        assert read_rows(path) == read_rows(winter / path.name)
        updates = [row for row in rows if row["date"]]
        assert len(updates) == count
        assert not any(matches_holdout(row, excluded) for row in updates for excluded in held)
