"""
Scoring — how worth applying to is this posting, today?

This file contains NO numbers and NO keywords. Every value comes from
config.py, so tuning never means editing logic.

THE MODEL
---------
Three factors, each 0 to 1, raised to a weight and multiplied:

    score = preference^0.35 x candidacy^1.0 x freshness^0.75 x 100

    preference   do you want it        (role family + topic)
    candidacy    would they take you   (resume, category, hiring volume)
    freshness    is it still open      (age of the posting)

WHY MULTIPLY — THE BUG THIS REPLACED
------------------------------------
The first version ADDED a large constant for wanting a role: +150 for
anything titled "forward deployed". Preference then swamped everything, and
the top of the list filled with month-old postings at companies that take a
handful of interns — real matches for the wish list, useless as actions.

Multiplication encodes the real requirement: an application is worth making
only if ALL THREE hold. You want it, AND you could plausibly get it, AND it's
still open. A near-zero in any factor should sink the result, and here it
does.

WHY THE EXPONENTS
-----------------
Equal weighting still let preference decide too much. The goal is roles that
can be WON, not a ranked list of things worth wanting. An exponent below 1
compresses a factor toward 1: preference^0.35 turns a 0.50-1.00 spread into
roughly 0.78-1.00, so it nudges the order without setting it. Candidacy keeps
full weight and does the real work.

For candidacy to deserve that weight it has to vary, and title keywords alone
left 54% of postings at the baseline. Two more signals fix it: which category
the role is in (two SWE internships is direct evidence for SWE, thin evidence
for PM) and how many roles the company is posting (117 means a structured
program; 1 means a lottery).

Nothing is ever excluded by age or odds — a bad factor lowers a posting, it
never hides it. Recruiters are consistent that old postings are still worth
applying to, and the rarest top-tier roles are exactly the ones you'd hate to
lose to a filter.

THREE IMPLEMENTATION NOTES
--------------------------
1. WHOLE-WORD MATCHING, NOT SUBSTRINGS. `if "ai" in title` is a bug: "ai"
   appears inside "training", "email", and "maintain"; "ml" inside "html".
   Every keyword is matched with a word-boundary regex instead.

2. FRESHNESS IS NEVER STORED. It changes daily for the same posting, so a
   stored score would be wrong by morning. Preference and candidacy are
   stable and get saved; freshness and the final score are recomputed every
   time they're shown.

3. PREFERENCE AND CANDIDACY STAY SEPARATE. Blending them would hide the two
   cases that matter most: a role you'd love but can't get, and one you'd
   walk into but hadn't considered. Those need different actions, so they get
   different numbers.
"""

import os
import re
from datetime import date

import config


# =============================================================================
# The résumé itself
# =============================================================================

_PROFILE_CACHE = {"path": None, "text": ""}


def profile_text() -> str:
    """
    The raw text of profile.md, lowercased, cached per path.

    Read directly rather than through letters.py so scoring never depends on
    the prompt module. A missing profile returns "" — scoring degrades to the
    hand-written weights rather than crashing, since you should be able to
    browse listings before writing a résumé file.
    """
    path = config.PROFILE_PATH
    if _PROFILE_CACHE["path"] != path:
        text = ""
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as handle:
                    text = handle.read().lower()
            except OSError:
                text = ""
        _PROFILE_CACHE.update({"path": path, "text": text})
    return _PROFILE_CACHE["text"]


def profile_supports(term: str) -> bool:
    """
    Does the résumé actually contain this term?

    The check that makes candidacy résumé-DRIVEN rather than merely
    résumé-inspired. An audit found 15 of 36 evidence keywords were never in
    the résumé at all — they were guesses about what it implied, and they
    were inflating candidacy for experience that couldn't be pointed at.
    """
    text = profile_text()
    if not text:
        # No résumé on disk: fall back to trusting the configured list, since
        # blocking everything would make the tool useless before setup.
        return True
    return term.lower() in text


# =============================================================================
# Keyword matching
# =============================================================================

_PATTERN_CACHE = {}


def _pattern_for(keyword: str):
    """Build (and cache) a whole-word regex for one keyword."""
    if keyword not in _PATTERN_CACHE:
        # re.escape handles regex-special characters in keywords, like the
        # "+" in "c++" or the "-" in "forward-deployed".
        _PATTERN_CACHE[keyword] = re.compile(
            r"\b" + re.escape(keyword) + r"\b", re.IGNORECASE
        )
    return _PATTERN_CACHE[keyword]


def _matches(keyword: str, text: str) -> bool:
    return bool(_pattern_for(keyword).search(text))


def _field(posting, name):
    """Read a field from either a Posting object or a database row dict."""
    if hasattr(posting, name):
        return getattr(posting, name)
    return posting.get(name)


# =============================================================================
# The internship gate
# =============================================================================

def is_internship(posting) -> bool:
    """
    Does this look like an internship?

    The Summer 2027 repo is internship-only, so today this passes nearly
    everything. It earns its keep when a second source is added.
    """
    if not config.REQUIRE_INTERNSHIP:
        return True
    title = _field(posting, "role")
    return any(_matches(kw, title) for kw in config.INTERNSHIP_KEYWORDS)


# =============================================================================
# 1. Preference — do you want it?
# =============================================================================

def preference(posting):
    """
    How much you want this role, 0 to 1. Returns (value, reasons, family).

    `family` names the matched ROLE_FAMILIES entry; the letter generator uses
    it to decide what a letter for this kind of role should emphasize.
    """
    reasons = []
    title = _field(posting, "role")

    # -- role family: highest match wins ------------------------------------
    # A posting can match several families ("Solutions Engineer" contains
    # "engineer"). Taking the highest keeps families behaving like priorities,
    # and stays correct if you reorder them in config.py.
    best = None
    for family in config.ROLE_FAMILIES:
        if any(_matches(kw, title) for kw in family["keywords"]):
            if best is None or family["preference"] > best["preference"]:
                best = family

    if best:
        value = best["preference"]
        family_name = best["name"]
        reasons.append({
            "label": f"Role type: {family_name}",
            "detail": f"base {value:.2f}",
        })
    else:
        value = config.UNKNOWN_FAMILY_PREFERENCE
        family_name = ""
        reasons.append({
            "label": "Role type not recognized",
            "detail": f"base {value:.2f}",
        })

    # -- topic lift ---------------------------------------------------------
    lift = 0.0
    hits = []
    for keyword, amount in config.FOCUS_LIFT.items():
        if _matches(keyword, title):
            lift += amount
            hits.append(keyword)

    if lift > config.MAX_FOCUS_LIFT:
        lift = config.MAX_FOCUS_LIFT

    if hits:
        value = min(1.0, value + lift)
        reasons.append({
            "label": "Areas you want: " + ", ".join(hits),
            "detail": f"+{lift:.2f}",
        })

    # -- out of scope: multiplies down --------------------------------------
    for keyword, multiplier in config.OUT_OF_SCOPE.items():
        if _matches(keyword, title):
            value *= multiplier
            reasons.append({
                "label": f"Outside this search: {keyword}",
                "detail": f"x{multiplier:.2f}",
            })

    # Some employers are out of scope for reasons the title never shows.
    # "Applied AI Engineer Intern" looks ideal until you see it's a hedge
    # fund and the work is signal research.
    company = (_field(posting, "company") or "").lower()
    for name, multiplier in config.OUT_OF_SCOPE_COMPANIES.items():
        if name in company:
            value *= multiplier
            reasons.append({
                "label": f"Quant/trading firm: {_field(posting, 'company')}",
                "detail": f"x{multiplier:.2f}",
            })
            break

    return round(value, 3), reasons, family_name


# =============================================================================
# 2. Candidacy — would they take you?
# =============================================================================

def candidacy(posting, company_volume=None):
    """
    How plausible a candidate you are, 0 to 1. Returns (value, reasons).

    Grounded in what the résumé proves. This carries the most weight in the
    final score, so it has to actually vary — hence three signals rather than
    just title keywords, which alone left 54% of postings at the baseline.

    `company_volume` is how many roles this company is currently posting.
    Passed in because it can only be computed by looking at the whole list.
    """
    reasons = []
    title = _field(posting, "role")
    category = _field(posting, "category") or "Uncategorized"

    value = config.CANDIDACY_BASELINE
    reasons.append({
        "label": "Baseline for an undergrad with two internships",
        "detail": f"base {value:.2f}",
    })

    # -- evidence from the résumé -------------------------------------------
    # A keyword only counts if it appears in BOTH the job title and the
    # résumé. Matching the title alone would credit experience you can't
    # point at — the same failure the cover letter prompt forbids.
    evidence = 0.0
    hits = []
    unsupported = []
    for keyword, amount in config.CANDIDACY_EVIDENCE.items():
        if not _matches(keyword, title):
            continue
        if (config.REQUIRE_EVIDENCE_IN_PROFILE
                and not profile_supports(keyword)):
            unsupported.append(keyword)
            continue
        evidence += amount
        hits.append(keyword)

    if evidence > config.CANDIDACY_MAX_EVIDENCE:
        evidence = config.CANDIDACY_MAX_EVIDENCE

    if hits:
        value = min(1.0, value + evidence)
        reasons.append({
            "label": "Your resume proves: " + ", ".join(hits),
            "detail": f"+{evidence:.2f}",
        })

    # -- what category the role sits in -------------------------------------
    # Two SWE internships is direct evidence for a SWE role and thin evidence
    # for a PM one, however good the engineering is.
    cat_adjust = 0.0
    for name, amount in config.CANDIDACY_CATEGORY.items():
        if name.lower() in category.lower():
            cat_adjust = amount
            break
    if cat_adjust:
        value = max(0.0, min(1.0, value + cat_adjust))
        direction = "supports" if cat_adjust > 0 else "works against"
        reasons.append({
            "label": f"Your background {direction} {category} roles",
            "detail": f"{cat_adjust:+.2f}",
        })

    # -- how many interns this company takes --------------------------------
    # A company posting 117 roles runs a structured program; one posting a
    # single role is a lottery with one ticket.
    if company_volume:
        for threshold, amount in config.CANDIDACY_VOLUME_TIERS:
            if company_volume >= threshold:
                if amount:
                    value = min(1.0, value + amount)
                    reasons.append({
                        "label": f"Hiring at scale "
                                 f"({company_volume} roles posted)",
                        "detail": f"+{amount:.2f}",
                    })
                break

    if unsupported:
        # Not a penalty — just honesty about why a promising-looking title
        # didn't earn points.
        reasons.append({
            "label": "In the title but not on your resume: "
                     + ", ".join(unsupported),
            "detail": "+0.00",
        })

    # -- technologies your résumé doesn't support ---------------------------
    # Softer than a blocker: a role wanting Go isn't closed to you, it's a
    # worse use of an application than one wanting Python. Anything that
    # later appears in your résumé stops counting automatically.
    for keyword, multiplier in config.UNSUPPORTED_TECH.items():
        if _matches(keyword, title) and not profile_supports(keyword):
            value *= multiplier
            reasons.append({
                "label": f"Wants {keyword}, which your resume doesn't show",
                "detail": f"x{multiplier:.2f}",
            })

    # -- blockers: multiply down --------------------------------------------
    for keyword, multiplier in config.CANDIDACY_BLOCKERS.items():
        if _matches(keyword, title):
            value *= multiplier
            reasons.append({
                "label": f"Works against you: {keyword}",
                "detail": f"x{multiplier:.2f}",
            })

    # The repo's advanced-degree marker is set deliberately by maintainers, so
    # it's more reliable than inferring from title wording.
    if _field(posting, "needs_advanced_degree"):
        value *= config.ADVANCED_DEGREE_MULTIPLIER
        reasons.append({
            "label": "Requires an advanced degree",
            "detail": f"x{config.ADVANCED_DEGREE_MULTIPLIER:.2f}",
        })

    if _field(posting, "is_faang"):
        value *= config.COMPETITIVE_EMPLOYER_MULTIPLIER
        reasons.append({
            "label": "Highly competitive employer",
            "detail": f"x{config.COMPETITIVE_EMPLOYER_MULTIPLIER:.2f}",
        })

    return round(value, 3), reasons


# =============================================================================
# 3. Freshness — is it still open?
# =============================================================================

def days_old(posting):
    """
    Days since posting, or None if unknown.

    Computed from `date_posted` — a real date — rather than the source's "Age"
    text, so it keeps ageing correctly between refreshes instead of freezing
    at whatever the last fetch happened to see.
    """
    posted = _field(posting, "date_posted")
    if not posted:
        return None
    try:
        return max(0, (date.today() - date.fromisoformat(posted)).days)
    except (TypeError, ValueError):
        return None


def company_tier(posting, company_volume=None) -> str:
    """
    Which tier this company's hiring falls into: "big", "mid", or "niche".

    Decided from data already in hand — the source's competitive marker and
    how many roles the company is posting — rather than a hand-maintained
    list of company names that would go stale immediately.
    """
    if _field(posting, "is_faang"):
        return "big"

    volume = company_volume or 0
    if volume >= config.COMPANY_TIERS["big"]["min_postings"]:
        return "big"
    if volume >= config.COMPANY_TIERS["mid"]["min_postings"]:
        return "mid"
    return "niche"


def freshness(age_days, tier="mid"):
    """
    Multiplier for how likely this is still open. Returns (value, label).

    The curve depends on the COMPANY, not just the calendar. A big-tech role
    is meaningfully behind by day one; a small company's posting can still be
    wide open a week later. Using one curve for both wastes applications at
    both ends — missing big-tech roles by a day, and skipping startup roles
    that were never closing.
    """
    if age_days is None:
        return config.UNKNOWN_AGE_FRESHNESS, "Age unknown"

    curve = config.COMPANY_TIERS.get(tier, {}).get(
        "curve", config.FRESHNESS_CURVE
    )

    for threshold, multiplier in curve:
        if age_days <= threshold:
            if age_days == 0:
                label = "Posted today"
            elif age_days == 1:
                label = "Posted yesterday"
            else:
                label = f"Posted {age_days} days ago"
            return multiplier, label

    return config.UNKNOWN_AGE_FRESHNESS, "Age unknown"


def is_coop(posting) -> bool:
    """
    Does this look like a co-op — full-time during a school term?

    Deliberately a label rather than a filter. Many listings read
    "Intern/Co-op" and are ordinary summer internships, so hiding them
    automatically would cost real roles.
    """
    title = _field(posting, "role") or ""
    return any(_matches(kw, title) for kw in config.COOP_KEYWORDS)


def is_off_season(posting) -> bool:
    """
    Is this for a term other than summer?

    "Software Engineer Intern - Winter 2027" runs during the academic year.
    Same practical problem as a co-op: you'd not be in class.

    A label, not a scoring input — and matched on the title only, so a
    company merely named "Winter" isn't caught.
    """
    title = _field(posting, "role") or ""
    return any(_matches(kw, title) for kw in config.OFF_SEASON_KEYWORDS)


def is_fresh(age_days) -> bool:
    return age_days is not None and age_days <= config.FRESH_DAYS


# =============================================================================
# Putting it together
# =============================================================================

def final_score(pref_value, cand_value, fresh_value) -> int:
    """
    The three factors combined, as a 0-100 number.

    Each is raised to its weight from config.SCORE_WEIGHTS before
    multiplying. An exponent below 1 compresses a factor toward 1, so it
    still moves the result but can no longer dominate it — which is how
    preference becomes a tie-breaker rather than the driver.

    Exponentiation needs a non-negative base, and a zero factor must stay
    zero rather than becoming 1 (0 ** 0 == 1 in Python), so both edges are
    handled explicitly.
    """
    weights = config.SCORE_WEIGHTS
    total = 1.0
    for value, key in (
        (pref_value, "preference"),
        (cand_value, "candidacy"),
        (fresh_value, "freshness"),
    ):
        value = max(0.0, value)
        weight = weights.get(key, 1.0)
        if value == 0.0:
            return 0            # a zero anywhere still sinks the whole thing
        total *= value ** weight
    return round(total * 100)


def company_volumes(postings) -> dict:
    """
    How many roles each company is posting.

    Computed across the whole list, so it has to happen before scoring rather
    than inside it.
    """
    counts = {}
    for posting in postings:
        company = _field(posting, "company") or ""
        key = company.strip().lower()
        counts[key] = counts.get(key, 0) + 1
    return counts


def score_posting(posting, volumes=None) -> dict:
    """
    Score one posting completely.

    Returns every component, not just the total, so the dashboard can show the
    whole calculation instead of one opaque number.
    """
    company = (_field(posting, "company") or "").strip().lower()
    volume = (volumes or {}).get(company)

    pref, pref_reasons, family = preference(posting)
    cand, cand_reasons = candidacy(posting, company_volume=volume)
    age = days_old(posting)
    tier = company_tier(posting, volume)
    fresh, fresh_label = freshness(age, tier)

    return {
        "preference": pref,
        "preference_reasons": pref_reasons,
        "candidacy": cand,
        "candidacy_reasons": cand_reasons,
        "freshness": fresh,
        "freshness_label": fresh_label,
        "age_days": age,
        "is_fresh": is_fresh(age),
        "role_family": family,
        "company_tier": tier,
        "tier_label": config.COMPANY_TIERS[tier]["label"],
        "score": final_score(pref, cand, fresh),
    }


def score_all(postings) -> list:
    """
    Score every posting, drop non-internships, best first.

    Recomputed from scratch every run, so editing config.py and re-running is
    all it takes to change the rankings.
    """
    volumes = company_volumes(postings)

    scored = []
    for posting in postings:
        if not is_internship(posting):
            continue
        result = score_posting(posting, volumes=volumes)
        posting.preference = result["preference"]
        posting.preference_reasons = result["preference_reasons"]
        posting.candidacy_score = result["candidacy"]
        posting.candidacy_reasons = result["candidacy_reasons"]
        posting.role_family = result["role_family"]
        posting.company_tier = result["company_tier"]
        posting.fit_score = result["score"]
        scored.append(posting)

    scored.sort(
        key=lambda p: (-p.fit_score, p.company.lower(), p.role.lower())
    )
    return scored


def fit_label(score_value: int) -> str:
    """Turn a score into a badge name for the dashboard."""
    if score_value >= config.STRONG_FIT_THRESHOLD:
        return "strong"
    if score_value >= config.GOOD_FIT_THRESHOLD:
        return "good"
    if score_value >= config.LOW_FIT_THRESHOLD:
        return "fair"
    return "low"
