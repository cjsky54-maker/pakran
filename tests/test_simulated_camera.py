"""Simulated camera backend behaviour (clearly synthetic data)."""
import numpy as np

from app.camera.camera_interface import CameraError
from app.camera.simulated_camera import DEFAULT_HOTSPOTS, SimulatedCamera


def test_read_requires_open():
    cam = SimulatedCamera()
    try:
        cam.read_frame()
        assert False, "expected CameraError"
    except CameraError:
        pass


def test_frame_is_radiometric_and_marked_simulated():
    cam = SimulatedCamera()
    cam.open()
    frame = cam.read_frame()
    assert frame.is_radiometric
    assert frame.is_simulated
    assert frame.source == "simulated"
    assert frame.matrix.shape == (192, 256)
    assert frame.wall_time  # UTC ISO populated


def test_matrix_values_are_plausible():
    cam = SimulatedCamera(seed=42)
    cam.open()
    frame = cam.read_frame()
    ambient = float(frame.matrix.min())
    hotspot = DEFAULT_HOTSPOTS[0]
    center_value = float(frame.matrix[hotspot.cy, hotspot.cx])
    assert 20.0 < ambient < 30.0
    assert center_value > ambient + 2.0  # hotspot is clearly hotter
    # Initial rise stays below the default 5 degC trigger right after open.
    assert center_value < ambient + 5.0


def test_hotspots_heat_up_over_time():
    cam = SimulatedCamera(seed=7)
    cam.open()
    # Fast-forward the internal clock.
    cam._open_since -= 120.0
    frame = cam.read_frame()
    hotspot = DEFAULT_HOTSPOTS[0]
    center_value = float(frame.matrix[hotspot.cy, hotspot.cx])
    ambient = float(frame.matrix.min())
    assert center_value > ambient + 30.0  # approached target rise


def test_emissivity_is_accepted():
    cam = SimulatedCamera()
    cam.open()
    cam.set_emissivity(0.90)
    cam.read_frame()  # still works


def test_display_image_is_rendered():
    cam = SimulatedCamera(seed=1)
    cam.open()
    frame = cam.read_frame()
    assert frame.image is not None
    assert frame.image.dtype == np.uint8
    assert frame.image.shape == (192, 256, 3)


def test_device_name_declares_simulation():
    cam = SimulatedCamera()
    assert "Simulated" in cam.info.name
    assert "Synthetic" in cam.info.name
    assert cam.info.is_simulated
