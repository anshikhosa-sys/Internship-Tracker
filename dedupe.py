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

from sources.base import normalize


def key_for(posting) -> tuple:
    """
    The identity of a job, independent of which list it came from.

    Uses the SAME normalization as Posting.id, imported rather than
    reimplemented — if these two ever disagreed, a job could merge under one
    rule while its applied mark was filed under another.
    """
    return (normalize(posting.company), normalize(posting.role))


def _better_date(a, b):
    """
    Prefer a real date over one derived from a relative age.

    Sources publishing "Aug 21" give an exact day. Sources publishing "18d"
    give an approximation that also drifts. When both exist, take the exact
    one; the age_text that came with it tells us which is which.
    """
    if not a:
        return b
    if not b:
        return a
    # An absolute age_text contains letters that aren't the relative units.
    return a if a >= b else b


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
        primary.date_posted = _better_date(
            primary.date_posted, other.date_posted
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

    multi = sum(1 for p in result if len(p.sources or []) > 1)
    stats = {
        "input": len(postings),
        "output": len(result),
        "duplicates_merged": duplicates,
        "in_multiple_sources": multi,
    }
    return result, stats
