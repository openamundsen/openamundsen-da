import csv
from pathlib import Path

import pandas as pd
import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
ROFENTAL_PROJECT_YAML = (
    REPO_ROOT
    / "examples"
    / "rofental"
    / "projects"
    / "project_2022_2023"
    / "project_2022_2023.yml"
)


def _load_rofental_project_config() -> dict:
    with ROFENTAL_PROJECT_YAML.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def test_shipped_rofental_uses_promoted_golden_project_config() -> None:
    cfg = _load_rofental_project_config()

    assert cfg["obs"]["snowcover"]["summary_csv"] == (
        "obs/summaries/project_2022_2023/scf_summary.csv"
    )
    assert cfg["obs"]["wetsnow"]["summary_csv"] == (
        "obs/summaries/project_2022_2023/wet_snow_summary.csv"
    )

    da_cfg = cfg["data_assimilation"]
    assert da_cfg["prior_forcing"]["random_seed"] == 1415935400
    assert da_cfg["resampling"]["seed"] == 1415935400
    assert da_cfg["rejuvenation"]["seed"] == 1415935400

    expected_sigmas = {
        "sigma_t": 1.0,
        "mu_p": -0.055,
        "sigma_p": 0.6,
        "sigma_rh": 1.2,
        "sigma_sw": 0.15,
    }
    assert {
        key: da_cfg["prior_forcing"][key]
        for key in expected_sigmas
    } == expected_sigmas
    assert {
        key: da_cfg["rejuvenation"][key]
        for key in expected_sigmas
    } == expected_sigmas

    assert da_cfg["assimilation_events"] == [
        {"date": "2022-11-17", "variable": "station_hs"},
        {"date": "2022-12-07", "variable": "station_hs"},
        {"date": "2023-01-01", "variable": "station_hs"},
        {"date": "2023-01-31", "variable": "station_hs"},
        {"date": "2023-02-21", "variable": "station_hs"},
        {"date": "2023-04-26", "variable": "scf", "product": "SNOWCOVER"},
        {
            "date": "2023-05-03",
            "observation_time": "2023-05-03T05:26:24Z",
            "variable": "wet_snow_line",
            "product": "WETSNOW",
        },
        {"date": "2023-05-26", "variable": "scf", "product": "SNOWCOVER"},
    ]
    assert da_cfg["likelihood"]["wet_snow_line"]["obs_sigma"] == 200.0
    assert da_cfg["likelihood"]["wet_snow_line"]["min_model_finite_fraction"] == 0.95
    assert da_cfg["uncertainty"]["scf"]["assimilation"] == {
        "sigma_mode": "uncertainty_layer",
        "aggregate_metric": "unc_mean",
    }


def test_shipped_rofental_snowcover_files_use_neutral_prefix() -> None:
    rofental_root = REPO_ROOT / "examples" / "rofental"
    snowcover_dir = rofental_root / "obs" / "snowcover"
    renamed_files = tuple(snowcover_dir.glob("s2_fsc_rofental_*"))
    legacy_prefix = "s2_fsc_" + "snowflake_rofental_*"

    assert len(renamed_files) == 153
    assert not tuple(snowcover_dir.glob(legacy_prefix))

    summary_path = (
        rofental_root
        / "obs"
        / "summaries"
        / "project_2022_2023"
        / "scf_summary.csv"
    )
    with summary_path.open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))

    assert rows
    assert all(row["source"].startswith("s2_fsc_rofental_") for row in rows)
    assert all("snowflake" not in row["source"].lower() for row in rows)


def test_shipped_rofental_wet_snow_summary_uses_uppermost_crossing() -> None:
    summary_path = (
        REPO_ROOT
        / "examples"
        / "rofental"
        / "obs"
        / "summaries"
        / "project_2022_2023"
        / "wet_snow_summary.csv"
    )
    with summary_path.open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))

    assert rows
    assert {row["wet_snow_line_method"] for row in rows} == {
        "uppermost_crossing_fraction"
    }
    may_3 = next(row for row in rows if row["date"] == "2023-05-03")
    assert may_3["acquisition_time"] == "2023-05-03T05:26:24Z"
    assert float(may_3["wet_snow_line"]) == 3066.718769538577


def test_rofental_hourly_forcing_covers_four_winters() -> None:
    meteo = REPO_ROOT / "examples" / "rofental" / "meteo"
    starts = {
        "bellavista": "2019-10-01",
        "latschbloder": "2019-10-01",
        "proviantdepot": "2019-10-03 02:00",
    }
    stations = {}
    for station_id, start in starts.items():
        frame = pd.read_csv(meteo / f"{station_id}.csv", index_col="date", parse_dates=True)
        assert frame.index.equals(pd.date_range(start, "2023-09-30 23:00", freq="h"))
        stations[station_id] = frame

    # A model step needs three hourly increments at the same station.
    available = []
    for frame in stations.values():
        bins = frame.resample("3h", closed="right", label="right")
        means = bins.mean()
        means["precip"] = bins["precip"].sum(min_count=3)
        available.append(means.notna())
    coverage = pd.concat(available).groupby(level=0).any()
    variables = ["temp", "precip", "sw_in", "rel_hum", "wind_speed"]
    for year in range(2019, 2023):
        start = "2019-10-04" if year == 2019 else f"{year}-10-01"
        steps = pd.date_range(start, f"{year + 1}-06-30 21:00", freq="3h")
        assert coverage.loc[steps, variables].all().all()

    bella_precip = stations["bellavista"]["precip"]
    assert bella_precip.loc["2020-12-07 13:00":"2020-12-07 18:00"].tolist() == [
        0.2, 0.4, 0.1, 0.5, 0.4, 0.1,
    ]
    for station_id in ("latschbloder", "proviantdepot"):
        for midnight in ("2022-01-01 00:00", "2023-01-01 00:00"):
            assert stations[station_id].at[pd.Timestamp(midnight), "precip"] == 0.0

    # Totals over available hourly readings in the shipped 2022/23 project.
    totals_mm = {
        station_id: round(frame.loc["2022-10-01":"2023-06-30", "precip"].sum(), 2)
        for station_id, frame in stations.items()
    }
    assert totals_mm == {"bellavista": 492.30, "latschbloder": 493.60, "proviantdepot": 499.51}
