"""ApplicationServices: wires paths, logging, config, database, backup, camera.

Created once by main.py. The database health check and crash recovery
run here; interactive restore dialogs are orchestrated by the UI layer.
"""
from __future__ import annotations

import os

from app.backup.backup_manager import BackupManager
from app.camera.camera_manager import CameraManager
from app.core.configuration import SettingsData, load_settings, save_settings
from app.core.constants import (
    APP_NAME,
    ensure_data_dirs,
)
from app.database.database_manager import DatabaseManager
from app.logging.log_manager import get_logger, setup_logging
from app.reports.report_manager import ReportManager


class ApplicationServices:
    """Owns every long-lived service instance (no Qt widgets here)."""

    def __init__(self, data_dir: str | None = None, attach_simulated: bool = False) -> None:
        self.data_dir = data_dir or os.environ.get("DRIVERTHERMALQC_DATA_DIR") or ""
        if not self.data_dir:
            from app.core.constants import default_data_dir
            self.data_dir = default_data_dir()
        ensure_data_dirs(self.data_dir)

        self.logs_dir = os.path.join(self.data_dir, "logs")
        self.settings_dir = os.path.join(self.data_dir, "settings")
        self.exports_dir = os.path.join(self.data_dir, "exports")
        self.backups_dir = os.path.join(self.data_dir, "database", "backups")
        self.db_path = os.path.join(self.data_dir, "database", "thermal_qc.db")

        self.logger = setup_logging(self.logs_dir)
        self.logger.info("%s starting - data dir: %s", APP_NAME, self.data_dir)

        self.settings = load_settings(self.data_dir)
        self.db = DatabaseManager(self.db_path)
        self.backup = BackupManager(self.db_path, self.backups_dir)
        self.reports = ReportManager(self.db)
        self.camera = CameraManager(lambda: self.settings)

        recovered = self.db.recover_unfinished_tests()
        if recovered:
            self.logger.warning("Crash recovery: %d unfinished test(s) marked INCOMPLETE", recovered)

        self._shutdown_done = False
        if attach_simulated:
            from app.camera.simulated_camera import SimulatedCamera
            self.camera.attach(SimulatedCamera())

    # -- settings ------------------------------------------------------------
    def save_settings(self, settings: SettingsData) -> None:
        save_settings(self.data_dir, settings)
        self.settings = settings
        self.camera.set_emissivity(settings.emissivity)
        self.logger.info(
            "Settings applied: rise=%.1fC wait=%.0fs interval=%ds reconnect=%.0fs emissivity=%.2f",
            settings.temperature_rise_threshold, settings.waiting_timeout,
            settings.sample_interval, settings.camera_reconnection_timeout,
            settings.emissivity,
        )

    # -- shutdown --------------------------------------------------------------
    def shutdown(self, normal: bool = True) -> None:
        """Stop the camera, back up the database (normal exit), close DB."""
        if self._shutdown_done:
            return
        self._shutdown_done = True
        try:
            self.camera.detach()
        except Exception as exc:  # pragma: no cover - defensive
            self.logger.error("Camera detach failed: %s", exc)
        if normal:
            try:
                self.backup.create_backup()
            except Exception as exc:
                self.logger.error("Exit backup failed: %s", exc)
        try:
            self.db.close()
        except Exception as exc:  # pragma: no cover - defensive
            self.logger.error("Database close failed: %s", exc)
        self.logger.info("%s shutdown complete (normal=%s)", APP_NAME, normal)
