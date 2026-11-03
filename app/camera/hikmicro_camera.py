"""HIKMICRO OEM SDK adapter - the clean Phase 2 integration point.

Status (see docs/FEASIBILITY.md and docs/CAMERA_INTEGRATION.md):
- HIKMICRO does not publish a public SDK for the Mini2 V2. OEM SDK access
  is granted by the manufacturer on request (techsupport).
- Until verified SDK bindings are installed, this adapter refuses to
  connect with a clear message. It NEVER fabricates measurements and
  never reverse-engineers HIKMICRO Analyzer.

How to complete this adapter once the official SDK is available:
1. Install the SDK per the manufacturer's license terms.
2. Set HIKMICRO_SDK_PATH / install the Python bindings so that
   ``sdk_available()`` returns True.
3. Implement open/close/read_frame below mapping the SDK's radiometric
   stream into ThermalFrame(matrix=..., image=...). Expected resolution
   for the Mini2 V2 class sensor is 256x192 - verify against hardware.
4. Discovery should enumerate SDK-visible USB devices.

The rest of the application (GUI, database, test workflow, reports)
already works unchanged on top of this interface.
"""
from __future__ import annotations

import importlib.util
import os

from app.camera.camera_interface import CameraError, CameraInterface
from app.camera.camera_models import CameraInfo
from app.thermal.thermal_frame import ThermalFrame

SDK_MODULE_CANDIDATES = ("hikmicro_thermal_sdk", "hikmicro_sdk")

_ADAPTER_DOC = "docs/CAMERA_INTEGRATION.md"


def sdk_available() -> bool:
    """True when official HIKMICRO SDK Python bindings are importable."""
    candidates = list(SDK_MODULE_CANDIDATES)
    extra = os.environ.get("HIKMICRO_SDK_MODULE")
    if extra:
        candidates.insert(0, extra)
    for module_name in candidates:
        try:
            if importlib.util.find_spec(module_name) is not None:
                return True
        except (ImportError, ValueError):
            continue
    return False


class HikmicroSdkCamera(CameraInterface):
    """Adapter for the official HIKMICRO SDK (pending SDK availability)."""

    def __init__(self, device_index: int = 0) -> None:
        self.device_index = int(device_index)
        self._sdk = None
        self.info = CameraInfo(
            key=f"hikmicro-sdk:{self.device_index}",
            name=f"HIKMICRO SDK Device {self.device_index}",
            kind="hikmicro-sdk",
            is_radiometric=True,
            is_simulated=False,
            details="Official OEM SDK adapter (integration point)",
        )

    # -- CameraInterface ---------------------------------------------------
    def open(self) -> None:
        if not sdk_available():
            raise CameraError(
                "HIKMICRO SDK bindings are not installed on this PC.\n\n"
                "Radiometric integration requires the official HIKMICRO SDK, "
                "which is provided by the manufacturer on request "
                "(techsupport@hikmicrotech.com).\n"
                f"See {_ADAPTER_DOC} for the integration steps."
            )
        # SDK present: load it and keep the reference for the adapter work.
        for module_name in SDK_MODULE_CANDIDATES:
            try:
                self._sdk = importlib.import_module(module_name)
                break
            except ImportError:
                continue
        raise CameraError(
            "HIKMICRO SDK detected, but the radiometric adapter has not been "
            f"completed and verified against real Mini2 V2 hardware yet. "
            f"See {_ADAPTER_DOC} - the integration point is ready."
        )

    def close(self) -> None:
        self._sdk = None

    def is_open(self) -> bool:
        return False

    def read_frame(self) -> ThermalFrame:
        raise CameraError("HIKMICRO SDK adapter is not implemented yet")

    def set_emissivity(self, value: float) -> None:
        raise CameraError("HIKMICRO SDK adapter is not implemented yet")


def discover_hikmicro_cameras() -> list[CameraInfo]:
    """List SDK devices when bindings are present (otherwise empty)."""
    if not sdk_available():
        return []
    return [
        CameraInfo(
            key="hikmicro-sdk:0",
            name="HIKMICRO SDK Device 0",
            kind="hikmicro-sdk",
            is_radiometric=True,
            is_simulated=False,
            details="SDK detected - adapter pending (see docs)",
        )
    ]
