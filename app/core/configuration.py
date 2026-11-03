"""Application settings: dataclass, JSON persistence, atomic writes."""
from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import asdict, dataclass, fields
from typing import Any

from app.core.constants import DEFAULT_SAMPLE_INTERVAL

logger = logging.getLogger("DriverThermalQC.config")

_BOOL_TRUE = ("1", "true", "yes", "on")

_FLOAT_FIELDS = (
    "temperature_rise_threshold", "waiting_timeout",
    "camera_reconnection_timeout", "emissivity",
)
_INT_FIELDS = ("sample_interval", "window_width", "window_height")
_BOOL_FIELDS = ("show_simulated_camera",)


@dataclass
class SettingsData:
    """User-configurable settings with engineering defaults."""

    temperature_rise_threshold: float = 5.0   # deg C above baseline region max
    waiting_timeout: float = 60.0             # seconds (waiting phase only)
    sample_interval: int = DEFAULT_SAMPLE_INTERVAL  # 5 / 10 / 15 seconds
    camera_reconnection_timeout: float = 10.0  # seconds
    emissivity: float = 0.95                  # only user-adjustable camera param in v1
    show_simulated_camera: bool = True        # list the simulated camera in device dialog
    window_width: int = 1280
    window_height: int = 820

    # ------------------------------------------------------------------
    def snapshot_dict(self) -> dict[str, Any]:
        """Flat dict stored beside each test as the settings snapshot."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "SettingsData":
        defaults = cls()
        if not isinstance(data, dict):
            return defaults
        kwargs: dict[str, Any] = {}
        known = {f.name for f in fields(cls)}
        for name, raw in data.items():
            if name not in known:
                continue
            try:
                if name in _FLOAT_FIELDS:
                    kwargs[name] = float(raw)
                elif name in _INT_FIELDS:
                    kwargs[name] = int(raw)
                elif name in _BOOL_FIELDS:
                    kwargs[name] = (
                        str(raw).strip().lower() in _BOOL_TRUE
                        if isinstance(raw, str) else bool(raw)
                    )
                else:
                    kwargs[name] = raw
            except (TypeError, ValueError):
                logger.warning("Invalid settings value for %s: %r (using default)", name, raw)
        return cls(**kwargs)


def settings_file_path(data_dir: str) -> str:
    return os.path.join(data_dir, "settings", "settings.json")


def load_settings(data_dir: str) -> SettingsData:
    path = settings_file_path(data_dir)
    if not os.path.isfile(path):
        logger.info("No settings file found; using defaults (%s)", path)
        return SettingsData()
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return SettingsData.from_dict(data)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Could not load settings (%s): %s; using defaults", path, exc)
        return SettingsData()


def save_settings(data_dir: str, settings: SettingsData) -> None:
    """Atomically persist settings (temp file + os.replace)."""
    path = settings_file_path(data_dir)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(asdict(settings), fh, indent=2)
        os.replace(tmp_path, path)
        logger.info("Settings saved to %s", path)
    except OSError:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
