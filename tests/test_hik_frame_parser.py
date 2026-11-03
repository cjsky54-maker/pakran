"""Unit tests for the HikCamera frame parser / stream classifier.

Synthetic frames are built byte-exact to the documented wire layouts
(see app/camera/hik_frame_parser.py and docs/CAMERA_INTEGRATION.md).
"""
from __future__ import annotations

import numpy as np
import pytest

from app.camera.hik_frame_parser import (
    HikStreamClassifier,
    _codes_to_celsius,
    _scan_camera_reported,
)
from app.core.constants import (
    HIK_DEFAULT_COUNTS_OFF,
    HIK_DEFAULT_FRAME_BYTES,
    HIK_DEFAULT_METADATA_OFF,
    HIK_DEFAULT_PREVIEW_OFF,
    HIK_DEFAULT_SUMMARY_OFF,
    HIK_M8_FRAME_BYTES,
    HIK_M8_PREVIEW_OFF,
    HIK_M8_TEMPS_OFF,
    HIK_TEMP_SCALE,
    SENSOR_HEIGHT,
    SENSOR_WIDTH,
)

PIXELS = SENSOR_WIDTH * SENSOR_HEIGHT


def _yuyv_from_luma(luma: np.ndarray) -> np.ndarray:
    """Build a 192x256x2 YUYV buffer from a luma plane (neutral chroma)."""
    y = np.clip(luma, 0, 255).astype(np.uint8)
    yuyv = np.zeros((SENSOR_HEIGHT, SENSOR_WIDTH, 2), dtype=np.uint8)
    yuyv[:, 0::2, 0] = y[:, 0::2]
    yuyv[:, 1::2, 0] = y[:, 1::2]
    yuyv[:, 0::2, 1] = 128
    yuyv[:, 1::2, 1] = 128
    return yuyv


def _temperature_field(seed: int = 0, jitter: float = 0.0) -> np.ndarray:
    """A plausible Celsius scene: warm elliptical hotspot on cool ambient."""
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:SENSOR_HEIGHT, 0:SENSOR_WIDTH]
    hotspot = 20.0 + 22.0 * np.exp(
        -(((xx - 150) / 60.0) ** 2 + ((yy - 90) / 40.0) ** 2)
    )
    noise = rng.normal(0, 0.05, size=hotspot.shape) if jitter else 0.0
    return (hotspot + noise).astype(np.float32)


def _celsius_to_codes(celsius: np.ndarray) -> np.ndarray:
    return np.round((celsius + 50.0) * HIK_TEMP_SCALE).astype("<u2")


def build_mode8_frame(seed: int = 0, jitter: float = 0.0,
                      correlated: bool = True) -> bytes:
    """Byte-exact calibrated (mode 8) frame: header + temps + YUYV preview."""
    temps = _temperature_field(seed, jitter)
    codes = _celsius_to_codes(temps)
    buf = bytearray(HIK_M8_FRAME_BYTES)
    buf[HIK_M8_TEMPS_OFF:HIK_M8_TEMPS_OFF + PIXELS * 2] = codes.tobytes()
    if correlated:
        # Preview luma tracks temperature (the camera visualises the temps).
        luma = ((temps - temps.min())
                / max(float(np.ptp(temps)), 1e-6) * 200 + 20)
    else:
        luma = np.full((SENSOR_HEIGHT, SENSOR_WIDTH), 128.0)
    preview = _yuyv_from_luma(luma)
    buf[HIK_M8_PREVIEW_OFF:HIK_M8_PREVIEW_OFF + PIXELS * 2] = preview.tobytes()
    return bytes(buf)


def build_default_frame(seed: int = 0, dense_metadata: bool = False,
                        meta_seed: int = 0) -> bytes:
    """Byte-exact default-mode frame: counts + metadata + preview + summary."""
    rng = np.random.default_rng(seed)
    buf = bytearray(HIK_DEFAULT_FRAME_BYTES)
    # Raw sensor counts - NOT temperatures (mid-scale 14-bit-ish values).
    counts = rng.integers(7_000, 9_000, size=PIXELS).astype("<u2")
    buf[HIK_DEFAULT_COUNTS_OFF:HIK_DEFAULT_COUNTS_OFF + PIXELS * 2] = \
        counts.tobytes()
    # Metadata: parameters + padding (zeros) unless dense is requested.
    if dense_metadata:
        meta = np.random.default_rng(meta_seed).integers(
            0, 200, size=2048, dtype=np.uint8
        ).tobytes()
    else:
        meta = bytes(2040) + bytes([95, 0, 100, 0, 1, 0, 0, 0])  # padding+params
    buf[HIK_DEFAULT_METADATA_OFF:HIK_DEFAULT_METADATA_OFF + 2048] = meta
    # Preview tracks the counts (the camera visualises its sensor data).
    luma = ((counts.reshape(SENSOR_HEIGHT, SENSOR_WIDTH).astype(np.float32)
             - 7000) / 2000 * 200 + 20)
    preview = _yuyv_from_luma(luma)
    buf[HIK_DEFAULT_PREVIEW_OFF:HIK_DEFAULT_PREVIEW_OFF + PIXELS * 2] = \
        preview.tobytes()
    # Summary: camera-reported min/max temp codes + coordinates.
    summary = bytearray(2048)
    summary[0:2] = (int((21.5 + 50) * HIK_TEMP_SCALE)).to_bytes(2, "little")
    summary[4:6] = (int((38.0 + 50) * HIK_TEMP_SCALE)).to_bytes(2, "little")
    summary[8:10] = (150).to_bytes(2, "little")   # x coordinate
    summary[12:14] = (90).to_bytes(2, "little")   # y coordinate
    buf[HIK_DEFAULT_SUMMARY_OFF:HIK_DEFAULT_SUMMARY_OFF + 2048] = bytes(summary)
    return bytes(buf)


# -- conversion -------------------------------------------------------------


def test_code_conversion_is_exact():
    codes = np.array([0, 4480, 5120, 12800], dtype="<u2")
    temps = _codes_to_celsius(codes)
    assert temps.tolist() == pytest.approx([-50.0, 20.0, 30.0, 150.0])


# -- default mode ------------------------------------------------------------


def test_default_frame_is_visual_only_with_camera_reported_temps():
    clf = HikStreamClassifier()
    result = clf.classify(build_default_frame(seed=1))
    assert result.layout == "default"
    assert result.radiometric is False
    assert result.temp_matrix is None
    assert result.preview_rgb is not None
    assert result.preview_rgb.shape == (SENSOR_HEIGHT, SENSOR_WIDTH, 3)
    # Camera-reported values are informational only.
    assert result.camera_min_c == pytest.approx(21.5, abs=0.05)
    assert result.camera_max_c == pytest.approx(38.0, abs=0.05)


def test_default_mode_with_dense_metadata_stays_visual():
    """Default frames with dense (non-zero, non-repeating) metadata are
    still recognised as default mode: their metadata window is not
    continuous with the sensor-count plane, so the mode-8 gates reject
    the frame and the safe default interpretation wins."""
    clf = HikStreamClassifier()
    frames = [build_default_frame(seed=s, dense_metadata=True, meta_seed=42 + s)
              for s in range(4)]
    results = [clf.classify(f) for f in frames]
    assert all(r.layout == "default" for r in results)
    assert all(r.radiometric is False for r in results)


def test_raw_counts_are_never_reported_as_temperatures():
    """A default frame whose counts happen to fall into the temperature band
    must not be misread as a calibrated frame (safety gate)."""
    clf = HikStreamClassifier()
    buf = bytearray(build_default_frame(seed=2, dense_metadata=True))
    # Metadata dense AND unique per frame would be needed to even reach the
    # mode-8 branch; give each frame fresh random metadata.
    r1 = clf.classify(build_default_frame(seed=3, dense_metadata=True, meta_seed=1))
    r2 = clf.classify(build_default_frame(seed=4, dense_metadata=True, meta_seed=2))
    assert r1.radiometric is False and r2.radiometric is False


# -- calibrated mode 8 --------------------------------------------------------


def test_mode8_frame_becomes_radiometric_after_confirmation():
    clf = HikStreamClassifier()
    r1 = clf.classify(build_mode8_frame(seed=5))
    assert r1.layout == "calibrated"
    assert r1.radiometric is False          # awaiting confirmation
    r2 = clf.classify(build_mode8_frame(seed=5))
    assert r2.radiometric is True
    assert r2.temp_matrix is not None
    assert r2.temp_matrix.shape == (SENSOR_HEIGHT, SENSOR_WIDTH)
    expected = _temperature_field(5)
    np.testing.assert_allclose(r2.temp_matrix, expected, atol=0.05)
    assert r2.preview_rgb is not None


def test_mode8_requires_correlated_preview():
    """A mode-8-shaped frame whose preview does not track the temperatures
    is rejected (protects against misinterpreting unknown data)."""
    clf = HikStreamClassifier()
    for _ in range(4):
        r = clf.classify(build_mode8_frame(seed=6, correlated=False))
        assert r.radiometric is False


def test_mode8_with_out_of_range_codes_is_rejected():
    buf = bytearray(build_mode8_frame(seed=7))
    codes = np.frombuffer(
        buf[HIK_M8_TEMPS_OFF:HIK_M8_TEMPS_OFF + PIXELS * 2], dtype="<u2"
    ).copy()
    codes[0] = 60_000          # ~890 degC - outside the -50..550 band
    buf[HIK_M8_TEMPS_OFF:HIK_M8_TEMPS_OFF + PIXELS * 2] = codes.tobytes()
    clf = HikStreamClassifier()
    for _ in range(4):
        r = clf.classify(bytes(buf))
        assert r.radiometric is False


def test_classifier_resets_between_connections():
    clf = HikStreamClassifier()
    clf.classify(build_mode8_frame(seed=8))
    clf.classify(build_mode8_frame(seed=8))
    assert clf.radiometric is True
    clf.reset()
    assert clf.radiometric is False


# -- malformed input -----------------------------------------------------------


def test_wrong_frame_size_is_rejected():
    clf = HikStreamClassifier()
    result = clf.classify(bytes(123_456))
    assert result.layout is None
    assert result.radiometric is False
    assert result.preview_rgb is None
    assert result.reason


def test_truncated_mode8_preview_rejected():
    buf = build_mode8_frame(seed=9)[:HIK_M8_PREVIEW_OFF + 1_000]
    clf = HikStreamClassifier()
    for _ in range(4):
        r = clf.classify(buf)
        assert r.radiometric is False


def test_scan_camera_reported_filters_coordinates():
    frame = bytearray(HIK_DEFAULT_FRAME_BYTES)
    window = bytearray(2048)
    window[0:2] = (int((25.0 + 50) * HIK_TEMP_SCALE)).to_bytes(2, "little")
    window[4:6] = (int((31.0 + 50) * HIK_TEMP_SCALE)).to_bytes(2, "little")
    window[8:10] = (200).to_bytes(2, "little")     # x coord -> -46.9 degC
    window[12:14] = (95).to_bytes(2, "little")     # emissivity -> -48.5 degC
    frame[HIK_DEFAULT_SUMMARY_OFF:HIK_DEFAULT_SUMMARY_OFF + 2048] = bytes(window)
    lo, hi = _scan_camera_reported(bytes(frame))
    assert lo == pytest.approx(25.0, abs=0.05)
    assert hi == pytest.approx(31.0, abs=0.05)
