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
    date_posted          TEXT,     -- exact from some sources, derived others
    salary               TEXT,     -- only some sources publish this
    sources              TEXT,     -- JSON list of every list it appeared in
    is_faang             INTEGER,
    needs_advanced_degree INTEGER,
    no_sponsorship       INTEGER,
    citizenship_required INTEGER,
    -- The score is three factors multiplied. preference and candidacy are
    -- stable and stored; freshness is NOT — it changes daily, so it's
    -- recomputed at display time. fit_score is a snapshot from the last run,
    -- used by the CLI report and notifications.
    preference           REAL,
    preference_reasons   TEXT,     -- JSON list of {label, detail}
    candidacy_score      REAL,
    candidacy_reasons    TEXT,     -- JSON list of {label, detail}
    role_family          TEXT,     -- matched ROLE_FAMILIES entry
    fit_score            INTEGER,  -- snapshot: preference x candidacy x fresh
    first_seen           TEXT,     -- OUR timestamp: drives the NEW flag
    last_seen            TEXT,
    is_active            INTEGER   -- 0 once it drops off the source
);

-- YOUR data. Never touched by a refresh.
CREATE TABLE IF NOT EXISTS applications (
    posting_id  TEXT PRIMARY KEY,
    applied     INTEGER NOT NULL DEFAULT 0,
    notes       TEXT DEFAULT '',
    updated_at  TEXT,
    -- The company and role are stored ALONGSIDE the id deliberately.
    -- If a posting's id ever changes anyway, these let the mark be found
    -- and re-attached instead of silently orphaned. This is the only data
    -- in the database that cannot be regenerated, so it gets a backup key.
    company     TEXT DEFAULT '',
    role        TEXT DEFAULT '',
    -- Where this application has got to. Empty means not applied.
    -- `applied_at` is set once, when it first becomes an application, so
    -- "how long have I been waiting" stays answerable after a stage change.
    status      TEXT DEFAULT '',
    applied_at  TEXT DEFAULT ''
);

-- One row per refresh, so we know what "since last time" means.
CREATE TABLE IF NOT EXISTS runs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ran_at        TEXT NOT NULL,
    postings_seen INTEGER,
    new_count     INTEGER
);

-- Small key/value store for app state. Currently holds the two timestamps
-- that decide what counts as "new to you" — see register_visit().
CREATE TABLE IF NOT EXISTS app_state (
    key   TEXT PRIMARY KEY,
    value TEXT
);

"""

# Indexes are created SEPARATELY, after the migration runs.
#
# They reference columns (fit_score) that an older database won't have yet.
# Creating them inside SCHEMA means they execute before ALTER TABLE has
# added those columns, and SQLite fails with "no such column" — turning a
# routine upgrade into a crash on startup. A test pins the order.
INDEXES = """
CREATE INDEX IF NOT EXISTS idx_postings_score ON postings(fit_score DESC);
CREATE INDEX IF NOT EXISTS idx_postings_active ON postings(is_active);
"""


def connect(path: str = None) -> sqlite3.Connection:
    """
    Open (and if needed create) the database, migrating it if the schema has
    grown since it was made.

    row_factory = sqlite3.Row makes query results behave like dictionaries —
    row["company"] instead of row[2] — which is easier to read and doesn't
    break when the column order changes.

    WHY THE MIGRATION EXISTS
    ------------------------
    CREATE TABLE IF NOT EXISTS does nothing to a table that already exists,
    so adding a column to SCHEMA above would leave older databases missing
    it, and every insert would fail. The tempting fix is to delete the
    database and start over — which is exactly how you lose applied marks,
    the one thing in here that can't be regenerated.

    So instead: compare the columns that exist against the columns the schema
    wants, and ADD the missing ones. Nothing is ever dropped or rewritten.
    """
    conn = sqlite3.connect(path or config.DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)      # tables first
    _migrate(conn)                  # then any columns added since
    conn.executescript(INDEXES)     # only then indexes, which need those
    return conn


# EVERY non-primary-key column the current schema expects, with its type.
#
# Deliberately the full list rather than only "columns added recently". A
# database can be missing any of them — one written by an older version, or
# one restored from a partial backup — and listing only the recent additions
# leaves the older gaps unfixed until something fails at query time.
#
# Adding a column to SCHEMA above means adding it here too. Anything present
# is left exactly as it is; nothing is ever dropped or rewritten.
_MIGRATIONS = {
    "postings": {
        "source": "TEXT",
        "company": "TEXT",
        "role": "TEXT",
        "category": "TEXT",
        "location": "TEXT",
        "apply_url": "TEXT",
        "simplify_url": "TEXT",
        "age_text": "TEXT",
        "date_posted": "TEXT",
        "salary": "TEXT",
        "sources": "TEXT",
        "is_faang": "INTEGER",
        "needs_advanced_degree": "INTEGER",
        "no_sponsorship": "INTEGER",
        "citizenship_required": "INTEGER",
        "preference": "REAL",
        "preference_reasons": "TEXT",
        "candidacy_score": "REAL",
        "candidacy_reasons": "TEXT",
        "role_family": "TEXT",
        "fit_score": "INTEGER",
        "first_seen": "TEXT",
        "last_seen": "TEXT",
        "is_active": "INTEGER",
    },
    "applications": {
        "applied": "INTEGER NOT NULL DEFAULT 0",
        "notes": "TEXT DEFAULT ''",
        "updated_at": "TEXT",
        "company": "TEXT DEFAULT ''",
        "role": "TEXT DEFAULT ''",
        "status": "TEXT DEFAULT ''",
        "applied_at": "TEXT DEFAULT ''",
    },
}


def _migrate(conn) -> None:
    """Add any columns the current schema wants that this database lacks."""
    for table, columns in _MIGRATIONS.items():
        existing = {
            row["name"]
            for row in conn.execute(f"PRAGMA table_info({table})")
        }
        for name, column_type in columns.items():
            if name not in existing:
                conn.execute(
                    f"ALTER TABLE {table} ADD COLUMN {name} {column_type}"
                )
    conn.commit()
    _backfill(conn)


def _backfill(conn) -> None:
    """
    Fill in values that a new column can't get from a DEFAULT alone.

    Adding a column is only half a migration. When `applied` (a boolean) was
    replaced by `status` (a stage), every existing mark got status='' — and
    since the pipeline view lists rows WHERE status != '', those applications
    would have silently disappeared from it. The data was still in the
    database, which makes it worse rather than better: it looks like loss
    with no error to investigate.
    """
    conn.execute(
        "UPDATE applications SET status = 'applied' "
        "WHERE applied = 1 AND (status IS NULL OR status = '')"
    )
    # An application with a stage but no timestamp predates applied_at.
    # Use the last update as the best available approximation rather than
    # leaving "days waiting" blank forever.
    conn.execute(
        "UPDATE applications SET applied_at = updated_at "
        "WHERE status != '' AND (applied_at IS NULL OR applied_at = '') "
        "AND updated_at IS NOT NULL"
    )
    conn.commit()


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
                salary, sources,
                is_faang, needs_advanced_degree, no_sponsorship,
                citizenship_required, preference, preference_reasons,
                candidacy_score, candidacy_reasons, role_family, fit_score,
                first_seen, last_seen, is_active
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)
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
                salary        = excluded.salary,
                sources       = excluded.sources,
                is_faang      = excluded.is_faang,
                needs_advanced_degree = excluded.needs_advanced_degree,
                no_sponsorship        = excluded.no_sponsorship,
                citizenship_required  = excluded.citizenship_required,
                preference        = excluded.preference,
                preference_reasons = excluded.preference_reasons,
                candidacy_score   = excluded.candidacy_score,
                candidacy_reasons = excluded.candidacy_reasons,
                role_family       = excluded.role_family,
                fit_score         = excluded.fit_score,
                last_seen     = excluded.last_seen,
                is_active     = 1
                -- NOTE: first_seen is intentionally NOT updated here.
            """,
            (
                posting.id, posting.source, posting.company, posting.role,
                posting.category, posting.location, posting.apply_url,
                posting.simplify_url, posting.age_text, posting.date_posted,
                posting.salary, json.dumps(posting.sources or []),
                int(posting.is_faang), int(posting.needs_advanced_degree),
                int(posting.no_sponsorship), int(posting.citizenship_required),
                posting.preference,
                json.dumps(posting.preference_reasons),
                posting.candidacy_score,
                json.dumps(posting.candidacy_reasons),
                posting.role_family,
                posting.fit_score,
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
               COALESCE(a.applied, 0)    AS applied,
               COALESCE(a.notes, '')     AS notes,
               COALESCE(a.status, '')    AS status,
               COALESCE(a.applied_at, '') AS applied_at
        FROM postings p
        LEFT JOIN applications a ON a.posting_id = p.id
        {where}
        ORDER BY p.fit_score DESC, p.company COLLATE NOCASE, p.role
        """
    ).fetchall()

    postings = []
    for row in rows:
        item = dict(row)
        # Reason lists are stored as JSON strings; decode for the template.
        for field in ("preference_reasons", "candidacy_reasons", "sources"):
            try:
                item[field] = json.loads(item.get(field) or "[]")
            except (json.JSONDecodeError, TypeError):
                item[field] = []
        postings.append(item)

    return postings


def new_posting_ids(conn) -> set:
    """
    IDs first seen during the most recent run.

    This is "new since the last REFRESH", which is what refresh.py reports on
    the command line. The dashboard uses new_since_last_visit() instead — see
    register_visit() for why those need to be different questions.
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
# "New to YOU" — tracking when you last looked
# =============================================================================
#
# WHY THIS EXISTS
# ---------------
# Before the daily scheduled refresh, "new" could safely mean "arrived in the
# last run", because you triggered every run yourself. Once a job refreshes
# every morning, that definition quietly breaks: "since the last run" becomes
# "since 6am today", so if you don't check for five days, five days of
# postings stop being flagged and you never see them.
#
# So we track two different things:
#
#   last_run     when the DATA was last updated   (the runs table)
#   last_visit   when YOU last looked             (here)
#
# NEW in the dashboard means "arrived since you last looked".
#
# THE SESSION IDEA
# ----------------
# If we advanced "last visit" on every page load, badges would vanish the
# moment you refreshed the page or clicked a filter — you'd see them once and
# lose them mid-browse.
#
# Instead we treat a burst of activity as one visit. Loading the page within
# VISIT_SESSION_MINUTES of your last activity continues the current visit and
# leaves the badges alone. Coming back later starts a new visit, and the
# badges then reflect everything that arrived since your previous one ended.

def _get_state(conn, key: str):
    row = conn.execute(
        "SELECT value FROM app_state WHERE key = ?", (key,)
    ).fetchone()
    return row["value"] if row else None


def _set_state(conn, key: str, value: str) -> None:
    conn.execute(
        """
        INSERT INTO app_state (key, value) VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (key, value),
    )


def register_visit(conn) -> str:
    """
    Record that the dashboard was opened, and return the timestamp that NEW
    badges should be measured against.

    Returns the "visit basis": postings first seen after this moment are new
    to you. See the section comment above for the session logic.
    """
    now = now_iso()
    last_activity = _get_state(conn, "last_activity")
    visit_basis = _get_state(conn, "visit_basis")

    if last_activity is None:
        # First time the dashboard has ever been opened. Everything currently
        # stored is pre-existing, not new — so measure from now.
        visit_basis = now
        _set_state(conn, "visit_basis", visit_basis)
    else:
        gap = _minutes_between(last_activity, now)
        if gap >= config.VISIT_SESSION_MINUTES:
            # Enough time has passed that this is a fresh visit. Anything that
            # arrived since your last visit ENDED is new to you.
            visit_basis = last_activity
            _set_state(conn, "visit_basis", visit_basis)
        # Otherwise: same visit still in progress, leave visit_basis alone so
        # the badges you're looking at don't disappear underneath you.

    _set_state(conn, "last_activity", now)
    conn.commit()

    return visit_basis or now


def _minutes_between(earlier_iso: str, later_iso: str) -> float:
    """Minutes between two ISO timestamps. Returns a huge number if unparseable
    so that a corrupt value starts a new visit rather than freezing badges."""
    try:
        earlier = datetime.fromisoformat(earlier_iso)
        later = datetime.fromisoformat(later_iso)
    except (TypeError, ValueError):
        return float("inf")
    return (later - earlier).total_seconds() / 60.0


def new_since_last_visit(conn, visit_basis: str) -> set:
    """IDs of active postings first seen after `visit_basis`."""
    if not visit_basis:
        return set()
    rows = conn.execute(
        "SELECT id FROM postings WHERE first_seen > ? AND is_active = 1",
        (visit_basis,),
    ).fetchall()
    return {r["id"] for r in rows}


def mark_all_seen(conn) -> None:
    """Clear all NEW badges — the 'Mark all as seen' button."""
    now = now_iso()
    _set_state(conn, "visit_basis", now)
    _set_state(conn, "last_activity", now)
    conn.commit()


# =============================================================================
# Writing YOUR data
# =============================================================================

def set_applied(conn, posting_id: str, applied: bool) -> None:
    """
    Mark a posting as applied / not applied.

    The company and role are copied in alongside the id. They're redundant
    while ids are stable — and they're the recovery key if one ever isn't.
    """
    row = conn.execute(
        "SELECT company, role FROM postings WHERE id = ?", (posting_id,)
    ).fetchone()
    company = row["company"] if row else ""
    role = row["role"] if row else ""

    conn.execute(
        """
        INSERT INTO applications
            (posting_id, applied, updated_at, company, role)
        VALUES (?,?,?,?,?)
        ON CONFLICT(posting_id) DO UPDATE SET
            applied    = excluded.applied,
            updated_at = excluded.updated_at,
            company    = excluded.company,
            role       = excluded.role
        """,
        (posting_id, int(applied), now_iso(), company, role),
    )
    conn.commit()


def set_status(conn, posting_id: str, status: str) -> None:
    """
    Move an application to a pipeline stage.

    `applied` stays in sync as a plain boolean so nothing that already reads
    it has to change. `applied_at` is stamped ONCE, the first time a posting
    leaves "not applied" — so it keeps answering "how long have I been
    waiting" even after the stage moves on.
    """
    valid = {stage["key"] for stage in config.APPLICATION_STAGES}
    if status not in valid:
        raise ValueError(f"unknown status: {status!r}")

    row = conn.execute(
        "SELECT company, role FROM postings WHERE id = ?", (posting_id,)
    ).fetchone()
    company = row["company"] if row else ""
    role = row["role"] if row else ""

    existing = conn.execute(
        "SELECT applied_at FROM applications WHERE posting_id = ?",
        (posting_id,),
    ).fetchone()
    applied_at = (existing["applied_at"] if existing else "") or ""
    if status and not applied_at:
        applied_at = now_iso()

    conn.execute(
        """
        INSERT INTO applications
            (posting_id, applied, status, applied_at, updated_at,
             company, role)
        VALUES (?,?,?,?,?,?,?)
        ON CONFLICT(posting_id) DO UPDATE SET
            applied    = excluded.applied,
            status     = excluded.status,
            applied_at = excluded.applied_at,
            updated_at = excluded.updated_at,
            company    = excluded.company,
            role       = excluded.role
        """,
        (posting_id, 1 if status else 0, status, applied_at,
         now_iso(), company, role),
    )
    conn.commit()


def set_notes(conn, posting_id: str, notes: str) -> None:
    """Save free-text notes against an application."""
    conn.execute(
        """
        INSERT INTO applications (posting_id, notes, updated_at)
        VALUES (?,?,?)
        ON CONFLICT(posting_id) DO UPDATE SET
            notes      = excluded.notes,
            updated_at = excluded.updated_at
        """,
        (posting_id, notes, now_iso()),
    )
    conn.commit()


def pipeline(conn) -> list:
    """
    Every application, joined to its posting, newest first.

    LEFT JOIN from applications, not postings: an application must survive
    its posting dropping off the source. You applied — that happened, and
    losing the record because a company took the listing down would be the
    exact failure this table exists to prevent.
    """
    rows = conn.execute(
        """
        SELECT a.posting_id, a.status, a.applied_at, a.updated_at,
               a.notes, a.company AS saved_company, a.role AS saved_role,
               p.company, p.role, p.location, p.apply_url, p.salary,
               p.role_family
        FROM applications a
        LEFT JOIN postings p ON p.id = a.posting_id
        WHERE a.status != ''
        ORDER BY a.applied_at DESC
        """
    ).fetchall()

    out = []
    for row in rows:
        item = dict(row)
        # Fall back to the saved copies when the posting is gone.
        item["company"] = item["company"] or item["saved_company"]
        item["role"] = item["role"] or item["saved_role"]
        item["days_waiting"] = _days_since(item["applied_at"])
        out.append(item)
    return out


def _days_since(timestamp: str):
    """Whole days between an ISO timestamp and now, or None."""
    if not timestamp:
        return None
    try:
        then = datetime.fromisoformat(timestamp)
    except (TypeError, ValueError):
        return None
    if then.tzinfo is None:
        then = then.replace(tzinfo=timezone.utc)
    return max(0, (datetime.now(timezone.utc) - then).days)


def pipeline_counts(conn) -> dict:
    """How many applications sit at each stage."""
    rows = conn.execute(
        "SELECT status, COUNT(*) AS n FROM applications "
        "WHERE status != '' GROUP BY status"
    ).fetchall()
    return {row["status"]: row["n"] for row in rows}


def reattach_orphaned_marks(conn) -> int:
    """
    Re-link applied marks whose posting id no longer exists.

    Runs on every refresh. If a posting's id changed — a source dropped it, a
    title was edited, an id scheme changed — the mark is matched back by
    company and role and re-filed under the new id.

    Returns how many were recovered. Should normally be 0; anything else is
    worth noticing, because it means ids moved.
    """
    orphans = conn.execute(
        """
        SELECT a.posting_id, a.applied, a.notes, a.company, a.role
        FROM applications a
        LEFT JOIN postings p ON p.id = a.posting_id
        WHERE p.id IS NULL AND a.company != '' AND a.role != ''
        """
    ).fetchall()

    recovered = 0
    for orphan in orphans:
        match = conn.execute(
            "SELECT id FROM postings WHERE company = ? AND role = ?",
            (orphan["company"], orphan["role"]),
        ).fetchone()
        if not match:
            continue
        conn.execute(
            """
            INSERT INTO applications
                (posting_id, applied, notes, updated_at, company, role)
            VALUES (?,?,?,?,?,?)
            ON CONFLICT(posting_id) DO UPDATE SET
                applied = excluded.applied
            """,
            (match["id"], orphan["applied"], orphan["notes"] or "",
             now_iso(), orphan["company"], orphan["role"]),
        )
        conn.execute(
            "DELETE FROM applications WHERE posting_id = ?",
            (orphan["posting_id"],),
        )
        recovered += 1

    conn.commit()
    return recovered


def applied_count(conn) -> int:
    """How many roles you've marked as applied."""
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM applications WHERE applied = 1"
    ).fetchone()
    return row["n"] if row else 0
