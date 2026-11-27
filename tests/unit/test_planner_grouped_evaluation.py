"""Grouped evaluation rules preserve the flat table's calendar and selection semantics."""

from datetime import date
from pathlib import Path

import pytest
from ruamel.yaml import YAML

from openamundsen_da.cli import main
from openamundsen_da.planner.layout import PlanningPeriod, load_evaluation_rules
from openamundsen_da.planner.tables import write_rows


def _period(year):
    return PlanningPeriod(str(year), date(year, 10, 1), date(year + 1, 6, 30))


def _grouped(tmp_path, evaluation):
    path = tmp_path / "evaluation.yml"
    with path.open("w") as stream:
        YAML(typ="safe").dump({"evaluation": evaluation}, stream)
    return path


@pytest.mark.parametrize("suffix", ["yml", "csv"])
def test_grouped_rule_precedence_matches_flat_evaluation(tmp_path, suffix):
    path = _grouped(tmp_path, {
        "defaults": {"strategy": "best", "min_spacing_days": 14, "min_support_coverage_ratio": 0.1},
        "scf": {
            "count": 1,
            "min_spacing_days": 10,
            "intervals": [
                {"period_end": "0-12-31"},
                {"period_start": "1-04-01", "strategy": "best_per_month", "count": 3, "min_spacing_days": 5},
            ],
        },
        "wet_snow_line": {"strategy": "spread", "count_by_year": {2019: 2, "2021": 1}},
    })
    base = {"purpose": "evaluation", "min_support_coverage_ratio": 0.1}
    legacy = tmp_path / f"flat.{suffix}"
    write_rows(legacy, [
        dict(base, variable="scf", strategy="best", min_spacing_days=10, period_end="0-12-31", count=1),
        dict(base, variable="scf", strategy="best_per_month", min_spacing_days=5, period_start="1-04-01", count=3),
        dict(base, variable="wet_snow_line", strategy="spread", min_spacing_days=14, year=2019, count=2),
        dict(base, variable="wet_snow_line", strategy="spread", min_spacing_days=14, year=2021, count=1),
    ])
    for year in (2019, 2020, 2021, 2022):
        period = _period(year)
        assert load_evaluation_rules(path, period) == load_evaluation_rules(legacy, period)


@pytest.mark.parametrize(("evaluation", "message"), [
    ({"defaults": {}}, "at least one variable"),
    ({"scf": {"intervals": []}}, "nonempty sequence"),
    ({"scf": {"intervals": ["best"]}}, "expected a mapping"),
    ({"scf": {"purpose": "assimilation"}}, "unexpected evaluation field"),
    ({"scf": {"year": 2019, "count_by_year": {2020: 1}}}, "cannot be combined"),
    ({"defaults": {"count": 2}, "scf": {"count_by_year": {2020: 1}}}, "cannot be combined"),
    ({"scf": {"count_by_year": {2019: 1, "2019": 1}}}, "duplicate year"),
    ({"scf": {"count_by_year": {"2019/20": 1}}}, "invalid calendar year"),
    ({"scf": {"count_by_year": {}}}, "nonempty mapping"),
    ({"scf": {"strategy": "spread", "count_by_year": {2019: 2, 2020: -1}}}, "count must be"),
    ({"scf": {"strategy": "invalid", "count_by_year": {2020: 2}}}, "strategy"),
    ({"scf": {"strategy": "best", "period_end": "1-02-29", "count_by_year": {2019: 1, 2020: 1}}}, "period_end"),
    ({"scf": {"strategy": "best", "count_by_year": {2020: 2}, "tolerance_days": 4}}, "unexpected evaluation field"),
])
def test_invalid_grouped_evaluation_fails_even_for_other_start_years(tmp_path, evaluation, message):
    with pytest.raises(ValueError, match=message):
        load_evaluation_rules(_grouped(tmp_path, evaluation), _period(2019))


def test_explicit_year_and_inventory_can_use_the_grouped_rofental_evaluation(tmp_path):
    planner = Path(__file__).resolve().parents[2] / "examples" / "rofental" / "planner"
    inputs = planner / "periods" / "2021_2022"
    output = tmp_path / "withheld.yml"
    assert main([
        "planner", "holdouts", "--rules", str(planner / "evaluation.yml"), "--year", "2021",
        "--inventory", str(inputs / "inventory.yml"), "--output", str(output),
    ]) == 0
    assert output.read_bytes() == (inputs / "withheld.yml").read_bytes()
