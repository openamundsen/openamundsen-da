"""Command-line arguments for collecting a project observation inventory."""

import argparse
from datetime import date
from pathlib import Path


def add_command(commands: argparse._SubParsersAction) -> None:
    parser = commands.add_parser("inventory", help="Measure observations using project sources")
    parser.add_argument("project_dir", type=Path, help="Project directory containing its observation definitions")
    parser.add_argument("--period", type=Path, help="Read inclusive dates from period.yml and write inventory.yml here")
    parser.add_argument("--start", type=date.fromisoformat, help="First inventory date (YYYY-MM-DD)")
    parser.add_argument("--end", type=date.fromisoformat, help="Last inventory date, inclusive")
    parser.add_argument("--variables", nargs="+", required=True, help="Project observation variables to inventory")
    parser.add_argument("--station-time", help="Local model clock time (defaults to the project start time)")
    parser.add_argument("--output", type=Path, help="Output inventory CSV or YAML path")
    parser.add_argument("--overwrite", action="store_true", help="Replace an existing inventory file")
    parser.set_defaults(planner_handler=_run_inventory)


def _run_inventory(args) -> int:
    from .inventory import build_inventory
    from .tables import write_rows

    if args.period:
        from .layout import read_period, table_path

        if args.start is not None or args.end is not None:
            raise ValueError("--period supplies the inventory dates; omit --start and --end")
        period = read_period(args.period)
        args.start, args.end = period.start, period.end
        args.output = args.output or table_path(args.period, "inventory", required=False) or args.period / "inventory.yml"
        if args.output.resolve() == (args.period / "period.yml").resolve():
            raise ValueError("inventory output must differ from period.yml")
    elif args.start is None or args.end is None or args.output is None:
        raise ValueError("supply --period or all of --start, --end and --output")
    rows = build_inventory(args.project_dir, args.start, args.end, args.variables, station_time=args.station_time)
    fields = None if rows else ["variable", "date", "source_id", "observation_time", "station_ids"]
    write_rows(args.output, rows, fields=fields, overwrite=args.overwrite)
    print(f"{len(rows)} candidate observations in {args.output}")
    return 0
