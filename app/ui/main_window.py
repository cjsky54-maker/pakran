"""Main window: header, navigation, camera wiring, test orchestration."""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from app.camera.camera_manager import CameraManager, create_camera
from app.core.constants import (
    APP_VERSION,
    CAM_CONNECTED,
    ORG_NAME,
    APP_TITLE,
    REASON_APP_CLOSED,
)
from app.database.models import Preset
from app.testing.quick_test import QuickTestError, run_quick_test
from app.testing.standard_test import StandardTestController, TestStartError
from app.ui.camera_view import CameraView
from app.ui.dialogs.confirmations import confirm_close_during_test, confirm_start_standard_test
from app.ui.dialogs.device_selection import DeviceSelectionDialog
from app.ui.preset_editor import PresetEditorDialog
from app.ui.reports_page import ReportsPage
from app.ui.settings_page import SettingsPage

_CREATE_NEW = "Create New Preset..."


class MainWindow(QMainWindow):
    def __init__(self, services) -> None:
        super().__init__()
        self._services = services
        self._presets: list[Preset] = []
        self._current_preset: Optional[Preset] = None
        self._standard_running = False

        self.setWindowTitle(f"{APP_TITLE} v{APP_VERSION} - {ORG_NAME}")
        self.resize(services.settings.window_width, services.settings.window_height)

        self.controller = StandardTestController(services.db, self)

        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_header())
        self.stack = QStackedWidget()
        self.camera_view = CameraView()
        self.reports_page = ReportsPage(services)
        self.settings_page = SettingsPage(services)
        self.settings_page.settingsSaved.connect(self._on_settings_saved)
        self.stack.addWidget(self.camera_view)
        self.stack.addWidget(self.reports_page)
        self.stack.addWidget(self.settings_page)
        root.addWidget(self.stack, 1)
        self.setCentralWidget(central)

        self._wire_controller()
        self._wire_camera()
        self.refresh_presets()
        self._device_dialog_open = False
        self._auto_connect()

        # Idle auto-reconnection: keep looking for a camera while disconnected.
        self._rediscover_timer = QTimer(self)
        self._rediscover_timer.setInterval(5000)
        self._rediscover_timer.timeout.connect(self._try_auto_reconnect)
        self._rediscover_timer.start()

    # -- header -------------------------------------------------------------
    def _build_header(self) -> QWidget:
        header = QFrame()
        header.setObjectName("Header")
        header.setStyleSheet(
            "#Header { background: #1f2733; }"
            "#Header QLabel { color: #ffffff; }"
        )
        layout = QHBoxLayout(header)
        layout.setContentsMargins(14, 8, 14, 8)

        title = QLabel(APP_TITLE)
        title.setStyleSheet("font-size:16px; font-weight:600; color:#ffffff;")
        layout.addWidget(title)

        self.preset_combo = QComboBox()
        self.preset_combo.setMinimumWidth(280)
        # activated (not currentIndexChanged): fires on every user pick,
        # even when the index stays the same. With no presets stored,
        # "Create New Preset..." is the only item and already current, so
        # a click on it must still open the editor.
        self.preset_combo.activated.connect(self._on_preset_selected)
        layout.addWidget(QLabel("Preset:"))
        layout.addWidget(self.preset_combo)

        self.status_dot = QLabel()
        self.status_dot.setFixedSize(14, 14)
        self.status_dot.setToolTip("Camera status")
        layout.addSpacing(12)
        layout.addWidget(self.status_dot)
        self.status_label = QLabel("Disconnected")
        self.status_label.setStyleSheet("color:#cfd6e1;")
        layout.addWidget(self.status_label)

        reconnect_btn = QPushButton("Reconnect Camera")
        reconnect_btn.clicked.connect(self._open_device_dialog)
        layout.addWidget(reconnect_btn)

        layout.addStretch(1)

        self.nav_live = QPushButton("Live View")
        self.nav_reports = QPushButton("Reports")
        self.nav_settings = QPushButton("Settings")
        for btn in (self.nav_live, self.nav_reports, self.nav_settings):
            btn.setCheckable(True)
            layout.addWidget(btn)
        self.nav_live.setChecked(True)
        self.nav_live.clicked.connect(lambda: self._goto(0))
        self.nav_reports.clicked.connect(lambda: (self.reports_page.refresh_presets(), self.reports_page.reload(), self._goto(1)))
        self.nav_settings.clicked.connect(lambda: (self.settings_page.reload_all(), self._goto(2)))
        return header

    def _goto(self, index: int) -> None:
        self.stack.setCurrentIndex(index)
        self.nav_live.setChecked(index == 0)
        self.nav_reports.setChecked(index == 1)
        self.nav_settings.setChecked(index == 2)

    # -- camera wiring ----------------------------------------------------------
    def _wire_camera(self) -> None:
        camera: CameraManager = self._services.camera
        camera.frameReady.connect(self.camera_view.on_frame)
        camera.statusChanged.connect(self._on_camera_status)
        camera.errorOccurred.connect(
            lambda msg: self.statusBar().showMessage(msg, 8000)
        )

    def _on_camera_status(self, state: str, info: dict) -> None:
        connected = state == CAM_CONNECTED
        color = "#2ecc71" if connected else "#e74c3c"
        self.status_dot.setStyleSheet(
            f"background:{color}; border-radius:7px; min-width:14px; min-height:14px;"
        )
        label = {
            "connected": "Connected", "connecting": "Connecting...",
            "reconnecting": "Reconnecting...", "disconnected": "Disconnected",
        }.get(state, state)
        if info.get("name"):
            label += f" - {info['name']}"
        self.status_label.setText(label)
        self.camera_view.set_camera_state(
            connected, bool(info.get("is_radiometric")), bool(info.get("is_simulated"))
        )
        if not connected:
            self.statusBar().showMessage(info.get("error") or "", 8000)

    def _auto_connect(self) -> None:
        devices = self._services.camera.discover()
        # Exactly one supported camera -> connect automatically.
        if len(devices) == 1:
            self._services.camera.attach(create_camera(devices[0]))
        elif len(devices) > 1:
            self._open_device_dialog()

    def _try_auto_reconnect(self) -> None:
        # Never race the open selection dialog: the deep discovery it runs
        # opens UVC devices, and auto-attaching behind a modal dialog is
        # confusing.
        if self._device_dialog_open:
            return
        # Never race a worker that is still (winding down from) opening
        # a device - it may hold the per-device lock for a few more
        # seconds inside a bounded native open.
        if self._services.camera.busy:
            return
        if self._services.camera.state != "disconnected":
            return
        devices = self._services.camera.discover()
        if len(devices) == 1:
            self._services.camera.attach(create_camera(devices[0]))

    def _open_device_dialog(self) -> None:
        self._device_dialog_open = True
        try:
            # Instant list WITHOUT any native camera call on the GUI
            # thread (simulated / SDK / the streaming camera). USB devices
            # arrive in two background stages: the shallow enumeration
            # lists them within a second or two, the deep HikCamera probe
            # then refines names and the Radiometric column (15-30 s).
            # Previously the shallow UVC enumeration ran here on the GUI
            # thread and could block behind a device lock held by a
            # winding-down worker.
            dialog = DeviceSelectionDialog(
                self._services.camera.discover_fast(), self,
                shallow_fn=lambda should_stop: self._services.camera.discover(
                    deep=False, should_stop=should_stop
                ),
                refresh_fn=lambda should_stop: self._services.camera.discover(
                    deep=True, should_stop=should_stop
                ),
            )
            if dialog.exec() == dialog.DialogCode.Accepted:
                device = dialog.selected or dialog.current_device()
                if device is not None:
                    self._services.camera.attach(create_camera(device))
        finally:
            self._device_dialog_open = False

    # -- presets -----------------------------------------------------------------
    def refresh_presets(self, select_id: Optional[int] = None) -> None:
        self._presets = self._services.db.list_presets()
        self.preset_combo.blockSignals(True)
        self.preset_combo.clear()
        for preset in self._presets:
            self.preset_combo.addItem(preset.display_name, preset.id)
        self.preset_combo.addItem(_CREATE_NEW)
        self.preset_combo.blockSignals(False)
        self.reports_page.refresh_presets()
        index = -1
        if select_id is not None:
            index = self.preset_combo.findData(select_id)
        if index < 0 and self._presets:
            index = 0
        if index >= 0:
            self.preset_combo.setCurrentIndex(index)
            self._load_preset_at(index)
        else:
            self._current_preset = None
            self.camera_view.set_preset(None)

    def _on_preset_selected(self, index: int) -> None:
        if index < 0:
            return
        if self.preset_combo.itemText(index) == _CREATE_NEW:
            self._open_preset_editor()
            return
        self._load_preset_at(index)

    def _load_preset_at(self, index: int) -> None:
        preset_id = self.preset_combo.itemData(index)
        preset = self._services.db.get_preset(preset_id) if preset_id else None
        self._current_preset = preset
        self.camera_view.set_preset(preset)

    def _open_preset_editor(self, copy_from: Optional[Preset] = None) -> None:
        dialog = PresetEditorDialog(
            self._services.db,
            frame=self._services.camera.latest_frame(),
            copy_from=copy_from,
            parent=self,
        )
        if dialog.exec() == dialog.DialogCode.Accepted and dialog.created_preset_id:
            self.refresh_presets(select_id=dialog.created_preset_id)
        else:
            # Restore previous selection when cancelled.
            self.refresh_presets(
                select_id=self._current_preset.id if self._current_preset else None
            )

    # -- tests --------------------------------------------------------------------
    def _wire_controller(self) -> None:
        self.controller.phaseChanged.connect(self._on_phase_changed)
        self.controller.statusMessage.connect(
            lambda msg: self.statusBar().showMessage(msg, 10000)
        )
        self.controller.sampleRecorded.connect(self.camera_view.on_sample)
        self.controller.testFinished.connect(self._on_test_finished)
        self.camera_view.startStandardRequested.connect(self._start_standard_test)
        self.camera_view.quickTestRequested.connect(self._run_quick_test)
        self.camera_view.emergencyStopRequested.connect(self.controller.emergency_stop)

    def _start_standard_test(self) -> None:
        preset = self._current_preset
        try:
            if preset is None or not preset.regions:
                raise TestStartError("Select a preset with at least one region first")
            if self._services.camera.state != CAM_CONNECTED:
                raise TestStartError("Camera is not connected")
            if not self._services.camera.is_radiometric:
                raise TestStartError(
                    "The active camera interface does not provide radiometric data"
                )
        except TestStartError as exc:
            QMessageBox.warning(self, "Cannot Start Test", str(exc))
            return
        if not confirm_start_standard_test(preset, self._services.settings, self):
            return
        # Starting a test must enable live display.
        self.camera_view.resume_live_display()
        try:
            self.controller.start(preset, self._services.settings, self._services.camera)
        except TestStartError as exc:
            QMessageBox.warning(self, "Cannot Start Test", str(exc))

    def _run_quick_test(self) -> None:
        try:
            result = run_quick_test(
                self._services.camera, self._current_preset,
                self._services.settings, self._services.db,
            )
        except QuickTestError as exc:
            QMessageBox.warning(self, "Quick Test", str(exc))
            return
        self.camera_view.show_result(result)
        self.reports_page.reload()

    def _on_phase_changed(self, phase: str) -> None:
        standard = phase in ("waiting_rise", "measuring")
        self._standard_running = standard
        self.camera_view.set_test_running(standard, standard=True)
        self.settings_page.set_locked(standard)
        self.preset_combo.setEnabled(not standard)

    def _on_test_finished(self, result: dict) -> None:
        self._standard_running = False
        self.camera_view.set_test_running(False)
        self.camera_view.show_result(result)
        self.settings_page.set_locked(False)
        self.preset_combo.setEnabled(True)
        self.reports_page.reload()

    def _on_settings_saved(self, settings) -> None:
        # Emissivity is applied to the camera immediately by services.
        self.statusBar().showMessage("Settings saved and applied.", 5000)

    # -- close ---------------------------------------------------------------------
    def closeEvent(self, event) -> None:  # noqa: N802
        if self.controller.is_running:
            if not confirm_close_during_test(self):
                event.ignore()
                return
            self.controller.abort(REASON_APP_CLOSED)
        self._services.settings.window_width = self.width()
        self._services.settings.window_height = self.height()
        try:
            from app.core.configuration import save_settings
            save_settings(self._services.data_dir, self._services.settings)
        except Exception:
            pass
        self._services.shutdown(normal=True)
        event.accept()
