"""Observation selection, editable table and timeline contracts."""

import json
from datetime import date

import pytest

from openamundsen_da.cli import main
from openamundsen_da.planner.holdouts import generate_holdouts, schedule_summary
from openamundsen_da.planner.selection import compile_rules, generate_schedule, matches_holdout, target_dates
from openamundsen_da.planner.tables import read_rows, write_rows


def assimilation(variable="scf", **fields):
    return {
        "purpose": "assimilation",
        "schedule": "trial",
        "variable": variable,
        "frequency": 7,
        "tolerance_days": 2,
        "priority": 1,
        "reschedule": True,
        "period_start": "0-01-01",
        "period_end": "0-01-31",
        **fields,
    }


def evaluation(variable="scf", **fields):
    return {
        "purpose": "evaluation",
        "variable": variable,
        "count": 2,
        "strategy": "best",
        "period_start": "0-01-01",
        "period_end": "0-01-31",
        **fields,
    }


def candidate(variable, day, quality=1, **fields):
    return {
        "variable": variable,
        "date": f"2020-01-{day:02d}",
        "source_id": f"{variable}-{day}",
        "quality_for_ranking": quality,
        **fields,
    }


def test_period_frequencies_counts_and_calendar_anchor():
    rows = [
        assimilation(period_end="0-01-20", count=2),
        assimilation(period_start="0-01-31", period_end="0-04-30", frequency="monthly"),
        assimilation("station_hs", priority=2, frequency=3, count=3),
        evaluation(year=2019),
    ]
    rules = compile_rules(rows, 2020)
    assert [list(map(str, target_dates(rule))) for rule in rules] == [
        ["2020-01-01", "2020-01-08"],
        ["2020-01-31", "2020-02-29", "2020-03-31", "2020-04-30"],
        ["2020-01-01", "2020-01-04", "2020-01-07"],
    ]
    assert compile_rules([evaluation(period_end="1-02-last")], 2019)[0].end == date(2020, 2, 29)
    events = generate_schedule([], rules, "trial", [])
    assert len(events) == 9
    assert all(not row["date"] and row["status"] == "missed" and row["reason"] for row in events)
    assert [r["requested"] for r in schedule_summary(events, rules, "trial")] == [2, 4, 3]


@pytest.mark.parametrize(
    ("rows", "error"),
    [
        ([], "no selection rules"),
        ([assimilation(), assimilation()], "overlapping"),
        ([assimilation(), assimilation("wet_snow")], "priority 1"),
        ([assimilation(), assimilation(priority=3, schedule="second")], "inconsistent priority"),
        ([assimilation(), assimilation("wet_snow", priority=2, reschedule=False)], "one reschedule"),
        ([assimilation(variable="other")], "unsupported variable"),
        ([assimilation(period_start="1-11-23")], "precedes period_start"),
        ([assimilation(period_end="1-02-30")], "period_end"),
        ([assimilation(count=7)], "exceeds the number"),
        ([assimilation(frequency="0")], "frequency"),
        ([assimilation(tolerance_days=-1)], "tolerance_days"),
        ([assimilation("station_hs", ranking="quality")], "requires ranking=nearest"),
        ([assimilation(strategy="spread")], "unexpected fields"),
        ([evaluation(priority=1)], "unexpected fields"),
        ([evaluation(strategy="odd")], "strategy"),
        ([evaluation(count="")], "count"),
        ([evaluation(min_station_count=1.5)], "nonnegative integer"),
        ([evaluation(min_n_wet="nan")], "finite"),
        ([evaluation(min_support_coverage_ratio=2)], "between zero and one"),
        ([evaluation(min_elevation=10, max_elevation=2)], "exceeds"),
    ],
)
def test_reject_invalid_rules(rows, error):
    with pytest.raises(ValueError, match=error):
        compile_rules(rows, 2020)


@pytest.mark.parametrize(
    ("reschedule", "alternative", "status", "selected"),
    [
        (True, True, "shifted", "2020-01-02"),
        (True, False, "missed", ""),
        (False, True, "missed", ""),
    ],
)
def test_conflict_controls_actual_acquisition_and_original_window(reschedule, alternative, status, selected):
    rules = compile_rules(
        [
            assimilation("wet_snow_line", count=1, reschedule=reschedule),
            assimilation(count=1, priority=2, reschedule=reschedule),
        ],
        2020,
    )
    candidates = [candidate("wet_snow_line", 1), candidate("scf", 1, 10), candidate("scf", 4, 50)]
    if alternative:
        candidates.append(candidate("scf", 2))
    rows = {r["variable"]: r for r in generate_schedule(candidates, rules, "trial", [])}
    assert rows["wet_snow_line"]["date"] == "2020-01-01"
    row = rows["scf"]
    assert (row["status"], row["date"], row["preferred_date"], row["conflict_with"]) == (
        status,
        selected,
        "2020-01-01",
        "wet_snow_line",
    )
    assert row["reschedule"] is reschedule
    assert row["source_id"] == ("scf-2" if selected else "")
    assert bool(row["reason"]) == (status == "missed")


def test_station_nearest_with_partial_reservation_and_project_lower_bound():
    rules = compile_rules([assimilation("station_hs", count=1, min_station_count=1)], 2020)
    candidates = [
        candidate("station_hs", 1, station_ids="lb|pd|s3", project_min_station_count=2),
        candidate("station_hs", 2, station_ids="a|b|c|d"),
    ]
    held = [{"date": candidates[0]["date"], "variable": "station_hs", "station_ids": "LB"}]
    (row,) = generate_schedule(candidates, rules, "trial", held)
    assert (row["date"], row["station_ids"]) == ("2020-01-01", "pd|s3")
    assert json.loads(row["effective_limits"])["min_station_count"] == 2
    held[0]["station_ids"] = "lb|pd"
    exclusions = []
    (row,) = generate_schedule(candidates, rules, "trial", held, exclusions=exclusions)
    assert row["date"] == "2020-01-02"
    assert "station_count=1 fails min_station_count=2" in exclusions[0]["reason"]


def test_evaluation_uses_tightest_rule_and_project_bounds_records_measures_and_shortfall():
    rules = compile_rules(
        [
            assimilation(min_support_coverage_ratio=0.4),
            assimilation(schedule="second", min_support_coverage_ratio=0.6),
            evaluation(min_support_coverage_ratio=0.3, min_spacing_days=10, count=3),
        ],
        2020,
    )
    candidates = [
        candidate("scf", 1, 10, support_coverage_ratio=0.5),
        candidate("scf", 2, 9, support_coverage_ratio=0.7),
        candidate("scf", 3, 7, support_coverage_ratio=0.8, project_min_support_coverage_ratio=0.9),
        candidate("scf", 4, 1),
        candidate("scf", 5, 10, support_coverage_ratio=0.9),
        candidate("scf", 16, 5, support_coverage_ratio=0.7),
    ]
    exclusions, summary = [], []
    held = generate_holdouts(candidates, rules, exclusions=exclusions, summary=summary)
    assert [r["date"] for r in held] == ["2020-01-05", "2020-01-16"]
    assert [r["reason"] for r in exclusions] == [
        "support_coverage_ratio=0.5 fails min_support_coverage_ratio=0.6",
        "support_coverage_ratio=0.8 fails min_support_coverage_ratio=0.9",
        "support_coverage_ratio unavailable (min_support_coverage_ratio=0.6)",
    ]
    assert (summary[0]["requested"], summary[0]["selected"], summary[0]["achieved_min_spacing_days"]) == (
        3,
        2,
        11,
    )
    assert "spacing" in summary[0]["reason"]
    assert all(
        r["date"] not in {h["date"] for h in held}
        for r in generate_schedule(candidates, rules, "trial", held)
    )


def test_monthly_holdouts_maximize_count_before_quality_and_spread_uses_eligible_span():
    candidates = [
        candidate("scf", 1),
        candidate("scf", 31, 1000),
        dict(candidate("scf", 1), date="2020-02-03"),
    ]
    rules = compile_rules(
        [evaluation(strategy="best_per_month", period_end="0-02-last", min_spacing_days=10)], 2020
    )
    assert [r["date"] for r in generate_holdouts(candidates, rules)] == ["2020-01-01", "2020-02-03"]
    rules = compile_rules([evaluation(strategy="spread")], 2020)
    candidates = [candidate("scf", d, 100 if d in (1, 31) else 1) for d in [1, 10, 12, 21, 31]]
    assert [r["date"] for r in generate_holdouts(candidates, rules)] == ["2020-01-10", "2020-01-21"]


def test_edited_reservations_resolve_unambiguously_and_exclude_the_shared_radar_scene():
    rules = compile_rules([assimilation("wet_snow", count=1)], 2020)
    observation = candidate("wet_snow_line", 1, observation_time="2020-01-01T10:00:00Z")
    second = {**observation, "source_id": "second", "observation_time": "2020-01-01T13:00:00Z"}
    same_scene = {**observation, "variable": "wet_snow"}
    different_scene = {**second, "variable": "wet_snow", "quality_for_ranking": 0}
    candidates = [observation, same_scene, second, different_scene]
    holdout = {
        "variable": "wet_snow_line",
        "date": "2020-01-01",
        "observation_time": "2020-01-01T11:00:00+01:00",
    }
    assert matches_holdout(observation, holdout)
    assert matches_holdout({**same_scene, "observation_time": ""}, observation)
    assert not matches_holdout({**same_scene, "variable": "scf"}, observation)
    (selected,) = generate_schedule(candidates, rules, "trial", [holdout])
    assert selected["source_id"] == "second"
    with pytest.raises(ValueError, match="2 matching"):
        generate_schedule(candidates, rules, "trial", [{"variable": "wet_snow_line", "date": "2020-01-01"}])
    with pytest.raises(ValueError, match="0 matching"):
        generate_schedule(candidates, rules, "trial", [{**holdout, "source_id": "typo"}])
    with pytest.raises(ValueError, match="duplicate reservation"):
        generate_schedule(candidates, rules, "trial", [holdout, holdout])
    with pytest.raises(ValueError, match="duplicate observation"):
        generate_schedule(candidates * 2, rules, "trial", [])
    with pytest.raises(ValueError, match="timezone offset"):
        generate_schedule([{**same_scene, "observation_time": "2020-01-01T11:00"}], rules, "trial", [])


def test_csv_yaml_round_trip_through_cli_and_editable_plot(tmp_path, monkeypatch):
    rules = [assimilation(count=2), evaluation(count=1)]
    candidates = [candidate("scf", 1, 2), candidate("scf", 2, 3)]
    selections = []
    for suffix in ("csv", "yaml"):
        inventory, prescriptions, holds, schedule = [
            tmp_path / f"{stem}.{suffix}" for stem in ["inventory", "rules", "held", "events"]
        ]
        write_rows(inventory, candidates)
        write_rows(prescriptions, rules)
        args = ["--inventory", str(inventory), "--rules", str(prescriptions), "--year", "2020"]
        assert main(["planner", "holdouts", *args, "--output", str(holds)]) == 0
        generate = [
            "planner",
            "generate",
            *args,
            "--withheld",
            str(holds),
            "--schedule",
            "trial",
            "--output",
            str(schedule),
        ]
        assert main(generate) == 0
        selections.append((read_rows(holds), read_rows(schedule)))
        assert main(generate) == 1
        assert main([*generate, "--overwrite"]) == 0
    assert selections[0] == selections[1]
    held, rows = selections[0]
    assert held[0]["date"] == "2020-01-02"
    assert [(r["target_date"], r["date"]) for r in rows] == [("2020-01-01", "2020-01-01"), ("2020-01-08", "")]

    from matplotlib.dates import date2num
    from openamundsen_da.methods.viz import common

    rendered = []
    save = common.save_figure_png

    def inspect(fig, output):
        (axis,) = fig.axes
        rendered.append(
            (
                [t.get_text() for t in axis.get_yticklabels()],
                [c.get_offsets().tolist() for c in axis.collections],
                axis.get_xlim(),
            )
        )
        save(fig, output)

    monkeypatch.setattr(common, "save_figure_png", inspect)
    plot = tmp_path / "winter.png"
    args = [
        "planner",
        "plot",
        "--schedule",
        f"Monthly={schedule}",
        "--withheld",
        str(holds),
        "--output",
        str(plot),
    ]
    assert main(args) == 0
    assert plot.read_bytes().startswith(b"\x89PNG")
    labels, points, _ = rendered[-1]
    assert labels == ["Monthly\n1 scheduled update", "Withheld\n1 observation"]
    assert [p[0] for p in points] == [
        [date2num(date(2020, 1, 1)), 0],
        [date2num(date(2020, 1, 8)), 0],
        [date2num(date(2020, 1, 2)), 1],
    ]
    write_rows(schedule, [{"variable": "scf", "date": "2020-02-20"}], overwrite=True)
    assert main(["planner", "plot", "--schedule", str(schedule), "--output", str(plot)]) == 0
    labels, points, bounds = rendered[-1]
    assert labels == [f"{schedule.stem}\n1 scheduled update", "Withheld\n0 observations"]
    assert points == [[[date2num(date(2020, 2, 20)), 0]]]
    assert bounds[1] - bounds[0] == 29


def test_spacing_across_periods_and_no_quality_candidates():
    candidates = [candidate("scf", 5, 3), candidate("scf", 7), candidate("scf", 8)]
    rules = compile_rules(
        [
            evaluation(count=1, min_spacing_days=4, period_end="0-01-06"),
            evaluation(count=1, min_spacing_days=1, period_start="0-01-07"),
        ],
        2020,
    )
    summary, exclusions = [], []
    rows = generate_holdouts(candidates, rules, summary=summary, exclusions=exclusions)
    assert [r["date"] for r in rows] == ["2020-01-05"]
    assert summary[1]["selected"] == 0 and summary[1]["reason"]
    assert all("spacing" in r["reason"] for r in exclusions)
    rules = compile_rules(
        [
            assimilation(count=1, min_spacing_days=4, tolerance_days=6, period_end="0-01-06"),
            assimilation(count=1, min_spacing_days=1, period_start="0-01-07"),
        ],
        2020,
    )
    rows = generate_schedule(candidates, rules, "trial", [])
    assert [(r["date"], r["status"]) for r in rows] == [("2020-01-05", "selected"), ("", "missed")]
    assert "spacing" in rows[1]["reason"]
    rules = compile_rules([assimilation(count=1, max_cloud_fraction=0.3)], 2020)
    rows = generate_schedule([candidate("scf", 1, cloud_fraction=0.4)], rules, "trial", [])
    assert "quality" in rows[0]["reason"]
    summary = []
    assert generate_holdouts([], compile_rules([evaluation()], 2020), summary=summary) == []
    assert summary[0]["requested"] == 2 and summary[0]["selected"] == 0


def test_plot_separates_overlapping_variables_and_empty_rows(tmp_path, monkeypatch):
    from openamundsen_da.methods.viz import common
    from openamundsen_da.planner.timeline import plot_timeline

    reviewed = {}

    def capture(fig, output):
        axis, = fig.axes
        reviewed[output.name] = {
            "positions": [c.get_offsets()[0].tolist() for c in axis.collections],
            "labels": [t.get_text() for t in axis.get_legend().get_texts()] if axis.get_legend() else [],
            "x_ticks": axis.get_xticks().tolist(),
        }

    monkeypatch.setattr(common, "save_figure_png", capture)
    missed = {"variable": "scf", "date": "", "target_date": "2020-01-01"}
    station = {"variable": "station_hs", "date": "2020-01-01"}
    plot_timeline({"7-day": [missed, station]}, [], tmp_path / "overlap.png")
    (x0, y0), (x1, y1) = reviewed["overlap.png"]["positions"]
    assert x0 == x1
    assert -0.5 < y0 < y1 < 0.5
    assert reviewed["overlap.png"]["labels"] == ["SCF", "station HS", "Missed target"]
    plot_timeline({"empty": []}, [], tmp_path / "empty.png")
    assert reviewed["empty.png"] == {"positions": [], "labels": [], "x_ticks": []}


def test_cli_preserves_other_outputs_when_any_destination_already_exists(tmp_path):
    rules, inventory, output = [tmp_path / name for name in ("rules.csv", "inventory.csv", "selection.csv")]
    write_rows(rules, [assimilation()])
    write_rows(inventory, [], fields=["date", "variable"])
    summary = tmp_path / "selection_summary.csv"
    summary.write_text("reviewed summary\n")
    args = ["planner", "generate", "--rules", str(rules), "--inventory", str(inventory), "--year", "2020",
            "--output", str(output), "--schedule", "trial"]
    assert main(args) == 1
    assert summary.read_text() == "reviewed summary\n"
    assert not output.exists()
    assert main([*args, "--output", str(rules), "--overwrite"]) == 1
    assert read_rows(rules)[0]["variable"] == "scf"
    assert main([*args, "--inventory", str(tmp_path / "missing.csv")]) == 1
