"""
Which profile the dashboard, refresh report, notifications and prompts use.

Resolution order: the profile chosen in the dashboard (stored in app_state),
then settings.DEFAULT_PROFILE_ID, then the first real profile on disk. The
committed `example` profile is used only when nothing else exists, so a fresh
clone still renders a ranked list.
"""

from __future__ import annotations

from jobrank import storage
from jobrank.config import settings
from jobrank.profile import store

STATE_KEY = "active_profile"
EXAMPLE_ID = "example"


def resolve(requested: str | None = None, conn=None) -> str | None:
    available = store.list_ids()
    if requested and requested in available:
        return requested
    chosen = None
    if conn is not None:
        chosen = storage.get_state(conn, STATE_KEY)
    else:
        own = storage.connect()
        try:
            chosen = storage.get_state(own, STATE_KEY)
        finally:
            own.close()
    for candidate in (chosen, settings.DEFAULT_PROFILE_ID):
        if candidate and candidate in available:
            return candidate
    real = [pid for pid in available if pid != EXAMPLE_ID]
    if real:
        return real[0]
    return EXAMPLE_ID if EXAMPLE_ID in available else None


def set_active(conn, user_id: str) -> None:
    store.validate_user_id(user_id)
    storage.set_state(conn, STATE_KEY, user_id)
