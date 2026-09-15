"""
Résumé dates -> "YYYY-MM".

Handles the forms résumés actually use: "Aug 2026", "August 2026", "08/2026",
"2026-08", "Summer 2025", bare "2024", and ranges joined by "-", "–", "—" or
"to", ending in "Present"/"Current"/"Now".
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7, "aug": 8,
    "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}
_SEASONS = {"spring": 3, "summer": 6, "fall": 9, "autumn": 9, "winter": 1}

_MONTH_NAME = r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?"
_SEASON = r"(?:spring|summer|fall|autumn|winter)"
_POINT = rf"(?:{_MONTH_NAME}\s+\d{{4}}|{_SEASON}\s+\d{{4}}|\d{{1,2}}/\d{{4}}|\d{{4}}-\d{{2}}(?!\d)|(?<![\d/-])\d{{4}}(?![\d/-]))"
_PRESENT = r"(?:present|current|now|ongoing|today)"
_RANGE = re.compile(rf"({_POINT})\s*(?:-|–|—|to|until)\s*({_POINT}|{_PRESENT})", re.IGNORECASE)
_SINGLE = re.compile(rf"({_POINT})", re.IGNORECASE)


@dataclass
class DateRange:
    start: str | None
    end: str | None
    current: bool


def parse_point(text: str) -> str | None:
    t = text.strip().lower().rstrip(".")
    m = re.fullmatch(r"(\w+)\.?\s+(\d{4})", t)
    if m:
        word, year = m.group(1), int(m.group(2))
        if word in _SEASONS:
            return f"{year:04d}-{_SEASONS[word]:02d}"
        month = _MONTHS.get(word[:4] if word.startswith("sept") else word[:3])
        if month:
            return f"{year:04d}-{month:02d}"
        return None
    m = re.fullmatch(r"(\d{1,2})/(\d{4})", t)
    if m and 1 <= int(m.group(1)) <= 12:
        return f"{int(m.group(2)):04d}-{int(m.group(1)):02d}"
    m = re.fullmatch(r"(\d{4})-(\d{2})", t)
    if m and 1 <= int(m.group(2)) <= 12:
        return f"{m.group(1)}-{m.group(2)}"
    m = re.fullmatch(r"(\d{4})", t)
    if m and 1950 <= int(m.group(1)) <= 2100:
        return f"{m.group(1)}-01"
    return None


def find_range(line: str) -> DateRange | None:
    line = _normalize(line)
    m = _RANGE.search(line)
    if m:
        start = parse_point(m.group(1))
        end_raw = m.group(2)
        if re.fullmatch(_PRESENT, end_raw.strip(), re.IGNORECASE):
            return DateRange(start, None, True)
        end = parse_point(end_raw)
        # A bare-year end ("2023 - 2024") means the whole year, not January.
        if end and re.fullmatch(r"\d{4}", end_raw.strip()):
            end = f"{end[:4]}-12"
        return DateRange(start, end, False)
    single = _SINGLE.search(line)
    if single:
        point = parse_point(single.group(1))
        return DateRange(point, point, False) if point else None
    return None


def all_points(text: str) -> list[str]:
    return [p for p in (parse_point(m.group(1)) for m in _SINGLE.finditer(_normalize(text))) if p]


_YEAR_DASH_YEAR = re.compile(r"(?<!\d)(\d{4})\s*([-–—])\s*(\d{4})(?!\d)")


def _normalize(text: str | None) -> str:
    return _YEAR_DASH_YEAR.sub(r"\1 \2 \3", text or "")


def months_between(start: str, end: str) -> int:
    sy, sm = int(start[:4]), int(start[5:7])
    ey, em = int(end[:4]), int(end[5:7])
    return max(0, (ey - sy) * 12 + (em - sm) + 1)


def today_ym(today: date | None = None) -> str:
    today = today or date.today()
    return f"{today.year:04d}-{today.month:02d}"


def strip_dates(line: str) -> str:
    """Remove dates, leaving a column gap where they were so segments stay split."""
    out = _RANGE.sub("\t", _normalize(line))
    out = _SINGLE.sub("\t", out)
    return out.strip(" \t,|·•-–—")
