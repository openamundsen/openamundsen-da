"""Run-independent Observation Planner checks against the example's actual observations."""

from pathlib import Path
from shutil import copy2, copytree

import pytest
from ruamel.yaml import YAML

from openamundsen_da.api import prepare_project, run_project
from openamundsen_da.exceptions import ProjectRunError
from openamundsen_da.configuration import load_project_configuration
from openamundsen_da.pipeline.project_skeleton import plan_project_steps
from openamundsen_da.planner.inventory import build_inventory
from openamundsen_da.planner.tables import read_rows, write_rows
from openamundsen_da.planner.validation import validate_schedule
from openamundsen_da.util.da_events import load_assimilation_events


@pytest.fixture
def project(tmp_path):
    source = Path(__file__).resolve().parents[2] / "examples/rofental"
    setup = tmp_path / "rofental"
    directory = setup / "projects/project_2022_2023"
    copytree(source / "projects" / directory.name, directory)
    copy2(source / "rofental.yml", setup)
    for folder in ("env", "grids", "obs"):
        copytree(source / folder, setup / folder)
    return directory


def _project_table(project, rows, **options):
    path = project / f"{project.name}.yml"
    yaml = YAML()
    config = yaml.load(path)
    config["data_assimilation"]["assimilation_events"] = rows
    config["data_assimilation"]["observation_planner"] = options
    with path.open("w") as stream:
        yaml.dump(config, stream)


def _scf_rule(**settings):
    return dict(
        purpose="assimilation", variable="scf", period_start="2022-10-03", period_end="2022-10-10",
        schedule="monthly", frequency="monthly", tolerance_days=2, reschedule=True, priority=1, **settings
    )


def test_manual_inline_csv_yaml_enter_the_same_run_with_frozen_stations(project):
    rows = [
        {"variable": "scf", "date": "2022-10-03"},
        {"variable": "station_hs", "date": "2022-11-03", "station_ids": "Latschbloder"},
    ]
    snapshots = []
    for schedule in (rows, "selection.csv", "selection.yaml"):
        if isinstance(schedule, str):
            write_rows(project / schedule, rows)
        _project_table(project, schedule)
        config = load_project_configuration(project)
        if isinstance(schedule, str):
            assert config.planner_inputs == (project / schedule,)
        report = validate_schedule(project)
        assert not report.errors
        assert any("frequency and timing unassessed" in check for check in report.checks)
        events = load_assimilation_events(project)
        assert events[0].source_id == "s2_fsc_rofental_2022_10_03.tif"
        assert events[1].station_ids == ("latschbloder",)
        assert events[1].observation_time.date() < events[1].date  # UTC vs local midnight
        snapshots.append((events, plan_project_steps(project.parent.parent, project)))
    assert snapshots[0] == snapshots[1] == snapshots[2]


def test_manual_quality_is_remeasured_after_edit_even_for_timing_exceptions(project):
    shared = build_inventory(project, "2022-10-08", "2022-10-08", ["scf"])
    shared[0]["support_coverage_ratio"] = 1  # Deliberately optimistic inventory edit.
    schedule = [dict(variable="scf", date="2022-10-08", target_date="2022-10-03",
                     timing_exception_reason="closest clear acquisition after the outage")]
    report = validate_schedule(project, schedule=schedule, inventory=shared, rules=[_scf_rule(min_support_coverage_ratio=0.9)])
    assert any("row 1" in error and "fails min_support_coverage_ratio=0.9" in error for error in report.errors)
    assert any("+5 days" in warning and "closest clear" in warning for warning in report.warnings)


def test_unreadable_uncertainty_is_reported_for_every_row_and_repair_is_checked(project):
    dates = ["2022-10-03", "2022-10-05"]
    rows = [dict(variable="scf", date=day) for day in dates]
    shared = build_inventory(project, dates[0], dates[-1], ["scf"])
    files = [project.parent.parent / "obs/snowcover" / r["source_id"].replace(".tif", "_uncertainty.tif")
             for r in shared]
    contents = [path.read_bytes() for path in files]
    for path in files:
        path.write_text("damaged")
    report = validate_schedule(project, schedule=rows, inventory=shared)
    for idx, (day, path) in enumerate(zip(dates, files, strict=True), 1):
        assert any(f"row {idx}" in error and day in error and path.name in error for error in report.errors)
    for path, content in zip(files, contents, strict=True):
        path.write_bytes(content)
    assert not validate_schedule(project, schedule=rows, inventory=shared).errors


@pytest.mark.parametrize("name", ["monthly", "fortnightly", "weekly"])
def test_edited_shared_holdouts_report_constraints_and_leave_schedule_untouched(project, name):
    rules = [{**_scf_rule(), "schedule": name}]
    rows = [dict(variable="scf", date="2022-10-03", target_date="2022-10-03", schedule=name)]
    path = project / f"{name}.csv"
    write_rows(path, rows)
    withheld = [{"variable": "scf", "date": "2022-10-03"}]
    report = validate_schedule(project, schedule=path, withheld=withheld, rules=rules)
    assert any("withheld for evaluation; suggested replacement 2022-10-05" in error for error in report.errors)
    withheld.append({"variable": "scf", "date": "2022-10-05"})
    report = validate_schedule(project, schedule=path, withheld=withheld, rules=rules)
    assert any("missed target 2022-10-03; no eligible replacement" in error for error in report.errors)
    assert read_rows(path) == rows


def test_invalid_or_empty_manual_rows_identify_the_field_and_target(project):
    schedule = [{"variable": "scf", "date": "", "target_date": "2022-10-08"},
                {"variable": "scf", "date": "broken"},
                {"variable": "typo", "date": "2022-10-05"},
                {"variable": "station_hs", "date": "2022-11-03", "station_ids": "not-present"},
                {"variable": "station_hs", "date": "2022-11-03", "station_id": "misspelled-field"}]
    report = validate_schedule(project, schedule=schedule)
    assert any("missed target 2022-10-08" in warning for warning in report.warnings)
    for expected in ["row 2 date", "row 3 variable", "station_ids: unavailable: not-present", "unknown fields: station_id"]:
        assert any(expected in error for error in report.errors)


def test_validation_uses_the_runtime_propagation_window(project):
    row = dict(variable="scf", date="2023-06-30")
    report = validate_schedule(project, schedule=[row])
    assert any("propagation windows" in error and "row 1 date 2023-06-30" in error for error in report.errors)
    assert not (project / "steps").exists()

def test_first_run_detects_schedule_edits_after_preparation(project):
    path = project / "selection.csv"
    write_rows(path, [{"variable": "station_hs", "date": "2022-11-03", "station_ids": "latschbloder"}])
    _project_table(project, path.name)
    prepare_project(project)
    write_rows(path, [{"variable": "station_hs", "date": "2022-11-03", "station_ids": "proviantdepot"}], overwrite=True)
    with pytest.raises(ProjectRunError, match="Reviewed schedule or preparation inputs changed"):
        run_project(project)


def test_ambiguous_acquisition_lists_choices_and_checks_time_against_model_steps(project):
    setup = project.parent.parent
    summary = setup / "obs/summaries" / project.name / "scf_summary.csv"
    rows = read_rows(summary)
    scene = dict(rows[0], source="other.tif", acquisition_time="2022-10-03T09:30:00Z")
    scene_dir = setup / "obs/snowcover"
    for suffix in (".tif", "_uncertainty.tif"):
        copy2(scene_dir / rows[0]["source"].replace(".tif", suffix), scene_dir / f"other{suffix}")
    write_rows(summary, [*rows, scene], overwrite=True)
    schedule = [{"variable": "scf", "date": "2022-10-03"}]
    report = validate_schedule(project, schedule=schedule)
    assert any("2 matching observations" in e and "other.tif" in e and "09:30:00" in e for e in report.errors)
    schedule[0]["source_id"] = "other.tif"
    report = validate_schedule(project, schedule=schedule)
    # UTC+1 projects 09:30Z to the midpoint between the 09:00 and 12:00 model times.
    assert any("row 1" in e and "tied between multiple model timesteps" in e for e in report.errors)


def test_unknown_acquisition_time_is_preserved_on_a_daily_clock(project):
    setup = project.parent.parent
    summary = setup / "obs/summaries" / project.name / "scf_summary.csv"
    rows = read_rows(summary)
    rows[0].update(acquisition_time="", time_source="midnight_fallback", time_quality="fallback_midnight")
    write_rows(summary, rows, overwrite=True)
    _project_table(project, [{"variable": "scf", "date": rows[0]["date"]}])
    yaml = YAML()
    config_path = project / f"{project.name}.yml"
    config = yaml.load(config_path)
    config["obs"]["snowcover"].pop("acquisition_manifest", None)
    with config_path.open("w") as stream:
        yaml.dump(config, stream)
    assert any("acquisition time unknown" in e for e in validate_schedule(project).errors)
    clock = setup / "rofental.yml"
    config = yaml.load(clock)
    config["timestep"] = "1D"
    with clock.open("w") as stream:
        yaml.dump(config, stream)
    result = prepare_project(project)
    observation, = read_rows(result.observation_paths[0])
    assert observation["time_quality"] == "unknown"
    assert "observation_time" not in observation
    assert "acquisition_time" not in observation
    assert observation["matched_model_time"].startswith("2022-10-03T00:00:00")


def test_scheduled_date_locates_obs_when_matched_model_time_is_on_previous_day(project):
    setup = project.parent.parent
    summary = setup / "obs/summaries" / project.name / "scf_summary.csv"
    rows = read_rows(summary)
    rows[0]["acquisition_time"] = "2022-10-03T01:00:00Z"
    write_rows(summary, rows, overwrite=True)
    _project_table(project, [{"variable": "scf", "date": rows[0]["date"]}])
    clock = setup / "rofental.yml"
    yaml = YAML()
    config = yaml.load(clock)
    config["timezone"] = -7
    with clock.open("w") as stream:
        yaml.dump(config, stream)
    result = prepare_project(project)
    path, = result.observation_paths
    observation, = read_rows(path)
    assert "20221003" in path.name
    assert observation["date"] == "2022-10-03"
    assert observation["matched_model_time"] == "2022-10-02T18:00:00"
