"""Parser and safety classifier for HIK "HikCamera" UVC thermal frames.

HIKMICRO pocket-class cameras (USB VID 2BDF, 256x192 VOx core) expose their
thermal data through a standard UVC video stream in YUYV format at
256 x 392 pixels, 25 fps. The stream carries MORE than an image: the 392
rows are a container with a documented layout (reverse-engineered and
published for the HT-203U "2bdf:0102 HikCamera", firmware 4.09; see
docs/CAMERA_INTEGRATION.md for the full protocol notes):

Default stream mode (200,704 bytes):
    rows   0-191   256x192 little-endian u16 RAW SENSOR COUNTS (not temps)
    rows  192-195   parameters / metadata / padding  (updates at ~2.5 Hz)
    rows  196-387   256x192 YUYV thermal preview
    rows  388-391   camera-computed temperature summaries / padding

Calibrated "mode 8" stream (201,248 bytes; the OS UVC stack may truncate
to the advertised 200,704 - the temperature plane ends at byte 102,944,
well within both sizes):
    bytes      0 - 4,639    proprietary header
    bytes  4,640 - 102,943  256x192 little-endian u16 TEMPERATURE CODES
    bytes 102,944 - end     256x192 YUYV thermal preview

Temperature conversion (confirmed against the manufacturer application):
    celsius = code / 64.0 - 50.0

SAFETY DESIGN - this module never guesses temperatures:
- The default-mode upper plane holds RAW sensor counts; it is NEVER
  converted to temperature.
- A frame is only reported as radiometric when it parses as a mode-8
  frame AND passes strict statistical gates (value ranges, distribution
  spread, preview/temperature correlation) for several consecutive
  frames.
- Ambiguous or failing frames fall back to visual-only with a reason.

The camera also reports its own computed min/max temperatures in the
default-mode summary rows; those are extracted as informational values
only (never used as measurement data).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np

from app.core.constants import (
    HIK_DEFAULT_FRAME_BYTES,
    HIK_DEFAULT_METADATA_OFF,
    HIK_DEFAULT_PREVIEW_OFF,
    HIK_DEFAULT_SUMMARY_OFF,
    HIK_M8_FRAME_BYTES,
    HIK_M8_PREVIEW_OFF,
    HIK_M8_TEMPS_OFF,
    HIK_MIN_CONSECUTIVE_M8,
    HIK_TEMP_OFFSET,
    HIK_TEMP_SCALE,
    SENSOR_HEIGHT,
    SENSOR_WIDTH,
)

_PIXELS = SENSOR_WIDTH * SENSOR_HEIGHT            # 49,152
_TEMP_PLANE_BYTES = _PIXELS * 2                   # 98,304
_VALID_FRAME_SIZES = (HIK_DEFAULT_FRAME_BYTES, HIK_M8_FRAME_BYTES)

# Metadata window used to distinguish the two layouts.

# Gates for accepting a mode-8 candidate as radiometric.
_CODE_MIN, _CODE_MAX = 0, int((550.0 - HIK_TEMP_OFFSET) * HIK_TEMP_SCALE)  # -50..550 C
_CODE_TYPICAL_MIN = int((-30.0 - HIK_TEMP_OFFSET) * HIK_TEMP_SCALE)        # -30 C
_CODE_TYPICAL_MAX = int((250.0 - HIK_TEMP_OFFSET) * HIK_TEMP_SCALE)        # 250 C
_TYPICAL_FRACTION = 0.95
_STD_MIN, _STD_MAX = 0.05 * HIK_TEMP_SCALE, 80.0 * HIK_TEMP_SCALE
_CORRELATION_MIN = 0.75
_META_CONTINUITY_MIN = 0.50      # meta window vs adjacent temp rows
_ZERO_FRACTION_MAX = 0.30          # metadata padding in default mode


@dataclass
class HikParseResult:
    """Outcome of parsing one raw HikCamera UVC frame."""

    layout: Optional[str] = None        # 'default' | 'calibrated' | None
    radiometric: bool = False
    temp_matrix: Optional[np.ndarray] = None   # float32 (192, 256) Celsius
    preview_rgb: Optional[np.ndarray] = None   # uint8 (192, 256, 3) RGB
    camera_min_c: Optional[float] = None       # informational (default mode)
    camera_max_c: Optional[float] = None       # informational (default mode)
    reason: str = ""


def _codes_to_celsius(codes: np.ndarray) -> np.ndarray:
    """Convert LE u16 temperature codes to Celsius (float32)."""
    return codes.astype(np.float32) / HIK_TEMP_SCALE + HIK_TEMP_OFFSET


def _decode_yuyv_preview(raw: bytes, offset: int) -> Optional[np.ndarray]:
    """Decode 256x192 YUYV bytes at ``offset`` into an RGB uint8 image."""
    chunk = raw[offset:offset + _TEMP_PLANE_BYTES]
    if len(chunk) < _TEMP_PLANE_BYTES:
        return None
    yuyv = np.frombuffer(chunk, dtype=np.uint8).reshape(
        SENSOR_HEIGHT, SENSOR_WIDTH, 2
    )
    import cv2

    bgr = cv2.cvtColor(yuyv, cv2.COLOR_YUV2BGR_YUYV)
    return np.ascontiguousarray(bgr[:, :, ::-1])  # BGR -> RGB


def _pearson(a: np.ndarray, b: np.ndarray) -> float:
    """Pearson correlation of two float arrays (robust to constant input)."""
    a = a.astype(np.float64)
    b = b.astype(np.float64)
    if a.size < 8:
        return 0.0
    a = a - a.mean()
    b = b - b.mean()
    denom = np.sqrt((a * a).sum() * (b * b).sum())
    if denom <= 0.0:
        return 0.0
    return float((a * b).sum() / denom)


def _scan_camera_reported(raw: bytes) -> tuple[Optional[float], Optional[float]]:
    """Scan the default-mode summary rows for camera-reported temperatures.

    The exact field offsets are not published, so every LE u16 value in the
    2,048-byte window is decoded and only values that land in a plausible
    temperature band are kept. Coordinates (0..255), flags and small
    parameter fields all decode below -46 degC and are filtered out;
    temperature codes for -20..200 degC (4,480..16,000) survive.
    Returns informational values only.
    """
    window = raw[HIK_DEFAULT_SUMMARY_OFF:HIK_DEFAULT_SUMMARY_OFF + 2048]
    if len(window) < 2048:
        return None, None
    codes = np.frombuffer(window, dtype="<u2").astype(np.float32)
    celsius = codes / HIK_TEMP_SCALE + HIK_TEMP_OFFSET
    plausible = celsius[(celsius >= -20.0) & (celsius <= 200.0)]
    if plausible.size == 0:
        return None, None
    return float(plausible.min()), float(plausible.max())


class HikStreamClassifier:
    """Stateful classifier separating default-mode from calibrated frames.

    Default-mode metadata rows are mostly padding (zeros). In mode 8 that
    same 2,048-byte window lies inside the live temperature plane and is
    dense. A frame is only accepted as radiometric when the candidate
    temperature plane passes every statistical gate (value ranges,
    distribution spread, continuity of the metadata window with the
    temperature plane, preview correlation) for several consecutive frames.
    """

    def __init__(self) -> None:
        self._consecutive_m8 = 0
        self._radiometric = False

    @property
    def radiometric(self) -> bool:
        return self._radiometric

    def reset(self) -> None:
        self._consecutive_m8 = 0
        self._radiometric = False

    def classify(self, raw: bytes) -> HikParseResult:
        """Parse one raw frame; returns the layout decision and payloads."""
        if len(raw) not in _VALID_FRAME_SIZES:
            return HikParseResult(
                reason=f"frame size {len(raw)} is not a HikCamera layout"
            )

        meta = raw[HIK_DEFAULT_METADATA_OFF:HIK_DEFAULT_METADATA_OFF + 2048]
        zero_fraction = 1.0 - (np.frombuffer(meta, dtype=np.uint8) > 0).mean()

        if zero_fraction < _ZERO_FRACTION_MAX:
            result = self._try_mode8(raw)
            if result is not None:
                return result
            # Mode-8 validation failed: most likely a default-mode frame
            # with unusually dense metadata. Fall through to the default
            # layout, which is the safe interpretation.

        return self._parse_default(raw)

    # -- internal ---------------------------------------------------------

    def _try_mode8(self, raw: bytes) -> Optional[HikParseResult]:
        """Validate the frame as calibrated mode 8. None when rejected."""
        codes = np.frombuffer(
            raw[HIK_M8_TEMPS_OFF:HIK_M8_TEMPS_OFF + _TEMP_PLANE_BYTES],
            dtype="<u2",
        )
        if codes.size != _PIXELS:
            return None

        reason = self._validate_codes(codes)
        if reason is None:
            continuity = self._meta_continuity(raw)
            if continuity < _META_CONTINUITY_MIN:
                reason = (
                    f"metadata window not continuous with the temperature "
                    f"plane (r={continuity:.2f})"
                )
        if reason is None:
            preview = _decode_yuyv_preview(raw, HIK_M8_PREVIEW_OFF)
            if preview is None:
                reason = "truncated mode-8 preview"
            else:
                corr = self._preview_correlation(codes, raw, HIK_M8_PREVIEW_OFF)
                if corr < _CORRELATION_MIN:
                    reason = f"preview/temperature correlation {corr:.2f} too low"

        if reason is not None:
            self._consecutive_m8 = 0
            self._radiometric = False
            return None

        self._consecutive_m8 += 1
        if self._consecutive_m8 < HIK_MIN_CONSECUTIVE_M8:
            # Valid so far but not yet confirmed - stay visual-only.
            return HikParseResult(
                layout="calibrated",
                radiometric=False,
                preview_rgb=_decode_yuyv_preview(raw, HIK_M8_PREVIEW_OFF),
                reason="calibrated frame awaiting confirmation",
            )

        self._radiometric = True
        return HikParseResult(
            layout="calibrated",
            radiometric=True,
            temp_matrix=_codes_to_celsius(codes).reshape(SENSOR_HEIGHT, SENSOR_WIDTH),
            preview_rgb=_decode_yuyv_preview(raw, HIK_M8_PREVIEW_OFF),
            reason="calibrated radiometric frame",
        )

    def _validate_codes(self, codes: np.ndarray) -> Optional[str]:
        """Return a rejection reason, or None when the plane is plausible."""
        as_float = codes.astype(np.float64)
        if as_float.min() < _CODE_MIN or as_float.max() > _CODE_MAX:
            return "temperature codes outside -50..550 degC range"
        typical = ((as_float >= _CODE_TYPICAL_MIN)
                   & (as_float <= _CODE_TYPICAL_MAX)).mean()
        if typical < _TYPICAL_FRACTION:
            return f"only {typical:.0%} of codes in -30..250 degC band"
        std = as_float.std()
        if not (_STD_MIN <= std <= _STD_MAX):
            return f"temperature spread {std / HIK_TEMP_SCALE:.1f} degC implausible"
        return None

    def _meta_continuity(self, raw: bytes) -> float:
        """Correlate the metadata window with the adjacent temperature rows.

        In a genuine mode-8 frame the 2,048-byte "metadata" window is
        nothing but temperature rows 183-186 of the same continuous plane,
        so it correlates almost perfectly with the rows right above it.
        Default-mode metadata (parameters/padding) does not - which is
        what makes this a decisive safety gate against misreading raw
        sensor counts as temperatures.
        """
        plane = np.frombuffer(
            raw[HIK_M8_TEMPS_OFF:HIK_M8_TEMPS_OFF + _TEMP_PLANE_BYTES],
            dtype="<u2",
        )
        if plane.size != _PIXELS:
            return 0.0
        plane = plane.reshape(SENSOR_HEIGHT, SENSOR_WIDTH)
        meta_rows = np.frombuffer(
            raw[HIK_DEFAULT_METADATA_OFF:HIK_DEFAULT_METADATA_OFF + 2048],
            dtype="<u2",
        )[:1024].astype(np.float32)
        adjacent = plane[179:183].ravel().astype(np.float32)
        return _pearson(meta_rows, adjacent)

    def _preview_correlation(self, codes: np.ndarray, raw: bytes,
                             preview_off: int) -> float:
        """Correlate the temperature plane with the preview luminance."""
        temps = codes.reshape(SENSOR_HEIGHT, SENSOR_WIDTH).astype(np.float32)
        y_plane = np.frombuffer(
            raw[preview_off:preview_off + _TEMP_PLANE_BYTES],
            dtype=np.uint8,
        )[0::2].reshape(SENSOR_HEIGHT, SENSOR_WIDTH).astype(np.float32)
        # Downsample for speed; noise averages out.
        step_y, step_x = SENSOR_HEIGHT // 24, SENSOR_WIDTH // 32
        t = temps[::step_y, ::step_x].ravel()
        y = y_plane[::step_y, ::step_x].ravel()
        return _pearson(t, y)

    def _parse_default(self, raw: bytes) -> HikParseResult:
        """Default layout: preview + camera-reported summaries, no matrix."""
        preview = _decode_yuyv_preview(raw, HIK_DEFAULT_PREVIEW_OFF)
        if preview is None:
            return HikParseResult(reason="truncated default-mode frame")
        cam_min, cam_max = _scan_camera_reported(raw)
        self._consecutive_m8 = 0
        self._radiometric = False
        reason = "default stream (raw counts, no per-pixel temperatures)"
        if cam_min is not None:
            reason = (
                "default stream (raw counts, no per-pixel temperatures); "
                f"camera reports {cam_min:.1f}..{cam_max:.1f} degC"
            )
        return HikParseResult(
            layout="default",
            radiometric=False,
            preview_rgb=preview,
            camera_min_c=cam_min,
            camera_max_c=cam_max,
            reason=reason,
        )
