"""Row-level data models shared between the database and the UI."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from app.thermal.region_calculator import RegionDefinition


@dataclass
class Preset:
    """A saved, immutable preset (soft-deleted, never edited in place)."""

    id: Optional[int]
    device_code: str
    version: str
    created_at: str                      # UTC ISO
    is_deleted: bool = False
    regions: list[RegionDefinition] = field(default_factory=list)

    @property
    def display_name(self) -> str:
        return f"{self.device_code} - {self.version}"


@dataclass
class RegionMeasurementData:
    region_label: str
    min_temperature: Optional[float]
    max_temperature: Optional[float]
    delta_t: Optional[float]
    region_status: str


@dataclass
class TestSampleData:
    id: Optional[int]
    sample_time_seconds: float
    timestamp: str                       # UTC ISO
    ambient_temperature: float
    measurements: list[RegionMeasurementData] = field(default_factory=list)


@dataclass
class TestRecord:
    """One test row (list view); codes come from the stored snapshot."""

    id: int
    test_type: str
    started_at: str
    completed_at: Optional[str]
    preset_id: Optional[int]
    device_code: str
    version: str
    overall_result: Optional[str]        # PASS / FAIL (None when not decided)
    status: str                          # PASS/FAIL come via result; TIMEOUT/INCOMPLETE via status
    failure_reason: Optional[str]
    reference_timestamp: Optional[str]
    region_count: int = 0

    @property
    def duration_s(self) -> Optional[float]:
        if not self.completed_at:
            return None
        from app.core.jalali import parse_iso
        try:
            return (parse_iso(self.completed_at) - parse_iso(self.started_at)).total_seconds()
        except (ValueError, TypeError):
            return None

    @property
    def result_display(self) -> str:
        if self.overall_result:
            return self.overall_result
        return self.status
