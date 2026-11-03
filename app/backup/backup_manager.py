"""Database backup and restore with atomic file operations.

Policy:
- A backup is created automatically on normal application exit.
- Only the latest backup is kept (database/backups/thermal_qc_backup.db).
- Restoration is explicit: the user is informed and confirms; the
  current (possibly corrupt) database is preserved before replacing it.
"""
from __future__ import annotations

import logging
import os
import shutil
import sqlite3
import tempfile
from datetime import datetime

logger = logging.getLogger("DriverThermalQC.backup")

BACKUP_FILENAME = "thermal_qc_backup.db"


class BackupError(Exception):
    """Backup/restore failure with a user-readable message."""


def _integrity_ok(db_path: str) -> bool:
    try:
        conn = sqlite3.connect(db_path)
        try:
            row = conn.execute("PRAGMA integrity_check").fetchone()
            return bool(row) and row[0] == "ok"
        finally:
            conn.close()
    except sqlite3.Error:
        return False


class BackupManager:
    def __init__(self, db_path: str, backup_dir: str) -> None:
        self.db_path = db_path
        self.backup_dir = backup_dir
        self.backup_path = os.path.join(backup_dir, BACKUP_FILENAME)

    # -- backup -----------------------------------------------------------
    def create_backup(self) -> str:
        """Atomically copy the live database over the single kept backup."""
        if not os.path.isfile(self.db_path):
            raise BackupError("Database file not found; nothing to back up")
        os.makedirs(self.backup_dir, exist_ok=True)
        # Flush the WAL into the main file so the copy is complete.
        try:
            conn = sqlite3.connect(self.db_path)
            try:
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            finally:
                conn.close()
        except sqlite3.Error as exc:
            raise BackupError(f"Could not prepare database for backup: {exc}")
        fd, tmp_path = tempfile.mkstemp(dir=self.backup_dir, suffix=".tmp")
        os.close(fd)
        try:
            shutil.copy2(self.db_path, tmp_path)
            if not _integrity_ok(tmp_path):
                raise BackupError("Backup failed integrity validation; backup not replaced")
            os.replace(tmp_path, self.backup_path)
        except (OSError, BackupError):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
        logger.info("Database backup created: %s", self.backup_path)
        return self.backup_path

    # -- restore ------------------------------------------------------------
    def has_backup(self) -> bool:
        return os.path.isfile(self.backup_path)

    def backup_is_valid(self) -> bool:
        return self.has_backup() and _integrity_ok(self.backup_path)

    def restore_backup(self) -> dict:
        """Restore the verified backup, preserving the current file first."""
        report = {"preserved_as": None, "restored_from": self.backup_path,
                  "validated": False}
        if not self.has_backup():
            raise BackupError("No backup file exists")
        if not _integrity_ok(self.backup_path):
            raise BackupError("The backup file failed integrity validation")
        if os.path.isfile(self.db_path):
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            preserved = os.path.join(self.backup_dir, f"corrupted_{stamp}.db")
            shutil.copy2(self.db_path, preserved)
            report["preserved_as"] = preserved
            logger.info("Corrupt database preserved as %s", preserved)
        fd, tmp_path = tempfile.mkstemp(dir=self.backup_dir, suffix=".tmp")
        os.close(fd)
        try:
            shutil.copy2(self.backup_path, tmp_path)
            os.replace(tmp_path, self.db_path)
        except OSError:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise BackupError("Could not replace the database file")
        if not _integrity_ok(self.db_path):
            raise BackupError("Restored database failed integrity validation")
        report["validated"] = True
        logger.info("Database restored from backup and validated")
        return report
