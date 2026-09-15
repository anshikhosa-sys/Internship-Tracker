"""
Facts derived from a posting alone, independent of any user: which industry
the employer is in, how large it is, where and how the work happens, which term
it starts, what it pays. Scoring compares these facts to a profile; nothing
here knows who is looking.

Accepts either a `Posting` object or a database row dict.
"""

from __future__ import annotations

import re
from datetime import date
from functools import lru_cache

from jobrank import textmatch
from jobrank.config import companies, scoring, taxonomy
from jobrank.config import settings as display
from jobrank.roles import classify_title, title_seniority


def field(posting, name, default=None):
    if hasattr(posting, name):
        return getattr(posting, name)
    if isinstance(posting, dict):
        return posting.get(name, default)
    return default


def company_key(posting) -> str:
    return (field(posting, "company") or "").strip().lower()


def industry(posting) -> str | None:
    return _industry_of(company_key(posting))


@lru_cache(maxsize=65536)
def _industry_of(name: str) -> str | None:
    if not name:
        return None
    for industry_name, names in companies.COMPANY_INDUSTRIES.items():
        if textmatch.find_all(name, names):
            return industry_name
    for industry_name, hints in companies.INDUSTRY_NAME_HINTS.items():
        if textmatch.find_all(name, hints):
            return industry_name
    return None


def is_tech_employer(posting) -> bool:
    return industry(posting) in companies.TECH_INDUSTRIES


def company_volumes(postings) -> dict[str, int]:
    counts: dict[str, int] = {}
    for posting in postings:
        key = company_key(posting)
        counts[key] = counts.get(key, 0) + 1
    return counts


def company_size(posting, volumes: dict[str, int] | None = None) -> str:
    """"large", "mid" or "startup" — from known employers, then posting volume."""
    name = company_key(posting)
    if field(posting, "is_faang") or _known_large(name):
        return "large"
    volume = (volumes or {}).get(name, 0)
    if volume >= scoring.LARGE_MIN_POSTINGS:
        return "large"
    if volume >= scoring.MID_MIN_POSTINGS:
        return "mid"
    return "startup"


@lru_cache(maxsize=65536)
def _known_large(name: str) -> bool:
    return bool(textmatch.find_all(name, companies.LARGE_COMPANIES))


def parent_company(posting_or_name) -> str | None:
    name = posting_or_name if isinstance(posting_or_name, str) else company_key(posting_or_name)
    name = (name or "").strip().lower()
    for parent, names in companies.PARENT_COMPANIES.items():
        if textmatch.find_all(name, names):
            return parent
    return None


def work_mode(posting, enrichment: dict | None = None) -> str | None:
    """"remote", "hybrid", "onsite", or None when the posting does not say."""
    if enrichment and enrichment.get("remote_status") in ("remote", "hybrid", "onsite"):
        return enrichment["remote_status"]
    text = " ".join(filter(None, [field(posting, "location"), field(posting, "role")]))
    if textmatch.find_all(text, scoring.REMOTE_MARKERS):
        return "remote"
    if textmatch.find_all(text, scoring.HYBRID_MARKERS):
        return "hybrid"
    return "onsite" if (field(posting, "location") or "").strip() else None


def term_start(posting) -> str | None:
    """"Summer 2027 Software Engineer Intern" -> "2027-06"."""
    title = field(posting, "role") or ""
    match = re.search(r"\b(winter|spring|summer|fall|autumn)\s*[-/]?\s*(20\d\d)\b", title, re.I)
    if not match:
        return None
    return f"{match.group(2)}-{scoring.TERM_START_MONTH[match.group(1).lower()]:02d}"


def seniority(posting, enrichment: dict | None = None) -> tuple[str | None, str]:
    """(level, how it was determined)."""
    from_title = title_seniority(field(posting, "role") or "")
    if from_title:
        return from_title, "title"
    if enrichment and enrichment.get("entry_level") is True:
        return "entry", "enrichment"
    source = field(posting, "source") or ""
    default = scoring.SOURCE_DEFAULT_SENIORITY.get(source)
    if default:
        return default, f"source {source} lists only {default} roles"
    return None, "unknown"


def role_families(posting) -> list[str]:
    families = classify_title(field(posting, "role") or "")
    if families:
        return families
    category = (field(posting, "category") or "").strip().lower()
    mapped = scoring.CATEGORY_FAMILIES.get(category)
    return [mapped] if mapped else []


def days_old(posting, today: date | None = None) -> int | None:
    posted = field(posting, "date_posted")
    if not posted:
        return None
    try:
        return max(0, ((today or date.today()) - date.fromisoformat(posted)).days)
    except (TypeError, ValueError):
        return None


def hourly_pay(posting) -> float | None:
    """Low end of a published hourly range, or None."""
    raw = (field(posting, "salary") or "").strip()
    amounts = re.findall(r"\$\s*([0-9][0-9,]*(?:\.[0-9]+)?)", raw)
    if not amounts:
        return None
    try:
        low = min(float(a.replace(",", "")) for a in amounts)
    except ValueError:
        return None
    return low if 0 < low <= display.MAX_PLAUSIBLE_HOURLY else None


def is_coop(posting) -> bool:
    return bool(textmatch.find_all(field(posting, "role") or "", display.COOP_KEYWORDS))


def is_off_season(posting) -> bool:
    return bool(textmatch.find_all(field(posting, "role") or "", display.OFF_SEASON_KEYWORDS))


def text_for_embedding(posting, enrichment: dict | None = None) -> str:
    title = field(posting, "role") or ""
    parts = [title, textmatch.expand_abbreviations(title, taxonomy.TITLE_ABBREVIATIONS),
             field(posting, "category") or ""]
    description = (field(posting, "description") or "").strip()
    if description:
        parts.append(description[:2000])
    if enrichment and enrichment.get("tech_stack"):
        parts.append("Tech: " + ", ".join(enrichment["tech_stack"]))
    return "\n".join(p for p in parts if p)


def text_for_enrichment(posting) -> str:
    return "\n".join(filter(None, [
        f"Title: {field(posting, 'role') or ''}",
        f"Company: {field(posting, 'company') or ''}",
        f"Location: {field(posting, 'location') or ''}",
        (field(posting, "description") or "").strip(),
    ]))
