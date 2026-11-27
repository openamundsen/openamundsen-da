"""Refresh the reports for a validated, manually reviewed period schedule."""

from __future__ import annotations

import json
from datetime import date

from openamundsen_da.util.observation_time import parse_utc_timestamp

from .holdouts import schedule_summary
from .layout import report_paths, schedule_table_path
from .selection import IDENTITY, effective_limits, generate_schedule, station_ids, target_dates
from .validation import ScheduleReport, validate_schedule


def _acquisition(row):
    stamp = row.get("observation_time")
    return (
        str(row["date"]), row["variable"], row.get("source_id") or "",
        parse_utc_timestamp(stamp, field="observation_time") if stamp else None,
    )


def _description(row):
    identity = [str(row["date"])]
    identity.extend(str(row[key]) for key in ("observation_time", "source_id", "station_ids") if row.get(key))
    return " ".join(identity)


def _reviewed_targets(report: ScheduleReport, name: str):
    exclusions = []
    targets = generate_schedule(report.inventory, report.rules, name, report.withheld or [], exclusions=exclusions)
    rules = {
        (rule.variable, target.isoformat()): rule for rule in report.rules for target in target_dates(rule)
    }
    reviewed = {_acquisition(obs): obs for obs in report.assignments.values() if obs.get("date")}
    for row in targets:
        key = (row["variable"], row["target_date"])
        rule = rules[key]
        observation = report.assignments.get(key, {})
        generated_day, reviewed_day = row["date"], observation.get("date")
        reason = row["reason"]
        if reviewed_day:
            changed = (
                not generated_day or _acquisition(row) != _acquisition(observation)
                or station_ids(row) != station_ids(observation)
            )
            if changed:
                reason = (
                    f"Manual replacement of generated update {_description(row)}"
                    if generated_day else "Manual addition to a target left empty during generation"
                )
        else:
            changed = bool(generated_day)
            if changed:
                reason = f"Manual removal of generated update {_description(row)}"
        if changed:
            if generated_day:
                retained = reviewed.get(_acquisition(row))
                removed_ids = station_ids(row) - station_ids(retained or {})
                if retained is None or removed_ids:
                    excluded = {field: row[field] for field in IDENTITY}
                    if removed_ids:
                        excluded["station_ids"] = "|".join(sorted(removed_ids))
                    exclusions.append({
                        **excluded, "schedule": name, "period_start": str(rule.start), "period_end": str(rule.end),
                        "reason": reason, "effective_limits": row["effective_limits"],
                    })
            row.update(status="selected" if reviewed_day else "missed", preferred_date="", conflict_with="")
        if reviewed_day or changed:
            for field in IDENTITY:
                if field != "variable":
                    row[field] = observation.get(field, "")
            row["offset_days"] = (date.fromisoformat(str(reviewed_day)) - date.fromisoformat(key[1])).days if reviewed_day else ""
            row["quality_for_ranking"] = observation.get("quality_for_ranking", "")
            row["effective_limits"] = json.dumps(effective_limits(observation, rule), sort_keys=True)
        notes = [reason]
        if observation.get("reason") and observation["reason"] != reason:
            notes.append(observation["reason"])
        if observation.get("timing_exception_reason"):
            notes.append(f"Timing exception: {observation['timing_exception_reason']}")
        row["reason"] = ". ".join(filter(None, notes))
    return targets, exclusions


def refresh_report(args):
    from .reporting import write_schedule_reports

    directory = args.period.resolve()
    schedule_path = schedule_table_path(directory, args.schedule)
    report = validate_schedule(args.project_dir, period=directory, schedule=schedule_path, schedule_name=args.schedule)
    for warning in report.warnings:
        print(f"WARNING {warning}")
    if report.errors:
        raise ValueError("\n".join(report.errors))
    targets, exclusions = _reviewed_targets(report, args.schedule)
    summary = schedule_summary(targets, report.rules, args.schedule)
    pdf_path, detail_path = report_paths(directory, schedule_path)
    write_schedule_reports(
        pdf_path, detail_path, label=report.period.name, schedule=args.schedule, targets=targets,
        summary=summary, exclusions=exclusions, withheld=report.withheld, rules=report.rules, overwrite=True,
    )
    print(pdf_path)
    print(detail_path)
    return 0
