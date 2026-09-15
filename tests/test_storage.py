"""
The SQLite storage layer: the NEW flag across runs, migrations, the
applied-marks that must never be lost, the application pipeline, and visit
tracking for "new since you last looked".

Ported from tests/legacy_checks.py (test_storage, test_migration,
test_applied_marks_survive, test_applications_are_separate, test_pipeline,
test_visit_tracking).

ADAPTATION: the legacy tests called `scorer.score_all(postings)` before
saving, to populate `posting.fit_score` / `.role_family` / `.company_tier`
before storage.save_postings() persisted them. scorer.py is gone — scoring is
now computed at READ time by jobrank.ranking from a per-user profile, not
stashed onto the Posting before a save. None of these tests actually assert
anything about the score, so that call is simply dropped; postings save with
their dataclass defaults (fit_score=0, role_family="", company_tier=""),
which is enough to exercise the NEW-flag/migration/pipeline logic under test.

DROPPED ASSERTIONS: the legacy test_storage checked
`isinstance(row["preference"], float)` and
`isinstance(row["preference_reasons"], list)` on a loaded row. Those columns
are v1 leftovers storage.py keeps only so an old database still opens
(storage.py: "unused since v2 and kept so databases created by v1 still
open") — save_postings() no longer writes them, so they come back as None,
not a float/list. Asserting types on dead columns describes nothing about
current behavior, so both assertions are dropped. `"role_family" in row` is
kept since that column is still written and read.
"""

import os
import sqlite3
import tempfile
from datetime import date, timedelta

from jobrank import config
from jobrank import dedupe
from jobrank import storage
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


def _point_at_temp(monkeypatch, tmp_path, name="test"):
    """Every test here gets its own throwaway internships.db + applications.db."""
    monkeypatch.setattr(config, "DATABASE_PATH", str(tmp_path / f"{name}.db"))
    monkeypatch.setattr(config, "APPLICATIONS_PATH", str(tmp_path / f"{name}-apps.db"))
    monkeypatch.setattr(config, "APPLICATIONS_EXPORT", str(tmp_path / f"{name}-apps.json"))


# =============================================================================
def test_storage_new_flag_across_runs(monkeypatch, tmp_path):
    """
    WHY THIS EXISTS: the NEW flag was once off by one run —
    `first_seen >= previous_run` also matched postings inserted during the
    PREVIOUS run, so they were reported as new a second time.
    """
    _point_at_temp(monkeypatch, tmp_path)
    conn = storage.connect(str(tmp_path / "test.db"))

    def run(postings, timestamp):
        result = storage.save_postings(conn, postings, timestamp)
        storage.record_run(conn, timestamp, result["total"], len(result["new_ids"]))
        return result

    r1 = run([make_posting(1), make_posting(2), make_posting(3)],
             "2026-08-01T00:00:00+00:00")
    assert r1["is_first_run"], "first run is recognized as a baseline"
    assert not r1["new_ids"], "baseline reports nothing as NEW"

    storage.set_applied(conn, make_posting(2).id, True)

    r2 = run([make_posting(1), make_posting(2), make_posting(4)],
             "2026-08-02T00:00:00+00:00")
    assert r2["new_ids"] == {make_posting(4).id}, (
        "only the genuinely new posting is flagged"
    )
    assert r2["deactivated"] == 1, (
        "a posting that dropped off the source is marked inactive"
    )

    # Regression: the off-by-one-run bug.
    r3 = run([make_posting(1), make_posting(2), make_posting(4)],
             "2026-08-03T00:00:00+00:00")
    assert not r3["new_ids"], (
        "last run's new posting is NOT flagged again (regression)"
    )

    r4 = run([make_posting(1), make_posting(2), make_posting(4), make_posting(3)],
             "2026-08-04T00:00:00+00:00")
    assert not r4["new_ids"], "a returning posting is not counted as new"

    rows = {row["id"]: row for row in storage.load_postings(conn)}
    assert storage.applied_count(conn) == 1, "applied mark survived three refreshes"
    assert rows[make_posting(2).id]["applied"] == 1, (
        "the right posting is still marked applied"
    )
    assert rows[make_posting(1).id]["first_seen"].startswith("2026-08-01"), (
        "first_seen is preserved, not overwritten each run"
    )
    assert len(storage.load_postings(conn)) == 4, (
        "a returning posting becomes active again"
    )

    row = rows[make_posting(1).id]
    assert "role_family" in row, "the role family is stored for the prompts"

    conn.close()


# =============================================================================
def test_migration_never_requires_deleting_the_database(monkeypatch, tmp_path):
    """A schema change must never require deleting the database."""
    monkeypatch.setattr(config, "APPLICATIONS_PATH", str(tmp_path / "apps.db"))
    monkeypatch.setattr(config, "APPLICATIONS_EXPORT", str(tmp_path / "apps.json"))

    db_path = str(tmp_path / "old.db")

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
    assert "salary" in columns, "a missing column is added by migration"
    assert "candidacy_score" in columns, "every new column is added"

    app_cols = {r["name"] for r in conn.execute("PRAGMA appdb.table_info(applications)")}
    assert "company" in app_cols, "the applications table migrates too"

    assert storage.applied_count(conn) == 1, (
        "existing applied marks survive the migration"
    )
    conn.close()


# =============================================================================
def test_applied_marks_survive(monkeypatch, tmp_path):
    """Applied marks are the only unrecoverable data here."""
    monkeypatch.setattr(config, "APPLICATIONS_PATH", str(tmp_path / "apps.db"))
    monkeypatch.setattr(config, "APPLICATIONS_EXPORT", str(tmp_path / "apps.json"))
    db_path = str(tmp_path / "marks.db")
    conn = storage.connect(db_path)

    posting = make_posting(1, role="Software Engineer Intern")
    storage.save_postings(conn, [posting], "2026-08-01T00:00:00+00:00")
    storage.record_run(conn, "2026-08-01T00:00:00+00:00", 1, 0)
    storage.set_applied(conn, posting.id, True)

    # The mark records company and role as a recovery key.
    row = conn.execute(
        "SELECT company, role FROM appdb.applications WHERE posting_id = ?",
        (posting.id,),
    ).fetchone()
    assert row["company"] == posting.company, (
        "an applied mark stores the company as a recovery key"
    )
    assert row["role"] == posting.role, (
        "an applied mark stores the role as a recovery key"
    )

    # Simulate an id that moved: file the mark under a stale id.
    conn.execute("DELETE FROM appdb.applications")
    conn.execute(
        "INSERT INTO appdb.applications (posting_id, applied, "
        "updated_at, company, role) VALUES (?,?,?,?,?)",
        ("stale:id", 1, "2026-08-01T00:00:00+00:00", posting.company, posting.role),
    )
    conn.commit()
    assert storage.applied_count(conn) == 1, "the stale mark exists"

    recovered = storage.reattach_orphaned_marks(conn)
    assert recovered == 1, "an orphaned mark is recovered"
    rows = {r["id"]: r for r in storage.load_postings(conn)}
    assert rows[posting.id]["applied"] == 1, (
        "the recovered mark is re-attached to the live posting"
    )
    assert storage.applied_count(conn) == 1, "recovery does not duplicate the mark"

    conn.close()


# =============================================================================
def test_applications_survive_a_deleted_postings_database(monkeypatch, tmp_path):
    """
    Applications must survive the postings database being deleted.

    Not hypothetical: during development internships.db was deleted several
    times to rebuild the schema, and each time it destroyed real application
    records. Separate tables protect against a careless UPDATE; only
    separate FILES protect against `rm`.
    """
    db = str(tmp_path / "internships.db")
    apps = str(tmp_path / "applications.db")
    export = str(tmp_path / "applications.json")
    monkeypatch.setattr(config, "DATABASE_PATH", db)
    monkeypatch.setattr(config, "APPLICATIONS_PATH", apps)
    monkeypatch.setattr(config, "APPLICATIONS_EXPORT", export)

    conn = storage.connect()
    posting = make_posting(1, role="Software Engineer Intern")
    storage.save_postings(conn, [posting], "2026-08-01T00:00:00+00:00")
    storage.record_run(conn, "2026-08-01T00:00:00+00:00", 1, 0)
    storage.set_status(conn, posting.id, "applied")
    assert storage.applied_count(conn) == 1, "the application is recorded"
    conn.close()

    assert os.path.exists(apps), "applications live in their own file"
    assert os.path.exists(export), "a plain-text mirror is written alongside"

    # The thing that kept destroying data.
    os.remove(db)
    assert not os.path.exists(db), "the postings database is gone"

    conn = storage.connect()
    assert storage.applied_count(conn) == 1, (
        "the application SURVIVED the postings database being deleted"
    )
    conn.close()


# =============================================================================
def test_pipeline(monkeypatch, tmp_path):
    """Application stages, notes, and the backfill from the old boolean."""
    monkeypatch.setattr(config, "APPLICATIONS_PATH", str(tmp_path / "apps.db"))
    monkeypatch.setattr(config, "APPLICATIONS_EXPORT", str(tmp_path / "apps.json"))
    conn = storage.connect(str(tmp_path / "pipe.db"))

    posting = make_posting(1, role="Software Engineer Intern")
    storage.save_postings(conn, [posting], "2026-08-01T00:00:00+00:00")
    storage.record_run(conn, "2026-08-01T00:00:00+00:00", 1, 0)

    storage.set_status(conn, posting.id, "applied")
    assert storage.pipeline_counts(conn) == {"applied": 1}, (
        "setting a stage puts the application in the pipeline"
    )

    rows = storage.pipeline(conn)
    assert len(rows) == 1, "the pipeline lists it"
    assert rows[0]["days_waiting"] == 0, "days waiting starts at zero"

    first_applied_at = conn.execute(
        "SELECT applied_at FROM appdb.applications WHERE posting_id = ?",
        (posting.id,),
    ).fetchone()["applied_at"]

    # Moving stage must NOT reset the clock.
    storage.set_status(conn, posting.id, "interview")
    still = conn.execute(
        "SELECT applied_at FROM appdb.applications WHERE posting_id = ?",
        (posting.id,),
    ).fetchone()["applied_at"]
    assert still == first_applied_at, (
        "applied_at is stamped once and survives a stage change"
    )
    assert storage.pipeline_counts(conn) == {"interview": 1}, "the stage moved"

    # Back to not-applied.
    storage.set_status(conn, posting.id, "")
    assert storage.pipeline_counts(conn) == {}, (
        "clearing the stage removes it from the pipeline"
    )
    assert storage.applied_count(conn) == 0, (
        "the applied boolean stays in sync with the stage"
    )

    # An unknown stage must be rejected, not silently stored.
    try:
        storage.set_status(conn, posting.id, "nonsense")
        assert False, "an unknown stage is rejected"
    except ValueError:
        pass

    storage.set_notes(conn, posting.id, "Recruiter: Dana. OA due Friday.")
    saved = storage.load_postings(conn)[0]["notes"]
    assert saved == "Recruiter: Dana. OA due Friday.", "notes round-trip"

    conn.close()

    # -- backfill from the old boolean ---------------------------------------
    # An application marked under the old applied=1 scheme, with no stage.
    # Without a backfill it would vanish from the pipeline view while still
    # sitting in the database — loss with no error to investigate.
    old_path = str(tmp_path / "legacy.db")
    monkeypatch.setattr(config, "APPLICATIONS_PATH", str(tmp_path / "legacy-apps.db"))
    monkeypatch.setattr(config, "APPLICATIONS_EXPORT", str(tmp_path / "legacy-apps.json"))
    old = sqlite3.connect(old_path)
    old.execute("CREATE TABLE applications (posting_id TEXT PRIMARY KEY, "
                "applied INTEGER, notes TEXT, updated_at TEXT)")
    old.execute("INSERT INTO applications VALUES "
                "('job:abc', 1, '', '2026-08-01T00:00:00+00:00')")
    old.commit()
    old.close()

    conn = storage.connect(old_path)
    counts = storage.pipeline_counts(conn)
    assert counts.get("applied") == 1, (
        "an old applied=1 mark is backfilled to the 'applied' stage"
    )
    row = conn.execute(
        "SELECT applied_at FROM appdb.applications WHERE posting_id = 'job:abc'"
    ).fetchone()
    assert row["applied_at"] == "2026-08-01T00:00:00+00:00", (
        "applied_at is backfilled from the last update time"
    )
    conn.close()


# =============================================================================
def test_visit_tracking(monkeypatch, tmp_path):
    """VISIT TRACKING: what counts as new to you."""
    monkeypatch.setattr(config, "APPLICATIONS_PATH", str(tmp_path / "apps.db"))
    monkeypatch.setattr(config, "APPLICATIONS_EXPORT", str(tmp_path / "apps.json"))
    conn = storage.connect(str(tmp_path / "visits.db"))

    live = []

    def add(posting, when):
        live.append(posting)
        result = storage.save_postings(conn, live, when)
        storage.record_run(conn, when, result["total"], len(result["new_ids"]))

    add(make_posting(1), "2026-08-01T09:00:00+00:00")
    add(make_posting(2), "2026-08-01T09:00:00+00:00")

    basis = storage.register_visit(conn)
    assert storage.new_since_last_visit(conn, basis) == set(), (
        "first ever visit badges nothing"
    )

    basis = storage.register_visit(conn)
    assert storage.new_since_last_visit(conn, basis) == set(), (
        "reloading during a visit is stable"
    )

    add(make_posting(3), "2026-08-02T08:00:00+00:00")
    storage._set_state(conn, "last_activity", "2026-08-01T09:05:00+00:00")
    conn.commit()

    basis = storage.register_visit(conn)
    assert storage.new_since_last_visit(conn, basis) == {make_posting(3).id}, (
        "a posting that arrived since your last visit is badged"
    )

    basis = storage.register_visit(conn)
    assert storage.new_since_last_visit(conn, basis) == {make_posting(3).id}, (
        "badges persist across reloads within one visit"
    )

    # Several days away must not lose anything — the reason this exists.
    add(make_posting(4), "2026-08-03T08:00:00+00:00")
    add(make_posting(5), "2026-08-04T08:00:00+00:00")
    storage._set_state(conn, "last_activity", "2026-08-02T08:10:00+00:00")
    conn.commit()

    basis = storage.register_visit(conn)
    assert storage.new_since_last_visit(conn, basis) == {
        make_posting(4).id, make_posting(5).id,
    }, "several days away still surfaces every posting since your visit"
    assert storage.new_posting_ids(conn) == {make_posting(5).id}, (
        "...whereas 'new since last run' would only show the latest day"
    )

    storage.mark_all_seen(conn)
    assert storage.new_since_last_visit(
        conn, storage._get_state(conn, "visit_basis")
    ) == set(), "'Mark all as seen' clears every badge"

    storage._set_state(conn, "last_activity", "not-a-timestamp")
    conn.commit()
    storage.register_visit(conn)  # must not raise

    conn.close()


# =============================================================================
def test_reattach_uses_same_role_matching_as_dedupe(monkeypatch, tmp_path):
    """
    storage.reattach_orphaned_marks() has to use dedupe.same_role() for its
    fuzzy fallback match, because dedupe now merges postings whose titles
    differ across sources and the surviving row keeps only ONE of the two
    titles. If reattach used a different rule, an application filed under the
    other title could look unattached forever.
    """
    monkeypatch.setattr(config, "APPLICATIONS_PATH", str(tmp_path / "apps.db"))
    monkeypatch.setattr(config, "APPLICATIONS_EXPORT", str(tmp_path / "apps.json"))
    conn = storage.connect(str(tmp_path / "reattach.db"))

    posting = make_posting(1, role="AI Software Engineer Intern - Edge")
    posting.company = "Microsoft"
    storage.save_postings(conn, [posting], "2026-08-01T00:00:00+00:00")
    storage.record_run(conn, "2026-08-01T00:00:00+00:00", 1, 0)

    # File a mark against the OTHER wording of the same job.
    conn.execute(
        "INSERT INTO appdb.applications (posting_id, applied, updated_at, "
        "company, role) VALUES (?,?,?,?,?)",
        ("job:orphaned", 1, "2026-08-01T00:00:00+00:00",
         "Microsoft", "AI Software Engineering Intern - Edge"),
    )
    conn.commit()

    assert dedupe.same_role(
        "AI Software Engineer Intern - Edge",
        "AI Software Engineering Intern - Edge",
    ), "sanity: dedupe considers these the same job"

    recovered = storage.reattach_orphaned_marks(conn)
    assert recovered == 1, "the mark is recovered via same_role, not exact match"
    rows = {r["id"]: r for r in storage.load_postings(conn)}
    assert rows[posting.id]["applied"] == 1

    conn.close()
