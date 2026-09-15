"""
Title classification shared by profile derivation and scoring.

One function decides which role families a title belongs to and one decides
its seniority, so "what kind of job is this" is answered identically for a
posting and for a line on a résumé.
"""

from __future__ import annotations

from jobrank import textmatch
from jobrank.config import taxonomy


def classify_title(title: str) -> list[str]:
    """
    Role families a title names, most specific first.

    A generic family ("software_engineering", matched by the bare word
    "engineer") is dropped when a specific one also matches, so "Machine
    Learning Engineer" is ML engineering, not ML engineering AND generic SWE.
    An explicit "software engineer" phrase keeps the generic family alongside.
    """
    if not title:
        return []
    expanded = textmatch.expand_abbreviations(title, taxonomy.TITLE_ABBREVIATIONS)
    hits: list[tuple[int, str]] = []
    for family, spec in taxonomy.ROLE_FAMILIES.items():
        matched = [kw for kw in spec["titles"]
                   if textmatch.contains(title, kw) or textmatch.contains(expanded, kw)]
        if matched:
            hits.append((max(len(kw) for kw in matched), family))
    if not hits:
        return []
    hits.sort(key=lambda h: -h[0])
    families = [f for _, f in hits]
    specific = [f for f in families if f not in taxonomy.GENERIC_FAMILIES]
    if specific:
        generic_explicit = [
            f for f in families if f in taxonomy.GENERIC_FAMILIES
            and any(len(kw.split()) > 1 and (textmatch.contains(title, kw) or textmatch.contains(expanded, kw))
                    for kw in taxonomy.ROLE_FAMILIES[f]["titles"])
        ]
        return specific + generic_explicit
    return families


def title_seniority(title: str) -> str | None:
    """The level a title names, or None when it names none."""
    if not title:
        return None
    if textmatch.find_all(title, taxonomy.SENIORITY_TITLE_MARKERS[taxonomy.SENIORITY_OVERRIDE_LEVEL]):
        return taxonomy.SENIORITY_OVERRIDE_LEVEL
    for level in reversed(taxonomy.SENIORITY_LEVELS):
        if level == taxonomy.SENIORITY_OVERRIDE_LEVEL:
            continue
        if textmatch.find_all(title, taxonomy.SENIORITY_TITLE_MARKERS.get(level, [])):
            return level
    return None
