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

WHY THERE'S NO LOGIN
Single user, local only. The server binds to 127.0.0.1, which means it accepts
connections only from this machine — nothing outside can reach it. That's why
there's no password: there's no one else to keep out.
"""

import sys

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

    if submitted:
        hide_coop = args.get("nocoop") == "1"
    else:
        hide_coop = config.DEFAULT_HIDE_COOP

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
        # Hide low-fit postings unless asked for. They're still in the
        # database and still scored — just collapsed by default.
        if not show_low and posting["fit_score"] < config.LOW_FIT_THRESHOLD:
            continue

        # The age cutoff. This HIDES postings rather than ranking them
        # lower, so it's the one filter that can lose you something — hence
        # the explicit override rather than a silent drop.
        age = posting["age_days"]
        too_old = age is not None and age > config.MAX_AGE_DAYS
        if too_old and not show_stale:
            continue

        if within is not None and (age is None or age > within):
            continue

        if hide_coop and posting["is_coop"]:
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


# =============================================================================
# Routes
# =============================================================================

@app.route("/")
def index():
    """The dashboard: ranked postings, best fit first."""
    conn = storage.connect()

    postings = storage.load_postings(conn)

    # Record that you've opened the dashboard, and find out what counts as new
    # to you. NEW means "arrived since you last looked", not "arrived in the
    # last refresh" — the two stopped being the same thing once refreshes
    # became automatic. See storage.register_visit().
    visit_basis = storage.register_visit(conn)
    new_ids = storage.new_since_last_visit(conn, visit_basis)

    last_run = storage.last_run_time(conn)
    applied_total = storage.applied_count(conn)

    conn.close()

    # Freshness and the final score are recomputed here rather than read from
    # the database. They depend on today's date, so a stored value would be
    # stale the morning after it was written.
    for posting in postings:
        age = scorer.days_old(posting)
        fresh, fresh_label = scorer.freshness(age)
        posting["age_days"] = age
        posting["freshness"] = fresh
        posting["freshness_label"] = fresh_label
        posting["is_fresh"] = scorer.is_fresh(age)
        posting["is_coop"] = scorer.is_coop(posting)
        posting["fit_score"] = scorer.final_score(
            posting["preference"], posting["candidacy_score"], fresh
        )
        posting["is_new"] = posting["id"] in new_ids
        posting["fit"] = scorer.fit_label(posting["fit_score"])

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
    }
    visible.sort(key=keys.get(sort, keys["score"]))

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
        sort=sort,
        hidden_by_age=hidden_by_age,
        max_age_days=config.MAX_AGE_DAYS,
        showing_stale=request.args.get("stale") == "1",
        within=_effective_within(request.args),
        hide_coop=_effective_hide_coop(request.args),
        filters=request.args,
        config=config,
    )


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
