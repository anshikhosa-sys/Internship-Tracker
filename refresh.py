"""
refresh.py — the command you run to update your listings.

    python3 refresh.py

It does four things, in order:

    1. FETCH    ask each source for its postings
    2. SCORE    rank them using the weights in config.py
    3. STORE    save to SQLite, preserving first_seen and your applied marks
    4. REPORT   print what's new since last time

Run this whenever you want fresh data (the source repo updates daily). Then
run `python3 app.py` to browse the results.

ADDING A SECOND SOURCE LATER
---------------------------
When you graduate and want SimplifyJobs/New-Grad-Positions too:

    1. Add a file in sources/ that returns Posting objects.
    2. Add it to the SOURCES list below.

That's the whole change. Scoring, storage, and the dashboard don't need to know
a second source exists — they only ever deal with Posting objects. This is why
the fetch logic was isolated behind the Source class in the first place.
"""

import sys
import traceback

import requests

import dedupe
import notify
import push
import scorer
import storage
from sources import (
    ChielerReadmeSource,
    DereC4ReadmeSource,
    SimplifyReadmeSource,
    SndshReadmeSource,
    SpeedyApplyAISource,
    SpeedyApplyReadmeSource,
    VanshReadmeSource,
)


# Every source to pull from. Add new ones here.
#
# Order matters slightly: when the same job appears in several lists, the
# FIRST one seen becomes the base record and later copies are merged into it.
# Simplify leads because it's the only source that labels roles by category,
# which the candidacy score uses.
SOURCES = [
    SimplifyReadmeSource(),
    VanshReadmeSource(),
    SpeedyApplyReadmeSource(),
    SpeedyApplyAISource(),
    SndshReadmeSource(),
    # Added for coverage: between them these carry ~1,600 company+role
    # combinations none of the lists above had. Chieler also publishes an
    # exact ISO date per row, which dedupe.py prefers over a date derived
    # from a relative age — so it improves postings we already held.
    ChielerReadmeSource(),
    DereC4ReadmeSource(),
]


def _stamp() -> str:
    """Local time, for the log.

    The log had no timestamps, which meant that after the fact there was no
    way to tell what time a scheduled run actually happened — only the file's
    modification time, which is the LAST write, not the first. That made a
    one-hour discrepancy between the configured schedule and the observed
    log impossible to diagnose. Every run now says when it started.
    """
    from datetime import datetime
    return datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")


def refresh(verbose: bool = True, notifications: bool = True) -> dict:
    """
    Run the full pipeline once. Returns a summary dict.

    `notifications` is False when called from the dashboard's Refresh button —
    you're already looking at the results, so a pop-up would be noise. The
    scheduled job leaves it True.
    """

    run_time = storage.now_iso()
    conn = storage.connect()

    if verbose:
        print(f"=== run started {_stamp()} ===")

    # -- 1. FETCH -----------------------------------------------------------
    all_postings = []
    for source in SOURCES:
        if verbose:
            print(f"Fetching from {source.name}...")
        try:
            postings = source.fetch()
            all_postings.extend(postings)
            if verbose:
                print(f"  got {len(postings)} postings")
        except requests.RequestException as exc:
            # A network failure in ONE source shouldn't kill the whole run —
            # that matters more once you have several sources. We report it
            # clearly and carry on with whatever else succeeded.
            print(f"  ERROR fetching {source.name}: {exc}", file=sys.stderr)
        except Exception:
            print(f"  UNEXPECTED ERROR parsing {source.name}:",
                  file=sys.stderr)
            traceback.print_exc()

    if not all_postings:
        print("\nNo postings fetched. Nothing was changed in the database.")
        print("Check your internet connection, then try again.")
        conn.close()
        return {"total": 0, "new": 0, "notified": False, "failed": True}

    # -- 1b. DEDUPLICATE ----------------------------------------------------
    # The lists overlap heavily. Merging keeps the best field from each copy —
    # an absolute date beats a derived one, a real category beats
    # "Uncategorized", a salary beats none.
    all_postings, dedupe_stats = dedupe.deduplicate(all_postings)
    if verbose:
        print(f"  {dedupe_stats['duplicates_merged']} duplicates merged "
              f"-> {dedupe_stats['output']} unique postings")
        print(f"  {dedupe_stats['in_multiple_sources']} appear in more than "
              f"one list")

    # -- 2. SCORE -----------------------------------------------------------
    # Scores are always recomputed from scratch, so editing config.py and
    # re-running is all it takes to change the rankings.
    if verbose:
        print("\nScoring...")
    scored = scorer.score_all(all_postings)

    dropped = len(all_postings) - len(scored)
    if verbose and dropped:
        print(f"  filtered out {dropped} non-internship postings")

    # -- 3. STORE -----------------------------------------------------------
    result = storage.save_postings(conn, scored, run_time)

    # If any posting ids moved, re-link the applied marks that pointed at
    # the old ones. Normally recovers nothing; when it does, say so, because
    # silently losing these is the worst failure this tool has.
    recovered = storage.reattach_orphaned_marks(conn)
    if recovered:
        print(f"  re-linked {recovered} applied marks whose posting id moved")

    storage.record_run(conn, run_time, result["total"], len(result["new_ids"]))

    # -- 4. REPORT ----------------------------------------------------------
    if verbose:
        _print_report(scored, result, conn)

    # Notify about strong new matches. This is best-effort: notify.py never
    # raises, so a notification problem can't stop the data from updating.
    notified = False
    if notifications and result["new_ids"]:
        new_postings = [p for p in scored if p.id in result["new_ids"]]
        notified = notify.notify_strong_matches(new_postings)
        # Phone push, if a topic is configured. Same threshold, so the two
        # never disagree about what's worth interrupting you for.
        if push.notify_matches(new_postings) and verbose:
            print("  pushed to phone")

    conn.close()
    return {
        "total": result["total"],
        "new": len(result["new_ids"]),
        "notified": notified,
        "failed": False,
    }


def _print_report(scored, result, conn) -> None:
    """Print a human-readable summary of the run."""

    print(f"\n{'=' * 66}")
    print(f"  {result['total']} active postings stored")

    if result["deactivated"]:
        print(f"  {result['deactivated']} postings dropped off the source "
              f"(filled or closed) — marked inactive, not deleted")

    # -- what's new ---------------------------------------------------------
    if result["is_first_run"]:
        print("\n  First run — baseline saved.")
        print("  Postings added before your next run will be flagged NEW.")
    elif result["new_ids"]:
        new_ones = [p for p in scored if p.id in result["new_ids"]]
        new_ones.sort(key=lambda p: -p.fit_score)
        print(f"\n  {len(new_ones)} NEW since your last run:")
        for posting in new_ones[:15]:
            print(f"    {posting.fit_score:4d}  {posting.company[:22]:22s} "
                  f"{posting.role[:46]}")
        if len(new_ones) > 15:
            print(f"    ...and {len(new_ones) - 15} more")
    else:
        print("\n  No new postings since your last run.")

    # -- top matches --------------------------------------------------------
    print("\n  Your top 10 matches right now:")
    for posting in scored[:10]:
        flag = "NEW " if posting.id in result["new_ids"] else "    "
        print(f"    {flag}{posting.fit_score:4d}  {posting.company[:22]:22s} "
              f"{posting.role[:44]}")

    applied = storage.applied_count(conn)
    if applied:
        print(f"\n  You've marked {applied} as applied.")

    print(f"{'=' * 66}")
    print("\nRun `python3 app.py` to open the dashboard.")


if __name__ == "__main__":
    refresh()
