"""Jalali calendar conversion and formatting."""
from datetime import date, datetime

from app.core.jalali import (
    display_datetime,
    format_jalali_date,
    gregorian_to_jalali,
    jalali_day_bounds_utc,
    jalali_to_gregorian,
    parse_iso,
    parse_jalali_date,
)


class TestConversions:
    def test_known_dates(self):
        # 2026-11-03 is 1405/08/12 in the Solar Hijri calendar.
        assert gregorian_to_jalali(2026, 11, 3) == (1405, 8, 12)
        assert gregorian_to_jalali(2026, 11, 2) == (1405, 8, 11)
        # Calendar epoch: 2026-03-21 = 1405/01/01.
        assert gregorian_to_jalali(2026, 3, 21) == (1405, 1, 1)
        # Year end: 1404/12/29 = 2026-03-20.
        assert gregorian_to_jalali(2026, 3, 20) == (1404, 12, 29)

    def test_inverse_roundtrip(self):
        for d in (date(2024, 2, 29), date(2025, 6, 15), date(2026, 11, 3),
                  date(2027, 1, 1), date(2020, 3, 20), date(2030, 9, 22)):
            j = gregorian_to_jalali(d.year, d.month, d.day)
            assert jalali_to_gregorian(*j) == (d.year, d.month, d.day)


class TestDisplay:
    def test_format(self):
        dt = datetime(2026, 11, 3, 13, 45, 20)
        assert format_jalali_date(dt) == "1405/08/12"

    def test_display_datetime_converts_utc_to_local(self):
        local = datetime(2026, 11, 3, 13, 45, 20).astimezone()
        utc = local.astimezone(tz=None).astimezone(tz=None)
        # Round-trip through ISO with the local offset attached.
        iso = local.astimezone().isoformat(timespec="seconds")
        out = display_datetime(iso)
        assert out.startswith("1405/")
        assert out.endswith(local.strftime("%H:%M:%S"))


class TestParsing:
    def test_parse_jalali(self):
        assert parse_jalali_date("1405/08/11") == date(2026, 11, 2)
        assert parse_jalali_date("1405/08/12") == date(2026, 11, 3)

    def test_parse_invalid(self):
        assert parse_jalali_date("1405/13/01") is None
        assert parse_jalali_date("not a date") is None
        assert parse_jalali_date("") is None

    def test_day_bounds_are_utc_iso(self):
        bounds = jalali_day_bounds_utc("1405/08/11")
        assert bounds is not None
        start, end = bounds
        delta = parse_iso(end) - parse_iso(start)
        assert delta.total_seconds() == 86400

    def test_iso_parse_naive_assumed_utc(self):
        dt = parse_iso("2026-11-03T10:00:00")
        assert dt.utcoffset().total_seconds() == 0
