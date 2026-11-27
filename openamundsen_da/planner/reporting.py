"""Publish an editable schedule and its review outputs from the same target outcomes."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator, Sequence
from contextlib import ExitStack, contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

from ruamel.yaml import YAML

from openamundsen_da.util.yaml_utils import read_yaml_mapping


@contextmanager
def staged_outputs(paths: Sequence[Path], *, overwrite: bool) -> Iterator[dict[Path, Path]]:
    """Prepare every output before publishing any, restoring prior files on failure."""
    if len({path.resolve() for path in paths}) != len(paths):
        raise ValueError("supply distinct paths for each output")
    for path in paths:
        if path.is_dir():
            raise ValueError(f"{path}: output must be a file")
        if path.exists() and not overwrite:
            raise FileExistsError(f"{path} exists; choose a new filename or pass --overwrite")
    with ExitStack() as stack:
        directories = {}
        staged = {}
        for path in paths:
            if path.parent not in directories:
                path.parent.mkdir(parents=True, exist_ok=True)
                directories[path.parent] = Path(stack.enter_context(TemporaryDirectory(dir=path.parent)))
            staged[path] = directories[path.parent] / path.name
        yield staged
        published = {}
        try:
            for path, prepared in staged.items():
                previous = None
                if overwrite and path.exists():
                    previous = prepared.parent / (prepared.name + ".backup")
                    os.link(path, previous)
                    os.replace(prepared, path)
                else:
                    os.link(prepared, path)
                published[path] = previous
        except OSError:
            for path, previous in reversed(published.items()):
                if previous is None:
                    path.unlink(missing_ok=True)
                else:
                    os.replace(previous, path)
            raise


def _compact_row(row):
    fields = {}
    for key, value in row.items():
        if key in {"variable", "schedule"} or value is None or value == "":
            continue
        if key == "effective_limits" and isinstance(value, str):
            value = json.loads(value)
        fields[key] = value
    return fields


def write_details(path: Path, schedule, *, summary, targets, exclusions) -> None:
    """Group review data by variable; common target settings occur once per variable."""
    observations = {}
    for group, rows in (("summary", summary), ("targets", targets), ("exclusions", exclusions)):
        for row in rows:
            section = observations.setdefault(row["variable"], {})
            section.setdefault(group, []).append(_compact_row(row))
    for variable, section in observations.items():
        shared = {}
        for group, key, fields in (
            ("targets", "settings", ("reschedule", "tolerance_days", "min_spacing_days", "effective_limits")),
            ("exclusions", "exclusion_settings", ("period_start", "period_end", "effective_limits")),
        ):
            records = section.get(group, [])
            if not records:
                continue
            common = {
                field: records[0][field] for field in fields
                if field in records[0] and all(row.get(field) == records[0][field] for row in records)
            }
            if common:
                shared[key] = common
                for row in records:
                    for field in common:
                        del row[field]
        observations[variable] = {**shared, **section}
    yaml = YAML(typ="safe")
    yaml.default_flow_style = False
    yaml.sort_base_mapping_type_on_output = False
    with path.open("w", encoding="utf-8") as stream:
        yaml.dump({"schedule": schedule, "observations": observations}, stream)


def read_report_targets(path: Path) -> list[dict]:
    """Expand grouped target records into the chronological rows used by timelines."""
    document = read_yaml_mapping(path, error_cls=ValueError, context="planner report")
    try:
        targets = [
            {**section.get("settings", {}), **row, "variable": variable}
            for variable, section in document["observations"].items()
            for row in section.get("targets", [])
        ]
        return sorted(targets, key=lambda row: (row["target_date"], row["variable"]))
    except (AttributeError, TypeError, KeyError) as exc:
        raise ValueError(f"{path}: invalid grouped observation or target record") from exc


def render_reports(pdf_path, detail_path, *, label, schedule, targets, summary, exclusions, withheld, rules):
    from .report_pdf import write_report_pdf

    write_details(detail_path, schedule, summary=summary, targets=targets, exclusions=exclusions)
    write_report_pdf(pdf_path, label=label, schedule=schedule, targets=targets, summary=summary,
                     withheld=withheld, rules=rules)


def write_schedule_reports(pdf_path, detail_path, *, overwrite, **records):
    """Refresh a PDF and its details only after both are successfully rendered."""
    with staged_outputs([pdf_path, detail_path], overwrite=overwrite) as staged:
        render_reports(staged[pdf_path], staged[detail_path], **records)
