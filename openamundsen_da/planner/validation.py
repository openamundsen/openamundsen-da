"""Read-only validation of manual and generated observation schedules."""

from __future__ import annotations

import json
import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path

import pandas as pd

from openamundsen_da.io.paths import find_project_yaml, find_setup_yaml
from openamundsen_da.planner.tables import read_rows
from openamundsen_da.util.observation_time import match_observation_to_model_time, parse_model_timestep, parse_utc_timestamp
from openamundsen_da.util.station_da import is_station_variable
from openamundsen_da.util.yaml_utils import read_yaml_mapping


@dataclass
class ScheduleReport:
    events: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    checks: list[str] = field(default_factory=list)


def _value(row, key):
    raw = row.get(key)
    return "" if raw is None else str(raw).strip()


def _day(raw):
    day = date.fromisoformat(str(raw))
    if day.isoformat() != str(raw):
        raise ValueError(f"Expected YYYY-MM-DD, got {raw!r}")
    return day


def _ids(raw):
    return tuple(part.strip().lower() for part in str(raw or "").split("|") if part.strip())


def _timestamp(raw):
    return parse_utc_timestamp(raw, field="observation_time") if raw else None


def _read_table(value, project_dir):
    if isinstance(value, list):
        return value
    if isinstance(value, Path):  # Explicit CLI input, relative to the caller.
        return read_rows(value)
    if isinstance(value, str) and value.strip():
        return read_rows(_reference(project_dir, value))
    raise ValueError("expected an inline sequence of rows or a project-relative CSV or YAML path")


def _reference(project_dir, value):
    path = Path(value)
    if path.is_absolute():
        raise ValueError(f"expected project-relative path, got {path}")
    resolved = (project_dir / path).resolve()
    if not resolved.is_relative_to(project_dir.parent.parent.resolve()):
        raise ValueError(f"path escapes the setup directory: {path}")
    return resolved


def planner_input_paths(project_dir, da):
    """Include all file references in preparation and scientific input identities."""
    planner = da.get("observation_planner", {})
    if not isinstance(planner, dict):
        raise ValueError("observation_planner must be a mapping")
    values = [da.get("assimilation_events"), *(planner.get(k) for k in ("inventory", "rules", "withheld"))]
    return tuple(_reference(project_dir, value) for value in values if isinstance(value, str))


def reviewed_schedule(da):
    rows = da.get("assimilation_events")
    return (
        "observation_planner" in da
        or isinstance(rows, str)
        or isinstance(rows, list)
        and any(isinstance(row, dict) and row.keys() - {"date", "product", "variable", "observation_time"} for row in rows)
    )


def _project_product(project, variable):
    section = {"scf": "snowcover", "wet_snow": "wetsnow", "wet_snow_line": "wetsnow"}.get(variable)
    if is_station_variable(variable):
        section = "stations"
    if section is None:
        raise ValueError(f"unsupported variable {variable!r}")
    obs = project.get("obs") or {}
    config = obs.get(section)
    if not isinstance(config, dict):
        raise ValueError(f"project.obs.{section} is missing for {variable}")
    if is_station_variable(variable):
        return "STATION"
    if not _value(config, "product_tag"):
        raise ValueError(f"project.obs.{section}.product_tag is missing")
    return _value(config, "product_tag").upper()


def _parse_row(raw, index, project, report, *, withheld=False):
    context = f"{'withheld' if withheld else 'schedule'} row {index}"
    if not isinstance(raw, dict):
        report.errors.append(f"{context}: expected a mapping")
        return None
    from openamundsen_da.planner.holdouts import HOLDOUT_FIELDS
    from openamundsen_da.planner.selection import SCHEDULE_FIELDS

    fields = set(HOLDOUT_FIELDS if withheld else SCHEDULE_FIELDS) | {"product", "timing_exception_reason"}
    unknown = sorted(str(key) for key in raw if key not in fields)
    if unknown:
        report.errors.append(f"{context}: unknown fields: {', '.join(unknown)}")
    invalid_fields = [str(k) for k, value in raw.items() if isinstance(value, (list, dict, tuple, set))]
    if invalid_fields:
        report.errors.append(f"{context}: expected scalar values for {', '.join(invalid_fields)}")
        return None
    row = {key: _value(raw, key) for key in raw}
    variable = row.get("variable", "").lower()
    row["variable"] = variable
    for key in ("offset_days", "tolerance_days", "min_spacing_days", "available", "eligible", "withheld"):
        if row.get(key):
            try:
                number = int(row[key])
                if key != "offset_days" and number < 0:
                    raise ValueError
            except ValueError:
                report.errors.append(f"{context} {key}: expected {'an integer' if key == 'offset_days' else 'a nonnegative integer'}")
    if row.get("quality_for_ranking"):
        try:
            if not math.isfinite(float(row["quality_for_ranking"])):
                raise ValueError
        except ValueError:
            report.errors.append(f"{context} quality_for_ranking: expected a finite number")
    if row.get("reschedule") and row["reschedule"].lower() not in {"true", "false"}:
        report.errors.append(f"{context} reschedule: expected true or false")
    if row.get("effective_limits"):
        try:
            if not isinstance(json.loads(row["effective_limits"]), dict):
                raise ValueError
        except ValueError:
            report.errors.append(f"{context} effective_limits: expected a JSON object")
    try:
        product = _project_product(project, variable)
        if row.get("product") and row["product"].upper() != product:
            raise ValueError(f"product {row['product']!r} differs from project product {product}")
        row["product"] = product
    except ValueError as exc:
        report.errors.append(f"{context} variable/product: {exc}")
        return None
    for key in ("date", "target_date", "preferred_date"):
        if row.get(key):
            try:
                row[key] = _day(row[key]).isoformat()
            except ValueError:
                report.errors.append(f"{context} {key}: expected YYYY-MM-DD, got {row[key]!r}")
                return None
    if "date" not in row:
        report.errors.append(f"{context} date: missing required field")
        return None
    if not row["date"] and (withheld or not row.get("target_date")):
        report.errors.append(f"{context} date: required (an empty target needs target_date)")
        return None
    if row.get("status") and row["status"] not in {"selected", "shifted", "missed"}:
        report.errors.append(f"{context} status: expected selected, shifted or missed")
    if row.get("status") == "missed" and row["date"]:
        report.errors.append(f"{context} status: missed target cannot have an assigned date")
    if row.get("observation_time"):
        try:
            stamp = _timestamp(row["observation_time"])
            if stamp.date().isoformat() != row["date"] and not is_station_variable(variable):
                raise ValueError(f"UTC date {stamp.date()} differs from {row['date']}")
            row["observation_time"] = stamp.isoformat()
        except ValueError as exc:
            report.errors.append(f"{context} observation_time: {exc}")
            return None
    if row.get("station_ids"):
        ids = _ids(row["station_ids"])
        if not is_station_variable(variable) or len(ids) != len(set(ids)) or not ids:
            report.errors.append(f"{context} station_ids: expected distinct station IDs for a station variable")
    return row


def _identify(row, matches):
    source = row.get("source_id")
    stamp = _timestamp(row.get("observation_time"))
    candidates = [
        item for item in matches
        if (not source or _value(item, "source_id") == source)
        and (not stamp or _timestamp(_value(item, "observation_time")) == stamp)
    ]
    if len(candidates) != 1:
        available = ", ".join(f"{item.get('source_id')} ({_value(item, 'observation_time') or 'time unknown'})"
                              for item in matches)
        raise ValueError(f"source_id/observation_time: {len(candidates)} matching observations; choices: {available or 'none'}")
    observation = dict(candidates[0])
    if row.get("station_ids"):
        available = set(_ids(observation.get("station_ids")))
        selected = _ids(row["station_ids"])
        missing = sorted(set(selected) - available)
        if missing:
            raise ValueError(f"station_ids: unavailable: {', '.join(missing)}")
        observation.update(station_ids="|".join(selected), station_count=len(selected))
    return observation


def _rule_for(row, rules, report, label):
    from openamundsen_da.planner.selection import target_dates

    actual = _day(row["date"]) if row.get("date") else None
    target = _day(row["target_date"]) if row.get("target_date") else None
    anchor = target or actual
    periods = [r for r in rules if r.variable == row["variable"] and r.start <= anchor <= r.end]
    if len(periods) != 1:
        report.errors.append(f"{label} variable/target_date: {anchor} is outside a unique active period")
        return None, target
    rule = periods[0]
    if actual and not rule.start <= actual <= rule.end:
        report.errors.append(f"{label} date: {actual} outside active period {rule.start} through {rule.end}")
    targets = target_dates(rule)
    if target and target not in targets:
        report.errors.append(f"{label} target_date: {target} is not a prescribed target")
    if not target:
        nearest = sorted(targets, key=lambda dt: (abs((dt - actual).days), dt))
        if not nearest:
            report.errors.append(f"{label} target_date: period contains no targets")
            return rule, None
        if len(nearest) > 1 and abs(nearest[0] - actual) == abs(nearest[1] - actual):
            report.errors.append(f"{label} target_date: equally near {nearest[0]} and {nearest[1]}; specify one")
        target = nearest[0]
    if actual:
        offset = (actual - target).days
        if abs(offset) > rule.tolerance_days:
            reason = row.get("timing_exception_reason")
            message = f"{label} date: {offset:+d} days from target {target}, timing tolerance ±{rule.tolerance_days} days"
            if reason:
                report.warnings.append(f"{message}; manual exception: {reason}")
            else:
                report.errors.append(f"{message}; supply timing_exception_reason for a manual exception")
    return rule, target


def _observations(project_dir, start, end, requested, report):
    """Remeasure each referenced variable/date from its configured project sources."""
    from openamundsen_da.planner.inventory import build_inventory

    by_date = defaultdict(list)
    for variable, dates in requested.items():
        try:
            for obs in build_inventory(project_dir, start, end, [variable], dates=dates):
                by_date[(variable, str(obs["date"]))].append(obs)
        except (OSError, RuntimeError, ValueError, KeyError, TypeError) as exc:
            report.errors.append(f"inventory for {variable}: {exc}")
    return by_date


def _current_observation(row, current, shared):
    key = (row["variable"], row["date"])
    # Resolve against all current acquisitions so that a new same-day scene can
    # never silently change a date-only selection.
    observation = _identify(row, current[key])
    if shared is not None:
        reviewed = _identify(row, shared[key])
        if _value(reviewed, "source_id") != _value(observation, "source_id"):
            raise ValueError("source_id differs between the shared inventory and the current project")
        old_time = _timestamp(_value(reviewed, "observation_time"))
        if old_time and old_time != _timestamp(_value(observation, "observation_time")):
            raise ValueError("observation_time differs between the shared inventory and the current project")
        if is_station_variable(row["variable"]):
            if not _ids(reviewed.get("station_ids")):
                raise ValueError("station_ids: no contributing station in the shared inventory")
            observation = _identify({**row, "station_ids": reviewed["station_ids"]}, current[key])
    if is_station_variable(row["variable"]):
        if not _ids(observation.get("station_ids")):
            raise ValueError("station_ids: no contributing stations")
    elif not _value(observation, "source_id"):
        raise ValueError("source_id: missing satellite source")
    return observation


def _windows(project, timestep, dates):
    from openamundsen_da.pipeline.project_skeleton import ProjectConfig, propagation_windows

    config = ProjectConfig(
        start=datetime.fromisoformat(str(project["start_date"])),
        end=datetime.fromisoformat(str(project["end_date"])),
        assimilation_event_dates=[datetime.combine(day, time.min) for day in sorted(dates)],
    )
    return dict(zip(sorted(dates), propagation_windows(timestep, config)))


def _match_update(obs, window, timestep, timezone):
    times = pd.date_range(window.start, window.end, freq=timestep)
    day = _day(obs["date"])
    stamp = _timestamp(_value(obs, "observation_time"))
    if is_station_variable(obs["variable"]):
        # The station likelihood uses this event date on the step's local clock.
        target = datetime.combine(day, window.start.time())
        if target not in times:
            raise ValueError(f"station update {target} is outside the model time grid")
        if stamp and match_observation_to_model_time(observation_time=stamp, model_times=times,
                                                    timezone_config=timezone).model_time != target:
            raise ValueError(f"station observation_time does not match the run's station update at {target}")
    elif stamp:
        match_observation_to_model_time(observation_time=stamp, model_times=times, timezone_config=timezone)
    elif timestep < timedelta(days=1) or len(times[times.date == day]) != 1:
        raise ValueError("acquisition time unknown; this model step requires source metadata")


def _suggest_replacement(project_dir, project, shared, rule, target, selected, holdouts, active,
                         start, end, timestep, timezone):
    from openamundsen_da.planner.selection import candidate_rank, screening_issues, unreserved_candidate

    occupied = {_day(r["date"]) for r in selected if r.get("date")}
    lower = max(start, rule.start, target - timedelta(days=rule.tolerance_days))
    upper = min(end, rule.end, target + timedelta(days=rule.tolerance_days))
    days = {lower + timedelta(days=i) for i in range((upper - lower).days + 1)} - occupied
    for row in selected:
        if row.get("date") and row.get("variable") == rule.variable:
            anchor = _day(row.get("target_date") or row["date"])
            other = next((r for r in active if r.variable == rule.variable and r.start <= anchor <= r.end), rule)
            days = {d for d in days if abs((d - _day(row["date"])).days) >= max(rule.min_spacing_days, other.min_spacing_days)}
    if shared is not None:
        days = {d for d in days if (rule.variable, d.isoformat()) in shared}
    report = ScheduleReport()
    current = _observations(project_dir, start, end, {rule.variable: days}, report) if days else {}
    candidates = []
    for (_, day), observations in current.items():
        for obs in observations:
            selector = {key: obs.get(key, "") for key in ("variable", "date", "source_id", "observation_time")}
            try:
                candidate = _current_observation(selector, current, shared)
                candidate = unreserved_candidate(candidate, holdouts)
                if candidate is None or screening_issues(candidate, rule):
                    continue
                windows = _windows(project, timestep, occupied | {_day(day)})
                _match_update(candidate, windows[_day(day)], timestep, timezone)
            except (ValueError, TypeError, KeyError):
                continue
            candidates.append(candidate)
    if not candidates:
        return f"missed target {target}; no eligible replacement" + (
            f" ({'; '.join(report.errors)})" if report.errors else ""
        )
    chosen = min(candidates, key=lambda item: candidate_rank(item, rule, target))
    return f"suggested replacement {chosen['date']} ({chosen.get('source_id') or chosen.get('station_ids')})"


def validate_schedule(project_dir, *, schedule=None, inventory=None, rules=None, withheld=None,
                      schedule_name=None, year=None, project=None):
    """Validate run-independent inputs, collecting row failures without writing files."""
    from openamundsen_da.planner.holdouts import inherited_evaluation_limits
    from openamundsen_da.planner.selection import compile_rules, matches_holdout, quality_issues, target_dates, validate_inventory

    project_dir = Path(project_dir).resolve()
    report = ScheduleReport()
    try:
        if project is None:
            project = read_yaml_mapping(find_project_yaml(project_dir), error_cls=ValueError, context="project")
        setup = read_yaml_mapping(find_setup_yaml(project_dir.parent.parent), error_cls=ValueError, context="setup")
        da = project.get("data_assimilation", {})
        if not isinstance(da, dict):
            raise ValueError("project.data_assimilation must be a mapping")
        settings = da.get("observation_planner", {})
        if not isinstance(settings, dict) or settings.keys() - {"inventory", "withheld", "rules", "schedule", "year"}:
            raise ValueError("observation_planner expects a mapping with inventory, withheld, rules, schedule or year")
        rows = _read_table(da.get("assimilation_events") if schedule is None else schedule, project_dir)
        withheld_input = settings.get("withheld", []) if withheld is None else withheld
        withheld_rows = _read_table(withheld_input, project_dir)
        inventory_input = settings.get("inventory") if inventory is None else inventory
        rule_input = settings.get("rules") if rules is None else rules
        start = datetime.fromisoformat(str(project["start_date"])).date()
        end = datetime.fromisoformat(str(project["end_date"])).date()
        timestep = parse_model_timestep(setup.get("timestep"))
        compiled = compile_rules(_read_table(rule_input, project_dir), int(year or settings.get("year", start.year))) if rule_input is not None else []
    except (ValueError, OSError, TypeError, KeyError) as exc:
        report.errors.append(str(exc))
        return report

    selected = [(i, _parse_row(raw, i, project, report)) for i, raw in enumerate(rows, 1)]
    reserved = [(i, _parse_row(raw, i, project, report, withheld=True)) for i, raw in enumerate(withheld_rows, 1)]
    selected = [(i, row) for i, row in selected if row is not None]
    reserved = [(i, row) for i, row in reserved if row is not None]
    labels = {row["schedule"] for _, row in selected if row.get("schedule")}
    chosen = schedule_name or (settings.get("schedule") if schedule is None or not labels else None)
    if not chosen and len(labels) == 1:
        chosen = next(iter(labels))
    if labels and (len(labels) != 1 or chosen not in labels):
        report.errors.append(f"schedule: inconsistent schedule names {sorted(labels)} (requested {chosen!r})")
    if compiled and not chosen:
        names = {rule.schedule for rule in compiled if rule.purpose == "assimilation"}
        if len(names) == 1:
            chosen = next(iter(names))
        else:
            report.errors.append(f"schedule: choose one rules schedule from {sorted(names)}")
    active = [r for r in compiled if r.purpose == "assimilation" and r.schedule == chosen]
    if compiled and not active:
        report.errors.append(f"schedule: no assimilation rules for {chosen!r}")
    report.checks += ["structure, project period and products, observation identity and quality, same-date conflicts",
                      "target frequency, timing tolerance and spacing" if active else "frequency and timing unassessed (no corresponding rule)"]
    if withheld_input:
        report.checks.append("withheld observations and assimilation exclusions")
    requested = defaultdict(set)
    for _, row in selected + reserved:
        if row.get("date"):
            requested[row["variable"]].add(_day(row["date"]))
    shared = None
    if inventory_input is not None:
        shared = defaultdict(list)
        try:
            inventory_rows = _read_table(inventory_input, project_dir)
            validate_inventory(inventory_rows)
            for i, item in enumerate(inventory_rows, 1):
                variable = _value(item, "variable")
                if not variable or (not is_station_variable(variable) and not _value(item, "source_id")):
                    raise ValueError(f"row {i}: expected variable and satellite source_id")
                shared[(variable, _day(item["date"]).isoformat())].append(item)
        except (ValueError, OSError, KeyError, TypeError) as exc:
            report.errors.append(f"inventory: {exc}")
            return report
    current = _observations(project_dir, start, end, requested, report)
    actual = {_day(row["date"]) for _, row in selected if row.get("date")}
    try:
        windows = _windows(project, timestep, actual)
    except ValueError as exc:
        dates = ", ".join(f"row {i} date {r['date']}" for i, r in selected if r.get("date"))
        report.errors.append(f"propagation windows ({dates}): {exc}")
        windows = {}

    holds = []
    assigned = {}
    occupied = {}
    assigned_dates = defaultdict(list)
    evaluation_dates = defaultdict(list)
    evaluation_counts = defaultdict(int)
    for is_withheld, entries in ((True, reserved), (False, selected)):
        for i, row in entries:
            variable = row.get("variable", "")
            label = f"{'withheld' if is_withheld else 'schedule'} row {i} ({variable}, {row['date'] or row.get('target_date')})"
            rule, target = None, None
            if active and not is_withheld:
                rule, target = _rule_for(row, active, report, label)
                if target:
                    key = (variable, target)
                    if key in assigned:
                        report.errors.append(f"{label} target_date: already assigned on row {assigned[key]}")
                    assigned[key] = i
            if row.get("target_date") and not start <= _day(row["target_date"]) <= end:
                report.errors.append(f"{label} target_date: outside project period {start} through {end}")
            if not row["date"]:
                report.warnings.append(f"{label} date: missed target {row['target_date']}")
                continue
            day = _day(row["date"])
            if not start <= day <= end:
                report.errors.append(f"{label} date: outside project period {start} through {end}")
            if not is_withheld:
                if day in occupied:
                    report.errors.append(f"{label} date: Duplicate assimilation event date {day}; conflicts with {occupied[day]}")
                occupied[day] = f"row {i} ({variable})"
                assigned_dates[variable].append((day, i, rule.min_spacing_days if rule else 0))
            else:
                matching = [r for r in compiled if r.purpose == "evaluation" and r.variable == variable and r.start <= day <= r.end]
                if compiled and any(r.purpose == "evaluation" and r.variable == variable for r in compiled) and not matching:
                    report.errors.append(f"{label} date: outside evaluation periods")
                rule = matching[0] if matching else None
                if rule:
                    evaluation_dates[variable].append((day, i, rule.min_spacing_days))
                    key = (rule.variable, rule.start, rule.end)
                    evaluation_counts[key] += 1
                    if evaluation_counts[key] > rule.count:
                        report.errors.append(f"{label} date: exceeds requested evaluation count {rule.count}")
                    if rule.strategy == "best_per_month" and any(
                        rule.start <= hday <= rule.end and (hday.year, hday.month) == (day.year, day.month)
                        for hday, index, _ in evaluation_dates[variable] if index != i
                    ):
                        report.errors.append(f"{label} date: evaluation strategy allows one observation per month")
            try:
                obs = _current_observation(row, current, shared)
                if _value(obs, "product") and _value(obs, "product").upper() != row.get("product"):
                    raise ValueError(f"product: observation product {obs['product']} differs from project")
                if not is_withheld and day in windows:
                    try:
                        _match_update(obs, windows[day], timestep, setup.get("timezone"))
                    except ValueError as exc:
                        report.errors.append(f"{label} observation_time: {exc}")
                limits = inherited_evaluation_limits(compiled, variable, day) if is_withheld else None
                for issue in quality_issues(obs, rule, extra_limits=limits):
                    report.errors.append(f"{label} quality: {issue}")
                if is_withheld:
                    if any(matches_holdout(obs, h) for h in holds):
                        report.errors.append(f"{label} source_id: duplicate withheld observation")
                    holds.append(obs)
                    continue
                if any(matches_holdout(obs, h) for h in holds):
                    suggestion = (
                        _suggest_replacement(project_dir, project, shared, rule, target,
                                             [r for j, r in selected if j != i], holds, active,
                                             start, end, timestep, setup.get("timezone"))
                        if rule and target else "no replacement proposed; target and timing tolerance unassessed"
                    )
                    report.errors.append(f"{label} source_id: withheld for evaluation; {suggestion}")
                event = {key: row[key] for key in ("date", "variable", "product")}
                if _value(obs, "source_id"):
                    event["source_id"] = _value(obs, "source_id")
                for key in ("observation_time", "station_ids"):
                    if _value(obs, key):
                        event[key] = _value(obs, key)
                report.events.append(event)
            except (OSError, TypeError, ValueError, KeyError) as exc:
                report.errors.append(f"{label}: {exc}")
    for series_name, series in (("schedule", assigned_dates), ("withheld", evaluation_dates)):
        for variable, dated in series.items():
            dated.sort()
            for (a, ai, asp), (b, bi, bsp) in zip(dated, dated[1:]):
                if (b - a).days < max(asp, bsp):
                    report.errors.append(f"{series_name} row {bi} date: {variable} only {(b-a).days} days "
                                         f"after row {ai}; minimum {max(asp, bsp)}")
    for rule in compiled:
        if rule.purpose == "evaluation":
            count = evaluation_counts[(rule.variable, rule.start, rule.end)]
            if count < rule.count:
                report.warnings.append(f"withheld {rule.variable}, {rule.start} to {rule.end}: {count}/{rule.count} selected")
    for rule in active:
        for target in target_dates(rule):
            if (rule.variable, target) not in assigned:
                report.warnings.append(f"missing target {target} for {rule.variable}")
    if not rows and not active:
        report.errors.append("schedule: empty, expected date and variable rows")
    report.events.sort(key=lambda event: event["date"])
    return report


def project_event_rows(project_dir, project):
    """Resolve a reviewed schedule at the same boundary used by the CLI."""
    da = project.get("data_assimilation") or {}
    if not reviewed_schedule(da):
        return da.get("assimilation_events")
    report = validate_schedule(project_dir, project=project)
    if report.errors:
        raise ValueError("\n".join(report.errors))
    if not report.events:
        raise ValueError("No assimilation_events assigned for this project")
    return report.events


def _validate_command(args):
    failed = False
    for path in args.schedule or [None]:
        report = validate_schedule(args.project_dir, schedule=path, rules=args.rules, withheld=args.withheld,
                                   inventory=args.inventory, schedule_name=args.schedule_name, year=args.year)
        print(f"{path or args.project_dir}: {len(report.events)} assigned event(s)")
        for prefix, items in (("CHECK", report.checks), ("WARNING", report.warnings), ("ERROR", report.errors)):
            for item in items:
                print(f"{prefix} {item}")
        failed |= bool(report.errors)
    return int(failed)
