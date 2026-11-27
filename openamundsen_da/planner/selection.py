"""Calendar rules and observation-only selection for editable project schedules."""

from __future__ import annotations

import calendar
import json
import math
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Iterable, Mapping

from openamundsen_da.util.observation_time import parse_utc_timestamp
from openamundsen_da.util.station_da import is_station_variable

Row = Mapping[str, Any]
IDENTITY = ("variable", "date", "observation_time", "source_id", "station_ids")
SCHEDULE_FIELDS = (
    *IDENTITY,
    "schedule",
    "target_date",
    "status",
    "reason",
    "preferred_date",
    "conflict_with",
    "reschedule",
    "offset_days",
    "tolerance_days",
    "min_spacing_days",
    "quality_for_ranking",
    "effective_limits",
    "available",
    "eligible",
    "withheld",
)


@dataclass(frozen=True)
class Rule:
    """One active selection period, resolved to a particular winter."""

    purpose: str
    variable: str
    start: date
    end: date
    schedule: str = ""
    frequency: int | str = ""
    tolerance_days: int = 0
    priority: int = 0
    reschedule: bool = True
    min_spacing_days: int = 1
    count: int | None = None
    ranking: str = "quality"
    strategy: str = ""
    limits: dict[str, float] = field(default_factory=dict)


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _integer(value: Any, name: str, minimum: int) -> int:
    if not re.fullmatch(r"[0-9]+", _text(value)) or int(value) < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}, got {value!r}")
    return int(value)


def _period_date(value: Any, year: int) -> date:
    token = _text(value)
    match = re.fullmatch(r"(\d{1,2})-(\d\d)-(\d\d|last)", token)
    if match is None:
        return date.fromisoformat(token)
    offset, month, day = match.groups()
    year += int(offset)
    day_number = calendar.monthrange(year, int(month))[1] if day == "last" else int(day)
    return date(year, int(month), day_number)


def _number(value: Any, name: str) -> float:
    try:
        result = float(value)
    except (ValueError, TypeError):
        raise ValueError(f"{name} must be a finite number, got {value!r}") from None
    if not math.isfinite(result):
        raise ValueError(f"{name} must be a finite number, got {value!r}")
    return result


def _limits(row: Row) -> dict[str, float]:
    limits = {
        key: _number(value, key)
        for key, value in row.items()
        if key.startswith(("min_", "max_")) and key != "min_spacing_days" and _text(value)
    }
    for name, value in limits.items():
        measure = name[4:]
        if not measure:
            raise ValueError(f"{name} requires a quality measure name")
        if name.startswith("min_") and value > limits.get(f"max_{measure}", value):
            raise ValueError(f"{name} exceeds max_{measure}")
        if measure == "support_coverage_ratio" and not 0 <= value <= 1:
            raise ValueError(f"{name} must be between zero and one")
        if measure in {"station_count", "n_wet", "wet_bands"} and (value < 0 or not value.is_integer()):
            raise ValueError(f"{name} must be a nonnegative integer")
    return limits


def _compile_rule(row: Row, year: int) -> Rule:
    purpose = _text(row.get("purpose"))
    variable = _text(row.get("variable"))
    if not variable or purpose not in {"assimilation", "evaluation"}:
        raise ValueError("expected variable and purpose (assimilation or evaluation)")
    if variable != variable.lower() or (
        variable not in {"scf", "wet_snow", "wet_snow_line"} and not is_station_variable(variable)
    ):
        raise ValueError(f"unsupported variable {variable!r}")
    common_fields = {
        "purpose",
        "variable",
        "year",
        "period_start",
        "period_end",
        "min_spacing_days",
        "count",
        "ranking",
    }
    purpose_fields = (
        {"strategy"}
        if purpose == "evaluation"
        else {
            "schedule",
            "frequency",
            "tolerance_days",
            "priority",
            "reschedule",
        }
    )
    unexpected = [
        k
        for k, value in row.items()
        if _text(value) and k not in common_fields | purpose_fields and not k.startswith(("min_", "max_"))
    ]
    if unexpected:
        raise ValueError(f"unexpected fields for {purpose}: {', '.join(unexpected)}")
    bounds = []
    for key in ("period_start", "period_end"):
        try:
            bounds.append(_period_date(row.get(key), year))
        except ValueError as exc:
            raise ValueError(f"{key}: {exc}") from exc
    start, end = bounds
    if start > end:
        raise ValueError(f"period_end {end} precedes period_start {start}")
    station_update = purpose == "assimilation" and is_station_variable(variable)
    ranking = _text(row.get("ranking")) or ("nearest" if station_update else "quality")
    if station_update and ranking != "nearest":
        raise ValueError("station assimilation requires ranking=nearest")
    if purpose == "evaluation" and ranking != "quality":
        raise ValueError("evaluation uses quality ranking with its configured holdout strategy")
    if ranking not in {"quality", "nearest"}:
        raise ValueError("ranking must be quality or nearest")
    count = _integer(row["count"], "count", 1) if _text(row.get("count")) else None
    common = dict(
        purpose=purpose,
        variable=variable,
        start=start,
        end=end,
        ranking=ranking,
        count=count,
        min_spacing_days=_integer(_text(row.get("min_spacing_days")) or 1, "min_spacing_days", 1),
        limits=_limits(row),
    )
    if purpose == "evaluation":
        strategy = _text(row.get("strategy"))
        if strategy not in {"best", "best_per_month", "spread"} or count is None:
            raise ValueError("evaluation requires count and strategy (best, best_per_month or spread)")
        return Rule(**common, strategy=strategy)
    frequency = _text(row.get("frequency"))
    if frequency != "monthly":
        frequency = _integer(frequency, "frequency", 1)
    schedule = _text(row.get("schedule"))
    if not schedule:
        raise ValueError("schedule is required")
    reschedule = _text(row.get("reschedule")).lower()
    if reschedule not in {"true", "false"}:
        raise ValueError("reschedule must be true or false")
    rule = Rule(
        **common,
        schedule=schedule,
        frequency=frequency,
        priority=_integer(row.get("priority"), "priority", 1),
        tolerance_days=_integer(row.get("tolerance_days"), "tolerance_days", 0),
        reschedule=reschedule == "true",
    )
    if len(target_dates(rule)) < (count or 0):
        raise ValueError("count exceeds the number of prescribed targets within this period")
    return rule


def compile_rules(rows: Iterable[Row], year: int) -> list[Rule]:
    """Validate CSV/YAML rule rows and resolve relative calendar bounds."""
    rules = []
    periods = defaultdict(list)
    priorities = {}
    year = _integer(year, "year", 1)
    for index, row in enumerate(rows, 1):
        try:
            if not isinstance(row, Mapping):
                raise ValueError("expected a mapping")
            row_year = _integer(row["year"], "year", 1) if _text(row.get("year")) else year
            rule = _compile_rule(row, row_year)
            if row_year != year:
                continue
            key = (rule.purpose, rule.schedule, rule.variable)
            if any(prior.start <= rule.end and rule.start <= prior.end for prior in periods[key]):
                raise ValueError(f"overlapping periods for {rule.variable} {rule.schedule}")
            if rule.purpose == "assimilation":
                if rule.variable in priorities and priorities[rule.variable] != rule.priority:
                    raise ValueError(f"inconsistent priority for {rule.variable}")
                if rule.variable not in priorities and rule.priority in priorities.values():
                    raise ValueError(f"priority {rule.priority} names more than one variable")
                priorities[rule.variable] = rule.priority
                if any(
                    prior.purpose == "assimilation" and prior.reschedule != rule.reschedule for prior in rules
                ):
                    raise ValueError("use one reschedule setting across all assimilation rules")
            periods[key].append(rule)
            rules.append(rule)
        except (KeyError, ValueError, TypeError) as exc:
            raise ValueError(f"rules row {index}: {exc}") from exc
    if not rules:
        raise ValueError(f"no selection rules for {year}")
    return rules


def target_dates(rule: Rule) -> list[date]:
    """Keep the period anchor through gaps and through short calendar months."""
    if rule.purpose != "assimilation":
        return []
    result = []
    current = rule.start
    while current <= rule.end and (rule.count is None or len(result) < rule.count):
        result.append(current)
        if rule.frequency == "monthly":
            year = current.year + (current.month == 12)
            month = current.month % 12 + 1
            current = date(year, month, min(rule.start.day, calendar.monthrange(year, month)[1]))
        else:
            current += timedelta(days=int(rule.frequency))
    return result


def effective_limits(
    observation: Row, rule: Rule | None, extra_limits: Row | None = None
) -> dict[str, float]:
    """Combine every applicable lower/upper bound using its strictest value."""
    result = {}
    inherited = {
        key.removeprefix("project_"): value
        for key, value in observation.items()
        if key.startswith(("project_min_", "project_max_")) and _text(value)
    }
    for group in (inherited, rule.limits if rule else {}, extra_limits or {}):
        for name, limit in group.items():
            value = _number(limit, name)
            combine = max if name.startswith("min_") else min
            result[name] = combine(result.get(name, value), value)
    return result


def station_ids(row: Row) -> set[str]:
    value = row.get("station_ids") or ""
    values = value.split("|") if isinstance(value, str) else value
    return {_text(item).casefold() for item in values if _text(item)}


def quality_issues(observation: Row, rule: Rule | None, *, extra_limits: Row | None = None) -> list[str]:
    """Return measured values and failed bounds; absent required measures fail closed."""
    issues = []
    if _text(observation.get("quality_issue")):
        issues.append(str(observation["quality_issue"]))
    if is_station_variable(observation.get("variable")) and not station_ids(observation):
        issues.append("no contributing station_ids")
    for name, limit in effective_limits(observation, rule, extra_limits).items():
        direction, measure = name.split("_", 1)
        raw = observation.get(measure)
        if measure == "station_count" and "station_ids" in observation:
            raw = len(station_ids(observation))
        try:
            measured = _number(raw, measure)
        except ValueError:
            issues.append(f"{measure} unavailable ({name}={limit:g})")
            continue
        if (direction == "min" and measured < limit) or (direction == "max" and measured > limit):
            issues.append(f"{measure}={measured:g} fails {name}={limit:g}")
    return issues


def matches_holdout(observation: Row, holdout: Row) -> bool:
    """Match an acquisition, including a reserved radar scene used by another operator."""
    if _text(observation.get("date")) != _text(holdout.get("date")):
        return False
    other_variable = observation.get("variable") != holdout.get("variable")
    if other_variable:
        same_radar_product = {observation.get("variable"), holdout.get("variable")} == {
            "wet_snow",
            "wet_snow_line",
        }
        if not same_radar_product or not _text(holdout.get("source_id")):
            return False
    if _text(holdout.get("source_id")) and _text(observation.get("source_id")) != _text(holdout["source_id"]):
        return False
    if _text(holdout.get("observation_time")):
        if not _text(observation.get("observation_time")):
            return other_variable
        if parse_utc_timestamp(
            observation["observation_time"], field="observation_time"
        ) != parse_utc_timestamp(
            holdout["observation_time"],
            field="observation_time",
        ):
            return False
    reserved = station_ids(holdout)
    return not reserved or bool(reserved & station_ids(observation))


def unreserved_candidate(observation: Row, holdouts: list[Row]) -> Row | None:
    """Keep the available station readings, or exclude a fully reserved acquisition."""
    matching = [h for h in holdouts if matches_holdout(observation, h)]
    if not matching:
        return observation
    if not is_station_variable(observation.get("variable")) or any(not station_ids(h) for h in matching):
        return None
    reserved = set().union(*(station_ids(h) for h in matching))
    available = sorted(station_ids(observation) - reserved)
    if not available:
        return None
    return {
        **observation,
        "station_ids": "|".join(available),
        "station_count": len(available),
        "quality_for_ranking": len(available),
    }


def validate_inventory(inventory: list[Row]) -> None:
    """Check acquisition identity without discarding candidates with missing quality measures."""
    seen = set()
    for index, row in enumerate(inventory, 1):
        label = f"inventory row {index}"
        variable = _text(row.get("variable"))
        if not variable:
            raise ValueError(f"{label} variable: required")
        try:
            day = date.fromisoformat(_text(row.get("date")))
        except ValueError as exc:
            raise ValueError(f"{label} date: expected YYYY-MM-DD") from exc
        stamp = None
        if _text(row.get("observation_time")):
            stamp = parse_utc_timestamp(row["observation_time"], field=f"{label} observation_time")
            if not is_station_variable(variable) and stamp.date() != day:
                raise ValueError(f"{label} observation_time: UTC date differs from {day}")
        identity = (variable, day, stamp, _text(row.get("source_id")))
        if identity in seen:
            raise ValueError(
                f"{label}: duplicate observation for {variable} on {day} with this time and source_id"
            )
        seen.add(identity)


def _resolve_holdouts(inventory: list[Row], holdouts: list[Row]) -> list[dict]:
    """Resolve edited reservations against the inventory before assigning any target."""
    resolved = []
    for index, holdout in enumerate(holdouts, 1):
        variable = _text(holdout.get("variable"))
        day = _text(holdout.get("date"))
        candidates = [
            row for row in inventory if row.get("variable") == variable and matches_holdout(row, holdout)
        ]
        if len(candidates) != 1:
            raise ValueError(
                f"withheld row {index} ({variable}, {day}): {len(candidates)} matching inventory observations; "
                "check the date, variable, observation_time, source_id and station_ids"
            )
        candidate = dict(candidates[0])
        if station_ids(holdout):
            missing = station_ids(holdout) - station_ids(candidate)
            if missing:
                raise ValueError(
                    f"withheld row {index} station_ids: unavailable: {', '.join(sorted(missing))}"
                )
            candidate["station_ids"] = holdout["station_ids"]
        if any(
            previous["variable"] == variable and matches_holdout(candidate, previous) for previous in resolved
        ):
            raise ValueError(f"withheld row {index}: duplicate reservation for {variable} on {day}")
        resolved.append(candidate)
    return resolved


def screening_issues(observation: Row, rule: Rule, extra_limits: Row | None = None) -> list[str]:
    issues = quality_issues(observation, rule, extra_limits=extra_limits)
    if rule.ranking == "quality":
        try:
            _number(observation.get("quality_for_ranking"), "quality_for_ranking")
        except ValueError:
            issues.append("quality_for_ranking unavailable")
    return issues


def candidate_rank(row: Row, rule: Rule, target: date) -> tuple:
    day = date.fromisoformat(str(row["date"]))
    score = float(row["quality_for_ranking"]) if rule.ranking == "quality" else 0
    return (
        -score,
        abs((day - target).days),
        day,
        _text(row.get("observation_time")),
        _text(row.get("source_id")),
    )


def generate_schedule(
    inventory: list[Row],
    rules: list[Rule],
    schedule: str,
    holdouts: list[Row],
    *,
    exclusions: list[dict] | None = None,
) -> list[dict]:
    """Assign each fixed target in priority order and retain every missed target."""
    active = [r for r in rules if r.purpose == "assimilation" and r.schedule == schedule]
    if not active:
        raise ValueError(f"no assimilation rules for schedule {schedule!r}")
    validate_inventory(inventory)
    holdouts = _resolve_holdouts(inventory, holdouts)
    used = {}
    prior_dates = defaultdict(list)
    events = []
    for rule in sorted(active, key=lambda r: (r.priority, r.start)):
        pool = []
        for obs in inventory:
            if obs.get("variable") != rule.variable:
                continue
            day = date.fromisoformat(str(obs["date"]))
            if rule.start <= day <= rule.end:
                available_obs = unreserved_candidate(obs, holdouts)
                reserved = available_obs is None
                withheld_reason = ""
                if reserved:
                    withheld_reason = "withheld"
                elif available_obs is not obs:
                    excluded_ids = sorted(station_ids(obs) - station_ids(available_obs))
                    withheld_reason = "withheld stations: " + ", ".join(excluded_ids)
                    obs = available_obs
                issues = screening_issues(obs, rule)
                pool.append((day, obs, issues, reserved))
                if exclusions is not None and (issues or withheld_reason):
                    reasons = [*issues, withheld_reason] if withheld_reason else issues
                    exclusions.append(
                        {
                            **{k: obs.get(k, "") for k in IDENTITY},
                            "schedule": schedule,
                            "period_start": str(rule.start),
                            "period_end": str(rule.end),
                            "reason": " | ".join(reasons),
                            "effective_limits": json.dumps(effective_limits(obs, rule), sort_keys=True),
                        }
                    )
        for target in target_dates(rule):
            nearby = [entry for entry in pool if abs((entry[0] - target).days) <= rule.tolerance_days]
            qualified = [entry for entry in nearby if not entry[2]]
            available = [entry for entry in qualified if not entry[3]]
            choices = sorted(
                (
                    (d, obs)
                    for d, obs, _, _ in available
                    if all(
                        abs((d - old).days) >= max(rule.min_spacing_days, spacing)
                        for old, spacing in prior_dates[rule.variable]
                    )
                ),
                key=lambda item: candidate_rank(item[1], rule, target),
            )
            reason = ""
            conflict = ""
            selected = None
            preferred = choices[0][0] if choices else None
            if not nearby:
                reason = "no observations within timing tolerance and active period"
            elif not qualified:
                reason = "no observation passed quality screening"
            elif not available:
                reason = "all eligible observations withheld"
            elif not choices:
                reason = "no eligible observation meets minimum spacing"
            elif preferred in used:
                conflict = used[preferred]
                if rule.reschedule:
                    selected = next((entry for entry in choices if entry[0] not in used), None)
                if selected is None:
                    reason = (
                        "no free eligible date within the original constraints"
                        if rule.reschedule
                        else "same-date rescheduling disabled"
                    )
            else:
                selected = choices[0]
            actual, obs = selected if selected else (None, {})
            if actual:
                used[actual] = rule.variable
                prior_dates[rule.variable].append((actual, rule.min_spacing_days))
            events.append(
                {
                    **{k: obs.get(k, "") for k in IDENTITY},
                    "variable": rule.variable,
                    "date": str(actual) if actual else "",
                    "schedule": schedule,
                    "target_date": str(target),
                    "status": ("shifted" if conflict else "selected") if selected else "missed",
                    "reason": reason,
                    "preferred_date": str(preferred) if preferred else "",
                    "conflict_with": conflict,
                    "reschedule": rule.reschedule,
                    "offset_days": (actual - target).days if actual else "",
                    "tolerance_days": rule.tolerance_days,
                    "min_spacing_days": rule.min_spacing_days,
                    "quality_for_ranking": obs.get("quality_for_ranking", ""),
                    "effective_limits": json.dumps(effective_limits(obs, rule), sort_keys=True),
                    "available": len(nearby),
                    "eligible": len(qualified),
                    "withheld": len(qualified) - len(available),
                }
            )
    return sorted(events, key=lambda row: (row["target_date"], row["variable"]))
