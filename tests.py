"""
tests.py — checks the logic that's easy to get quietly wrong.

    python3 tests.py

These use a throwaway database in a temp folder, so running them never touches
your real internships.db. They don't hit the network either — the parser is
tested against a small chunk of saved HTML.

WHY THESE PARTICULAR TESTS?
Not everything needs a test, but two things here genuinely do:

  1. The NEW flag. It depends on timestamps lining up across runs, and a bug
     is invisible — you'd just see slightly wrong badges and never know. The
     first version of this code had an off-by-one-run bug that these tests
     caught: `first_seen >= previous_run` also matched postings inserted
     during the previous run, so they were reported new twice.

  2. Applied marks surviving a refresh. If that breaks, you lose data you
     can't recover.
"""

import os
import tempfile

import scorer
import storage
from sources.base import Posting
from sources.simplify_readme import _TableParser, _age_to_date, _cell_text


PASSED = 0


def check(condition, label):
    """Tiny assertion helper so output reads like a checklist."""
    global PASSED
    if condition:
        PASSED += 1
        print(f"  PASS  {label}")
    else:
        print(f"  FAIL  {label}")
        raise AssertionError(label)


def make_posting(n, score=50, role=None, category="Software Engineering"):
    posting = Posting(
        company=f"Company{n}",
        role=role or f"Role {n} Intern",
        category=category,
        location="New York, NY",
        apply_url=f"https://example.com/{n}",
        simplify_url=f"https://simplify.jobs/p/uuid-{n}",
        source="test",
    )
    posting.fit_score = score
    return posting


# =============================================================================
def test_parser():
    print("\nPARSER")

    # A cut-down version of the real table, including the two awkward cases:
    # a continuation row (↳) and a collapsed multi-location cell.
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

    # The multi-location cell is the one that used to come out mangled as
    # "2 locationsSeattle, WA".
    location = _cell_text(rows[1][2])
    check("locations" not in location,
          "drops the '2 locations' summary label")
    check(location == "Seattle, WA | Remote",
          f"joins multiple locations cleanly (got {location!r})")

    # Links: the apply URL and the Simplify URL must be told apart.
    links = rows[0][3]["links"]
    check(any("simplify.jobs/p/" in link for link in links),
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
def test_scoring():
    print("\nSCORING")

    def score_of(role, category="Software Engineering"):
        return scorer.score(make_posting(1, role=role, category=category))[0]

    # The whole-word matching bug: "ai" must not match inside other words.
    check(score_of("Email Platform Intern") < score_of("AI Platform Intern"),
          "'Email' does not collect the AI bonus (whole-word matching)")
    check(score_of("Training Program Intern") < score_of("AI Engineer Intern"),
          "'Training' does not collect the AI bonus")

    # Tier ordering must hold even against bonuses.
    fde = score_of("Forward Deployed Engineer Intern")
    pm = score_of("AI Data Platform Product Manager Intern",
                  "Product Management")
    check(fde > pm,
          f"a bare top-tier role ({fde}) outranks a loaded lower tier ({pm})")

    generic = score_of("Software Engineer Intern")
    focused = score_of("Software Engineer Intern, Distributed Systems")
    check(focused > generic,
          f"a systems SWE role ({focused}) outranks a generic one ({generic})")

    # Only the best tier counts, so "Solutions Engineer" isn't also paid for
    # matching the generic "engineer" tier.
    solutions = score_of("Solutions Engineer Intern")
    check(solutions < 150 + 10 + 40 + 1, "role tiers do not stack")

    # The focus cap.
    stuffed = score_of("AI ML Data Platform Infrastructure Systems Intern")
    import config
    check(stuffed <= 40 + config.MAX_FOCUS_BONUS + 150,
          "keyword-stuffed titles are capped")

    # Out-of-scope fields sort to the bottom.
    check(score_of("Quantitative Trading Intern") < 0,
          "roles outside this search's scope sort below everything else")

    # The internship gate.
    intern = make_posting(1, role="Software Engineer Intern")
    check(scorer.is_internship(intern), "an intern role passes the gate")
    check(not scorer.is_internship(make_posting(1, role="Senior Engineer")),
          "a full-time role is filtered out")


# =============================================================================
def test_storage():
    print("\nSTORAGE: the NEW flag across runs")

    db_path = os.path.join(tempfile.mkdtemp(), "test.db")
    conn = storage.connect(db_path)

    def run(postings, timestamp):
        result = storage.save_postings(conn, postings, timestamp)
        storage.record_run(conn, timestamp, result["total"],
                           len(result["new_ids"]))
        return result

    # Run 1: baseline.
    r1 = run([make_posting(1), make_posting(2), make_posting(3)],
             "2026-08-01T00:00:00+00:00")
    check(r1["is_first_run"], "first run is recognized as a baseline")
    check(not r1["new_ids"], "baseline reports nothing as NEW")

    storage.set_applied(conn, make_posting(2).id, True)

    # Run 2: one posting leaves, one arrives.
    r2 = run([make_posting(1), make_posting(2), make_posting(4)],
             "2026-08-02T00:00:00+00:00")
    check(r2["new_ids"] == {make_posting(4).id},
          "only the genuinely new posting is flagged")
    check(r2["deactivated"] == 1,
          "a posting that dropped off the source is marked inactive")

    # Run 3: nothing changes. This is the regression test for the off-by-one.
    r3 = run([make_posting(1), make_posting(2), make_posting(4)],
             "2026-08-03T00:00:00+00:00")
    check(not r3["new_ids"],
          "last run's new posting is NOT flagged again (regression)")

    # Run 4: a previously-inactive posting returns.
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
    check(rows[make_posting(4).id]["first_seen"].startswith("2026-08-02"),
          "a later posting keeps its own first_seen")
    check(len(storage.load_postings(conn)) == 4,
          "a returning posting becomes active again")
    check(storage.new_posting_ids(conn) == set(),
          "new_posting_ids() agrees with save_postings()")

    conn.close()


# =============================================================================
def test_visit_tracking():
    """
    The 'new since you last looked' logic.

    This matters because the scheduled daily refresh broke the old definition
    of NEW. When you triggered every refresh yourself, "new since the last
    run" was fine. With a job running each morning it silently becomes "new in
    the last 24 hours", so skipping a few days meant postings stopped being
    flagged and you'd never see them.
    """
    print("\nVISIT TRACKING: what counts as new to you")

    db_path = os.path.join(tempfile.mkdtemp(), "visits.db")
    conn = storage.connect(db_path)

    # Each refresh re-sends every posting still live at the source. Sending
    # only the new one would (correctly) mark all the others inactive, so the
    # helper accumulates — this mirrors what a real refresh does.
    live = []

    def add(posting, when):
        live.append(posting)
        result = storage.save_postings(conn, live, when)
        storage.record_run(conn, when, result["total"],
                           len(result["new_ids"]))

    # Two postings already exist before you ever open the dashboard.
    add(make_posting(1), "2026-08-01T09:00:00+00:00")
    add(make_posting(2), "2026-08-01T09:00:00+00:00")

    # First visit ever: nothing should be badged, since none of it arrived
    # "since you last looked" — you've never looked.
    basis = storage.register_visit(conn)
    check(storage.new_since_last_visit(conn, basis) == set(),
          "first ever visit badges nothing")

    # A reload moments later must not clear anything or crash.
    basis = storage.register_visit(conn)
    check(storage.new_since_last_visit(conn, basis) == set(),
          "reloading during a visit is stable")

    # Now the scheduled job runs overnight and finds a new posting.
    add(make_posting(3), "2026-08-02T08:00:00+00:00")

    # Simulate coming back the next day by ageing your last activity beyond
    # the session window.
    storage._set_state(conn, "last_activity", "2026-08-01T09:05:00+00:00")
    conn.commit()

    basis = storage.register_visit(conn)
    new_ids = storage.new_since_last_visit(conn, basis)
    check(new_ids == {make_posting(3).id},
          "a posting that arrived since your last visit is badged")

    # THE KEY TEST: badges must survive while you browse. A second page load
    # moments later is the same visit, so posting 3 stays flagged.
    basis = storage.register_visit(conn)
    check(storage.new_since_last_visit(conn, basis) == {make_posting(3).id},
          "badges persist across reloads within one visit")

    # THE OTHER KEY TEST: skipping several days must not lose anything. Two
    # more postings arrive on separate days; both should still be flagged,
    # even though only one arrived in the most recent run.
    add(make_posting(4), "2026-08-03T08:00:00+00:00")
    add(make_posting(5), "2026-08-04T08:00:00+00:00")

    storage._set_state(conn, "last_activity", "2026-08-02T08:10:00+00:00")
    conn.commit()

    basis = storage.register_visit(conn)
    new_ids = storage.new_since_last_visit(conn, basis)
    check(new_ids == {make_posting(4).id, make_posting(5).id},
          "several days away still surfaces every posting since your visit")

    # This is what the old logic would have returned — one day's worth.
    check(storage.new_posting_ids(conn) == {make_posting(5).id},
          "...whereas 'new since last run' would only show the latest day")

    # Explicitly clearing badges.
    storage.mark_all_seen(conn)
    basis = storage._get_state(conn, "visit_basis")
    check(storage.new_since_last_visit(conn, basis) == set(),
          "'Mark all as seen' clears every badge")

    # A corrupt timestamp must not freeze the badges forever.
    storage._set_state(conn, "last_activity", "not-a-timestamp")
    conn.commit()
    storage.register_visit(conn)
    check(True, "an unparseable timestamp is handled without crashing")

    conn.close()


# =============================================================================
def test_notifications():
    """The notifier must never raise — it runs from a background job."""
    print("\nNOTIFICATIONS")

    import notify

    # Text from job postings is untrusted third-party input, so quotes and
    # backslashes must be escaped before going into an AppleScript string.
    escaped = notify._escape('Acme "Corp" \\ Ltd')
    check('\\"' in escaped and "\\\\" in escaped,
          "quotes and backslashes are escaped for AppleScript")

    # With the feature off, nothing is sent regardless of what's passed in.
    import config
    original = config.NOTIFY_ON_STRONG_FIT
    try:
        config.NOTIFY_ON_STRONG_FIT = False
        strong = make_posting(1, score=999)
        check(notify.notify_strong_matches([strong]) is False,
              "notifications respect the config switch")
    finally:
        config.NOTIFY_ON_STRONG_FIT = original

    # Nothing new that clears the threshold means nothing to say.
    weak = make_posting(2, score=0)
    check(notify.notify_strong_matches([weak]) is False,
          "a weak match does not trigger a notification")
    check(notify.notify_strong_matches([]) is False,
          "an empty list does not trigger a notification")

    # The threshold boundary. Scoring one point under must stay silent —
    # this is checked without sending anything.
    just_under = make_posting(3, score=config.NOTIFY_THRESHOLD - 1)
    check(notify.notify_strong_matches([just_under]) is False,
          "a posting just below NOTIFY_THRESHOLD stays silent")

    # The notify threshold must remain SEPARATE from the badge threshold.
    # Tying them together is what kept notifications silent for days while
    # relevant roles arrived — see the comment in config.py.
    check(config.NOTIFY_THRESHOLD <= config.STRONG_FIT_THRESHOLD,
          "notify threshold is not stricter than the strong-fit badge")


# =============================================================================
if __name__ == "__main__":
    test_parser()
    test_scoring()
    test_storage()
    test_visit_tracking()
    test_notifications()
    print(f"\n{PASSED} checks passed.\n")
