"""SQLite schema creation and safe migrations.

Schema versioning uses PRAGMA user_version. Every new version appends an
idempotent migration step so existing databases upgrade in place, inside
a transaction.
"""
from __future__ import annotations

import sqlite3

SCHEMA_VERSION = 1

_SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS presets (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    device_code   TEXT    NOT NULL,
    version       TEXT    NOT NULL,
    created_at    TEXT    NOT NULL,
    is_deleted    INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS preset_regions (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    preset_id          INTEGER NOT NULL REFERENCES presets(id),
    region_label       TEXT    NOT NULL,
    x                  INTEGER NOT NULL,
    y                  INTEGER NOT NULL,
    width              INTEGER NOT NULL,
    height             INTEGER NOT NULL,
    color              TEXT    NOT NULL,
    max_allowed_delta_t REAL   NOT NULL,
    sort_order         INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS tests (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    test_type            TEXT    NOT NULL,           -- standard | quick
    started_at           TEXT    NOT NULL,           -- UTC ISO
    completed_at         TEXT,                       -- UTC ISO
    preset_id            INTEGER REFERENCES presets(id),
    preset_snapshot_json TEXT    NOT NULL,           -- authoritative history
    settings_snapshot_json TEXT  NOT NULL,
    device_code          TEXT    NOT NULL,           -- denormalized from snapshot for filtering
    version              TEXT    NOT NULL,
    overall_result       TEXT,                       -- PASS | FAIL | NULL
    status               TEXT    NOT NULL,           -- RUNNING/WAITING | COMPLETED | TIMEOUT | INCOMPLETE
    failure_reason       TEXT,
    reference_timestamp  TEXT                        -- UTC ISO of the rise trigger
);

CREATE TABLE IF NOT EXISTS test_samples (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    test_id             INTEGER NOT NULL REFERENCES tests(id) ON DELETE CASCADE,
    sample_time_seconds REAL    NOT NULL,
    timestamp           TEXT    NOT NULL,
    ambient_temperature REAL    NOT NULL
);

CREATE TABLE IF NOT EXISTS region_measurements (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    sample_id       INTEGER NOT NULL REFERENCES test_samples(id) ON DELETE CASCADE,
    region_label    TEXT    NOT NULL,
    min_temperature REAL,
    max_temperature REAL,
    delta_t         REAL,
    region_status   TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_preset_regions_preset ON preset_regions(preset_id);
CREATE INDEX IF NOT EXISTS idx_tests_started         ON tests(started_at);
CREATE INDEX IF NOT EXISTS idx_tests_preset          ON tests(preset_id);
CREATE INDEX IF NOT EXISTS idx_samples_test          ON test_samples(test_id);
CREATE INDEX IF NOT EXISTS idx_measurements_sample   ON region_measurements(sample_id);
"""


def run_migrations(conn: sqlite3.Connection) -> None:
    """Bring the database up to SCHEMA_VERSION, one transaction per step."""
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    if current >= SCHEMA_VERSION:
        return
    if current < 1:
        conn.executescript(_SCHEMA_V1)
        conn.execute("PRAGMA user_version = 1")
        conn.commit()
