"""Abstract camera interface implemented by every backend.

Backends:
- SimulatedCamera     - synthetic data for development and automated tests
- UvcCamera           - best-effort UVC visual stream (NOT radiometric)
- HikmicroSdkCamera   - clean integration point for the official HIKMICRO SDK

A backend MUST set ``info`` and implement open/close/read_frame. A backend
that cannot provide a real temperature matrix must return frames with
``matrix=None``; it must never estimate temperatures from image colors.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from app.camera.camera_models import CameraInfo
from app.thermal.thermal_frame import ThermalFrame


class CameraError(Exception):
    """Camera communication / USB / SDK failure with a user-readable message."""


class CameraInterface(ABC):
    """Contract for camera backends."""

    info: CameraInfo

    @abstractmethod
    def open(self) -> None:
        """Open the device. Raises CameraError with a detailed message."""

    @abstractmethod
    def close(self) -> None:
        """Close the device and release resources. Never raises."""

    @abstractmethod
    def is_open(self) -> bool:
        """True when the device is currently open."""

    @abstractmethod
    def read_frame(self) -> ThermalFrame:
        """Read one frame. Raises CameraError on communication failure."""

    def set_emissivity(self, value: float) -> None:
        """Set emissivity when supported; otherwise a no-op is acceptable."""
