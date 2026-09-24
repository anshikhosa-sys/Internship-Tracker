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

import html
import re
from datetime import date, datetime

# Matches a markdown table separator row: | --- | :--- | ---: |
SEPARATOR = re.compile(r"^\s*\|[\s:|-]+\|\s*$")


def _extract_links(cell: str) -> list:
    """
    Every link in a cell, in order.

    Two syntaxes appear across the sources: HTML anchors with an href, and
    plain markdown [text](url). A source using the second form would return
    no links at all if only hrefs were matched, and every one of its rows
    would be dropped for having nothing to apply to.
    """
    links = re.findall(r'href=["\']([^"\']+)["\']', cell)
    links += re.findall(r'\[[^\]]*\]\((https?://[^)\s]+)\)', cell)
    return links


def _strip_html(text: str) -> str:
    """
    Remove markup and collapse whitespace, keeping the visible text.

    Markdown links are UNWRAPPED to their label, not deleted: a source that
    puts the apply link inside the role cell would otherwise give roles
    reading "[Algorithm Development Engineer Intern](https://analogdevices…"
    — the URL becomes part of the job title, which then reaches the scorer,
    the dashboard and the letter prompts. _extract_links() has already
    taken the href by the time this runs, so nothing is lost.

    Bare markdown images (an "Apply" button graphic) drop entirely; their
    alt text is not the visible text of the cell in any useful sense.

    THE LABEL PATTERN ALLOWS ESCAPED BRACKETS, and has to.

    A naive "anything but a closing bracket" label stops at the FIRST
    closing bracket, which in a title like "[ESCAPED-OPEN Summer 2027
    ESCAPED-CLOSE Software Engineer Intern](https://…)" is the escaped one
    inside the label. The unwrap then fails and the whole raw link —
    URL included — survives as the job title, which is the exact bug this
    function exists to prevent, reached by a different route. Four live
    rows hit it (Roblox, SAP, two at Figure).
    """
    # "</br>" is not valid HTML but it is what these READMEs actually write
    # between the cities in a multi-location cell, so it has to be matched
    # alongside <br> and <br/>. Missing it left 40 live postings with a
    # location like "Mountain View, CAAtlanta, GAAustin, TX" — one token, so
    # whole-word matching against a user's wanted locations never fired.
    text = re.sub(r"<\s*/?\s*br\s*/?\s*>", " | ", text, flags=re.I)
    # A <details> cell's <summary> is a disclosure label ("**30 locations**"),
    # not content. Dropping the tags alone would leave that count glued to the
    # first real value.
    text = re.sub(r"<summary\b[^>]*>.*?</summary\s*>", "", text, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", "", text)
    # Images first, then links. Both labels may contain backslash escapes.
    text = re.sub(r"!\[(?:[^\[\]\\]|\\.)*\]\([^)]*\)", "", text)
    text = re.sub(r"\[((?:[^\[\]\\]|\\.)*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"\\([\[\]()])", r"\1", text)   # unescape what's left
    text = html.unescape(text)                    # &amp; -> &
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
        if "%Y" in fmt:
            try:
                return datetime.strptime(text, fmt).date().isoformat()
            except ValueError:
                continue

        # The year isn't printed, so supply one rather than letting strptime
        # default to 1900. Two reasons: 1900 is not a leap year, so "Feb 29"
        # raised ValueError and the date was thrown away entirely; and Python
        # 3.15 stops accepting a day-of-month with no year at all.
        #
        # Candidates run backwards from this year because a job cannot be
        # posted in the future. Four years back is enough to reach the last
        # leap day from any starting year; every other date resolves on the
        # first or second try.
        for year in range(today.year, today.year - 5, -1):
            try:
                parsed = datetime.strptime(f"{text} {year}", f"{fmt} %Y").date()
            except ValueError:
                continue     # e.g. Feb 29 of a non-leap year
            if parsed > today:
                continue     # belongs to an earlier year
            return parsed.isoformat()

    # Fall back to the relative form in case the column mixes both.
    return parse_relative_age(text, today)


def _timedelta_days(days: int):
    from datetime import timedelta
    return timedelta(days=days)
