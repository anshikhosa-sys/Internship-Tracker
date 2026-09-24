"""
Merging the same job across overlapping sources, and the stable posting id
that has to agree with it.

Ported from tests/legacy_checks.py (test_url_dedupe, test_dedupe,
test_stable_ids). None of this touches scoring, so it ported unchanged aside
from imports — dedupe.py and sources/base.py are untouched by the v1 -> v2
rebuild.
"""

from datetime import date, timedelta

from jobrank import dedupe
from jobrank.sources.base import Posting


def make_posting(n, role=None, category="Software Engineering", age_days=0):
    posted = (date.today() - timedelta(days=age_days)).isoformat()
    return Posting(
        company=f"Company{n}",
        role=role or f"Role {n} Intern",
        category=category,
        location="New York, NY",
        apply_url=f"https://example.com/{n}",
        simplify_url=f"https://simplify.jobs/p/uuid-{n}",
        source="test",
        date_posted=posted,
    )


# =============================================================================
def test_url_dedupe():
    """
    The same job, worded differently in two lists, must not become two cards.

    This shipped: Microsoft's "AI Software Engineering Intern - Edge" and
    "AI Software Engineer Intern - Edge" had the SAME apply URL and appeared
    as two postings. An application was sent to both.
    """
    # -- the URL is reduced to its identity ---------------------------------
    assert (dedupe.normalize_url("https://WWW.Example.com/job/1?utm_source=gh")
            == dedupe.normalize_url("http://example.com/job/1")), (
        "tracking parameters, scheme, www and trailing slash are ignored"
    )
    assert dedupe.normalize_url("") == "", "an empty URL has no identity"

    # -- titles that mean the same thing ------------------------------------
    same = [
        ("AI Software Engineering Intern - Edge",
         "AI Software Engineer Intern - Edge"),
        ("Software Engineer Intern", "Software Engineering Intern"),
        ("Software Developer Intern", "Software Development Intern"),
        ("Software Engineer Intern - Summer 2027",
         "Software Engineer Intern"),
    ]
    for first, second in same:
        assert dedupe.same_role(first, second), (
            f"same job: {first[:34]!r} == {second[:34]!r}"
        )

    # -- titles that do NOT, which matter more -----------------------------
    # A wrong merge HIDES a real job and does it invisibly. A missed merge
    # shows a duplicate, which is visible. So the bias is to under-merge.
    different = [
        ("Software Engineer Intern, C++",
         "Software Engineer Intern, Python"),
        ("Software Engineer Intern - Summer 2027",
         "Computational Physics Intern - Summer 2027"),
        ("Product Management Intern", "Product Management Intern, MBA"),
        ("Software Engineer Intern",
         "Software Engineer Intern - TikTok AI Search"),
        ("Data Engineer Intern", "Data Science Intern"),
    ]
    for first, second in different:
        assert not dedupe.same_role(first, second), (
            f"different jobs: {first[:30]!r} != {second[:30]!r}"
        )

    assert not dedupe.same_role("", "Software Engineer Intern"), (
        "an empty title never matches anything"
    )

    # -- the end-to-end behaviour -------------------------------------------
    url = "https://apply.example.com/careers/job/12345"
    a = make_posting(1, role="AI Software Engineering Intern - Edge")
    b = make_posting(2, role="AI Software Engineer Intern - Edge")
    c = make_posting(3, role="Computational Physics Intern")
    for posting in (a, b, c):
        posting.company = "Microsoft"
        posting.apply_url = url
    a.source, b.source, c.source = "list-one", "list-two", "list-two"

    merged, stats = dedupe.deduplicate([a, b, c])
    roles = [p.role for p in merged]
    assert len(merged) == 2, (
        f"two wordings of one job collapse to one card, and a genuinely "
        f"different role at the same URL survives ({roles})"
    )
    assert any("Computational Physics" in r for r in roles), (
        "a shared careers URL does not swallow a different job"
    )

    # A shared URL alone is not identity — several employers point every
    # listing at one careers page.
    survivor = next(p for p in merged if "Edge" in p.role)
    assert len(survivor.sources or []) == 2, (
        "the surviving card records both lists it appeared in"
    )


# =============================================================================
def test_dedupe():
    """Merging the same job across three overlapping sources."""
    a = make_posting(1, role="Software Engineer Intern - Summer 2027")
    a.source = "ListA"
    a.category = "Software Engineering"

    # Same job, different list: title noise differs, no category, has salary.
    b = make_posting(1, role="Software Engineer Intern")
    b.source = "ListB"
    b.category = "Uncategorized"
    b.salary = "$60/hr"
    b.needs_advanced_degree = True

    c = make_posting(2, role="Product Manager Intern")
    c.source = "ListC"

    merged, stats = dedupe.deduplicate([a, b, c])

    assert stats["output"] == 2, "the same job in two lists collapses to one posting"
    assert stats["duplicates_merged"] == 1, "the merge is counted"

    kept = merged[0]
    assert kept.salary == "$60/hr", "a salary from one list survives onto the merged record"
    assert kept.category == "Software Engineering", "a real category beats 'Uncategorized'"
    assert kept.needs_advanced_degree, (
        "flags are OR-ed — one list marking a requirement counts"
    )
    assert set(kept.sources) == {"ListA", "ListB"}, (
        "the merged record remembers every list it appeared in"
    )

    # Title noise must not prevent a match, and different jobs must not merge.
    assert dedupe.key_for(a) == dedupe.key_for(b), (
        "'Summer 2027' noise doesn't stop two copies matching"
    )
    assert dedupe.key_for(a) != dedupe.key_for(c), (
        "genuinely different roles keep separate identities"
    )

    # Requisition numbers differ between lists for the same job.
    d = make_posting(1, role="Software Engineer Intern JR2023492")
    assert dedupe.key_for(a) == dedupe.key_for(d), (
        "requisition numbers are stripped before matching"
    )


# =============================================================================
def test_stable_ids():
    """
    Posting identity must survive everything the sources throw at it.

    This is the most safety-critical property in the project: the id is what
    an applied mark is filed under, so an id that shifts means silent data
    loss with no error to notice.
    """
    def mk(source, location, role, company="Acme"):
        return Posting(company=company, role=role, category="x",
                       location=location, apply_url="u", source=source)

    base = mk("Simplify", "NYC", "Software Engineer Intern")
    assert base.id == mk("Vansh", "NYC", "Software Engineer Intern").id, (
        "the id does not depend on which source supplied it"
    )
    assert base.id == mk("Simplify", "New York, NY", "Software Engineer Intern").id, (
        "a location edit does not mint a new id"
    )
    assert base.id == mk(
        "Simplify", "NYC", "Software Engineer Intern - Summer 2027"
    ).id, "season noise in the title does not mint a new id"
    assert base.id == mk(
        "Simplify", "NYC", "Software Engineer Intern JR2029481"
    ).id, "a requisition number does not mint a new id"
    assert base.id != mk("Simplify", "NYC", "Product Manager Intern").id, (
        "genuinely different roles keep different ids"
    )
    assert base.id != mk(
        "Simplify", "NYC", "Software Engineer Intern", company="Globex"
    ).id, "the same role at another company keeps a different id"

    # Identity and deduplication must agree, or a job could merge under one
    # rule while its applied mark is filed under another.
    a = mk("Simplify", "NYC", "Software Engineer Intern - Summer 2027")
    b = mk("Vansh", "New York, NY", "Software Engineer Intern")
    assert (dedupe.key_for(a) == dedupe.key_for(b)) == (a.id == b.id), (
        "dedupe matching and id equality never disagree"
    )


def test_exact_date_beats_an_approximate_one_and_age_text_follows():
    """
    Merging kept `max(date_a, date_b)` while its docstring promised the EXACT
    date. Those disagree in the common direction: "1mo" rounds to a later day
    than the source that printed "Aug 21", so the approximation won and the
    posting scored fresher than it was. Worse, age_text was left on the losing
    copy, so 234 live postings displayed an age their own date_posted
    contradicted -- one read "1mo" over a date_posted of that same morning.
    """
    exact = make_posting(1)
    exact.date_posted, exact.age_text = "2026-08-21", "Aug 21"
    approx = make_posting(1)
    approx.date_posted, approx.age_text = "2026-09-05", "1mo"

    merged = dedupe.merge(exact, approx)
    assert merged.date_posted == "2026-08-21", "the exact date wins"
    assert merged.age_text == "Aug 21", "age_text moves with the date it describes"

    # And the other way round: whichever copy is primary, exactness decides.
    approx2 = make_posting(1)
    approx2.date_posted, approx2.age_text = "2026-09-05", "1mo"
    exact2 = make_posting(1)
    exact2.date_posted, exact2.age_text = "2026-08-21", "Aug 21"
    merged2 = dedupe.merge(approx2, exact2)
    assert (merged2.date_posted, merged2.age_text) == ("2026-08-21", "Aug 21")


def test_two_approximate_dates_still_keep_the_later_one():
    """Nothing to choose between them, so the previous rule stands."""
    a = make_posting(1)
    a.date_posted, a.age_text = "2026-08-21", "30d"
    b = make_posting(1)
    b.date_posted, b.age_text = "2026-09-05", "15d"
    merged = dedupe.merge(a, b)
    assert (merged.date_posted, merged.age_text) == ("2026-09-05", "15d")
