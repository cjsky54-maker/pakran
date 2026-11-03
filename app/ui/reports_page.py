"""Reports page: searchable/sortable test table, details, delete, CSV export."""
from __future__ import annotations

import os
from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
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

from app.core.constants import RESULT_FAIL, RESULT_PASS
from app.core.jalali import display_date, display_time
from app.database.models import TestRecord
from app.reports.csv_exporter import export_tests_csv
from app.ui.dialogs.confirmations import confirm_delete_tests, confirm_overwrite_csv
from app.ui.dialogs.test_details import TestDetailsDialog

_COLUMNS = ["", "Test ID", "Type", "Date", "Time", "Device Code", "Version",
            "Result", "Status", "Regions", "Duration (s)"]


class ReportsPage(QWidget):
    """Dedicated reports page; SQLite is authoritative, CSV is manual."""

    def __init__(self, services, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._services = services
        self._records: list[TestRecord] = []
        self._build_ui()
        self.reload()

    # -- UI ---------------------------------------------------------------
    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        filters = QGroupBox("Filters")
        fgrid = QHBoxLayout(filters)
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Search: device code, version or test ID")
        self.type_combo = QComboBox()
        self.type_combo.addItems(["All Types", "Standard", "Quick"])
        self.result_combo = QComboBox()
        self.result_combo.addItems(["All Results", "PASS", "FAIL", "TIMEOUT", "INCOMPLETE"])
        self.preset_combo = QComboBox()
        self.date_from = QLineEdit()
        self.date_from.setPlaceholderText("From 1405/08/01")
        self.date_to = QLineEdit()
        self.date_to.setPlaceholderText("To 1405/08/12")
        apply_btn = QPushButton("Apply")
        clear_btn = QPushButton("Clear")
        apply_btn.clicked.connect(self.reload)
        clear_btn.clicked.connect(self._clear_filters)
        for w in (self.search_edit, self.type_combo, self.result_combo,
                  self.preset_combo, self.date_from, self.date_to, apply_btn, clear_btn):
            fgrid.addWidget(w)
        layout.addWidget(filters)

        self.table = QTableWidget(0, len(_COLUMNS))
        self.table.setHorizontalHeaderLabels(_COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.MultiSelection)
        self.table.setSortingEnabled(True)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table, 1)

        actions = QHBoxLayout()
        self.count_label = QLabel("0 tests")
        details_btn = QPushButton("Open Test Details")
        delete_btn = QPushButton("Delete Selected")
        export_btn = QPushButton("Export CSV")
        refresh_btn = QPushButton("Refresh")
        details_btn.clicked.connect(self._open_details)
        delete_btn.clicked.connect(self._delete_selected)
        export_btn.clicked.connect(self._export_csv)
        refresh_btn.clicked.connect(self.reload)
        actions.addWidget(self.count_label)
        actions.addStretch(1)
        actions.addWidget(details_btn)
        actions.addWidget(delete_btn)
        actions.addWidget(export_btn)
        actions.addWidget(refresh_btn)
        layout.addLayout(actions)

    # -- data ---------------------------------------------------------------
    def reload(self) -> None:
        test_type = None
        if self.type_combo.currentIndex() == 1:
            test_type = "standard"
        elif self.type_combo.currentIndex() == 2:
            test_type = "quick"
        result = None
        if self.result_combo.currentIndex() > 0:
            result = self.result_combo.currentText()
        preset_id = None
        preset_row = self.preset_combo.currentIndex()
        if preset_row > 0:
            preset_id = self._preset_ids[preset_row - 1]

        self._records = self._services.reports.query(
            test_type=test_type,
            result=result,
            preset_id=preset_id,
            jalali_date_from=self.date_from.text().strip() or None,
            jalali_date_to=self.date_to.text().strip() or None,
            search=self.search_edit.text().strip() or None,
        )
        self._fill_table()

    def refresh_presets(self) -> None:
        current = self.preset_combo.currentText()
        self.preset_combo.blockSignals(True)
        self.preset_combo.clear()
        self.preset_combo.addItem("All Presets")
        self._preset_ids = []
        for preset in self._services.db.list_presets():
            self.preset_combo.addItem(preset.display_name)
            self._preset_ids.append(preset.id)
        if current:
            index = self.preset_combo.findText(current)
            if index >= 0:
                self.preset_combo.setCurrentIndex(index)
        self.preset_combo.blockSignals(False)

    def _fill_table(self) -> None:
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(self._records))
        for row, record in enumerate(self._records):
            checkbox = QTableWidgetItem()
            checkbox.setFlags(
                Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
            )
            checkbox.setCheckState(Qt.CheckState.Unchecked)
            self.table.setItem(row, 0, checkbox)
            self._set_num(row, 1, record.id)
            self.table.setItem(row, 2, QTableWidgetItem(record.test_type.capitalize()))
            self.table.setItem(row, 3, QTableWidgetItem(display_date(record.started_at)))
            self.table.setItem(row, 4, QTableWidgetItem(display_time(record.started_at)))
            self.table.setItem(row, 5, QTableWidgetItem(record.device_code))
            self.table.setItem(row, 6, QTableWidgetItem(record.version))
            result_item = QTableWidgetItem(record.result_display)
            if record.result_display == RESULT_PASS:
                result_item.setForeground(Qt.GlobalColor.darkGreen)
            elif record.result_display == RESULT_FAIL:
                result_item.setForeground(Qt.GlobalColor.red)
            self.table.setItem(row, 7, result_item)
            self.table.setItem(row, 8, QTableWidgetItem(record.status))
            self._set_num(row, 9, record.region_count)
            duration = record.duration_s
            self._set_num(row, 10, f"{duration:.0f}" if duration is not None else "")
        self.table.setSortingEnabled(True)
        self.count_label.setText(f"{len(self._records)} test(s)")

    def _set_num(self, row: int, col: int, value) -> None:
        item = QTableWidgetItem()
        if isinstance(value, (int, float)):
            item.setData(Qt.ItemDataRole.DisplayRole, value)
        else:
            item.setText(str(value))
        item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        self.table.setItem(row, col, item)

    def _clear_filters(self) -> None:
        self.search_edit.clear()
        self.type_combo.setCurrentIndex(0)
        self.result_combo.setCurrentIndex(0)
        if hasattr(self, "_preset_ids"):
            self.preset_combo.setCurrentIndex(0)
        self.date_from.clear()
        self.date_to.clear()
        self.reload()

    # -- actions ------------------------------------------------------------
    def _checked_records(self) -> list[TestRecord]:
        selected = []
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item is not None and item.checkState() == Qt.CheckState.Checked:
                test_id = self.table.item(row, 1).data(Qt.ItemDataRole.DisplayRole)
                for record in self._records:
                    if record.id == test_id:
                        selected.append(record)
                        break
        return selected

    def _open_details(self) -> None:
        row = self.table.currentRow()
        if row < 0:
            QMessageBox.information(self, "Open Details", "Select a test row first.")
            return
        test_id = self.table.item(row, 1).data(Qt.ItemDataRole.DisplayRole)
        details = self._services.reports.get_details(int(test_id))
        if details is None:
            QMessageBox.warning(self, "Open Details", f"Test {test_id} not found.")
            return
        TestDetailsDialog(details, self).exec()

    def _delete_selected(self) -> None:
        records = self._checked_records()
        if not records:
            QMessageBox.information(
                self, "Delete Tests", "Check the checkbox of at least one test to delete."
            )
            return
        if not confirm_delete_tests(len(records), self):
            return
        self._services.reports.delete_tests([r.id for r in records])
        self.reload()

    def _export_csv(self) -> None:
        records = self._checked_records() or list(self._records)
        if not records:
            QMessageBox.information(self, "Export CSV", "No test records to export.")
            return
        default_path = os.path.join(
            self._services.exports_dir, "thermal_qc_export.csv"
        )
        path, _ = QFileDialog.getSaveFileName(
            self, "Export CSV", default_path, "CSV files (*.csv)"
        )
        if not path:
            return
        if os.path.exists(path) and not confirm_overwrite_csv(path, self):
            return
        try:
            count = export_tests_csv(path, records, self._services.db)
        except Exception as exc:
            QMessageBox.critical(self, "Export Failed", f"CSV export failed:\n{exc}")
            return
        QMessageBox.information(
            self, "Export Complete", f"{count} test record(s) exported to:\n{path}"
        )
