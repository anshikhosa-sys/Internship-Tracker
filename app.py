"""
app.py — the local Flask dashboard.

    python3 app.py     then open http://127.0.0.1:5000

HOW A FLASK APP IS PUT TOGETHER (the short version)
---------------------------------------------------
Flask maps URLs to Python functions. The @app.route("/") decorator above a
function means "when a browser asks for /, run this function and send back
whatever it returns".

A function that ends in render_template("index.html", ...) hands its data to a
template in templates/. The template is HTML with placeholders — {{ value }}
prints something, {% for %} loops. Flask fills them in and sends the finished
HTML to the browser. This is called server-side rendering: the page arrives
complete, rather than being assembled by JavaScript afterwards.

THE ROUTES HERE
    GET  /               the dashboard
    POST /api/applied    mark a posting applied/not (called by JavaScript)
    POST /mark-seen      clear all NEW badges
    POST /refresh        re-fetch listings, then bounce back to the dashboard
    GET  /prompts/<id>   copy-paste prompts for one posting
    GET  /applications   the pipeline: everything you've applied to
    POST /api/status     move an application to a stage
    POST /api/notes      save notes against an application

WHY THERE'S NO LOGIN
Single user, local only. The server binds to 127.0.0.1, which means it accepts
connections only from this machine — nothing outside can reach it. That's why
there's no password: there's no one else to keep out.
"""

import sys
from datetime import datetime, timezone

from flask import (
    Flask, jsonify, redirect, render_template, request, url_for
)

import config
import letters
import scorer
import storage
from refresh import refresh as run_refresh

app = Flask(__name__)


# =============================================================================
# Helpers
# =============================================================================

def _filtered(postings, new_ids, args):
    """
    Apply the search box and filter dropdowns.

    Filtering happens in Python rather than SQL. With a few hundred postings
    that's instant, and it keeps the SQL in storage.py simple. If this ever
    grew to hundreds of thousands of rows, this is the piece you'd push down
    into the database query.
    """
    # Has the filter form been submitted, or is this a bare page load?
    #
    # This matters because of how HTML checkboxes work: an unchecked box
    # sends NOTHING. Without a marker there's no way to tell "the user
    # unticked Hide co-ops" from "the user just opened the page", so a
    # default-on checkbox could never be switched off.
    #
    # The form carries a hidden `f=1`. Its presence means "these are the
    # user's choices, use them exactly"; its absence means "use the defaults
    # from config.py".
    submitted = args.get("f") == "1"

    query = (args.get("q") or "").strip().lower()
    category = args.get("category") or ""
    status = args.get("status") or ""
    show_low = args.get("show_low") == "1"
    fresh_only = args.get("fresh") == "1"
    show_stale = args.get("stale") == "1"
    tier = args.get("tier") or ""
    # "Tech employers only" — the single filter that answers "stop showing
    # me internal IT at a manufacturer". Off by default so nothing is
    # hidden without being asked for; the ranking already handles the
    # ordinary case.
    tech_only = args.get("techonly") == "1"

    if submitted:
        hide_coop = args.get("nocoop") == "1"
        hide_offseason = args.get("nowinter") == "1"
    else:
        hide_coop = config.DEFAULT_HIDE_COOP
        hide_offseason = config.DEFAULT_HIDE_OFFSEASON

    # Explicit "posted within N days" filter, independent of the global
    # cutoff.
    #
    # None means "not set"; 0 means "today only". Those must stay distinct —
    # an earlier version used 0 for both, and since 0 is falsy the "posted
    # today only" option silently showed everything.
    raw_within = args.get("within")
    if raw_within in (None, ""):
        within = None if submitted else config.DEFAULT_WITHIN_DAYS
    else:
        try:
            within = max(0, int(raw_within))
        except ValueError:
            within = None

    results = []
    for posting in postings:
        # ANYTHING YOU'VE APPLIED TO IS ALWAYS VISIBLE.
        #
        # The discovery filters exist to narrow down what to apply to NEXT.
        # Once you've applied, the posting stops being a candidate and starts
        # being a record, and a record that disappears because it aged past
        # "posted today" looks exactly like lost data — which is how this was
        # first reported.
        #
        # The search box and the status dropdown still apply below, so you
        # can deliberately narrow the list; only the age, fit, co-op and
        # season filters are bypassed.
        in_pipeline = bool(posting.get("status"))

        # THE "JUST APPLY" GATE.
        #
        # Anything reaching the list should already be worth an application,
        # so you can work down it without deciding. Two rules: we must be
        # able to classify the role, and you must be a plausible candidate.
        # Both are bypassed by "show low-fit" for when you want everything.
        if not in_pipeline and not show_low:
            if (config.REQUIRE_KNOWN_ROLE_FAMILY
                    and not posting.get("role_family")):
                continue
            if (posting.get("candidacy_score") or 0) < config.MIN_CANDIDACY:
                continue

        # Hide low-fit postings unless asked for. They're still in the
        # database and still scored — just collapsed by default.
        if (not in_pipeline and not show_low
                and posting["fit_score"] < config.LOW_FIT_THRESHOLD):
            continue

        # The age cutoff. This HIDES postings rather than ranking them
        # lower, so it's the one filter that can lose you something — hence
        # the explicit override rather than a silent drop.
        age = posting["age_days"]
        too_old = age is not None and age > config.MAX_AGE_DAYS
        if too_old and not show_stale and not in_pipeline:
            continue

        if (within is not None and not in_pipeline
                and (age is None or age > within)):
            continue

        if hide_coop and posting["is_coop"] and not in_pipeline:
            continue

        if hide_offseason and posting["is_off_season"] and not in_pipeline:
            continue

        if tier and posting["company_tier"] != tier:
            continue

        if tech_only and not posting["is_tech_employer"] and not in_pipeline:
            continue

        if fresh_only and not posting["is_fresh"]:
            continue

        if category and posting["category"] != category:
            continue

        if status == "new" and posting["id"] not in new_ids:
            continue
        if status == "applied" and not posting["applied"]:
            continue
        if status == "not_applied" and posting["applied"]:
            continue

        if query:
            haystack = " ".join([
                posting["company"], posting["role"],
                posting["location"], posting["category"],
            ]).lower()
            if query not in haystack:
                continue

        results.append(posting)

    return results


def _effective_within(args):
    """What the age dropdown should show as selected."""
    if args.get("f") == "1":
        return args.get("within", "")
    raw = args.get("within")
    if raw not in (None, ""):
        return raw
    default = config.DEFAULT_WITHIN_DAYS
    return "" if default is None else str(default)


def _effective_hide_coop(args) -> bool:
    """Whether the Hide co-ops box should render ticked."""
    if args.get("f") == "1":
        return args.get("nocoop") == "1"
    return config.DEFAULT_HIDE_COOP


def _effective_hide_offseason(args) -> bool:
    """Whether the Summer only box should render ticked."""
    if args.get("f") == "1":
        return args.get("nowinter") == "1"
    return config.DEFAULT_HIDE_OFFSEASON


# =============================================================================
# Routes
# =============================================================================

def _quota_group(company):
    """
    The APPLICATION_LIMITS entry covering this company, or None.

    Whole-word matching, like the employer lists: a substring test would
    file "Tiktokenizer" under TikTok's quota.
    """
    name = (company or "").strip().lower()
    if not name:
        return None
    for group in config.APPLICATION_LIMITS:
        for candidate in group["companies"]:
            if scorer._matches(candidate, name):
                return group
    return None


def _quota_state(postings):
    """
    How much of each capped company's quota you've already spent.

    Keyed by the group's name. Counts anything with a status — an
    application at any stage, including a rejection, still consumed a slot.
    """
    used = {}
    for posting in postings:
        if not posting.get("status"):
            continue
        group = _quota_group(posting.get("company"))
        if group:
            used[group["name"]] = used.get(group["name"], 0) + 1

    state = {}
    for group in config.APPLICATION_LIMITS:
        spent = used.get(group["name"], 0)
        state[group["name"]] = {
            "name": group["name"],
            "limit": group["limit"],
            "used": spent,
            "left": max(0, group["limit"] - spent),
        }
    return state


def _relative_time(iso_string):
    """
    "12 minutes ago" for a stored timestamp, or None.

    The header used to print just the DATE, which answers "did it run today"
    but not "is what I'm looking at current" — and with launchd lagging the
    schedule, that second question is the one that matters.
    """
    if not iso_string:
        return None
    try:
        then = datetime.fromisoformat(iso_string)
    except (TypeError, ValueError):
        return None
    if then.tzinfo is None:
        then = then.replace(tzinfo=timezone.utc)

    seconds = (datetime.now(timezone.utc) - then).total_seconds()
    if seconds < 0:
        return "just now"
    minutes = seconds / 60
    if minutes < 2:
        return "just now"
    if minutes < 60:
        return f"{int(minutes)} minutes ago"
    hours = minutes / 60
    if hours < 24:
        count = int(hours)
        return f"{count} hour{'s' if count != 1 else ''} ago"
    days = int(hours / 24)
    return f"{days} day{'s' if days != 1 else ''} ago"


def _catch_up_if_stale() -> bool:
    """
    Refresh before rendering if the data has gone stale.

    launchd fires a missed schedule when the Mac wakes from sleep, but a
    machine powered OFF through a slot — or shut for a weekend — can miss
    runs entirely. Rather than rely on that, the page checks for itself:
    opening the dashboard is exactly when the data needs to be current.

    Returns True if it refreshed. Failures are swallowed deliberately —
    stale listings are far better than an error page, and the header shows
    the last-updated date either way.
    """
    conn = storage.connect()
    last_run = storage.last_run_time(conn)
    conn.close()

    if last_run:
        try:
            then = datetime.fromisoformat(last_run)
            if then.tzinfo is None:
                then = then.replace(tzinfo=timezone.utc)
            hours = (datetime.now(timezone.utc) - then).total_seconds() / 3600
            if hours < config.STALE_DATA_HOURS:
                return False
        except (TypeError, ValueError):
            pass

    try:
        run_refresh(verbose=False, notifications=True)
        return True
    except Exception:
        return False


@app.route("/healthz")
def healthz():
    """
    Liveness check. Deliberately does NOT register a visit.

    This exists because health checks were being counted as you opening the
    dashboard. `healthcheck.py` and `schedule.sh status` both fetched "/",
    which advanced the visit basis and cleared your NEW badges — so running
    the health check destroyed the very thing it was reporting on. Probes
    get their own endpoint that reads nothing and writes nothing.
    """
    return jsonify({"ok": True})


@app.route("/")
def index():
    """The dashboard: ranked postings, best fit first."""
    caught_up = _catch_up_if_stale()

    conn = storage.connect()

    postings = storage.load_postings(conn)

    # Record that you've opened the dashboard, and find out what counts as new
    # to you. NEW means "arrived since you last looked", not "arrived in the
    # last refresh" — the two stopped being the same thing once refreshes
    # became automatic. See storage.register_visit().
    # Only a real page view counts as a visit. A browser rendering this page
    # always accepts HTML; curl, urllib and monitoring probes send */* and
    # must not be able to clear badges nobody has looked at. /healthz above
    # is the endpoint probes should use — this is the backstop for the ones
    # that don't.
    # Note this tests the RAW header for "text/html" rather than using
    # request.accept_mimetypes.accept_html, which returns True for the
    # "*/*" that curl sends — wildcard matching makes every probe look like
    # a browser. A real browser always names text/html explicitly.
    if "text/html" in request.headers.get("Accept", ""):
        visit_basis = storage.register_visit(conn)
    else:
        visit_basis = storage.current_visit_basis(conn)
    new_ids = storage.new_since_last_visit(conn, visit_basis)

    last_run = storage.last_run_time(conn)
    applied_total = storage.applied_count(conn)
    stage_counts = storage.pipeline_counts(conn)

    conn.close()

    # Freshness and the final score are recomputed here rather than read from
    # the database. They depend on today's date, so a stored value would be
    # stale the morning after it was written.
    volumes = scorer.company_volumes(postings)

    for posting in postings:
        age = scorer.days_old(posting)
        # The freshness curve depends on the company tier: a big-tech role
        # is behind by day one, a small company's can still be open a week
        # later. Recomputed here, like freshness itself, because it depends
        # on today's date.
        volume = volumes.get((posting["company"] or "").strip().lower())
        tier = posting.get("company_tier") or scorer.company_tier(
            posting, volume
        )
        posting["company_tier"] = tier
        posting["tier_label"] = config.COMPANY_TIERS[tier]["label"]
        fresh, fresh_label = scorer.freshness(age, tier)
        posting["age_days"] = age
        posting["freshness"] = fresh
        posting["freshness_label"] = fresh_label
        posting["is_fresh"] = scorer.is_fresh(age)
        posting["is_coop"] = scorer.is_coop(posting)
        posting["is_off_season"] = scorer.is_off_season(posting)
        posting["fit_score"] = scorer.final_score(
            posting["preference"], posting["candidacy_score"], fresh
        )
        posting["is_new"] = posting["id"] in new_ids
        posting["fit"] = scorer.fit_label(posting["fit_score"])
        # Employer tier and pay are pure functions of fields already on the
        # row, so they're computed here rather than stored — no migration,
        # and editing the lists in config.py takes effect on next reload.
        posting["employer_tier"] = scorer.employer_class(posting)
        posting["employer_label"] = config.EMPLOYER_TIERS[
            posting["employer_tier"]
        ]["label"]
        posting["is_tech_employer"] = posting["employer_tier"] in (
            "frontier", "big_tech", "tech"
        )
        posting["hourly_pay"] = scorer.hourly_pay(posting)

    hidden_by_age = sum(
        1 for p in postings
        if p["age_days"] is not None and p["age_days"] > config.MAX_AGE_DAYS
    )

    visible = _filtered(postings, new_ids, request.args)

    # Sorting happens after filtering, on the freshly computed numbers.
    sort = request.args.get("sort") or config.DEFAULT_SORT
    keys = {
        "score": lambda p: -p["fit_score"],
        "preference": lambda p: -p["preference"],
        "candidacy": lambda p: -p["candidacy_score"],
        # None sorts last: an unknown age shouldn't lead a recency sort.
        "recency": lambda p: (p["age_days"] is None, p["age_days"] or 0),
        # Same rule for pay: only a quarter of postings publish a rate, and
        # an unpublished rate is missing data, not a low number.
        "pay": lambda p: (p["hourly_pay"] is None, -(p["hourly_pay"] or 0)),
    }
    visible.sort(key=keys.get(sort, keys["score"]))

    # ONE COMPANY SHOULD NOT BE THE WHOLE LIST.
    #
    # Applied after sorting, so the roles kept are each company's best. The
    # held-back ones stay in the database and in every count — this only
    # decides what the page shows, and `allper=1` lifts it.
    # Quota state is computed over EVERY posting, not just the visible ones,
    # so an application made months ago still counts against the limit even
    # when its posting has aged off the list.
    quotas = _quota_state(postings)

    crowded = {}
    exhausted = []
    quota_limited = {}
    if config.MAX_PER_COMPANY and request.args.get("allper") != "1":
        per_company = {}
        capped = []
        for posting in visible:
            name = (posting["company"] or "").strip().lower()
            # An application you've made is a record, not a candidate, and
            # never counts against the cap or gets hidden by it.
            if posting.get("status"):
                capped.append(posting)
                continue

            # A capped company's real limit is what's LEFT of its quota, not
            # MAX_PER_COMPANY. Showing three TikTok roles when one
            # application remains isn't a shortlist, it's three ways to
            # waste the last slot.
            group = _quota_group(posting["company"])
            if group:
                key = group["name"]
                allowance = min(config.MAX_PER_COMPANY, quotas[key]["left"])
            else:
                key = name
                allowance = config.MAX_PER_COMPANY

            per_company[key] = per_company.get(key, 0) + 1
            if per_company[key] <= allowance:
                capped.append(posting)
            elif group:
                # Held back by the quota, not by ordinary crowding. Reported
                # separately because the two have different remedies: one
                # you can lift, the other is a fact about the employer.
                if quotas[key]["left"] == 0:
                    if key not in exhausted:
                        exhausted.append(key)
                else:
                    quota_limited[key] = quota_limited.get(key, 0) + 1
            else:
                crowded[posting["company"]] = crowded.get(
                    posting["company"], 0
                ) + 1
        visible = capped

    # Tell each surviving card how much of its company's quota is left, so
    # the constraint is visible at the moment you're deciding to apply.
    for posting in visible:
        group = _quota_group(posting["company"])
        posting["quota"] = quotas[group["name"]] if group else None

    # Categories for the filter dropdown, taken from the data itself so it
    # stays correct if you change INGEST_CATEGORIES in config.py.
    categories = sorted({p["category"] for p in postings})

    return render_template(
        "index.html",
        postings=visible,
        categories=categories,
        total_count=len(postings),
        new_count=len(new_ids),
        applied_total=applied_total,
        last_run=last_run,
        last_run_relative=_relative_time(last_run),
        sort=sort,
        caught_up=caught_up,
        stages=config.APPLICATION_STAGES,
        stage_counts=stage_counts,
        hidden_by_age=hidden_by_age,
        max_age_days=config.MAX_AGE_DAYS,
        showing_stale=request.args.get("stale") == "1",
        quotas=[q for q in quotas.values() if q["used"]],
        exhausted=[quotas[k] for k in exhausted],
        quota_limited=[(quotas[k], n) for k, n in quota_limited.items()],
        crowded=sorted(crowded.items(), key=lambda kv: -kv[1]),
        crowded_total=sum(crowded.values()),
        max_per_company=config.MAX_PER_COMPANY,
        showing_all_per=request.args.get("allper") == "1",
        within=_effective_within(request.args),
        hide_coop=_effective_hide_coop(request.args),
        tech_only=request.args.get("techonly") == "1",
        hide_offseason=_effective_hide_offseason(request.args),
        tiers=config.COMPANY_TIERS,
        tier=request.args.get("tier", ""),
        filters=request.args,
        config=config,
    )


@app.route("/applications")
def applications_route():
    """
    Everything you've applied to, and where it stands.

    The dashboard answers "what should I do next". This answers "what's in
    flight" — which is the question that starts mattering once applications
    accumulate and you can no longer hold them in your head.
    """
    conn = storage.connect()
    items = storage.pipeline(conn)
    counts = storage.pipeline_counts(conn)
    conn.close()

    stale = [
        item for item in items
        if item["status"] in ("applied", "oa")
        and (item["days_waiting"] or 0) >= config.STALE_APPLICATION_DAYS
    ]

    return render_template(
        "applications.html",
        items=items,
        counts=counts,
        stages=config.APPLICATION_STAGES,
        stale=stale,
        stale_days=config.STALE_APPLICATION_DAYS,
    )


@app.route("/api/status", methods=["POST"])
def api_status():
    """Move one application to a pipeline stage."""
    data = request.get_json(silent=True) or {}
    posting_id = data.get("id")
    status = data.get("status", "")

    if not posting_id:
        return jsonify({"ok": False, "error": "missing id"}), 400

    conn = storage.connect()
    try:
        storage.set_status(conn, posting_id, status)
    except ValueError as exc:
        conn.close()
        return jsonify({"ok": False, "error": str(exc)}), 400
    counts = storage.pipeline_counts(conn)
    conn.close()

    return jsonify({"ok": True, "status": status, "counts": counts})


@app.route("/api/notes", methods=["POST"])
def api_notes():
    """Save free-text notes against an application."""
    data = request.get_json(silent=True) or {}
    posting_id = data.get("id")
    if not posting_id:
        return jsonify({"ok": False, "error": "missing id"}), 400

    conn = storage.connect()
    storage.set_notes(conn, posting_id, data.get("notes", ""))
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/applied", methods=["POST"])
def api_applied():
    """
    Toggle a posting's applied status.

    The page calls this with JavaScript rather than submitting a form, so
    ticking a checkbox doesn't reload the page and lose your scroll position.
    It returns JSON because the caller is code, not a person.
    """
    data = request.get_json(silent=True) or {}
    posting_id = data.get("id")
    applied = bool(data.get("applied"))

    if not posting_id:
        # 400 = "the request was malformed". Returning a status code rather
        # than a cheerful 200 means the JavaScript can actually detect failure.
        return jsonify({"ok": False, "error": "missing id"}), 400

    conn = storage.connect()
    storage.set_applied(conn, posting_id, applied)
    total = storage.applied_count(conn)
    conn.close()

    return jsonify({"ok": True, "applied": applied, "applied_total": total})


@app.route("/mark-seen", methods=["POST"])
def mark_seen_route():
    """Clear every NEW badge."""
    conn = storage.connect()
    storage.mark_all_seen(conn)
    conn.close()
    return redirect(url_for("index", **request.args))


@app.route("/prompts/<path:posting_id>")
def prompts_route(posting_id):
    """
    The application-prep page for one posting: a cover letter prompt and a
    work experience prompt, both tailored to this role's family.

    Nothing here calls an API or costs anything — the page hands you text to
    paste into whatever assistant you already use.

    An optional `jd` query parameter carries a pasted job description, which
    materially improves both prompts. It travels in the URL rather than a
    database because it's per-application scratch, not something to keep.
    """
    conn = storage.connect()
    posting = next(
        (p for p in storage.load_postings(conn) if p["id"] == posting_id),
        None,
    )
    conn.close()

    if posting is None:
        return render_template("error.html",
                               message="No such posting."), 404

    job_description = request.args.get("jd", "")

    try:
        cover = letters.cover_letter_prompt(
            posting, job_description=job_description
        )
        experience = letters.work_experience_prompt(
            posting, job_description=job_description
        )
        error = None
    except letters.LetterError as exc:
        cover, experience, error = None, None, str(exc)

    return render_template(
        "prompts.html",
        posting=posting,
        cover_prompt=cover,
        experience_prompt=experience,
        profile_error=error,
        job_description=job_description,
    )


@app.route("/refresh", methods=["POST"])
def refresh_route():
    """
    Re-fetch listings from the button in the header.

    This runs the same pipeline as `python3 refresh.py`. It's synchronous —
    the browser waits the few seconds it takes. For a single-user local tool
    that's fine; a hosted app would push this to a background job.
    """
    # notifications=False: you're looking at the dashboard already, so a
    # macOS pop-up about what you're about to see would just be noise.
    run_refresh(verbose=False, notifications=False)
    # Redirect after a POST so refreshing the browser doesn't re-submit it.
    return redirect(url_for("index", **request.args))


if __name__ == "__main__":
    conn = storage.connect()
    has_data = storage.last_run_time(conn) is not None
    conn.close()

    if not has_data:
        print("\n  No data yet — run `python3 refresh.py` first.\n")

    print("  Dashboard: http://127.0.0.1:5000\n")

    # Debug mode is OFF unless you ask for it with --debug.
    #
    # It's genuinely useful while editing — it reloads on every file change
    # and shows tracebacks in the browser. But this app also runs as a
    # background service, and there debug mode is wrong: the reloader spawns
    # a second process launchd doesn't know about, and the interactive
    # debugger it exposes has no business being left running.
    debug = "--debug" in sys.argv

    # host="127.0.0.1" keeps this reachable only from this machine.
    app.run(host="127.0.0.1", port=5000, debug=debug)
