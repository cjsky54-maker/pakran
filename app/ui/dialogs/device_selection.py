"""Device selection dialog (discovered devices, refresh, connect).

The dialog opens INSTANTLY with a native-call-free list (simulated camera,
SDK devices and the camera that is currently streaming). A background
discovery sequence then fills in the rest in two stages, so the user sees
USB camera rows after a second or two instead of waiting for the full
deep probe:

1. ``shallow_fn`` (optional) briefly opens each UVC index and lists every
   device that answers - fast, but names stay generic ("UVC Camera 0").
2. ``refresh_fn`` runs the DEEP HikCamera capability probe per device
   (several seconds each, 15-30 s total) and refines the rows in place:
   proper HIKMICRO name plus an honest Radiometric column.

v1.0.3: the whole sequence is CANCELLABLE. Closing the dialog (or clicking
Connect/Cancel) sets a cancel event that every stage honours between its
native camera calls, so connecting never has to wait behind a still
running probe - and the per-device locks in ``app.camera.uvc_camera``
make an actual overlap impossible even if a native call is in flight.

The background thread performs NO Qt calls at all (emitting a Qt signal
from a plain thread after the dialog was destroyed is a native crash);
it only writes a Python result and sets an event. A QTimer owned by the
dialog polls that result on the GUI thread and stops automatically when
the dialog is destroyed. An empty final result (or a failing discovery)
replaces the placeholder row with an honest "No cameras found" /
"Discovery failed" status instead of searching forever.
"""
from __future__ import annotations

import threading
from typing import Callable, Optional

from PySide6.QtCore import Qt, QTimer
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
_SEARCHING = "Searching for USB cameras..."
_NO_CAMERAS = "No cameras found - connect a USB camera and click Refresh."
_DISCOVERY_FAILED = "Discovery failed - click Refresh to retry."
_POLL_MS = 50


class DeviceSelectionDialog(QDialog):
    """Choose one discovered camera; returns the selected CameraInfo."""

    def __init__(
        self,
        devices: list[CameraInfo],
        parent=None,
        refresh_fn: Optional[Callable[..., list[CameraInfo]]] = None,
        shallow_fn: Optional[Callable[..., list[CameraInfo]]] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Select Camera")
        self.setMinimumWidth(640)
        self.selected: Optional[CameraInfo] = None
        self._refresh_fn = refresh_fn
        # Optional fast pre-stage: briefly opens each UVC index (a second or
        # two) so the camera rows appear long before the deep probe (15-30 s)
        # finishes refining names and the Radiometric column.
        self._shallow_fn = shallow_fn
        self._probe_thread: threading.Thread | None = None
        # Set as soon as the dialog closes: stops the background discovery
        # between its native calls (Connect must not wait for it).
        self._probe_cancel = threading.Event()
        self._closed = False
        # Result handover from the probe thread (plain Python only - the
        # thread never touches Qt; see the module docstring).
        self._probe_result: list[CameraInfo] | None = None
        self._probe_done = threading.Event()
        self._probe_stages: list[Callable[..., list[CameraInfo]]] = []
        self._current_stage: Optional[Callable[..., list[CameraInfo]]] = None
        # True while a discovery pass is running; drives the placeholder
        # row shown when the table would otherwise be empty.
        self._searching = refresh_fn is not None or shallow_fn is not None
        self._probe_failed = False
        self._poll_timer = QTimer(self)          # dies with the dialog
        self._poll_timer.setInterval(_POLL_MS)
        self._poll_timer.timeout.connect(self._drain_probe_result)

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
        self.refresh_btn.setEnabled(refresh_fn is not None or shallow_fn is not None)

        self._devices: list[CameraInfo] = []
        self._apply_devices(devices)
        # Fill in USB cameras right away (background, non-blocking).
        if self._refresh_fn is not None or self._shallow_fn is not None:
            self.start_deep_refresh()

    # -- device table --------------------------------------------------------
    def _apply_devices(self, devices: list[CameraInfo]) -> None:
        if self._closed:
            return
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
        if not self._devices:
            self._show_placeholder_row()

    def _show_placeholder_row(self) -> None:
        """Empty table: an honest status, never an eternal 'Searching'."""
        if self._searching:
            name, status = "(searching)", _SEARCHING
        elif self._probe_failed:
            name, status = "(error)", _DISCOVERY_FAILED
        else:
            name, status = "(no camera)", _NO_CAMERAS
        self.table.setRowCount(1)
        for col, text in enumerate((name, "", "", status)):
            item = QTableWidgetItem(text)
            # Not selectable: Connect must stay disabled for placeholders.
            item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.table.setItem(0, col, item)

    @staticmethod
    def _interface_text(dev: CameraInfo) -> str:
        if dev.kind == "uvc" and dev.name.startswith("HIKMICRO"):
            return "HikCamera USB (UVC)"
        return {
            "simulated": "Simulated (development)",
            "uvc": "UVC / USB video",
            "hikmicro-sdk": "HIKMICRO SDK",
        }.get(dev.kind, dev.kind)

    # -- background discovery --------------------------------------------------
    def start_deep_refresh(self) -> None:
        """Run the full discovery sequence in the background: the shallow
        USB enumeration first (when available), then the deep HikCamera
        probe that refines the rows."""
        if ((self._refresh_fn is None and self._shallow_fn is None)
                or self._probe_thread is not None):
            return
        self.refresh_btn.setEnabled(False)
        # A fresh pass clears a cancellation from a previous one.
        self._probe_cancel.clear()
        self._probe_done.clear()
        self._probe_result = None
        self._searching = True
        self._probe_failed = False
        self._probe_stages = []
        if self._shallow_fn is not None:
            self._probe_stages.append(self._shallow_fn)
        if self._refresh_fn is not None:
            self._probe_stages.append(self._refresh_fn)
        # Mark generic UVC rows as being re-checked.
        self._mark_generic_rows_detecting()
        self._start_next_probe_stage()

    def _start_next_probe_stage(self) -> None:
        """Launch the next queued stage on its own background thread."""
        if not self._probe_stages:
            return
        self._current_stage = self._probe_stages.pop(0)
        self._probe_done.clear()
        self._probe_result = None
        self._probe_thread = threading.Thread(
            target=self._run_refresh, daemon=True, name="device-deep-discovery"
        )
        self._probe_thread.start()
        self._poll_timer.start()

    def _run_refresh(self) -> None:
        """Probe thread body - MUST NOT touch Qt (see the module docstring):
        write the result, set the done event, let the timer pick it up."""
        try:
            devices = self._current_stage(self._probe_cancel.is_set) or []
            self._probe_failed = False
        except Exception:
            devices = []
            self._probe_failed = True
        self._probe_result = list(devices)
        self._probe_done.set()

    def _drain_probe_result(self) -> None:
        """GUI thread: adopt the finished stage's result, then continue
        with the deep probe or finish the pass."""
        if not self._probe_done.is_set():
            return
        self._poll_timer.stop()
        result = self._probe_result
        self._probe_result = None
        self._probe_thread = None
        if self._closed:
            return
        if self._probe_stages:
            # Intermediate (shallow) result: show the rows now, the deep
            # probe is about to refine them.
            self._apply_devices(result or [])
            self._mark_generic_rows_detecting()
            self._start_next_probe_stage()
            return
        # Final stage finished - allow another refresh.
        self._searching = False
        self.refresh_btn.setEnabled(
            self._refresh_fn is not None or self._shallow_fn is not None
        )
        self._apply_devices(result or [])

    def _mark_generic_rows_detecting(self) -> None:
        for row, dev in enumerate(self._devices):
            if dev.kind == "uvc" and not dev.name.startswith("HIKMICRO"):
                self.table.setItem(row, 3, QTableWidgetItem(_DETECTING))

    def _stop_probe(self) -> None:
        """Ask the background discovery to abort; it finishes within one
        bounded native call. Safe to call more than once."""
        self._probe_cancel.set()

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
        self._stop_probe()
        super().reject()

    def accept(self) -> None:  # noqa: D102 - QDialog override
        self._closed = True
        # Cancel FIRST: the acquisition worker that follows must not wait
        # behind (or race) this dialog's still-running deep probe.
        self._stop_probe()
        device = self.current_device()
        if device is not None:
            self.selected = device
        super().accept()

    def closeEvent(self, event) -> None:  # noqa: N802 - QDialog override
        self._closed = True
        self._stop_probe()
        super().closeEvent(event)
