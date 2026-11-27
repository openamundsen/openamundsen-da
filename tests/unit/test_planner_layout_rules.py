"""Planning folders resolve one period consistently for selection and project loading."""

from datetime import date
from pathlib import Path
from shutil import copytree

import pytest
from ruamel.yaml import YAML

from openamundsen_da.configuration import load_project_configuration
from openamundsen_da.planner.layout import load_evaluation_rules, load_schedule_rules, read_period, schedule_rules_path, table_path
from openamundsen_da.planner.tables import read_rows, write_rows
from openamundsen_da.planner.validation import validate_schedule


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as stream:
        YAML(typ="safe").dump(value, stream)


def _document():
    return {
        "defaults": {"frequency": "monthly", "reschedule": True, "tolerance_days": 4},
        "observations": {
            "scf": {"priority": 2, "min_support_coverage_ratio": 0.6},
            "station_hs": {
                "priority": 1, "tolerance_days": 2, "min_station_count": 2,
                "intervals": [
                    {"period_start": "0-11-01", "period_end": "0-12-31"},
                    {"period_start": "1-01-01", "period_end": "1-02-last", "tolerance_days": 1, "frequency": 7},
                ],
            },
        },
    }


def test_period_and_schedule_rules_resolve_defaults_and_interval_overrides(tmp_path):
    directory = tmp_path / "periods" / "acquisitions"
    _write(directory / "period.yml", {"start": "2023-10-01", "end": "2024-06-30"})
    period = read_period(directory)
    assert (period.name, period.start, period.end) == ("acquisitions", date(2023, 10, 1), date(2024, 6, 30))
    path = schedule_rules_path(directory, "mixed")
    _write(path, _document())
    scf, autumn, winter = load_schedule_rules(path, 2023, "mixed", period=period)
    assert (scf.start, scf.end, scf.frequency, scf.tolerance_days) == (period.start, period.end, "monthly", 4)
    assert (autumn.frequency, autumn.tolerance_days) == ("monthly", 2)
    assert (winter.start, winter.end, winter.frequency, winter.tolerance_days) == (date(2024, 1, 1), date(2024, 2, 29), 7, 1)
    assert winter.limits == autumn.limits == {"min_station_count": 2}
    assert {rule.schedule for rule in (winter, autumn, scf)} == {"mixed"}


def test_period_rule_bounds_and_ambiguous_files_fail_before_selection(tmp_path):
    directory = tmp_path / "periods" / "spring"
    _write(directory / "period.yml", {"label": "Spring study", "start": "2023-03-15", "end": "2023-06-30"})
    period = read_period(directory)
    assert period.name == "Spring study"
    path = schedule_rules_path(directory, "mixed")
    doc = _document()
    _write(path, doc)
    with pytest.raises(ValueError, match="outside planning period"):
        load_schedule_rules(path, 2023, "mixed", period=period)
    doc["observations"]["station_hs"]["intervals"] = []
    _write(path, doc)
    with pytest.raises(ValueError, match="intervals"):
        load_schedule_rules(path, 2023, "mixed", period=period)
    eval_path = tmp_path / "evaluation.yml"
    _write(eval_path, [{"purpose": "evaluation", "variable": "scf", "count": 1, "strategy": "best",
                        "period_start": "2023-03-14", "period_end": "2023-03-18"}])
    with pytest.raises(ValueError, match="outside planning period"):
        load_evaluation_rules(eval_path, period)
    for name in ("inventory.yml", "inventory.csv"):
        (directory / name).touch()
    with pytest.raises(ValueError, match="multiple tables"):
        table_path(directory, "inventory")
    assert table_path(directory, "withheld", required=False) is None
    with pytest.raises(ValueError, match="single directory"):
        schedule_rules_path(directory, "../escape")


def test_schedule_rules_support_the_previous_folder_layout(tmp_path):
    period = tmp_path / "periods" / "winter"
    flat = tmp_path / "schedules" / "weekly.yml"
    nested = flat.parent / "weekly" / "rules.yml"
    assert schedule_rules_path(period, "weekly") == flat
    _write(nested, _document())
    assert schedule_rules_path(period, "weekly") == nested
    _write(flat, _document())
    with pytest.raises(ValueError, match="Multiple rule files"):
        schedule_rules_path(period, "weekly")


def test_folder_project_validates_reviewed_inputs_and_tracks_every_source(tmp_path):
    source = Path(__file__).resolve().parents[2] / "examples" / "rofental"
    setup = tmp_path / "rofental"
    copytree(source, setup)
    project = setup / "projects" / "project_2022_2023"
    period = setup / "planner" / "periods" / "2022_2023"
    schedule = period / "schedules" / "monthly.yml"
    config_path = project / f"{project.name}.yml"
    config = YAML(typ="safe").load(config_path)
    config["data_assimilation"]["assimilation_events"] = "../../planner/periods/2022_2023/schedules/monthly.yml"
    config["data_assimilation"]["observation_planner"] = {"period": "../../planner/periods/2022_2023", "schedule": "monthly"}
    _write(config_path, config)
    prepared = load_project_configuration(project)
    assert set(prepared.planner_inputs) == {
        schedule, period / "inventory.csv", period / "period.yml", period / "withheld.yml",
        setup / "planner" / "evaluation.yml", schedule_rules_path(period, "monthly"),
    }
    report = validate_schedule(project, period=period, schedule_name="monthly")
    assert not report.errors
    assert len(report.events) == 14
    # The read-only report still lists the removed update; only the editable
    # schedule determines which observations enter this project.
    schedule_rows = read_rows(schedule)
    removed, *retained = schedule_rows
    write_rows(schedule, retained, overwrite=True)
    report = validate_schedule(project)
    assert not report.errors
    assert [row["date"] for row in report.events] == [row["date"] for row in retained]
    assert removed["date"] not in {row["date"] for row in report.events}
    write_rows(schedule, schedule_rows, overwrite=True)
    report = validate_schedule(project, period=period, schedule_name="monthly", withheld=[])
    assert any("require the reviewed withheld table" in error for error in report.errors)
    # A standalone period takes precedence over references from a different project plan.
    config["data_assimilation"]["observation_planner"].update(
        rules="old/rules.yml", inventory="old/inventory.yml", withheld="old/withheld.yml", year=1990
    )
    _write(config_path, config)
    report = validate_schedule(project, period=period, schedule_name="monthly")
    assert not report.errors
    assert len(report.events) == 14
    (period / "withheld.yml").unlink()
    assert any("missing withheld" in error for error in validate_schedule(project, period=period, schedule_name="monthly").errors)


def test_evaluation_rules_use_their_own_limits(tmp_path):
    # Full coverage required for assimilation would disqualify the evaluation acquisition.
    source = Path(__file__).resolve().parents[2] / "examples" / "rofental"
    project = source / "projects" / "project_2022_2023"
    period = tmp_path / "periods" / "short"
    _write(period / "period.yml", {"start": "2022-10-03", "end": "2022-10-08"})
    _write(schedule_rules_path(period, "strict"), {
        "defaults": {"frequency": "monthly", "tolerance_days": 0, "reschedule": True},
        "observations": {"scf": {"priority": 1, "min_support_coverage_ratio": 1}},
    })
    _write(tmp_path / "evaluation.yml", [{"purpose": "evaluation", "variable": "scf", "count": 1,
                                          "strategy": "best", "period_start": "0-10-03", "period_end": "0-10-08"}])
    write_rows(period / "withheld.yml", [{"variable": "scf", "date": "2022-10-08"}])
    write_rows(period / "schedules" / "strict.yml", [{"variable": "scf", "date": "", "target_date": "2022-10-03"}])
    from openamundsen_da.planner.inventory import build_inventory

    inventory = build_inventory(project, "2022-10-03", "2022-10-08", ["scf"])
    assert float(next(row for row in inventory if row["date"] == "2022-10-08")["support_coverage_ratio"]) < 1
    write_rows(period / "inventory.yml", inventory)
    report = validate_schedule(project, period=period, schedule_name="strict")
    assert not report.errors

def test_shared_evaluation_rules_can_skip_a_year_without_hiding_malformed_rows(tmp_path):
    _write(tmp_path / "period.yml", {"start": "2023-01-01", "end": "2023-12-31"})
    period = read_period(tmp_path)
    path = tmp_path / "evaluation.yml"
    row = {"purpose": "evaluation", "year": 2022, "variable": "scf", "count": 1, "strategy": "best",
           "period_end": "0-12-31"}
    _write(path, [row])
    assert load_evaluation_rules(path, period) == []
    _write(path, [{**row, "strategy": "unknown"}])
    with pytest.raises(ValueError, match="strategy"):
        load_evaluation_rules(path, period)
    _write(path, [])
    with pytest.raises(ValueError, match="at least one evaluation rule"):
        load_evaluation_rules(path, period)
