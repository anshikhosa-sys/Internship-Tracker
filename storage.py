"""
Storage — a local SQLite database. No server, just a file (internships.db).

SQLite ships with Python, so there's nothing to install and nothing to run. The
whole database is one file you can delete to start over, or copy to back up.

THE MOST IMPORTANT IDEA IN THIS FILE
------------------------------------
There are two kinds of data here, and they must never mix:

  FETCHED DATA  (the `postings` table)
      Comes from the internet. Thrown away and rewritten on every run.

  YOUR DATA     (the `applications` table)
      Which roles you marked as applied. Irreplaceable — if this is lost,
      there is no way to get it back.

They live in separate tables joined by posting ID. That means the refresh step
can overwrite every posting field it likes and physically cannot touch your
applied marks. If both lived in one table, a single careless UPDATE during a
refresh would silently wipe weeks of tracking.

This is a habit worth keeping in every project you build: derived data and
user-entered data get separate homes.

HOW THE "NEW" FLAG WORKS
------------------------
The source has no reliable posted-date (see sources/simplify_readme.py), so we
don't trust it for this. Instead, every posting records `first_seen` — the
timestamp of the run that first saw it. The `runs` table records when each run
happened. A posting is NEW if its first_seen equals the latest run's timestamp.
That's exact, and it stays correct even if the source's "Age" column is wrong
or the repo reshuffles its rows.
"""

import json
import sqlite3
from datetime import datetime, timezone

import config


# =============================================================================
# Schema
# =============================================================================

SCHEMA = """
-- Fetched data. Rewritten every run.
CREATE TABLE IF NOT EXISTS postings (
    id                   TEXT PRIMARY KEY,
    source               TEXT,
    company              TEXT,
    role                 TEXT,
    category             TEXT,
    location             TEXT,
    apply_url            TEXT,
    simplify_url         TEXT,
    age_text             TEXT,
    date_posted          TEXT,     -- approximate, derived from "Age"
    is_faang             INTEGER,
    needs_advanced_degree INTEGER,
    no_sponsorship       INTEGER,
    citizenship_required INTEGER,
    fit_score            INTEGER,
    score_reasons        TEXT,     -- JSON list of {label, points}
    first_seen           TEXT,     -- OUR timestamp: drives the NEW flag
    last_seen            TEXT,
    is_active            INTEGER   -- 0 once it drops off the source
);

-- YOUR data. Never touched by a refresh.
CREATE TABLE IF NOT EXISTS applications (
    posting_id  TEXT PRIMARY KEY,
    applied     INTEGER NOT NULL DEFAULT 0,
    notes       TEXT DEFAULT '',
    updated_at  TEXT
);

-- One row per refresh, so we know what "since last time" means.
CREATE TABLE IF NOT EXISTS runs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ran_at        TEXT NOT NULL,
    postings_seen INTEGER,
    new_count     INTEGER
);

-- Indexes: make sorting by score and filtering by active fast.
CREATE INDEX IF NOT EXISTS idx_postings_score ON postings(fit_score DESC);
CREATE INDEX IF NOT EXISTS idx_postings_active ON postings(is_active);
"""


def connect(path: str = None) -> sqlite3.Connection:
    """
    Open (and if needed create) the database.

    row_factory = sqlite3.Row makes query results behave like dictionaries —
    row["company"] instead of row[2] — which is far easier to read and doesn't
    break when the column order changes.
    """
    conn = sqlite3.connect(path or config.DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def now_iso() -> str:
    """Current UTC time as an ISO string. UTC avoids timezone/DST confusion."""
    return datetime.now(timezone.utc).isoformat()


# =============================================================================
# Reading run history
# =============================================================================

def last_run_time(conn):
    """
    When did the previous refresh happen? Returns None on the very first run.
    """
    row = conn.execute(
        "SELECT ran_at FROM runs ORDER BY id DESC LIMIT 1"
    ).fetchone()
    return row["ran_at"] if row else None


# =============================================================================
# Writing postings
# =============================================================================

def save_postings(conn, postings, run_time: str) -> dict:
    """
    Save this run's postings and work out which ones are new.

    Returns a summary dict with the keys "total", "new_ids",
    "is_first_run" and "deactivated".

    The interesting part is the UPSERT below. For each posting we either:
      - INSERT it (first time we've seen it) with first_seen = now, or
      - UPDATE it (seen before), refreshing the details but DELIBERATELY
        leaving first_seen alone.

    Preserving first_seen is what keeps the NEW flag honest. If we overwrote it
    every run, every posting would look new forever.
    """
    previous_run = last_run_time(conn)
    is_first_run = previous_run is None

    seen_ids = []

    for posting in postings:
        seen_ids.append(posting.id)
        conn.execute(
            """
            INSERT INTO postings (
                id, source, company, role, category, location,
                apply_url, simplify_url, age_text, date_posted,
                is_faang, needs_advanced_degree, no_sponsorship,
                citizenship_required, fit_score, score_reasons,
                first_seen, last_seen, is_active
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)
            ON CONFLICT(id) DO UPDATE SET
                source        = excluded.source,
                company       = excluded.company,
                role          = excluded.role,
                category      = excluded.category,
                location      = excluded.location,
                apply_url     = excluded.apply_url,
                simplify_url  = excluded.simplify_url,
                age_text      = excluded.age_text,
                date_posted   = excluded.date_posted,
                is_faang      = excluded.is_faang,
                needs_advanced_degree = excluded.needs_advanced_degree,
                no_sponsorship        = excluded.no_sponsorship,
                citizenship_required  = excluded.citizenship_required,
                fit_score     = excluded.fit_score,
                score_reasons = excluded.score_reasons,
                last_seen     = excluded.last_seen,
                is_active     = 1
                -- NOTE: first_seen is intentionally NOT updated here.
            """,
            (
                posting.id, posting.source, posting.company, posting.role,
                posting.category, posting.location, posting.apply_url,
                posting.simplify_url, posting.age_text, posting.date_posted,
                int(posting.is_faang), int(posting.needs_advanced_degree),
                int(posting.no_sponsorship), int(posting.citizenship_required),
                posting.fit_score, json.dumps(posting.score_reasons),
                run_time, run_time,
            ),
        )

    # Anything we didn't see this run has dropped off the source — the role was
    # filled or pulled. Mark it inactive rather than deleting it, so that if
    # you'd applied to it, your record survives.
    placeholders = ",".join("?" for _ in seen_ids) or "''"
    deactivated = conn.execute(
        f"UPDATE postings SET is_active = 0 "
        f"WHERE is_active = 1 AND id NOT IN ({placeholders})",
        seen_ids,
    ).rowcount

    # Which of these are NEW?
    #
    # Every posting's first_seen is stamped with the run_time of whichever run
    # first saw it. So "new in this run" is exactly "first_seen == run_time" —
    # no range comparison needed.
    #
    # (An earlier version used `first_seen >= previous_run_time`. That looked
    # right but was off by one run: postings inserted during the PREVIOUS run
    # have first_seen exactly equal to that timestamp, so `>=` matched them
    # too and reported them as new a second time. Testing caught it.)
    #
    # On the first run everything is technically new, which is useless
    # information — so we treat the first run as a baseline and report zero.
    if is_first_run:
        new_ids = set()
    else:
        rows = conn.execute(
            "SELECT id FROM postings WHERE first_seen = ? AND is_active = 1",
            (run_time,),
        ).fetchall()
        new_ids = {row["id"] for row in rows}

    conn.commit()

    return {
        "total": len(seen_ids),
        "new_ids": new_ids,
        "is_first_run": is_first_run,
        "deactivated": deactivated,
    }


def record_run(conn, run_time: str, total: int, new_count: int) -> None:
    """Log this refresh so the next one knows what 'since last time' means."""
    conn.execute(
        "INSERT INTO runs (ran_at, postings_seen, new_count) VALUES (?,?,?)",
        (run_time, total, new_count),
    )
    conn.commit()


# =============================================================================
# Reading postings back out (used by the dashboard)
# =============================================================================

def load_postings(conn, include_inactive: bool = False) -> list:
    """
    Load postings, best fit first, with your applied status attached.

    The LEFT JOIN is doing real work here: it says "attach the application row
    if one exists, otherwise leave those columns NULL". A plain JOIN would
    return ONLY postings you'd already marked — i.e. an empty dashboard until
    you'd applied to something.
    """
    where = "" if include_inactive else "WHERE p.is_active = 1"

    rows = conn.execute(
        f"""
        SELECT p.*,
               COALESCE(a.applied, 0) AS applied,
               COALESCE(a.notes, '')  AS notes
        FROM postings p
        LEFT JOIN applications a ON a.posting_id = p.id
        {where}
        ORDER BY p.fit_score DESC, p.company COLLATE NOCASE, p.role
        """
    ).fetchall()

    postings = []
    for row in rows:
        item = dict(row)
        # score_reasons is stored as a JSON string; decode it for the template.
        try:
            item["score_reasons"] = json.loads(item["score_reasons"] or "[]")
        except (json.JSONDecodeError, TypeError):
            item["score_reasons"] = []
        postings.append(item)

    return postings


def new_posting_ids(conn) -> set:
    """
    IDs first seen during the most recent run — what the dashboard badges NEW.

    We fetch the last TWO runs. If there's only one, this is the baseline run
    and nothing should be badged (everything would be "new", which tells you
    nothing). Otherwise, new means first_seen equals the latest run's
    timestamp — the same rule save_postings() uses.
    """
    runs = conn.execute(
        "SELECT ran_at FROM runs ORDER BY id DESC LIMIT 2"
    ).fetchall()

    if len(runs) < 2:
        return set()   # baseline run only — nothing to compare against

    rows = conn.execute(
        "SELECT id FROM postings WHERE first_seen = ? AND is_active = 1",
        (runs[0]["ran_at"],),
    ).fetchall()
    return {r["id"] for r in rows}


# =============================================================================
# Writing YOUR data
# =============================================================================

def set_applied(conn, posting_id: str, applied: bool) -> None:
    """Mark a posting as applied / not applied."""
    conn.execute(
        """
        INSERT INTO applications (posting_id, applied, updated_at)
        VALUES (?,?,?)
        ON CONFLICT(posting_id) DO UPDATE SET
            applied    = excluded.applied,
            updated_at = excluded.updated_at
        """,
        (posting_id, int(applied), now_iso()),
    )
    conn.commit()


def applied_count(conn) -> int:
    """How many roles you've marked as applied."""
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM applications WHERE applied = 1"
    ).fetchone()
    return row["n"] if row else 0
