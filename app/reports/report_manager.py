"""Report queries (filters, sorting inputs) on top of DatabaseManager."""
from __future__ import annotations

from typing import Optional

from app.core.jalali import jalali_day_bounds_utc
from app.database.database_manager import DatabaseManager
from app.database.models import TestRecord


class ReportManager:
    """High-level report queries used by the Reports page."""

    def __init__(self, db: DatabaseManager) -> None:
        self._db = db

    def query(
        self,
        test_type: Optional[str] = None,
        result: Optional[str] = None,
        preset_id: Optional[int] = None,
        jalali_date_from: Optional[str] = None,
        jalali_date_to: Optional[str] = None,
        search: Optional[str] = None,
    ) -> list[TestRecord]:
        date_from_utc = date_to_utc = None
        if jalali_date_from:
            bounds = jalali_day_bounds_utc(jalali_date_from)
            if bounds:
                date_from_utc = bounds[0]
        if jalali_date_to:
            bounds = jalali_day_bounds_utc(jalali_date_to)
            if bounds:
                date_to_utc = bounds[1]
        return self._db.list_tests(
            test_type=test_type,
            result=result,
            preset_id=preset_id,
            date_from_utc=date_from_utc,
            date_to_utc=date_to_utc,
            search=search,
        )

    def get_details(self, test_id: int) -> Optional[dict]:
        return self._db.get_test_details(test_id)

    def delete_tests(self, test_ids: list[int]) -> int:
        return self._db.delete_tests(test_ids)
