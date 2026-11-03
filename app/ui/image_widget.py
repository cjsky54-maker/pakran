"""Thermal image display widget with region overlays and hover readout.

Fixed display size (512x384, 2x of the 256x192 sensor). No zoom/pan in
v1. Region outlines always keep their assigned preset colors - they are
never recolored by PASS/FAIL status.
"""
from __future__ import annotations

from typing import Optional

import cv2
import numpy as np
from PySide6.QtCore import QPoint, QRect, Qt, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPen
from PySide6.QtWidgets import QWidget

from app.core.constants import DISPLAY_SCALE, SENSOR_HEIGHT, SENSOR_WIDTH
from app.thermal.region_calculator import RegionDefinition
from app.thermal.thermal_frame import ThermalFrame


def frame_to_qimage(frame: ThermalFrame) -> Optional[QImage]:
    """Render a frame for display: matrix -> auto-scaled INFERNO palette."""
    if frame.matrix is not None:
        matrix = np.asarray(frame.matrix, dtype=np.float32)
        lo, hi = float(matrix.min()), float(matrix.max())
        span = (hi - lo) or 1.0
        norm = ((matrix - lo) / span * 255.0).astype(np.uint8)
        bgr = cv2.applyColorMap(norm, cv2.COLORMAP_INFERNO)
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        h, w, ch = rgb.shape
        return QImage(rgb.data, w, h, w * ch, QImage.Format.Format_RGB888).copy()
    if frame.image is not None:
        rgb = np.ascontiguousarray(frame.image, dtype=np.uint8)
        h, w, ch = rgb.shape
        return QImage(rgb.data, w, h, w * ch, QImage.Format.Format_RGB888).copy()
    return None


class ThermalImageWidget(QWidget):
    """Live thermal image with overlays; emits hover temperature info."""

    hoverInfo = Signal(dict)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        from app.core.constants import DISPLAY_WIDTH, DISPLAY_HEIGHT
        self.setFixedSize(DISPLAY_WIDTH, DISPLAY_HEIGHT)
        self.setMouseTracking(True)
        self._qimage: Optional[QImage] = None
        self._regions: list[RegionDefinition] = []
        self._frame: Optional[ThermalFrame] = None
        self._drag_rect: Optional[QRect] = None

    # -- public API ---------------------------------------------------------
    def set_frame(self, frame: ThermalFrame) -> None:
        self._frame = frame
        self._qimage = frame_to_qimage(frame)
        self.update()

    def set_regions(self, regions: list[RegionDefinition]) -> None:
        self._regions = list(regions)
        self.update()

    def current_frame(self) -> Optional[ThermalFrame]:
        return self._frame

    # -- painting --------------------------------------------------------------
    def paintEvent(self, event) -> None:  # noqa: N802 (Qt API)
        from PySide6.QtGui import QPainter
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#101216"))
        if self._qimage is not None:
            painter.drawImage(0, 0, self._qimage)
        else:
            painter.setPen(QColor("#5a616e"))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                             "No camera image")
        scale = DISPLAY_SCALE
        label_font = QFont("Segoe UI", 10)
        label_font.setBold(True)
        painter.setFont(label_font)
        for region in self._regions:
            color = QColor(region.color)
            pen = QPen(color, 2)
            painter.setPen(pen)
            rect = QRect(region.x * scale, region.y * scale,
                         region.width * scale, region.height * scale)
            painter.drawRect(rect)
            # Letter badge (top-left, outside when possible)
            badge = QRect(rect.left(), max(0, rect.top() - 16), 18, 16)
            painter.fillRect(badge, color)
            painter.setPen(QColor("white"))
            painter.drawText(badge, Qt.AlignmentFlag.AlignCenter, region.label)
        if self._drag_rect is not None:
            painter.setPen(QPen(QColor("#ffffff"), 1, Qt.PenStyle.DashLine))
            painter.drawRect(self._drag_rect)

    # -- hover temperature readout ------------------------------------------------
    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        pos = event.position().toPoint()
        sx = int(pos.x() // DISPLAY_SCALE)
        sy = int(pos.y() // DISPLAY_SCALE)
        temp = None
        if (
            self._frame is not None
            and self._frame.is_radiometric
            and 0 <= sx < SENSOR_WIDTH
            and 0 <= sy < SENSOR_HEIGHT
        ):
            temp = float(self._frame.matrix[sy, sx])
        self.hoverInfo.emit({"x": sx, "y": sy, "temp": temp})
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self.hoverInfo.emit({"x": None, "y": None, "temp": None})
        super().leaveEvent(event)

    # -- region drawing support (used by the preset editor canvas) -----------------
    def _set_drag_rect(self, rect: Optional[QRect]) -> None:
        self._drag_rect = rect
        self.update()

    def sensor_point(self, pos: QPoint) -> tuple[int, int]:
        return int(pos.x() // DISPLAY_SCALE), int(pos.y() // DISPLAY_SCALE)
