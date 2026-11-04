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
    """Opens and reads frames from one camera inside its own thread.

    Opening happens here on purpose: UVC opens can take seconds (backend
    format negotiation, retries after a busy device) and must never block
    the GUI thread.
    """

    frameReady = Signal(object)
    readError = Signal(str)
    openFailed = Signal(str)
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
        # Nothing may ever escape this slot: PySide6 >= 6.5.2 treats an
        # unhandled exception in a slot as FATAL and kills the process.
        try:
            self._run()
        except BaseException as exc:  # noqa: BLE001 - last-resort guard
            if not self._stop:
                try:
                    self.openFailed.emit(f"Unexpected camera error: {exc}")
                except Exception:
                    pass

    def _run(self) -> None:
        if self._stop:
            return
        # Open OFF the GUI thread (see class docstring).
        try:
            self._camera.open()
        except CameraError as exc:
            if not self._stop:
                self.openFailed.emit(str(exc))
            return
        except Exception as exc:  # unexpected backend crash
            if not self._stop:
                self.openFailed.emit(f"Unexpected camera error: {exc}")
            return
        if self._stop:
            # Detached while opening - clean up on this thread where the
            # device is actually being used.
            self._camera.close()
            return
        reconnect_deadline: float | None = None
        while not self._stop:
            started = time.monotonic()
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
                # Actually try to reopen the device (a closed camera would
                # just keep raising "not open" until the deadline).
                try:
                    self._camera.open()
                except Exception:
                    continue
            except Exception as exc:  # unexpected backend crash
                self.readError.emit(f"Unexpected camera error: {exc}")
                self._camera.close()
                time.sleep(1.0)
            else:
                # Target-rate pacing: real cameras block inside read() for a
                # full frame interval, so the leftover wait is ~0 and the
                # stream runs at its native rate (25 Hz for HikCamera UVC).
                # Simulated cameras return instantly and are paced here.
                delay = self._interval - (time.monotonic() - started)
                if delay > 0:
                    time.sleep(delay)

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
        self._last_frame_radiometric = False
        # Workers still winding down after a blocked open/read; kept alive
        # so their QThread can finish safely (see _stop_worker).
        self._retiring: list = []

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

    @property
    def busy(self) -> bool:
        """True while any acquisition worker exists or is still winding
        down. Discovery/auto-reconnect must not run in this state - the
        previous worker may still hold the per-device lock inside a
        bounded open(), and racing it (even with the lock serialising
        native calls) only produces confusing device lists."""
        return self._worker is not None or bool(self._retiring)

    # -- discovery / attachment -------------------------------------------
    def discover(self, deep: bool = False, should_stop=None) -> list[CameraInfo]:
        """Enumerate all supported devices (simulated, UVC, SDK).

        ``deep=True`` also probes each UVC device for the HikCamera raw
        stream (adds ~1-2 s per device); use it only from the interactive
        device dialog, never from the periodic reconnect timer.

        The device that is currently ATTACHED is never re-opened or
        deep-probed (its live info is used instead), so a dialog refresh
        can safely run while a camera is streaming. ``should_stop`` is
        honoured between native calls and cancels the deep probe.
        """
        try:
            show_simulated = bool(self._settings_getter().show_simulated_camera)
        except Exception:
            show_simulated = True
        active_key = self._camera.info.key if self._camera else None
        devices: list[CameraInfo] = []
        if show_simulated:
            devices.append(SimulatedCamera().info)
        try:
            uvc_devices = discover_uvc_cameras(
                deep=deep,
                should_stop=should_stop,
                skip_keys=(active_key,) if active_key else (),
            )
        except Exception as exc:
            self.errorOccurred.emit(f"UVC discovery failed: {exc}")
            uvc_devices = []
        devices.extend(uvc_devices)
        devices.extend(discover_hikmicro_cameras())
        # Keep the streaming camera listed even when a fresh shallow open
        # reported it as busy/absent (it is obviously still working).
        if active_key is not None and all(d.key != active_key for d in devices):
            if self._camera is not None:
                devices.append(self._camera.info)
        return devices

    def discover_fast(self) -> list[CameraInfo]:
        """Instant device list with NO native camera call at all.

        Simulated camera, SDK devices (pure Python check) and the
        currently attached camera. USB/UVC enumeration is deliberately
        skipped: it opens devices and can block behind a per-device
        lock, so it belongs on a background thread (the device dialog's
        deep refresh), never on the GUI thread.
        """
        try:
            show_simulated = bool(self._settings_getter().show_simulated_camera)
        except Exception:
            show_simulated = True
        devices: list[CameraInfo] = []
        if show_simulated:
            devices.append(SimulatedCamera().info)
        devices.extend(discover_hikmicro_cameras())
        active = self._camera.info if self._camera else None
        if active is not None and all(d.key != active.key for d in devices):
            devices.append(active)
        return devices

    def attach(self, camera: CameraInterface) -> None:
        """Start acquiring frames from a camera.

        The device is OPENED inside the worker thread; the GUI thread
        returns immediately and the status shows "Connecting..." until
        the first frame arrives (or an open failure is reported).
        """
        self.detach()
        self._camera = camera
        self._set_state(CAM_CONNECTING)
        logger.info("Connecting to camera: %s (%s)", camera.info.name, camera.info.kind)
        fps = ACQ_FPS_SIMULATED if camera.info.is_simulated else ACQ_FPS_UVC
        self._worker = _AcquisitionWorker(camera, fps, lambda: self._settings_getter().camera_reconnection_timeout)
        self._thread = QThread(self)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.frameReady.connect(self._on_frame)
        self._worker.readError.connect(self._on_read_error)
        self._worker.openFailed.connect(self._on_open_failed)
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
        stopped = self._stop_worker()
        if self._camera is not None:
            if stopped:
                self._camera.close()
            # else: the worker is still inside open()/read() and closes the
            # device itself when it sees the stop flag - closing it here
            # would race native code from two threads.
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
        radiometric = bool(frame.is_radiometric)
        if self._state != CAM_CONNECTED:
            self._set_state(CAM_CONNECTED)
        elif radiometric != self._last_frame_radiometric:
            # The stream flipped between preview and calibrated mode while
            # staying connected (e.g. HikCamera stream mode change) - force a
            # status refresh so the UI reflects the new capability.
            self._emit_status()
        self._last_frame_radiometric = radiometric

    def _on_read_error(self, message: str) -> None:
        self._last_error = message
        logger.warning("Camera read error: %s", message)
        self.errorOccurred.emit(message)
        if self._state not in (CAM_RECONNECTING, CAM_DISCONNECTED):
            logger.info("Attempting automatic reconnection...")
            self._set_state(CAM_RECONNECTING)

    def _on_open_failed(self, message: str) -> None:
        """Worker could not open the camera - report and clean up."""
        self._last_error = message
        logger.error("Camera open failed: %s", message)
        self._stop_worker()
        if self._camera is not None:
            try:
                self._camera.close()
            except Exception:  # pragma: no cover - defensive
                pass
            self._camera = None
        self._set_state(CAM_DISCONNECTED)
        self.errorOccurred.emit(message)

    def _on_gave_up(self) -> None:
        logger.error("Reconnection failed; camera disconnected")
        self._set_state(CAM_DISCONNECTED)

    def _set_state(self, state: str) -> None:
        if state == self._state:
            return
        self._state = state
        self._emit_status()

    def _emit_status(self) -> None:
        info = self.camera_info
        payload = {
            "name": info.name if info else "",
            "kind": info.kind if info else "",
            "is_radiometric": bool(info.is_radiometric) if info else False,
            "is_simulated": bool(info.is_simulated) if info else False,
            "error": self._last_error,
        }
        self.statusChanged.emit(self._state, payload)

    def _stop_worker(self) -> bool:
        """Stop the acquisition thread.

        Returns True when the thread is fully stopped (safe to touch the
        camera from the calling thread), False when the worker is still
        blocked inside open()/read() and will clean up after itself.
        """
        worker = self._worker
        thread = self._thread
        self._worker = None
        self._thread = None
        if worker is not None:
            worker.stop()
        if thread is None:
            return True
        thread.quit()
        if thread.isRunning() and not thread.wait(2000):
            # The worker is still blocked inside open()/read(). Do NOT
            # touch the camera from here (racing native code); the worker
            # closes the device itself once it sees the stop flag. Keep
            # the references alive until the thread really ends.
            self._retiring.append((thread, worker))

            def _reap(t=thread, w=worker) -> None:
                try:
                    self._retiring.remove((t, w))
                except ValueError:
                    pass

            thread.finished.connect(_reap)
            thread.finished.connect(thread.deleteLater)
            return False
        thread.deleteLater()
        return True


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
