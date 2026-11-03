"""Camera discovery, acquisition thread and automatic reconnection.

CameraManager owns exactly one active camera backend. A worker QThread
reads frames and emits them to the GUI thread; the GUI never blocks on
camera I/O. On read failures the worker attempts automatic reconnection
until the configurable reconnection timeout expires, then reports the
camera as disconnected.
"""
from __future__ import annotations

import threading
import time

from PySide6.QtCore import QObject, QThread, Signal, Slot

from app.camera.camera_interface import CameraError, CameraInterface
from app.camera.camera_models import CameraInfo
from app.camera.hikmicro_camera import discover_hikmicro_cameras
from app.camera.simulated_camera import SimulatedCamera
from app.camera.uvc_camera import UvcCamera, discover_uvc_cameras
from app.core.constants import (
    ACQ_FPS_SIMULATED,
    ACQ_FPS_UVC,
    CAM_CONNECTED,
    CAM_CONNECTING,
    CAM_DISCONNECTED,
    CAM_RECONNECTING,
)
from app.logging.log_manager import get_logger
from app.thermal.thermal_frame import ThermalFrame

logger = get_logger("camera")


class _AcquisitionWorker(QObject):
    """Reads frames from one camera inside its own thread."""

    frameReady = Signal(object)
    readError = Signal(str)
    gaveUp = Signal()

    def __init__(self, camera: CameraInterface, fps: float,
                 reconnection_timeout_getter) -> None:
        super().__init__()
        self._camera = camera
        self._interval = 1.0 / max(fps, 1.0)
        self._reconnection_timeout_getter = reconnection_timeout_getter
        self._stop = False
        self._emissivity_lock = threading.Lock()
        self._pending_emissivity: float | None = None

    def request_emissivity(self, value: float) -> None:
        with self._emissivity_lock:
            self._pending_emissivity = float(value)

    @Slot()
    def run(self) -> None:
        reconnect_deadline: float | None = None
        while not self._stop:
            try:
                with self._emissivity_lock:
                    pending = self._pending_emissivity
                    self._pending_emissivity = None
                if pending is not None:
                    self._camera.set_emissivity(pending)
                frame = self._camera.read_frame()
                self.frameReady.emit(frame)
                reconnect_deadline = None
            except CameraError as exc:
                self.readError.emit(str(exc))
                self._camera.close()
                if reconnect_deadline is None:
                    try:
                        timeout = float(self._reconnection_timeout_getter())
                    except Exception:
                        timeout = 10.0
                    reconnect_deadline = time.monotonic() + max(timeout, 1.0)
                if time.monotonic() >= reconnect_deadline:
                    self.gaveUp.emit()
                    return
                time.sleep(1.0)
            except Exception as exc:  # unexpected backend crash
                self.readError.emit(f"Unexpected camera error: {exc}")
                self._camera.close()
                time.sleep(1.0)
            else:
                time.sleep(self._interval)

    def stop(self) -> None:
        self._stop = True


class CameraManager(QObject):
    """Owns the active camera, the latest frame and connection state."""

    frameReady = Signal(object)          # ThermalFrame
    statusChanged = Signal(str, dict)    # state, info-dict
    errorOccurred = Signal(str)

    def __init__(self, settings_getter) -> None:
        super().__init__()
        self._settings_getter = settings_getter
        self._state = CAM_DISCONNECTED
        self._camera: CameraInterface | None = None
        self._worker: _AcquisitionWorker | None = None
        self._thread: QThread | None = None
        self._lock = threading.Lock()
        self._latest: ThermalFrame | None = None
        self._last_error = ""

    # -- state ------------------------------------------------------------
    @property
    def state(self) -> str:
        return self._state

    @property
    def is_connected(self) -> bool:
        return self._state == CAM_CONNECTED

    @property
    def is_radiometric(self) -> bool:
        return bool(self._camera and self._camera.info.is_radiometric)

    @property
    def camera_info(self) -> CameraInfo | None:
        return self._camera.info if self._camera else None

    @property
    def last_error(self) -> str:
        return self._last_error

    # -- discovery / attachment -------------------------------------------
    def discover(self) -> list[CameraInfo]:
        """Enumerate all supported devices (simulated, UVC, SDK)."""
        try:
            show_simulated = bool(self._settings_getter().show_simulated_camera)
        except Exception:
            show_simulated = True
        devices: list[CameraInfo] = []
        if show_simulated:
            devices.append(SimulatedCamera().info)
        try:
            devices.extend(discover_uvc_cameras())
        except Exception as exc:
            self.errorOccurred.emit(f"UVC discovery failed: {exc}")
        devices.extend(discover_hikmicro_cameras())
        return devices

    def attach(self, camera: CameraInterface) -> None:
        """Open a camera and start acquiring frames in a worker thread."""
        self.detach()
        self._camera = camera
        self._set_state(CAM_CONNECTING)
        logger.info("Connecting to camera: %s (%s)", camera.info.name, camera.info.kind)
        try:
            camera.open()
        except CameraError as exc:
            self._last_error = str(exc)
            logger.error("Camera open failed: %s", exc)
            self.errorOccurred.emit(str(exc))
            self._camera = None
            self._set_state(CAM_DISCONNECTED)
            return
        fps = ACQ_FPS_SIMULATED if camera.info.is_simulated else ACQ_FPS_UVC
        self._worker = _AcquisitionWorker(camera, fps, lambda: self._settings_getter().camera_reconnection_timeout)
        self._thread = QThread(self)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.frameReady.connect(self._on_frame)
        self._worker.readError.connect(self._on_read_error)
        self._worker.gaveUp.connect(self._on_gave_up)
        # Apply the current emissivity once connected.
        try:
            self._worker.request_emissivity(self._settings_getter().emissivity)
        except Exception:
            pass
        self._thread.start()

    def detach(self) -> None:
        if self._camera is not None:
            logger.info("Camera detached: %s", self._camera.info.name)
        self._stop_worker()
        if self._camera is not None:
            self._camera.close()
            self._camera = None
        with self._lock:
            self._latest = None
        if self._state != CAM_DISCONNECTED:
            self._set_state(CAM_DISCONNECTED)

    def set_emissivity(self, value: float) -> None:
        if self._worker is not None:
            self._worker.request_emissivity(value)

    # -- frame access -------------------------------------------------------
    def latest_frame(self) -> ThermalFrame | None:
        with self._lock:
            return self._latest

    # -- internal slots ------------------------------------------------------
    def _on_frame(self, frame: ThermalFrame) -> None:
        with self._lock:
            self._latest = frame
        if self._state != CAM_CONNECTED:
            self._set_state(CAM_CONNECTED)

    def _on_read_error(self, message: str) -> None:
        self._last_error = message
        logger.warning("Camera read error: %s", message)
        self.errorOccurred.emit(message)
        if self._state not in (CAM_RECONNECTING, CAM_DISCONNECTED):
            logger.info("Attempting automatic reconnection...")
            self._set_state(CAM_RECONNECTING)

    def _on_gave_up(self) -> None:
        logger.error("Reconnection failed; camera disconnected")
        self._set_state(CAM_DISCONNECTED)

    def _set_state(self, state: str) -> None:
        if state == self._state:
            return
        self._state = state
        info = self.camera_info
        payload = {
            "name": info.name if info else "",
            "kind": info.kind if info else "",
            "is_radiometric": bool(info.is_radiometric) if info else False,
            "is_simulated": bool(info.is_simulated) if info else False,
            "error": self._last_error,
        }
        self.statusChanged.emit(state, payload)

    def _stop_worker(self) -> None:
        if self._worker is not None:
            self._worker.stop()
        if self._thread is not None:
            self._thread.quit()
            self._thread.wait(2000)
        self._worker = None
        self._thread = None


def create_camera(info: CameraInfo) -> CameraInterface:
    """Factory: build a camera backend from a discovered CameraInfo."""
    if info.kind == "simulated":
        return SimulatedCamera()
    if info.kind == "uvc":
        return UvcCamera(int(info.key.split(":", 1)[1]))
    if info.kind == "hikmicro-sdk":
        return __import__(
            "app.camera.hikmicro_camera", fromlist=["HikmicroSdkCamera"]
        ).HikmicroSdkCamera(int(info.key.split(":", 1)[1]))
    raise CameraError(f"Unknown camera kind: {info.kind}")
