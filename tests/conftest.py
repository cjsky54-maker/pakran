"""Shared fixtures: Qt app, temp database, fake camera, test settings."""
from __future__ import annotations

import time

import numpy as np
import pytest

from app.core.configuration import SettingsData
from app.core.constants import SENSOR_HEIGHT, SENSOR_WIDTH
from app.core.jalali import utc_now_iso
from app.database.database_manager import DatabaseManager
from app.thermal.thermal_frame import ThermalFrame


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


class FakeClock:
    """Controllable monotonic clock for deterministic timing tests."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeCameraManager:
    """CameraManager look-alike (state + latest_frame) without Qt threads."""

    def __init__(self) -> None:
        self.state = "connected"
        self._frame: ThermalFrame | None = None

    def set_matrix(self, matrix) -> None:
        self._frame = ThermalFrame(
            matrix=np.asarray(matrix, dtype=np.float32),
            image=None,
            timestamp=time.monotonic(),
            wall_time=utc_now_iso(),
            source="fake",
            is_simulated=False,
        )

    def clear_frame(self) -> None:
        self._frame = None

    def latest_frame(self):
        return self._frame


def make_matrix(ambient: float, blocks: list[tuple[float, int, int, int, int]]) -> np.ndarray:
    """Matrix with uniform ambient plus rectangular constant blocks.

    blocks: (value, x0, y0, x1, y1) - x1/y1 exclusive, clipped to sensor.
    """
    matrix = np.full((SENSOR_HEIGHT, SENSOR_WIDTH), ambient, dtype=np.float32)
    for value, x0, y0, x1, y1 in blocks:
        x0, y0 = max(0, x0), max(0, y0)
        x1, y1 = min(SENSOR_WIDTH, x1), min(SENSOR_HEIGHT, y1)
        matrix[y0:y1, x0:x1] = value
    return matrix


@pytest.fixture()
def db(tmp_path):
    return DatabaseManager(str(tmp_path / "test_qc.db"))


@pytest.fixture()
def fake_camera():
    return FakeCameraManager()


@pytest.fixture()
def clock():
    return FakeClock()


@pytest.fixture()
def fast_settings():
    """Settings with short timings for automated tests."""
    return SettingsData(
        temperature_rise_threshold=5.0,
        waiting_timeout=3.0,
        sample_interval=1,
        camera_reconnection_timeout=2.0,
        emissivity=0.95,
    )
