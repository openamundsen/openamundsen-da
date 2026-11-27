"""Folder discovery, editable selections and the optional shared evaluation workflow."""

from datetime import date

import pytest

from openamundsen_da.cli import main
from openamundsen_da.planner.reporting import read_report_targets
from openamundsen_da.planner.tables import read_rows, write_rows


def test_plot_discovers_dated_files_in_period_order_and_reads_edits(tmp_path, monkeypatch):
    from openamundsen_da.planner import discovery

    monkeypatch.chdir(tmp_path)
    late = tmp_path / "periods" / "a"
    early = tmp_path / "periods" / "z"
    unused = tmp_path / "periods" / "draft"
    for directory, label, month in ((early, "Early", "03"), (late, "Late", "06"), (unused, "Draft", "12")):
        directory.mkdir(parents=True)
        (directory / "period.yml").write_text(f"label: {label}\nstart: 2020-{month}-01\nend: 2020-{month}-07\n")
    write_rows(early / "schedules/x.csv", [{"date": "2020-03-01", "variable": "scf"}])
    source = late / "schedules/weekly.yaml"
    write_rows(source, [{"date": "2020-06-02", "variable": "scf"}])
    write_rows(late / "reports/weekly_summary.yml", [{"requested": "1"}])
    seen = []

    def inspect(panels, output, *, title, columns):
        assert output == tmp_path / "timeline.png" and columns == 2
        assert [p.name for p in panels] == ["Early", "Late"]
        assert [list(p.schedules) for p in panels] == [["X"], ["Weekly"]]
        assert panels[1].bounds == (date(2020, 6, 1), date(2020, 6, 8))
        assert panels[0].withheld is None
        seen.append(panels[1].schedules["Weekly"][0]["date"])

    monkeypatch.setattr(discovery, "plot_timelines", inspect)
    assert main(["planner", "plot"]) == 0
    write_rows(source, [{"date": "2020-06-04", "variable": "scf"}], overwrite=True)
    assert main(["planner", "plot"]) == 0
    assert seen == ["2020-06-02", "2020-06-04"]
    write_rows(source.with_suffix(".csv"), [{"date": "2020-06-02", "variable": "scf"}])
    assert main(["planner", "plot"]) == 1


def test_generate_one_schedule_without_evaluation_and_preserve_edits(tmp_path):
    project = tmp_path / "periods" / "spring_campaign"
    project.mkdir(parents=True)
    (project / "period.yml").write_text("start: 2020-03-01\nend: 2020-03-10\n")
    rules = tmp_path / "schedules" / "my-trial"
    rules.mkdir(parents=True)
    (rules / "rules.yml").write_text(
        "defaults: {frequency: 7, tolerance_days: 0, reschedule: false}\n"
        "observations:\n  scf: {priority: 1}\n"
    )
    write_rows(project / "inventory.csv", [], fields=["date", "variable", "source_id"])
    args = ["planner", "generate", "--period", str(project), "--schedule", "my-trial"]
    assert main(args) == 0
    selected = project / "my-trial.schedule.yml"
    assert not (project / "schedules").exists()
    assert read_rows(selected) == []
    assert (project / "reports/my-trial.pdf").read_bytes().startswith(b"%PDF-")
    assert not (project / "reports/details").exists()
    targets = read_report_targets(project / "reports/my-trial_details.yml")
    assert [row["target_date"] for row in targets] == ["2020-03-01", "2020-03-08"]
    assert all(row["status"] == "missed" and row["reason"] for row in targets)
    export = tmp_path / "export" / "review.csv"
    assert main([*args, "--output", str(export)]) == 0
    assert export.read_text() == "date,variable\n"
    assert read_report_targets(export.with_name("review.csv.details.yml")) == targets
    selected.write_text("# hand edit\n[]\n")
    assert main(args) == 1
    assert selected.read_text() == "# hand edit\n[]\n"
    from openamundsen_da.planner.discovery import read_plot_schedule

    assert len(read_plot_schedule(selected)[1]) == 2
    next_rules = tmp_path / "schedules/second.yml"
    next_rules.write_bytes((rules / "rules.yml").read_bytes())
    assert main(["planner", "generate", "--period", str(project), "--schedule", "second"]) == 0
    assert (project / "second.schedule.yml").is_file()
    assert (project / "reports/second_details.yml").is_file()
    assert not (project / "reports/details").exists()
    assert not (project / "schedules").exists()
    (tmp_path / "evaluation.yml").write_text("[]\n")
    assert main([*args, "--overwrite"]) == 1
    assert selected.read_text() == "# hand edit\n[]\n"


@pytest.mark.parametrize("suffix", [".yml", ".csv"])
def test_compact_updates_preserve_acquisition_and_station_selections(tmp_path, suffix):
    period = tmp_path / "periods" / "spring"
    period.mkdir(parents=True)
    (period / "period.yml").write_text("start: 2020-03-01\nend: 2020-03-10\n")
    rules = tmp_path / "schedules" / "trial"
    rules.mkdir(parents=True)
    (rules / "rules.yml").write_text(
        "defaults: {frequency: 4, tolerance_days: 1, reschedule: false}\n"
        "observations: {scf: {priority: 1}, station_hs: {priority: 2}}\n"
    )

    def scene(day, hour, source, quality):
        return dict(date=day, variable="scf", observation_time=f"{day}T{hour}:00:00Z",
                    source_id=source, quality_for_ranking=quality)

    def station(day, hour):
        return dict(date=day, variable="station_hs", observation_time=f"{day}T{hour}:00:00Z",
                    station_ids="a|b")

    day1, day2, day5, day9 = (f"2020-03-{day:02d}" for day in (1, 2, 5, 9))
    inventory = [
        scene(day2, "08", "early.tif", 8), scene(day2, "09", "late.tif", 10),
        scene(day5, "10", "west.tif", 4), scene(day5, "10", "east.tif", 2),
        station(day1, "06"), station(day9, "06"), station(day9, "07"),
    ]
    write_rows(period / "inventory.csv", inventory)
    withheld = [{**station(day9, "06"), "station_ids": "a"}]
    write_rows(period / "withheld.yml", withheld)

    output = period / "schedules" / f"trial{suffix}"
    targets_path = period / "reports/trial_details.yml"
    args = ["planner", "generate", "--period", str(period), "--schedule", "trial", "--output", str(output)]
    assert main([*args, "--summary", str(targets_path), "--overwrite"]) == 1
    assert not output.exists() and not targets_path.exists()
    assert main(args) == 0
    expected = [
        {"date": day1, "variable": "station_hs"},
        {"date": day2, "variable": "scf", "observation_time": f"{day2}T09:00:00Z"},
        {"date": day5, "variable": "scf", "observation_time": f"{day5}T10:00:00Z", "source_id": "west.tif"},
        {"date": day9, "variable": "station_hs", "observation_time": f"{day9}T06:00:00Z", "station_ids": "b"},
    ]
    actual = read_rows(output)
    if suffix == ".csv":
        assert output.read_text().splitlines()[0] == "date,variable,observation_time,source_id,station_ids"
        actual = [{k: v for k, v in row.items() if v} for row in actual]
    assert actual == expected
    targets = read_report_targets(targets_path)
    assert len(targets) == 6
    assert [row.get("source_id", "") for row in targets if row["variable"] == "scf"] == ["late.tif", "west.tif", ""]
    assert [(row["status"], row.get("station_ids", "")) for row in targets if row["variable"] == "station_hs"] == [
        ("selected", "a|b"), ("missed", ""), ("selected", "b")
    ]
    targets_path.write_text("reviewed")
    output.unlink()
    assert main(args) == 1
    assert not output.exists() and targets_path.read_text() == "reviewed"
    assert main([*args, "--overwrite"]) == 0
    assert read_report_targets(targets_path) == targets


def test_plot_a_short_campaign_has_readable_dates_and_no_withheld_row(tmp_path, monkeypatch):
    from openamundsen_da.methods.viz import common

    period = tmp_path / "periods" / "campaign"
    period.mkdir(parents=True)
    (period / "period.yml").write_text("start: 2020-06-02\nend: 2020-06-08\n")
    write_rows(period / "schedules/sparse.csv", [{"date": "2020-06-05", "variable": "scf"}])
    seen = []

    def inspect(fig, path):
        axis, = fig.axes
        axis.figure.canvas.draw()
        left, right = axis.get_xlim()
        seen.extend(
            label.get_text() for label in axis.get_xticklabels() if left <= label.get_position()[0] <= right
        )
        assert [label.get_text() for label in axis.get_yticklabels()] == ["Sparse (1)"]

    monkeypatch.setattr(common, "save_figure_png", inspect)
    assert main(["planner", "plot", "--period", str(period)]) == 0
    assert len(set(seen) - {""}) >= 2
