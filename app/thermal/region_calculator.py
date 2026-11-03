"""Region definitions, measurement and PASS/FAIL evaluation.

Measurement rules (v1):
- Each Region is an axis-aligned rectangular ROI on the sensor matrix.
- Region Min / Max use every pixel inside the ROI. No outlier rejection.
- DeltaT = Region_Max - Ambient (frame minimum).
- Region fails only when DeltaT is strictly greater than its configured
  Maximum Allowed DeltaT (equality with the limit is a PASS).
- Each Region is evaluated independently.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np

from app.core.constants import RESULT_FAIL, RESULT_PASS, REGION_COLORS


def label_for_index(index: int) -> str:
    """0->A, 1->B ... 25->Z, 26->AA, 27->AB ..."""
    if index < 0:
        raise ValueError("index must be >= 0")
    letters = ""
    index += 1
    while index > 0:
        index, rem = divmod(index - 1, 26)
        letters = chr(ord("A") + rem) + letters
    return letters


def pick_color(index: int) -> str:
    """Fixed unique overlay color for the n-th region."""
    return REGION_COLORS[index % len(REGION_COLORS)]


@dataclass
class RegionDefinition:
    """An immutable rectangular measurement region inside a Preset."""

    label: str
    x: int
    y: int
    width: int
    height: int
    color: str
    max_allowed_delta_t: float

    def to_dict(self) -> dict:
        return {
            "label": self.label,
            "x": self.x,
            "y": self.y,
            "width": self.width,
            "height": self.height,
            "color": self.color,
            "max_allowed_delta_t": self.max_allowed_delta_t,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "RegionDefinition":
        return cls(
            label=str(data["label"]),
            x=int(data["x"]),
            y=int(data["y"]),
            width=int(data["width"]),
            height=int(data["height"]),
            color=str(data["color"]),
            max_allowed_delta_t=float(data["max_allowed_delta_t"]),
        )

    def clipped(self, bounds_w: int, bounds_h: int) -> tuple[int, int, int, int]:
        """Clip the rectangle to the matrix bounds; width/height may become 0."""
        x0 = max(0, min(self.x, bounds_w))
        y0 = max(0, min(self.y, bounds_h))
        x1 = max(0, min(self.x + self.width, bounds_w))
        y1 = max(0, min(self.y + self.height, bounds_h))
        return x0, y0, x1 - x0, y1 - y0


def measure_region(matrix: np.ndarray, region: RegionDefinition) -> Optional[tuple[float, float]]:
    """Return (min, max) temperatures inside the region, or None when empty."""
    if matrix is None:
        return None
    h, w = matrix.shape[:2]
    x, y, rw, rh = region.clipped(w, h)
    if rw <= 0 or rh <= 0:
        return None
    sub = matrix[y:y + rh, x:x + rw]
    return float(sub.min()), float(sub.max())


def evaluate_region(delta_t: float, max_allowed_delta_t: float) -> str:
    """PASS when DeltaT <= limit (equality passes), FAIL only above it."""
    return RESULT_FAIL if delta_t > max_allowed_delta_t else RESULT_PASS


def measure_regions(
    matrix: np.ndarray, regions: list[RegionDefinition]
) -> dict[str, Optional[tuple[float, float]]]:
    """Measure every region; labels map to (min, max) or None when empty."""
    return {region.label: measure_region(matrix, region) for region in regions}
