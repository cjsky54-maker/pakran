"""GUI smoke test: construct the full MainWindow offscreen with the
simulated camera and verify the wiring survives a short frame burst."""
import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from app.core.application import ApplicationServices


@pytest.fixture()
def services(tmp_path):
    svc = ApplicationServices(data_dir=str(tmp_path / "data"), attach_simulated=True)
    yield svc
    svc.shutdown(normal=False)


def test_main_window_smoke(services, qapp):
    from app.ui.main_window import MainWindow

    window = MainWindow(services)
    window.show()

    # Let some simulated frames flow through the worker thread.
    deadline_timer = QTimer.singleShot(600, QApplication.quit)
    QApplication.processEvents()
    import time
    end = time.monotonic() + 0.8
    while time.monotonic() < end:
        QApplication.processEvents()
        time.sleep(0.02)

    frame = services.camera.latest_frame()
    assert frame is not None
    assert frame.is_radiometric
    assert frame.is_simulated

    # Camera status propagated to the header.
    assert "Simulated" in window.status_label.text()

    # A preset can be created and selected.
    from app.thermal.region_calculator import RegionDefinition
    pid = services.db.create_preset("SMOKE-1", "1.00", [
        RegionDefinition("A", 20, 20, 50, 50, "#ff0000", 35.0),
    ])
    window.refresh_presets(select_id=pid)
    assert window._current_preset is not None
    assert window._current_preset.id == pid
    assert window.camera_view.table.rowCount() == 1

    # No test can run against a valid state without user interaction;
    # buttons reflect the connected simulated radiometric camera.
    assert window.camera_view.standard_btn.isEnabled()

    window.close()  # exercises closeEvent (normal shutdown path)


def test_create_new_preset_opens_editor_on_every_click(services, qapp, monkeypatch):
    """Regression: with no presets stored, 'Create New Preset...' is the only
    combo item and already current; a click on it must still open the editor
    (the combo reacts to `activated`, not `currentIndexChanged`)."""
    from app.ui.main_window import MainWindow
    from app.ui.preset_editor import PresetEditorDialog

    window = MainWindow(services)
    window.show()
    QApplication.processEvents()

    combo = window.preset_combo
    assert combo.count() == 1
    assert combo.currentText() == "Create New Preset..."
    assert combo.currentIndex() == 0  # already selected before any click

    calls = {"n": 0}

    def fake_exec(self):
        calls["n"] += 1
        return PresetEditorDialog.DialogCode.Rejected

    monkeypatch.setattr(PresetEditorDialog, "exec", fake_exec)

    # A real popup click emits `activated(index)` even when the index is
    # unchanged (0 -> 0); currentIndexChanged would stay silent.
    combo.activated.emit(combo.currentIndex())
    QApplication.processEvents()
    assert calls["n"] == 1

    # Clicking again after cancelling must reopen the editor.
    combo.activated.emit(combo.currentIndex())
    QApplication.processEvents()
    assert calls["n"] == 2

    # With an existing preset, selecting it loads it; selecting
    # "Create New Preset..." afterwards opens the editor again.
    from app.thermal.region_calculator import RegionDefinition
    pid = services.db.create_preset("REG-1", "1.00", [
        RegionDefinition("A", 20, 20, 50, 50, "#ff0000", 35.0),
    ])
    window.refresh_presets(select_id=pid)
    combo.activated.emit(combo.findData(pid))
    QApplication.processEvents()
    assert window._current_preset.id == pid

    combo.activated.emit(combo.findText("Create New Preset..."))
    QApplication.processEvents()
    assert calls["n"] == 3

    window.close()


def test_simulated_preset_workflow(services, qapp):
    """End-to-end: simulated camera + preset + Quick Test through the DB."""
    from app.testing.quick_test import run_quick_test
    from app.thermal.region_calculator import RegionDefinition

    pid = services.db.create_preset("SMOKE-2", "1.00", [
        RegionDefinition("A", 20, 20, 50, 50, "#ff0000", 35.0),
    ])
    preset = services.db.get_preset(pid)

    import time
    end = time.monotonic() + 1.0
    while services.camera.latest_frame() is None and time.monotonic() < end:
        QApplication.processEvents()
        time.sleep(0.02)
    assert services.camera.latest_frame() is not None

    result = run_quick_test(services.camera, preset, services.settings, services.db)
    assert result["test_id"] > 0
    assert result["overall_result"] in ("PASS", "FAIL")
    assert services.db.list_tests(test_type="quick")


# -- camera manager: async open --------------------------------------------------


def test_camera_manager_connects_async(qapp, tmp_path):
    """attach() returns immediately; the device is opened on the worker
    thread and the state settles to connected with frames."""
    import time

    from app.camera.camera_manager import CameraManager
    from app.camera.simulated_camera import SimulatedCamera
    from app.core.configuration import SettingsData
    from app.core.constants import CAM_CONNECTED, CAM_CONNECTING

    mgr = CameraManager(lambda: SettingsData())
    mgr.attach(SimulatedCamera())
    assert mgr.state == CAM_CONNECTING  # non-blocking: not connected yet

    end = time.monotonic() + 3.0
    while mgr.latest_frame() is None and time.monotonic() < end:
        qapp.processEvents()
        time.sleep(0.02)
    assert mgr.latest_frame() is not None
    assert mgr.state == CAM_CONNECTED
    assert mgr.is_radiometric
    mgr.detach()
    assert mgr.state == "disconnected"


def test_camera_manager_open_failure_reported_async(qapp):
    """A failing open never blocks the GUI thread: the error arrives via
    signal and the manager settles back to disconnected (this used to be
    a 30 s frozen window followed by a fallback to the simulated camera)."""
    import time

    from app.camera.camera_interface import CameraError, CameraInterface
    from app.camera.camera_manager import CameraManager
    from app.camera.camera_models import CameraInfo
    from app.core.configuration import SettingsData
    from app.core.constants import CAM_CONNECTING, CAM_DISCONNECTED

    class BadCamera(CameraInterface):
        def __init__(self) -> None:
            self.info = CameraInfo(
                key="bad:0", name="Bad Camera", kind="uvc",
                is_radiometric=False, is_simulated=False,
            )

        def open(self) -> None:
            raise CameraError("simulated open failure - device busy")

        def close(self) -> None:
            pass

        def is_open(self) -> bool:
            return False

        def read_frame(self):
            raise CameraError("never opened")

        def set_emissivity(self, value: float) -> None:
            pass

    mgr = CameraManager(lambda: SettingsData())
    errors: list[str] = []
    mgr.errorOccurred.connect(errors.append)
    mgr.attach(BadCamera())
    assert mgr.state == CAM_CONNECTING

    end = time.monotonic() + 3.0
    while mgr.state != CAM_DISCONNECTED and time.monotonic() < end:
        qapp.processEvents()
        time.sleep(0.02)
    assert mgr.state == CAM_DISCONNECTED
    assert errors and "open failure" in errors[0]
    assert "open failure" in mgr.last_error
    mgr.detach()


def test_worker_reopens_camera_after_read_error(qapp):
    """The reconnection loop actually reopens the device instead of
    spinning on a closed camera until the deadline."""
    import time

    from app.camera.camera_interface import CameraError, CameraInterface
    from app.camera.camera_manager import CameraManager
    from app.camera.camera_models import CameraInfo
    from app.core.configuration import SettingsData
    from app.thermal.thermal_frame import ThermalFrame
    from app.core.jalali import utc_now_iso

    class FlakyCamera(CameraInterface):
        """Fails the first read, then recovers after a reopen."""

        def __init__(self) -> None:
            self.info = CameraInfo(
                key="flaky:0", name="Flaky Camera", kind="uvc",
                is_radiometric=False, is_simulated=False,
            )
            self._open_count = 0
            self._reads = 0

        def open(self) -> None:
            self._open_count += 1

        def close(self) -> None:
            pass

        def is_open(self) -> bool:
            return self._open_count > 0

        def read_frame(self):
            self._reads += 1
            if self._open_count == 1 and self._reads == 1:
                raise CameraError("transient stream stall")
            return ThermalFrame(
                matrix=None, image=None, timestamp=time.monotonic(),
                wall_time=utc_now_iso(), source="flaky", is_simulated=False,
            )

        def set_emissivity(self, value: float) -> None:
            pass

    camera = FlakyCamera()
    mgr = CameraManager(lambda: SettingsData())
    mgr.attach(camera)
    end = time.monotonic() + 5.0
    while ((camera._open_count < 2 or mgr.latest_frame() is None)
           and time.monotonic() < end):
        qapp.processEvents()
        time.sleep(0.02)
    assert camera._open_count >= 2      # reopened during reconnection
    assert mgr.latest_frame() is not None
    mgr.detach()


def test_device_dialog_deep_refresh_identifies_hikcamera(qapp):
    """The dialog shows the fast list immediately, then the background
    deep pass renames HIKMICRO devices and updates the table in place."""
    import time

    from app.camera.camera_models import CameraInfo
    from app.ui.dialogs.device_selection import DeviceSelectionDialog

    shallow = [
        CameraInfo("simulated:0", "Simulated Thermal Camera", "simulated",
                   True, True, "synthetic data"),
        CameraInfo("uvc:0", "UVC Camera 0", "uvc", False, False,
                   "USB video device - capability is checked when connected"),
    ]
    deep = [
        shallow[0],
        CameraInfo(
            "uvc:0", "HIKMICRO Thermal Camera (USB)", "uvc",
            False, False,
            "HikCamera USB stream - preview stream (no temperatures) - open "
            "HIKMICRO Analyzer once, close it, then reconnect to enable the "
            "calibrated stream",
        ),
    ]

    dialog = DeviceSelectionDialog(shallow, refresh_fn=lambda should_stop: deep)
    QApplication.processEvents()
    assert dialog.table.rowCount() == 2
    assert dialog.table.item(0, 2).text() == "Yes"   # simulated is radiometric
    # (the background deep pass may already have completed here on a fast
    # machine, so do not assert the generic "UVC Camera 0" label)

    # Deep pass result (may complete immediately or via the queued signal).
    end = time.monotonic() + 3.0
    while dialog.table.item(1, 0).text() != "HIKMICRO Thermal Camera (USB)" \
            and time.monotonic() < end:
        QApplication.processEvents()
        time.sleep(0.02)
    assert dialog.table.item(1, 0).text() == "HIKMICRO Thermal Camera (USB)"
    assert dialog.table.item(1, 1).text() == "HikCamera USB (UVC)"
    assert dialog.table.item(1, 2).text() == "No"    # honest until calibrated
    assert "Analyzer" in dialog.table.item(1, 3).text()
    assert dialog.refresh_btn.isEnabled()            # re-enabled after the pass

    dialog.reject()


# -- v1.0.3: connect-while-probing crash regression tests -----------------------


def test_device_dialog_cancels_probe_on_connect(qapp):
    """Clicking Connect/Cancel/close must cancel the background deep probe
    so the acquisition open() that follows never races a native probe."""
    from app.camera.camera_models import CameraInfo
    from app.ui.dialogs.device_selection import DeviceSelectionDialog

    devices = [
        CameraInfo("simulated:0", "Simulated Thermal Camera", "simulated",
                   True, True, "synthetic data"),
    ]
    seen = {}

    def refresh_fn(should_stop):
        seen["should_stop"] = should_stop
        return devices

    dialog = DeviceSelectionDialog(devices, refresh_fn=refresh_fn)
    assert not dialog._probe_cancel.is_set()
    dialog.accept()
    assert dialog._probe_cancel.is_set()          # probe told to stop
    assert callable(seen["should_stop"])          # probe could observe it
    assert seen["should_stop"]()


def test_device_dialog_lists_usb_cameras_before_deep_probe(qapp):
    """'Opens instantly' must include the camera LIST, not just the window:
    the shallow background enumeration shows the USB rows within a couple
    of seconds, then the deep pass refines them (15-30 s)."""
    import threading
    import time

    from app.camera.camera_models import CameraInfo
    from app.ui.dialogs.device_selection import DeviceSelectionDialog

    shallow = [CameraInfo("uvc:0", "UVC Camera 0", "uvc", False, False,
                          "USB video device - capability is checked when connected")]
    deep = [CameraInfo("uvc:0", "HIKMICRO Thermal Camera (USB)", "uvc",
                       False, False,
                       "HikCamera USB stream - preview stream (no temperatures)")]
    # The deep stage blocks until released so the intermediate (shallow)
    # state is observable deterministically.
    deep_may_finish = threading.Event()

    def deep_stage(should_stop):
        deep_may_finish.wait(3.0)
        return deep

    def first_cell() -> str:
        item = dialog.table.item(0, 0)
        return item.text() if item else ""

    dialog = DeviceSelectionDialog(
        [], refresh_fn=deep_stage,
        shallow_fn=lambda should_stop: shallow,
    )
    # Stage 1: the shallow list replaces the placeholder quickly...
    end = time.monotonic() + 3.0
    while first_cell() != "UVC Camera 0" and time.monotonic() < end:
        QApplication.processEvents()
        time.sleep(0.02)
    assert first_cell() == "UVC Camera 0"
    assert not dialog.refresh_btn.isEnabled()     # deep probe still running
    # Stage 2: the deep pass renames the row and re-enables Refresh.
    deep_may_finish.set()
    end = time.monotonic() + 3.0
    while first_cell() != "HIKMICRO Thermal Camera (USB)" \
            and time.monotonic() < end:
        QApplication.processEvents()
        time.sleep(0.02)
    assert first_cell() == "HIKMICRO Thermal Camera (USB)"
    assert dialog.table.item(0, 1).text() == "HikCamera USB (UVC)"
    assert dialog.refresh_btn.isEnabled()

    dialog.reject()


def test_device_dialog_empty_search_does_not_search_forever(qapp):
    """When the discovery pass finishes with no cameras, the placeholder
    row must say so - not 'Searching for USB cameras...' forever."""
    import time

    from app.ui.dialogs.device_selection import DeviceSelectionDialog

    dialog = DeviceSelectionDialog([], refresh_fn=lambda should_stop: [])
    end = time.monotonic() + 3.0
    while dialog.table.item(0, 3).text() \
            == "Searching for USB cameras..." and time.monotonic() < end:
        QApplication.processEvents()
        time.sleep(0.02)
    assert dialog.table.item(0, 0).text() == "(no camera)"
    assert "No cameras found" in dialog.table.item(0, 3).text()
    # The placeholder is not selectable -> Connect stays disabled, but a
    # retry (Refresh) is possible.
    assert not dialog.connect_btn.isEnabled()
    assert dialog.refresh_btn.isEnabled()

    dialog.reject()


def test_device_dialog_shows_error_when_discovery_fails(qapp):
    """A crashing discovery pass reports an error instead of pretending
    to search forever."""
    import time

    from app.ui.dialogs.device_selection import DeviceSelectionDialog

    def broken_refresh(should_stop):
        raise RuntimeError("probe exploded")

    dialog = DeviceSelectionDialog([], refresh_fn=broken_refresh)
    end = time.monotonic() + 3.0
    while dialog.table.item(0, 3).text() \
            == "Searching for USB cameras..." and time.monotonic() < end:
        QApplication.processEvents()
        time.sleep(0.02)
    assert dialog.table.item(0, 0).text() == "(error)"
    assert "Discovery failed" in dialog.table.item(0, 3).text()
    assert dialog.refresh_btn.isEnabled()          # retry is offered

    dialog.reject()


def test_discover_skips_and_keeps_active_camera(qapp, monkeypatch):
    """A dialog refresh while a camera streams: the active device is not
    re-opened (its key is passed as skip_keys) and stays listed even when
    a fresh open reports it busy/absent."""
    from app.camera import camera_manager as cm
    from app.camera.camera_models import CameraInfo
    from app.core.configuration import SettingsData

    mgr = cm.CameraManager(lambda: SettingsData())
    active = CameraInfo(
        "uvc:0", "HIKMICRO Thermal Camera (USB)", "uvc",
        True, False, "Calibrated thermal stream - measurements enabled",
    )
    mgr._camera = _StaticCameraStub(active)       # pretend it is attached

    calls = {}

    def fake_discover_uvc(**kwargs):
        calls.update(kwargs)
        return []                                 # shallow open "fails"

    monkeypatch.setattr(cm, "discover_uvc_cameras", fake_discover_uvc)

    devices = mgr.discover(deep=True)
    assert calls["skip_keys"] == ("uvc:0",)       # active device not probed
    keys = [d.key for d in devices]
    assert "uvc:0" in keys                        # but still listed
    uvc = next(d for d in devices if d.key == "uvc:0")
    assert uvc.name == "HIKMICRO Thermal Camera (USB)"
    assert uvc.is_radiometric

    fast = mgr.discover_fast()
    assert "uvc:0" in [d.key for d in fast]       # instant list includes it


def test_camera_manager_busy_while_worker_runs(qapp):
    """Auto-reconnect must not run discovery while a worker is alive or
    winding down (it may hold a per-device lock inside a bounded open)."""
    import time

    from app.core.configuration import SettingsData
    from app.camera.camera_manager import CameraManager
    from app.camera.camera_models import CameraInfo

    class _BlockingCamera(_StaticCameraStub):
        def __init__(self) -> None:
            super().__init__(CameraInfo(
                "uvc:0", "UVC Camera 0", "uvc", False, False, "slow open",
            ))
            self.released = False

        def open(self) -> None:
            import threading
            # Simulate a slow bounded native open: longer than the
            # manager's 2 s join, so the worker goes to "retiring".
            self._open_event = threading.Event()
            self._open_event.wait(timeout=3.0)

        def close(self) -> None:
            self.released = True

    camera = _BlockingCamera()
    mgr = CameraManager(lambda: SettingsData())
    assert not mgr.busy
    mgr.attach(camera)
    assert mgr.busy                              # worker exists -> busy
    mgr.detach()
    deadline = time.monotonic() + 8.0
    while mgr.busy and time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(0.02)
    assert not mgr.busy                          # retired worker reaped
    assert camera.released


class _StaticCameraStub:
    """Minimal CameraInterface stand-in holding a fixed info."""

    def __init__(self, info) -> None:
        self.info = info

    def open(self) -> None:
        return

    def close(self) -> None:
        return

    def is_open(self) -> bool:
        return True

    def read_frame(self):
        from app.camera.camera_interface import CameraError

        raise CameraError("not used")

    def set_emissivity(self, value: float) -> None:
        return
