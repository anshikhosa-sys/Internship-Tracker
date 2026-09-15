"""
push.py — send a notification to your phone.

Uses ntfy.sh: a free, open push service that needs no account, no API key,
and no app store purchase. You pick a secret topic name, the script POSTs to
https://ntfy.sh/<topic>, and the ntfy app on your phone (iOS or Android)
subscribed to that topic receives it.

SETUP, ONCE
-----------
  1. Install "ntfy" from the App Store or Play Store (free).
  2. In the app, Subscribe to a topic. Pick something unguessable, e.g.
     internships-ansh-7f3k9q2  — treat it like a password.
  3. Put that same string in PUSH_TOPIC in config.py.

That's it. No account anywhere.

THE ONE THING TO UNDERSTAND ABOUT PRIVACY
-----------------------------------------
ntfy topics are public to anyone who knows the name. There's no auth on the
free tier — knowing the topic IS the credential. So:

  - Pick a long random topic name. A guessable one means strangers get your
    notifications, and could send you fake ones.
  - Never put anything sensitive in the message. This sends company names and
    job titles, which are already public listings. It does NOT send your
    resume, your applications, or anything about you.

If that tradeoff isn't acceptable, leave PUSH_TOPIC empty and macOS
notifications keep working exactly as before.
"""

import urllib.error
import urllib.request

from jobrank import config


def is_configured() -> bool:
    """True if a push topic has been set."""
    return bool((config.PUSH_TOPIC or "").strip())


def send(title: str, message: str, url: str = "") -> bool:
    """
    Send one push. Returns True if it was accepted.

    Never raises. This runs from a background job where a failed
    notification is a minor annoyance, but an exception would stop the
    listings updating — a far worse outcome for something you aren't
    watching.
    """
    if not is_configured():
        return False

    topic = config.PUSH_TOPIC.strip()
    endpoint = f"https://ntfy.sh/{topic}"

    headers = {
        "Title": title.encode("utf-8"),
        "Priority": "default",
        "Tags": "briefcase",
    }
    if url:
        # Makes the notification tappable, straight to the dashboard.
        headers["Click"] = url

    request = urllib.request.Request(
        endpoint,
        data=message.encode("utf-8"),
        headers=headers,
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return 200 <= response.status < 300
    except (urllib.error.URLError, OSError, ValueError):
        return False


def notify_matches(new_postings) -> bool:
    """
    Push about new postings worth acting on.

    Mirrors notify.py's rule so the phone and the Mac never disagree about
    what counts as worth interrupting you for.
    """
    if not is_configured() or not config.NOTIFY_ON_STRONG_FIT:
        return False

    matches = [
        posting for posting in new_postings
        if posting.fit_score >= config.NOTIFY_THRESHOLD
    ]
    if not matches:
        return False

    matches.sort(key=lambda p: -p.fit_score)

    if len(matches) == 1:
        title = "1 new internship match"
    else:
        title = f"{len(matches)} new internship matches"

    # Name the top few rather than just a count — the point of a phone push
    # is deciding whether to stop what you're doing, and a bare number can't
    # answer that.
    lines = [
        f"{p.fit_score}  {p.company} — {p.role}"
        for p in matches[:3]
    ]
    if len(matches) > 3:
        lines.append(f"...and {len(matches) - 3} more")

    return send(title, "\n".join(lines), url=config.DASHBOARD_URL)


def send_test() -> bool:
    """Send a test push, for checking setup."""
    return send(
        "Internship Finder",
        "Phone notifications are working. You can dismiss this.",
        url=config.DASHBOARD_URL,
    )


if __name__ == "__main__":
    if not is_configured():
        print("PUSH_TOPIC is not set in config.py — see the notes in this "
              "file for the one-time setup.")
    elif send_test():
        print(f"Test push sent to topic '{config.PUSH_TOPIC}'.")
        print("If nothing arrived, check the ntfy app is subscribed to that "
              "exact topic.")
    else:
        print("Push failed. Check your internet connection and the topic "
              "name in config.py.")
