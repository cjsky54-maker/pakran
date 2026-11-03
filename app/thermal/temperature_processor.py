"""Temperature matrix validation and Ambient calculation.

Ambient is defined by the application as the minimum temperature of the
entire radiometric matrix of the frame. No separate Ambient ROI exists.
"""
from __future__ import annotations

import numpy as np


def validate_matrix(matrix: np.ndarray) -> np.ndarray:
    """Validate and normalize a temperature matrix to float32 2-D."""
    if matrix is None:
        raise ValueError("Temperature matrix is missing")
    arr = np.asarray(matrix, dtype=np.float32)
    if arr.ndim != 2 or arr.size == 0:
        raise ValueError(f"Invalid temperature matrix shape: {arr.shape}")
    if not np.isfinite(arr).all():
        raise ValueError("Temperature matrix contains non-finite values")
    return arr


def compute_ambient(matrix: np.ndarray) -> float:
    """Ambient temperature = minimum of the entire matrix (full precision)."""
    arr = validate_matrix(matrix)
    return float(arr.min())
