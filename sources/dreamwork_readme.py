"""
Source: the DreamWork HQ Tech Internships 2027 list.

    https://github.com/dreamworkhq/Tech-Internships-2027   (MIT licensed)

WHY THIS ONE IS WORTH HAVING
----------------------------
Two things no other source we read gives us at this volume:

  1. A PAY COLUMN on most rows. Only ~25% of the pool publishes a rate at
     all, and pay_lift() can only reward what it can see.
  2. SECTION HEADINGS that name a category — Engineering, Data Science,
     Security, Design, Product. Only Simplify otherwise labels category,
     and candidacy leans on it (CANDIDACY_CATEGORY).

It is also the largest single markdown list in the ecosystem at ~740 rows,
regenerated daily.

FORMAT
------
    ### Engineering (548)
    | Company | Role | Location | Pay | Added |
    | --- | --- | --- | --- | --- |
    | **[Freddie Mac](…)** | [SWE Intern- Summer 2027](…) | McLean, VA | $54K–$82K | 0d |

THREE THINGS THAT WOULD HAVE GONE WRONG, ALL HANDLED BELOW
----------------------------------------------------------
1. PAY IS ANNUAL, AND EVERY OTHER SOURCE'S IS HOURLY.

   This is the one that mattered. scorer.hourly_pay() reads "$54K" as
   FIFTY-FOUR DOLLARS AN HOUR — it takes the digits and ignores the "K" —
   and "$104K" as $104/hr, which clears the top PAY_LIFT band and would
   have handed the biggest pay bonus in the config to ordinary roles, on
   every row this source contributes. Silently.

   So the conversion happens HERE, at the edge, and the Posting carries an
   hourly figure like every other source. That is the whole point of the
   Source contract: site-specific weirdness is translated once, on the way
   in, and nothing downstream learns that this list is different.

   2080 = 40 hours x 52 weeks. Rough for a 12-week internship, but it is
   the ratio the salary itself is quoted against.

2. THE COMPANY CELL IS BOLD MARKDOWN: `**Freddie Mac**`.

   md.resolve_company() strips leading punctuation and trailing emoji, but
   not a trailing `**`, so companies arrived as "Freddie Mac**" — a
   different dedupe key from every other source's "Freddie Mac", which
   would have silently duplicated every overlapping row rather than
   merging it.

3. THE "Added" COLUMN IS A RELATIVE AGE ("0d"), NOT A DATE.

   The repo does print ISO dates, but in its own "last updated" banner, not
   per row. So this is parse_relative_age(), the approximate path — this
   source does NOT improve dates the way Chieler does, and should not be
   trusted over it. dedupe.py already prefers an absolute date, so it will
   defer to Chieler automatically wherever both carry a row.

ONE THING LEFT DELIBERATELY ALONE
---------------------------------
Apply links point at dreamworkhq.com/job/<uuid> redirects rather than the
employer's own posting, so dedupe's URL pass will not match them against
other lists. That is fine and is not worth "fixing" by following the
redirect: the company+role pass still merges them, and the project's stated
bias is toward under-merging, because a wrong merge hides a real job
invisibly while a missed one is merely visible.
"""

import re
from datetime import datetime, timezone

import requests

import config

from . import markdown_table as md
from .base import Posting, Source


# Their section headings -> the category vocabulary the rest of the app uses
# (see config.INGEST_CATEGORIES). Anything not listed here keeps its own
# heading text rather than being dropped: "Security" is not a category we
# ingest from Simplify, but a security-flavoured SWE internship is still a
# software internship, and the role-family gate is what decides.
CATEGORY_MAP = {
    "engineering": "Software Engineering",
    "data science": "Data Science, AI & Machine Learning",
    "product": "Product Management",
}

# Hours in a nominal work year, for the annual -> hourly conversion above.
HOURS_PER_YEAR = 2080

_HEADING = re.compile(r"^#{2,4}\s+(.+?)\s*(?:\(\d+\))?\s*$")
_BOLD = re.compile(r"\*\*|__")
# "$54K", "$54k", "$54,000", "$54K–$82K" — the amount and its optional unit.
_MONEY = re.compile(r"\$\s*([0-9][0-9,]*(?:\.[0-9]+)?)\s*([kK])?")

_TOKEN_CACHE = None
_NAME_CACHE = None


def _known_tokens() -> list:
    """
    Industry and company words worth splitting a squashed name on.

    Built from config rather than hardcoded, so it improves on its own every
    time a company is added to EMPLOYER_NAMES or OUT_OF_SCOPE_COMPANIES.
    Longest first, so "capitalmanagement" splits on the longer token before
    the shorter one inside it.
    """
    global _TOKEN_CACHE
    if _TOKEN_CACHE is None:
        words = set(config.NON_TECH_NAME_HINTS)
        for names in config.EMPLOYER_NAMES.values():
            words.update(names)
        words.update(config.OUT_OF_SCOPE_COMPANIES)

        tokens = set()
        for phrase in words:
            for word in phrase.split():
                # 6 characters is the shortest that has not produced a false
                # split in the live data. Below it you start matching
                # fragments of ordinary names rather than industry words.
                if len(word) >= 6 and word.isalpha():
                    tokens.add(word.lower())
        _TOKEN_CACHE = sorted(tokens, key=len, reverse=True)
    return _TOKEN_CACHE


def _known_names() -> set:
    """Every full company name the config can already match, lowercased."""
    global _NAME_CACHE
    if _NAME_CACHE is None:
        names = set(config.OUT_OF_SCOPE_COMPANIES)
        names.update(config.NON_TECH_NAME_HINTS)
        for group in config.EMPLOYER_NAMES.values():
            names.update(group)
        _NAME_CACHE = {n.lower() for n in names}
    return _NAME_CACHE


def unsquash(name: str) -> str:
    """
    Reinsert a word break into a company name derived from a domain.

    WHY THIS IS NEEDED, AND IT IS THE MOST IMPORTANT FUNCTION IN THIS FILE
    ---------------------------------------------------------------------
    This list names companies after their DOMAIN, not their display name:
    "Eastpennmanufacturing", "Akunacapital", "Zurichinsurance". 203 of its
    267 companies — 76% — arrive with no spaces at all.

    Everything that classifies an employer matches WHOLE WORDS, deliberately
    and for good reason (see scorer.employer_class: "Texas Instruments"
    contains "exa"). A whole-word matcher cannot see "manufacturing" inside
    "eastpennmanufacturing", so every one of those rows fell through to the
    "unknown" tier at x0.90 — the mild penalty for a company we've never
    heard of — instead of the x0.40 its industry earns.

    That is not a cosmetic problem. It silently defeated OUT_OF_SCOPE_
    COMPANIES: Garda Capital scored 19 as "Garda Capital Partners" and 48
    as "Gardacp", and the whole point of that list is that a quant firm's
    name is the only clue its titles ever give.

    WHAT THIS DOES AND DOESN'T PROMISE
    ----------------------------------
    It does NOT try to recover the real name — "Gardacp" is an abbreviation
    and no amount of splitting recovers "Garda Capital Partners". It only
    makes a known industry or company word VISIBLE to a word-boundary
    matcher, which is all the classifier actually needs.

    It is deliberately conservative, and splits only when a known token sits
    at the START or END of the squashed name. A missed split leaves the name
    exactly as it arrived and the posting in the "unknown" tier — which is
    where it already was, so the failure mode is no change rather than a
    wrong one. That is the same bias as dedupe's: under-merge, under-split.
    """
    name = (name or "").strip()
    if not name or " " in name:
        return name

    lowered = name.lower()

    # If the squashed form is ALREADY a name the config knows, leave it
    # alone. "Alixpartners" is listed verbatim as non-tech, and splitting it
    # into "Alix partners" broke a match that was working — the rescue made
    # a correctly classified employer unrecognized. This function exists to
    # rescue names nothing can place; it must never touch one already placed.
    if lowered in _known_names():
        return name

    for token in _known_tokens():
        if len(lowered) <= len(token):
            continue
        if lowered.endswith(token):
            return f"{name[:-len(token)]} {name[-len(token):]}"
        if lowered.startswith(token):
            return f"{name[:len(token)]} {name[len(token):]}"
    return name


def _clean_company(text: str) -> str:
    """Drop markdown bold, then restore a word break if the name is a domain."""
    return unsquash(_BOLD.sub("", text or "").strip())


def to_hourly(pay_text: str) -> str:
    """
    An annual pay cell rendered as the hourly string the scorer expects.

    Returns "" when there is nothing parseable, which is the honest answer:
    PAY_LIFT only ever adds, so a missing rate costs a posting nothing.

    A figure that is already hourly (no "K", small number) is passed through
    unchanged rather than divided again — the column is annual today, but a
    format change upstream should degrade quietly rather than turn $45/hr
    into two cents.
    """
    if not pay_text:
        return ""

    amounts = []
    for raw, unit in _MONEY.findall(pay_text):
        try:
            value = float(raw.replace(",", ""))
        except ValueError:
            continue
        if unit:                      # "$54K"
            value *= 1000
        if value >= 10000:            # an annual figure, however written
            value /= HOURS_PER_YEAR
        amounts.append(value)

    if not amounts:
        return ""

    # Low end of a range, matching hourly_pay()'s own rule. Formatting it
    # back into "$N/hr" keeps this source's output identical in shape to
    # every other one, so nothing downstream needs to know.
    return f"${min(amounts):.0f}/hr"


class DreamWorkReadmeSource(Source):
    """Fetches and parses the DreamWork HQ Tech Internships 2027 list."""

    name = "DreamWork-2027"

    URL = (
        "https://raw.githubusercontent.com/"
        "dreamworkhq/Tech-Internships-2027/main/README.md"
    )

    def __init__(self, url: str = None):
        self.url = url or self.URL

    def _download(self) -> str:
        response = requests.get(
            self.url,
            timeout=30,
            headers={"User-Agent": "personal-internship-finder"},
        )
        response.raise_for_status()
        return response.text

    def _sections(self, markdown: str) -> list:
        """
        Split the page into (category, markdown) blocks by heading.

        parse_rows() flattens the whole document and so cannot tell which
        table a row came from. The category only exists in the heading
        above it, so the split has to happen before parsing.
        """
        sections = []
        current, buffer = "", []

        for line in markdown.split("\n"):
            heading = _HEADING.match(line)
            if heading:
                if buffer:
                    sections.append((current, "\n".join(buffer)))
                raw = heading.group(1).strip().lower()
                current = CATEGORY_MAP.get(raw, heading.group(1).strip())
                buffer = []
            else:
                buffer.append(line)

        if buffer:
            sections.append((current, "\n".join(buffer)))
        return sections

    def fetch(self) -> list:
        today = datetime.now(timezone.utc).date()
        postings = []

        for category, block in self._sections(self._download()):
            last_company = ""
            for row in md.parse_rows(block):
                # Company | Role | Location | Pay | Added. Rows with extra
                # trailing cells appear in the wild, so this checks a
                # MINIMUM width and indexes from the left.
                if len(row) < 5:
                    continue

                company, last_company = md.resolve_company(
                    _clean_company(row[0]["text"]), last_company
                )
                role = row[1]["text"]
                if not company or not role:
                    continue

                # The apply link lives in the ROLE cell here, which is why
                # md._strip_html() unwrapping markdown links to their label
                # matters: without it the URL becomes part of the title.
                links = row[1]["links"] or row[0]["links"]
                if not links:
                    continue

                age_text = row[4]["text"]
                row_text = " ".join(cell["raw"] for cell in row)

                postings.append(
                    Posting(
                        company=company,
                        role=role,
                        category=category or "Uncategorized",
                        location=row[2]["text"],
                        apply_url=links[0],
                        date_posted=md.parse_relative_age(age_text, today),
                        age_text=age_text,
                        salary=to_hourly(row[3]["text"]),
                        source=self.name,
                        is_faang="🔥" in row_text,
                        needs_advanced_degree="🎓" in row_text
                        or "PhD" in role or "Masters" in role,
                        no_sponsorship="🛂" in row_text,
                        citizenship_required="🇺🇸" in row_text,
                    )
                )

        return postings
