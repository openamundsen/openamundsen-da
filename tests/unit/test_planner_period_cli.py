"""Folder discovery, editable selections and the optional shared evaluation workflow."""

from datetime import date

from openamundsen_da.cli import main
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
    selected = project / "schedules" / "my-trial.yml"
    assert [row["target_date"] for row in read_rows(selected)] == ["2020-03-01", "2020-03-08"]
    selected.write_text("# hand edit\n[]\n")
    assert main(args) == 1
    assert selected.read_text() == "# hand edit\n[]\n"
    (tmp_path / "evaluation.yml").write_text("[]\n")
    assert main([*args, "--overwrite"]) == 1
    assert selected.read_text() == "# hand edit\n[]\n"


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
