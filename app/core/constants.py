"""Application-wide constants and filesystem layout.

All persistent data lives in a stable writable directory:
- Windows: %LOCALAPPDATA%\\DriverThermalQC\\
- Other OS (development): ~/.local/share/DriverThermalQC/
"""
from __future__ import annotations

import os
import sys

APP_NAME = "DriverThermalQC"
APP_TITLE = "Driver Thermal QC"
APP_VERSION = "1.0.3"
ORG_NAME = "Pakran"

# Sensor / display geometry (HIKMICRO Mini2 V2 class sensor, 256x192)
SENSOR_WIDTH = 256
SENSOR_HEIGHT = 192
DISPLAY_SCALE = 2
DISPLAY_WIDTH = SENSOR_WIDTH * DISPLAY_SCALE   # 512
DISPLAY_HEIGHT = SENSOR_HEIGHT * DISPLAY_SCALE  # 384

ACQ_FPS_SIMULATED = 10.0
# HIKMICRO pocket-class cameras (VID 2BDF "HikCamera", 256x192 core)
# stream YUYV 256x392 at their full native rate of 25 Hz.
ACQ_FPS_UVC = 25.0

# --- HIK "HikCamera" UVC frame layout (256x192 core, see docs/CAMERA_INTEGRATION.md)
# Default stream mode: 200,704 bytes total
HIK_DEFAULT_FRAME_BYTES = 200_704
HIK_DEFAULT_COUNTS_OFF = 0            # 256x192 LE u16 RAW sensor counts (not temps)
HIK_DEFAULT_METADATA_OFF = 98_304    # 2,048 B parameters/metadata/padding (~2.5 Hz)
HIK_DEFAULT_PREVIEW_OFF = 100_352    # 256x192 YUYV preview
HIK_DEFAULT_SUMMARY_OFF = 198_656    # 2,048 B camera-computed temperature summaries
# Calibrated "mode 8" stream: 201,248 bytes total (OS stacks may truncate to
# the advertised 200,704 - the temperature plane ends well before that)
HIK_M8_FRAME_BYTES = 201_248
HIK_M8_HEADER_BYTES = 4_640          # proprietary header
HIK_M8_TEMPS_OFF = 4_640             # 256x192 LE u16 temperature codes
HIK_M8_PREVIEW_OFF = 102_944         # 256x192 YUYV preview
# Temperature conversion shared by the calibrated plane and the default-mode
# summaries (confirmed against the manufacturer application):
#     celsius = code / 64.0 - 50.0
HIK_TEMP_SCALE = 64.0
HIK_TEMP_OFFSET = -50.0
# Safety gates for accepting a frame as radiometric (see hik_frame_parser)
HIK_MIN_CONSECUTIVE_M8 = 2

# Test types
TEST_TYPE_STANDARD = "standard"
TEST_TYPE_QUICK = "quick"

# Overall results and statuses
RESULT_PASS = "PASS"
RESULT_FAIL = "FAIL"
STATUS_TIMEOUT = "TIMEOUT"
STATUS_INCOMPLETE = "INCOMPLETE"

# Camera connection states
CAM_CONNECTED = "connected"
CAM_CONNECTING = "connecting"
CAM_RECONNECTING = "reconnecting"
CAM_DISCONNECTED = "disconnected"

# Standard test phases
PHASE_IDLE = "idle"
PHASE_WAITING = "waiting_rise"
PHASE_MEASURING = "measuring"
PHASE_FINISHED = "finished"

# Standard test timing
STANDARD_TEST_DURATION_S = 60.0
SAMPLE_INTERVAL_CHOICES = (5, 10, 15)
DEFAULT_SAMPLE_INTERVAL = 10
TICK_INTERVAL_MS = 100

# Failure reasons (stored in tests.failure_reason)
REASON_TIMEOUT = "Temperature Rise Threshold Not Reached"
REASON_EMERGENCY = "Manual Emergency Stop"
REASON_APP_CLOSED = "Application Closed During Test"
REASON_CAMERA_LOST = "Camera Connection Lost"
REASON_T60_MISSING = "T60 Sample Missing"
REASON_CRASH = "Unexpected Application Termination"
REASON_DB_WRITE = "Database Write Failure"

# Unique fixed colors assigned to regions (creation order)
REGION_COLORS = (
    "#E53935", "#1E88E5", "#43A047", "#FB8C00", "#8E24AA", "#00ACC1",
    "#F4511E", "#3949AB", "#7CB342", "#D81B60", "#00897B", "#FDD835",
)

DEFAULT_MAX_ALLOWED_DELTA_T = 35.0

# Data subdirectories created under the application data directory
DATA_SUBDIRS = (
    "database",
    os.path.join("database", "backups"),
    "settings",
    "logs",
    "exports",
)


def default_data_dir() -> str:
    """Return the stable writable application data directory."""
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        return os.path.join(base, APP_NAME)
    base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return os.path.join(base, APP_NAME)


def ensure_data_dirs(data_dir: str) -> None:
    """Create the full data directory layout."""
    for sub in DATA_SUBDIRS:
        os.makedirs(os.path.join(data_dir, sub), exist_ok=True)
