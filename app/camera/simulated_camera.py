"""Simulated thermal camera backend (development and automated testing).

Generates clearly identified SYNTHETIC data:
- a base ambient field with sensor-like noise,
- configurable hot spots that heat up over time so the temperature-rise
  trigger, the sampling schedule and PASS/FAIL evaluation can be exercised
  end-to-end without hardware.

This backend must never be presented as a real HIKMICRO camera; its device
name and every emitted frame are explicitly marked as simulated.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

import cv2
import numpy as np

from app.camera.camera_interface import CameraInterface
from app.camera.camera_models import CameraInfo
from app.core.constants import SENSOR_HEIGHT, SENSOR_WIDTH
from app.core.jalali import utc_now_iso
from app.thermal.thermal_frame import ThermalFrame

DEVICE_NAME = "Simulated Thermal Camera (Synthetic Data - Development Only)"


@dataclass
class HotSpot:
    """A heating component model used by the simulated camera."""

    cx: int                 # center x on the sensor
    cy: int                 # center y on the sensor
    sigma: float            # gaussian spread (pixels)
    target_rise: float      # final temperature rise above ambient (deg C)
    ramp_seconds: float = 45.0
    start_delay: float = 0.0
    initial_rise: float = 3.0  # rise at t=0 (below the default 5 deg trigger)


DEFAULT_HOTSPOTS = (
    HotSpot(cx=72, cy=84, sigma=14.0, target_rise=46.0, ramp_seconds=40.0),
    HotSpot(cx=140, cy=70, sigma=11.0, target_rise=34.0, ramp_seconds=50.0),
    HotSpot(cx=196, cy=110, sigma=9.0, target_rise=24.0, ramp_seconds=55.0, start_delay=8.0),
)


class SimulatedCamera(CameraInterface):
    """Deterministic-ish synthetic radiometric source at 256x192."""

    def __init__(
        self,
        hotspots: tuple[HotSpot, ...] = DEFAULT_HOTSPOTS,
        ambient: float = 24.5,
        noise: float = 0.15,
        seed: int | None = None,
    ) -> None:
        self.hotspots = tuple(hotspots)
        self.base_ambient = ambient
        self.noise = noise
        self._rng = np.random.default_rng(seed)
        self._open_since: float | None = None
        self._emissivity = 0.95
        self.info = CameraInfo(
            key="simulated:0",
            name=DEVICE_NAME,
            kind="simulated",
            is_radiometric=True,
            is_simulated=True,
            details="256x192 synthetic matrix - NOT real HIKMICRO hardware",
        )

    # -- CameraInterface ------------------------------------------------
    def open(self) -> None:
        self._open_since = time.monotonic()

    def close(self) -> None:
        self._open_since = None

    def is_open(self) -> bool:
        return self._open_since is not None

    def set_emissivity(self, value: float) -> None:
        # Synthetic source: accepted and stored, does not alter physics.
        self._emissivity = float(value)

    def read_frame(self) -> ThermalFrame:
        if self._open_since is None:
            from app.camera.camera_interface import CameraError
            raise CameraError("Simulated camera is not open")
        elapsed = time.monotonic() - self._open_since
        matrix = self._generate(elapsed)
        return ThermalFrame(
            matrix=matrix,
            image=self._render(matrix),
            timestamp=time.monotonic(),
            wall_time=utc_now_iso(),
            source="simulated",
            is_simulated=True,
        )

    # -- internals -------------------------------------------------------
    def _generate(self, elapsed: float) -> np.ndarray:
        rng = self._rng
        yy, xx = np.mgrid[0:SENSOR_HEIGHT, 0:SENSOR_WIDTH].astype(np.float32)
        matrix = np.full((SENSOR_HEIGHT, SENSOR_WIDTH), self.base_ambient, dtype=np.float32)
        matrix += rng.normal(0.0, self.noise, (SENSOR_HEIGHT, SENSOR_WIDTH)).astype(np.float32)
        for spot in self.hotspots:
            t = max(0.0, elapsed - spot.start_delay)
            progress = 1.0 - np.exp(-t / max(spot.ramp_seconds, 1e-6))
            rise = spot.initial_rise + (spot.target_rise - spot.initial_rise) * progress
            gauss = np.exp(
                -((xx - spot.cx) ** 2 + (yy - spot.cy) ** 2) / (2.0 * spot.sigma ** 2)
            ).astype(np.float32)
            matrix += rise * gauss
        return matrix

    @staticmethod
    def _render(matrix: np.ndarray) -> np.ndarray:
        """Auto-scaled false-color rendering (display only, INFERNO palette)."""
        lo = float(matrix.min())
        hi = float(matrix.max())
        span = (hi - lo) or 1.0
        norm = ((matrix - lo) / span * 255.0).astype(np.uint8)
        bgr = cv2.applyColorMap(norm, cv2.COLORMAP_INFERNO)
        return cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
