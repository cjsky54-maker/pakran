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

Opening is strictly BOUNDED in time (MSMF open/read timeouts, a small
number of probe reads) and the visual fallback RETRIES: after the raw
probe releases the device the OS may need a moment before the same
device can be reopened, which previously surfaced as a spurious
"did not open" error.

THREAD-SAFETY (v1.0.3): OpenCV's MSMF/DirectShow backends are NOT safe
against concurrent use of the same device from two threads - doing so
crashes the whole process with an access violation and NO log line
(it looked like "the app suddenly closes while connecting"). Every
native call in this module therefore holds a PER-DEVICE lock, so the
deep-discovery probe of the device dialog and the acquisition worker's
open() can never race each other. Long probe sequences additionally
accept a ``should_stop`` callback so a cancelled dialog probe aborts
between native calls instead of holding its lock.

BACKEND ORDER + SESSION SETTLE (v1.0.4): crash.log evidence showed the
v1.0.3 access violation inside the ``cv2.VideoCapture`` CONSTRUCTOR
itself (worker thread, first backend = MSMF) - i.e. not a two-thread
race but Media Foundation crashing while (re)opening the HikCamera.
Two defences, both applied everywhere in this module:

1. DIRECTSHOW FIRST. ``cv2.CAP_DSHOW`` is tried before ``cv2.CAP_MSMF``
   for every raw open, and before the platform default for every visual
   open. DirectShow negotiates the odd 256x392 YUY2 media type through
   the classic IAMStreamConfig path and does not access-violate on this
   device class; MSMF stays only as a last-resort fallback.
2. SESSION SETTLE. The per-device locks made the gap between one
   session's ``release()`` and the next ``open()`` effectively ZERO on
   lock handoff (a cancelled-but-in-flight dialog probe hands the lock
   straight to the acquisition worker). Every release is timestamped
   per device and every open first waits out ``_DEVICE_SETTLE_S`` since
   that timestamp, so the driver/MF pipeline always gets its release
   window.
"""
from __future__ import annotations

import threading
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
_GENERIC_UVC_DETAILS = "USB video device - capability is checked when connected"
_HIK_W, HIK_H = SENSOR_WIDTH, SENSOR_HEIGHT + 200   # 256 x 392 stream
_RAW_FRAME_SIZES = (HIK_DEFAULT_FRAME_BYTES, HIK_M8_FRAME_BYTES)
_PROBE_READS = 5            # raw-open probe reads per backend
_DISCOVERY_PROBE_READS = 4  # deep-discovery reads (enough to confirm mode 8)
_VISUAL_OPEN_ATTEMPTS = 3   # visual fallback retries (device may be busy)
_VISUAL_RETRY_DELAY_S = 1.5
_MSMF_OPEN_TIMEOUT_MS = 4000
_MSMF_READ_TIMEOUT_MS = 1200

# DirectShow first, Media Foundation only as a fallback (v1.0.4 - see
# the module docstring: the MSMF open access-violates on the HikCamera).
_RAW_BACKENDS = (cv2.CAP_DSHOW, cv2.CAP_MSMF)

# One lock per device index: native (C++) camera calls for the SAME
# device must never overlap - that is a hard process crash on Windows.
# Cross-device opens are independent and may run in parallel.
_DEVICE_LOCKS: dict[int, threading.RLock] = {}
_DEVICE_LOCKS_GUARD = threading.Lock()

# Monotonic timestamp of the last REAL session release per device index
# (only caps that were actually opened count). The next open on the same
# device waits out the settle window so the OS/driver finishes releasing
# before Media Foundation / DirectShow is asked to open it again.
_DEVICE_SETTLE_S = 1.0
_DEVICE_LAST_RELEASE: dict[int, float] = {}


def _device_lock(index: int) -> threading.RLock:
    with _DEVICE_LOCKS_GUARD:
        lock = _DEVICE_LOCKS.get(index)
        if lock is None:
            lock = threading.RLock()
            _DEVICE_LOCKS[index] = lock
        return lock


def _note_release(index: int) -> None:
    """Record that a real capture session on ``index`` just ended."""
    with _DEVICE_LOCKS_GUARD:
        _DEVICE_LAST_RELEASE[index] = time.monotonic()


def _wait_settle(index: int) -> None:
    """Sleep just long enough that the previous session on ``index`` had
    ``_DEVICE_SETTLE_S`` to fully release before we open it again."""
    with _DEVICE_LOCKS_GUARD:
        last = _DEVICE_LAST_RELEASE.get(index)
    if last is not None:
        remaining = _DEVICE_SETTLE_S - (time.monotonic() - last)
        if remaining > 0.0:
            time.sleep(remaining)


def _release_capture(cap: cv2.VideoCapture, index: int) -> None:
    """Release a capture and timestamp the end of its device session."""
    try:
        cap.release()
    finally:
        _note_release(index)


def _open_visual_capture(index: int) -> cv2.VideoCapture | None:
    """Open a device for visual use, preferring DirectShow (v1.0.4).

    The MSMF backend access-violates while opening some UVC devices (seen
    with the HikCamera raw container), so DirectShow is tried first; the
    platform default backend order remains as a fallback for devices
    DirectShow cannot handle. Returns None when nothing opens the device.
    """
    for backend in (cv2.CAP_DSHOW, cv2.CAP_ANY):
        cap = cv2.VideoCapture(index, backend)
        if cap.isOpened():
            return cap
        cap.release()
    return None


def _set_stream_timeouts(cap: cv2.VideoCapture) -> None:
    """Bound MSMF open/read calls so a stalled device cannot hang forever.

    The properties are MSMF-only and may be absent in older OpenCV builds;
    setting them on other backends is simply ignored.
    """
    for prop, value in (
        (cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, _MSMF_OPEN_TIMEOUT_MS),
        (cv2.CAP_PROP_READ_TIMEOUT_MSEC, _MSMF_READ_TIMEOUT_MS),
    ):
        if hasattr(cv2, "CAP_PROP_OPEN_TIMEOUT_MSEC"):
            try:
                cap.set(prop, value)
            except Exception:  # pragma: no cover - defensive
                pass


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
            details=_GENERIC_UVC_DETAILS,
        )

    # -- CameraInterface --------------------------------------------------
    def open(self) -> None:
        # The WHOLE open sequence (raw probe on two backends + visual
        # retries) runs under the device lock: concurrent native opens of
        # the same device are a process-killing access violation.
        with _device_lock(self.index):
            self._open_locked()

    def _open_locked(self) -> None:
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

        # Fallback: plain visual UVC device. The raw probe above just
        # released the device; the OS may need a moment before it can be
        # reopened, so retry instead of failing on the first attempt.
        last_error = ""
        for attempt in range(1, _VISUAL_OPEN_ATTEMPTS + 1):
            _wait_settle(self.index)
            try:
                cap = _open_visual_capture(self.index)
            except Exception as exc:  # pragma: no cover - platform dependent
                raise CameraError(f"Could not open UVC device {self.index}: {exc}") from exc
            if cap is not None:
                _set_stream_timeouts(cap)
                self._hik_raw = False
                self._classifier = None
                self._cap = cap
                return
            last_error = (
                f"UVC device {self.index} did not open (attempt {attempt}/"
                f"{_VISUAL_OPEN_ATTEMPTS}). Check the USB connection and "
                "Windows camera privacy settings."
            )
            if attempt < _VISUAL_OPEN_ATTEMPTS:
                logger.info(
                    "Visual open attempt %d/%d for UVC device %d failed; retrying",
                    attempt, _VISUAL_OPEN_ATTEMPTS, self.index,
                )
                time.sleep(_VISUAL_RETRY_DELAY_S)
        raise CameraError(last_error)

    def close(self) -> None:
        with _device_lock(self.index):
            if self._cap is not None:
                _release_capture(self._cap, self.index)
                self._cap = None
            self._hik_raw = False
            self._classifier = None

    def is_open(self) -> bool:
        with _device_lock(self.index):
            return self._cap is not None and self._cap.isOpened()

    def set_emissivity(self, value: float) -> None:
        # Emissivity is applied by the camera firmware (default 0.95).
        # The UVC stream itself carries no control endpoint we can set from
        # a standard user-space stack; kept as a no-op by design.
        return

    def read_frame(self) -> ThermalFrame:
        with _device_lock(self.index):
            if self._cap is None:
                raise CameraError("UVC camera is not open")
            if self._hik_raw:
                return self._read_hik_frame()
            return self._read_visual_frame()

    # -- HIK raw path -------------------------------------------------------
    def _open_hik_raw(
        self,
        probe_reads: int = _PROBE_READS,
        should_stop=None,
    ) -> cv2.VideoCapture | None:
        """Try to open the device as a raw HikCamera YUYV stream.

        Attempts the DirectShow and (only as a fallback) MSMF backends
        with an explicit YUY2 format request at 256x392 and RGB conversion
        disabled, then verifies that probe frames really carry the
        HikCamera container size. Returns None when the device does not
        expose that format (caller falls back to the plain visual path).

        ``should_stop`` (a no-arg callable returning True) is honoured
        BETWEEN native calls so a cancelled dialog probe can abort a
        multi-second sequence early; a native call already in flight
        still runs to its bounded timeout.
        """
        fourcc_yuy2 = cv2.VideoWriter_fourcc(*"YUY2")
        for backend in _RAW_BACKENDS:
            if should_stop is not None and should_stop():
                return None
            # Give a just-released session on this device its settle
            # window before the next native open (see module docstring).
            _wait_settle(self.index)
            try:
                cap = cv2.VideoCapture(self.index, backend)
            except Exception:
                continue
            if not cap.isOpened():
                cap.release()
                continue
            _set_stream_timeouts(cap)
            cap.set(cv2.CAP_PROP_FOURCC, fourcc_yuy2)
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, _HIK_W)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, HIK_H)
            cap.set(cv2.CAP_PROP_FPS, 25.0)
            cap.set(cv2.CAP_PROP_CONVERT_RGB, 0)
            if self._probe_raw_frames(cap, probe_reads, should_stop):
                return cap
            _release_capture(cap, self.index)
        return None

    @staticmethod
    def _probe_raw_frames(
        cap: cv2.VideoCapture,
        reads: int = _PROBE_READS,
        should_stop=None,
    ) -> bool:
        """True when a probe frame matches the HikCamera container size."""
        for _ in range(reads):
            if should_stop is not None and should_stop():
                return False
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


# -- discovery -----------------------------------------------------------------

def probe_hik_capability(index: int, should_stop=None) -> dict:
    """Check whether UVC device ``index`` is a HIKMICRO HikCamera.

    Opens the device once with the raw-stream settings (DirectShow first,
    YUY2 256x392, no RGB conversion, short read timeout) and classifies a
    few frames. Used by the device-selection dialog so a real HIKMICRO camera
    is listed under its proper name with an honest Radiometric value and
    instructions, instead of a generic "UVC Camera N / No" entry.

    Runs entirely under the device lock (see module docstring) and can be
    cancelled between native calls via ``should_stop``.

    Returns {"is_hik": bool, "radiometric": bool, "reason": str}.
    """
    result = {"is_hik": False, "radiometric": False, "reason": ""}
    with _device_lock(index):
        cam = UvcCamera(index)
        try:
            cap = cam._open_hik_raw(
                probe_reads=_DISCOVERY_PROBE_READS, should_stop=should_stop
            )
            if cap is None:
                return result
            classifier = HikStreamClassifier()
            parsed = 0
            try:
                for _ in range(_DISCOVERY_PROBE_READS):
                    if should_stop is not None and should_stop():
                        break
                    ok, frame = cap.read()
                    if not ok or frame is None:
                        break
                    if frame.ndim == 3 and frame.shape[2] == 3:
                        break  # converted frame - not the raw HikCamera stream
                    raw = frame.tobytes()
                    if len(raw) not in _RAW_FRAME_SIZES:
                        break
                    outcome = classifier.classify(raw)
                    if outcome.preview_rgb is None:
                        break
                    parsed += 1
            finally:
                _release_capture(cap, index)
            if parsed > 0:
                result["is_hik"] = True
                result["radiometric"] = bool(classifier.radiometric)
                result["reason"] = "calibrated radiometric stream" if result["radiometric"] else (
                    "preview stream (no temperatures) - open HIKMICRO Analyzer "
                    "once, close it, then reconnect to enable the calibrated stream"
                )
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("HikCamera capability probe failed for index %d: %s", index, exc)
    return result


def discover_uvc_cameras(
    max_index: int = 3,
    deep: bool = False,
    should_stop=None,
    skip_keys: tuple[str, ...] = (),
) -> list[CameraInfo]:
    """Probe video device indices and return the ones that open.

    ``deep=True`` additionally runs the HikCamera capability probe so
    HIKMICRO devices are identified by name in the selection dialog.
    The deep probe opens each device again for a few hundred
    milliseconds, so it is only used from the dialog's background
    refresh - never from the periodic reconnect timer.

    ``should_stop`` cancels the (multi-second) deep probe between
    devices/backends; ``skip_keys`` lists device keys (e.g. the camera
    that is currently streaming) whose deep probe must be skipped so a
    dialog refresh never stalls or re-opens the active device.
    """
    found: list[CameraInfo] = []
    for index in range(max_index + 1):
        if should_stop is not None and should_stop():
            break
        key = f"uvc:{index}"
        if key in tuple(skip_keys):
            # Currently streaming: opening a second handle on it can stall
            # or disturb the stream. CameraManager.discover() re-adds the
            # live camera info for this key.
            continue
        with _device_lock(index):
            try:
                _wait_settle(index)
                cap = _open_visual_capture(index)
                if cap is None:
                    continue
                _release_capture(cap, index)
            except Exception:  # pragma: no cover - platform dependent
                continue

        info = CameraInfo(
            key=key,
            name=f"UVC Camera {index}",
            kind="uvc",
            is_radiometric=False,
            is_simulated=False,
            details=_GENERIC_UVC_DETAILS,
        )
        if deep and key not in tuple(skip_keys):
            probe = probe_hik_capability(index, should_stop=should_stop)
            if probe["is_hik"]:
                info = replace(
                    info,
                    name=_HIK_NAME,
                    is_radiometric=bool(probe["radiometric"]),
                    details=(
                        "HikCamera USB stream - "
                        + ("calibrated, per-pixel temperatures available"
                           if probe["radiometric"] else probe["reason"])
                    ),
                )
        found.append(info)
    return found
