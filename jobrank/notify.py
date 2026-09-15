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

from jobrank import config


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
    Ask macOS to show a notification.

    Returns True if the request was ACCEPTED, which is not the same as
    delivered and must not be reported as such. `osascript` exits 0 whether
    the banner appears or is silently dropped — by a Focus mode, by Script
    Editor's alert style being None, or by the notification going straight
    to Notification Center unseen. Nothing available to a background job
    can distinguish those cases, so this promises only what it knows.

    That is also why the dashboard banner exists: it is the channel that
    cannot be suppressed. See `python3 notify.py` to test delivery by eye.

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


# =============================================================================
# Self-test
# =============================================================================
#
# `python3 notify.py` sends a real notification and tells you what to do if
# it doesn't appear. Delivery cannot be detected in software, so the only
# honest test is one where you look at the screen.

INSTRUCTIONS = """
If no banner appeared, macOS suppressed it. Notifications sent this way are
attributed to SCRIPT EDITOR, not to this project, so that is the app to look
for in Settings.

On macOS 15 (Sequoia):

  1. System Settings > Notifications
  2. Scroll to "Script Editor" under Application Notifications
     (if it is absent, run this script once more — sending a notification is
     what registers the app in that list, and it takes a moment to appear)
  3. Turn ON "Allow notifications"
  4. Set the alert style to "Banners" or "Alerts"
       - Banners disappear on their own
       - Alerts stay until dismissed, which is the better choice here:
         a refresh can fire while you are away from the machine
  5. Turn ON "Show in Notification Center" and "Show on Lock Screen"

Then check Focus:

  - Control Centre > Focus. Any active mode silences banners.
  - System Settings > Focus > [your mode] > Allowed Notifications, and add
    Script Editor, if you want alerts to come through while focused.

Two things worth knowing:

  - A notification that arrives while the screen is locked or asleep goes
    to Notification Center without a banner. It is not lost; click the
    clock in the top-right corner to see it.
  - The dashboard banner always shows new postings and cannot be
    suppressed by any of the above. Notifications are a convenience; the
    dashboard is the reliable channel.
"""


if __name__ == "__main__":
    if not is_available():
        print("Not macOS, or osascript is missing — notifications are off.")
        raise SystemExit(0)

    accepted = send(
        "Internship Finder",
        "If you can read this, notifications are working.",
        subtitle="Test notification",
    )

    if not accepted:
        print("osascript refused the request. Notifications will not work.")
        raise SystemExit(1)

    print("Sent. Look at the top-right of your screen now.")
    print()
    print("macOS accepted the request — but it exits 0 even when it drops")
    print("the notification, so this cannot confirm you saw anything.")
    print(INSTRUCTIONS)
