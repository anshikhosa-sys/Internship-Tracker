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

import config


PASS, WARN, FAIL = "PASS", "WARN", "FAIL"
_results = []


def report(status, area, detail):
    _results.append(status)
    print(f"  [{status}] {area}: {detail}")


# =============================================================================

def check_config():
    print("\nCONFIGURATION")
    try:
        weights = config.SCORE_WEIGHTS
        report(PASS, "score weights",
               f"preference {weights['preference']}, "
               f"candidacy {weights['candidacy']}, "
               f"freshness {weights['freshness']}")
    except Exception as exc:
        report(FAIL, "score weights", f"unreadable: {exc}")

    report(PASS, "age cutoff", f"{config.MAX_AGE_DAYS} days")
    report(PASS, "refresh times",
           ", ".join(f"{h:02d}:{m:02d}" for h, m in config.REFRESH_TIMES))
    report(PASS, "company tiers", ", ".join(config.COMPANY_TIERS))


def check_profile():
    print("\nRESUME")
    path = config.PROFILE_PATH
    if not os.path.exists(path):
        report(FAIL, "profile.md",
               f"missing — copy profile_example.md to {path}")
        return

    with open(path, encoding="utf-8") as handle:
        text = handle.read()

    if len(text) < 200:
        report(FAIL, "profile.md", "too short to produce useful prompts")
        return

    report(PASS, "profile.md", f"{len(text)} characters")

    # Candidacy only credits keywords that appear here, so a low count means
    # scoring is running on very little evidence.
    lowered = text.lower()
    grounded = [k for k in config.CANDIDACY_EVIDENCE if k in lowered]
    ratio = len(grounded) / max(len(config.CANDIDACY_EVIDENCE), 1)
    status = PASS if ratio >= 0.4 else WARN
    report(status, "resume-grounded evidence",
           f"{len(grounded)}/{len(config.CANDIDACY_EVIDENCE)} scoring "
           f"keywords appear in your resume")


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
    import storage
    import scorer

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

    # Scoring should produce a spread. Everything at one value means a
    # signal has stopped varying and the ranking is meaningless.
    scores = set()
    for posting in postings:
        age = scorer.days_old(posting)
        tier = posting.get("company_tier") or "mid"
        fresh, _ = scorer.freshness(age, tier)
        scores.add(scorer.final_score(
            posting["preference"], posting["candidacy_score"], fresh))
    status = PASS if len(scores) > 20 else WARN
    report(status, "score spread",
           f"{len(scores)} distinct scores across {len(postings)} postings")

    # WHO is at the top, not just how spread out the numbers are. The list
    # this replaced was topped by pharma, blinds and oil companies with
    # perfectly good job titles, and no numeric check would have caught it.
    ranked = sorted(postings, key=lambda p: -(p["fit_score"] or 0))[:20]
    tech = sum(1 for p in ranked
               if scorer.employer_class(p) in ("frontier", "big_tech", "tech"))
    status = PASS if tech >= len(ranked) / 2 else WARN
    report(status, "top of the list",
           f"{tech}/{len(ranked)} of your top 20 are technology companies")

    crowd = {}
    for p in ranked:
        crowd[p["company"]] = crowd.get(p["company"], 0) + 1
    worst, count = max(crowd.items(), key=lambda kv: kv[1])
    limit = config.MAX_PER_COMPANY or len(ranked)
    status = PASS if count <= max(limit, 4) else WARN
    report(status, "crowding",
           f"most from one company in the top 20: {worst} ({count}) — "
           f"the page shows at most {config.MAX_PER_COMPANY} each")


def check_sources():
    print("\nSOURCES (network)")
    # Imported from refresh.py so this can never drift out of date when a
    # source is added — there is one list, and it lives there.
    from refresh import SOURCES
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
    import notify
    if not notify.is_available():
        report(WARN, "macOS notifications", "unavailable on this system")
        return

    report(PASS, "notification path", "osascript present and callable")
    report(WARN, "delivery",
           "cannot be checked in software — osascript exits 0 even when "
           "macOS drops the banner. Run: python3 notify.py")
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
    import letters
    try:
        profile = letters.load_profile()
    except letters.LetterError as exc:
        report(FAIL, "prompts", str(exc).split("\n")[0])
        return

    posting = {
        "company": "Example", "role": "Software Engineer Intern",
        "category": "Software Engineering", "location": "NYC",
        "apply_url": "", "age_text": "0d",
        "role_family": "Software Engineering",
    }
    cover = letters.cover_letter_prompt(posting, profile)
    experience = letters.work_experience_prompt(posting, profile)
    report(PASS, "cover letter prompt", f"{len(cover)} characters")
    report(PASS, "work experience prompt", f"{len(experience)} characters")

    source = open("letters.py", encoding="utf-8").read().lower()
    status = PASS if "anthropic" not in source else FAIL
    report(status, "cost", "no API calls — prompts are assembled locally")


# =============================================================================

if __name__ == "__main__":
    print("=" * 62)
    print(" INTERNSHIP FINDER — HEALTH CHECK")
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
