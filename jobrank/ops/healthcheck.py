"""
healthcheck.py — is the whole thing actually working?

    python3 healthcheck.py

Checks every part end to end and prints a plain verdict. Run it whenever
something feels off, or after changing config.py, or if a morning goes by
with no notification and you want to know whether that's because nothing
new arrived or because something broke.

WHY THIS EXISTS
A tool that runs unattended fails silently by default. The daily refresh
could stop fetching, the dashboard could die, notifications could be
suppressed, and everything would look exactly the same from the outside —
an empty list is indistinguishable from a quiet day. This makes the
difference visible.

It is read-only. It never writes to the database or changes any setting.
"""

import os
import platform
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone

from jobrank import config


PASS, WARN, FAIL = "PASS", "WARN", "FAIL"
_results = []


def report(status, area, detail):
    _results.append(status)
    print(f"  [{status}] {area}: {detail}")


# =============================================================================

def check_config():
    print("\nCONFIGURATION")
    from jobrank.config import scoring
    report(PASS, "scoring factors", ", ".join(scoring.FACTORS))
    report(PASS, "age cutoff", f"{config.MAX_AGE_DAYS} days")
    report(PASS, "refresh times",
           ", ".join(f"{h:02d}:{m:02d}" for h, m in config.REFRESH_TIMES))


def check_profile():
    print("\nPROFILE")
    from jobrank.profile import active, store
    user_id = active.resolve()
    if not user_id:
        report(FAIL, "profile", "none — create one at /profile or: python3 run.py profile create")
        return
    try:
        profile = store.load(user_id)
        resume = store.load_resume(user_id)
    except Exception as exc:  # noqa: BLE001 - report, don't crash the check
        report(FAIL, "profile", f"'{user_id}' unreadable: {exc}")
        return
    status = WARN if user_id == active.EXAMPLE_ID else PASS
    report(status, "active profile", f"{user_id} (extracted by {resume.extractor})")
    report(PASS if len(profile.skills) >= 5 else WARN, "skills evidence",
           f"{len(profile.skills)} skills derived from the résumé")
    for note in resume.warnings:
        report(WARN, "résumé", note)


def _daily_coverage(conn, days=7):
    """
    Did a refresh actually LAND on each of the last `days` days?

    The plist says 07:30/11:30/16:30. Whether runs happened depends on
    whether the Mac was awake, so this reads the record rather than
    trusting the schedule. Returns a dict; the caller prints it, so this
    can run before the connection closes.
    """
    rows = conn.execute(
        "SELECT date(ran_at) AS day, COUNT(*) AS n "
        "FROM runs GROUP BY day ORDER BY day DESC LIMIT ?",
        (days,),
    ).fetchall()
    counts = {row["day"]: row["n"] for row in rows}

    first = conn.execute("SELECT MIN(date(ran_at)) AS d FROM runs").fetchone()
    today = date.today()

    # Only judge days the tool has actually existed for — a project three
    # days old has not "missed" the four days before it was written.
    lifetime = days
    if first and first["d"]:
        lifetime = max(1, min(days, (today - date.fromisoformat(first["d"])).days + 1))

    covered, marks = 0, []
    for offset in range(lifetime):
        day = (today - timedelta(days=offset)).isoformat()
        if counts.get(day):
            covered += 1
            marks.append("+")
        else:
            marks.append(".")
    marks.reverse()

    return {
        "status": PASS if covered >= lifetime else WARN,
        "detail": (f"refreshed on {covered} of the last {lifetime} day"
                   f"{'s' if lifetime != 1 else ''}  "
                   f"[{''.join(marks)}]  oldest left, today right"),
    }


def check_database():
    print("\nDATABASE")
    from jobrank import storage

    if not os.path.exists(config.DATABASE_PATH):
        report(FAIL, "database", "missing — run: python3 refresh.py")
        return

    conn = storage.connect()
    postings = storage.load_postings(conn)
    last_run = storage.last_run_time(conn)
    applied = storage.applied_count(conn)
    pipeline = storage.pipeline(conn)
    coverage = _daily_coverage(conn)
    conn.close()

    if not postings:
        report(FAIL, "postings", "none stored — run: python3 refresh.py")
        return

    report(PASS, "postings", f"{len(postings)} active")

    # Staleness of the DATA, not of the postings.
    if last_run:
        try:
            then = datetime.fromisoformat(last_run)
            if then.tzinfo is None:
                then = then.replace(tzinfo=timezone.utc)
            hours = (datetime.now(timezone.utc) - then).total_seconds() / 3600
            status = PASS if hours < 24 else WARN
            report(status, "last refresh", f"{hours:.1f} hours ago")
        except ValueError:
            report(WARN, "last refresh", "timestamp unreadable")
    else:
        report(WARN, "last refresh", "never")

    report(coverage["status"], "daily coverage", coverage["detail"])

    report(PASS, "your data",
           f"{applied} marked applied, {len(pipeline)} in the pipeline")

    from jobrank import ranking
    from jobrank.config import companies
    ranked = ranking.rank()
    if ranked is None:
        report(WARN, "scores", "no profile, so nothing is ranked")
        return

    # Scoring should produce a spread. Everything at one value means a
    # signal has stopped varying and the ranking is meaningless.
    scores = {r.score for r in ranked.results.values()}
    status = PASS if len(scores) > 20 else WARN
    report(status, "score spread", f"{len(scores)} distinct scores across {len(ranked.results)} postings")

    # Whether the semantic layer actually ran, and with which model.
    top = ranked.results[ranked.ordered_ids[0]]
    semantic = top.factors.get("semantic")
    report(PASS if semantic else WARN, "semantic layer",
           f"model {semantic.detail['model']}" if semantic else "unavailable — ranking on keyword factors only")

    rows = {r["id"]: r for r in ranked.rows}
    top20 = [rows[i] for i in ranked.ordered_ids[:20] if i in rows]
    crowd = {}
    for p in top20:
        crowd[p["company"]] = crowd.get(p["company"], 0) + 1
    worst, count = max(crowd.items(), key=lambda kv: kv[1])
    status = PASS if count <= max(companies.MAX_PER_COMPANY, 4) else WARN
    report(status, "crowding",
           f"most from one company in the top 20: {worst} ({count}) — "
           f"the page shows at most {companies.MAX_PER_COMPANY} each")


def _host_resolves(host: str = "raw.githubusercontent.com") -> bool:
    """Whether this machine can resolve the host every source is served from."""
    import socket
    try:
        socket.getaddrinfo(host, 443)
        return True
    except OSError:
        return False


def check_sources():
    """
    Ask each source for its postings.

    An offline machine is reported ONCE, as a warning, rather than as one
    failure per source. On 2026-09-20 this check recorded "8 check(s) failing"
    and sent an alert; all eight were the same DNS lookup failing while the
    laptop was asleep. Nothing was broken, and eight identical failures for one
    absent network is exactly how a health check teaches you to ignore it.
    A source that is genuinely gone still fails, loudly.
    """
    print("\nSOURCES (network)")
    # Imported from refresh.py so this can never drift out of date when a
    # source is added — there is one list, and it lives there.
    from jobrank.refresh import SOURCES

    if not _host_resolves():
        report(WARN, "network", "offline — cannot resolve raw.githubusercontent.com, "
                                f"so none of the {len(SOURCES)} sources can be checked")
        return

    total = 0
    for source in SOURCES:
        try:
            postings = source.fetch()
            total += len(postings)
            status = PASS if postings else FAIL
            report(status, source.name, f"{len(postings)} postings")
        except Exception as exc:
            report(FAIL, source.name, f"{type(exc).__name__}: {exc}")
    if total:
        report(PASS, "combined", f"{total} rows before deduplication")


def check_jobs():
    print("\nSCHEDULED JOBS")
    if platform.system() != "Darwin":
        report(WARN, "launchd", "not macOS — scheduling unavailable")
        return

    try:
        listing = subprocess.run(["launchctl", "list"],
                                 capture_output=True, text=True).stdout
    except OSError as exc:
        report(FAIL, "launchctl", str(exc))
        return

    for label, name in (("com.internship-finder.daily", "refresh job"),
                        ("com.internship-finder.dashboard", "dashboard")):
        line = next((ln for ln in listing.splitlines() if label in ln), None)
        if not line:
            report(FAIL, name, "not installed — ./scripts/schedule.sh install")
            continue
        exit_code = line.split()[1]
        status = PASS if exit_code in ("0", "-") else WARN
        report(status, name, f"loaded, last exit status {exit_code}")


def check_dashboard():
    print("\nDASHBOARD")
    import urllib.error
    import urllib.request
    try:
        with urllib.request.urlopen(config.DASHBOARD_HEALTH_URL,
                                    timeout=5) as resp:
            body = resp.read().decode("utf-8", "replace")
        # /healthz answers JSON, not the dashboard HTML. The old check
        # looked for "postings" in the body, which was only ever true
        # because this probe used to fetch "/" — the fetch that was
        # clearing the NEW badges.
        if resp.status == 200 and '"ok"' in body:
            report(PASS, "responding", config.DASHBOARD_URL)
        else:
            report(WARN, "responding", f"HTTP {resp.status}, unexpected body")
    except (urllib.error.URLError, OSError) as exc:
        report(FAIL, "responding",
               f"no answer at {config.DASHBOARD_URL} ({exc})")


def check_notifications():
    print("\nNOTIFICATIONS")
    from jobrank import notify
    if not notify.is_available():
        report(WARN, "macOS notifications", "unavailable on this system")
        return

    report(PASS, "notification path", "osascript present and callable")
    report(WARN, "delivery",
           "cannot be checked in software — osascript exits 0 even when "
           "macOS drops the banner. Run: python3 -m jobrank.notify")
    report(PASS, "dashboard banner",
           "new postings also show as a banner, which cannot be suppressed")


def check_ui():
    """
    Is the interface actually usable, or only serving 200s?

    Two copy-button bugs shipped while every Python test passed, because
    neither bug was in Python. This runs the real browser suite if it is
    installed, and says plainly when it isn't — a check that quietly skips
    is how both bugs reached you.
    """
    print("\nINTERFACE (real browser)")

    try:
        import playwright  # noqa: F401
    except ImportError:
        report(WARN, "browser tests", "playwright not installed — the UI "
                                      "is NOT being checked. Install with: "
                                      "pip install playwright && python3 -m "
                                      "playwright install chromium")
        return

    import subprocess
    result = subprocess.run(
        [sys.executable, "browser_tests.py"],
        capture_output=True, text=True, timeout=180,
    )
    summary = ""
    for line in reversed(result.stdout.splitlines()):
        if "browser checks" in line:
            summary = line.strip()
            break

    if result.returncode == 0:
        report(PASS, "browser tests", summary or "all passed")
    else:
        failures = [l.strip() for l in result.stdout.splitlines()
                    if "[FAIL]" in l]
        report(FAIL, "browser tests",
               (summary or "failed") +
               ((" — " + failures[0]) if failures else ""))


def check_prompts():
    print("\nAPPLICATION PROMPTS")
    from jobrank import letters
    try:
        profile = letters.load_profile()
    except letters.LetterError as exc:
        report(FAIL, "prompts", str(exc).split("\n")[0])
        return

    posting = {
        "company": "Example", "role": "Software Engineer Intern",
        "category": "Software Engineering", "location": "NYC",
        "apply_url": "", "age_text": "0d",
        "role_family": "software_engineering",
    }
    cover = letters.cover_letter_prompt(posting, profile)
    experience = letters.work_experience_prompt(posting, profile)
    report(PASS, "cover letter prompt", f"{len(cover)} characters")
    report(PASS, "work experience prompt", f"{len(experience)} characters")

    source = open("jobrank/letters.py", encoding="utf-8").read().lower()
    status = PASS if "anthropic" not in source else FAIL
    report(status, "cost", "no API calls — prompts are assembled locally")


# =============================================================================

def main():
    print("=" * 62)
    print(" JOBRANK — HEALTH CHECK")
    print("=" * 62)

    for check in (check_config, check_profile, check_database,
                  check_sources, check_jobs, check_dashboard,
                  check_notifications, check_prompts, check_ui):
        try:
            check()
        except Exception as exc:
            report(FAIL, check.__name__, f"crashed: {exc}")

    failures = _results.count(FAIL)
    warnings = _results.count(WARN)

    print("\n" + "=" * 62)
    if failures:
        print(f" {failures} FAILED, {warnings} warnings — see above")
        sys.exit(1)
    elif warnings:
        noun = "thing" if warnings == 1 else "things"
        print(f" Everything works. {warnings} {noun} worth knowing about.")
    else:
        print(" Everything works.")
    print("=" * 62)


if __name__ == "__main__":
    main()
