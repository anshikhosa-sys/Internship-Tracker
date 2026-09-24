"""
dedupe.py — merge the same job appearing in several sources.

Three aggregators cover overlapping ground, so the same Palantir internship
shows up two or three times. Without merging you'd apply twice, and the
company-volume signal in the scorer would be badly inflated.

HOW MATCHING WORKS
------------------
Two postings are the same job if their normalized company AND normalized role
match. Normalizing means lowercasing, dropping punctuation, and stripping the
noise words every list adds differently — "Summer 2027", "Intern", "(Remote)",
season markers, and requisition numbers.

WHY NOT MATCH ON THE APPLY URL
------------------------------
It seems like the obvious key, and it doesn't work. Each aggregator appends
its own tracking parameters (`utm_source=Simplify`, `utm_source=github-vansh`,
`ref=...`), so the same job has a different URL in every list. Stripping query
strings helps but still fails when one source links to the company's careers
page and another to a Greenhouse or Workday mirror of it.

Company plus role is fuzzier but matches what a human would call the same job.

WHICH COPY WINS
---------------
The merged record takes the best field from each copy rather than picking one
wholesale:

  - an ABSOLUTE date beats a date derived from a relative age
  - a real category beats "Uncategorized"
  - a salary beats no salary
  - flags are OR-ed, since one list marking a degree requirement is evidence
    even when another omits it

That way adding a source can only ever improve a record.
"""

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from jobrank.sources.base import normalize


def key_for(posting) -> tuple:
    """
    The identity of a job, independent of which list it came from.

    Uses the SAME normalization as Posting.id, imported rather than
    reimplemented — if these two ever disagreed, a job could merge under one
    rule while its applied mark was filed under another.
    """
    return (normalize(posting.company), normalize(posting.role))


# "0d", "18d", "3mo", "2h" -- the relative shorthand. An age_text that looks
# like this produced an APPROXIMATE date ("1mo" is anywhere from 30 to 59
# days); anything else ("Aug 21", "2026-08-21") produced an exact one.
_RELATIVE_AGE = re.compile(r"^\s*\d+\s*(h|d|w|mo|y)\b", re.I)


def _is_relative(age_text) -> bool:
    return bool(_RELATIVE_AGE.match(age_text or ""))


def _better_date(a, age_a, b, age_b):
    """
    Prefer a real date over one derived from a relative age.

    Returns (date, age_text) so the two stay in step.

    Sources publishing "Aug 21" give an exact day. Sources publishing "18d"
    give an approximation that also drifts. When both exist, take the exact
    one; the age_text that came with it tells us which is which.

    This used to be `max(a, b)`, which read the docstring's intent backwards:
    the approximate date is usually the LATER one (a month-old posting listed
    as "1mo" lands on a rounded day), so the exact date lost every time and
    the posting scored fresher than it was. 234 live postings carried an
    age_text that contradicted their own date_posted because of it.
    """
    if not a:
        return b, age_b
    if not b:
        return a, age_a
    if _is_relative(age_a) != _is_relative(age_b):
        return (b, age_b) if _is_relative(age_a) else (a, age_a)
    # Both exact or both approximate: nothing to choose between them, so keep
    # the later one, as before.
    return (a, age_a) if a >= b else (b, age_b)


def merge(primary, other):
    """
    Fold `other` into `primary`, keeping the best of each field.

    `primary` is mutated and returned. It should be the copy from whichever
    source you trust most for the fields that aren't compared here.
    """
    if not primary.date_posted:
        primary.date_posted = other.date_posted
        primary.age_text = other.age_text
    elif other.date_posted:
        # age_text moves with the date it describes. Leaving it behind is how
        # a card ended up reading "Aug 21" over a date_posted of Sep 23.
        primary.date_posted, primary.age_text = _better_date(
            primary.date_posted, primary.age_text,
            other.date_posted, other.age_text,
        )

    if primary.category in ("", "Uncategorized") and \
            other.category not in ("", "Uncategorized"):
        primary.category = other.category

    if not primary.salary and other.salary:
        primary.salary = other.salary

    if not primary.simplify_url and other.simplify_url:
        primary.simplify_url = other.simplify_url

    # Flags are evidence: one source marking a requirement counts even when
    # another omits it.
    primary.is_faang = primary.is_faang or other.is_faang
    primary.needs_advanced_degree = (
        primary.needs_advanced_degree or other.needs_advanced_degree
    )
    primary.no_sponsorship = primary.no_sponsorship or other.no_sponsorship
    primary.citizenship_required = (
        primary.citizenship_required or other.citizenship_required
    )

    # Record every list this job appeared in. Being on several is itself mild
    # evidence the posting is real and current.
    sources = set(primary.sources or [primary.source])
    sources.update(other.sources or [other.source])
    primary.sources = sorted(s for s in sources if s)

    return primary


# ---------------------------------------------------------------------------
# Second pass: the same job listed under different words
# ---------------------------------------------------------------------------
#
# key_for() matches on (company, role), which misses a real case: two lists
# describing one job as "AI Software Engineering Intern - Edge" and "AI
# Software Engineer Intern - Edge". Those became two cards with the SAME
# apply URL, and an application was sent to both — which is exactly the
# waste APPLICATION_LIMITS exists to prevent.
#
# So a second pass merges postings that share an apply URL AND have
# equivalent role titles. Both halves are required. A shared URL alone is
# not identity: several employers point every listing at one careers page,
# and Zipline's "Software Engineer Intern" and "Computational Physics
# Intern" share theirs.
#
# The bias is deliberately toward UNDER-merging. A wrong merge hides a real
# job and does it invisibly; a missed merge shows a duplicate, which you can
# see and we can fix. So there is no "one title contains the other" rule —
# it collapsed "Software Engineer Intern, C++" into "…, Python".

_ROLE_STOPWORDS = {
    "intern", "interns", "internship", "internships",
    "summer", "fall", "winter", "spring",
    "coop", "co", "op", "program",
    "2026", "2027", "2028", "the", "a", "and", "us", "na",
}

# How much two titles must overlap to be the same job. 0.85 was measured:
# it merges the wording differences and separates every genuinely distinct
# role in the current data.
ROLE_MATCH_RATIO = 0.85


def normalize_url(url: str) -> str:
    """
    An apply URL reduced to its identity.

    Tracking parameters differ between lists for the same link, so they are
    dropped; scheme and "www." are ignored for the same reason.
    """
    if not url:
        return ""
    parts = urlsplit(url.strip())
    query = [
        (key, value) for key, value in parse_qsl(parts.query)
        if not key.lower().startswith(("utm_", "ref", "source", "gh_", "src"))
    ]
    host = parts.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    return urlunsplit(
        ("", host, parts.path.rstrip("/"), urlencode(sorted(query)), "")
    )


def _role_tokens(role: str) -> frozenset:
    """A role title as a bag of meaningful words, with variants folded."""
    text = (role or "").lower()
    text = re.sub(r"\b(engineering|engineer|eng)\b", "engineer", text)
    text = re.sub(r"\b(developer|development|dev)\b", "developer", text)
    text = re.sub(r"\b(sciences?|scientist)\b", "scientist", text)
    text = re.sub(r"[^a-z0-9+#]+", " ", text)
    return frozenset(
        word for word in text.split()
        if word and word not in _ROLE_STOPWORDS
    )


def same_role(first: str, second: str) -> bool:
    """Whether two titles describe the same job."""
    a, b = _role_tokens(first), _role_tokens(second)
    if not a or not b:
        return False
    if a == b:
        return True
    return len(a & b) / len(a | b) >= ROLE_MATCH_RATIO


def _merge_same_url(result: list) -> tuple:
    """Merge postings sharing an apply URL when their roles agree."""
    by_url = {}
    keep = []
    merged_count = 0

    for posting in result:
        url = normalize_url(posting.apply_url)
        if not url:
            keep.append(posting)
            continue

        for existing in by_url.setdefault(url, []):
            if same_role(existing.role, posting.role):
                merge(existing, posting)
                merged_count += 1
                break
        else:
            by_url[url].append(posting)
            keep.append(posting)

    return keep, merged_count


def deduplicate(postings) -> tuple:
    """
    Merge duplicates across sources.

    Returns (merged_list, stats) where stats reports how much overlap there
    was — worth surfacing, because it tells you whether a newly added source
    is earning its keep or just re-reporting what you already had.
    """
    merged = {}
    order = []
    duplicates = 0

    for posting in postings:
        key = key_for(posting)
        if not key[0] or not key[1]:
            continue     # unusable identity; skip rather than collide

        if key in merged:
            merge(merged[key], posting)
            duplicates += 1
        else:
            posting.sources = [posting.source]
            merged[key] = posting
            order.append(key)

    result = [merged[k] for k in order]

    # Second pass: the same job worded differently in two lists.
    result, url_merged = _merge_same_url(result)
    duplicates += url_merged

    multi = sum(1 for p in result if len(p.sources or []) > 1)
    stats = {
        "input": len(postings),
        "output": len(result),
        "duplicates_merged": duplicates,
        "in_multiple_sources": multi,
    }
    return result, stats
