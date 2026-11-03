"""Preset creation dialog: draw rectangular regions on the thermal image.

- Regions are created by dragging the mouse; IDs (A, B, C...) and unique
  colors are assigned automatically.
- Max Allowed ΔT is set per region.
- At least one region is required; Save creates a NEW immutable preset.
- Copy mode prefills fields/regions from an existing preset; the source
  preset is never modified and the copy gets a new internal ID.
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QPoint, QRect, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.core.constants import (
    DEFAULT_MAX_ALLOWED_DELTA_T,
    SENSOR_HEIGHT,
    SENSOR_WIDTH,
)
from app.database.models import Preset
from app.thermal.region_calculator import (
    RegionDefinition,
    label_for_index,
    pick_color,
)
from app.ui.image_widget import ThermalImageWidget


class _EditorCanvas(ThermalImageWidget):
    """Thermal image that lets the user drag new rectangular regions."""

    regionDrawn = Signal(int, int, int, int)  # x, y, w, h in sensor pixels

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._press: Optional[QPoint] = None

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._press = event.position().toPoint()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._press is not None:
            current = event.position().toPoint()
            self._set_drag_rect(QRect(self._press, current).normalized())
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if self._press is not None and event.button() == Qt.MouseButton.LeftButton:
            start = self._press
            end = event.position().toPoint()
            self._press = None
            self._set_drag_rect(None)
            rect = QRect(start, end).normalized()
            if rect.width() >= 4 and rect.height() >= 4:
                sx, sy = self.sensor_point(rect.topLeft())
                ex, ey = self.sensor_point(rect.bottomRight())
                w, h = ex - sx + 1, ey - sy + 1
                if w >= 2 and h >= 2:
                    self.regionDrawn.emit(
                        sx, sy, min(w, SENSOR_WIDTH - sx), min(h, SENSOR_HEIGHT - sy)
                    )
        super().mouseReleaseEvent(event)


class PresetEditorDialog(QDialog):
    """Create a new preset (or a copy of an existing one)."""

    def __init__(
        self,
        db,
        frame=None,
        copy_from: Optional[Preset] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._db = db
        self._regions: list[RegionDefinition] = []
        self.created_preset_id: Optional[int] = None
        self.setWindowTitle("Create Preset" if copy_from is None else "Copy Preset")
        self.setMinimumWidth(1000)

        layout = QHBoxLayout(self)

        # ---- left: image canvas
        left = QVBoxLayout()
        form = QFormLayout()
        self.code_edit = QLineEdit()
        self.version_edit = QLineEdit()
        form.addRow("Device Code:", self.code_edit)
        form.addRow("Version:", self.version_edit)
        left.addLayout(form)
        self.canvas = _EditorCanvas()
        if frame is not None:
            self.canvas.set_frame(frame)
        self.canvas.regionDrawn.connect(self._add_region)
        left.addWidget(self.canvas)
        hint = QLabel("Drag with the mouse on the image to draw a rectangular region.")
        hint.setStyleSheet("color: #5a616e;")
        left.addWidget(hint)
        left_widget = QWidget()
        left_widget.setLayout(left)
        layout.addWidget(left_widget, 1)

        # ---- right: region list
        right = QVBoxLayout()
        box = QGroupBox("Regions")
        box_layout = QVBoxLayout(box)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["Region", "Color", "Max Allowed ΔT (°C)", "X", "Y", "W x H"]
        )
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        box_layout.addWidget(self.table)
        btn_row = QHBoxLayout()
        delete_btn = QPushButton("Delete Selected")
        clear_btn = QPushButton("Clear All")
        delete_btn.clicked.connect(self._delete_selected)
        clear_btn.clicked.connect(self._clear_regions)
        btn_row.addWidget(delete_btn)
        btn_row.addWidget(clear_btn)
        box_layout.addLayout(btn_row)
        right.addWidget(box)

        save_btn = QPushButton("Save Preset")
        save_btn.setStyleSheet("font-weight:bold;")
        cancel_btn = QPushButton("Cancel")
        save_btn.clicked.connect(self._save)
        cancel_btn.clicked.connect(self.reject)
        right.addWidget(save_btn)
        right.addWidget(cancel_btn)
        right_widget = QWidget()
        right_widget.setFixedWidth(430)
        right_widget.setLayout(right)
        layout.addWidget(right_widget)

        if copy_from is not None:
            self.code_edit.setText(copy_from.device_code)
            self.version_edit.setText(copy_from.version)
            for region in copy_from.regions:
                self._regions.append(
                    RegionDefinition(
                        label=region.label, x=region.x, y=region.y,
                        width=region.width, height=region.height,
                        color=region.color,
                        max_allowed_delta_t=region.max_allowed_delta_t,
                    )
                )
            self._refresh_table()

    # -- region management ------------------------------------------------------
    def _add_region(self, x: int, y: int, w: int, h: int) -> None:
        index = len(self._regions)
        self._regions.append(
            RegionDefinition(
                label=label_for_index(index),
                x=x, y=y, width=w, height=h,
                color=pick_color(index),
                max_allowed_delta_t=DEFAULT_MAX_ALLOWED_DELTA_T,
            )
        )
        self._refresh_table()

    def _delete_selected(self) -> None:
        row = self.table.currentRow()
        if 0 <= row < len(self._regions):
            # Labels/colors are reassigned so they stay unique and compact.
            del self._regions[row]
            for i, region in enumerate(self._regions):
                region.label = label_for_index(i)
                region.color = pick_color(i)
            self._refresh_table()

    def _clear_regions(self) -> None:
        self._regions.clear()
        self._refresh_table()

    def _refresh_table(self) -> None:
        self.table.setRowCount(len(self._regions))
        for row, region in enumerate(self._regions):
            self.table.setItem(row, 0, QTableWidgetItem(region.label))
            color_item = QTableWidgetItem("")
            color_item.setBackground(QColor(region.color))
            self.table.setItem(row, 1, color_item)
            spin = QDoubleSpinBox()
            spin.setDecimals(1)
            spin.setRange(0.0, 500.0)
            spin.setSingleStep(0.5)
            spin.setValue(region.max_allowed_delta_t)
            spin.valueChanged.connect(
                lambda value, r=row: self._regions[r].__setattr__(
                    "max_allowed_delta_t", float(value)
                )
            )
            self.table.setCellWidget(row, 2, spin)
            self.table.setItem(row, 3, QTableWidgetItem(str(region.x)))
            self.table.setItem(row, 4, QTableWidgetItem(str(region.y)))
            self.table.setItem(row, 5, QTableWidgetItem(f"{region.width} x {region.height}"))
        self.canvas.set_regions(self._regions)

    # -- save ----------------------------------------------------------------------
    def _save(self) -> None:
        device_code = self.code_edit.text().strip()
        version = self.version_edit.text().strip()
        if not device_code or not version:
            QMessageBox.warning(
                self, "Missing Fields",
                "Device Code and Version are required.",
            )
            return
        if not self._regions:
            QMessageBox.warning(
                self, "No Regions",
                "At least one region is required. Draw a rectangle on the image.",
            )
            return
        for region in self._regions:
            if region.max_allowed_delta_t <= 0:
                QMessageBox.warning(
                    self, "Invalid Limit",
                    f"Region {region.label}: Maximum Allowed ΔT must be positive.",
                )
                return
        try:
            self.created_preset_id = self._db.create_preset(
                device_code, version, self._regions
            )
        except Exception as exc:
            QMessageBox.critical(self, "Save Failed", f"Could not save preset:\n{exc}")
            return
        self.accept()
