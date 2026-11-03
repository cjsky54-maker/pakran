"""Standard Test workflow (two phases, per the QC procedure).

Phase 1 - WAITING FOR TEMPERATURE RISE:
the test records a baseline, then waits until the first Region exceeds
its initial Max by the configured rise threshold, or the waiting
timeout expires (-> TIMEOUT).

Phase 2 - TIMED MEASUREMENT:
samples are taken at fixed slots (interval, 2*interval, ... 60 s) on a
monotonic clock. Missed slots (camera loss) stay blank and are never
shifted. The final slot (T60) alone decides PASS/FAIL.

The controller is driven by ``tick()`` (a QTimer in the GUI, direct
calls in automated tests), which keeps the logic testable without an
event loop.
"""
from __future__ import annotations

import time
from typing import Optional

from PySide6.QtCore import QObject, QTimer, Signal

from app.core.constants import (
    CAM_CONNECTED,
    PHASE_FINISHED,
    PHASE_IDLE,
    PHASE_MEASURING,
    PHASE_WAITING,
    REASON_CAMERA_LOST,
    REASON_DB_WRITE,
    REASON_EMERGENCY,
    REASON_T60_MISSING,
    REASON_TIMEOUT,
    RESULT_FAIL,
    RESULT_PASS,
    SAMPLE_INTERVAL_CHOICES,
    STANDARD_TEST_DURATION_S,
    STATUS_INCOMPLETE,
    STATUS_TIMEOUT,
    TEST_TYPE_STANDARD,
    TICK_INTERVAL_MS,
)
from app.core.jalali import utc_now_iso
from app.database.database_manager import DatabaseError, DatabaseManager
from app.database.models import Preset
from app.thermal.region_calculator import (
    RegionDefinition,
    evaluate_region,
    measure_region,
)
from app.thermal.temperature_processor import compute_ambient

from app.testing.test_state_machine import TestPhase, TestStateMachine


class TestStartError(Exception):
    """Raised when prerequisites for starting a test are not met."""


def build_schedule(interval_s: float, duration_s: float) -> list[float]:
    """Sampling slots: interval, 2*interval ... duration (inclusive)."""
    if interval_s <= 0:
        raise ValueError("Sample interval must be positive")
    slots: list[float] = []
    t = float(interval_s)
    while t < duration_s - 1e-9:
        slots.append(t)
        t += interval_s
    slots.append(duration_s)
    return slots


class StandardTestController(QObject):
    """Runs one Standard Test at a time against a CameraManager-like object."""

    phaseChanged = Signal(str)
    statusMessage = Signal(str)
    sampleRecorded = Signal(dict)
    testFinished = Signal(dict)

    def __init__(self, db: DatabaseManager, parent: Optional[QObject] = None,
                 clock=time.monotonic) -> None:
        super().__init__(parent)
        self._db = db
        self._clock = clock
        self._timer = QTimer(self)
        self._timer.setInterval(TICK_INTERVAL_MS)
        self._timer.timeout.connect(self.tick)
        self._machine = TestStateMachine()
        self._reset_runtime()

    # -- state ------------------------------------------------------------
    @property
    def phase(self) -> str:
        return self._machine.phase.value

    @property
    def is_running(self) -> bool:
        return self._machine.phase in (TestPhase.WAITING_RISE, TestPhase.MEASURING)

    @property
    def test_id(self) -> Optional[int]:
        return self._test_id

    def _reset_runtime(self) -> None:
        self._test_id: Optional[int] = None
        self._preset: Optional[Preset] = None
        self._settings = None
        self._camera = None
        self._baselines: dict[str, float] = {}
        self._schedule: list[float] = []
        self._recorded: set[float] = set()
        self._missed: set[float] = set()
        self._samples: dict[float, dict] = {}
        self._t_wait_start: float = 0.0
        self._t_ref: Optional[float] = None
        self._deciding_sample: Optional[dict] = None
        self._duration: float = STANDARD_TEST_DURATION_S

    # -- start ---------------------------------------------------------------
    def start(
        self,
        preset: Preset,
        settings,
        camera,
        measurement_duration: float = STANDARD_TEST_DURATION_S,
    ) -> int:
        """Validate prerequisites, record the baseline and start waiting."""
        if self.is_running:
            raise TestStartError("A test is already running")
        if preset is None or not preset.regions:
            raise TestStartError("Select a preset with at least one region first")
        if camera is None or camera.state != CAM_CONNECTED:
            raise TestStartError("Camera is not connected")
        frame = camera.latest_frame()
        if frame is None or not frame.is_radiometric:
            raise TestStartError(
                "No radiometric temperature data available from the camera"
            )

        self._preset = preset
        self._settings = settings
        self._camera = camera
        self._machine.reset()
        self._machine.transition(TestPhase.WAITING_RISE)
        self.phaseChanged.emit(PHASE_WAITING)

        ambient = compute_ambient(frame.matrix)
        regions_out: dict[str, dict] = {}
        for region in preset.regions:
            mm = measure_region(frame.matrix, region)
            if mm is None:
                raise TestStartError(f"Region {region.label} is empty on the sensor")
            self._baselines[region.label] = mm[1]
            regions_out[region.label] = {
                "min": mm[0], "max": mm[1], "delta_t": mm[1] - ambient,
                "status": evaluate_region(mm[1] - ambient, region.max_allowed_delta_t),
            }

        self._test_id = self._db.create_test(
            TEST_TYPE_STANDARD, preset, settings.snapshot_dict(), "WAITING"
        )
        try:
            self._record_sample(0.0, frame, ambient, regions_out, is_baseline=True)
        except DatabaseError as exc:
            self._abort_from_error(exc)
            raise

        self._schedule = build_schedule(
            float(settings.sample_interval), float(measurement_duration)
        )
        self._duration = float(measurement_duration)
        self._recorded = {0.0}
        self._t_wait_start = self._clock()
        self._t_ref = None
        self.statusMessage.emit("WAITING FOR TEMPERATURE RISE")
        self._timer.start()
        return self._test_id

    # -- main tick -----------------------------------------------------------
    def tick(self) -> None:
        """Drive the state machine; called by the QTimer or by tests."""
        if not self.is_running or self._camera is None:
            return
        now = self._clock()
        frame = self._camera.latest_frame()
        cam_state = self._camera.state

        if cam_state != CAM_CONNECTED and cam_state != "reconnecting":
            # Manager gave up reconnecting entirely.
            self._finish(STATUS_INCOMPLETE, None, REASON_CAMERA_LOST)
            return

        if self._machine.phase == TestPhase.WAITING_RISE:
            self._tick_waiting(now, frame)
        elif self._machine.phase == TestPhase.MEASURING:
            self._tick_measuring(now, frame)

    def _tick_waiting(self, now: float, frame) -> None:
        if now - self._t_wait_start > float(self._settings.waiting_timeout):
            self._finish(STATUS_TIMEOUT, None, REASON_TIMEOUT)
            return
        if frame is None or not frame.is_radiometric:
            return  # camera briefly unavailable; keep waiting
        threshold = float(self._settings.temperature_rise_threshold)
        for region in self._preset.regions:
            mm = measure_region(frame.matrix, region)
            if mm is None:
                continue
            if mm[1] - self._baselines[region.label] > threshold:
                # First region to rise sets the reference time; it never resets.
                self._t_ref = now
                self._machine.transition(TestPhase.MEASURING)
                self.phaseChanged.emit(PHASE_MEASURING)
                self.statusMessage.emit("MEASURING")
                try:
                    self._db.set_reference_timestamp(self._test_id, utc_now_iso())
                except DatabaseError as exc:
                    self._abort_from_error(exc)
                return

    def _tick_measuring(self, now: float, frame) -> None:
        assert self._t_ref is not None
        elapsed = now - self._t_ref
        due = [
            s for s in self._schedule
            if s <= elapsed + 1e-9 and s not in self._recorded and s not in self._missed
        ]
        for slot in due:
            if frame is not None and frame.is_radiometric and self._camera.state == CAM_CONNECTED:
                try:
                    self._capture(slot, frame)
                except DatabaseError as exc:
                    self._abort_from_error(exc)
                    return
            else:
                # Missed sample: stays blank, schedule is never shifted.
                self._missed.add(slot)

        if elapsed >= self._duration + 0.05:
            final_slot = self._duration
            if final_slot in self._recorded:
                self._decide_from_sample(self._samples[final_slot])
            else:
                self._finish(STATUS_INCOMPLETE, None, REASON_T60_MISSING)

    # -- capture / decide ------------------------------------------------------
    def _capture(self, slot: float, frame) -> None:
        ambient = compute_ambient(frame.matrix)
        regions_out: dict[str, dict] = {}
        for region in self._preset.regions:
            mm = measure_region(frame.matrix, region)
            if mm is None:
                continue
            delta = mm[1] - ambient
            regions_out[region.label] = {
                "min": mm[0], "max": mm[1], "delta_t": delta,
                "status": evaluate_region(delta, region.max_allowed_delta_t),
                "max_allowed": region.max_allowed_delta_t,
            }
        self._record_sample(slot, frame, ambient, regions_out, is_baseline=False)
        self._samples[slot] = {
            "t": slot, "ambient": ambient, "regions": regions_out,
            "timestamp": frame.wall_time,
        }

    def _record_sample(
        self, t: float, frame, ambient: float, regions_out: dict, is_baseline: bool
    ) -> None:
        sample_id = self._db.add_sample(self._test_id, t, frame.wall_time, ambient)
        for label, data in regions_out.items():
            self._db.add_region_measurement(
                sample_id, label, data["min"], data["max"], data["delta_t"], data["status"]
            )
        self._recorded.add(float(t))
        self.sampleRecorded.emit({
            "t": float(t), "ambient": ambient, "regions": regions_out,
            "is_baseline": is_baseline,
        })

    def _decide_from_sample(self, sample: dict) -> None:
        failing = [
            (label, data) for label, data in sample["regions"].items()
            if data["status"] == RESULT_FAIL
        ]
        if failing:
            overall = RESULT_FAIL
            reason = "; ".join(
                f"Region {label} exceeded allowed DeltaT "
                f"({data['delta_t']:.1f} > {data['max_allowed']:.1f} C)"
                for label, data in failing
            )
        else:
            overall = RESULT_PASS
            reason = None
        self._deciding_sample = sample
        self._finish(overall, overall, reason)

    # -- stop paths ----------------------------------------------------------
    def emergency_stop(self) -> None:
        """Immediate stop; collected data saved, no final snapshot taken."""
        if self.is_running:
            self._finish(STATUS_INCOMPLETE, None, REASON_EMERGENCY)

    def abort(self, reason: str) -> None:
        """Stop from outside (e.g. application close during test)."""
        if self.is_running:
            self._finish(STATUS_INCOMPLETE, None, reason)

    def _abort_from_error(self, exc: DatabaseError) -> None:
        self.statusMessage.emit(f"Database error: {exc}")
        self._finish(STATUS_INCOMPLETE, None, REASON_DB_WRITE)

    # -- finish -----------------------------------------------------------------
    def _finish(self, status: str, overall_result: Optional[str], reason: Optional[str]) -> None:
        self._timer.stop()
        if self._machine.phase != TestPhase.FINISHED:
            self._machine.transition(TestPhase.FINISHED)
        self.phaseChanged.emit(PHASE_FINISHED)
        if self._test_id is not None:
            try:
                self._db.complete_test(self._test_id, overall_result, status, reason)
            except DatabaseError as exc:
                self.statusMessage.emit(f"Could not save test result: {exc}")
        result = self._build_result(status, overall_result, reason)
        self.statusMessage.emit(status if not reason else f"{status} - {reason}")
        self.testFinished.emit(result)

    def reset(self) -> None:
        """Return to IDLE (a new test may then be started)."""
        if self.is_running:
            self._finish(STATUS_INCOMPLETE, None, "Controller Reset")
        self._machine.reset()
        self._reset_runtime()
        self.phaseChanged.emit(PHASE_IDLE)

    def _build_result(self, status: str, overall_result: Optional[str], reason: Optional[str]) -> dict:
        sample = self._deciding_sample or self._last_sample()
        regions: list[dict] = []
        if sample is not None:
            for region in self._preset.regions:
                data = sample["regions"].get(region.label)
                if data is None:
                    continue
                regions.append({
                    "label": region.label,
                    "min": data["min"],
                    "max": data["max"],
                    "ambient": sample["ambient"],
                    "delta_t": data["delta_t"],
                    "max_allowed": region.max_allowed_delta_t,
                    "status": data["status"],
                    "exceedance": max(0.0, data["delta_t"] - region.max_allowed_delta_t),
                })
        return {
            "test_id": self._test_id,
            "test_type": TEST_TYPE_STANDARD,
            "status": status,
            "overall_result": overall_result,
            "reason": reason,
            "started": True,
            "regions": regions,
            "deciding_sample_t": sample["t"] if sample else None,
        }

    def _last_sample(self) -> Optional[dict]:
        if not self._samples:
            return None
        last_slot = max(self._samples)
        return self._samples[last_slot]
