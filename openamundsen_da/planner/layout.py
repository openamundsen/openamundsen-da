"""Resolve planning periods and named selection rules from a planner directory."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from openamundsen_da.planner.selection import Rule, compile_rules
from openamundsen_da.planner.tables import read_rows
from openamundsen_da.util.yaml_utils import read_yaml_mapping


@dataclass(frozen=True)
class PlanningPeriod:
    """Inclusive dates and display label for one planning period."""

    name: str
    start: date
    end: date


def read_period(period_dir: Path) -> PlanningPeriod:
    """Read and validate period.yml, using the directory name as the default label."""
    directory = Path(period_dir).resolve()
    path = directory / "period.yml"
    config = read_yaml_mapping(path, error_cls=ValueError, context="planning period")
    if config.keys() - {"start", "end", "label"} or not {"start", "end"} <= config.keys():
        raise ValueError(f"{path}: expected start, end and optional label")
    label = config.get("label", directory.name)
    if not isinstance(label, str) or not label.strip():
        raise ValueError(f"{path}: label must be nonempty text")
    bounds = []
    for key in ("start", "end"):
        raw = str(config[key])
        try:
            day = date.fromisoformat(raw)
            if day.isoformat() != raw:
                raise ValueError
        except ValueError as exc:
            raise ValueError(f"{path}: {key} must be a YYYY-MM-DD date, got {raw!r}") from exc
        bounds.append(day)
    start, end = bounds
    if start > end:
        raise ValueError(f"{path}: end {end} precedes start {start}")
    return PlanningPeriod(label.strip(), start, end)


def _check_name(name: str) -> None:
    if (
        not isinstance(name, str) or not name.strip() or name != name.strip()
        or name in {".", ".."} or Path(name).name != name or "\\" in name
    ):
        raise ValueError(f"expected a single directory or file name, got {name!r}")


def schedule_rules_path(period_dir: Path, schedule: str) -> Path:
    """Locate a named schedule's rules from a directory beneath planner/periods."""
    directory = Path(period_dir).resolve()
    if directory.parent.name != "periods":
        raise ValueError(f"{directory}: expected a planning directory inside periods/")
    _check_name(schedule)
    schedules = directory.parent.parent / "schedules"
    flat = schedules / f"{schedule}.yml"
    nested = schedules / schedule / "rules.yml"
    if nested.is_file():
        if flat.is_file():
            raise ValueError(f"Multiple rule files for {schedule}: {flat} and {nested}")
        return nested
    return flat


def table_path(directory: Path, stem: str, *, required: bool = True) -> Path | None:
    """Resolve a table by stem, rejecting ambiguous alternative formats."""
    directory = Path(directory)
    _check_name(stem)
    entries = directory.iterdir() if directory.is_dir() else ()
    matches = sorted(
        path for path in entries
        if path.stem == stem and path.suffix.lower() in {".yml", ".yaml", ".csv"} and path.is_file()
    )
    if len(matches) > 1:
        raise ValueError(f"{directory}: multiple tables for {stem}: {', '.join(path.name for path in matches)}")
    if required and not matches:
        raise ValueError(f"{directory}: missing {stem} table (.yml, .yaml or .csv)")
    return matches[0] if matches else None


def compile_period_rules(
    rows: Iterable[Mapping], year: int, period: PlanningPeriod | None, *, allow_empty: bool = False
) -> list[Rule]:
    """Compile rules, defaulting unspecified calendar bounds to the declared period."""
    if period is None:
        return compile_rules(rows, year, allow_empty=allow_empty)
    if year != period.start.year:
        raise ValueError(f"year {year} differs from planning period start year {period.start.year}")

    # The compiler also checks rows explicitly assigned to other years. Use relative
    # bounds there so a missing endpoint is evaluated on the same calendar as its peer.
    def default_bound(bound):
        offset = bound.year - period.start.year
        if offset > 99:
            return bound
        day = "last" if (bound.month, bound.day) == (2, 29) else f"{bound.day:02d}"
        return f"{offset}-{bound.month:02d}-{day}"

    def bounded_rows():
        for row in rows:
            if not isinstance(row, Mapping):
                yield row  # The rule compiler reports the offending row number.
                continue
            values = dict(row)
            for key, bound in (("period_start", period.start), ("period_end", period.end)):
                if values.get(key) in (None, ""):
                    values[key] = default_bound(bound)
            yield values

    rules = compile_rules(bounded_rows(), year, allow_empty=allow_empty)
    for rule in rules:
        if rule.start < period.start or rule.end > period.end:
            raise ValueError(
                f"{rule.variable} rule {rule.start} through {rule.end} outside planning period "
                f"{period.start} through {period.end}"
            )
    return rules


def _settings(raw, where):
    if not isinstance(raw, dict):
        raise ValueError(f"{where}: expected a mapping")
    for key, value in raw.items():
        if not isinstance(key, str) or not key.strip() or key != key.strip():
            raise ValueError(f"{where}: expected a nonempty field name, got {key!r}")
        if isinstance(value, (list, dict, set, tuple)) or value is None:
            raise ValueError(f"{where}.{key}: expected a scalar setting")
    return raw


def _schedule_rows(document, schedule):
    if document.keys() - {"defaults", "observations"}:
        raise ValueError("expected defaults and observations at the root of schedule rules")
    defaults = _settings(document.get("defaults", {}), "defaults")
    if defaults.keys() - {"frequency", "tolerance_days", "reschedule", "min_spacing_days"}:
        raise ValueError("defaults may contain frequency, tolerance_days, reschedule and min_spacing_days")
    observations = document.get("observations")
    if not isinstance(observations, dict) or not observations:
        raise ValueError("observations must map at least one variable to its rules")
    if not isinstance(schedule, str) or not schedule.strip():
        raise ValueError("structured rules require a schedule name")
    for variable, observation in observations.items():
        context = f"observations.{variable}"
        if not isinstance(observation, dict):
            raise ValueError(f"{context}: expected a mapping")
        options = _settings({k: v for k, v in observation.items() if k != "intervals"}, context)
        intervals = observation.get("intervals", [{}])
        if not isinstance(intervals, list) or not intervals:
            raise ValueError(f"{context}.intervals: expected a nonempty sequence of intervals")
        for index, raw_interval in enumerate(intervals, 1):
            interval = _settings(raw_interval, f"{context}.intervals[{index}]")
            rule = {**defaults, **options, **interval}
            if rule.keys() & {"purpose", "schedule", "variable"}:
                raise ValueError(f"{context}: purpose, schedule and variable are defined by the schedule layout")
            yield dict(rule, purpose="assimilation", variable=variable, schedule=schedule)


def load_schedule_rules(
    path: Path, year: int, schedule: str | None, *, period: PlanningPeriod | None = None
) -> list[Rule]:
    """Compile a structured schedule or a legacy table of selection rules."""
    path = Path(path)
    if path.suffix.lower() in {".yaml", ".yml"}:
        try:
            with path.open(encoding="utf-8-sig") as stream:
                document = YAML(typ="safe").load(stream)
            if isinstance(document, dict):
                return compile_period_rules(_schedule_rows(document, schedule), year, period)
        except (ValueError, YAMLError) as exc:
            raise ValueError(f"{path}: {exc}") from exc
    return compile_period_rules(read_rows(path), year, period)


def _evaluation_settings(raw, where):
    if not isinstance(raw, dict):
        raise ValueError(f"{where}: expected a mapping")
    options = _settings({key: value for key, value in raw.items() if key != "count_by_year"}, where)
    fields = {"period_start", "period_end", "count", "year", "strategy", "ranking"}
    for key in options:
        if key not in fields and not key.startswith(("min_", "max_")):
            raise ValueError(f"{where}: unexpected evaluation field {key!r}")
    if "count_by_year" in raw:
        counts = raw["count_by_year"]
        if not isinstance(counts, dict) or not counts:
            raise ValueError(f"{where}.count_by_year: expected a nonempty mapping of start years to counts")
        normalized = {}
        for key, value in counts.items():
            token = str(key)
            if not token.isascii() or not token.isdigit() or not 1 <= int(token) <= 9999:
                raise ValueError(f"{where}.count_by_year: invalid calendar year {key!r}")
            year = int(token)
            if year in normalized:
                raise ValueError(f"{where}.count_by_year: duplicate year {year}")
            normalized[year] = value
        options["count_by_year"] = normalized
    return options


def _evaluation_rows(document):
    if document.keys() != {"evaluation"}:
        raise ValueError("expected evaluation at the root of grouped evaluation rules")
    evaluation = document["evaluation"]
    if not isinstance(evaluation, dict) or not evaluation.keys() - {"defaults"}:
        raise ValueError("evaluation must map at least one variable to its rules")
    defaults = _evaluation_settings(evaluation.get("defaults", {}), "evaluation.defaults")
    for variable, observation in evaluation.items():
        if variable == "defaults":
            continue
        context = f"evaluation.{variable}"
        if not isinstance(observation, dict):
            raise ValueError(f"{context}: expected a mapping")
        options = _evaluation_settings({k: v for k, v in observation.items() if k != "intervals"}, context)
        intervals = observation.get("intervals", [{}])
        if not isinstance(intervals, list) or not intervals:
            raise ValueError(f"{context}.intervals: expected a nonempty sequence of intervals")
        for index, raw_interval in enumerate(intervals, 1):
            interval = _evaluation_settings(raw_interval, f"{context}.intervals[{index}]")
            rule = {**defaults, **options, **interval, "purpose": "evaluation", "variable": variable}
            if "count_by_year" not in rule:
                yield rule
                continue
            if "count" in rule or "year" in rule:
                raise ValueError(f"{context}: count_by_year cannot be combined with count or year")
            for year, count in rule.pop("count_by_year").items():
                yield dict(rule, year=year, count=count)


def load_evaluation_rules(
    path: Path, period: PlanningPeriod | None = None, *, year: int | None = None
) -> list[Rule]:
    """Compile grouped or flat holdout rules for a declared period or an explicit start year."""
    path = Path(path)
    if year is None:
        if period is None:
            raise ValueError("evaluation rules require a planning period or start year")
        year = period.start.year
    if path.suffix.lower() in {".yaml", ".yml"}:
        try:
            with path.open(encoding="utf-8-sig") as stream:
                document = YAML(typ="safe").load(stream)
            if isinstance(document, dict):
                return compile_period_rules(_evaluation_rows(document), year, period, allow_empty=True)
        except (ValueError, YAMLError) as exc:
            raise ValueError(f"{path}: {exc}") from exc
    rows = read_rows(path)
    if period is None:
        # Explicit legacy tables may supply assimilation rules that contribute evaluation limits.
        return compile_rules(rows, year)
    if not rows:
        raise ValueError(f"{path}: expected at least one evaluation rule")
    if any(row.get("purpose") != "evaluation" for row in rows):
        raise ValueError(f"{path}: shared evaluation rules must all have purpose evaluation")
    return compile_period_rules(rows, year, period, allow_empty=True)


def _configured_schedules(period: Path) -> set[str]:
    folder = period.parent.parent / "schedules"
    if not folder.is_dir():
        return set()
    return {
        path.name if path.is_dir() else path.stem for path in folder.iterdir()
        if (path.is_dir() and (path / "rules.yml").is_file())
        or (path.is_file() and path.suffix.lower() in {".yaml", ".yml", ".csv"})
    }


def schedule_table_path(period: Path, name: str, *, creating: bool = False) -> Path:
    """Keep a single schedule beside period.yml; preserve existing schedule locations."""
    _check_name(name)
    flat = table_path(period, name + ".schedule", required=False)
    grouped = table_path(period / "schedules", name, required=False)
    if flat and grouped:
        raise ValueError(f"{period}: duplicate schedule files for {name}")
    if flat or grouped:
        return flat or grouped
    if not creating:
        raise ValueError(f"{period}: missing schedule {name}")
    if not (period / "schedules").is_dir() and (
        len(_configured_schedules(period)) == 1 or any(period.glob("*.schedule.*"))
    ):
        return period / f"{name}.schedule.yml"
    return period / "schedules" / f"{name}.yml"


def report_paths(period: Path | None, output: Path) -> tuple[Path, Path]:
    """Use one reports directory; group details when multiple schedules share a period."""
    output = Path(output)
    if period is not None:
        period = Path(period)
        in_period = output.parent.resolve() == period.resolve()
        flat_schedule = in_period and output.stem.endswith(".schedule")
        evaluation = in_period and output.stem == "withheld"
        if evaluation or flat_schedule or output.parent.resolve() == (period / "schedules").resolve():
            name = output.stem.removesuffix(".schedule") if flat_schedule else output.stem
            reports = period / "reports"
            pdf = reports / f"{name}.pdf"
            configured = _configured_schedules(period)
            if evaluation and "withheld" in configured:
                return pdf, period / "withheld_details.yml"
            grouped = reports / "details" / f"{name}.yml"
            flat = reports / f"{name}_details.yml"
            if grouped.is_file() and flat.is_file():
                raise ValueError(f"{output}: multiple structured reports found")
            if grouped.is_file() or (not flat.is_file() and len(configured) > 1 and not flat_schedule):
                return pdf, grouped
            return pdf, flat
    return output.with_name(output.name + ".pdf"), output.with_name(output.name + ".details.yml")
