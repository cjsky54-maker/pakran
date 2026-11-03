"""Settings page: test settings, emissivity, preset management, logs."""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.core.configuration import SettingsData
from app.core.constants import SAMPLE_INTERVAL_CHOICES
from app.database.models import Preset
from app.ui.dialogs.confirmations import confirm_delete_preset, confirm_restore_preset
from app.ui.preset_editor import PresetEditorDialog


class SettingsPage(QWidget):
    settingsSaved = Signal(object)  # SettingsData

    def __init__(self, services, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._services = services
        self._locked = False
        self._build_ui()
        self.reload_all()

    # -- UI ------------------------------------------------------------------
    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        test_box = QGroupBox("Test Settings")
        form = QFormLayout(test_box)
        self.rise_spin = QDoubleSpinBox()
        self.rise_spin.setDecimals(1)
        self.rise_spin.setRange(0.1, 100.0)
        self.rise_spin.setSuffix(" °C")
        self.wait_spin = QDoubleSpinBox()
        self.wait_spin.setDecimals(0)
        self.wait_spin.setRange(5, 3600)
        self.wait_spin.setSuffix(" s")
        self.interval_combo = QComboBox()
        for seconds in SAMPLE_INTERVAL_CHOICES:
            self.interval_combo.addItem(f"{seconds} s", seconds)
        self.reconnect_spin = QDoubleSpinBox()
        self.reconnect_spin.setDecimals(0)
        self.reconnect_spin.setRange(2, 300)
        self.reconnect_spin.setSuffix(" s")
        form.addRow("Temperature Rise Threshold:", self.rise_spin)
        form.addRow("Waiting Timeout:", self.wait_spin)
        form.addRow("Sample Interval:", self.interval_combo)
        form.addRow("Camera Reconnection Timeout:", self.reconnect_spin)
        layout.addWidget(test_box)

        camera_box = QGroupBox("Camera Settings")
        cam_form = QFormLayout(camera_box)
        self.emissivity_spin = QDoubleSpinBox()
        self.emissivity_spin.setDecimals(2)
        self.emissivity_spin.setRange(0.01, 1.00)
        self.emissivity_spin.setSingleStep(0.01)
        cam_form.addRow("Emissivity:", self.emissivity_spin)
        note = QLabel(
            "Only Emissivity is user-adjustable in version 1. Distance, reflected "
            "temperature and humidity use camera defaults. Palette is fixed; the "
            "temperature scale auto-adjusts per frame (display only)."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color:#5a616e;")
        cam_form.addRow(note)
        layout.addWidget(camera_box)

        preset_box = QGroupBox("Preset Management")
        preset_layout = QHBoxLayout(preset_box)
        active_wrap = QVBoxLayout()
        active_wrap.addWidget(QLabel("Active Presets"))
        self.active_list = QListWidget()
        active_wrap.addWidget(self.active_list)
        active_btns = QHBoxLayout()
        self.copy_btn = QPushButton("Copy Preset")
        self.delete_btn = QPushButton("Delete Preset")
        self.copy_btn.clicked.connect(self._copy_preset)
        self.delete_btn.clicked.connect(self._delete_preset)
        active_btns.addWidget(self.copy_btn)
        active_btns.addWidget(self.delete_btn)
        active_wrap.addLayout(active_btns)
        preset_layout.addLayout(active_wrap, 1)
        deleted_wrap = QVBoxLayout()
        deleted_wrap.addWidget(QLabel("Deleted Presets"))
        self.deleted_list = QListWidget()
        deleted_wrap.addWidget(self.deleted_list)
        self.restore_btn = QPushButton("Restore")
        self.restore_btn.clicked.connect(self._restore_preset)
        deleted_wrap.addWidget(self.restore_btn)
        preset_layout.addLayout(deleted_wrap, 1)
        layout.addWidget(preset_box, 1)

        maint_box = QGroupBox("Maintenance")
        maint_layout = QHBoxLayout(maint_box)
        self.logs_btn = QPushButton("Open Logs Folder")
        self.logs_btn.clicked.connect(self._open_logs)
        maint_layout.addWidget(self.logs_btn)
        maint_layout.addStretch(1)
        layout.addWidget(maint_box)

        bottom = QHBoxLayout()
        self.locked_label = QLabel("Settings are locked while a test is running.")
        self.locked_label.setStyleSheet("color:#8a6d3b;")
        self.locked_label.hide()
        self.save_btn = QPushButton("Save")
        self.save_btn.setStyleSheet("font-weight:bold;")
        self.discard_btn = QPushButton("Discard Changes")
        self.save_btn.clicked.connect(self._save)
        self.discard_btn.clicked.connect(self.reload_all)
        bottom.addWidget(self.locked_label)
        bottom.addStretch(1)
        bottom.addWidget(self.discard_btn)
        bottom.addWidget(self.save_btn)
        layout.addLayout(bottom)

    # -- loading -----------------------------------------------------------------
    def reload_all(self) -> None:
        """Reload from disk; unsaved changes are discarded (per spec)."""
        settings = self._services.settings
        self.rise_spin.setValue(settings.temperature_rise_threshold)
        self.wait_spin.setValue(settings.waiting_timeout)
        index = list(SAMPLE_INTERVAL_CHOICES).index(
            settings.sample_interval if settings.sample_interval in SAMPLE_INTERVAL_CHOICES
            else SAMPLE_INTERVAL_CHOICES[2]
        )
        self.interval_combo.setCurrentIndex(index)
        self.reconnect_spin.setValue(settings.camera_reconnection_timeout)
        self.emissivity_spin.setValue(settings.emissivity)
        self._reload_presets()

    def _reload_presets(self) -> None:
        self.active_list.clear()
        self.deleted_list.clear()
        self._active_presets: list[Preset] = []
        self._deleted_presets: list[Preset] = []
        for preset in self._services.db.list_presets(include_deleted=True):
            item = QListWidgetItem(f"{preset.display_name}   [{preset.created_at[:10]}]")
            item.setData(Qt.ItemDataRole.UserRole, preset.id)
            if preset.is_deleted:
                self._deleted_presets.append(preset)
                self.deleted_list.addItem(item)
            else:
                self._active_presets.append(preset)
                self.active_list.addItem(item)

    # -- actions --------------------------------------------------------------------
    def _save(self) -> None:
        if self._locked:
            return
        settings = SettingsData(
            temperature_rise_threshold=round(self.rise_spin.value(), 1),
            waiting_timeout=round(self.wait_spin.value(), 0),
            sample_interval=self.interval_combo.currentData(),
            camera_reconnection_timeout=round(self.reconnect_spin.value(), 0),
            emissivity=round(self.emissivity_spin.value(), 2),
            show_simulated_camera=self._services.settings.show_simulated_camera,
            window_width=self._services.settings.window_width,
            window_height=self._services.settings.window_height,
        )
        try:
            self._services.save_settings(settings)
        except Exception as exc:
            QMessageBox.critical(self, "Save Failed", f"Could not save settings:\n{exc}")
            return
        self.settingsSaved.emit(settings)
        QMessageBox.information(self, "Settings", "Settings saved.")

    def _copy_preset(self) -> None:
        preset = self._selected_active_preset()
        if preset is None:
            QMessageBox.information(self, "Copy Preset", "Select an active preset first.")
            return
        dialog = PresetEditorDialog(
            self._services.db, frame=self._latest_frame(), copy_from=preset, parent=self
        )
        if dialog.exec() == dialog.DialogCode.Accepted and dialog.created_preset_id:
            self._reload_presets()
            QMessageBox.information(
                self, "Copy Preset",
                "Preset copied. The original preset is unchanged.",
            )

    def _delete_preset(self) -> None:
        preset = self._selected_active_preset()
        if preset is None:
            QMessageBox.information(self, "Delete Preset", "Select an active preset first.")
            return
        if not confirm_delete_preset(preset, self):
            return
        self._services.db.soft_delete_preset(preset.id)
        self._reload_presets()

    def _restore_preset(self) -> None:
        row = self.deleted_list.currentRow()
        if row < 0 or row >= len(self._deleted_presets):
            QMessageBox.information(self, "Restore Preset", "Select a deleted preset first.")
            return
        preset = self._deleted_presets[row]
        if not confirm_restore_preset(preset, self):
            return
        self._services.db.restore_preset(preset.id)
        self._reload_presets()

    def _selected_active_preset(self) -> Optional[Preset]:
        row = self.active_list.currentRow()
        if 0 <= row < len(self._active_presets):
            return self._active_presets[row]
        return None

    def _latest_frame(self):
        return self._services.camera.latest_frame()

    def _open_logs(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(self._services.logs_dir))

    # -- lock during tests -----------------------------------------------------------
    def set_locked(self, locked: bool) -> None:
        self._locked = locked
        self.locked_label.setVisible(locked)
        for widget in (
            self.rise_spin, self.wait_spin, self.interval_combo,
            self.reconnect_spin, self.emissivity_spin,
            self.save_btn, self.discard_btn,
            self.copy_btn, self.delete_btn, self.restore_btn,
        ):
            widget.setEnabled(not locked)
