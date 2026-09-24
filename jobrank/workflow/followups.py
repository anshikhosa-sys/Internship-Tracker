"""
Follow-up tracking: which open applications have gone quiet.

Measured, not guessed: of 45 applications tracked so far, 30 had gone 21+
days without a recorded event. Most jobs never respond at all, so silence
isn't itself a bug — but it should be something you can see at a glance
instead of something you find by scrolling the pipeline and doing the date
math by hand. No notifications and no emails; this only computes the list.
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timezone

from jobrank import storage
from jobrank.config import workflow as workflow_config


def _days_since(occurred_at: str, today: date) -> int:
    """
    Whole days between a stored timestamp and `today`.

    Clamped at zero: a clock that's briefly out of sync must never read as a
    NEGATIVE number of days silent, which would be more confusing than
    reporting nothing happened yet.
    """
    try:
        then = datetime.fromisoformat(occurred_at)
    except (TypeError, ValueError):
        return 0
    if then.tzinfo is None:
        then = then.replace(tzinfo=timezone.utc)
    return max(0, (today - then.date()).days)


def overdue(
    conn: sqlite3.Connection,
    today: date | None = None,
    threshold_days: int | None = None,
) -> list[dict]:
    """
    Every OPEN application that's gone `threshold_days` or more (default:
    config.workflow.OVERDUE_AFTER_DAYS) without a recorded event, worst-first.

    "Open" excludes offer/rejected/withdrawn — those already have an answer,
    so silence there isn't the thing this is meant to surface. Reads
    application_events (migrating legacy applications.status rows into it
    first, the same way jobrank.workflow.states does) rather than the status
    column directly, so a real transition() call and a migrated legacy row
    are treated identically.
    """
    storage.migrate_application_events(conn)
    today = today or datetime.now(timezone.utc).date()
    threshold = workflow_config.OVERDUE_AFTER_DAYS if threshold_days is None else threshold_days

    latest = storage.latest_application_events(conn)
    if not latest:
        return []

    rows = conn.execute(
        """
        SELECT posting_id, company, role
        FROM appdb.applications
        WHERE status != ''
        """
    ).fetchall()

    results = []
    for row in rows:
        event = latest.get(row["posting_id"])
        if event is None:
            continue
        stage = event["to_status"]
        if stage in workflow_config.CLOSED_STAGES:
            continue
        days_silent = _days_since(event["occurred_at"], today)
        if days_silent < threshold:
            continue
        results.append({
            "posting_id": row["posting_id"],
            "company": row["company"],
            "role": row["role"],
            "stage": stage,
            "days_silent": days_silent,
            "last_event_at": event["occurred_at"],
        })

    results.sort(key=lambda item: item["days_silent"], reverse=True)
    return results


# ---------------------------------------------------------------------------
# CLI — `run.py followups`
# ---------------------------------------------------------------------------
#
# jobrank/cli.py exposes a `register` hook precisely so a command can attach
# itself without editing build_parser()'s body; this is that command. See
# the two lines added near the top of cli.py that import and register it.

def cmd_followups(args) -> int:
    """`run.py followups` — every open application that's gone quiet, worst first."""
    conn = storage.connect()
    rows = overdue(conn, threshold_days=args.days)
    if not rows:
        print("Nothing overdue.")
        return 0
    for row in rows:
        print(f"{row['days_silent']:>4}d silent  {row['company']:<28} {row['role']:<42} [{row['stage']}]")
    return 0


def build_followups_parser(sub) -> None:
    """Adds the `followups` subcommand to run.py's argparse subparsers."""
    parser = sub.add_parser("followups", help="open applications that have gone quiet")
    parser.add_argument(
        "--days", type=int, default=None,
        help=f"override the overdue threshold (default: {workflow_config.OVERDUE_AFTER_DAYS})",
    )
    parser.set_defaults(func=cmd_followups)
