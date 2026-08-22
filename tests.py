"""
tests.py — checks the logic that's easy to get quietly wrong.

    python3 tests.py

Throwaway databases in temp folders, no network. Running this never touches
your real internships.db.

WHY THESE TESTS EXIST
Two of them encode bugs this project actually shipped and had to fix:

  1. The NEW flag was off by one run — `first_seen >= previous_run` also
     matched postings inserted during the previous run, so they were reported
     as new twice.

  2. Scoring used to ADD a large constant for wanting a role (+150 for
     "forward deployed"), which let preference swamp everything else. A
     month-old role you couldn't realistically get outranked a fresh one that
     matched exactly. The multiplicative model replaced it, and the tests
     below pin the property that makes it work.
"""

import os
import tempfile
from datetime import date, timedelta

import config
import dedupe
import letters
import scorer
import storage
from sources.base import Posting
from sources.simplify_readme import _TableParser, _age_to_date, _cell_text


PASSED = 0


def check(condition, label):
    global PASSED
    if condition:
        PASSED += 1
        print(f"  PASS  {label}")
    else:
        print(f"  FAIL  {label}")
        raise AssertionError(label)


def make_posting(n, role=None, category="Software Engineering",
                 age_days=0, faang=False, advanced_degree=False):
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
        is_faang=faang,
        needs_advanced_degree=advanced_degree,
    )


# =============================================================================
def test_parser():
    print("\nPARSER")

    html = """
    <table><tbody>
    <tr>
      <td><strong><a href="https://simplify.jobs/c/Acme">Acme</a></strong></td>
      <td>Solutions Engineer Intern</td>
      <td>Austin, TX</td>
      <td><a href="https://acme.com/apply">A</a>
          <a href="https://simplify.jobs/p/abc-123">S</a></td>
      <td>3d</td>
    </tr>
    <tr>
      <td>↳</td>
      <td>Product Manager Intern</td>
      <td><details><summary><strong>2 locations</strong></summary>
          Seattle, WA<br>Remote</details></td>
      <td><a href="https://acme.com/apply2">A</a>
          <a href="https://simplify.jobs/p/def-456">S</a></td>
      <td>1mo</td>
    </tr>
    </tbody></table>
    """

    parser = _TableParser()
    parser.feed(html)
    rows = parser.rows

    check(len(rows) == 2, "reads both rows")
    check(_cell_text(rows[0][0]) == "Acme", "reads the company name")
    check(_cell_text(rows[1][0]) == "↳", "continuation marker reaches the row")

    location = _cell_text(rows[1][2])
    check("locations" not in location,
          "drops the '2 locations' summary label")
    check(location == "Seattle, WA | Remote",
          f"joins multiple locations cleanly (got {location!r})")
    check(any("simplify.jobs/p/" in link for link in rows[0][3]["links"]),
          "captures the Simplify posting URL")

    print("\nAGE PARSING")
    from datetime import datetime, timezone
    now = datetime(2026, 8, 21, tzinfo=timezone.utc)
    check(_age_to_date("0d", now) == "2026-08-21", "'0d' is today")
    check(_age_to_date("3d", now) == "2026-08-18", "'3d' is three days ago")
    check(_age_to_date("1mo", now) == "2026-07-22", "'1mo' is ~30 days ago")
    check(_age_to_date("", now) is None, "empty age returns None, not a guess")
    check(_age_to_date("???", now) is None, "unparseable age returns None")


# =============================================================================
def test_preference():
    print("\nPREFERENCE — do you want it")

    def pref(role, category="Software Engineering"):
        return scorer.preference(make_posting(1, role=role,
                                              category=category))[0]

    # Whole-word matching: "ai" must not fire inside other words.
    check(pref("Email Platform Intern") < pref("AI Platform Intern"),
          "'Email' does not collect the AI lift (whole-word matching)")
    check(pref("Training Program Intern") < pref("AI Engineer Intern"),
          "'Training' does not collect the AI lift")

    # Family ordering.
    check(pref("Forward Deployed Engineer Intern") >
          pref("Product Manager Intern") >
          pref("Software Engineer Intern"),
          "role families rank in the intended order")

    # Focus lift is what separates good SWE roles from generic ones.
    generic = pref("Software Engineer Intern")
    focused = pref("Software Engineer Intern, Data Platform")
    check(focused > generic,
          f"a data-platform SWE role ({focused}) beats a generic one "
          f"({generic})")
    check(generic == config.ROLE_FAMILIES[-1]["preference"],
          "a bare SWE title sits at the family floor")

    # Only the best family counts, so "Solutions Engineer" isn't also paid
    # for matching the generic "engineer" family.
    check(pref("Solutions Engineer Intern") <= 1.0,
          "preference never exceeds 1.0")

    # Out-of-scope multiplies down rather than subtracting.
    check(pref("Quantitative Trading Intern") < 0.2,
          "out-of-scope roles are worth a fraction of a real match")


# =============================================================================
def test_candidacy():
    print("\nCANDIDACY — would they take you")

    def cand(role, category="Software Engineering", **kwargs):
        return scorer.candidacy(
            make_posting(1, role=role, category=category, **kwargs)
        )[0]

    # An uncategorized posting with no skill keywords is the true baseline —
    # a categorized one now picks up the category signal on top.
    baseline = cand("Analyst Intern", category="Uncategorized")
    check(baseline == config.CANDIDACY_BASELINE,
          "an unremarkable, uncategorized title sits at the baseline")

    check(cand("Data Pipeline Engineer Intern") > baseline,
          "a title naming proven experience raises candidacy")

    # Blockers must sink a posting even when it otherwise looks perfect.
    check(cand("Machine Learning PhD Research Intern") < 0.15,
          "a PhD requirement sinks candidacy")
    check(cand("Senior Software Engineer") < baseline / 2,
          "a senior title sinks candidacy")
    check(cand("Applied AI Engineer Intern") > 0,
          "a normal role keeps a usable candidacy value")
    # Compare like with like: these must be measured against the same role
    # in the same category, or the category signal swamps the effect.
    plain = cand("Software Engineer Intern")
    check(cand("Software Engineer Intern", advanced_degree=True) < plain,
          "the advanced-degree marker lowers candidacy")
    check(cand("Software Engineer Intern", faang=True) < plain,
          "a highly competitive employer lowers candidacy slightly")

    check(0.0 <= cand("Compiler Engineer Intern") <= 1.0,
          "candidacy stays within 0-1")


# =============================================================================
def test_freshness():
    print("\nFRESHNESS — is it still open")

    check(scorer.freshness(0)[0] == 1.0,
          "a posting from today is undiscounted")
    check(scorer.freshness(0)[0] > scorer.freshness(3)[0] >
          scorer.freshness(14)[0] > scorer.freshness(60)[0],
          "freshness decreases monotonically with age")

    # The floor matters: recruiters are consistent that old postings are
    # still worth applying to, so nothing should ever reach zero.
    check(scorer.freshness(9999)[0] > 0,
          "a very old posting still scores above zero")

    check(scorer.freshness(None)[0] == config.UNKNOWN_AGE_FRESHNESS,
          "unknown age is treated as middling, not penalized")
    check(scorer.freshness(0)[1] == "Posted today", "today is labelled")
    check(scorer.freshness(1)[1] == "Posted yesterday",
          "yesterday is labelled")

    # The hard cutoff hides rather than ranks, so its boundary is worth
    # pinning: off by one here silently loses a day of postings.
    cutoff = config.MAX_AGE_DAYS
    check(cutoff > 0, "the age cutoff is a positive number of days")
    check(scorer.freshness(cutoff)[0] > scorer.freshness(cutoff + 1)[0],
          "freshness still falls across the cutoff boundary")
    check(config.FRESH_DAYS <= cutoff,
          "the 'fresh' badge window fits inside the cutoff")

    check(scorer.days_old(make_posting(1, age_days=5)) == 5,
          "age is derived from the posted date")
    check(scorer.days_old(Posting(company="x", role="y", category="z",
                                  location="l", apply_url="u")) is None,
          "a missing date yields None rather than a guess")


# =============================================================================
def test_scoring_model():
    """The properties that make the multiplicative model work."""
    print("\nSCORING MODEL")

    def score(role, age_days=0, **kwargs):
        return scorer.score_posting(
            make_posting(1, role=role, age_days=age_days, **kwargs)
        )["score"]

    # THE REGRESSION THAT MATTERS. The old additive model ranked a month-old
    # dream role above a fresh, well-matched one. This is the exact case that
    # made the tool useless, so it gets a test.
    stale_dream = score("Forward Deployed Engineer Intern", age_days=30)
    fresh_match = score("Software Engineer Intern, Data Platform", age_days=0)
    check(fresh_match > stale_dream,
          f"a fresh matching role ({fresh_match}) beats a stale dream role "
          f"({stale_dream}) — the bug this model replaced")

    # But the dream role must still be VISIBLE. Sinking it to zero would be
    # the opposite failure.
    check(stale_dream > 0,
          "the stale dream role still scores above zero")

    # All three factors have to matter. Hold two fixed, vary the third.
    check(score("Forward Deployed Engineer Intern", age_days=0) >
          score("Forward Deployed Engineer Intern", age_days=30),
          "age changes the score when preference and candidacy are fixed")
    check(score("Software Engineer Intern", age_days=0) >
          score("Software Engineer Intern", age_days=0,
                advanced_degree=True),
          "candidacy changes the score when the other two are fixed")
    check(score("Product Manager Intern", age_days=0) >
          score("Software Engineer Intern", age_days=0),
          "preference changes the score when the other two are fixed")

    # A near-zero in any factor should sink the whole thing — that's the
    # entire reason for multiplying rather than adding.
    check(score("Quantitative Trading PhD Intern", age_days=0) < 5,
          "a posting that fails every test scores near zero")

    check(0 <= score("Software Engineer Intern") <= 100,
          "scores stay within 0-100")

    # Bands must actually be reachable, or the badges are decorative.
    best = score(
        "Solutions Engineer Intern, Data Platform", age_days=0
    )
    check(best >= config.STRONG_FIT_THRESHOLD,
          f"a near-ideal posting ({best}) can reach the STRONG band "
          f"({config.STRONG_FIT_THRESHOLD})")

    print("\nCO-OP DETECTION")
    check(scorer.is_coop(make_posting(1, role="SWE Intern/Co-op")),
          "an Intern/Co-op title is flagged")
    check(scorer.is_coop(make_posting(1, role="Data Enablement Co-op")),
          "a plain co-op title is flagged")
    check(not scorer.is_coop(make_posting(1, role="Software Engineer Intern")),
          "an ordinary internship is not flagged")
    # A label, not a filter: co-ops must still score normally, because many
    # "Intern/Co-op" listings are ordinary summer internships.
    check(scorer.score_posting(
        make_posting(1, role="Software Engineer Intern/Co-op")
    )["score"] > 0, "a co-op still scores rather than being zeroed")

    print("\nOFF-SEASON DETECTION")
    winter = make_posting(1, role="SWE Intern - Winter 2027")
    check(scorer.is_off_season(winter), "a winter role is flagged")
    check(scorer.is_off_season(make_posting(1, role="SWE Intern - Fall 2026")),
          "a fall role is flagged")
    check(not scorer.is_off_season(
        make_posting(1, role="Software Engineer Intern - Summer 2027")),
        "a summer role is not flagged")
    # Matched on the TITLE only — a company called Winter shouldn't be caught.
    p = make_posting(1, role="Software Engineer Intern")
    p.company = "Winter Capital"
    check(not scorer.is_off_season(p),
          "a company name containing 'winter' is not flagged")
    # A label, not a score input.
    check(scorer.score_posting(
        make_posting(1, role="Software Engineer Intern - Winter 2027")
    )["score"] > 0, "an off-season role still scores rather than being zeroed")

    print("\nRESUME-GROUNDED CANDIDACY")
    original = config.PROFILE_PATH
    try:
        # A profile mentioning Python but not Kubernetes.
        path = os.path.join(tempfile.mkdtemp(), "p.md")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("Built Python data pipelines on AWS with Postgres. " * 8)
        config.PROFILE_PATH = path
        scorer._PROFILE_CACHE["path"] = None       # bust the cache

        supported = scorer.candidacy(
            make_posting(1, role="Python Backend Engineer Intern"))[0]
        unsupported = scorer.candidacy(
            make_posting(1, role="Kubernetes Platform Engineer Intern"))[0]
        check(supported > unsupported,
              "a title matching the resume beats one naming tech it lacks")
        check(scorer.profile_supports("python"),
              "a term in the resume is recognized")
        check(not scorer.profile_supports("kubernetes"),
              "a term absent from the resume is not")
    finally:
        config.PROFILE_PATH = original
        scorer._PROFILE_CACHE["path"] = None

    print("\nTRADING FIRMS CAUGHT BY NAME")
    dv = make_posting(1, role="Software Engineer Intern, Commodities")
    dv.company = "DV Group"
    ordinary = make_posting(2, role="Software Engineer Intern")
    check(scorer.preference(dv)[0] < scorer.preference(ordinary)[0],
          "a trading firm's SWE role scores below an ordinary one")

    print("\nPHONE PUSH")
    import push
    check(push.is_configured() is False or bool(config.PUSH_TOPIC),
          "push reports configured only when a topic is set")
    # Must never raise from a background job, even with a bad topic.
    original_topic = config.PUSH_TOPIC
    try:
        config.PUSH_TOPIC = ""
        check(push.notify_matches([make_posting(1)]) is False,
              "no topic configured means no push, and no error")
    finally:
        config.PUSH_TOPIC = original_topic

    print("\nCOMPANY TIER AND FRESHNESS")
    faang = make_posting(1, faang=True)
    check(scorer.company_tier(faang) == "big",
          "the competitive marker makes a company big-tier")
    check(scorer.company_tier(make_posting(2), company_volume=100) == "big",
          "a company posting 100 roles is big-tier")
    check(scorer.company_tier(make_posting(3), company_volume=10) == "mid",
          "a company posting 10 roles is mid-tier")
    check(scorer.company_tier(make_posting(4), company_volume=1) == "niche",
          "a company posting 1 role is niche")

    # The whole point: the same age means different things per tier.
    for age in (1, 3, 7):
        big = scorer.freshness(age, "big")[0]
        niche = scorer.freshness(age, "niche")[0]
        check(niche > big,
              f"at {age}d a niche role is fresher than a big-tech one "
              f"({niche:.2f} vs {big:.2f})")

    check(scorer.freshness(0, "big")[0] == scorer.freshness(0, "niche")[0],
          "on day zero every tier is undiscounted")
    # An unknown tier must fall back, not crash.
    check(scorer.freshness(3, "nonsense")[0] > 0,
          "an unrecognized tier falls back to the default curve")

    print("\nINTERNSHIP GATE")
    intern = make_posting(1, role="Software Engineer Intern")
    check(scorer.is_internship(intern), "an intern role passes the gate")
    check(not scorer.is_internship(make_posting(1, role="Staff Engineer")),
          "a full-time role is filtered out")


# =============================================================================
def test_weighting():
    """The exponents that make candidacy, not preference, drive the score."""
    print("\nSCORE WEIGHTING")

    w = config.SCORE_WEIGHTS
    check(w["candidacy"] >= w["preference"],
          "candidacy is weighted at least as heavily as preference")
    check(w["preference"] < 1.0,
          "preference is compressed rather than counted in full")

    # A weight below 1 must actually compress the spread.
    lo = scorer.final_score(0.5, 0.8, 1.0)
    hi = scorer.final_score(1.0, 0.8, 1.0)
    spread_pref = hi - lo
    lo_c = scorer.final_score(0.8, 0.5, 1.0)
    hi_c = scorer.final_score(0.8, 1.0, 1.0)
    spread_cand = hi_c - lo_c
    check(spread_cand > spread_pref,
          f"the same 0.5-1.0 swing moves the score more via candidacy "
          f"({spread_cand}) than preference ({spread_pref})")

    # A zero anywhere must still sink the result. Python's 0 ** 0 == 1, so
    # this would silently become a perfect score without an explicit guard.
    check(scorer.final_score(0.0, 1.0, 1.0) == 0,
          "zero preference still yields zero, despite the exponent")
    check(scorer.final_score(1.0, 0.0, 1.0) == 0, "zero candidacy yields zero")
    check(scorer.final_score(1.0, 1.0, 0.0) == 0, "zero freshness yields zero")

    check(scorer.final_score(1.0, 1.0, 1.0) == 100,
          "a perfect posting scores 100")


# =============================================================================
def test_candidacy_signals():
    """Candidacy has to VARY to deserve the weight it carries."""
    print("\nCANDIDACY SIGNALS")

    swe = make_posting(1, role="Software Engineer Intern",
                       category="Software Engineering")
    pm = make_posting(2, role="Software Engineer Intern",
                      category="Product Management")
    check(scorer.candidacy(swe)[0] > scorer.candidacy(pm)[0],
          "two SWE internships support a SWE role more than a PM one")

    # Hiring volume: a company taking 100 interns is not a one-ticket lottery.
    small = scorer.candidacy(swe, company_volume=1)[0]
    large = scorer.candidacy(swe, company_volume=100)[0]
    check(large > small,
          f"a company posting 100 roles scores higher ({large}) than one "
          f"posting 1 ({small})")

    check(scorer.company_volumes(
        [make_posting(1), make_posting(1), make_posting(2)]
    )["company1"] == 2, "company volumes are counted across the list")


# =============================================================================
def test_dedupe():
    """Merging the same job across three overlapping sources."""
    print("\nDEDUPLICATION")

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

    check(stats["output"] == 2,
          "the same job in two lists collapses to one posting")
    check(stats["duplicates_merged"] == 1, "the merge is counted")

    kept = merged[0]
    check(kept.salary == "$60/hr",
          "a salary from one list survives onto the merged record")
    check(kept.category == "Software Engineering",
          "a real category beats 'Uncategorized'")
    check(kept.needs_advanced_degree,
          "flags are OR-ed — one list marking a requirement counts")
    check(set(kept.sources) == {"ListA", "ListB"},
          "the merged record remembers every list it appeared in")

    # Title noise must not prevent a match, and different jobs must not merge.
    check(dedupe.key_for(a) == dedupe.key_for(b),
          "'Summer 2027' noise doesn't stop two copies matching")
    check(dedupe.key_for(a) != dedupe.key_for(c),
          "genuinely different roles keep separate identities")

    # Requisition numbers differ between lists for the same job.
    d = make_posting(1, role="Software Engineer Intern JR2023492")
    check(dedupe.key_for(a) == dedupe.key_for(d),
          "requisition numbers are stripped before matching")


# =============================================================================
def test_markdown_sources():
    """The markdown table reader the two newer sources share."""
    print("\nMARKDOWN TABLE PARSING")

    from sources import markdown_table as md

    # Built from parts to keep the source lines short; the reader only
    # cares that each row is a pipe-delimited line.
    header = "| Company | Role | Location | Link | Date Posted |"
    sep = "| --- | --- | --- | --- | --- |"
    row1 = ('| Acme | Solutions Engineer Intern | TX '
            '| <a href="https://a.com/x">A</a> | Aug 21 |')
    row2 = ('| \u21b3 | Product Manager Intern | Remote '
            '| <a href="https://a.com/y">A</a> | Aug 20 |')
    table = "\n".join([header, sep, row1, row2])
    rows = md.parse_rows(table)
    check(len(rows) == 2, "reads the data rows and skips header/separator")
    check(rows[0][0]["text"] == "Acme", "strips HTML to the visible text")
    check(rows[0][3]["links"] == ["https://a.com/x"],
          "pulls the href out of a cell")

    company, last = md.resolve_company(rows[0][0]["text"], "")
    check(company == "Acme", "reads a real company name")
    company2, _ = md.resolve_company(rows[1][0]["text"], last)
    check(company2 == "Acme", "the arrow inherits the company above it")

    today = date(2026, 8, 22)
    check(md.parse_absolute_date("Aug 21", today) == "2026-08-21",
          "an absolute date parses without approximation")
    # A date that would be in the future must belong to last year.
    check(md.parse_absolute_date("Dec 15", today) == "2025-12-15",
          "a future-looking date rolls back a year")
    check(md.parse_relative_age("3d", today) == "2026-08-19",
          "a relative age still parses")
    check(md.parse_absolute_date("", today) is None,
          "an empty date returns None rather than guessing")


# =============================================================================
def test_stable_ids():
    """
    Posting identity must survive everything the sources throw at it.

    This is the most safety-critical property in the project: the id is what
    an applied mark is filed under, so an id that shifts means silent data
    loss with no error to notice.
    """
    print("\nSTABLE POSTING IDS")

    def mk(source, location, role, company="Acme"):
        return Posting(company=company, role=role, category="x",
                       location=location, apply_url="u", source=source)

    base = mk("Simplify", "NYC", "Software Engineer Intern")
    check(base.id == mk("Vansh", "NYC", "Software Engineer Intern").id,
          "the id does not depend on which source supplied it")
    check(base.id == mk("Simplify", "New York, NY",
                        "Software Engineer Intern").id,
          "a location edit does not mint a new id")
    check(base.id == mk("Simplify", "NYC",
                        "Software Engineer Intern - Summer 2027").id,
          "season noise in the title does not mint a new id")
    check(base.id == mk("Simplify", "NYC",
                        "Software Engineer Intern JR2029481").id,
          "a requisition number does not mint a new id")
    check(base.id != mk("Simplify", "NYC", "Product Manager Intern").id,
          "genuinely different roles keep different ids")
    check(base.id != mk("Simplify", "NYC", "Software Engineer Intern",
                        company="Globex").id,
          "the same role at another company keeps a different id")

    # Identity and deduplication must agree, or a job could merge under one
    # rule while its applied mark is filed under another.
    a = mk("Simplify", "NYC", "Software Engineer Intern - Summer 2027")
    b = mk("Vansh", "New York, NY", "Software Engineer Intern")
    check((dedupe.key_for(a) == dedupe.key_for(b)) == (a.id == b.id),
          "dedupe matching and id equality never disagree")


# =============================================================================
def test_applied_marks_survive():
    """Applied marks are the only unrecoverable data here."""
    print("\nAPPLIED MARKS SURVIVE")

    db_path = os.path.join(tempfile.mkdtemp(), "marks.db")
    conn = storage.connect(db_path)

    posting = make_posting(1, role="Software Engineer Intern")
    scorer.score_all([posting])
    storage.save_postings(conn, [posting], "2026-08-01T00:00:00+00:00")
    storage.record_run(conn, "2026-08-01T00:00:00+00:00", 1, 0)
    storage.set_applied(conn, posting.id, True)

    # The mark records company and role as a recovery key.
    row = conn.execute(
        "SELECT company, role FROM applications WHERE posting_id = ?",
        (posting.id,),
    ).fetchone()
    check(row["company"] == posting.company,
          "an applied mark stores the company as a recovery key")
    check(row["role"] == posting.role,
          "an applied mark stores the role as a recovery key")

    # Simulate an id that moved: file the mark under a stale id.
    conn.execute("DELETE FROM applications")
    conn.execute(
        "INSERT INTO applications (posting_id, applied, updated_at, "
        "company, role) VALUES (?,?,?,?,?)",
        ("stale:id", 1, "2026-08-01T00:00:00+00:00",
         posting.company, posting.role),
    )
    conn.commit()
    check(storage.applied_count(conn) == 1, "the stale mark exists")

    recovered = storage.reattach_orphaned_marks(conn)
    check(recovered == 1, "an orphaned mark is recovered")
    rows = {r["id"]: r for r in storage.load_postings(conn)}
    check(rows[posting.id]["applied"] == 1,
          "the recovered mark is re-attached to the live posting")
    check(storage.applied_count(conn) == 1,
          "recovery does not duplicate the mark")

    conn.close()


# =============================================================================
def test_migration():
    """A schema change must never require deleting the database."""
    print("\nSCHEMA MIGRATION")

    import sqlite3
    db_path = os.path.join(tempfile.mkdtemp(), "old.db")

    # An old database: postings without the columns added later.
    old = sqlite3.connect(db_path)
    old.execute("CREATE TABLE postings (id TEXT PRIMARY KEY, company TEXT)")
    old.execute("CREATE TABLE applications (posting_id TEXT PRIMARY KEY, "
                "applied INTEGER, notes TEXT, updated_at TEXT)")
    old.execute("INSERT INTO applications VALUES ('x', 1, '', 'then')")
    old.commit()
    old.close()

    conn = storage.connect(db_path)
    columns = {r["name"] for r in conn.execute("PRAGMA table_info(postings)")}
    check("salary" in columns, "a missing column is added by migration")
    check("candidacy_score" in columns, "every new column is added")

    app_cols = {
        r["name"] for r in conn.execute("PRAGMA table_info(applications)")
    }
    check("company" in app_cols, "the applications table migrates too")

    check(storage.applied_count(conn) == 1,
          "existing applied marks survive the migration")
    conn.close()


# =============================================================================
def test_defaults():
    """The dashboard opens with the settings that were asked for."""
    print("\nDASHBOARD DEFAULTS")

    check(config.DEFAULT_SORT == "candidacy",
          "opens sorted by strongest candidate")
    check(config.DEFAULT_WITHIN_DAYS == 0, "opens showing today only")
    check(config.DEFAULT_HIDE_COOP is True, "opens with co-ops hidden")

    import app as dashboard
    # A bare load uses the defaults.
    check(dashboard._effective_hide_coop({}) is True,
          "a bare page load hides co-ops")
    check(dashboard._effective_within({}) == "0",
          "a bare page load shows today only")

    # A submitted form with the box unticked must actually untick it. This
    # is the case a naive implementation gets wrong, because an unchecked
    # HTML checkbox sends nothing at all.
    check(dashboard._effective_hide_coop({"f": "1"}) is False,
          "unticking Hide co-ops is respected, not overridden by the default")
    check(dashboard._effective_hide_coop({"f": "1", "nocoop": "1"}) is True,
          "leaving it ticked is respected")


# =============================================================================
def test_pipeline():
    """Application stages, notes, and the backfill from the old boolean."""
    print("\nAPPLICATION PIPELINE")

    db_path = os.path.join(tempfile.mkdtemp(), "pipe.db")
    conn = storage.connect(db_path)

    posting = make_posting(1, role="Software Engineer Intern")
    scorer.score_all([posting])
    storage.save_postings(conn, [posting], "2026-08-01T00:00:00+00:00")
    storage.record_run(conn, "2026-08-01T00:00:00+00:00", 1, 0)

    storage.set_status(conn, posting.id, "applied")
    check(storage.pipeline_counts(conn) == {"applied": 1},
          "setting a stage puts the application in the pipeline")

    rows = storage.pipeline(conn)
    check(len(rows) == 1, "the pipeline lists it")
    check(rows[0]["days_waiting"] == 0, "days waiting starts at zero")

    first_applied_at = conn.execute(
        "SELECT applied_at FROM applications WHERE posting_id = ?",
        (posting.id,),
    ).fetchone()["applied_at"]

    # Moving stage must NOT reset the clock — "how long have I been waiting"
    # has to survive a stage change or the tracker can't spot stalled ones.
    storage.set_status(conn, posting.id, "interview")
    still = conn.execute(
        "SELECT applied_at FROM applications WHERE posting_id = ?",
        (posting.id,),
    ).fetchone()["applied_at"]
    check(still == first_applied_at,
          "applied_at is stamped once and survives a stage change")
    check(storage.pipeline_counts(conn) == {"interview": 1},
          "the stage moved")

    # Back to not-applied.
    storage.set_status(conn, posting.id, "")
    check(storage.pipeline_counts(conn) == {},
          "clearing the stage removes it from the pipeline")
    check(storage.applied_count(conn) == 0,
          "the applied boolean stays in sync with the stage")

    # An unknown stage must be rejected, not silently stored.
    try:
        storage.set_status(conn, posting.id, "nonsense")
        check(False, "an unknown stage is rejected")
    except ValueError:
        check(True, "an unknown stage raises rather than storing junk")

    storage.set_notes(conn, posting.id, "Recruiter: Dana. OA due Friday.")
    saved = storage.load_postings(conn)[0]["notes"]
    check(saved == "Recruiter: Dana. OA due Friday.", "notes round-trip")

    conn.close()

    print("\nBACKFILL FROM THE OLD BOOLEAN")
    # An application marked under the old applied=1 scheme, with no stage.
    # Without a backfill it would vanish from the pipeline view while still
    # sitting in the database — loss with no error to investigate.
    import sqlite3
    old_path = os.path.join(tempfile.mkdtemp(), "legacy.db")
    old = sqlite3.connect(old_path)
    old.execute("CREATE TABLE applications (posting_id TEXT PRIMARY KEY, "
                "applied INTEGER, notes TEXT, updated_at TEXT)")
    old.execute("INSERT INTO applications VALUES "
                "('job:abc', 1, '', '2026-08-01T00:00:00+00:00')")
    old.commit()
    old.close()

    conn = storage.connect(old_path)
    counts = storage.pipeline_counts(conn)
    check(counts.get("applied") == 1,
          "an old applied=1 mark is backfilled to the 'applied' stage")
    row = conn.execute(
        "SELECT applied_at FROM applications WHERE posting_id = 'job:abc'"
    ).fetchone()
    check(row["applied_at"] == "2026-08-01T00:00:00+00:00",
          "applied_at is backfilled from the last update time")
    conn.close()


# =============================================================================
def test_applied_always_visible():
    """
    A posting you've applied to must never be filtered out of the list.

    Reported as "I lose the status of ones I've applied to when I refresh".
    The mark was never lost — the posting aged past the "posted today only"
    filter and vanished from view, which looks identical to data loss.
    """
    print("\nAPPLIED POSTINGS STAY VISIBLE")

    import app as dashboard

    def row(status="", age_days=5, score=80, coop=False, off=False):
        return {
            "id": "job:x", "company": "Acme", "role": "SWE Intern",
            "location": "NYC", "category": "Software Engineering",
            "status": status, "applied": bool(status), "fit_score": score,
            "age_days": age_days, "is_fresh": age_days <= 3,
            "is_coop": coop, "is_off_season": off,
            "company_tier": "mid",
        }

    # Bare load = today only. A 5-day-old unapplied posting is filtered out.
    unapplied = dashboard._filtered([row()], set(), {})
    check(len(unapplied) == 0,
          "an old unapplied posting is hidden by the age filter")

    # The same posting, applied, must survive.
    applied = dashboard._filtered([row(status="applied")], set(), {})
    check(len(applied) == 1,
          "an old APPLIED posting stays visible despite the age filter")

    # And survive every other discovery filter too.
    check(len(dashboard._filtered(
        [row(status="applied", score=1)], set(), {})) == 1,
        "a low-scoring applied posting stays visible")
    check(len(dashboard._filtered(
        [row(status="applied", coop=True)], set(), {})) == 1,
        "an applied co-op stays visible")
    check(len(dashboard._filtered(
        [row(status="applied", off=True)], set(), {})) == 1,
        "an applied off-season role stays visible")
    check(len(dashboard._filtered(
        [row(status="applied", age_days=400)], set(), {})) == 1,
        "an applied posting past the hard cutoff stays visible")

    # But a deliberate narrowing must still work — otherwise you could never
    # filter down to just the new ones.
    only_new = dashboard._filtered(
        [row(status="applied")], set(), {"f": "1", "status": "new"})
    check(len(only_new) == 0,
          "an explicit status filter still applies to applied postings")


# =============================================================================
def test_storage():
    print("\nSTORAGE: the NEW flag across runs")

    db_path = os.path.join(tempfile.mkdtemp(), "test.db")
    conn = storage.connect(db_path)

    def run(postings, timestamp):
        scorer.score_all(postings)
        result = storage.save_postings(conn, postings, timestamp)
        storage.record_run(conn, timestamp, result["total"],
                           len(result["new_ids"]))
        return result

    r1 = run([make_posting(1), make_posting(2), make_posting(3)],
             "2026-08-01T00:00:00+00:00")
    check(r1["is_first_run"], "first run is recognized as a baseline")
    check(not r1["new_ids"], "baseline reports nothing as NEW")

    storage.set_applied(conn, make_posting(2).id, True)

    r2 = run([make_posting(1), make_posting(2), make_posting(4)],
             "2026-08-02T00:00:00+00:00")
    check(r2["new_ids"] == {make_posting(4).id},
          "only the genuinely new posting is flagged")
    check(r2["deactivated"] == 1,
          "a posting that dropped off the source is marked inactive")

    # Regression: the off-by-one-run bug.
    r3 = run([make_posting(1), make_posting(2), make_posting(4)],
             "2026-08-03T00:00:00+00:00")
    check(not r3["new_ids"],
          "last run's new posting is NOT flagged again (regression)")

    r4 = run([make_posting(1), make_posting(2), make_posting(4),
              make_posting(3)], "2026-08-04T00:00:00+00:00")
    check(not r4["new_ids"], "a returning posting is not counted as new")

    print("\nSTORAGE: your data survives refreshes")
    rows = {row["id"]: row for row in storage.load_postings(conn)}
    check(storage.applied_count(conn) == 1,
          "applied mark survived three refreshes")
    check(rows[make_posting(2).id]["applied"] == 1,
          "the right posting is still marked applied")
    check(rows[make_posting(1).id]["first_seen"].startswith("2026-08-01"),
          "first_seen is preserved, not overwritten each run")
    check(len(storage.load_postings(conn)) == 4,
          "a returning posting becomes active again")

    print("\nSTORAGE: the score components round-trip")
    row = rows[make_posting(1).id]
    check(isinstance(row["preference"], float),
          "preference is stored as a number")
    check(isinstance(row["preference_reasons"], list),
          "reason lists are decoded from JSON on the way out")
    check("role_family" in row, "the role family is stored for the prompts")

    conn.close()


# =============================================================================
def test_visit_tracking():
    print("\nVISIT TRACKING: what counts as new to you")

    db_path = os.path.join(tempfile.mkdtemp(), "visits.db")
    conn = storage.connect(db_path)

    live = []

    def add(posting, when):
        live.append(posting)
        scorer.score_all(live)
        result = storage.save_postings(conn, live, when)
        storage.record_run(conn, when, result["total"],
                           len(result["new_ids"]))

    add(make_posting(1), "2026-08-01T09:00:00+00:00")
    add(make_posting(2), "2026-08-01T09:00:00+00:00")

    basis = storage.register_visit(conn)
    check(storage.new_since_last_visit(conn, basis) == set(),
          "first ever visit badges nothing")

    basis = storage.register_visit(conn)
    check(storage.new_since_last_visit(conn, basis) == set(),
          "reloading during a visit is stable")

    add(make_posting(3), "2026-08-02T08:00:00+00:00")
    storage._set_state(conn, "last_activity", "2026-08-01T09:05:00+00:00")
    conn.commit()

    basis = storage.register_visit(conn)
    check(storage.new_since_last_visit(conn, basis) == {make_posting(3).id},
          "a posting that arrived since your last visit is badged")

    basis = storage.register_visit(conn)
    check(storage.new_since_last_visit(conn, basis) == {make_posting(3).id},
          "badges persist across reloads within one visit")

    # Several days away must not lose anything — the reason this exists.
    add(make_posting(4), "2026-08-03T08:00:00+00:00")
    add(make_posting(5), "2026-08-04T08:00:00+00:00")
    storage._set_state(conn, "last_activity", "2026-08-02T08:10:00+00:00")
    conn.commit()

    basis = storage.register_visit(conn)
    check(storage.new_since_last_visit(conn, basis) ==
          {make_posting(4).id, make_posting(5).id},
          "several days away still surfaces every posting since your visit")
    check(storage.new_posting_ids(conn) == {make_posting(5).id},
          "...whereas 'new since last run' would only show the latest day")

    storage.mark_all_seen(conn)
    check(storage.new_since_last_visit(
        conn, storage._get_state(conn, "visit_basis")) == set(),
        "'Mark all as seen' clears every badge")

    storage._set_state(conn, "last_activity", "not-a-timestamp")
    conn.commit()
    storage.register_visit(conn)
    check(True, "an unparseable timestamp is handled without crashing")

    conn.close()


# =============================================================================
def test_prompts():
    print("\nPROMPTS")

    posting = {
        "company": "Acme", "role": "Solutions Engineer Intern",
        "category": "Software Engineering", "location": "Austin, TX",
        "apply_url": "https://acme.com/apply", "age_text": "2d",
        "role_family": "Forward-Deployed / Solutions",
    }
    profile = "MY-UNIQUE-PROFILE-MARKER"

    cover = letters.cover_letter_prompt(posting, profile)
    experience = letters.work_experience_prompt(posting, profile)

    pairs = (("cover letter", cover), ("work experience", experience))
    for name, text in pairs:
        flat = " ".join(text.split())
        check("MY-UNIQUE-PROFILE-MARKER" in text,
              f"the {name} prompt carries the profile")
        check("Acme" in text and "Solutions Engineer Intern" in text,
              f"the {name} prompt carries the posting")
        check("Use only what the candidate profile below states" in flat,
              f"the {name} prompt keeps the honesty rule")
        check("do not invent requirements that were never stated" in flat,
              f"the {name} prompt forbids inventing requirements")

    # The whole point of role families: different documents for different
    # audiences, from the same profile.
    fde = letters.cover_letter_prompt(posting, profile)
    swe_posting = dict(posting, role_family="Software Engineering")
    swe = letters.cover_letter_prompt(swe_posting, profile)
    check(fde != swe,
          "an FDE letter prompt differs from a SWE one")
    check("customers" in fde and "customers" not in swe,
          "the FDE prompt asks for customer evidence; the SWE one doesn't")
    check("hardest engineering problem" in swe,
          "the SWE prompt asks for technical depth")

    # An unknown family must still produce a usable prompt.
    unknown = letters.cover_letter_prompt(
        dict(posting, role_family="Nonexistent"), profile)
    check(len(unknown) > 500, "an unrecognized family falls back gracefully")

    # The pasted job description is the biggest quality lever available.
    with_jd = letters.cover_letter_prompt(
        posting, profile, job_description="Must know Kubernetes and Go.")
    check("Kubernetes" in with_jd,
          "a pasted job description reaches the prompt")
    check("prefer it over inference" in " ".join(with_jd.split()),
          "the prompt says to prefer the real description over the title")
    without = letters.cover_letter_prompt(posting, profile)
    check("No job description was pasted" in without,
          "the prompt says when it's working from the title alone")

    # These prompts are free by design — nothing should reach for an API.
    source = open("letters.py", encoding="utf-8").read()
    check("anthropic" not in source.lower(),
          "letters.py makes no API calls and costs nothing")

    print("\nPROFILE LOADING")
    original = config.PROFILE_PATH
    try:
        config.PROFILE_PATH = os.path.join(tempfile.mkdtemp(), "missing.md")
        try:
            letters.load_profile()
            check(False, "a missing profile raises LetterError")
        except letters.LetterError as exc:
            check("profile_example.md" in str(exc),
                  "a missing profile points at the template")

        thin = os.path.join(tempfile.mkdtemp(), "thin.md")
        with open(thin, "w", encoding="utf-8") as handle:
            handle.write("# Me\n")
        config.PROFILE_PATH = thin
        try:
            letters.load_profile()
            check(False, "a near-empty profile raises LetterError")
        except letters.LetterError:
            check(True, "a near-empty profile is rejected, not used")
    finally:
        config.PROFILE_PATH = original


# =============================================================================
if __name__ == "__main__":
    test_parser()
    test_preference()
    test_candidacy()
    test_freshness()
    test_scoring_model()
    test_weighting()
    test_candidacy_signals()
    test_dedupe()
    test_markdown_sources()
    test_stable_ids()
    test_applied_marks_survive()
    test_migration()
    test_defaults()
    test_pipeline()
    test_applied_always_visible()
    test_storage()
    test_visit_tracking()
    test_prompts()
    print(f"\n{PASSED} checks passed.\n")
