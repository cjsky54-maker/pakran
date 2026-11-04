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
