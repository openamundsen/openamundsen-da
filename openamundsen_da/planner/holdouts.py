"""Reserve observations for shared evaluation before generating assimilation schedules."""

from __future__ import annotations

from bisect import bisect_left
from collections import defaultdict
from datetime import date, timedelta
import json

from .selection import IDENTITY, Row, Rule, effective_limits, screening_issues, validate_inventory

HOLDOUT_FIELDS = (*IDENTITY, "quality_for_ranking", "effective_limits", "strategy")
SUMMARY_FIELDS = (
    "purpose",
    "variable",
    "period_start",
    "period_end",
    "requested",
    "selected",
    "min_spacing_days",
    "achieved_min_spacing_days",
    "reason",
)


def inherited_evaluation_limits(rules: list[Rule], variable: str, day: date) -> dict:
    """Evaluation inherits the tightest applicable assimilation bounds across schedules."""
    related = [r for r in rules if r.variable == variable and r.purpose == "assimilation"]
    active = [r for r in related if r.start <= day <= r.end]
    limits = {}
    for rule in active or related:
        limits = effective_limits({}, rule, limits)
    return limits


def _best_dates(days: list[date], scores: list[float], rule: Rule) -> tuple[date, ...]:
    """Optimize cardinality first, then the stated selection objective and earlier dates.

    A suffix table over dates and remaining picks avoids enumerating combinations.
    At each date, selection jumps directly to the next permitted date/month.
    """
    if not days:
        return ()
    successors = []
    for day in days:
        earliest = day + timedelta(days=rule.min_spacing_days)
        if rule.strategy == "best_per_month":
            following_month = date(day.year + (day.month == 12), day.month % 12 + 1, 1)
            earliest = max(earliest, following_month)
        successors.append(bisect_left(days, earliest))
    # Earliest-first maximizes the achievable count for these monotone separation constraints.
    available = 0
    index = 0
    while index < len(days):
        available += 1
        index = successors[index]
    count = min(rule.count, available)
    span = (days[-1] - days[0]).days
    # Each state holds (total fractional-target distance, negative quality sum, chosen dates).
    previous = [(0, 0.0, ())] * (len(days) + 1)
    for remaining in range(1, count + 1):
        current = [None] * (len(days) + 1)
        ordinal = count - remaining + 1
        for i in reversed(range(len(days))):
            skip = current[i + 1]
            tail = previous[successors[i]]
            if tail is None:
                current[i] = skip
                continue
            distance = (
                abs((count + 1) * (days[i] - days[0]).days - ordinal * span)
                if rule.strategy == "spread"
                else 0
            )
            take = (distance + tail[0], -scores[i] + tail[1], (days[i], *tail[2]))
            current[i] = take if skip is None or take < skip else skip
        previous = current
    return previous[0][2]


def generate_holdouts(
    inventory: list[Row],
    rules: list[Rule],
    *,
    summary: list[dict] | None = None,
    exclusions: list[dict] | None = None,
) -> list[dict]:
    """Return common evaluation acquisitions, recording achieved counts and spacing."""
    validate_inventory(inventory)
    selected = []
    prior = defaultdict(list)
    for rule in sorted((r for r in rules if r.purpose == "evaluation"), key=lambda r: (r.variable, r.start)):
        by_date = {}
        for observation in inventory:
            if observation.get("variable") != rule.variable:
                continue
            day = date.fromisoformat(str(observation["date"]))
            if not rule.start <= day <= rule.end:
                continue
            inherited = inherited_evaluation_limits(rules, rule.variable, day)
            issues = screening_issues(observation, rule, inherited)
            limits = json.dumps(effective_limits(observation, rule, inherited), sort_keys=True)
            if any(
                abs((day - old).days) < max(spacing, rule.min_spacing_days)
                for old, spacing in prior[rule.variable]
            ):
                issues.append("minimum spacing from prior evaluation period")
            if issues:
                if exclusions is not None:
                    exclusions.append(
                        {
                            **{k: observation.get(k, "") for k in IDENTITY},
                            "period_start": str(rule.start),
                            "period_end": str(rule.end),
                            "reason": " | ".join(issues),
                            "effective_limits": limits,
                        }
                    )
                continue
            score = float(observation["quality_for_ranking"]) if rule.ranking == "quality" else 0.0
            order = (
                -score,
                str(observation.get("observation_time") or ""),
                str(observation.get("source_id") or ""),
            )
            if day not in by_date or order < by_date[day][0]:
                by_date[day] = (order, observation, limits)
        days = sorted(by_date)
        choices = _best_dates(days, [-by_date[d][0][0] for d in days], rule)
        for day in choices:
            _, obs, limits = by_date[day]
            prior[rule.variable].append((day, rule.min_spacing_days))
            selected.append(
                {
                    **{k: obs.get(k, "") for k in IDENTITY},
                    "quality_for_ranking": obs.get("quality_for_ranking", ""),
                    "effective_limits": limits,
                    "strategy": rule.strategy,
                }
            )
        if summary is not None:
            if len(choices) == rule.count:
                reason = ""
            elif len(by_date) < rule.count:
                reason = "insufficient eligible dates in the evaluation period"
            else:
                reason = "insufficient eligible dates satisfying spacing or distinct-month constraints"
            summary.append(
                {
                    "purpose": "evaluation",
                    "variable": rule.variable,
                    "period_start": str(rule.start),
                    "period_end": str(rule.end),
                    "requested": rule.count,
                    "selected": len(choices),
                    "min_spacing_days": rule.min_spacing_days,
                    "achieved_min_spacing_days": min(
                        ((b - a).days for a, b in zip(choices, choices[1:])), default=""
                    ),
                    "reason": reason,
                }
            )
    return sorted(selected, key=lambda row: (str(row["date"]), row["variable"]))


def schedule_summary(rows: list[Row], rules: list[Rule], schedule: str) -> list[dict]:
    """Summarize each prescription independently after selection."""
    result = []
    for rule in rules:
        if rule.purpose != "assimilation" or rule.schedule != schedule:
            continue
        period = [
            r
            for r in rows
            if r["variable"] == rule.variable
            and rule.start <= date.fromisoformat(str(r["target_date"])) <= rule.end
        ]
        dates = sorted(date.fromisoformat(str(r["date"])) for r in period if r.get("date"))
        reasons = sorted({r["reason"] for r in period if r.get("reason")})
        result.append(
            {
                "purpose": "assimilation",
                "variable": rule.variable,
                "period_start": str(rule.start),
                "period_end": str(rule.end),
                "requested": len(period),
                "selected": len(dates),
                "min_spacing_days": rule.min_spacing_days,
                "achieved_min_spacing_days": min(
                    ((b - a).days for a, b in zip(dates, dates[1:])), default=""
                ),
                "reason": " | ".join(reasons),
            }
        )
    return result
