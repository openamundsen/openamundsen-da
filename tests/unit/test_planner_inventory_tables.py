from datetime import date
from pathlib import Path

import pytest

from openamundsen_da.planner.inventory import build_inventory
from openamundsen_da.planner.tables import read_rows, write_rows


@pytest.mark.parametrize("suffix", [".csv", ".yml"])
def test_editable_rows_preserve_identifiers_and_user_edits(tmp_path, suffix):
    path = tmp_path / f"schedule{suffix}"
    original = [{"date": "2025-01-02", "source_id": "001", "variable": "scf"}]
    write_rows(path, original)
    assert read_rows(path) == original
    with pytest.raises(FileExistsError, match="overwrite"):
        write_rows(path, [{"date": "2025-01-03"}])
    assert read_rows(path) == original
    write_rows(path, [], fields=["date", "variable"], overwrite=True)
    assert read_rows(path) == []


@pytest.mark.parametrize(("name", "data"), [
    ("bad.csv", "date,date\na,b\n"),
    ("bad.csv", "date,variable\na,b,c\n"),
    ("bad.yml", "- date: '2025-01-02'\n  date: '2025-01-03'\n"),
    ("bad.yml", "- source_id: [1, 2]\n"),
])
def test_malformed_tables_report_the_filename(tmp_path, name, data):
    path = tmp_path / name
    path.write_text(data)
    with pytest.raises(ValueError, match=name):
        read_rows(path)


def test_inventory_collects_only_the_requested_dates_from_real_inputs():
    project = Path(__file__).resolve().parents[2] / "examples/rofental/projects/project_2022_2023"
    rows = build_inventory(project, "2022-10-03", "2022-10-05", ["scf", "station_hs"],
                           dates={date(2022, 10, 3)})
    assert len(rows) == 2
    optical, station = rows
    assert optical["source_id"] == "s2_fsc_rofental_2022_10_03.tif"
    assert optical["observation_time"] == "2022-10-03T10:17:49.024000Z"
    assert optical["project_min_support_coverage_ratio"] == 0.1
    assert optical["support_coverage_ratio"] > optical["project_min_support_coverage_ratio"]
    assert optical["quality_issue"] == ""
    assert station["station_ids"] == "latschbloder|proviantdepot"
    assert station["station_count"] == 2
