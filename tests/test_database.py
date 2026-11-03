"""Database: presets (immutable, soft delete/restore), snapshots, recovery."""
import json

import pytest

from app.database.database_manager import DatabaseManager
from app.thermal.region_calculator import RegionDefinition


def make_regions():
    return [
        RegionDefinition("A", 10, 10, 30, 30, "#ff0000", 35.0),
        RegionDefinition("B", 60, 40, 20, 20, "#00ff00", 40.0),
    ]


def make_preset(db, code="A1", version="1.00"):
    return db.create_preset(code, version, make_regions())


class TestPresets:
    def test_create_and_list(self, db):
        pid = make_preset(db)
        presets = db.list_presets()
        assert len(presets) == 1
        assert presets[0].id == pid
        assert presets[0].device_code == "A1"
        assert [r.label for r in presets[0].regions] == ["A", "B"]

    def test_requires_at_least_one_region(self, db):
        with pytest.raises(Exception):
            db.create_preset("A1", "1.00", [])

    def test_soft_delete_and_restore_preserves_id_and_regions(self, db):
        pid = make_preset(db)
        db.soft_delete_preset(pid)
        assert db.list_presets() == []
        db.restore_preset(pid)
        presets = db.list_presets()
        assert len(presets) == 1
        assert presets[0].id == pid  # original internal ID preserved
        assert [r.label for r in presets[0].regions] == ["A", "B"]
        assert presets[0].regions[0].max_allowed_delta_t == 35.0

    def test_no_update_api_exists_immutability(self, db):
        # Presets are immutable by design: no update method is exposed.
        for forbidden in ("update_preset", "update_region", "edit_preset"):
            assert not hasattr(db, forbidden)

    def test_copy_preset_creates_new_id(self, db):
        pid = make_preset(db, "A1", "1.00")
        new_id = db.copy_preset(pid, "A1", "1.01")
        assert new_id != pid
        original = db.get_preset(pid)
        copy = db.get_preset(new_id)
        assert original.version == "1.00"
        assert copy.version == "1.01"
        assert [r.label for r in copy.regions] == ["A", "B"]


class TestTestsAndSnapshots:
    def _create_test(self, db, status="COMPLETED"):
        from app.core.configuration import SettingsData
        from app.database.models import Preset

        pid = make_preset(db, "A1", "1.00")
        preset = db.get_preset(pid)
        settings = SettingsData().snapshot_dict()
        test_id = db.create_test("standard", preset, settings, status)
        sample_id = db.add_sample(test_id, 60.0, "2026-11-03T10:00:00+00:00", 25.0)
        db.add_region_measurement(sample_id, "A", 24.0, 58.0, 33.0, "PASS")
        db.add_region_measurement(sample_id, "B", 25.0, 60.0, 35.0, "PASS")
        db.complete_test(test_id, "PASS", "COMPLETED", None)
        return test_id, pid

    def test_snapshot_preserved_after_preset_deletion(self, db):
        test_id, pid = self._create_test(db)
        db.soft_delete_preset(pid)
        details = db.get_test_details(test_id)
        assert details is not None
        snapshot = details["preset_snapshot"]
        assert snapshot["device_code"] == "A1"
        assert len(snapshot["regions"]) == 2
        record = details["record"]
        assert record.region_count == 2
        assert record.result_display == "PASS"

    def test_list_filters(self, db):
        self._create_test(db)
        self._create_test(db)
        assert len(db.list_tests()) == 2
        assert len(db.list_tests(test_type="standard")) == 2
        assert len(db.list_tests(test_type="quick")) == 0
        assert len(db.list_tests(result="PASS")) == 2
        assert len(db.list_tests(search="A1")) == 2
        assert len(db.list_tests(search="ZZZ")) == 0

    def test_delete_tests_cascades(self, db):
        test_id, _ = self._create_test(db)
        db.delete_tests([test_id])
        assert db.list_tests() == []
        assert db.get_test_details(test_id) is None

    def test_delete_is_permanent_for_tests_only(self, db):
        test_id, pid = self._create_test(db)
        db.delete_tests([test_id])
        # Preset itself is untouched (soft-delete domain).
        assert db.get_preset(pid) is not None


class TestCrashRecovery:
    def test_unfinished_tests_marked_incomplete(self, db):
        from app.core.configuration import SettingsData
        from app.database.models import Preset

        pid = make_preset(db)
        preset = db.get_preset(pid)
        for status in ("RUNNING", "WAITING", "MEASURING"):
            db.create_test("standard", preset, SettingsData().snapshot_dict(), status)
        count = db.recover_unfinished_tests()
        assert count == 3
        for record in db.list_tests():
            assert record.status == "INCOMPLETE"
            assert record.failure_reason == "Unexpected Application Termination"

    def test_finished_tests_untouched(self, db):
        from app.core.configuration import SettingsData
        from app.database.models import Preset

        pid = make_preset(db)
        preset = db.get_preset(pid)
        tid = db.create_test("quick", preset, SettingsData().snapshot_dict(), "COMPLETED")
        db.complete_test(tid, "PASS", "COMPLETED", None)
        assert db.recover_unfinished_tests() == 0


class TestIntegrity:
    def test_integrity_check_ok(self, db):
        ok, message = db.integrity_check()
        assert ok and message == "ok"

    def test_migrations_idempotent(self, tmp_path):
        path = str(tmp_path / "x.db")
        db1 = DatabaseManager(path)
        db1.close()
        db2 = DatabaseManager(path)  # runs migrations again on existing file
        ok, _ = db2.integrity_check()
        assert ok
        db2.close()
