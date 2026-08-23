"""
selfcheck.py — run the health check on a schedule and speak up only if
something is actually wrong.

WHY
---
`healthcheck.py` is thorough, but it only helps if you remember to run it,
and the whole point of this project is not having to remember things. So a
LaunchAgent runs this weekly. It is silent when everything works, and
notifies when it doesn't.

WHAT COUNTS AS "WRONG"
----------------------
Only FAILures, never warnings. Warnings are things worth knowing when you
happen to look — "TikTok is crowding the top of your list" is not worth
interrupting you for. A notification that fires for non-problems gets
ignored, and then the one that matters gets ignored too.

The dashboard also shows the last self-check result, so a notification you
miss is not lost.
"""

import json
import os
import subprocess
import sys
from datetime import datetime, timezone

import config
import notify
import storage

# Where the last result is recorded, so the dashboard can show it.
STATE_KEY = "selfcheck"


def run(verbose: bool = True) -> dict:
    """Run healthcheck.py, record the result, notify only on failure."""
    here = os.path.dirname(os.path.abspath(__file__))

    result = subprocess.run(
        [sys.executable, os.path.join(here, "healthcheck.py")],
        capture_output=True, text=True, timeout=600, cwd=here,
    )

    failures = [line.strip() for line in result.stdout.splitlines()
                if "[FAIL]" in line]
    warnings = [line.strip() for line in result.stdout.splitlines()
                if "[WARN]" in line]

    outcome = {
        "ran_at": datetime.now(timezone.utc).isoformat(),
        "ok": result.returncode == 0,
        "failures": failures,
        "warning_count": len(warnings),
    }

    conn = storage.connect()
    try:
        storage.set_state(conn, STATE_KEY, json.dumps(outcome))
        conn.commit()
    finally:
        conn.close()

    if verbose:
        print(result.stdout)

    # Silence is the success case. Only a real failure earns an interruption.
    if failures:
        first = failures[0].split("]", 1)[-1].strip()
        notify.send(
            "Internship Finder needs attention",
            first[:180],
            subtitle=(f"{len(failures)} check"
                      f"{'s' if len(failures) != 1 else ''} failing"),
        )

    return outcome


def last_result(conn):
    """The most recent self-check outcome, or None. For the dashboard."""
    raw = storage.get_state(conn, STATE_KEY)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


if __name__ == "__main__":
    summary = run(verbose="--quiet" not in sys.argv)
    if summary["failures"]:
        print(f"\n{len(summary['failures'])} check(s) failing — "
              f"a notification was sent.")
        sys.exit(1)
    print("\nSelf-check passed.")
