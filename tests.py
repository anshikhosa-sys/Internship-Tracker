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

    def cand(role, **kwargs):
        return scorer.candidacy(make_posting(1, role=role, **kwargs))[0]

    baseline = cand("Software Engineer Intern")
    check(baseline == config.CANDIDACY_BASELINE,
          "an unremarkable title sits at the baseline")

    check(cand("Data Pipeline Engineer Intern") > baseline,
          "a title naming proven experience raises candidacy")

    # Blockers must sink a posting even when it otherwise looks perfect.
    check(cand("Machine Learning PhD Research Intern") < 0.15,
          "a PhD requirement sinks candidacy")
    check(cand("Senior Software Engineer") < baseline / 2,
          "a senior title sinks candidacy")
    check(cand("Software Engineer Intern", advanced_degree=True) < baseline,
          "the advanced-degree marker lowers candidacy")
    check(cand("Software Engineer Intern", faang=True) < baseline,
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

    print("\nINTERNSHIP GATE")
    intern = make_posting(1, role="Software Engineer Intern")
    check(scorer.is_internship(intern), "an intern role passes the gate")
    check(not scorer.is_internship(make_posting(1, role="Staff Engineer")),
          "a full-time role is filtered out")


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
    test_storage()
    test_visit_tracking()
    test_prompts()
    print(f"\n{PASSED} checks passed.\n")
