"""
Storage — a local SQLite database. No server, just a file (internships.db).

SQLite ships with Python, so there's nothing to install and nothing to run. The
whole database is one file you can delete to start over, or copy to back up.

THE MOST IMPORTANT IDEA IN THIS FILE
------------------------------------
There are two kinds of data here, and they must never mix:

  FETCHED DATA  (internships.db)
      Comes from the internet. Rebuildable at any time by re-running
      refresh.py. Losing it costs nothing but a few seconds.

  YOUR DATA     (applications.db)
      Which roles you applied to and where each stands. IRREPLACEABLE.
      Nothing can regenerate it.

THEY LIVE IN SEPARATE FILES, and that separation was learned the hard way.
An earlier version put them in separate TABLES of the same database, which
sounds equivalent and isn't: rebuilding the schema meant deleting the file,
and deleting the file took the applications with it. That happened several
times during development and destroyed real records each time.

Separate tables protect against a careless UPDATE. Only separate FILES
protect against `rm`. Now internships.db can be deleted at any moment — as a
schema reset, a bad backup restore, anything — and your applications are
untouched, because they were never in it.

There is also a plain-text mirror (applications.json) written after every
change. Belt and braces: if both databases were somehow lost, that file is
readable in any text editor and can be restored by hand.

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

from jobrank import config


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
    description          TEXT,     -- job description text, when a source has one
    -- Scores are per profile and recomputed at read time. These columns hold
    -- a snapshot for the ACTIVE profile only, used by the CLI report and
    -- notifications. preference/candidacy columns are unused since v2 and
    -- kept so databases created by v1 still open.
    preference           REAL,
    preference_reasons   TEXT,
    candidacy_score      REAL,
    candidacy_reasons    TEXT,
    role_family          TEXT,     -- taxonomy family from the title
    company_tier         TEXT,     -- large/mid/startup
    fit_score            INTEGER,  -- snapshot for the active profile
    first_seen           TEXT,     -- OUR timestamp: drives the NEW flag
    last_seen            TEXT,
    is_active            INTEGER   -- 0 once it drops off the source
);

-- Kept for MIGRATION ONLY. Applications now live in their own database
-- file; this definition exists so an older internships.db can still be read
-- and its rows moved across. Nothing writes here any more.
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

-- Structured facts extracted from a posting's text (jobrank/enrich.py).
-- Keyed by posting and the hash of the text analyzed, so a posting whose text
-- is unchanged is never analyzed twice.
CREATE TABLE IF NOT EXISTS enrichments (
    posting_id TEXT PRIMARY KEY,
    text_hash  TEXT NOT NULL,
    extractor  TEXT NOT NULL,
    data       TEXT NOT NULL,
    updated_at TEXT NOT NULL
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


# =============================================================================
# YOUR data — a separate file, deliberately
# =============================================================================

APPLICATIONS_SCHEMA = """
CREATE TABLE IF NOT EXISTS applications (
    posting_id  TEXT PRIMARY KEY,
    applied     INTEGER NOT NULL DEFAULT 0,
    notes       TEXT DEFAULT '',
    updated_at  TEXT,
    -- Company and role are stored alongside the id so a mark can be found
    -- again even if the posting id scheme ever changes.
    company     TEXT DEFAULT '',
    role        TEXT DEFAULT '',
    status      TEXT DEFAULT '',
    applied_at  TEXT DEFAULT ''
);
"""

# Append-only ledger for jobrank/workflow: every legal stage change, with when
# it happened. `applications.status` above only ever holds the current stage
# as a bare string, so it can't answer "how did this get here" and nothing
# stops it being overwritten to something that skips steps. This table
# doesn't replace it and nothing ever rewrites a row after the insert; a bad
# transition can add at most one wrong row, never touch the history behind
# it. See jobrank/workflow/states.py.
#
# Deliberately NOT folded into APPLICATIONS_SCHEMA above: _attach_applications()
# re-qualifies that string's one `applications` table as `appdb.applications`
# with a literal find/replace, and doesn't know about any other table name in
# it. An unqualified CREATE TABLE run over that same connection would default
# to `main` — i.e. postings.db, the database this table must never land in
# (invariant 13). _ensure_application_events() below qualifies it explicitly
# instead, whichever of the two connections it's asked to run on.
APPLICATION_EVENTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS {schema}.application_events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    posting_id   TEXT NOT NULL,
    from_status  TEXT NOT NULL,
    to_status    TEXT NOT NULL,
    occurred_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS {schema}.idx_application_events_posting
    ON application_events(posting_id, occurred_at);
"""


def applications_path() -> str:
    """Where your applications live — deliberately NOT internships.db."""
    return config.APPLICATIONS_PATH


def connect_applications(path: str = None) -> sqlite3.Connection:
    """Open the applications database, creating it if needed."""
    conn = sqlite3.connect(path or applications_path())
    conn.row_factory = sqlite3.Row
    conn.executescript(APPLICATIONS_SCHEMA)
    conn.commit()
    return conn


def _attach_applications(conn) -> None:
    """
    Make the applications database visible to queries on this connection.

    ATTACH lets one connection see two files, so the existing LEFT JOIN
    between postings and applications keeps working unchanged even though
    they now live apart.
    """
    path = applications_path().replace("'", "''")
    conn.execute(f"ATTACH DATABASE '{path}' AS appdb")
    conn.executescript(
        APPLICATIONS_SCHEMA.replace(
            "CREATE TABLE IF NOT EXISTS applications",
            "CREATE TABLE IF NOT EXISTS appdb.applications",
        )
    )
    conn.commit()


def _migrate_applications_out(conn) -> int:
    """
    Move any rows left in the OLD in-file applications table across.

    Runs once. After it, the old table is empty and everything reads from
    the separate file.
    """
    try:
        rows = list(conn.execute("SELECT * FROM main.applications"))
    except sqlite3.OperationalError:
        return 0
    if not rows:
        return 0

    for row in rows:
        item = dict(row)
        conn.execute(
            """
            INSERT INTO appdb.applications
                (posting_id, applied, notes, updated_at, company, role,
                 status, applied_at)
            VALUES (?,?,?,?,?,?,?,?)
            ON CONFLICT(posting_id) DO NOTHING
            """,
            (item.get("posting_id"), item.get("applied", 0),
             item.get("notes", ""), item.get("updated_at", ""),
             item.get("company", ""), item.get("role", ""),
             item.get("status", ""), item.get("applied_at", "")),
        )
    conn.execute("DELETE FROM main.applications")
    conn.commit()
    return len(rows)


def export_applications(conn) -> None:
    """
    Mirror your applications to a plain JSON file after every change.

    A third copy, readable without any tooling. If both database files were
    lost you could rebuild from this by hand, which is the whole point of
    keeping something irreplaceable in more than one format.
    """
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM appdb.applications WHERE status != ''")]
        with open(config.APPLICATIONS_EXPORT, "w", encoding="utf-8") as fh:
            json.dump(rows, fh, indent=2)
    except (sqlite3.Error, OSError):
        # A failed mirror must never block the actual save.
        pass


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
    _attach_applications(conn)      # your data, from its own file
    _migrate_applications_out(conn)  # move any legacy rows across
    _backfill(conn)
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
        "description": "TEXT",
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
        "company_tier": "TEXT",
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
        "UPDATE appdb.applications SET status = 'applied' "
        "WHERE applied = 1 AND (status IS NULL OR status = '')"
    )
    # An application with a stage but no timestamp predates applied_at.
    # Use the last update as the best available approximation rather than
    # leaving "days waiting" blank forever.
    conn.execute(
        "UPDATE appdb.applications SET applied_at = updated_at "
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
                salary, description, sources,
                is_faang, needs_advanced_degree, no_sponsorship,
                citizenship_required, role_family,
                company_tier, fit_score,
                first_seen, last_seen, is_active
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)
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
                description   = CASE WHEN excluded.description != ''
                                     THEN excluded.description
                                     ELSE postings.description END,
                sources       = excluded.sources,
                is_faang      = excluded.is_faang,
                needs_advanced_degree = excluded.needs_advanced_degree,
                no_sponsorship        = excluded.no_sponsorship,
                citizenship_required  = excluded.citizenship_required,
                role_family       = excluded.role_family,
                company_tier      = excluded.company_tier,
                fit_score         = excluded.fit_score,
                last_seen     = excluded.last_seen,
                is_active     = 1
                -- NOTE: first_seen is intentionally NOT updated here.
            """,
            (
                posting.id, posting.source, posting.company, posting.role,
                posting.category, posting.location, posting.apply_url,
                posting.simplify_url, posting.age_text, posting.date_posted,
                posting.salary, posting.description or "",
                json.dumps(posting.sources or []),
                int(posting.is_faang), int(posting.needs_advanced_degree),
                int(posting.no_sponsorship), int(posting.citizenship_required),
                posting.role_family,
                posting.company_tier,
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
        LEFT JOIN appdb.applications a ON a.posting_id = p.id
        {where}
        ORDER BY p.fit_score DESC, p.company COLLATE NOCASE, p.role
        """
    ).fetchall()

    postings = []
    for row in rows:
        item = dict(row)
        # Reason lists are stored as JSON strings; decode for the template.
        for field in ("sources",):
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


def get_state(conn, key: str):
    """
    Read a value from the small key/value table.

    Public counterpart to _get_state, for callers outside this module —
    selfcheck.py records its last result here so the dashboard can show it.
    """
    return _get_state(conn, key)


def set_state(conn, key: str, value: str) -> None:
    """
    Write a value to the small key/value table, and commit it.

    The private _set_state leaves committing to its caller because
    register_visit() batches several writes. This public one commits: an
    earlier version did not, so a setting written through it survived only
    when some later write in the same request happened to commit, and
    silently vanished otherwise.
    """
    _set_state(conn, key, value)
    conn.commit()


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


def current_visit_basis(conn) -> str:
    """
    The visit basis WITHOUT recording a visit.

    For callers that need to know what counts as new but must not change
    it — health probes, and anything else that isn't a person looking at
    the page. Read-only by construction.

    Returns "" if no visit has ever been registered. It must NOT fall back
    to "now": that would make two reads return different answers, and a
    probe would report a different set of new postings each time it ran.
    new_since_last_visit() treats "" as "nothing is new", which is the
    right answer for a caller that isn't a person.
    """
    return _get_state(conn, "visit_basis") or ""


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
        "SELECT company, role FROM main.postings WHERE id = ?", (posting_id,)
    ).fetchone()
    company = row["company"] if row else ""
    role = row["role"] if row else ""

    conn.execute(
        """
        INSERT INTO appdb.applications
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
    export_applications(conn)


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
        "SELECT company, role FROM main.postings WHERE id = ?", (posting_id,)
    ).fetchone()
    company = row["company"] if row else ""
    role = row["role"] if row else ""

    existing = conn.execute(
        "SELECT applied_at FROM appdb.applications WHERE posting_id = ?",
        (posting_id,),
    ).fetchone()
    applied_at = (existing["applied_at"] if existing else "") or ""
    if status and not applied_at:
        applied_at = now_iso()

    conn.execute(
        """
        INSERT INTO appdb.applications
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
    export_applications(conn)


def set_notes(conn, posting_id: str, notes: str) -> None:
    """Save free-text notes against an application."""
    conn.execute(
        """
        INSERT INTO appdb.applications (posting_id, notes, updated_at)
        VALUES (?,?,?)
        ON CONFLICT(posting_id) DO UPDATE SET
            notes      = excluded.notes,
            updated_at = excluded.updated_at
        """,
        (posting_id, notes, now_iso()),
    )
    conn.commit()
    export_applications(conn)


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
        FROM appdb.applications a
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
        "SELECT status, COUNT(*) AS n FROM appdb.applications "
        "WHERE status != '' GROUP BY status"
    ).fetchall()
    return {row["status"]: row["n"] for row in rows}


def reattach_orphaned_marks(conn) -> int:
    """
    Re-link applied marks whose posting id no longer exists.

    Runs on every refresh. If a posting's id changed — a source dropped it, a
    title was edited, an id scheme changed — the mark is matched back by
    company and role and re-filed under the new id.

    Matching is exact first, then falls back to dedupe.same_role(). It has
    to: dedupe now merges postings that share an apply URL and describe the
    same job in different words, and the surviving row keeps ONE of the two
    titles. An application filed under the other title would otherwise be
    orphaned, its card would show as not-applied, and the obvious next step
    would be to apply to the same job a second time. The rule for "is this
    the same job" must be the same rule in both places.

    Returns how many were recovered. Should normally be 0; anything else is
    worth noticing, because it means ids moved.
    """
    from jobrank import dedupe
    orphans = conn.execute(
        """
        SELECT a.posting_id, a.applied, a.notes, a.company, a.role,
               a.status, a.applied_at
        FROM appdb.applications a
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
            # Same company, equivalent title. Scoped to the company so a
            # generic title like "Software Engineer Intern" can never
            # re-file an application against a different employer.
            candidates = conn.execute(
                "SELECT id, role FROM postings WHERE company = ?",
                (orphan["company"],),
            ).fetchall()
            for candidate in candidates:
                if dedupe.same_role(orphan["role"], candidate["role"]):
                    match = candidate
                    break

        if not match:
            continue
        # status and applied_at move across with the mark. They are the whole
        # point of the row -- "applied" alone cannot say whether this is at
        # online-assessment, interview or rejected, and applied_at is what
        # answers "how long have I been waiting". Re-filing without them
        # silently demoted a tracked application back to a bare tick, and
        # dropped it out of the per-company quota, which counts anything with
        # a status.
        conn.execute(
            """
            INSERT INTO appdb.applications
                (posting_id, applied, notes, updated_at, company, role,
                 status, applied_at)
            VALUES (?,?,?,?,?,?,?,?)
            ON CONFLICT(posting_id) DO UPDATE SET
                applied    = excluded.applied,
                notes      = CASE WHEN excluded.notes != ''
                                  THEN excluded.notes
                                  ELSE appdb.applications.notes END,
                company    = excluded.company,
                role       = excluded.role,
                status     = CASE WHEN appdb.applications.status = ''
                                  THEN excluded.status
                                  ELSE appdb.applications.status END,
                applied_at = CASE WHEN appdb.applications.applied_at = ''
                                  THEN excluded.applied_at
                                  ELSE appdb.applications.applied_at END,
                updated_at = excluded.updated_at
            """,
            (match["id"], orphan["applied"], orphan["notes"] or "",
             now_iso(), orphan["company"], orphan["role"],
             orphan["status"] or "", orphan["applied_at"] or ""),
        )
        conn.execute(
            "DELETE FROM appdb.applications WHERE posting_id = ?",
            (orphan["posting_id"],),
        )
        recovered += 1

    conn.commit()
    return recovered


def applied_count(conn) -> int:
    """How many roles you've marked as applied."""
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM appdb.applications WHERE applied = 1"
    ).fetchone()
    return row["n"] if row else 0


# =============================================================================
# Enrichments
# =============================================================================

def get_enrichments(conn) -> dict:
    """posting_id -> {"text_hash", "extractor", "data"} for every stored enrichment."""
    out = {}
    for row in conn.execute("SELECT posting_id, text_hash, extractor, data FROM enrichments"):
        try:
            data = json.loads(row["data"])
        except (json.JSONDecodeError, TypeError):
            continue
        out[row["posting_id"]] = {"text_hash": row["text_hash"], "extractor": row["extractor"], "data": data}
    return out


def put_enrichment(conn, posting_id: str, text_hash: str, extractor: str, data: dict) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO enrichments (posting_id, text_hash, extractor, data, updated_at)"
        " VALUES (?, ?, ?, ?, ?)",
        (posting_id, text_hash, extractor, json.dumps(data), now_iso()),
    )


def set_score_snapshot(conn, scores: dict) -> None:
    """Store {posting_id: (fit_score, company_size)} for the active profile."""
    conn.executemany(
        "UPDATE postings SET fit_score = ?, company_tier = ? WHERE id = ?",
        [(score, size, posting_id) for posting_id, (score, size) in scores.items()],
    )
    conn.commit()


def set_description(conn, posting_id: str, description: str) -> None:
    """
    Store a fetched job description.

    Descriptions arrive from a best-effort network fetch (jobrank/descriptions),
    so an empty one means "we could not get it", never "this job has none".
    Writing it would erase a description a source did supply, so it is ignored.
    """
    if not description:
        return
    conn.execute("UPDATE postings SET description = ? WHERE id = ?", (description, posting_id))


def postings_without_description(conn, limit: int | None = None) -> list[tuple[str, str]]:
    """(id, apply_url) for active postings we have no description for."""
    sql = ("SELECT id, apply_url FROM postings "
           "WHERE is_active = 1 AND COALESCE(description, '') = '' AND COALESCE(apply_url, '') != ''")
    if limit:
        sql += f" LIMIT {int(limit)}"
    return [(row[0], row[1]) for row in conn.execute(sql)]


# =============================================================================
# Application workflow events (jobrank/workflow) — an append-only ledger
# =============================================================================
#
# Everything below only INSERTs into application_events, never UPDATEs or
# DELETEs a row once written. jobrank.workflow.states is the only caller that
# should reach these after checking a move is legal; calling them directly
# skips that check.

def _applications_schema(conn) -> str:
    """
    `appdb` if this connection came from connect() (postings.db with
    applications.db ATTACHed), `main` if it came from connect_applications()
    directly (the only database on the connection). Needed because an
    unqualified CREATE TABLE run on the attached connection would silently
    default to `main` — postings.db — which is exactly where application
    data must never live (invariant 13).
    """
    names = {row[1] for row in conn.execute("PRAGMA database_list")}
    return "appdb" if "appdb" in names else "main"


def _ensure_application_events(conn) -> str:
    """Create application_events in the same file as `applications` if it isn't there yet, and return which schema name it lives under on this connection."""
    schema = _applications_schema(conn)
    conn.executescript(APPLICATION_EVENTS_SCHEMA.format(schema=schema))
    conn.commit()
    return schema


def record_application_event(
    conn, posting_id: str, from_status: str, to_status: str, occurred_at: str | None = None
) -> None:
    """
    Append one stage change. `occurred_at` defaults to now so a live
    transition is timestamped automatically; migration passes an explicit
    past time so a backfilled row doesn't claim to have just happened.
    """
    schema = _ensure_application_events(conn)
    conn.execute(
        f"INSERT INTO {schema}.application_events "
        "(posting_id, from_status, to_status, occurred_at) VALUES (?, ?, ?, ?)",
        (posting_id, from_status, to_status, occurred_at or now_iso()),
    )
    conn.commit()


def application_event_history(conn, posting_id: str) -> list[dict]:
    """Every recorded stage change for one posting, oldest first — the answer `applications.status` alone can't give."""
    schema = _ensure_application_events(conn)
    rows = conn.execute(
        f"SELECT posting_id, from_status, to_status, occurred_at "
        f"FROM {schema}.application_events WHERE posting_id = ? "
        "ORDER BY occurred_at ASC, id ASC",
        (posting_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def latest_application_events(conn) -> dict:
    """posting_id -> its most recent recorded event, for every posting with at least one. One query instead of one per posting when scanning a whole pipeline."""
    schema = _ensure_application_events(conn)
    rows = conn.execute(
        f"""
        SELECT e.posting_id, e.from_status, e.to_status, e.occurred_at
        FROM {schema}.application_events e
        JOIN (
            SELECT posting_id, MAX(id) AS max_id
            FROM {schema}.application_events
            GROUP BY posting_id
        ) latest ON latest.max_id = e.id
        """
    ).fetchall()
    return {row["posting_id"]: dict(row) for row in rows}


def migrate_application_events(conn) -> int:
    """
    Seed application_events for applications that predate it.

    Every application in `applications` with a status was set through the
    existing set_status(), which never wrote a timestamped event. This adds
    ONE synthetic event per such row — reading its current status and
    applied_at, never touching the row itself — so jobrank.workflow.states
    has somewhere to start instead of treating a pre-existing application as
    though it were never applied to. Idempotent: a posting that already has
    an event (real or previously migrated) is left alone, so calling this
    again once real transitions exist is a no-op for those rows.
    """
    from jobrank.config import workflow as workflow_config

    schema = _ensure_application_events(conn)
    rows = conn.execute(
        f"""
        SELECT posting_id, status, applied_at, updated_at
        FROM {schema}.applications
        WHERE status != ''
        AND posting_id NOT IN (SELECT DISTINCT posting_id FROM {schema}.application_events)
        """
    ).fetchall()

    migrated = 0
    for row in rows:
        to_status = workflow_config.LEGACY_STATUS_MAP.get(row["status"])
        if to_status is None:
            continue
        occurred_at = row["applied_at"] or row["updated_at"] or now_iso()
        conn.execute(
            f"INSERT INTO {schema}.application_events "
            "(posting_id, from_status, to_status, occurred_at) VALUES (?, ?, ?, ?)",
            (row["posting_id"], workflow_config.DISCOVERED, to_status, occurred_at),
        )
        migrated += 1
    conn.commit()
    return migrated
