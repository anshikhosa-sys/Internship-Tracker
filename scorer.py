"""
Scoring — turns a Posting into a fit score using the weights in config.py.

This file contains NO numbers and NO keywords. That's deliberate: every value
it uses is imported from config.py, so tuning the rankings never means editing
any logic. If you find yourself wanting to change a number here, it belongs in
config.py instead.

TWO SUBTLE DECISIONS WORTH UNDERSTANDING
----------------------------------------

1. WE MATCH ON WHOLE WORDS, NOT SUBSTRINGS.
   The obvious way to check a keyword is `if "ai" in title`. That's a bug:
   "ai" appears inside "training", "email", "chair", and "maintain". "ml"
   appears inside "html". A naive substring check would hand out AI bonus
   points to a role called "Email Platform Intern".

   So we build a regex with word boundaries (\b) for each keyword and match
   that instead. "AI" matches; "training" doesn't.

2. FOCUS BONUSES MATCH THE ROLE TITLE ONLY — NOT THE CATEGORY.
   This one took a moment to spot. The category "Data Science, AI & Machine
   Learning" literally contains the words "AI", "Machine Learning", and "Data".
   If focus bonuses were matched against the category text too, every single
   posting in that section would automatically collect ~32 bonus points just
   for existing there — on top of the category bonus it already gets. Generic
   data-science listings would then outrank the Product Management roles you
   actually prioritized.

   Categories get their points from CATEGORY_BONUS. Titles get theirs from
   FOCUS_BONUSES. No double-dipping.
"""

import re

import config


# =============================================================================
# Keyword matching helpers
# =============================================================================

# Regexes are compiled once and reused, rather than rebuilt for all 400+
# postings. This is a small cache: keyword -> compiled pattern.
_PATTERN_CACHE = {}


def _pattern_for(keyword: str):
    """Build (and cache) a whole-word regex for one keyword."""
    if keyword not in _PATTERN_CACHE:
        # re.escape handles keywords containing regex-special characters,
        # like the "+" in "c++" or the "-" in "forward-deployed".
        _PATTERN_CACHE[keyword] = re.compile(
            r"\b" + re.escape(keyword) + r"\b", re.IGNORECASE
        )
    return _PATTERN_CACHE[keyword]


def _matches(keyword: str, text: str) -> bool:
    """True if `keyword` appears in `text` as a whole word."""
    return bool(_pattern_for(keyword).search(text))


# =============================================================================
# The internship gate
# =============================================================================

def is_internship(posting) -> bool:
    """
    Does this posting look like an internship?

    The Summer 2027 repo is internship-only, so today this passes essentially
    everything. It earns its keep later, when you add New-Grad-Positions as a
    second source and suddenly need to tell the two apart.
    """
    if not config.REQUIRE_INTERNSHIP:
        return True

    text = posting.role
    return any(_matches(kw, text) for kw in config.INTERNSHIP_KEYWORDS)


# =============================================================================
# The scorer
# =============================================================================

def score(posting):
    """
    Compute a fit score for one posting.

    Returns (total_score, reasons) where `reasons` is a list of
    {"label": str, "points": int} dicts explaining every point awarded.

    Keeping the reasons is what makes the dashboard trustworthy: instead of an
    unexplained "97", you can see it was 85 for the PM title, 30 for the
    category, and -15 for requiring a Master's. When a ranking looks wrong,
    the reasons tell you exactly which config value to edit.
    """
    reasons = []
    total = 0

    title = posting.role
    category = posting.category

    # -- 1. Role tier -------------------------------------------------------
    # Find every tier this title matches, then keep only the best one.
    #
    # Why the best rather than the first: a title like "Solutions Engineer
    # Intern" contains the word "engineer" and so also matches the generic
    # Software Engineering tier. If tiers stacked, padded generic titles would
    # beat true top-priority matches. Taking the highest-value match keeps the
    # tiers behaving like priorities — and it stays correct even if you
    # reorder the tiers in config.py.
    best_tier = None
    for tier in config.ROLE_TIERS:
        if any(_matches(kw, title) for kw in tier["keywords"]):
            if best_tier is None or tier["points"] > best_tier["points"]:
                best_tier = tier

    if best_tier:
        total += best_tier["points"]
        reasons.append({
            "label": f"Role type: {best_tier['name']}",
            "points": best_tier["points"],
        })

    # -- 2. Category bonus --------------------------------------------------
    for cat_name, points in config.CATEGORY_BONUS.items():
        if cat_name.lower() in category.lower():
            total += points
            reasons.append({
                "label": f"Listed under {cat_name}",
                "points": points,
            })
            break   # a posting belongs to exactly one category

    # -- 3. Focus bonuses (title only — see module docstring) ---------------
    focus_total = 0
    focus_hits = []
    for keyword, points in config.FOCUS_BONUSES.items():
        if _matches(keyword, title):
            focus_total += points
            focus_hits.append(keyword)

    # Cap the focus total so a keyword-stuffed title can't run away with it.
    # Without this, "AI/ML Data Platform Infrastructure Intern" would collect
    # points for six overlapping keywords and outrank a genuine top-tier role.
    if focus_total > config.MAX_FOCUS_BONUS:
        focus_total = config.MAX_FOCUS_BONUS
        capped = f" (capped at {config.MAX_FOCUS_BONUS})"
    else:
        capped = ""

    if focus_hits:
        total += focus_total
        reasons.append({
            "label": f"Focus areas: {', '.join(focus_hits)}{capped}",
            "points": focus_total,
        })

    # -- 4. Out-of-scope fields ---------------------------------------------
    for keyword, points in config.OUT_OF_SCOPE_KEYWORDS.items():
        if _matches(keyword, title):
            total += points     # values are already negative in config
            reasons.append({
                "label": f"Outside this search's scope: {keyword}",
                "points": points,
            })

    # -- 5. Flag-based adjustments ------------------------------------------
    if posting.needs_advanced_degree and config.ADVANCED_DEGREE_PENALTY:
        total += config.ADVANCED_DEGREE_PENALTY
        reasons.append({
            "label": "Requires an advanced degree",
            "points": config.ADVANCED_DEGREE_PENALTY,
        })

    if posting.is_faang and config.FAANG_BONUS:
        total += config.FAANG_BONUS
        reasons.append({
            "label": "FAANG+ company",
            "points": config.FAANG_BONUS,
        })

    return total, reasons


def score_all(postings) -> list:
    """
    Score every posting, drop non-internships, and sort best-fit first.

    Scores are always recomputed from scratch — never read back from the
    database. That's what lets you edit config.py, re-run, and immediately see
    new rankings without clearing any stored state.
    """
    scored = []

    for posting in postings:
        if not is_internship(posting):
            continue
        posting.fit_score, posting.score_reasons = score(posting)
        scored.append(posting)

    # Sort by score descending. The secondary sort on company keeps the order
    # stable and predictable for postings that tie, instead of shuffling
    # between runs.
    scored.sort(
        key=lambda p: (-p.fit_score, p.company.lower(), p.role.lower())
    )
    return scored


def fit_label(score_value: int) -> str:
    """Turn a numeric score into a badge for the dashboard."""
    if score_value >= config.STRONG_FIT_THRESHOLD:
        return "strong"
    if score_value >= config.GOOD_FIT_THRESHOLD:
        return "good"
    if score_value >= config.LOW_FIT_THRESHOLD:
        return "fair"
    return "low"
