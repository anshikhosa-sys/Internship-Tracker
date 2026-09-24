"""
jobrank/workflow: the application state machine (states.py) and follow-up
tracking (followups.py).

These sit on top of applications.db without changing anything already
written to it — `applications.status` stays exactly as jobrank.storage
already reads and writes it. What's under test here is the NEW layer:
legal transitions are timestamped and recorded, illegal ones are refused,
and pre-existing rows (written before this layer existed) come along for
free through a one-time, read-only migration.
"""

import sqlite3
from datetime import date, timedelta

from jobrank import config, storage
from jobrank.config import workflow as workflow_config
from jobrank.workflow import followups, states

from tests.test_storage import make_posting


def _point_at_temp(monkeypatch, tmp_path, name="wf"):
    monkeypatch.setattr(config, "DATABASE_PATH", str(tmp_path / f"{name}.db"))
    monkeypatch.setattr(config, "APPLICATIONS_PATH", str(tmp_path / f"{name}-apps.db"))
    monkeypatch.setattr(config, "APPLICATIONS_EXPORT", str(tmp_path / f"{name}-apps.json"))


def _seed(conn, n=1):
    posting = make_posting(n, role=f"Software Engineer Intern {n}")
    storage.save_postings(conn, [posting], "2026-08-01T00:00:00+00:00")
    storage.record_run(conn, "2026-08-01T00:00:00+00:00", 1, 0)
    return posting


# =============================================================================
# can_transition / the table itself
# =============================================================================

def test_legal_moves_are_allowed():
    assert states.can_transition(workflow_config.DISCOVERED, workflow_config.APPLIED)
    assert states.can_transition(workflow_config.APPLIED, workflow_config.ONLINE_ASSESSMENT)
    assert states.can_transition(workflow_config.APPLIED, workflow_config.INTERVIEW), (
        "applied -> interview directly is legal: not every application gets an OA"
    )
    assert states.can_transition(workflow_config.INTERVIEW, workflow_config.INTERVIEW), (
        "a second interview round is a real, timestamped event, not a no-op"
    )
    assert states.can_transition(workflow_config.INTERVIEW, workflow_config.OFFER)


def test_discovered_to_offer_is_impossible():
    """CLAUDE.md invariant 14, named explicitly: this exact jump must be refused."""
    assert not states.can_transition(workflow_config.DISCOVERED, workflow_config.OFFER)


def test_no_self_transition():
    assert not states.can_transition(workflow_config.APPLIED, workflow_config.APPLIED)


def test_closed_stages_have_no_way_out():
    assert states.can_transition(workflow_config.REJECTED, workflow_config.APPLIED) is False
    assert states.can_transition(workflow_config.WITHDRAWN, workflow_config.OFFER) is False


# =============================================================================
# transition() against a real database
# =============================================================================

def test_transition_records_a_timestamped_event(monkeypatch, tmp_path):
    _point_at_temp(monkeypatch, tmp_path)
    conn = storage.connect(str(tmp_path / "wf.db"))
    posting = _seed(conn)

    assert states.current_state(conn, posting.id) == workflow_config.DISCOVERED, (
        "a posting nobody has applied to yet starts DISCOVERED"
    )

    states.transition(conn, posting.id, workflow_config.APPLIED)
    assert states.current_state(conn, posting.id) == workflow_config.APPLIED

    events = states.history(conn, posting.id)
    assert len(events) == 1
    assert events[0]["from_status"] == workflow_config.DISCOVERED
    assert events[0]["to_status"] == workflow_config.APPLIED
    assert events[0]["occurred_at"], "every event is timestamped"


def test_illegal_transition_raises_and_changes_nothing(monkeypatch, tmp_path):
    """The load-bearing test: Discovered -> Offer must be impossible end to end, not just at the config level."""
    _point_at_temp(monkeypatch, tmp_path)
    conn = storage.connect(str(tmp_path / "wf.db"))
    posting = _seed(conn)

    try:
        states.transition(conn, posting.id, workflow_config.OFFER)
        assert False, "Discovered -> Offer must raise"
    except states.IllegalTransitionError:
        pass

    assert states.current_state(conn, posting.id) == workflow_config.DISCOVERED, (
        "a rejected transition must not have moved the state"
    )
    assert states.history(conn, posting.id) == [], (
        "a rejected transition must not have recorded an event"
    )


def test_unknown_stage_name_is_rejected(monkeypatch, tmp_path):
    _point_at_temp(monkeypatch, tmp_path)
    conn = storage.connect(str(tmp_path / "wf.db"))
    posting = _seed(conn)
    try:
        states.transition(conn, posting.id, "made_up_stage")
        assert False, "an unknown stage must be rejected"
    except ValueError:
        pass


def test_full_legal_path_and_second_interview_round(monkeypatch, tmp_path):
    _point_at_temp(monkeypatch, tmp_path)
    conn = storage.connect(str(tmp_path / "wf.db"))
    posting = _seed(conn)

    states.transition(conn, posting.id, workflow_config.APPLIED)
    states.transition(conn, posting.id, workflow_config.ONLINE_ASSESSMENT)
    states.transition(conn, posting.id, workflow_config.INTERVIEW)
    states.transition(conn, posting.id, workflow_config.INTERVIEW)  # round two
    states.transition(conn, posting.id, workflow_config.OFFER)

    assert states.current_state(conn, posting.id) == workflow_config.OFFER
    stages = [e["to_status"] for e in states.history(conn, posting.id)]
    assert stages == [
        workflow_config.APPLIED,
        workflow_config.ONLINE_ASSESSMENT,
        workflow_config.INTERVIEW,
        workflow_config.INTERVIEW,
        workflow_config.OFFER,
    ]

    # An offer already reached is a closed door except withdrawing.
    try:
        states.transition(conn, posting.id, workflow_config.APPLIED)
        assert False, "an offer must not un-happen"
    except states.IllegalTransitionError:
        pass


# =============================================================================
# Migration — existing rows must survive untouched
# =============================================================================

def test_existing_application_rows_survive_migration(monkeypatch, tmp_path):
    """
    An application set via the OLD storage.set_status(), before this table
    existed, must not be altered by the migration — only read.
    """
    _point_at_temp(monkeypatch, tmp_path)
    conn = storage.connect(str(tmp_path / "wf.db"))
    posting = _seed(conn)

    storage.set_status(conn, posting.id, "interview")
    before = dict(conn.execute(
        "SELECT * FROM appdb.applications WHERE posting_id = ?", (posting.id,)
    ).fetchone())

    migrated = storage.migrate_application_events(conn)
    assert migrated == 1

    after = dict(conn.execute(
        "SELECT * FROM appdb.applications WHERE posting_id = ?", (posting.id,)
    ).fetchone())
    assert after == before, "migration must not rewrite the row it reads"

    assert states.current_state(conn, posting.id) == workflow_config.INTERVIEW, (
        "the legacy status is readable as a canonical stage after migration"
    )

    # Idempotent: calling it again adds nothing more.
    assert storage.migrate_application_events(conn) == 0


def test_migration_is_read_only_across_all_legacy_statuses(monkeypatch, tmp_path):
    """Every key in config/settings.py's APPLICATION_STAGES round-trips to a real, known workflow stage."""
    _point_at_temp(monkeypatch, tmp_path)
    conn = storage.connect(str(tmp_path / "wf.db"))

    legacy_statuses = ["applied", "oa", "interview", "offer", "rejected", "ghosted"]
    postings = []
    for i, status in enumerate(legacy_statuses, start=1):
        posting = _seed(conn, n=i)
        storage.set_status(conn, posting.id, status)
        postings.append((posting, status))

    storage.migrate_application_events(conn)

    for posting, status in postings:
        stage = states.current_state(conn, posting.id)
        assert stage in workflow_config.STAGES, f"{status!r} must map to a known stage, got {stage!r}"


def test_migration_does_not_touch_the_postings_database(monkeypatch, tmp_path):
    """The events table must live in applications.db, never in the rebuildable postings.db (invariant 13)."""
    _point_at_temp(monkeypatch, tmp_path)
    conn = storage.connect(str(tmp_path / "wf.db"))
    posting = _seed(conn)
    storage.set_status(conn, posting.id, "applied")
    storage.migrate_application_events(conn)

    main_tables = {row[0] for row in conn.execute(
        "SELECT name FROM main.sqlite_master WHERE type = 'table'"
    )}
    assert "application_events" not in main_tables, (
        "application_events must not be created in postings.db"
    )

    appdb_tables = {row[0] for row in conn.execute(
        "SELECT name FROM appdb.sqlite_master WHERE type = 'table'"
    )}
    assert "application_events" in appdb_tables


# =============================================================================
# followups.overdue()
# =============================================================================

def test_overdue_finds_silent_open_applications(monkeypatch, tmp_path):
    _point_at_temp(monkeypatch, tmp_path)
    conn = storage.connect(str(tmp_path / "wf.db"))

    quiet = _seed(conn, n=1)
    fresh = _seed(conn, n=2)
    closed = _seed(conn, n=3)

    long_ago = "2026-01-01T00:00:00+00:00"
    recent = "2026-08-20T00:00:00+00:00"

    storage.set_status(conn, quiet.id, "applied")
    storage.record_application_event(conn, quiet.id, workflow_config.DISCOVERED, workflow_config.APPLIED, long_ago)

    storage.set_status(conn, fresh.id, "applied")
    storage.record_application_event(conn, fresh.id, workflow_config.DISCOVERED, workflow_config.APPLIED, recent)

    storage.set_status(conn, closed.id, "rejected")
    storage.record_application_event(conn, closed.id, workflow_config.DISCOVERED, workflow_config.APPLIED, long_ago)
    storage.record_application_event(conn, closed.id, workflow_config.APPLIED, workflow_config.REJECTED, long_ago)

    today = date(2026, 9, 1)
    results = followups.overdue(conn, today=today)

    ids = [r["posting_id"] for r in results]
    assert quiet.id in ids, "silent open application is overdue"
    assert fresh.id not in ids, "recently touched application is not overdue"
    assert closed.id not in ids, "a rejected application already has an answer"
    assert results[0]["days_silent"] >= workflow_config.OVERDUE_AFTER_DAYS


def test_overdue_is_sorted_worst_first(monkeypatch, tmp_path):
    _point_at_temp(monkeypatch, tmp_path)
    conn = storage.connect(str(tmp_path / "wf.db"))

    a = _seed(conn, n=1)
    b = _seed(conn, n=2)
    storage.set_status(conn, a.id, "applied")
    storage.record_application_event(conn, a.id, workflow_config.DISCOVERED, workflow_config.APPLIED,
                                      "2026-06-01T00:00:00+00:00")
    storage.set_status(conn, b.id, "applied")
    storage.record_application_event(conn, b.id, workflow_config.DISCOVERED, workflow_config.APPLIED,
                                      "2026-08-01T00:00:00+00:00")

    results = followups.overdue(conn, today=date(2026, 9, 1))
    assert [r["posting_id"] for r in results] == [a.id, b.id], (
        "the longest-silent application comes first"
    )


def test_overdue_respects_a_custom_threshold(monkeypatch, tmp_path):
    _point_at_temp(monkeypatch, tmp_path)
    conn = storage.connect(str(tmp_path / "wf.db"))
    posting = _seed(conn)
    storage.set_status(conn, posting.id, "applied")
    storage.record_application_event(conn, posting.id, workflow_config.DISCOVERED, workflow_config.APPLIED,
                                      "2026-08-25T00:00:00+00:00")

    today = date(2026, 9, 1)  # 7 days silent
    assert followups.overdue(conn, today=today, threshold_days=10) == []
    assert len(followups.overdue(conn, today=today, threshold_days=5)) == 1


def test_overdue_with_no_applications_is_empty(monkeypatch, tmp_path):
    _point_at_temp(monkeypatch, tmp_path)
    conn = storage.connect(str(tmp_path / "wf.db"))
    _seed(conn)  # a posting with no application at all
    assert followups.overdue(conn, today=date.today()) == []


# =============================================================================
# CLI registration
# =============================================================================

def test_followups_cli_is_registered():
    """`run.py followups` exists via jobrank.cli's register() hook, not a copy of build_parser()."""
    from jobrank import cli

    parser = cli.build_parser()
    args = parser.parse_args(["followups"])
    assert args.func is not None
