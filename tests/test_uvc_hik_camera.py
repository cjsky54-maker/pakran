"""Integration tests for the UvcCamera HIK raw-stream path.

cv2.VideoCapture is replaced by a fake that replays byte-exact HikCamera
frames, verifying the full pipeline: raw open probe -> frame parsing ->
device info updates -> ThermalFrame contents.
"""
from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("cv2")

import app.camera.uvc_camera as uvc_module
from app.camera.uvc_camera import UvcCamera
from app.core.constants import (
    HIK_DEFAULT_FRAME_BYTES,
    HIK_M8_FRAME_BYTES,
    SENSOR_HEIGHT,
    SENSOR_WIDTH,
)

from tests.test_hik_frame_parser import (
    build_default_frame,
    build_mode8_frame,
    _temperature_field,
    _celsius_to_codes,
)


class _FakeVideoCapture:
    """Stands in for cv2.VideoCapture; replays prepared raw frames."""

    # class-level test state
    frames: list[bytes] = []
    opened: bool = True
    bgr: bool = False

    def __init__(self, *args, **kwargs):
        self._released = False
        self._read_index = 0
        self._settings: dict[int, float] = {}

    def isOpened(self):
        return self.opened and not self._released

    def set(self, prop, value):
        self._settings[prop] = value
        return True

    def read(self):
        if self.bgr:
            return True, np.zeros((480, 640, 3), dtype=np.uint8)
        if not self.frames:
            return False, None
        raw = self.frames[self._read_index % len(self.frames)]
        self._read_index += 1
        frame = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 1, 2)
        return True, frame

    def release(self):
        self._released = True


@pytest.fixture()
def fake_capture(monkeypatch):
    monkeypatch.setattr(uvc_module.cv2, "VideoCapture", _FakeVideoCapture)
    # Keep the suite fast: the per-device settle window is a no-op in
    # tests (it exists to protect the real MSMF/DirectShow pipeline).
    monkeypatch.setattr(uvc_module, "_DEVICE_SETTLE_S", 0.0)
    _FakeVideoCapture.frames = []
    _FakeVideoCapture.opened = True
    _FakeVideoCapture.bgr = False
    yield _FakeVideoCapture
    _FakeVideoCapture.frames = []
    _FakeVideoCapture.opened = True
    _FakeVideoCapture.bgr = False


def test_hik_calibrated_stream_enables_measurements(fake_capture):
    """A camera left in calibrated (mode 8) mode by HIKMICRO Analyzer
    delivers full per-pixel temperatures through the UVC stream."""
    fake_capture.frames = [build_mode8_frame(seed=1)]
    cam = UvcCamera(0)
    cam.open()
    assert cam.is_open()

    cam.read_frame()                      # first valid frame (confirmation)
    frame = cam.read_frame()               # confirmed calibrated frame
    assert frame.is_radiometric
    assert frame.matrix is not None
    assert frame.matrix.shape == (SENSOR_HEIGHT, SENSOR_WIDTH)
    np.testing.assert_allclose(
        frame.matrix, _temperature_field(1), atol=0.05
    )
    assert frame.image is not None
    assert frame.image.shape == (SENSOR_HEIGHT, SENSOR_WIDTH, 3)
    assert cam.info.is_radiometric
    assert cam.info.name == "HIKMICRO Thermal Camera (USB)"
    assert "Calibrated" in cam.info.details
    cam.close()


def test_hik_default_stream_is_visual_only(fake_capture):
    """The default stream shows the live preview but no temperatures."""
    fake_capture.frames = [build_default_frame(seed=2)]
    cam = UvcCamera(0)
    cam.open()
    frame = cam.read_frame()
    assert not frame.is_radiometric
    assert frame.matrix is None
    assert frame.image is not None
    assert not cam.info.is_radiometric
    assert "Analyzer" in cam.info.details
    cam.close()


def test_mode_switch_detected_without_reconnect(fake_capture):
    """If the stream flips to calibrated mode after connecting, the frame
    itself carries the temperatures and the info flag follows."""
    fake_capture.frames = [build_default_frame(seed=3)]
    cam = UvcCamera(0)
    cam.open()
    first = cam.read_frame()
    assert not first.is_radiometric

    fake_capture.frames = [build_mode8_frame(seed=4)]
    warmup = cam.read_frame()          # awaiting confirmation
    assert not warmup.is_radiometric
    confirmed = cam.read_frame()
    assert confirmed.is_radiometric
    assert cam.info.is_radiometric
    cam.close()


def test_non_hik_camera_falls_back_to_visual(fake_capture):
    """A plain UVC webcam (BGR frames, wrong size) uses the visual path."""
    fake_capture.bgr = True
    fake_capture.frames = []
    cam = UvcCamera(0)
    cam.open()
    assert cam.is_open()
    frame = cam.read_frame()
    assert not frame.is_radiometric
    assert frame.matrix is None
    assert frame.image is not None
    assert frame.image.shape == (480, 640, 3)
    assert cam.info.name == "UVC Camera 0"
    cam.close()


def test_hik_open_failure_raises_camera_error(fake_capture, monkeypatch):
    monkeypatch.setattr(uvc_module, "_VISUAL_RETRY_DELAY_S", 0.0)
    fake_capture.opened = False
    cam = UvcCamera(1)
    from app.camera.camera_interface import CameraError

    with pytest.raises(CameraError):
        cam.open()


def test_visual_open_retries_after_busy_device(fake_capture, monkeypatch):
    """After the raw probe releases a device the OS may need a moment
    before it can be reopened - the visual fallback must retry instead of
    failing on the first attempt (previously surfaced as a spurious
    'did not open' error after ~30 s)."""
    monkeypatch.setattr(uvc_module, "_VISUAL_RETRY_DELAY_S", 0.0)
    fake_capture.bgr = True          # raw probe fails fast (converted frames)
    fake_capture.frames = []
    fake_capture.opened = False      # first two opens fail, then the device appears
    cam = UvcCamera(0)

    class _FlakyCounter:
        n = 0

    original_init = _FakeVideoCapture.__init__

    def flaky_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        _FlakyCounter.n += 1
        # Raw probe (MSMF, DSHOW) + visual attempts 1-2 fail; the third
        # visual attempt finally opens the device.
        self.opened = _FlakyCounter.n > 4

    monkeypatch.setattr(_FakeVideoCapture, "__init__", flaky_init)
    cam.open()
    assert cam.is_open()
    frame = cam.read_frame()          # BGR visual path
    assert frame.image is not None
    assert not frame.is_radiometric
    cam.close()


# -- deep discovery (HikCamera identification in the device dialog) ---------------


def test_deep_discovery_identifies_hikcamera_preview_mode(fake_capture):
    """A HikCamera streaming the default (raw counts) mode is listed under
    its proper name with an honest Radiometric=No and instructions."""
    fake_capture.frames = [build_default_frame(seed=5)]
    devices = uvc_module.discover_uvc_cameras(max_index=0, deep=True)
    assert len(devices) == 1
    dev = devices[0]
    assert dev.kind == "uvc"
    assert dev.name == "HIKMICRO Thermal Camera (USB)"
    assert not dev.is_radiometric
    assert "Analyzer" in dev.details


def test_deep_discovery_identifies_calibrated_hikcamera(fake_capture):
    """A camera left in calibrated mode by HIKMICRO Analyzer is reported
    as radiometric right in the dialog."""
    fake_capture.frames = [build_mode8_frame(seed=6)]
    devices = uvc_module.discover_uvc_cameras(max_index=0, deep=True)
    dev = devices[0]
    assert dev.name == "HIKMICRO Thermal Camera (USB)"
    assert dev.is_radiometric
    assert "calibrated" in dev.details.lower()


def test_deep_discovery_keeps_generic_webcam_label(fake_capture):
    """A plain UVC webcam keeps the generic name and No."""
    fake_capture.bgr = True
    fake_capture.frames = []
    devices = uvc_module.discover_uvc_cameras(max_index=0, deep=True)
    dev = devices[0]
    assert dev.name == "UVC Camera 0"
    assert not dev.is_radiometric


def test_shallow_discovery_does_not_probe_hik_stream(fake_capture):
    """The periodic reconnect timer uses the cheap path: no raw probing."""
    fake_capture.frames = [build_mode8_frame(seed=7)]
    devices = uvc_module.discover_uvc_cameras(max_index=0, deep=False)
    dev = devices[0]
    assert dev.name == "UVC Camera 0"
    assert not dev.is_radiometric


# -- thread safety: per-device locks + cancellable probes (v1.0.3) ------------------
#
# Regression: opening the SAME device from two threads at once (the device
# dialog's deep-discovery probe racing the acquisition worker's open) is a
# native access violation that kills the whole process without any log
# line - "the app suddenly closes while connecting".


def test_device_lock_is_per_index_and_reentrant():
    lock_a = uvc_module._device_lock(0)
    lock_b = uvc_module._device_lock(0)
    lock_c = uvc_module._device_lock(1)
    assert lock_a is lock_b            # same device -> same lock
    assert lock_a is not lock_c        # different devices are independent
    with lock_a:                       # RLock: reentrant on the same thread
        assert lock_a.acquire(blocking=False)
        lock_a.release()


def test_open_serialises_against_concurrent_probe(fake_capture, monkeypatch):
    """UvcCamera.open() must hold the device lock for the whole native
    open sequence, so a deep probe running in parallel can only ever
    wait - never overlap - and the lock is released even when open fails."""
    import threading

    fake_capture.bgr = True
    fake_capture.frames = []
    fake_capture.opened = False
    monkeypatch.setattr(uvc_module, "_VISUAL_RETRY_DELAY_S", 0.0)

    lock = uvc_module._device_lock(0)
    held = []
    real_open = uvc_module.UvcCamera._open_locked

    def spy_open(self):
        # While open() runs, ANOTHER thread must not be able to grab the
        # device lock (that is exactly the concurrent-probe situation the
        # lock exists to prevent).
        outcome = {}

        def attempt():
            outcome["acquired"] = lock.acquire(timeout=0.05)
            if outcome["acquired"]:
                lock.release()

        probe_thread = threading.Thread(target=attempt)
        probe_thread.start()
        probe_thread.join()
        held.append(not outcome["acquired"])
        return real_open(self)

    monkeypatch.setattr(uvc_module.UvcCamera, "_open_locked", spy_open)
    from app.camera.camera_interface import CameraError

    cam = uvc_module.UvcCamera(0)
    with pytest.raises(CameraError):
        cam.open()                      # exhausts the visual retries
    assert held and all(held)
    # Lock was released on the failure path (exception left the `with`).
    assert lock.acquire(blocking=False)
    lock.release()


def test_probe_hik_capability_is_cancellable(fake_capture, monkeypatch):
    """A cancelled dialog probe returns 'not hik' without touching the
    device (no native reads after cancellation)."""
    fake_capture.frames = [build_mode8_frame(seed=8)]
    reads = {"n": 0}
    real_read = _FakeVideoCapture.read

    def counting_read(self):
        reads["n"] += 1
        return real_read(self)

    monkeypatch.setattr(_FakeVideoCapture, "read", counting_read)
    result = uvc_module.probe_hik_capability(0, should_stop=lambda: True)
    assert result == {"is_hik": False, "radiometric": False, "reason": ""}
    assert reads["n"] == 0             # cancelled before any native call


def test_deep_discovery_skips_active_device(fake_capture, monkeypatch):
    """The streaming (active) camera is never re-opened by a dialog
    refresh: its key is skipped ENTIRELY (not even the shallow open runs
    on it - a second handle could stall or disturb the stream) and
    CameraManager.discover() re-adds its live info instead."""
    opened = {"n": 0}
    real_init = _FakeVideoCapture.__init__

    def counting_init(self, *args, **kwargs):
        real_init(self, *args, **kwargs)
        opened["n"] += 1

    monkeypatch.setattr(_FakeVideoCapture, "__init__", counting_init)
    fake_capture.frames = [build_mode8_frame(seed=9)]
    devices = uvc_module.discover_uvc_cameras(
        max_index=0, deep=True, skip_keys=("uvc:0",)
    )
    assert devices == []                # skipped entirely - manager re-adds it
    assert opened["n"] == 0             # never opened at all


# -- v1.0.4: DirectShow first + per-device session settle -------------------------
#
# Regression: crash.log (v1.0.3, EXE) showed "Windows fatal exception:
# access violation" with the faulting thread inside the cv2.VideoCapture
# CONSTRUCTOR (uvc_camera.py line 232, first backend = MSMF) on the
# acquisition worker. Not a two-thread race - Media Foundation itself
# crashes while (re)opening the HikCamera, especially right after another
# session released it.


def test_raw_open_tries_directshow_before_msmf(fake_capture, monkeypatch):
    """The raw open must try DirectShow FIRST and only fall back to MSMF;
    the visual open must also prefer DirectShow."""
    order = []
    real_init = _FakeVideoCapture.__init__

    def recording_init(self, *args, **kwargs):
        order.append(args[1] if len(args) > 1 else uvc_module.cv2.CAP_ANY)
        real_init(self, *args, **kwargs)

    monkeypatch.setattr(_FakeVideoCapture, "__init__", recording_init)
    fake_capture.bgr = True          # raw probe fails -> both backends tried
    fake_capture.frames = []
    cam = UvcCamera(0)
    cam.open()
    try:
        assert order[0] == uvc_module.cv2.CAP_DSHOW   # raw: DSHOW first
        assert order[1] == uvc_module.cv2.CAP_MSMF    # raw: MSMF fallback
        assert order[2] == uvc_module.cv2.CAP_DSHOW   # visual: DSHOW first
    finally:
        cam.close()


def test_reopen_waits_out_settle_window(fake_capture, monkeypatch):
    """After a real session ends, the next open on the same device waits
    out the settle window - the zero-gap reopen (lock handoff from a
    cancelled probe) is what crashed Media Foundation."""
    import time as _time

    monkeypatch.setattr(uvc_module, "_DEVICE_SETTLE_S", 0.2)
    fake_capture.bgr = True
    fake_capture.frames = []
    cam = UvcCamera(0)
    cam.open()
    cam.close()                      # releases + timestamps the session
    start = _time.monotonic()
    uvc_module._wait_settle(0)
    assert _time.monotonic() - start >= 0.15   # settle window honoured

    # Without a recent release there is no wait at all.
    monkeypatch.setattr(uvc_module, "_DEVICE_LAST_RELEASE", {})
    start = _time.monotonic()
    uvc_module._wait_settle(0)
    assert _time.monotonic() - start < 0.1


def test_failed_open_does_not_count_as_session(fake_capture, monkeypatch):
    """A constructor that never opened the device must NOT start a settle
    window - clean failures hold no driver state."""
    fake_capture.opened = False      # every open fails cleanly
    cam = UvcCamera(2)
    from app.camera.camera_interface import CameraError

    with pytest.raises(CameraError):
        cam.open()
    # No release of an actually-opened cap happened on index 2.
    assert 2 not in uvc_module._DEVICE_LAST_RELEASE
