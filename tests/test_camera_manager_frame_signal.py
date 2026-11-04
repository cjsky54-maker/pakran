"""Regression test: CameraManager must publish frames to UI subscribers."""

import numpy as np

from app.camera.camera_manager import CameraManager
from app.thermal.thermal_frame import ThermalFrame


def test_on_frame_emits_frame_ready_signal():
    manager = CameraManager(lambda: None)
    received = []
    manager.frameReady.connect(received.append)
    frame = ThermalFrame(
        matrix=None,
        image=np.zeros((4, 4, 3), dtype=np.uint8),
        timestamp=1.0,
        wall_time="2026-11-04T00:00:00Z",
        source="uvc",
    )

    manager._on_frame(frame)

    assert manager.latest_frame() is frame
    assert received == [frame]
