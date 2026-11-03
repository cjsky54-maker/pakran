"""CSV export: stable schema, multi-region union, blanks (spec items 16, 17)."""
import csv

import pytest

from app.core.configuration import SettingsData
from app.database.models import Preset
from app.reports.csv_exporter import export_tests_csv
from app.thermal.region_calculator import RegionDefinition


def _make_preset(db, code, regions):
    pid = db.create_preset(code, "1.00", regions)
    return db.get_preset(pid)


def _add_test(db, preset, samples):
    """samples: list of (t, ambient, {label: (min, max, dT, status)})."""
    test_id = db.create_test("standard", preset, SettingsData().snapshot_dict(),
                             "COMPLETED")
    for t, ambient, regions in samples:
        sample_id = db.add_sample(test_id, t, "2026-11-03T10:00:00+00:00", ambient)
        for label, (tmin, tmax, dt, status) in regions.items():
            db.add_region_measurement(sample_id, label, tmin, tmax, dt, status)
    db.complete_test(test_id, "PASS", "COMPLETED", None)
    return test_id


def _read_csv(path):
    with open(path, "r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.reader(fh)
        return list(reader)


def test_multi_region_union_schema(db, tmp_path):
    preset1 = _make_preset(db, "A1", [
        RegionDefinition("A", 0, 0, 10, 10, "#111111", 10.0),
        RegionDefinition("B", 20, 20, 10, 10, "#222222", 20.0),
    ])
    preset2 = _make_preset(db, "A2", [
        RegionDefinition("A", 0, 0, 10, 10, "#111111", 10.0),
        RegionDefinition("C", 40, 40, 10, 10, "#333333", 30.0),
    ])
    _add_test(db, preset1, [
        (10.0, 25.0, {"A": (24.0, 30.0, 5.0, "PASS"), "B": (25.0, 40.0, 15.0, "PASS")}),
        (60.0, 25.5, {"A": (24.0, 31.0, 5.5, "PASS"), "B": (25.0, 42.0, 16.5, "PASS")}),
    ])
    _add_test(db, preset2, [
        (15.0, 24.0, {"A": (23.0, 29.0, 5.0, "PASS"), "C": (24.0, 50.0, 26.0, "FAIL")}),
    ])

    records = db.list_tests()
    path = str(tmp_path / "export.csv")
    count = export_tests_csv(path, records, db)
    assert count == 2

    rows = _read_csv(path)
    header, data = rows[0], rows[1:]
    assert len(data) == 2

    # Union of regions A, B, C across different presets.
    assert "Region A Min @T10" in header
    assert "Region B Max @T10" in header
    assert "Region C Status @T15" in header
    assert "Ambient @T60" in header
    assert "Timestamp @T15" in header

    row_by_code = {dict(zip(header, row))["Device Code"]: dict(zip(header, row)) for row in data}
    assert set(row_by_code) == {"A1", "A2"}
    row1 = row_by_code["A1"]
    row2 = row_by_code["A2"]
    # Missing region/slot cells stay blank (not fabricated).
    assert row1["Region C Max @T15"] == ""
    assert row2["Region B Max @T10"] == ""
    assert row2["Ambient @T60"] == ""
    # Present values are exported with the expected precision.
    assert row1["Region A Max @T60"] == "31.00"
    assert row2["Region C DeltaT @T15"] == "26.00"
    # Fixed identification columns.
    assert row1["Test Type"] == "Standard"
    assert row1["Device Code"] == "A1"
    assert row2["Device Code"] == "A2"


def test_fixed_header_order(db, tmp_path):
    preset = _make_preset(db, "A1", [
        RegionDefinition("A", 0, 0, 10, 10, "#111111", 10.0),
    ])
    _add_test(db, preset, [(5.0, 25.0, {"A": (24.0, 30.0, 5.0, "PASS")})])
    records = db.list_tests()
    path = str(tmp_path / "export.csv")
    export_tests_csv(path, records, db)
    header = _read_csv(path)[0]
    assert header[:11] == [
        "Test ID", "Date", "Time", "Test Type", "Device Code", "Version",
        "Preset ID", "Overall Result", "Test Status", "Duration (s)",
        "Failure Reason",
    ]


def test_export_failure_cases(db, tmp_path):
    preset = _make_preset(db, "A1", [
        RegionDefinition("A", 0, 0, 10, 10, "#111111", 10.0),
    ])
    _add_test(db, preset, [(5.0, 25.0, {"A": (24.0, 30.0, 5.0, "PASS")})])

    # Empty selection.
    with pytest.raises(ValueError):
        export_tests_csv(str(tmp_path / "none.csv"), [], db)

    # Unwritable directory.
    with pytest.raises(OSError):
        export_tests_csv("/nonexistent_dir_xyz/export.csv", db.list_tests(), db)
