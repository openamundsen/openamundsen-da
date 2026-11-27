"""Observation Planner commands for generating and reviewing schedules."""

from __future__ import annotations

import argparse
from pathlib import Path


def _period_selection(args):
    from .layout import load_evaluation_rules, load_schedule_rules, read_period, schedule_rules_path, schedule_table_path, table_path

    directory = args.period.resolve()
    period = read_period(directory)
    if directory.parent.name != "periods":
        raise ValueError("--period must name a period directory inside planner/periods/")
    if args.rules or args.year is not None:
        raise ValueError("--period supplies the rules location and calendar year; omit --rules and --year")
    args.inventory = args.inventory or table_path(directory, "inventory")
    evaluation = directory.parent.parent / "evaluation.yml"
    if args.planner_action == "holdouts":
        if not evaluation.is_file():
            raise ValueError(f"{evaluation}: configure evaluation rules before generating withheld observations")
        args.rules = evaluation
        rules = load_evaluation_rules(evaluation, period)
        if args.output is None:
            args.output = table_path(directory, "withheld", required=False) or directory / "withheld.yml"
    else:
        args.rules = schedule_rules_path(directory, args.schedule)
        rules = load_schedule_rules(args.rules, period.start.year, args.schedule, period=period)
        shared = table_path(directory, "withheld", required=False)
        if args.holdouts and (shared is None or shared.resolve() != args.holdouts.resolve()):
            raise ValueError("--period uses the withheld table shared by all schedules in this period")
        args.holdouts = shared
        if evaluation.exists() and shared is None:
            raise ValueError(f"generate the shared withheld list with planner holdouts --period {directory} first")
        if args.output is None:
            args.output = schedule_table_path(directory, args.schedule, creating=True)
    return rules, period.name


def _select(args: argparse.Namespace) -> int:
    from .holdouts import HOLDOUT_FIELDS, SUMMARY_FIELDS, generate_holdouts, schedule_summary
    from .layout import load_evaluation_rules, load_schedule_rules, report_paths
    from .reporting import render_reports, staged_outputs, write_details
    from .schedule_output import UPDATE_FIELDS, assigned_updates
    from .selection import IDENTITY, generate_schedule
    from .tables import read_rows, write_rows

    exclusion_fields = (*IDENTITY, "schedule", "period_start", "period_end", "reason", "effective_limits")

    if args.period is not None:
        rules, label = _period_selection(args)
    else:
        if args.rules is None or args.year is None or args.inventory is None or args.output is None:
            raise ValueError("supply --period or all of --rules, --year, --inventory and --output")
        if args.planner_action == "holdouts":
            rules = load_evaluation_rules(args.rules, year=args.year)
        else:
            rules = load_schedule_rules(args.rules, args.year, args.schedule)
        label = str(args.year)
    inventory = read_rows(args.inventory)
    exclusions = []
    summary = []
    targets = []
    output = args.output
    if args.planner_action == "holdouts":
        rows = generate_holdouts(inventory, rules, summary=summary, exclusions=exclusions)
        fields = HOLDOUT_FIELDS
    else:
        holdouts = (
            read_rows(args.holdouts) if args.holdouts is not None else generate_holdouts(inventory, rules)
        )
        targets = generate_schedule(inventory, rules, args.schedule, holdouts, exclusions=exclusions)
        summary = schedule_summary(targets, rules, args.schedule)
        rows = assigned_updates(targets, inventory)
        fields = [key for key in UPDATE_FIELDS if key in {"date", "variable"} or any(key in row for row in rows)]
    generate = args.planner_action == "generate"
    tables = [(output, rows, fields)]
    if args.summary:
        tables.append((args.summary, summary, SUMMARY_FIELDS))
    if args.exclusions:
        tables.append((args.exclusions, exclusions, exclusion_fields))
    pdf, detail = report_paths(args.period, output)
    paths = [detail, *([pdf] if generate else []), *(path for path, _, _ in tables)]
    sources = [args.rules, args.inventory, getattr(args, "holdouts", None)]
    if args.period:
        sources.extend([args.period / "period.yml", args.period.resolve().parent.parent / "evaluation.yml"])
    if {path.resolve() for path in paths} & {path.resolve() for path in sources if path}:
        raise ValueError("supply distinct paths for each input and output")
    with staged_outputs(paths, overwrite=args.overwrite) as staged:
        for path, records, headers in tables:
            write_rows(staged[path], records, fields=headers, sparse=generate and path == output)
        if generate:
            evaluation = args.holdouts is not None or any(rule.purpose == "evaluation" for rule in rules)
            render_reports(staged[pdf], staged[detail], label=label, schedule=args.schedule,
                           targets=targets, summary=summary, exclusions=exclusions,
                           withheld=holdouts if evaluation else None, rules=rules)
        else:
            write_details(staged[detail], "withheld", summary=summary, targets=[], exclusions=exclusions)
    if generate:
        print(pdf)
    for row in summary:
        print(
            f"{row['variable']} ({row['period_start']} to {row['period_end']}): "
            f"{row['selected']}/{row['requested']}" + (f" — {row['reason']}" if row["reason"] else "")
        )
    print(output)
    return 0


def _plot_panels(config_path: Path, output: Path, title: str | None) -> None:
    from openamundsen_da.util.yaml_utils import read_yaml_mapping

    from .discovery import read_plot_schedule
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
            label: read_plot_schedule(root / filename)[1]
            for label, filename in config["schedules"].items()
        }
        withheld = read_rows(root / config["withheld"]) if "withheld" in config else []
        panels.append(TimelinePanel(name, schedules, withheld))
    plot_timelines(panels, output, title=title or config.get("title"), columns=min(2, len(panels)))


def _plot(args: argparse.Namespace) -> int:
    from .discovery import read_plot_schedule
    from .tables import read_rows
    from .timeline import plot_timeline

    if args.panels or args.schedule:
        if args.output is None:
            raise ValueError("explicit --schedule or --panels inputs require --output")
    else:
        from .discovery import plot_periods

        directory = args.period or args.planner_dir or Path.cwd()
        if args.holdouts:
            raise ValueError("period discovery reads the shared withheld table from each period")
        output = args.output or directory / "timeline.png"
        plot_periods(directory, output, single_period=args.period is not None, title=args.title)
        print(output)
        return 0

    if args.panels:
        if args.holdouts:
            raise ValueError("with --panels, set withheld in the panel configuration")
        _plot_panels(args.panels, args.output, args.title)
    else:
        schedules = {}
        for spec in args.schedule:
            label, sep, filename = spec.partition("=")
            default_label, rows = read_plot_schedule(Path(filename if sep else label))
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
    parser = subparsers.add_parser("report", help="Validate an edited schedule and refresh its reports")
    parser.add_argument("--project-dir", required=True, type=Path, help="Directory containing the project YAML")
    parser.add_argument("--period", required=True, type=Path, help="Planning period directory")
    parser.add_argument("--schedule", required=True, metavar="NAME", help="Name of the reviewed schedule")
    parser.set_defaults(planner_handler=_report)
    for action, description in (
        ("generate", "Generate an editable assimilation schedule"),
        ("holdouts", "Reserve shared observations for evaluation"),
    ):
        parser = subparsers.add_parser(action, help=description)
        parser.set_defaults(planner_handler=_select, planner_action=action)
        parser.add_argument("--period", type=Path, help="Planning period directory inside planner/periods/")
        parser.add_argument("--rules", type=Path, help="Explicit rule file (CSV or YAML)")
        parser.add_argument(
            "--year", type=int, help="First calendar year of the planning period"
        )
        parser.add_argument(
            "--inventory", type=Path, help="Winter observation inventory in CSV or YAML"
        )
        parser.add_argument(
            "--output", type=Path, help="Editable CSV or YAML selection to write"
        )
        parser.add_argument("--summary", type=Path, help="Optional flat counts and spacing export")
        parser.add_argument(
            "--exclusions", type=Path, help="Optional flat quality and holdout exclusions export"
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
    source = plot.add_mutually_exclusive_group()
    source.add_argument("--planner-dir", type=Path, help="Discover prepared periods here (default: current directory)")
    source.add_argument("--period", type=Path, help="Plot the existing schedules in one planning period")
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
    plot.add_argument("--output", type=Path, help="PNG output (default for discovery: timeline.png in the input directory)")


def _report(args: argparse.Namespace) -> int:
    from .report_refresh import refresh_report

    return refresh_report(args)


def _validate(args: argparse.Namespace) -> int:
    from .validation import _validate_command

    return _validate_command(args)


def add_validation(subparsers):
    parser = subparsers.add_parser("validate", help="Validate reviewed schedules without modifying any files")
    parser.add_argument("--project-dir", type=Path, required=True, help="Directory containing the project YAML")
    parser.add_argument("--period", type=Path, help="Planning period directory with inventory and reviewed schedules")
    parser.add_argument("--schedule", type=Path, action="append", help="CSV/YAML schedule. Repeat to check multiple files.")
    parser.add_argument("--inventory", type=Path, help="Shared winter observation inventory (CSV/YAML)")
    parser.add_argument("--withheld", type=Path, help="Common observations withheld for evaluation (CSV/YAML)")
    parser.add_argument("--rules", type=Path, help="Observation selection rules (CSV/YAML)")
    parser.add_argument("--schedule-name", help="Schedule name in the rules when the input is unlabeled")
    parser.add_argument("--year", type=int, help="Base year for relative rule periods (default: project start year)")
    parser.set_defaults(planner_handler=_validate)
