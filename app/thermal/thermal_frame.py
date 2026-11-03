"""ThermalFrame: one acquisition from a camera backend.

Two independent streams may be present:
- ``matrix``: the real radiometric temperature matrix in Celsius
  (float32, height x width). ``None`` when the backend cannot provide
  radiometric values (e.g. a plain UVC visual stream).
- ``image``: an RGB uint8 image suitable for display.

Display imagery is NEVER treated as a temperature source.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np


@dataclass
class ThermalFrame:
    matrix: Optional[np.ndarray] = None   # float32 (H, W), degrees Celsius
    image: Optional[np.ndarray] = None    # uint8 (H, W, 3) RGB
    timestamp: float = 0.0                # time.monotonic() at capture
    wall_time: str = ""                   # UTC ISO-8601
    source: str = "unknown"               # simulated / uvc / hikmicro-sdk
    is_simulated: bool = False

    @property
    def is_radiometric(self) -> bool:
        """True only when a genuine temperature matrix is available."""
        return self.matrix is not None
