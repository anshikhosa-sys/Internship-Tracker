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
    GET  /stats          model usage, cache hit rates, coverage, recent runs
    POST /api/status     move an application to a stage
    POST /api/notes      save notes against an application
    POST /profile/active set the profile the dashboard opens with

Scores come from jobrank.ranking for the active profile (switch with
?profile=<id>); nothing here computes a score itself.
"""

import logging
import sys
from urllib.parse import urlparse
from datetime import datetime, timezone

from werkzeug.datastructures import MultiDict
from flask import (
    Flask, Response, jsonify, redirect, render_template, request, url_for
)

from jobrank import config
from jobrank import letters
from jobrank import textmatch
from jobrank import postings as posting_facts
from jobrank import ranking
from jobrank.config import companies
from jobrank.config import scoring as scoring_config
from jobrank.ops import selfcheck
from jobrank.profile import active
from jobrank.log import event, get_logger

log = get_logger(__name__)
from jobrank import storage
from jobrank.refresh import refresh as run_refresh

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 6 * 1024 * 1024

from jobrank.web.profile_views import bp as profiles_bp  # noqa: E402
app.register_blueprint(profiles_bp)

@app.before_request
def _reject_cross_site_posts():
    """
    Refuse state-changing requests that a browser marks as coming from another
    site. The server only listens on loopback, but any web page the user visits
    can still make their browser POST to 127.0.0.1 — overwriting a profile or
    an application's status. Requests with no Origin/Referer (curl, tests) pass.
    """
    if request.method not in ("POST", "PUT", "PATCH", "DELETE"):
        return None
    origin = request.headers.get("Origin") or request.headers.get("Referer") or ""
    if origin and urlparse(origin).netloc != request.host:
        return Response("Cross-site request refused.", status=403, content_type="text/plain")
    return None


# =============================================================================
# Helpers
# =============================================================================

# How many cards one page of results renders.
#
# The list used to render every match — 361 cards, 60,000 pixels of scroll.
# Nobody reads card 200, and the browser pays for all of them. The COUNTS
# stay truthful (they describe the whole result set); only the rendering is
# paged, and the page number lives in the URL like every other filter.
PAGE_SIZE = 25

SIZE_LABELS = {"large": "Large company", "mid": "Mid-size", "startup": "Startup / small"}
FACTOR_LABELS = {
    "skills": "Skills you have",
    "seniority": "Right level for you",
    "role": "Kind of role you want",
    "freshness": "Still open",
    "semantic": "Résumé similarity",
}


# Words in a location that mean "you don't have to be there". This is a
# display filter over the location string, not a scoring input — sources
# don't publish a remote flag, they publish "Remote (US)".
REMOTE_WORDS = ("remote", "anywhere", "virtual", "wfh")


def _is_remote(posting) -> bool:
    """Whether a posting's location reads as remote."""
    where = posting.get("location") or ""
    # Whole words: "remote" must not match inside a company's address, and
    # textmatch is the only place in this project that builds a regex.
    return any(textmatch.contains(where, word) for word in REMOTE_WORDS)


def _profile_ids():
    from jobrank.profile import store
    return store.list_ids()


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
    # The location box, separate from the keyword box. Every job board has
    # both, and folding location into the keyword search means "Austin"
    # also matches a company called Austin Industries.
    where = (args.get("l") or "").strip().lower()
    remote_only = args.get("remote") == "1"
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

    # One term filter replaces the old "Summer only" + "No co-ops" pair.
    # They overlapped — most co-ops ARE summer terms — so the two boxes could
    # contradict each other and hide rows neither label mentioned.
    term = _effective_term(args)

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

        # Hide low-fit postings unless asked for. They're still in the
        # database and still scored — just collapsed by default.
        if (not in_pipeline and not show_low
                and posting["fit_score"] < scoring_config.LOW_FIT_THRESHOLD):
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

        if term and not in_pipeline:
            if term == "coop" and not posting["is_coop"]:
                continue
            if term == "summer" and (posting["is_coop"] or posting["is_off_season"]):
                continue
            if term == "offseason" and not posting["is_off_season"]:
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

        if where and where not in (posting["location"] or "").lower():
            continue

        if remote_only and not _is_remote(posting):
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


def _effective_term(args) -> str:
    """
    Which term the list is restricted to: "", "summer", "coop" or "offseason".

    A single value rather than two independent checkboxes. The old pair could
    both be ticked, and since most co-ops are summer terms that combination hid
    postings neither label claimed to hide (invariant 18: a filter must do what
    it says). Anything already in the application pipeline is exempt, because a
    filter hides, it never deletes.
    """
    value = (args.get("term") or "").strip().lower()
    return value if value in ("summer", "coop", "offseason") else ""


# =============================================================================
# Routes
# =============================================================================

def _quota_group(company):
    """
    The capped parent company this employer belongs to, or None.

    Grouped by parent, so an application at AWS spends Amazon's slot.
    """
    parent = posting_facts.parent_company(company or "")
    if parent and parent in companies.APPLICATION_LIMITS:
        return {"name": parent, "limit": companies.APPLICATION_LIMITS[parent]}
    return None


def _quota_state(postings):
    """
    How much of each capped company's quota has been spent. Counts anything
    with a status — a rejection still consumed a slot.
    """
    used = {}
    for posting in postings:
        if not posting.get("status"):
            continue
        group = _quota_group(posting.get("company"))
        if group:
            used[group["name"]] = used.get(group["name"], 0) + 1
    return {
        name: {"name": name, "limit": limit, "used": used.get(name, 0),
               "left": max(0, limit - used.get(name, 0))}
        for name, limit in companies.APPLICATION_LIMITS.items()
    }


def _apply_caps(visible, quotas, show_all):
    """
    Trim the ranked list so no company dominates it, and so a spent
    application quota stops offering roles you cannot apply to.

    Returns (visible, crowded, exhausted, quota_limited).

    Shared with _age_window_counts so the number beside each age window in
    the dropdown is the number you actually get. It said 41 and showed 33
    before this was pulled out — a filter whose own label disagrees with
    the result is the same class of problem as the one this page had.
    """
    crowded, exhausted, quota_limited = {}, [], {}
    if not companies.MAX_PER_COMPANY or show_all:
        return visible, crowded, exhausted, quota_limited

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
        # MAX_PER_COMPANY. Showing three TikTok roles when one application
        # remains isn't a shortlist, it's three ways to waste the last slot.
        group = _quota_group(posting["company"])
        if group:
            key = group["name"]
            allowance = min(companies.MAX_PER_COMPANY, quotas[key]["left"])
        else:
            key = name
            allowance = companies.MAX_PER_COMPANY

        per_company[key] = per_company.get(key, 0) + 1
        if per_company[key] <= allowance:
            capped.append(posting)
        elif group:
            # Held back by the quota, not by ordinary crowding. Reported
            # separately because the two have different remedies: one you
            # can lift, the other is a fact about the employer.
            if quotas[key]["left"] == 0:
                if key not in exhausted:
                    exhausted.append(key)
            else:
                quota_limited[key] = quota_limited.get(key, 0) + 1
        else:
            crowded[posting["company"]] = crowded.get(
                posting["company"], 0) + 1

    return capped, crowded, exhausted, quota_limited


def _age_window_counts(postings, new_ids, args):
    """
    For each age window, how many postings it would show.

    Rendered into the dropdown as "Last 3 days (34)". The filter that
    prompted this could never match anything, and there was no way to tell
    from the interface — the list just went blank.
    """
    windows = []
    for days, label in config.AGE_WINDOWS:
        # The probe must carry the EFFECTIVE filter state, not just f=1.
        # Setting f=1 alone tells _filtered "the user submitted the form",
        # which turns OFF the co-op and off-season defaults — so the count
        # was computed against a slightly different filter than the page
        # used, and read one higher than the list it described.
        probe = MultiDict(args)
        probe["f"] = "1"
        probe["term"] = _effective_term(args)
        probe["within"] = "" if days is None else str(days)
        matched = _filtered(postings, new_ids, probe)
        matched, _, _, _ = _apply_caps(
            matched, _quota_state(postings), args.get("allper") == "1"
        )
        count = len(matched)
        windows.append({
            "value": "" if days is None else str(days),
            "label": label,
            "count": count,
        })
    return windows


def _decorate(postings, new_ids):
    """Facts the list and the detail pane both display but nothing stores."""
    for posting in postings:
        posting["is_fresh"] = (posting["age_days"] is not None
                               and posting["age_days"] <= config.FRESH_DAYS)
        posting["is_new"] = posting["id"] in new_ids
        posting["tier_label"] = SIZE_LABELS.get(posting["company_tier"], "")
        posting["is_remote"] = _is_remote(posting)
    return postings


@app.template_global()
def qurl(**overrides):
    """
    This page's URL with some query parameters changed.

    Every filter, the page number and the selected job all live in the URL,
    so a link that changes one has to carry the rest. Passing "" drops a
    parameter, which is how the override links switch themselves back off.
    """
    args = request.args.to_dict(flat=True)
    args.update(overrides)
    args = {k: v for k, v in args.items() if v not in (None, "")}
    return url_for("index", **args)


def _selfcheck_banner():
    """The last weekly self-check, but only if it found something wrong."""
    conn = storage.connect()
    try:
        result = selfcheck.last_result(conn)
    finally:
        conn.close()

    if not result or not result.get("failures"):
        return None
    return {
        "failures": result["failures"],
        "when": _relative_time(result.get("ran_at")),
    }


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
    except Exception as exc:  # noqa: BLE001 - stale data beats an error page
        event(log, "catch_up_refresh_failed", level=logging.WARNING, error=f"{type(exc).__name__}: {exc}"[:300])
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

    # A GET never changes stored state. Viewing another profile's ranking is
    # a view, not a preference; "Make default" below is a POST.
    requested = request.args.get("profile")
    ranked = ranking.rank(requested, conn=conn)
    if ranked is None:
        conn.close()
        return redirect(url_for("profiles.profile_form", first=1))
    postings = ranked.rows

    # Only a real page view counts as a visit. A browser rendering this page
    # always names text/html in Accept; curl and monitoring probes send */*
    # and must not clear badges nobody has looked at. This tests the RAW
    # header: request.accept_mimetypes.accept_html is True for "*/*".
    if "text/html" in request.headers.get("Accept", ""):
        visit_basis = storage.register_visit(conn)
    else:
        visit_basis = storage.current_visit_basis(conn)
    new_ids = storage.new_since_last_visit(conn, visit_basis)

    last_run = storage.last_run_time(conn)
    applied_total = storage.applied_count(conn)
    stage_counts = storage.pipeline_counts(conn)

    conn.close()

    _decorate(postings, new_ids)

    hidden_by_age = sum(
        1 for p in postings
        if p["age_days"] is not None and p["age_days"] > config.MAX_AGE_DAYS
    )

    visible = _filtered(postings, new_ids, request.args)

    # Sorting happens after filtering, on the freshly computed numbers.
    sort = request.args.get("sort") or config.DEFAULT_SORT
    keys = {
        "score": lambda p: -p["fit_score"],
        "role": lambda p: -p["factor_values"].get("role", 0),
        "skills": lambda p: -p["factor_values"].get("skills", 0),
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

    visible, crowded, exhausted, quota_limited = _apply_caps(
        visible, quotas, request.args.get("allper") == "1"
    )

    # Tell each surviving card how much of its company's quota is left, so
    # the constraint is visible at the moment you're deciding to apply.
    for posting in visible:
        group = _quota_group(posting["company"])
        posting["quota"] = quotas[group["name"]] if group else None

    # Categories for the filter dropdown, taken from the data itself so it
    # stays correct if you change INGEST_CATEGORIES in config.py.
    categories = sorted({p["category"] for p in postings})

    # ONE PAGE OF CARDS, NOT ALL OF THEM.
    #
    # `result_count` is the whole filtered set and is what every count on
    # the page reports; `page_rows` is only what gets rendered. Keeping the
    # two separate is what lets the age-window counts stay truthful.
    result_count = len(visible)
    page_count = max(1, -(-result_count // PAGE_SIZE))

    # A link to one job has to land on the page that job is on, or the
    # highlighted card is nowhere to be seen.
    selected_id = request.args.get("job") or ""
    position = next((i for i, p in enumerate(visible)
                     if p["id"] == selected_id), None)
    if position is not None and not request.args.get("page"):
        page = position // PAGE_SIZE + 1
    else:
        try:
            page = int(request.args.get("page") or 1)
        except ValueError:
            page = 1
    page = max(1, min(page, page_count))

    page_rows = visible[(page - 1) * PAGE_SIZE: page * PAGE_SIZE]

    # What the detail pane shows. Defaults to the first card, so the pane is
    # never empty and the page reads the same with JavaScript off.
    selected = next((p for p in page_rows if p["id"] == selected_id), None)
    if selected is None and selected_id:
        # Linked to a posting the current filters hide: still show it.
        selected = next((p for p in postings if p["id"] == selected_id), None)
        if selected is not None:
            group = _quota_group(selected["company"])
            selected["quota"] = quotas[group["name"]] if group else None
    if selected is None:
        selected = page_rows[0] if page_rows else None

    return render_template(
        "index.html",
        postings=page_rows,
        selected=selected,
        result_count=result_count,
        page=page,
        page_count=page_count,
        page_size=PAGE_SIZE,
        page_first=(page - 1) * PAGE_SIZE + 1 if page_rows else 0,
        page_last=(page - 1) * PAGE_SIZE + len(page_rows),
        categories=categories,
        total_count=len(postings),
        new_count=len(new_ids),
        applied_total=applied_total,
        last_run=last_run,
        last_run_relative=_relative_time(last_run),
        # A notification you were away for is not lost: the last weekly
        # self-check result shows here too.
        selfcheck=_selfcheck_banner(),
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
        max_per_company=companies.MAX_PER_COMPANY,
        showing_all_per=request.args.get("allper") == "1",
        within=_effective_within(request.args),
        # Each window carries how many postings it would actually show, so
        # an empty result is visible BEFORE you pick it rather than looking
        # like a broken page afterwards.
        age_windows=_age_window_counts(postings, new_ids, request.args),
        term=_effective_term(request.args),
        tech_only=request.args.get("techonly") == "1",
        remote_only=request.args.get("remote") == "1",
        show_low=request.args.get("show_low") == "1",
        tiers={k: {"label": v} for k, v in SIZE_LABELS.items()},
        profile=ranked.profile,
        profiles=_profile_ids(),
        default_profile=active.resolve(None),
        factor_labels=FACTOR_LABELS,
        tier=request.args.get("tier", ""),
        filters=request.args,
        config=config,
    )


@app.route("/job/<path:posting_id>")
def job_panel(posting_id):
    """
    One posting's detail pane, as an HTML fragment.

    The list fetches this and swaps it in, so choosing a different job costs
    one small request instead of re-rendering the whole page. It is the same
    template the dashboard renders inline, so the JavaScript path and the
    no-JavaScript path cannot drift apart.

    A GET, and it reads only: no visit is registered here, or moving down
    the list would quietly clear the NEW badges you are moving through.
    """
    conn = storage.connect()
    ranked = ranking.rank(request.args.get("profile"), conn=conn)
    if ranked is None:
        conn.close()
        return render_template("error.html", message="No profile yet."), 404

    postings = ranked.rows
    new_ids = storage.new_since_last_visit(
        conn, storage.current_visit_basis(conn))
    conn.close()

    posting = next((p for p in postings if p["id"] == posting_id), None)
    if posting is None:
        return render_template("error.html", message="No such posting."), 404

    _decorate([posting], new_ids)
    quotas = _quota_state(postings)
    group = _quota_group(posting["company"])
    posting["quota"] = quotas[group["name"]] if group else None

    body = render_template(
        "_detail.html",
        posting=posting,
        stages=config.APPLICATION_STAGES,
        factor_labels=FACTOR_LABELS,
    )
    # content_type, not mimetype: mimetype appends a second charset.
    return Response(body, content_type="text/html; charset=utf-8")


@app.route("/profile/active", methods=["POST"])
def set_active_profile():
    """Make one profile the default the dashboard opens with."""
    conn = storage.connect()
    try:
        wanted = request.form.get("profile")
        chosen = active.resolve(wanted, conn)
        if chosen != wanted:
            return render_template("error.html", message="No such profile."), 404
        active.set_active(conn, chosen)
    finally:
        conn.close()
    return redirect(url_for("index"))


@app.route("/stats")
def stats_route():
    """What the machinery is doing: model usage, caches, coverage, recent runs."""
    from jobrank.ops import stats

    return render_template("stats.html", stats=stats.collect())


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


@app.route("/prompts/<path:posting_id>/<kind>.txt")
def prompt_text_route(posting_id, kind):
    """
    One prompt as plain text, nothing else on the page.

    THIS IS WHAT THE COPY BUTTON READS. It used to scrape the rendered <pre>
    with innerText, which is a rendering-dependent API: it reflows the
    element, is affected by CSS (this one has max-height, overflow-y: auto,
    white-space: pre-wrap and word-break: break-word), and returns the text
    as LAID OUT rather than as written. Prompts were arriving mangled or
    empty. Fetching the source text from the server removes every one of
    those variables.

    It doubles as the manual escape hatch: open this URL and Cmd+A, Cmd+C
    works with no JavaScript at all.
    """
    if kind not in ("cover", "experience"):
        return "Unknown prompt.", 404

    conn = storage.connect()
    posting = next(
        (p for p in storage.load_postings(conn) if p["id"] == posting_id),
        None,
    )
    conn.close()

    if posting is None:
        return "No such posting.", 404

    job_description = request.args.get("jd", "")
    try:
        if kind == "cover":
            body = letters.cover_letter_prompt(
                posting, job_description=job_description
            )
        else:
            body = letters.work_experience_prompt(
                posting, job_description=job_description
            )
    except letters.LetterError as exc:
        return str(exc), 500

    # text/plain so the browser shows it rather than downloading it, and an
    # explicit charset because the prompt contains en dashes and accents.
    # content_type, not mimetype: mimetype appends its own charset and you
    # end up with "text/plain; charset=utf-8; charset=utf-8".
    return Response(body, content_type="text/plain; charset=utf-8")


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


def main():
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


if __name__ == "__main__":
    main()
