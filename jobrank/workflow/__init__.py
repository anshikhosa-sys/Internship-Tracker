"""
Application workflow: the state machine (states.py) and follow-up tracking
(followups.py) built on top of applications.db.

Neither module touches `applications.status`, the loose stage string
storage.set_status() already writes. They add a second, append-only ledger —
the `application_events` table — that timestamps every legal move and
refuses an illegal one, so an application's history can be reconstructed
instead of only ever showing its current stage.
"""
