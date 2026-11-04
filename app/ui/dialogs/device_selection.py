"""Device selection dialog (discovered devices, refresh, connect).

When a ``refresh_fn`` is provided the dialog runs a DEEP discovery pass in
a background thread (the pass probes each UVC device for the HIKMICRO
HikCamera raw stream, which takes a second or two per device). The table
is shown immediately with the fast shallow list, then refreshed in place
when the deep pass completes - the GUI never blocks.
"""
from __future__ import annotations

import threading
from typing import Callable, Optional

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from app.camera.camera_models import CameraInfo

_DETECTING = "Detecting HikCamera capability..."


class DeviceSelectionDialog(QDialog):
    """Choose one discovered camera; returns the selected CameraInfo."""

    # Marshals deep-discovery results from the background thread to the
    # GUI thread (queued connection).
    _devicesReady = Signal(list)

    def __init__(
        self,
        devices: list[CameraInfo],
        parent=None,
        refresh_fn: Optional[Callable[[], list[CameraInfo]]] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Select Camera")
        self.setMinimumWidth(640)
        self.selected: Optional[CameraInfo] = None
        self._refresh_fn = refresh_fn
        self._probe_thread: threading.Thread | None = None
        self._closed = False

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Discovered devices:"))

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Device", "Interface", "Radiometric", "Status"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.doubleClicked.connect(self.accept)
        layout.addWidget(self.table)

        buttons = QHBoxLayout()
        self.refresh_btn = QPushButton("Refresh")
        cancel_btn = QPushButton("Cancel")
        self.connect_btn = QPushButton("Connect")
        self.connect_btn.setDefault(True)
        self.connect_btn.setEnabled(False)
        buttons.addWidget(self.refresh_btn)
        buttons.addStretch(1)
        buttons.addWidget(cancel_btn)
        buttons.addWidget(self.connect_btn)
        layout.addLayout(buttons)

        self.table.itemSelectionChanged.connect(self._selection_changed)
        self.connect_btn.clicked.connect(self.accept)
        cancel_btn.clicked.connect(self.reject)
        self.refresh_btn.clicked.connect(self.start_deep_refresh)
        self.refresh_btn.setEnabled(refresh_fn is not None)
        self._devicesReady.connect(self._apply_devices)

        self._devices: list[CameraInfo] = []
        self._apply_devices(devices)
        # Identify HIKMICRO cameras right away (background, non-blocking).
        if refresh_fn is not None:
            self.start_deep_refresh()

    # -- device table --------------------------------------------------------
    def _apply_devices(self, devices: list[CameraInfo]) -> None:
        if self._closed:
            return
        # Deep pass finished - allow another refresh.
        self._probe_thread = None
        self.refresh_btn.setEnabled(self._refresh_fn is not None)
        previous_key = None
        if self._devices:
            current = self.current_device()
            previous_key = current.key if current else None
        self._devices = list(devices)

        self.table.setRowCount(len(self._devices))
        for row, dev in enumerate(self._devices):
            self.table.setItem(row, 0, QTableWidgetItem(dev.name))
            self.table.setItem(row, 1, QTableWidgetItem(self._interface_text(dev)))
            self.table.setItem(row, 2, QTableWidgetItem("Yes" if dev.is_radiometric else "No"))
            self.table.setItem(row, 3, QTableWidgetItem(dev.details))
        if previous_key is not None:
            for row, dev in enumerate(self._devices):
                if dev.key == previous_key:
                    self.table.selectRow(row)
                    break

    @staticmethod
    def _interface_text(dev: CameraInfo) -> str:
        if dev.kind == "uvc" and dev.name.startswith("HIKMICRO"):
            return "HikCamera USB (UVC)"
        return {
            "simulated": "Simulated (development)",
            "uvc": "UVC / USB video",
            "hikmicro-sdk": "HIKMICRO SDK",
        }.get(dev.kind, dev.kind)

    # -- background deep discovery --------------------------------------------
    def start_deep_refresh(self) -> None:
        """Re-run discovery (including the HikCamera probe) in the background."""
        if self._refresh_fn is None or self._probe_thread is not None:
            return
        self.refresh_btn.setEnabled(False)
        # Mark generic UVC rows as being re-checked.
        for row, dev in enumerate(self._devices):
            if dev.kind == "uvc" and not dev.name.startswith("HIKMICRO"):
                self.table.setItem(row, 3, QTableWidgetItem(_DETECTING))
        self._probe_thread = threading.Thread(
            target=self._run_refresh, daemon=True, name="device-deep-discovery"
        )
        self._probe_thread.start()

    def _run_refresh(self) -> None:
        try:
            devices = self._refresh_fn() or []
        except Exception:
            devices = []
        # Signal emission from a non-Qt thread is safe: delivery to this
        # dialog (GUI thread) is automatically queued.
        self._devicesReady.emit(list(devices))

    # -- selection -------------------------------------------------------------
    def _selection_changed(self) -> None:
        self.connect_btn.setEnabled(bool(self.table.selectedItems()))

    def current_device(self) -> Optional[CameraInfo]:
        row = self.table.currentRow()
        if 0 <= row < len(self._devices):
            return self._devices[row]
        return None

    def reject(self) -> None:  # noqa: D102 - QDialog override
        self._closed = True
        super().reject()

    def accept(self) -> None:  # noqa: D102 - QDialog override
        self._closed = True
        device = self.current_device()
        if device is not None:
            self.selected = device
        super().accept()
