"""Manual CSV export of test records (one row per test).

The export builds a consistent schema across all exported records:
- fixed identification columns first,
- one 'Timestamp' + 'Ambient' pair per sample slot (union of all slots),
- per Region per slot: Min / Max / DeltaT / Status.

Missing regions or sample slots stay blank. Headers are English, digits
are ASCII, temperatures are Celsius.
"""
from __future__ import annotations

import csv
import logging
from typing import Optional

from app.core.jalali import display_date, display_time
from app.database.database_manager import DatabaseManager
from app.database.models import TestRecord

logger = logging.getLogger("DriverThermalQC.csv")

_TYPE_DISPLAY = {"standard": "Standard", "quick": "Quick"}


def _slot_label(slot: float) -> str:
    return f"T{slot:g}"


def export_tests_csv(path: str, tests: list[TestRecord], db: DatabaseManager) -> int:
    """Export the given tests to ``path``. Returns the number of rows."""
    if not tests:
        raise ValueError("No test records selected for export")

    details = []
    region_labels: set[str] = set()
    slots: set[float] = set()
    for record in tests:
        detail = db.get_test_details(record.id)
        if detail is None:
            continue
        details.append(detail)
        for region in detail["preset_snapshot"].get("regions", []):
            region_labels.add(str(region["label"]))
        for sample in detail["samples"]:
            slots.add(float(sample.sample_time_seconds))

    sorted_slots = sorted(slots)
    sorted_labels = sorted(region_labels)

    header = [
        "Test ID", "Date", "Time", "Test Type", "Device Code", "Version",
        "Preset ID", "Overall Result", "Test Status", "Duration (s)",
        "Failure Reason",
    ]
    for slot in sorted_slots:
        header.extend([f"Timestamp @{_slot_label(slot)}", f"Ambient @{_slot_label(slot)}"])
    for label in sorted_labels:
        for slot in sorted_slots:
            tag = f"{label} @{_slot_label(slot)}"
            header.extend([
                f"Region {label} Min @{_slot_label(slot)}",
                f"Region {label} Max @{_slot_label(slot)}",
                f"Region {label} DeltaT @{_slot_label(slot)}",
                f"Region {label} Status @{_slot_label(slot)}",
            ])

    rows = []
    for detail in details:
        record: TestRecord = detail["record"]
        row = [
            record.id,
            display_date(record.started_at),
            display_time(record.started_at),
            _TYPE_DISPLAY.get(record.test_type, record.test_type),
            record.device_code,
            record.version,
            record.preset_id if record.preset_id is not None else "",
            record.overall_result or "",
            record.status,
            f"{record.duration_s:.1f}" if record.duration_s is not None else "",
            record.failure_reason or "",
        ]
        by_slot = {float(s.sample_time_seconds): s for s in detail["samples"]}
        for slot in sorted_slots:
            sample = by_slot.get(slot)
            if sample is None:
                row.extend(["", ""])
            else:
                row.extend([display_time(sample.timestamp), f"{sample.ambient_temperature:.2f}"])
        for label in sorted_labels:
            for slot in sorted_slots:
                sample = by_slot.get(slot)
                measurement = None
                if sample is not None:
                    for m in sample.measurements:
                        if m.region_label == label:
                            measurement = m
                            break
                if measurement is None:
                    row.extend(["", "", "", ""])
                else:
                    row.extend([
                        f"{measurement.min_temperature:.2f}" if measurement.min_temperature is not None else "",
                        f"{measurement.max_temperature:.2f}" if measurement.max_temperature is not None else "",
                        f"{measurement.delta_t:.2f}" if measurement.delta_t is not None else "",
                        measurement.region_status,
                    ])
        rows.append(row)

    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(header)
        writer.writerows(rows)
    logger.info("Exported %d test records to %s", len(rows), path)
    return len(rows)
