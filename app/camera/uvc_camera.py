"""Best-effort UVC camera backend (OpenCV VideoCapture).

Purpose: display a live thermal image when the camera exposes a standard
UVC video stream on Windows. A UVC stream carries only imagery; it does
NOT provide a radiometric temperature matrix. Frames from this backend
always have ``matrix=None`` and all temperature-dependent features are
disabled while it is active - the image is never used to estimate
temperatures.
"""
from __future__ import annotations

import time

import cv2

# Keep the console clean when probing indices that do not exist.
try:
    cv2.setLogLevel(cv2.LOG_LEVEL_ERROR)
except Exception:  # pragma: no cover - older OpenCV builds
    pass

from app.camera.camera_interface import CameraError, CameraInterface
from app.camera.camera_models import CameraInfo
from app.core.jalali import utc_now_iso
from app.thermal.thermal_frame import ThermalFrame


class UvcCamera(CameraInterface):
    """A UVC video device opened through OpenCV."""

    def __init__(self, index: int) -> None:
        self.index = int(index)
        self._cap: cv2.VideoCapture | None = None
        self.info = CameraInfo(
            key=f"uvc:{self.index}",
            name=f"UVC Camera {self.index}",
            kind="uvc",
            is_radiometric=False,
            is_simulated=False,
            details="Visual stream only - radiometric measurements disabled",
        )

    # -- CameraInterface --------------------------------------------------
    def open(self) -> None:
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
        self._cap = cap

    def close(self) -> None:
        if self._cap is not None:
            self._cap.release()
            self._cap = None

    def is_open(self) -> bool:
        return self._cap is not None and self._cap.isOpened()

    def set_emissivity(self, value: float) -> None:
        # Not applicable to a visual-only UVC stream.
        return

    def read_frame(self) -> ThermalFrame:
        if self._cap is None:
            raise CameraError("UVC camera is not open")
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
                        details="Visual stream only - radiometric measurements disabled",
                    )
                )
            cap.release()
        except Exception:  # pragma: no cover - platform dependent
            continue
    return found
