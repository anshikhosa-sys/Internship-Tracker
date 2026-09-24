"""
Whole-word matching and text normalization. Every keyword match in the
project goes through here.

Why not `\\b`: a word boundary sits between a word character and a non-word
character, so `\\bc\\+\\+\\b` can never match "C++ developer" (there is no
boundary between "+" and " "), and `\\b.net\\b` never matches ".NET". Those
matchers were silently dead. Lookarounds on an explicit token alphabet fix both
while still refusing "ai" inside "maintain" and "go" inside "google".
"""

from __future__ import annotations

import re
from functools import lru_cache

# Characters that continue a technical token. "c" followed by "+" is part of
# "c++"; "node" followed by "." is part of "node.js".
_TOKEN_CHARS = r"\w+#"


@lru_cache(maxsize=8192)
def pattern_for(keyword: str, case_sensitive: bool = False) -> re.Pattern:
    escaped = re.escape(keyword.strip())
    flags = 0 if case_sensitive else re.IGNORECASE
    # A trailing "." is sentence punctuation, not part of the token, so it may
    # follow a match; a leading "." may not precede one ("asp.net" is not ".net").
    return re.compile(rf"(?<![{_TOKEN_CHARS}.]){escaped}(?![{_TOKEN_CHARS}]|\.\w)", flags)


@lru_cache(maxsize=4096)
def _lowered(text: str) -> str:
    """
    text.lower(), memoised.

    contains() is called on the order of a million times per scoring run, and
    lowercasing its haystack each time cost seconds for a result that never
    differs. The same few thousand titles and descriptions come round again
    and again, so the cache hits almost every time.
    """
    return text.lower()


def contains(text: str, keyword: str, case_sensitive: bool = False) -> bool:
    if not text or not keyword:
        return False
    # A substring test is a necessary condition and ~50x cheaper than the regex;
    # it rejects nearly every keyword before the regex runs.
    if case_sensitive:
        if keyword.strip() not in text:
            return False
    elif keyword.strip().lower() not in _lowered(text):
        return False
    return bool(pattern_for(keyword, case_sensitive).search(text))


def find_all(text: str, keywords, case_sensitive: bool = False) -> list[str]:
    """Keywords (in given order) that occur in text."""
    if not text:
        return []
    haystack = text if case_sensitive else _lowered(text)
    out = []
    for kw in keywords:
        needle = kw.strip() if case_sensitive else kw.strip().lower()
        if needle and needle in haystack and pattern_for(kw, case_sensitive).search(text):
            out.append(kw)
    return out


def is_valid_keyword(keyword: str) -> bool:
    """True if the keyword can ever match itself — the dead-matcher test."""
    if not isinstance(keyword, str) or not keyword.strip() or keyword != keyword.strip():
        return False
    try:
        return contains(keyword, keyword)
    except re.error:
        return False


_SPACE = re.compile(r"\s+")
_PUNCT = re.compile(r"[^\w\s+#./-]")


def squash(text: str) -> str:
    """Lowercase, collapse whitespace, drop decorative punctuation."""
    return _SPACE.sub(" ", _PUNCT.sub(" ", (text or "").lower())).strip()


def expand_abbreviations(text: str, table: dict[str, str]) -> str:
    """Replace whole-word abbreviations ("SDE" -> "software development engineer")."""
    out = squash(text)
    for short, full in table.items():
        out = pattern_for(short).sub(full, out)
    return _SPACE.sub(" ", out).strip()


def mention_is_genuine(text: str, skill: str, rule: dict) -> bool:
    """
    Decide whether a matched word is the skill or the ordinary English word.

    Some skill names are common words that no amount of whole-word matching or
    casing can separate — "Spring 2027" is a season. A rule names what may not
    follow the word, and what elsewhere in the text vouches for it.
    """
    if any(contains(text, phrase) for phrase in rule.get("qualified_by", [])):
        return True
    rejected = rule.get("rejected_after")
    if not rejected:
        return True
    for match in pattern_for(skill).finditer(text):
        tail = text[match.end():match.end() + 40]
        if not re.match(rejected, tail, re.IGNORECASE):
            return True          # at least one mention has no disqualifier after it
    return False
