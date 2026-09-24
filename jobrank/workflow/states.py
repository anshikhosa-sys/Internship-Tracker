"""
The application state machine.

Formalizes CLAUDE.md invariant 14 — application state moves only through
legal transitions, each timestamped, and Discovered -> Offer must be
impossible. `applications.status` (jobrank/storage.py) already tracks a
stage, but as a single mutable string: nothing stops it being set to
anything, and overwriting it erases how it got there. This module checks
each move against jobrank.config.workflow.TRANSITIONS before it happens, and
records it in the append-only application_events table instead of editing
that string, so an illegal edit can corrupt at most one new row, never the
history behind it.
"""

from __future__ import annotations

import sqlite3

from jobrank import storage
from jobrank.config import workflow as workflow_config


class IllegalTransitionError(ValueError):
    """A requested stage change isn't a legal move from where the application currently sits (e.g. Discovered -> Offer)."""


def can_transition(from_state: str, to_state: str) -> bool:
    """
    Whether `to_state` is a legal next stage from `from_state`.

    Kept separate from transition() so a caller — the CLI, a future UI,
    a test — can check "is this even allowed" before touching the database,
    rather than relying on catching an exception to find out.

    No blanket "same state twice is never allowed" rule here: whether a
    stage can repeat is exactly what config/workflow.py's TRANSITIONS table
    already decides (INTERVIEW allows looping to itself for a second round;
    everything else doesn't), and hard-coding an exception to that in code
    would just be a second, contradictory copy of the same decision.
    """
    return to_state in workflow_config.TRANSITIONS.get(from_state, ())


def current_state(conn: sqlite3.Connection, posting_id: str) -> str:
    """
    The stage implied by the most recent recorded event.

    Every posting that has never had an event is still DISCOVERED — that's
    the state before any application exists, not an error — so this never
    raises for an unseen posting_id, it just reports the starting point.
    """
    storage.migrate_application_events(conn)
    history_rows = storage.application_event_history(conn, posting_id)
    if not history_rows:
        return workflow_config.DISCOVERED
    return history_rows[-1]["to_status"]


def transition(conn: sqlite3.Connection, posting_id: str, to_state: str) -> None:
    """
    Move an application to `to_state`, timestamped, or raise if the move from
    its current stage isn't one of the legal ones in config/workflow.py.

    Raising here — rather than silently storing whatever string was passed,
    the way the older set_status() does — is the whole point: it's the one
    place that can make "Discovered -> Offer" actually impossible instead of
    merely undocumented.
    """
    if to_state not in workflow_config.STAGES:
        raise ValueError(
            f"{to_state!r} is not a known stage; expected one of {workflow_config.STAGES}"
        )
    from_state = current_state(conn, posting_id)
    if not can_transition(from_state, to_state):
        raise IllegalTransitionError(
            f"posting {posting_id}: cannot move from {from_state!r} to {to_state!r} "
            f"(legal next stages: {workflow_config.TRANSITIONS.get(from_state, ())})"
        )
    storage.record_application_event(conn, posting_id, from_state, to_state)


def history(conn: sqlite3.Connection, posting_id: str) -> list[dict]:
    """
    Every recorded stage change for one posting, oldest first.

    Separate from jobrank.storage.pipeline(), which only ever shows the
    CURRENT stage — this answers "how did it get here", which a single
    mutable status column was never able to.
    """
    storage.migrate_application_events(conn)
    return storage.application_event_history(conn, posting_id)
