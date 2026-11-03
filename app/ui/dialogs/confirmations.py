"""Confirmation dialogs used across the application."""
from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QInputDialog,
    QLabel,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from app.core.constants import SAMPLE_INTERVAL_CHOICES


def confirm_start_standard_test(preset, settings, parent: QWidget | None = None) -> bool:
    """Confirmation dialog before a Standard Test starts."""
    lines = [
        f"Device Code: {preset.device_code}",
        f"Version: {preset.version}",
        "",
        "Region limits (maximum allowed ΔT):",
    ]
    for region in preset.regions:
        lines.append(f"  Region {region.label}: {region.max_allowed_delta_t:.1f} °C")
    lines += [
        "",
        f"Temperature Rise Threshold: {settings.temperature_rise_threshold:.1f} °C",
        f"Waiting Timeout: {settings.waiting_timeout:.0f} s",
        f"Sample Interval: {settings.sample_interval} s (T{settings.sample_interval} ... T60)",
    ]
    box = QMessageBox(parent)
    box.setWindowTitle("Start Standard Test")
    box.setIcon(QMessageBox.Icon.Question)
    box.setText("Start the Standard Test with the following configuration?")
    box.setInformativeText("\n".join(lines))
    box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel)
    box.setDefaultButton(QMessageBox.StandardButton.Cancel)
    return box.exec() == QMessageBox.StandardButton.Yes


def confirm_close_during_test(parent: QWidget | None = None) -> bool:
    box = QMessageBox(parent)
    box.setWindowTitle("Test in Progress")
    box.setIcon(QMessageBox.Icon.Warning)
    box.setText(
        "A Standard Test is running.\n"
        "Closing the application will stop the test and save the collected "
        "data as INCOMPLETE. Continue?"
    )
    box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel)
    box.setDefaultButton(QMessageBox.StandardButton.Cancel)
    return box.exec() == QMessageBox.StandardButton.Yes


def confirm_delete_tests(count: int, parent: QWidget | None = None) -> bool:
    """Permanent deletion requiring the user to type DELETE."""
    text, ok = QInputDialog.getText(
        parent,
        "Delete Test Records",
        f"{count} test record(s) will be permanently deleted.\n"
        "This cannot be undone (no trash/archive for tests).\n\n"
        "Type DELETE to confirm:",
    )
    return ok and text.strip() == "DELETE"


def confirm_overwrite_csv(path: str, parent: QWidget | None = None) -> bool:
    box = QMessageBox(parent)
    box.setWindowTitle("Overwrite File")
    box.setIcon(QMessageBox.Icon.Warning)
    box.setText(f"The file already exists:\n{path}\n\nOverwrite it?")
    box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel)
    return box.exec() == QMessageBox.StandardButton.Yes


def confirm_delete_preset(preset, parent: QWidget | None = None) -> bool:
    box = QMessageBox(parent)
    box.setWindowTitle("Delete Preset")
    box.setIcon(QMessageBox.Icon.Question)
    box.setText(
        f"Delete preset '{preset.device_code} / {preset.version}'?\n\n"
        "The preset is removed from the active list but can be restored "
        "from Settings. Historical tests are not affected."
    )
    box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel)
    return box.exec() == QMessageBox.StandardButton.Yes


def confirm_restore_preset(preset, parent: QWidget | None = None) -> bool:
    box = QMessageBox(parent)
    box.setWindowTitle("Restore Preset")
    box.setIcon(QMessageBox.Icon.Question)
    box.setText(
        f"Restore preset '{preset.device_code} / {preset.version}'?\n"
        "The original preset ID, regions, colors and ΔT limits are preserved."
    )
    box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel)
    return box.exec() == QMessageBox.StandardButton.Yes


class DatabaseRestoreDialog(QDialog):
    """Startup integrity failure: offer backup restoration."""

    def __init__(self, message: str, backup_available: bool, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Database Problem")
        self._choice = "exit"
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "A database integrity problem was detected:\n\n"
            f"{message}\n\n"
            + (
                "A previously verified backup is available. Restoring it will "
                "replace the current database (the current file is preserved "
                "next to the backup first)."
                if backup_available
                else "No valid backup is available. The application will start "
                     "with the current database as-is."
            )
        ))
        buttons = QDialogButtonBox()
        self.restore_btn = buttons.addButton(
            "Restore Backup", QDialogButtonBox.ButtonRole.AcceptRole
        )
        self.restore_btn.setEnabled(backup_available)
        continue_btn = buttons.addButton(
            "Continue Anyway", QDialogButtonBox.ButtonRole.AcceptRole
        )
        exit_btn = buttons.addButton(
            "Exit", QDialogButtonBox.ButtonRole.RejectRole
        )
        layout.addWidget(buttons)

        self.restore_btn.clicked.connect(lambda: self._choose("restore"))
        continue_btn.clicked.connect(lambda: self._choose("continue"))
        exit_btn.clicked.connect(lambda: self._choose("exit"))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

    def _choose(self, choice: str) -> None:
        self._choice = choice

    @property
    def wants_restore(self) -> bool:
        return self._choice == "restore"

    @property
    def wants_continue(self) -> bool:
        return self._choice == "continue"
