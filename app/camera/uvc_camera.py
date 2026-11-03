"""UVC camera backend (OpenCV VideoCapture) with HIK "HikCamera" support.

Two acquisition paths:

1. HIK raw path - HIKMICRO pocket-class cameras (USB VID 2BDF) expose a
   YUYV 256x392 stream that embeds the thermal preview AND (in the
   calibrated stream mode) a full per-pixel temperature plane. The camera
   is opened with ``CAP_PROP_CONVERT_RGB = 0`` so the raw bytes reach this
   backend untouched, then every frame is parsed by
   :mod:`app.camera.hik_frame_parser`. When the calibrated stream is
   detected, frames carry a genuine temperature matrix and all
   measurements/tests are enabled - identical to the manufacturer's
   ANALYZER software, which reads the same stream.

2. Visual path (fallback) - any other UVC device: OpenCV converts frames
   to BGR; ``matrix`` stays ``None`` and temperature-dependent features
   remain disabled. The image is never used to estimate temperatures.
"""
from __future__ import annotations

import time
from dataclasses import replace

import cv2
import numpy as np

# Keep the console clean when probing indices that do not exist.
try:
    cv2.setLogLevel(cv2.LOG_LEVEL_ERROR)
except Exception:  # pragma: no cover - older OpenCV builds
    pass

from app.camera.camera_interface import CameraError, CameraInterface
from app.camera.camera_models import CameraInfo
from app.camera.hik_frame_parser import HikStreamClassifier
from app.core.constants import (
    HIK_DEFAULT_FRAME_BYTES,
    HIK_M8_FRAME_BYTES,
    SENSOR_HEIGHT,
    SENSOR_WIDTH,
)
from app.core.jalali import utc_now_iso
from app.logging.log_manager import get_logger
from app.thermal.thermal_frame import ThermalFrame

logger = get_logger("camera.uvc")

_HIK_NAME = "HIKMICRO Thermal Camera (USB)"
_HIK_W, HIK_H = SENSOR_WIDTH, SENSOR_HEIGHT + 200   # 256 x 392 stream
_RAW_FRAME_SIZES = (HIK_DEFAULT_FRAME_BYTES, HIK_M8_FRAME_BYTES)
_PROBE_READS = 12


class UvcCamera(CameraInterface):
    """A UVC video device opened through OpenCV."""

    def __init__(self, index: int) -> None:
        self.index = int(index)
        self._cap: cv2.VideoCapture | None = None
        self._hik_raw = False
        self._classifier: HikStreamClassifier | None = None
        self._reported_radiometric: bool | None = None
        self.info = CameraInfo(
            key=f"uvc:{self.index}",
            name=f"UVC Camera {self.index}",
            kind="uvc",
            is_radiometric=False,
            is_simulated=False,
            details="USB video device - capability is checked when connected",
        )

    # -- CameraInterface --------------------------------------------------
    def open(self) -> None:
        cap = self._open_hik_raw()
        if cap is not None:
            self._cap = cap
            self._hik_raw = True
            self._classifier = HikStreamClassifier()
            self._reported_radiometric = None
            self.info = replace(
                self.info,
                name=_HIK_NAME,
                details="HikCamera USB stream - analysing frames",
            )
            logger.info(
                "Opened %s as raw HikCamera stream (YUYV %dx%d, 25 fps target)",
                self.info.name, _HIK_W, HIK_H,
            )
            return

        # Fallback: plain visual UVC device.
        try:
            cap = cv2.VideoCapture(self.index)
        except Exception as exc:  # pragma: no cover - platform dependent
            raise CameraError(f"Could not open UVC device {self.index}: {exc}") from exc
        if not cap.isOpened():
            cap.release()
            raise CameraError(
                f"UVC device {self.index} did not open. Check the USB connection "
                "and Windows camera privacy settings."
            )
        self._hik_raw = False
        self._classifier = None
        self._cap = cap

    def close(self) -> None:
        if self._cap is not None:
            self._cap.release()
            self._cap = None
        self._hik_raw = False
        self._classifier = None

    def is_open(self) -> bool:
        return self._cap is not None and self._cap.isOpened()

    def set_emissivity(self, value: float) -> None:
        # Emissivity is applied by the camera firmware (default 0.95).
        # The UVC stream itself carries no control endpoint we can set from
        # a standard user-space stack; kept as a no-op by design.
        return

    def read_frame(self) -> ThermalFrame:
        if self._cap is None:
            raise CameraError("UVC camera is not open")
        if self._hik_raw:
            return self._read_hik_frame()
        return self._read_visual_frame()

    # -- HIK raw path -------------------------------------------------------
    def _open_hik_raw(self) -> cv2.VideoCapture | None:
        """Try to open the device as a raw HikCamera YUYV stream.

        Attempts the MSMF and DirectShow backends with an explicit YUY2
        format request at 256x392 and RGB conversion disabled, then
        verifies that probe frames really carry the HikCamera container
        size. Returns None when the device does not expose that format
        (caller falls back to the plain visual path).
        """
        fourcc_yuy2 = cv2.VideoWriter_fourcc(*"YUY2")
        for backend in (cv2.CAP_MSMF, cv2.CAP_DSHOW, None):
            try:
                cap = (
                    cv2.VideoCapture(self.index, backend)
                    if backend is not None else cv2.VideoCapture(self.index)
                )
            except Exception:
                continue
            if not cap.isOpened():
                cap.release()
                continue
            cap.set(cv2.CAP_PROP_FOURCC, fourcc_yuy2)
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, _HIK_W)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, HIK_H)
            cap.set(cv2.CAP_PROP_FPS, 25.0)
            cap.set(cv2.CAP_PROP_CONVERT_RGB, 0)
            if self._probe_raw_frames(cap):
                return cap
            cap.release()
        return None

    @staticmethod
    def _probe_raw_frames(cap: cv2.VideoCapture) -> bool:
        """True when a probe frame matches the HikCamera container size."""
        for _ in range(_PROBE_READS):
            ok, frame = cap.read()
            if ok and frame is not None and frame.dtype == np.uint8:
                if frame.nbytes in _RAW_FRAME_SIZES:
                    return True
                # A BGR/converted frame means this backend cannot deliver
                # the raw bytes - stop probing with it.
                if frame.ndim == 3 and frame.shape[2] == 3:
                    return False
        return False

    def _read_hik_frame(self) -> ThermalFrame:
        ok, frame = self._cap.read()
        if not ok or frame is None:
            raise CameraError(f"Failed to read frame from {self.info.name}")
        raw = frame.tobytes()
        assert self._classifier is not None
        result = self._classifier.classify(raw)
        self._sync_info(result)
        if result.preview_rgb is None:
            raise CameraError(
                f"Unexpected frame size from {self.info.name}: {len(raw)} bytes"
            )
        return ThermalFrame(
            matrix=result.temp_matrix,
            image=result.preview_rgb,
            timestamp=time.monotonic(),
            wall_time=utc_now_iso(),
            source="uvc",
            is_simulated=False,
        )

    def _sync_info(self, result) -> None:
        """Reflect the detected stream mode in the device info + logs."""
        radiometric = bool(result.radiometric)
        changed = (
            self._reported_radiometric is None
            or radiometric != self._reported_radiometric
        )
        if changed:
            if radiometric:
                details = "Calibrated thermal stream - measurements enabled"
                logger.info("HIK calibrated radiometric stream detected")
            elif result.layout == "default":
                details = (
                    "Preview stream (raw counts) - open HIKMICRO Analyzer once, "
                    "close it, then reconnect to enable the calibrated stream"
                )
            else:
                details = f"Stream not recognised: {result.reason}"
            self.info = replace(
                self.info, is_radiometric=radiometric, details=details
            )
            logger.info("HIK stream mode: %s", result.reason)
        self._reported_radiometric = radiometric

    # -- visual fallback path -------------------------------------------------
    def _read_visual_frame(self) -> ThermalFrame:
        ok, bgr = self._cap.read()
        if not ok or bgr is None:
            raise CameraError(f"Failed to read frame from UVC device {self.index}")
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        return ThermalFrame(
            matrix=None,
            image=rgb,
            timestamp=time.monotonic(),
            wall_time=utc_now_iso(),
            source="uvc",
            is_simulated=False,
        )


def discover_uvc_cameras(max_index: int = 3) -> list[CameraInfo]:
    """Probe video device indices and return the ones that open."""
    found: list[CameraInfo] = []
    for index in range(max_index + 1):
        try:
            cap = cv2.VideoCapture(index)
            if cap.isOpened():
                found.append(
                    CameraInfo(
                        key=f"uvc:{index}",
                        name=f"UVC Camera {index}",
                        kind="uvc",
                        is_radiometric=False,
                        is_simulated=False,
                        details=(
                            "USB video device - capability is checked when "
                            "connected"
                        ),
                    )
                )
            cap.release()
        except Exception:  # pragma: no cover - platform dependent
            continue
    return found
