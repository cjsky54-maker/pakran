"""Device selection dialog (discovered devices, refresh, connect)."""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
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


class DeviceSelectionDialog(QDialog):
    """Choose one discovered camera; returns the selected CameraInfo."""

    def __init__(self, devices: list[CameraInfo], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Select Camera")
        self.setMinimumWidth(560)
        self.selected: Optional[CameraInfo] = None

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Discovered devices:"))

        self.table = QTableWidget(len(devices), 4)
        self.table.setHorizontalHeaderLabels(["Device", "Interface", "Radiometric", "Status"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        for row, dev in enumerate(devices):
            self.table.setItem(row, 0, QTableWidgetItem(dev.name))
            kind_display = {
                "simulated": "Simulated (development)",
                "uvc": "UVC / USB video",
                "hikmicro-sdk": "HIKMICRO SDK",
            }.get(dev.kind, dev.kind)
            self.table.setItem(row, 1, QTableWidgetItem(kind_display))
            self.table.setItem(row, 2, QTableWidgetItem("Yes" if dev.is_radiometric else "No"))
            self.table.setItem(row, 3, QTableWidgetItem(dev.details))
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
        self._devices = devices

    def _selection_changed(self) -> None:
        self.connect_btn.setEnabled(bool(self.table.selectedItems()))

    def current_device(self) -> Optional[CameraInfo]:
        row = self.table.currentRow()
        if 0 <= row < len(self._devices):
            return self._devices[row]
        return None
