"""Data models describing discovered camera devices."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CameraInfo:
    """One entry in the device selection dialog."""

    key: str            # unique discovery key, e.g. 'simulated:0', 'uvc:1'
    name: str           # user-visible device name
    kind: str           # 'simulated' | 'uvc' | 'hikmicro-sdk'
    is_radiometric: bool
    is_simulated: bool
    details: str = ""   # extra status text shown in the dialog
