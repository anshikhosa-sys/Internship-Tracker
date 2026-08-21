"""
notify.py — macOS notifications when strong matches appear.

Used by the scheduled daily refresh. If a run finds new postings scoring at or
above config.STRONG_FIT_THRESHOLD, you get a Notification Center alert instead
of having to remember to check the dashboard.

HOW IT WORKS
------------
macOS can display a notification from the command line via AppleScript:

    osascript -e 'display notification "text" with title "title"'

`osascript` ships with macOS, so this needs no extra dependency and no
account. On any other operating system it simply does nothing.

WHY EVERYTHING HERE FAILS QUIETLY
---------------------------------
This runs from a background scheduled job. A notification failing is a minor
inconvenience; the same failure crashing the refresh would mean your listings
silently stop updating — a much worse outcome for something you won't be
watching. So every failure path here returns False rather than raising, and
refresh.py treats notifying as optional.
"""

import platform
import shutil
import subprocess

import config


def _escape(text: str) -> str:
    """
    Make text safe to embed in an AppleScript string literal.

    AppleScript strings are double-quoted, so a stray quote or backslash in a
    company name would break the script — or, in the worst case, let text from
    a job posting run as code. Company and role names come from a third-party
    README, so they're untrusted input and get escaped rather than trusted.
    """
    return text.replace("\\", "\\\\").replace('"', '\\"')


def is_available() -> bool:
    """True if we're on macOS and osascript exists."""
    return (platform.system() == "Darwin"
            and shutil.which("osascript") is not None)


def send(title: str, message: str, subtitle: str = "") -> bool:
    """
    Show a notification. Returns True if it was displayed.

    Never raises — see the module docstring for why.
    """
    if not is_available():
        return False

    script = (
        f'display notification "{_escape(message)}" '
        f'with title "{_escape(title)}"'
    )
    if subtitle:
        script += f' subtitle "{_escape(subtitle)}"'

    try:
        subprocess.run(
            ["osascript", "-e", script],
            check=True,
            capture_output=True,
            timeout=10,
        )
        return True
    except (subprocess.SubprocessError, OSError):
        return False


def notify_strong_matches(new_postings) -> bool:
    """
    Given the postings that are new this run, notify about the good ones.

    "Good" means at or above config.NOTIFY_THRESHOLD — which is deliberately a
    different number from STRONG_FIT_THRESHOLD. See the comment on
    NOTIFY_THRESHOLD in config.py for why tying these together kept the
    feature silent while relevant roles went by.

    Returns True if a notification was sent. Does nothing if notifications are
    switched off, or if nothing new cleared the threshold.
    """
    if not config.NOTIFY_ON_STRONG_FIT:
        return False

    matches = [
        posting for posting in new_postings
        if posting.fit_score >= config.NOTIFY_THRESHOLD
    ]
    if not matches:
        return False

    matches.sort(key=lambda p: -p.fit_score)
    best = matches[0]

    # One notification per run, not one per posting — a morning that turns up
    # seven matches should be a single alert, not seven.
    if len(matches) == 1:
        title = "1 new internship match"
    else:
        title = f"{len(matches)} new internship matches"

    # Lead with the best one, since a notification only has room for so much.
    message = f"{best.company} — {best.role}"
    if len(matches) > 1:
        message += f"  (+{len(matches) - 1} more)"

    return send(title, message, subtitle=f"Best fit score: {best.fit_score}")
