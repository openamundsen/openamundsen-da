"""Observation Planner commands for generating and reviewing schedules."""

from __future__ import annotations

import argparse
from pathlib import Path

def _select(args: argparse.Namespace) -> int:
    from .holdouts import HOLDOUT_FIELDS, SUMMARY_FIELDS, generate_holdouts, schedule_summary
    from .selection import IDENTITY, SCHEDULE_FIELDS, compile_rules, generate_schedule

    exclusion_fields = (*IDENTITY, "schedule", "period_start", "period_end", "reason", "effective_limits")
    from .tables import read_rows, write_rows

    rules = compile_rules(read_rows(args.rules), args.year)
    inventory = read_rows(args.inventory)
    exclusions = []
    summary = []
    if args.planner_action == "holdouts":
        rows = generate_holdouts(inventory, rules, summary=summary, exclusions=exclusions)
        fields = HOLDOUT_FIELDS
    else:
        holdouts = (
            read_rows(args.holdouts) if args.holdouts is not None else generate_holdouts(inventory, rules)
        )
        rows = generate_schedule(inventory, rules, args.schedule, holdouts, exclusions=exclusions)
        summary = schedule_summary(rows, rules, args.schedule)
        fields = SCHEDULE_FIELDS
    output = args.output
    exclusions_path = args.exclusions or output.with_stem(output.stem + "_exclusions")
    summary_path = args.summary or output.with_stem(output.stem + "_summary")
    tables = [
        (output, rows, fields),
        (exclusions_path, exclusions, exclusion_fields),
        (summary_path, summary, SUMMARY_FIELDS),
    ]
    sources = [args.rules, args.inventory, getattr(args, "holdouts", None)]
    destinations = {path.resolve() for path, _, _ in tables}
    if len(destinations) != len(tables) or any(path and path.resolve() in destinations for path in sources):
        raise ValueError("supply distinct paths for each input and output table")
    for path, _, _ in tables:
        if path.suffix.lower() not in {".csv", ".yaml", ".yml"}:
            raise ValueError(f"{path}: expected a CSV or YAML table")
        if path.exists() and not args.overwrite:
            raise FileExistsError(f"{path} exists; choose a new filename or pass --overwrite")
    for path, records, headers in tables:
        write_rows(path, records, fields=headers, overwrite=args.overwrite)
    for row in summary:
        print(
            f"{row['variable']} ({row['period_start']} to {row['period_end']}): "
            f"{row['selected']}/{row['requested']}" + (f" — {row['reason']}" if row["reason"] else "")
        )
    print(output)
    return 0


def _read_schedule(path: Path):
    from .tables import read_rows

    records = read_rows(path)
    names = {row["schedule"] for row in records if row.get("schedule")}
    if len(names) > 1:
        raise ValueError(f"{path}: expected one schedule per file, found {sorted(names)}")
    return next(iter(names), path.stem), records


def _plot_panels(config_path: Path, output: Path, title: str | None) -> None:
    from openamundsen_da.util.yaml_utils import read_yaml_mapping

    from .tables import read_rows
    from .timeline import TimelinePanel, plot_timelines

    config = read_yaml_mapping(config_path, error_cls=ValueError, context="Timeline configuration")
    unknown = config.keys() - {"panels", "schedules", "withheld", "title"}
    if unknown:
        raise ValueError(f"{config_path}: unknown timeline fields: {', '.join(map(str, unknown))}")
    for field in ("panels", "schedules"):
        mapping = config.get(field)
        if not isinstance(mapping, dict) or not mapping or not all(
            isinstance(label, str) and label.strip() and isinstance(path, str) and path.strip()
            for label, path in mapping.items()
        ):
            raise ValueError(f"{config_path}: {field} must map nonempty labels to paths")
    for field in ("withheld", "title"):
        if field in config and not (isinstance(config[field], str) and config[field].strip()):
            raise ValueError(f"{config_path}: {field} must be a nonempty string")

    panels = []
    for name, directory in config["panels"].items():
        root = config_path.parent / directory
        schedules = {
            label: _read_schedule(root / filename)[1]
            for label, filename in config["schedules"].items()
        }
        withheld = read_rows(root / config["withheld"]) if "withheld" in config else []
        panels.append(TimelinePanel(name, schedules, withheld))
    plot_timelines(panels, output, title=title or config.get("title"), columns=min(2, len(panels)))


def _plot(args: argparse.Namespace) -> int:
    from .tables import read_rows
    from .timeline import plot_timeline

    if args.panels:
        if args.holdouts:
            raise ValueError("with --panels, set withheld in the panel configuration")
        _plot_panels(args.panels, args.output, args.title)
    else:
        schedules = {}
        for spec in args.schedule:
            label, sep, filename = spec.partition("=")
            default_label, rows = _read_schedule(Path(filename if sep else label))
            name = label if sep and label else default_label
            if name in schedules:
                raise ValueError(f"duplicate plotted schedule label {name!r}")
            schedules[name] = rows
        holdouts = read_rows(args.holdouts) if args.holdouts else []
        plot_timeline(schedules, holdouts, args.output, title=args.title)
    print(args.output)
    return 0


def add_commands(subparsers) -> None:
    """Register the planner's selection and timeline operations on the public CLI."""
    for action, description in (
        ("generate", "Generate an editable assimilation schedule"),
        ("holdouts", "Reserve shared observations for evaluation"),
    ):
        parser = subparsers.add_parser(action, help=description)
        parser.set_defaults(planner_handler=_select, planner_action=action)
        parser.add_argument("--rules", type=Path, required=True, help="Shared CSV or YAML selection rules")
        parser.add_argument(
            "--year", type=int, required=True, help="First calendar year of the planning period"
        )
        parser.add_argument(
            "--inventory", type=Path, required=True, help="Winter observation inventory in CSV or YAML"
        )
        parser.add_argument(
            "--output", type=Path, required=True, help="Editable CSV or YAML selection to write"
        )
        parser.add_argument("--summary", type=Path, help="Counts and spacing (defaults to OUTPUT_summary)")
        parser.add_argument(
            "--exclusions", type=Path, help="Quality/holdout exclusions (defaults to OUTPUT_exclusions)"
        )
        parser.add_argument(
            "--overwrite", action="store_true", help="Explicitly replace existing output tables"
        )
        if action == "generate":
            parser.add_argument("--schedule", required=True, help="Schedule name in the rules")
            parser.add_argument(
                "--holdouts",
                "--withheld",
                type=Path,
                help="Reviewed CSV or YAML evaluation list; default: select from rules",
            )
    plot = subparsers.add_parser("plot", help="Plot editable schedules for one or more planning periods")
    plot.set_defaults(planner_handler=_plot)
    source = plot.add_mutually_exclusive_group(required=True)
    source.add_argument("--panels", type=Path, metavar="FILE", help="YAML configuration for a combined timeline")
    source.add_argument(
        "--schedule",
        action="append",
        metavar="[LABEL=]FILE",
        help="Repeat for each schedule, in display order",
    )
    plot.add_argument(
        "--holdouts", "--withheld", type=Path, help="Shared evaluation observations in CSV or YAML"
    )
    plot.add_argument("--title", help="Optional figure title, for example the winter")
    plot.add_argument("--output", type=Path, required=True, help="PNG timeline to write")


def _validate(args: argparse.Namespace) -> int:
    from .validation import _validate_command

    return _validate_command(args)


def add_validation(subparsers):
    parser = subparsers.add_parser("validate", help="Validate reviewed schedules without modifying any files")
    parser.add_argument("--project-dir", type=Path, required=True, help="Directory containing the project YAML")
    parser.add_argument("--schedule", type=Path, action="append", help="CSV/YAML schedule. Repeat to check multiple files.")
    parser.add_argument("--inventory", type=Path, help="Shared winter observation inventory (CSV/YAML)")
    parser.add_argument("--withheld", type=Path, help="Common observations withheld for evaluation (CSV/YAML)")
    parser.add_argument("--rules", type=Path, help="Observation selection rules (CSV/YAML)")
    parser.add_argument("--schedule-name", help="Schedule name in the rules when the input is unlabeled")
    parser.add_argument("--year", type=int, help="Base year for relative rule periods (default: project start year)")
    parser.set_defaults(planner_handler=_validate)
