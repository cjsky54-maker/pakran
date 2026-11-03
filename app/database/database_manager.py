"""SQLite persistence for presets, tests, samples and measurements.

Design notes:
- WAL journal mode + foreign keys + a process-wide lock; all writes run
  in explicit transactions so a crash never leaves partial samples.
- Presets are IMMUTABLE: no update API exists. Only soft delete/restore.
- Tests carry full preset/settings JSON snapshots, so history stays
  intact even after a preset is deleted and restored.
- Crash recovery: ``recover_unfinished_tests`` marks interrupted tests.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
from typing import Any, Optional, Sequence

from app.core.constants import REASON_CRASH, STATUS_INCOMPLETE
from app.core.jalali import utc_now_iso
from app.database.migrations import run_migrations
from app.database.models import (
    Preset,
    RegionMeasurementData,
    TestRecord,
    TestSampleData,
)
from app.thermal.region_calculator import RegionDefinition

logger = logging.getLogger("DriverThermalQC.database")

# Non-terminal test statuses that require crash recovery on startup.
_ACTIVE_STATUSES = ("RUNNING", "WAITING", "MEASURING")


class DatabaseError(Exception):
    """Wrapped sqlite failure with a user-readable message."""


def _preset_snapshot(preset: Preset) -> dict:
    return {
        "preset_id": preset.id,
        "device_code": preset.device_code,
        "version": preset.version,
        "created_at": preset.created_at,
        "regions": [r.to_dict() for r in preset.regions],
    }


class DatabaseManager:
    """Thread-safe SQLite access layer (one writer, GUI thread + workers)."""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.execute("PRAGMA synchronous=FULL")
            run_migrations(self._conn)
        logger.info("Database ready: %s", db_path)

    # -- integrity / recovery ---------------------------------------------
    def integrity_check(self) -> tuple[bool, str]:
        try:
            with self._lock:
                row = self._conn.execute("PRAGMA integrity_check").fetchone()
            return row[0] == "ok", str(row[0])
        except sqlite3.Error as exc:
            return False, str(exc)

    def recover_unfinished_tests(self, reason: str = REASON_CRASH) -> int:
        """Mark tests left RUNNING/WAITING/MEASURING by a crash as INCOMPLETE."""
        with self._lock:
            placeholders = ",".join("?" for _ in _ACTIVE_STATUSES)
            rows = self._conn.execute(
                f"SELECT id FROM tests WHERE status IN ({placeholders})",
                _ACTIVE_STATUSES,
            ).fetchall()
            ids = [r["id"] for r in rows]
            if not ids:
                return 0
            now = utc_now_iso()
            self._conn.executemany(
                "UPDATE tests SET status=?, overall_result=NULL, "
                "failure_reason=?, completed_at=? WHERE id=?",
                [(STATUS_INCOMPLETE, reason, now, i) for i in ids],
            )
            self._conn.commit()
        for i in ids:
            logger.warning("Test %d recovered as INCOMPLETE (%s)", i, reason)
        return len(ids)

    # -- presets -------------------------------------------------------------
    def create_preset(
        self, device_code: str, version: str, regions: Sequence[RegionDefinition]
    ) -> int:
        if not regions:
            raise DatabaseError("A preset requires at least one region")
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO presets(device_code, version, created_at, is_deleted) "
                "VALUES(?,?,?,0)",
                (device_code.strip(), version.strip(), utc_now_iso()),
            )
            preset_id = int(cur.lastrowid)
            self._insert_regions(preset_id, regions)
            self._conn.commit()
        logger.info("Preset %d created (%s / %s, %d regions)",
                    preset_id, device_code, version, len(regions))
        return preset_id

    def _insert_regions(self, preset_id: int, regions: Sequence[RegionDefinition]) -> None:
        self._conn.executemany(
            "INSERT INTO preset_regions(preset_id, region_label, x, y, width, height,"
            " color, max_allowed_delta_t, sort_order) VALUES(?,?,?,?,?,?,?,?,?)",
            [
                (preset_id, r.label, r.x, r.y, r.width, r.height,
                 r.color, r.max_allowed_delta_t, order)
                for order, r in enumerate(regions)
            ],
        )

    def get_preset(self, preset_id: int) -> Optional[Preset]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM presets WHERE id=?", (preset_id,)
            ).fetchone()
            if row is None:
                return None
            regions = [
                RegionDefinition.from_dict({
                    "label": r["region_label"], "x": r["x"], "y": r["y"],
                    "width": r["width"], "height": r["height"], "color": r["color"],
                    "max_allowed_delta_t": r["max_allowed_delta_t"],
                })
                for r in self._conn.execute(
                    "SELECT * FROM preset_regions WHERE preset_id=? ORDER BY sort_order, id",
                    (preset_id,),
                )
            ]
            return Preset(
                id=row["id"], device_code=row["device_code"], version=row["version"],
                created_at=row["created_at"], is_deleted=bool(row["is_deleted"]),
                regions=regions,
            )

    def list_presets(self, include_deleted: bool = False) -> list[Preset]:
        query = "SELECT id FROM presets"
        if not include_deleted:
            query += " WHERE is_deleted=0"
        query += " ORDER BY created_at DESC, id DESC"
        with self._lock:
            ids = [r["id"] for r in self._conn.execute(query)]
            presets = [p for p in (self.get_preset(i) for i in ids) if p is not None]
        return presets

    def soft_delete_preset(self, preset_id: int) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE presets SET is_deleted=1 WHERE id=?", (preset_id,)
            )
            self._conn.commit()
        logger.info("Preset %d soft-deleted", preset_id)

    def restore_preset(self, preset_id: int) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE presets SET is_deleted=0 WHERE id=?", (preset_id,)
            )
            self._conn.commit()
        logger.info("Preset %d restored (original id preserved)", preset_id)

    def copy_preset(
        self,
        preset_id: int,
        new_device_code: str,
        new_version: str,
        regions: Optional[Sequence[RegionDefinition]] = None,
    ) -> int:
        """Duplicate a preset; optionally override region geometry/limits."""
        source = self.get_preset(preset_id)
        if source is None:
            raise DatabaseError(f"Preset {preset_id} not found")
        return self.create_preset(
            new_device_code, new_version, regions if regions is not None else source.regions
        )

    # -- tests -----------------------------------------------------------------
    def create_test(
        self,
        test_type: str,
        preset: Preset,
        settings_snapshot: dict,
        status: str,
    ) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO tests(test_type, started_at, preset_id, preset_snapshot_json,"
                " settings_snapshot_json, device_code, version, overall_result, status)"
                " VALUES(?,?,?,?,?,?,?,NULL,?)",
                (
                    test_type, utc_now_iso(), preset.id,
                    json.dumps(_preset_snapshot(preset)),
                    json.dumps(settings_snapshot),
                    preset.device_code, preset.version, status,
                ),
            )
            self._conn.commit()
            test_id = int(cur.lastrowid)
        logger.info("Test %d created (type=%s preset=%s)", test_id, test_type, preset.id)
        return test_id

    def set_reference_timestamp(self, test_id: int, iso_value: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE tests SET reference_timestamp=? WHERE id=?", (iso_value, test_id)
            )
            self._conn.commit()

    def add_sample(
        self, test_id: int, sample_time_seconds: float, timestamp: str, ambient: float
    ) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO test_samples(test_id, sample_time_seconds, timestamp,"
                " ambient_temperature) VALUES(?,?,?,?)",
                (test_id, float(sample_time_seconds), timestamp, float(ambient)),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def add_region_measurement(
        self,
        sample_id: int,
        region_label: str,
        min_temperature: Optional[float],
        max_temperature: Optional[float],
        delta_t: Optional[float],
        region_status: str,
    ) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO region_measurements(sample_id, region_label, min_temperature,"
                " max_temperature, delta_t, region_status) VALUES(?,?,?,?,?,?)",
                (sample_id, region_label, min_temperature, max_temperature,
                 delta_t, region_status),
            )
            self._conn.commit()

    def complete_test(
        self,
        test_id: int,
        overall_result: Optional[str],
        status: str,
        failure_reason: Optional[str],
    ) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE tests SET overall_result=?, status=?, failure_reason=?,"
                " completed_at=? WHERE id=?",
                (overall_result, status, failure_reason, utc_now_iso(), test_id),
            )
            self._conn.commit()
        logger.info("Test %d finished: status=%s result=%s reason=%s",
                    test_id, status, overall_result, failure_reason)

    # -- queries ------------------------------------------------------------
    def _row_to_record(self, row: sqlite3.Row) -> TestRecord:
        snapshot = json.loads(row["preset_snapshot_json"])
        regions = snapshot.get("regions", [])
        return TestRecord(
            id=row["id"],
            test_type=row["test_type"],
            started_at=row["started_at"],
            completed_at=row["completed_at"],
            preset_id=row["preset_id"],
            device_code=row["device_code"],
            version=row["version"],
            overall_result=row["overall_result"],
            status=row["status"],
            failure_reason=row["failure_reason"],
            reference_timestamp=row["reference_timestamp"],
            region_count=len(regions),
        )

    def list_tests(
        self,
        test_type: Optional[str] = None,
        result: Optional[str] = None,
        preset_id: Optional[int] = None,
        date_from_utc: Optional[str] = None,
        date_to_utc: Optional[str] = None,
        search: Optional[str] = None,
    ) -> list[TestRecord]:
        clauses, params = [], []
        if test_type:
            clauses.append("test_type=?")
            params.append(test_type)
        if result == "PASS":
            clauses.append("overall_result='PASS'")
        elif result == "FAIL":
            clauses.append("overall_result='FAIL'")
        elif result in ("TIMEOUT", "INCOMPLETE"):
            clauses.append("status=?")
            params.append(result)
        if preset_id is not None:
            clauses.append("preset_id=?")
            params.append(preset_id)
        if date_from_utc:
            clauses.append("started_at>=?")
            params.append(date_from_utc)
        if date_to_utc:
            clauses.append("started_at<?")
            params.append(date_to_utc)
        if search:
            like = f"%{search.strip()}%"
            clauses.append("(device_code LIKE ? OR version LIKE ? OR CAST(id AS TEXT) LIKE ?)")
            params.extend([like, like, like])
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        query = f"SELECT * FROM tests {where} ORDER BY started_at DESC, id DESC"
        with self._lock:
            rows = self._conn.execute(query, params).fetchall()
        return [self._row_to_record(r) for r in rows]

    def get_test_overview(self, test_id: int) -> Optional[TestRecord]:
        with self._lock:
            row = self._conn.execute("SELECT * FROM tests WHERE id=?", (test_id,)).fetchone()
        return self._row_to_record(row) if row else None

    def get_test_details(self, test_id: int) -> Optional[dict]:
        """Full test data: record + preset/settings snapshots + all samples."""
        with self._lock:
            row = self._conn.execute("SELECT * FROM tests WHERE id=?", (test_id,)).fetchone()
            if row is None:
                return None
            record = self._row_to_record(row)
            sample_rows = self._conn.execute(
                "SELECT * FROM test_samples WHERE test_id=? ORDER BY sample_time_seconds",
                (test_id,),
            ).fetchall()
            samples: list[TestSampleData] = []
            for sr in sample_rows:
                measurements = [
                    RegionMeasurementData(
                        region_label=mr["region_label"],
                        min_temperature=mr["min_temperature"],
                        max_temperature=mr["max_temperature"],
                        delta_t=mr["delta_t"],
                        region_status=mr["region_status"],
                    )
                    for mr in self._conn.execute(
                        "SELECT * FROM region_measurements WHERE sample_id=?"
                        " ORDER BY region_label",
                        (sr["id"],),
                    )
                ]
                samples.append(
                    TestSampleData(
                        id=sr["id"],
                        sample_time_seconds=sr["sample_time_seconds"],
                        timestamp=sr["timestamp"],
                        ambient_temperature=sr["ambient_temperature"],
                        measurements=measurements,
                    )
                )
        return {
            "record": record,
            "preset_snapshot": json.loads(row["preset_snapshot_json"]),
            "settings_snapshot": json.loads(row["settings_snapshot_json"]),
            "samples": samples,
        }

    def delete_tests(self, test_ids: Sequence[int]) -> int:
        """Permanently delete tests and all associated measurements."""
        if not test_ids:
            return 0
        with self._lock:
            placeholders = ",".join("?" for _ in test_ids)
            self._conn.execute(
                f"DELETE FROM region_measurements WHERE sample_id IN "
                f"(SELECT id FROM test_samples WHERE test_id IN ({placeholders}))",
                tuple(test_ids),
            )
            self._conn.execute(
                f"DELETE FROM test_samples WHERE test_id IN ({placeholders})",
                tuple(test_ids),
            )
            cur = self._conn.execute(
                f"DELETE FROM tests WHERE id IN ({placeholders})", tuple(test_ids)
            )
            self._conn.commit()
        deleted = cur.rowcount if cur.rowcount and cur.rowcount > 0 else len(test_ids)
        logger.info("Deleted %d test records (permanent): %s", deleted, list(test_ids))
        return deleted

    # -- lifecycle -----------------------------------------------------------
    def close(self) -> None:
        with self._lock:
            try:
                self._conn.commit()
                self._conn.close()
            except sqlite3.Error:
                pass
        logger.info("Database connection closed")
