"""Backup creation, validation and restore (spec items 14, 15)."""
import os

import pytest

from app.backup.backup_manager import BackupError, BackupManager
from app.core.configuration import SettingsData
from app.database.database_manager import DatabaseManager
from app.thermal.region_calculator import RegionDefinition


def _populate(db):
    pid = db.create_preset("A1", "1.00", [
        RegionDefinition("A", 0, 0, 10, 10, "#111111", 10.0),
    ])
    preset = db.get_preset(pid)
    tid = db.create_test("quick", preset, SettingsData().snapshot_dict(), "COMPLETED")
    db.complete_test(tid, "PASS", "COMPLETED", None)
    return pid, tid


def test_create_backup_keeps_latest_only(db, tmp_path):
    _populate(db)
    backups = tmp_path / "backups"
    manager = BackupManager(db.db_path, str(backups))
    path = manager.create_backup()
    assert os.path.isfile(path)
    # Second backup replaces (only the latest is kept).
    manager.create_backup()
    files = [f for f in os.listdir(backups) if f.endswith(".db")]
    assert files == ["thermal_qc_backup.db"]


def test_backup_validity_check(db, tmp_path):
    _populate(db)
    manager = BackupManager(db.db_path, str(tmp_path / "backups"))
    assert not manager.has_backup()
    manager.create_backup()
    assert manager.backup_is_valid()


def test_restore_recovers_data_and_preserves_corrupt(db, tmp_path):
    db_path = str(tmp_path / "main.db")
    db = DatabaseManager(db_path)
    pid, tid = _populate(db)
    manager = BackupManager(db_path, str(tmp_path / "backups"))
    manager.create_backup()
    db.close()

    # Corrupt the live database (simulating an unexpected shutdown).
    with open(db_path, "r+b") as fh:
        fh.seek(0)
        fh.write(b"CORRUPTED DATA" * 100)

    report = manager.restore_backup()
    assert report["validated"] is True
    assert report["preserved_as"] is not None
    assert os.path.isfile(report["preserved_as"])

    db2 = DatabaseManager(db_path)
    ok, _ = db2.integrity_check()
    assert ok
    assert db2.list_presets()[0].id == pid
    assert db2.list_tests()[0].id == tid
    db2.close()


def test_restore_rejects_invalid_backup(db, tmp_path):
    _populate(db)
    backups = tmp_path / "backups"
    manager = BackupManager(db.db_path, str(backups))
    manager.create_backup()
    # Corrupt the backup itself.
    with open(manager.backup_path, "r+b") as fh:
        fh.seek(0)
        fh.write(b"GARBAGE" * 500)
    assert not manager.backup_is_valid()
    with pytest.raises(BackupError):
        manager.restore_backup()


def test_restore_without_backup_raises(db, tmp_path):
    manager = BackupManager(str(tmp_path / "missing.db"), str(tmp_path / "backups"))
    with pytest.raises(BackupError):
        manager.restore_backup()


def test_backup_failure_on_missing_database(tmp_path):
    manager = BackupManager(str(tmp_path / "nope.db"), str(tmp_path / "backups"))
    with pytest.raises(BackupError):
        manager.create_backup()
