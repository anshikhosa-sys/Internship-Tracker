"""
Shared helpers for sources whose listings are markdown pipe tables.

The Simplify repo uses HTML <table> blocks. Two of the other aggregators use
markdown tables instead:

    | Company | Role | Location | Application/Link | Date Posted |
    | ------- | ---- | -------- | ---------------- | ----------- |
    | Vertiv | Product Management Intern | Westerville, OH | <a..> | Aug 21 |

Same shape, different syntax, so the row-level quirks are the same too —
continuation rows, emoji flags, links buried in HTML inside a cell. That
shared logic lives here rather than being copy-pasted into each source.

WHY NOT JUST SPLIT ON "|"
-------------------------
Because the cells contain HTML that contains attributes that contain... more
characters. A naive split works on the sample above but breaks the moment a
cell holds a URL with a pipe in a query string, or a nested table. The reader
below strips HTML properly and pulls hrefs out first, so the split happens on
text we've already made safe.
"""

import re
from datetime import date, datetime

# Matches a markdown table separator row: | --- | :--- | ---: |
SEPARATOR = re.compile(r"^\s*\|[\s:|-]+\|\s*$")


def _extract_links(cell: str) -> list:
    """Every href in a cell, in order."""
    return re.findall(r'href=["\']([^"\']+)["\']', cell)


def _strip_html(text: str) -> str:
    """Remove tags and collapse whitespace, keeping the visible text."""
    text = re.sub(r"<br\s*/?>", " | ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"&nbsp;?", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def parse_rows(markdown: str) -> list:
    """
    Find every markdown table row and return it as a list of cell dicts:
    {"text": visible text, "links": [href, ...], "raw": original cell}.

    Header and separator rows are skipped.
    """
    rows = []
    seen_header = False

    for line in markdown.split("\n"):
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        if SEPARATOR.match(stripped):
            seen_header = True     # everything after this is data
            continue

        # Drop the leading and trailing pipe, then split.
        inner = stripped.strip("|")
        cells = inner.split("|")

        # A header row looks like data until we've seen the separator.
        lowered = [c.strip().lower() for c in cells]
        if not seen_header and "company" in lowered:
            continue

        rows.append([
            {
                "text": _strip_html(cell),
                "links": _extract_links(cell),
                "raw": cell,
            }
            for cell in cells
        ])

    return rows


def resolve_company(text: str, last_company: str):
    """
    Handle the "↳ means same company as the row above" convention.

    Returns (company, new_last_company). Emoji prefixes like 🔥 are stripped
    so the name is usable as a key for deduplication across sources.
    """
    text = text.strip()
    if text.startswith("↳") or text in ("↳", ""):
        return last_company, last_company

    cleaned = re.sub(r"^[^A-Za-z0-9(]+", "", text).strip()
    cleaned = re.sub(r"\s*[🔥🛂🇺🇸🎓🔒]+\s*$", "", cleaned).strip()
    return cleaned, cleaned


def parse_relative_age(age_text: str, today: date):
    """
    "0d" / "18d" / "1mo" -> an ISO date. Returns None if unparseable.

    Shared with the HTML source; the aggregators all use the same shorthand.
    """
    if not age_text:
        return None
    match = re.match(r"(\d+)\s*(h|d|w|mo|y)", age_text.strip().lower())
    if not match:
        return None
    amount, unit = int(match.group(1)), match.group(2)
    days = {
        "h": 0,
        "d": amount,
        "w": amount * 7,
        "mo": amount * 30,    # approximation
        "y": amount * 365,
    }[unit]
    return (today - _timedelta_days(days)).isoformat()


def parse_absolute_date(text: str, today: date):
    """
    Parse an absolute date like "Aug 21" into an ISO date.

    One source publishes real dates rather than relative ages, which is
    strictly better information — no approximation. The only ambiguity is the
    year, which isn't printed: a date that would be in the future must belong
    to last year, since a job can't be posted tomorrow.
    """
    text = (text or "").strip()
    if not text:
        return None

    for fmt in ("%b %d", "%B %d", "%b %d %Y", "%B %d %Y", "%Y-%m-%d"):
        try:
            parsed = datetime.strptime(text, fmt).date()
        except ValueError:
            continue

        if "%Y" not in fmt:
            parsed = parsed.replace(year=today.year)
            if parsed > today:
                parsed = parsed.replace(year=today.year - 1)
        return parsed.isoformat()

    # Fall back to the relative form in case the column mixes both.
    return parse_relative_age(text, today)


def _timedelta_days(days: int):
    from datetime import timedelta
    return timedelta(days=days)
