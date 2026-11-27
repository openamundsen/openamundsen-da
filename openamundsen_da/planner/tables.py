"""Flat, editable observation tables with equivalent CSV and YAML representations."""

from __future__ import annotations

import csv
import os
from collections.abc import Iterable, Mapping, Sequence
from datetime import date, datetime
from pathlib import Path
from tempfile import NamedTemporaryFile

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError


def _suffix(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix not in {".csv", ".yaml", ".yml"}:
        raise ValueError(f"{path}: expected a CSV or YAML file")
    return suffix


def _cell(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (list, dict, set, tuple)):
        raise ValueError("expected a scalar cell value")
    return str(value).strip()


def _fields(keys: Iterable[object]) -> list[str]:
    fields = list(keys)
    if any(not isinstance(key, str) or not key.strip() or key != key.strip() for key in fields):
        raise ValueError("column names must be nonempty strings without surrounding spaces")
    if len(set(fields)) != len(fields):
        raise ValueError("duplicate column names")
    return fields


def read_rows(path: Path) -> list[dict[str, str]]:
    """Read a headered CSV or YAML sequence, preserving source identifiers as text."""
    path = Path(path)
    suffix = _suffix(path)
    try:
        with path.open(encoding="utf-8-sig", newline="") as stream:
            if suffix == ".csv":
                reader = csv.reader(stream, strict=True)
                headers = _fields(next(reader, []))
                if not headers:
                    raise ValueError("missing CSV header")
                result = []
                for row in reader:
                    if not row:
                        continue
                    if len(row) != len(headers):
                        raise ValueError(f"line {reader.line_num}: expected {len(headers)} fields, got {len(row)}")
                    result.append(dict(zip(headers, map(_cell, row), strict=True)))
                return result

            document = YAML(typ="safe").load(stream)
            if not isinstance(document, list):
                raise ValueError("expected a YAML sequence of rows")
            result = []
            for index, row in enumerate(document, 1):
                if not isinstance(row, dict):
                    raise ValueError(f"row {index}: expected a mapping")
                try:
                    result.append({key: _cell(row[key]) for key in _fields(row)})
                except ValueError as exc:
                    raise ValueError(f"row {index}: {exc}") from exc
            return result
    except (ValueError, csv.Error, YAMLError) as exc:
        raise ValueError(f"{path}: {exc}") from exc


def write_rows(
    path: Path,
    rows: Iterable[Mapping],
    *,
    fields: Sequence[str] | None = None,
    overwrite: bool = False,
) -> None:
    """Publish a complete table atomically, preserving an existing file by default."""
    path = Path(path)
    suffix = _suffix(path)
    records = list(rows)
    headers = _fields(fields if fields is not None else dict.fromkeys(key for row in records for key in row))
    if suffix == ".csv" and not headers:
        raise ValueError(f"{path}: supply fields for an empty CSV table")
    cells = []
    for index, row in enumerate(records, 1):
        if row.keys() - set(headers):
            raise ValueError(f"{path}: row {index} contains fields outside the supplied header")
        cells.append({key: _cell(row.get(key)) for key in headers})
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(mode="w", encoding="utf-8", newline="", dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
    try:
        with temporary.open("w", encoding="utf-8", newline="") as stream:
            if suffix == ".csv":
                writer = csv.DictWriter(stream, fieldnames=headers, lineterminator="\n")
                writer.writeheader()
                writer.writerows(cells)
            else:
                yaml = YAML(typ="safe")
                yaml.default_flow_style = False
                yaml.sort_base_mapping_type_on_output = False
                yaml.dump(cells, stream)
        if overwrite:
            os.replace(temporary, path)
        else:
            try:
                os.link(temporary, path)
            except FileExistsError as exc:
                raise FileExistsError(f"{path} already exists; choose a new filename or request overwrite") from exc
    finally:
        temporary.unlink(missing_ok=True)
