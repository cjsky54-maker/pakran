"""Quick Test: one instantaneous radiometric snapshot, saved immediately."""
from __future__ import annotations

from typing import Optional

from app.core.constants import (
    CAM_CONNECTED,
    RESULT_FAIL,
    RESULT_PASS,
    TEST_TYPE_QUICK,
)
from app.core.jalali import utc_now_iso
from app.database.database_manager import DatabaseManager
from app.database.models import Preset
from app.thermal.region_calculator import evaluate_region, measure_region
from app.thermal.temperature_processor import compute_ambient


class QuickTestError(Exception):
    """Prerequisites not met or measurement unavailable."""


def run_quick_test(camera, preset: Preset, settings, db: DatabaseManager) -> dict:
    """Capture one snapshot, evaluate every region and persist the record."""
    if preset is None or not preset.regions:
        raise QuickTestError("Select a preset with at least one region first")
    if camera is None or camera.state != CAM_CONNECTED:
        raise QuickTestError("Camera is not connected")
    frame = camera.latest_frame()
    if frame is None or not frame.is_radiometric:
        raise QuickTestError("No radiometric temperature data available from the camera")

    ambient = compute_ambient(frame.matrix)
    regions: list[dict] = []
    overall = RESULT_PASS
    for region in preset.regions:
        mm = measure_region(frame.matrix, region)
        if mm is None:
            raise QuickTestError(f"Region {region.label} is empty on the sensor")
        delta = mm[1] - ambient
        status = evaluate_region(delta, region.max_allowed_delta_t)
        if status == RESULT_FAIL:
            overall = RESULT_FAIL
        regions.append({
            "label": region.label,
            "min": mm[0],
            "max": mm[1],
            "ambient": ambient,
            "delta_t": delta,
            "max_allowed": region.max_allowed_delta_t,
            "status": status,
            "exceedance": max(0.0, delta - region.max_allowed_delta_t),
        })

    test_id = db.create_test(TEST_TYPE_QUICK, preset, settings.snapshot_dict(), "COMPLETED")
    sample_id = db.add_sample(test_id, 0.0, frame.wall_time or utc_now_iso(), ambient)
    for data in regions:
        db.add_region_measurement(
            sample_id, data["label"], data["min"], data["max"],
            data["delta_t"], data["status"],
        )
    db.complete_test(test_id, overall, "COMPLETED", None)

    return {
        "test_id": test_id,
        "test_type": TEST_TYPE_QUICK,
        "status": overall,
        "overall_result": overall,
        "reason": None,
        "started": True,
        "regions": regions,
        "deciding_sample_t": 0.0,
    }
