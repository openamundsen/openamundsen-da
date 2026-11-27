"""Collect observation-only evidence once, using the project's existing inputs."""

from __future__ import annotations

import csv
import math
from datetime import date, time
from pathlib import Path

import numpy as np
import pandas as pd
import rasterio

from openamundsen_da.io.paths import find_project_yaml, infer_setup_dir_from_project
from openamundsen_da.methods.pf.fraction_support import observation_source_support
from openamundsen_da.observer.summary_paths import resolve_fraction_summary_path
from openamundsen_da.planner.tables import read_rows
from openamundsen_da.util.landcover_mask import resolve_landcover_mask
from openamundsen_da.util.observation_raster import (
    is_netcdf_path,
    netcdf_band_index_for_token,
    open_netcdf_variable_raster,
)
from openamundsen_da.util.observation_time import (
    SeriesTimeUnavailableError,
    load_model_clock_config,
    match_series_value_to_model_time,
    model_timezone,
    read_acquisition_manifest,
)
from openamundsen_da.util.station_da import (
    STATION_DA_METADATA_FILENAME,
    is_station_variable,
    read_station_metadata,
    station_ids_disabled_for_role,
    station_observation_csvs,
    station_variable_spec,
)
from openamundsen_da.util.ts import read_timeseries_csv
from openamundsen_da.util.uncertainty_common import assert_same_grid
from openamundsen_da.util.yaml_utils import read_yaml_mapping


def project_quality_limits(project_config: dict, variable: str) -> dict:
    """Return observation-only likelihood gates; ensemble-dependent gates run later."""
    likelihood = project_config.get("data_assimilation", {}).get("likelihood", {}).get(variable, {})
    fields = {"min_support_coverage_ratio": "project_min_support_coverage_ratio"}
    if variable == "wet_snow_line":
        fields.update(min_wet_pixels_total="project_min_n_wet", min_wet_bands="project_min_wet_bands")
    return {column: likelihood[key] for key, column in fields.items() if key in likelihood}


def _station_rows(setup, config, variable, days, clock_time):
    station_dir = setup / config["obs"]["stations"]["dir"]
    paths = station_observation_csvs(station_dir)
    if not paths:
        raise FileNotFoundError(f"No station CSVs in {station_dir}")
    metadata = read_station_metadata(station_dir / STATION_DA_METADATA_FILENAME)
    disabled = station_ids_disabled_for_role(metadata, "da")
    clock = load_model_clock_config(setup)
    zone = model_timezone(clock.timezone)
    column = station_variable_spec(variable).obs_column
    observations = {}
    for path in paths:
        station = path.stem.lower()
        if station in disabled:
            continue
        with path.open(encoding="utf-8-sig", newline="") as stream:
            header = next(csv.reader(stream), [])
        if column not in header:
            continue
        key = "time" if "time" in header else "date"
        series = read_timeseries_csv(path, key, [column])[column]
        series = series[np.isfinite(series) & (series >= 0)].sort_index()
        if series.index.tz is None:
            series.index = series.index.tz_localize(zone)
        observations[station] = series.tz_convert("UTC")

    for day in days:
        target = pd.Timestamp.combine(day, clock_time).tz_localize(zone)
        utc = target.tz_convert("UTC")
        matches = {}
        for station, series in observations.items():
            local = series.loc[utc - clock.timestep / 2 : utc + clock.timestep / 2]
            try:
                matched = match_series_value_to_model_time(
                    local, model_time=target, timestep=clock.timestep, timezone_config=clock.timezone,
                )
            except SeriesTimeUnavailableError:
                continue
            matches[station] = ",".join(stamp.isoformat() for stamp in matched.source_times)
        yield dict(
            variable=variable, date=day.isoformat(),
            observation_time=utc.isoformat(),
            source_id="", station_ids="|".join(matches), station_count=len(matches),
            station_source_times="|".join(f"{station}={times}" for station, times in matches.items()),
            quality_for_ranking=len(matches), quality_issue="" if matches else "no usable stations",
        )


def _product(variable: str) -> tuple[str, str]:
    if variable == "scf":
        return "snowcover", "scf_summary.csv"
    if variable in {"wet_snow", "wet_snow_line"}:
        return "wetsnow", "wet_snow_summary.csv"
    raise ValueError(f"Unsupported satellite variable: {variable}")


def _check_files(setup: Path, config: dict, variable: str, source_id: str) -> None:
    product, _ = _product(variable)
    directory = setup / config["obs"][product]["dir"]
    key = "scf" if variable == "scf" else "wet_snow"
    uncertainty = config.get("data_assimilation", {}).get("uncertainty", {}).get(key, {})
    ingest = uncertainty.get("ingest", {})
    external = uncertainty.get("enabled", False) and ingest.get("uncertainty_source", "product") == "product"
    tokens = [s.strip() for s in source_id.replace(";", ",").split(",") if s.strip()]
    if not tokens:
        raise ValueError("Missing satellite source ID")
    for token in tokens:
        path = directory / token.split("@")[0]
        if not path.is_file():
            raise FileNotFoundError(f"Missing observation source {path}")
        if is_netcdf_path(path):
            if external:
                band = netcdf_band_index_for_token(path, token=token, time_variable=ingest.get("time_variable"))
                with open_netcdf_variable_raster(path, variable=ingest["uncertainty_variable"], band_index=band) as src:
                    src.read(1)
        else:
            with rasterio.open(path) as src:
                src.read(1)
                if external:
                    companion = path.with_name(path.stem + "_uncertainty.tif")
                    if not companion.is_file():
                        raise FileNotFoundError(f"Missing uncertainty raster {companion}")
                    with rasterio.open(companion) as uncertainty_src:
                        assert_same_grid(src, uncertainty_src, left=path, right=companion)
                        uncertainty_src.read(1)


def check_satellite_files(project_dir: Path, variable: str, source_id: str) -> None:
    """Verify the selected project source and required uncertainty raster are readable."""
    project_dir = Path(project_dir)
    config = read_yaml_mapping(find_project_yaml(project_dir))
    _check_files(infer_setup_dir_from_project(project_dir), config, variable, source_id)


def _summary_issues(row: dict, variable: str, config: dict) -> list[str]:
    value_field = "wet_snow_fraction" if variable == "wet_snow" else variable
    issues = []
    try:
        value = float(row.get(value_field, ""))
        if not math.isfinite(value) or (variable != "wet_snow_line" and not 0 <= value <= 1):
            raise ValueError
    except ValueError:
        issues.append(f"{value_field} unavailable or out of range")
    if variable == "wet_snow_line" and row.get("wet_snow_line_gate_reason"):
        issues.append(row["wet_snow_line_gate_reason"])
    family = "scf" if variable == "scf" else "wet_snow"
    uncertainty = config.get("data_assimilation", {}).get("uncertainty", {}).get(family, {})
    assimilation = uncertainty.get("assimilation", {})
    if uncertainty.get("enabled") and assimilation.get("sigma_mode") == "uncertainty_layer":
        metric = assimilation.get("aggregate_metric")
        try:
            measured = float(row.get(metric, ""))
            valid = int(row.get("n_valid", ""))
            if not 0 <= measured <= 100 or valid <= 0 or int(row.get("unc_n_valid", "")) != valid:
                raise ValueError
        except ValueError:
            issues.append(f"incomplete or invalid uncertainty summary ({metric}, n_valid, unc_n_valid)")
    return issues


def _satellite_rows(project_dir, setup, config, variable, start, end, source_ids, dates):
    product, filename = _product(variable)
    summary = resolve_fraction_summary_path(setup, project_dir, filename)
    if not summary.is_file():
        raise FileNotFoundError(f"Missing observation summary {summary}; preprocess {product} for this project")
    landcover = resolve_landcover_mask(setup, project_dir)
    limits = project_quality_limits(config, variable)
    section = config["obs"][product]
    seen = set()
    for row in read_rows(summary):
        day = date.fromisoformat(row["date"])
        if not start <= day <= end or (source_ids is not None and row.get("source") not in source_ids):
            continue
        if dates is not None and day not in dates:
            continue
        stamp = row.get("acquisition_time", "")
        if row.get("time_quality") == "fallback_midnight" or row.get("time_source") == "midnight_fallback":
            stamp = ""
        source = row.get("source", "")
        seen.update(token.strip().split("@")[0] for token in source.replace(";", ",").split(","))
        issues = _summary_issues(row, variable, config)
        item = dict(row, **limits)
        item.update(variable=variable, source_id=source, observation_time=stamp,
                    product=config["obs"][product].get("product_tag", ""),
                    n_wet=row.get("wet_snow_line_n_wet", ""), wet_bands=row.get("wet_snow_line_wet_bands", ""))
        try:
            support = observation_source_support(
                setup_dir=setup, project_dir=project_dir, source=source, observable=variable,
                landcover_cfg=landcover, read_only=True,
            )
            item.update(support_coverage_ratio=support.coverage_ratio, quality_for_ranking=support.n_valid)
            if not support.n_valid:
                issues.append("no valid pixels in the project area")
            _check_files(setup, config, variable, source)
        except (OSError, ValueError, RuntimeError, KeyError) as exc:
            issues.append(str(exc))
        item["quality_issue"] = "; ".join(issues)
        yield item

    # A preprocessing summary can omit scenes with zero usable pixels. Preserve
    # catalogued acquisitions in the inventory so these omissions remain visible.
    if section.get("acquisition_manifest"):
        manifest = read_acquisition_manifest(setup / section["acquisition_manifest"])
        tag = section.get("product_tag", "")
        for entry in manifest.itertuples():
            if entry.product.upper() != tag.upper() or entry.source in seen:
                continue
            day = date.fromisoformat(entry.acquisition_time[:10])
            if start <= day <= end and (dates is None or day in dates) and (
                source_ids is None or entry.source in source_ids
            ):
                yield dict(variable=variable, date=day.isoformat(), observation_time=entry.acquisition_time,
                           product=tag, source_id=entry.source, quality_issue=f"no observation row in {summary.name}",
                           **limits)


def build_inventory(project_dir, start, end, variables, *, station_time=None, source_ids=None, dates=None) -> list[dict]:
    """Measure candidate observations for an inclusive period in a project."""
    project_dir = Path(project_dir).resolve()
    setup = infer_setup_dir_from_project(project_dir)
    config = read_yaml_mapping(find_project_yaml(project_dir))
    first, last = date.fromisoformat(str(start)), date.fromisoformat(str(end))
    if last < first:
        raise ValueError("Inventory end date precedes its start date")
    requested_dates = None if dates is None else {date.fromisoformat(str(d)) for d in dates}
    days = [day for day in pd.date_range(first, last).date if requested_dates is None or day in requested_dates]
    clock_time = (pd.Timestamp(config.get("start_date", first)).time() if station_time is None
                  else time.fromisoformat(station_time))
    if clock_time.tzinfo is not None:
        raise ValueError("station_time must be given on the local model clock")
    result = []
    for variable in dict.fromkeys(variables):
        if is_station_variable(variable):
            result.extend(_station_rows(setup, config, variable, days, clock_time))
        else:
            result.extend(_satellite_rows(project_dir, setup, config, variable, first, last, source_ids, requested_dates))
    return result
