"""Solar Hijri (Jalali) calendar conversion and display formatting.

Internal timestamps are always stored as UTC ISO-8601 strings.
Display converts to local time, then renders the Jalali date and a
24-hour clock, using English (ASCII) digits only.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone

_G_D_M = (0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334)


def gregorian_to_jalali(gy: int, gm: int, gd: int) -> tuple[int, int, int]:
    """Convert a Gregorian date to Jalali (Jalali month/day are 1-based)."""
    gy2 = gy - 1600
    gm2 = gm - 1
    gd2 = gd - 1
    g_day_no = 365 * gy2 + (gy2 + 3) // 4 - (gy2 + 99) // 100 + (gy2 + 399) // 400
    g_day_no += _G_D_M[gm2]
    if gm2 > 1 and ((gy % 4 == 0 and gy % 100 != 0) or (gy % 400 == 0)):
        g_day_no += 1
    g_day_no += gd2

    j_day_no = g_day_no - 79
    j_np = j_day_no // 12053
    j_day_no %= 12053
    jy = 979 + 33 * j_np + 4 * (j_day_no // 1461)
    j_day_no %= 1461
    if j_day_no >= 366:
        jy += (j_day_no - 1) // 365
        j_day_no = (j_day_no - 1) % 365
    if j_day_no < 186:
        jm = 1 + j_day_no // 31
        jd = 1 + j_day_no % 31
    else:
        jm = 7 + (j_day_no - 186) // 30
        jd = 1 + (j_day_no - 186) % 30
    return jy, jm, jd


def jalali_to_gregorian(jy: int, jm: int, jd: int) -> tuple[int, int, int]:
    """Convert a Jalali date to Gregorian (standard jdf.scr.ir algorithm)."""
    jy += 1595
    days = -355668 + (365 * jy) + ((jy // 33) * 8) + (((jy % 33) + 3) // 4) + jd
    if jm < 7:
        days += (jm - 1) * 31
    else:
        days += ((jm - 7) * 30) + 186

    gy = 400 * (days // 146097)
    days %= 146097
    if days > 36524:
        days -= 1
        gy += 100 * (days // 36524)
        days %= 36524
        if days >= 365:
            days += 1
    gy += 4 * (days // 1461)
    days %= 1461
    if days > 365:
        gy += (days - 1) // 365
        days = (days - 1) % 365
    gd = days + 1

    is_leap = (gy % 4 == 0 and gy % 100 != 0) or (gy % 400 == 0)
    months = (31, 29 if is_leap else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)
    gm = 1
    for i, days_in_month in enumerate(months, start=1):
        if gd <= days_in_month:
            gm = i
            break
        gd -= days_in_month
    return gy, gm, gd


def utc_now_iso() -> str:
    """Current UTC time as ISO-8601 (seconds precision)."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_iso(value: str) -> datetime:
    """Parse an ISO-8601 timestamp (assumed UTC when naive)."""
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def to_local(dt: datetime) -> datetime:
    """Convert an aware datetime to local time."""
    return dt.astimezone()


def format_jalali_date(dt: datetime) -> str:
    """Local datetime -> '1405/08/12'."""
    jy, jm, jd = gregorian_to_jalali(dt.year, dt.month, dt.day)
    return f"{jy:04d}/{jm:02d}/{jd:02d}"


def format_jalali_datetime(dt: datetime) -> str:
    """Local datetime -> '1405/08/12 13:45:20'."""
    return f"{format_jalali_date(dt)} {dt.strftime('%H:%M:%S')}"


def display_datetime(iso_value: str | None) -> str:
    """UTC ISO string -> local Jalali date + 24h time for display."""
    if not iso_value:
        return ""
    return format_jalali_datetime(to_local(parse_iso(iso_value)))


def display_date(iso_value: str | None) -> str:
    if not iso_value:
        return ""
    return format_jalali_date(to_local(parse_iso(iso_value)))


def display_time(iso_value: str | None) -> str:
    if not iso_value:
        return ""
    return to_local(parse_iso(iso_value)).strftime("%H:%M:%S")


_JALALI_DATE_RE = re.compile(r"^(\d{4})/(\d{1,2})/(\d{1,2})$")


def parse_jalali_date(text: str) -> date | None:
    """Parse '1405/08/11' into a Gregorian date. None when invalid."""
    match = _JALALI_DATE_RE.match(text.strip())
    if not match:
        return None
    jy, jm, jd = (int(g) for g in match.groups())
    if not (1 <= jm <= 12 and 1 <= jd <= 31):
        return None
    try:
        gy, gm, gd = jalali_to_gregorian(jy, jm, jd)
        return date(gy, gm, gd)
    except ValueError:
        return None


def jalali_day_bounds_utc(text: str) -> tuple[str, str] | None:
    """'1405/08/11' -> (UTC ISO start-of-day, UTC ISO end-of-day) or None."""
    d = parse_jalali_date(text)
    if d is None:
        return None
    start_local = datetime(d.year, d.month, d.day).astimezone()
    end_local = start_local + timedelta(days=1)
    return (
        start_local.astimezone(timezone.utc).isoformat(timespec="seconds"),
        end_local.astimezone(timezone.utc).isoformat(timespec="seconds"),
    )
