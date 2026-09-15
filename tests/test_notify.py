"""
Notification honesty: osascript exits 0 whether or not macOS actually showed
the notification, so notify.send() must not claim delivery it can't verify.

Ported from tests/legacy_checks.py (test_notify_honesty), unchanged — notify.py
was not touched by the v1 -> v2 rebuild.
"""

from jobrank import notify


def test_notify_send_exists_and_does_not_claim_delivery():
    assert hasattr(notify, "send"), "notify.send exists"
    doc = (notify.send.__doc__ or "").lower()
    assert "deliver" in doc or "displayed" not in doc, (
        "send() does not claim the notification was displayed"
    )


def test_escape_handles_quotes_and_backslashes():
    """
    Escaping matters: company and role names come from a third-party README,
    so they are untrusted input for the AppleScript string they get embedded
    into.
    """
    assert '\\"' in notify._escape('a "quoted" name'), (
        "quotes in a posting title are escaped, not injected"
    )
    assert "\\\\" in notify._escape("back\\slash"), "backslashes are escaped"
