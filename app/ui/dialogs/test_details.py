"""Test details dialog: full numerical measurements for one test."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHeaderView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.core.jalali import display_datetime, display_time


class TestDetailsDialog(QDialog):
    """Shows every recorded sample and region measurement of a test."""

    def __init__(self, details: dict, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        record = details["record"]
        snapshot = details["preset_snapshot"]
        settings_snapshot = details["settings_snapshot"]
        self.setWindowTitle(f"Test Details - #{record.id}")
        self.setMinimumSize(780, 520)

        layout = QVBoxLayout(self)
        info = QLabel(
            f"Test ID: {record.id}    Type: {record.test_type.capitalize()}    "
            f"Started: {display_datetime(record.started_at)}\n"
            f"Device Code: {record.device_code}    Version: {record.version}    "
            f"Preset ID: {record.preset_id}\n"
            f"Result: {record.overall_result or '-'}    Status: {record.status}\n"
            f"Failure Reason: {record.failure_reason or '-'}\n"
            f"Settings snapshot: rise {settings_snapshot.get('temperature_rise_threshold')} °C, "
            f"waiting timeout {settings_snapshot.get('waiting_timeout')} s, "
            f"interval {settings_snapshot.get('sample_interval')} s, "
            f"emissivity {settings_snapshot.get('emissivity')}"
        )
        info.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(info)

        samples = details["samples"]
        region_labels = sorted({
            m.region_label for s in samples for m in s.measurements
        })
        columns = ["T (s)", "Timestamp", "Ambient (°C)"]
        for label in region_labels:
            columns += [
                f"{label} Min", f"{label} Max", f"{label} ΔT", f"{label} Status",
            ]
        table = QTableWidget(len(samples), len(columns))
        table.setHorizontalHeaderLabels(columns)
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)

        for row, sample in enumerate(samples):
            table.setItem(row, 0, QTableWidgetItem(f"{sample.sample_time_seconds:g}"))
            table.setItem(row, 1, QTableWidgetItem(display_time(sample.timestamp)))
            table.setItem(row, 2, QTableWidgetItem(f"{sample.ambient_temperature:.2f}"))
            by_label = {m.region_label: m for m in sample.measurements}
            col = 3
            for label in region_labels:
                m = by_label.get(label)
                for value in (
                    f"{m.min_temperature:.2f}" if m and m.min_temperature is not None else "",
                    f"{m.max_temperature:.2f}" if m and m.max_temperature is not None else "",
                    f"{m.delta_t:.2f}" if m and m.delta_t is not None else "",
                    m.region_status if m else "",
                ):
                    table.setItem(row, col, QTableWidgetItem(value))
                    col += 1
        layout.addWidget(table, 1)
