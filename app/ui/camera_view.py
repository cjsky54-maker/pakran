"""Main live-view page: image, ambient, region table, test controls, results."""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from app.core.constants import (
    CAM_CONNECTED,
    RESULT_FAIL,
    RESULT_PASS,
)
from app.database.models import Preset
from app.testing.result_format import format_result_text
from app.thermal.region_calculator import evaluate_region, measure_region
from app.thermal.temperature_processor import compute_ambient
from app.ui.image_widget import ThermalImageWidget

_STATUS_COLOR = {
    RESULT_PASS: "#2e7d32",
    RESULT_FAIL: "#c62828",
    "WAITING": "#5a616e",
}


class CameraView(QWidget):
    """Home page: live thermal inspection and test execution."""

    startStandardRequested = Signal()
    quickTestRequested = Signal()
    emergencyStopRequested = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._preset: Optional[Preset] = None
        self._playing = True
        self._radiometric = False
        self._test_running = False
        self._camera_connected = False
        self._build_ui()

    # -- UI construction ------------------------------------------------------
    def _build_ui(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)

        # ---- left: image column
        left = QVBoxLayout()
        toolbar = QHBoxLayout()
        self.play_pause_btn = QPushButton("Pause Live Display")
        self.play_pause_btn.setCheckable(True)
        self.play_pause_btn.clicked.connect(self._toggle_play)
        self.cursor_label = QLabel("Cursor: -")
        self.cursor_label.setStyleSheet("color: #5a616e;")
        toolbar.addWidget(self.play_pause_btn)
        toolbar.addStretch(1)
        toolbar.addWidget(self.cursor_label)
        left.addLayout(toolbar)

        self.image_widget = ThermalImageWidget()
        self.image_widget.hoverInfo.connect(self._on_hover)
        left.addWidget(self.image_widget)

        self.banner = QLabel("")
        self.banner.setWordWrap(True)
        self.banner.setStyleSheet(
            "background:#fff3cd; color:#664d03; border:1px solid #ffe69c;"
            "border-radius:4px; padding:6px;"
        )
        self.banner.hide()
        left.addWidget(self.banner)
        root.addLayout(left, 1)

        # ---- right: measurements and controls
        right = QVBoxLayout()

        ambient_box = QGroupBox("Ambient Temperature")
        amb_layout = QVBoxLayout(ambient_box)
        self.ambient_label = QLabel("--.- °C")
        big = QFont("Segoe UI", 22)
        big.setBold(True)
        self.ambient_label.setFont(big)
        self.ambient_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        amb_layout.addWidget(self.ambient_label)
        right.addWidget(ambient_box)

        regions_box = QGroupBox("Region Measurements")
        reg_layout = QVBoxLayout(regions_box)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["Region", "Min (°C)", "Max (°C)", "Amb (°C)", "ΔT (°C)", "Status"]
        )
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        # Keep headers fully legible in the narrow side panel: columns may
        # never shrink below this, a scrollbar appears instead of eliding.
        header.setMinimumSectionSize(76)
        reg_layout.addWidget(self.table)
        right.addWidget(regions_box, 1)

        test_box = QGroupBox("Test Controls")
        test_layout = QVBoxLayout(test_box)
        self.standard_btn = QPushButton("Start Standard Test")
        self.quick_btn = QPushButton("Quick Test")
        self.emergency_btn = QPushButton("Emergency Stop")
        self.emergency_btn.setStyleSheet(
            "background:#c62828; color:white; font-weight:bold;"
        )
        self.standard_btn.clicked.connect(self.startStandardRequested.emit)
        self.quick_btn.clicked.connect(self.quickTestRequested.emit)
        self.emergency_btn.clicked.connect(self.emergencyStopRequested.emit)
        quick_row = QHBoxLayout()
        self.quick_btn.setMaximumHeight(32)
        self.quick_result = QLabel("")
        self.quick_result.setStyleSheet("font-weight:bold;")
        quick_row.addWidget(self.quick_btn, 1)
        quick_row.addWidget(self.quick_result)
        test_layout.addWidget(self.standard_btn)
        test_layout.addLayout(quick_row)
        test_layout.addWidget(self.emergency_btn)
        right.addWidget(test_box)

        result_box = QGroupBox("Result Panel")
        res_layout = QVBoxLayout(result_box)
        self.result_text = QTextEdit()
        self.result_text.setReadOnly(True)
        self.result_text.setFixedHeight(150)
        res_layout.addWidget(self.result_text)
        self.copy_btn = QPushButton("Copy Failure Text")
        self.copy_btn.setEnabled(False)
        self.copy_btn.clicked.connect(self._copy_failure)
        res_layout.addWidget(self.copy_btn)
        right.addWidget(result_box)

        right_widget = QWidget()
        right_widget.setLayout(right)
        # Wide enough for the 6 measurement columns (6 x 76px minimum) so
        # no header ever truncates; the camera column takes the rest.
        right_widget.setFixedWidth(505)
        root.addWidget(right_widget)

        self._update_buttons()
        self.set_camera_state(False, False)

    # -- public API --------------------------------------------------------------
    def set_preset(self, preset: Optional[Preset]) -> None:
        self._preset = preset
        self.image_widget.set_regions(preset.regions if preset else [])
        self.table.setRowCount(0)
        if preset:
            self.table.setRowCount(len(preset.regions))
            for row, region in enumerate(preset.regions):
                for col in range(6):
                    item = QTableWidgetItem("")
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                    self.table.setItem(row, col, item)
                self.table.item(row, 0).setText(region.label)
        self._update_buttons()

    def set_camera_state(self, connected: bool, radiometric: bool, simulated: bool = False) -> None:
        self._camera_connected = connected
        self._radiometric = radiometric
        if not connected:
            self.banner.setText(
                "Camera disconnected. Presets, Reports and Settings remain available."
            )
            self.banner.setStyleSheet(
                "background:#fdecea; color:#8a1c1c; border:1px solid #f5c2c7;"
                "border-radius:4px; padding:6px;"
            )
            self.banner.show()
        elif simulated:
            self.banner.setText(
                "SIMULATED DATA - synthetic camera for development. "
                "NOT a real HIKMICRO camera."
            )
            self.banner.setStyleSheet(
                "background:#e7f1ff; color:#0b3d91; border:1px solid #b6d4fe;"
                "border-radius:4px; padding:6px;"
            )
            self.banner.show()
        elif not radiometric:
            self.banner.setText(
                "Radiometric temperature data is not available from this camera "
                "interface. Live image only - measurements and tests are disabled."
            )
            self.banner.setStyleSheet(
                "background:#fff3cd; color:#664d03; border:1px solid #ffe69c;"
                "border-radius:4px; padding:6px;"
            )
            self.banner.show()
        else:
            self.banner.hide()
        self._update_buttons()

    def set_test_running(self, running: bool, standard: bool = False) -> None:
        self._test_running = running
        self.emergency_btn.setEnabled(running and standard)
        self._update_buttons()

    def on_frame(self, frame) -> None:
        """Live update from the camera manager (display + measurements)."""
        if self._playing:
            self.image_widget.set_frame(frame)
        if not frame.is_radiometric:
            return
        ambient = compute_ambient(frame.matrix)
        self.ambient_label.setText(f"{ambient:.1f} °C")
        if self._preset is None:
            return
        for row, region in enumerate(self._preset.regions):
            mm = measure_region(frame.matrix, region)
            if mm is None:
                continue
            delta = mm[1] - ambient
            status = evaluate_region(delta, region.max_allowed_delta_t)
            self._set_row(row, mm[0], mm[1], ambient, delta, status)

    def on_sample(self, sample: dict) -> None:
        """Update the table from a recorded test sample."""
        ambient = sample.get("ambient")
        if ambient is not None:
            self.ambient_label.setText(f"{ambient:.1f} °C")
        if self._preset is None:
            return
        for row, region in enumerate(self._preset.regions):
            data = sample.get("regions", {}).get(region.label)
            if data is None:
                continue
            self._set_row(
                row, data["min"], data["max"], ambient or 0.0,
                data["delta_t"], data["status"],
            )

    def show_result(self, result: dict) -> None:
        self.result_text.setPlainText(format_result_text(result))
        overall = result.get("overall_result")
        if result.get("test_type") == "quick":
            if overall == RESULT_PASS:
                self.quick_result.setText("PASS")
                self.quick_result.setStyleSheet("color:#2e7d32; font-weight:bold;")
            elif overall == RESULT_FAIL:
                self.quick_result.setText("FAIL")
                self.quick_result.setStyleSheet("color:#c62828; font-weight:bold;")
            else:
                self.quick_result.setText(result.get("status", ""))
                self.quick_result.setStyleSheet("color:#5a616e; font-weight:bold;")
        self.copy_btn.setEnabled(overall == RESULT_FAIL)

    def clear_result(self) -> None:
        self.result_text.setPlainText("")
        self.quick_result.setText("")
        self.copy_btn.setEnabled(False)

    def resume_live_display(self) -> None:
        """Starting a test must enable live display (per spec)."""
        self._playing = True
        self.play_pause_btn.setChecked(False)
        self.play_pause_btn.setText("Pause Live Display")

    # -- internals ---------------------------------------------------------------
    def _set_row(self, row: int, tmin: float, tmax: float, ambient: float,
                 delta: float, status: str) -> None:
        values = [
            f"{tmin:.1f}", f"{tmax:.1f}", f"{ambient:.1f}", f"{delta:.1f}", status,
        ]
        for col, text in enumerate(values, start=1):
            item = self.table.item(row, col)
            if item is None:
                continue
            item.setText(text)
            if col == 5:
                color = _STATUS_COLOR.get(status, "#5a616e")
                item.setForeground(QColor("white"))
                item.setBackground(QColor(color))

    def _toggle_play(self) -> None:
        # Pausing only freezes the display; acquisition and sampling continue.
        self._playing = not self.play_pause_btn.isChecked()
        self.play_pause_btn.setText(
            "Pause Live Display" if self._playing else "Resume Live Display"
        )

    def _on_hover(self, info: dict) -> None:
        if info.get("temp") is None:
            self.cursor_label.setText("Cursor: -")
        else:
            self.cursor_label.setText(
                f"Cursor: ({info['x']}, {info['y']})  {info['temp']:.1f} °C"
            )

    def _update_buttons(self) -> None:
        enabled = (
            self._camera_connected and self._radiometric
            and self._preset is not None and not self._test_running
        )
        self.standard_btn.setEnabled(enabled)
        self.quick_btn.setEnabled(enabled)

    def _copy_failure(self) -> None:
        from PySide6.QtWidgets import QApplication
        clipboard = QApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(self.result_text.toPlainText())
