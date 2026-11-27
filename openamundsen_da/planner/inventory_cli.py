"""Command-line arguments for collecting a project observation inventory."""

import argparse
from datetime import date
from pathlib import Path


def add_command(commands: argparse._SubParsersAction) -> None:
    parser = commands.add_parser("inventory", help="Measure observations using project sources")
    parser.add_argument("project_dir", type=Path, help="Project directory containing its observation definitions")
    parser.add_argument("--start", required=True, type=date.fromisoformat, help="First inventory date (YYYY-MM-DD)")
    parser.add_argument("--end", required=True, type=date.fromisoformat, help="Last inventory date, inclusive")
    parser.add_argument("--variables", nargs="+", required=True, help="Project observation variables to inventory")
    parser.add_argument("--station-time", help="Local model clock time (defaults to the project start time)")
    parser.add_argument("--output", required=True, type=Path, help="Output inventory CSV or YAML path")
    parser.add_argument("--overwrite", action="store_true", help="Replace an existing inventory file")
    parser.set_defaults(planner_handler=_run_inventory)


def _run_inventory(args) -> int:
    from .inventory import build_inventory
    from .tables import write_rows

    rows = build_inventory(args.project_dir, args.start, args.end, args.variables, station_time=args.station_time)
    fields = None if rows else ["variable", "date", "source_id", "observation_time", "station_ids"]
    write_rows(args.output, rows, fields=fields, overwrite=args.overwrite)
    print(f"{len(rows)} candidate observations in {args.output}")
    return 0
