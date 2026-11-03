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
