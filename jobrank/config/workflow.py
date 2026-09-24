"""
Data for the application state machine and follow-up tracking
(jobrank/workflow/). No logic here — per the project rule, `workflow/` reads
this file rather than hard-coding a stage name or a day count.

`applications.db` already stores a loose `status` string (see
config/settings.py APPLICATION_STAGES), set through storage.set_status() with
no memory of how an application got there and nothing stopping it jumping
straight from unset to "offer". This file defines a stricter, timestamped
model on top of it: a fixed set of stages and the legal moves between them.
jobrank/workflow/states.py enforces the moves; it never edits `status`
itself, it appends to a separate `application_events` ledger instead.
"""

from __future__ import annotations

DISCOVERED = "discovered"
APPLIED = "applied"
ONLINE_ASSESSMENT = "online_assessment"
INTERVIEW = "interview"
OFFER = "offer"
REJECTED = "rejected"
WITHDRAWN = "withdrawn"

# Every stage an application can be in, in the order it normally happens.
STAGES: tuple[str, ...] = (
    DISCOVERED,
    APPLIED,
    ONLINE_ASSESSMENT,
    INTERVIEW,
    OFFER,
    REJECTED,
    WITHDRAWN,
)

# Legal next stages for each stage. Anything not listed here is refused.
# Discovered -> Offer is deliberately absent — CLAUDE.md invariant 14 names
# that exact jump as the one a state machine exists to prevent.
TRANSITIONS: dict[str, tuple[str, ...]] = {
    DISCOVERED: (APPLIED, WITHDRAWN),
    APPLIED: (ONLINE_ASSESSMENT, INTERVIEW, REJECTED, WITHDRAWN),
    ONLINE_ASSESSMENT: (INTERVIEW, REJECTED, WITHDRAWN),
    # INTERVIEW -> INTERVIEW covers a second/third round without inventing a
    # stage for each one; it's still a real, timestamped event.
    INTERVIEW: (INTERVIEW, OFFER, REJECTED, WITHDRAWN),
    # Accepting/declining an offer lives outside this system (nothing here
    # tracks it); withdrawing after an offer is the one move left open.
    OFFER: (WITHDRAWN,),
    REJECTED: (),
    WITHDRAWN: (),
}

# The loose status strings jobrank.storage.set_status already accepts (see
# config/settings.py APPLICATION_STAGES), mapped onto the stages above so a
# one-time migration can read pre-existing applications.db rows without
# altering them. "" means set_status() was never called for that row.
#
# "ghosted" (the board went quiet) has no exact equivalent here — WITHDRAWN
# is the nearest existing stage with no further activity, so migrated rows
# land there rather than inventing an eighth stage for one legacy value.
LEGACY_STATUS_MAP: dict[str, str] = {
    "": DISCOVERED,
    "applied": APPLIED,
    "oa": ONLINE_ASSESSMENT,
    "interview": INTERVIEW,
    "offer": OFFER,
    "rejected": REJECTED,
    "ghosted": WITHDRAWN,
}

# Stages where nothing further is expected — used by followups.py to skip
# applications that already have an answer.
CLOSED_STAGES: frozenset[str] = frozenset({OFFER, REJECTED, WITHDRAWN})

# Measured, not guessed: of 45 applications tracked so far, 30 had gone this
# many days or more without any recorded event. Change the number here, never
# in followups.py.
OVERDUE_AFTER_DAYS = 21
