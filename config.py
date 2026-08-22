"""
==============================================================================
 CONFIG — THIS IS THE FILE YOU EDIT
==============================================================================

Everything that decides how a posting gets ranked lives here. No scoring
numbers exist anywhere else in the codebase.

HOW SCORING WORKS
-----------------
A posting's score is three factors combined, each between 0 and 1:

    score = preference^0.35  ×  candidacy^1.0  ×  freshness^0.75  × 100

The exponents are the weights, and they are the most important numbers in
this file. An exponent below 1 COMPRESSES a factor toward 1, making it matter
less. Preference at 0.35 means the gap between a role you love and one you
merely like shrinks from 0.50-1.00 down to about 0.78-1.00 — present, but no
longer able to decide the ranking on its own.

That is deliberate. The goal is roles you can actually GET, not a ranked list
of things you'd enjoy. Candidacy carries full weight; wanting it is a
tie-breaker.

    PREFERENCE   Do you want this role?
                 From the role family and the topics involved.

    CANDIDACY    Would they realistically interview you?
                 Grounded in what your résumé actually proves, and knocked
                 down hard by things that disqualify you.

    FRESHNESS    Is it still open?
                 Internship hiring is rolling; postings fill and close.

WHY MULTIPLY INSTEAD OF ADD
---------------------------
This is the important idea, and the first version of this tool got it wrong.

The original model ADDED a big number for wanting a role — +150 for anything
titled "forward deployed". That made preference swamp everything else, so a
month-old role at a company that takes a handful of interns outranked a fresh
posting that matched the résumé exactly. The list looked impressive and was
useless: the top of it was full of things that couldn't be acted on.

Multiplying fixes that, because it encodes the actual requirement: an
application is only worth making if ALL THREE are true. You want it, AND you
could plausibly get it, AND it's still open. A zero anywhere should sink the
whole thing, and with multiplication it does.

    Dream role, stale, long odds:   1.00 × 0.35 × 0.30  =  10
    Good match, fresh, credible:    0.75 × 0.85 × 1.00  =  64

The second one is what you should do today. The first is still visible — it's
never hidden — but it stops crowding out things you can act on.

TO TUNE IT
----------
  - Want preference to matter more? Raise SCORE_WEIGHTS["preference"]
    toward 1.0. At 1.0 all three factors count equally again.
  - A role type ranked too low?    Raise its number in ROLE_FAMILIES.
  - Irrelevant roles too high?     Add keywords to OUT_OF_SCOPE.
  - Not getting credit for a skill you have? Add it to CANDIDACY_EVIDENCE.
  - Old postings sinking too fast? Raise the values in FRESHNESS_CURVE.

  Then run:  python3 refresh.py
  Scores are recomputed from scratch every run, so edits take effect at once.
"""

# =============================================================================
# 1. WHICH SOURCE TO PULL FROM
# =============================================================================

SOURCE_URL = (
    "https://raw.githubusercontent.com/"
    "SimplifyJobs/Summer2027-Internships/dev/README.md"
)

SOURCE_NAME = "Summer2027-Internships"

# Which category sections to ingest. Matched case-insensitively as substrings,
# so "Product Management" matches "📱 Product Management Internship Roles".
#
# Left out: "Quantitative Finance" and "Hardware Engineering" — different
# career tracks. Add them here to pull them in; nothing else needs changing.
INGEST_CATEGORIES = [
    "Software Engineering",
    "Product Management",
    "Data Science, AI & Machine Learning",
]


# =============================================================================
# 2. PREFERENCE — do you want this role?  (0.0 to 1.0)
# =============================================================================
#
# `preference` is the base value for a role family. A posting matching several
# families takes the HIGHEST — "Solutions Engineer" contains "engineer", so it
# matches both Solutions and generic SWE, and should be treated as the former.
#
# These are fractions of an ideal role, not points. 1.0 means "exactly what
# I'm looking for". 0.5 means "half as interesting".

ROLE_FAMILIES = [
    {
        # THE THESIS ROLE.
        #
        # This is the bet the résumé is built around: that AI/ML
        # infrastructure — the data and serving layer beneath models rather
        # than the modelling itself — is where the work grows fastest.
        #
        # It sits at the top because preference and candidacy AGREE here,
        # which is rare. It's the work most wanted AND the work the résumé
        # most directly proves: pipelines feeding ML models, scaled image
        # ingestion, a developer platform, a batch microservice on AWS.
        #
        # Note what's deliberately absent: "research", "scientist",
        # "modeling". Those are a different job that wants publications and
        # usually a PhD, and they're in CANDIDACY_BLOCKERS.
        "name": "AI/ML Infrastructure & Platform",
        "preference": 0.95,
        "keywords": [
            "ml infrastructure", "ml platform", "ml systems",
            "machine learning infrastructure", "machine learning platform",
            "ai infrastructure", "ai platform", "ai systems",
            "mlops", "ml ops", "model serving", "inference",
            "training infrastructure", "ml engineer", "ai engineer",
            "applied ai", "llm infrastructure", "llm platform",
            "data platform", "data infrastructure", "data engineering",
            "data engineer", "feature store", "vector database",
            "ml tooling", "ai tooling", "genai platform",
        ],
    },
    {
        "name": "Forward-Deployed / Solutions",
        "preference": 0.95,
        "keywords": [
            "forward deployed", "forward-deployed", "fde",
            "solutions engineer", "solutions architect",
            "solution engineer", "solution architect",
            "sales engineer", "customer engineer",
            "implementation engineer", "deployment engineer",
            "field engineer", "solutions consultant",
        ],
    },
    {
        # Still high — a technical PM role at an AI/infra company is squarely
        # on thesis. Slightly below the engineering families because the
        # résumé proves less of it (see CANDIDACY_CATEGORY).
        "name": "Technical PM / APM",
        "preference": 0.85,
        "keywords": [
            "product manager", "product management",
            "associate product manager", "apm", "technical product",
            "product intern", "pm intern",
        ],
    },
    {
        "name": "Technical Consulting / Strategy",
        "preference": 0.72,
        "keywords": [
            "technical consultant", "technology consultant",
            "consultant", "consulting", "technical strategy",
            "product strategy", "business technology",
            "technical program manager", "tpm",
        ],
    },
    {
        "name": "Software Engineering",
        # 0.62 is the FLOOR for a generic SWE role, not the ceiling.
        #
        # Raised from 0.50 deliberately: the goal is achievable roles, not a
        # narrow bet on one specialty. A plain software engineering
        # internship is a good outcome, and the FOCUS_LIFT values below
        # still push the infra/data/AI ones above it. The
        # FOCUS_LIFT values below raise it toward 1.0 when the role is in an
        # area you actually want — infrastructure, data platform, developer
        # tools. A plain "Software Engineer Intern" stays at 0.50; a "Software
        # Engineer Intern, Data Platform" lands around 0.80.
        "preference": 0.62,
        "keywords": [
            "software engineer", "software engineering", "swe",
            "developer", "programmer", "data engineer",
            "machine learning engineer", "ml engineer",
            "research engineer", "data scientist",
            "backend", "full stack", "fullstack", "infrastructure engineer",
            "platform engineer",
        ],
    },
]

# Preference for a posting that matched no family at all.
UNKNOWN_FAMILY_PREFERENCE = 0.30

# Topics that make a role MORE interesting, added to the family's preference
# and capped at 1.0. These are about what you want to work on — CANDIDACY
# below is the separate question of whether you're qualified for it.
FOCUS_LIFT = {
    # Infrastructure and platform — the strongest signal for your targets
    "infrastructure": 0.22,
    "infra": 0.22,
    "platform": 0.18,
    "developer experience": 0.22,
    "developer tools": 0.22,
    "internal tools": 0.18,
    "devex": 0.22,
    "systems": 0.12,
    "distributed": 0.12,
    "cloud": 0.10,
    "devops": 0.12,
    "sre": 0.12,
    "site reliability": 0.12,

    # Data
    "data platform": 0.25,
    "data infrastructure": 0.25,
    "data engineering": 0.20,
    "data pipeline": 0.22,
    "database": 0.12,
    "data": 0.08,

    # AI, applied rather than research — see CANDIDACY_BLOCKERS for why
    # research-heavy roles are treated differently
    "llm": 0.22,
    "genai": 0.20,
    "generative": 0.14,
    "ai": 0.10,
    "ml": 0.08,
    "machine learning": 0.08,
    "applied ai": 0.20,
    "ai engineer": 0.20,

    # Customer-facing — core to forward-deployed work
    "customer": 0.18,
    "client": 0.15,
    "field": 0.12,
    "solutions": 0.15,
    "technical": 0.06,
}

# Ceiling on total lift, so a keyword-stuffed title can't rocket a generic
# role past a genuinely top-tier one.
MAX_FOCUS_LIFT = 0.35

# Fields this search isn't covering. These MULTIPLY preference down rather
# than subtracting, so a quant role is worth a fraction of a real match no
# matter what else its title says.
#
# Not a judgment about the work — a relevance filter for one person's search.
OUT_OF_SCOPE = {
    "quantitative": 0.10, "quant": 0.10, "trading": 0.10,
    "trader": 0.10, "hedge fund": 0.15,

    # Trading-desk words that appear in otherwise ordinary-looking titles.
    "commodities": 0.20,
    "derivatives": 0.15,
    "market making": 0.10,
    "market maker": 0.10,
    "proprietary trading": 0.10,
    "execution algo": 0.10,
    "low latency": 0.30,
    "high frequency": 0.15,
    "portfolio": 0.35,
    "securities": 0.35,

    "hardware": 0.20, "embedded": 0.25, "fpga": 0.10, "asic": 0.10,
    "verilog": 0.10, "rtl": 0.15, "silicon": 0.15, "analog": 0.15,
    "circuit": 0.15, "mechanical": 0.10, "electrical": 0.20,

    "recruiting": 0.15, "accounting": 0.10, "sales development": 0.25,

    # Non-technical roles that read like tech roles until you look. A
    # "Product Marketing Intern" matches "product" and sails up the list
    # otherwise.
    "marketing": 0.12,
    "product marketing": 0.08,
    "brand": 0.10,
    "communications": 0.12,
    "public relations": 0.10,
    "human resources": 0.10,
    "talent": 0.15,
    "social media": 0.10,
    "content": 0.20,
    "audit": 0.15,
    "tax": 0.10,
    "actuarial": 0.15,
    "underwriting": 0.15,
    "supply chain": 0.25,
    "procurement": 0.15,
}


# Some employers are out of scope for reasons the TITLE never reveals.
# "Applied AI Engineer Intern" reads like a perfect match until you notice
# the company is a hedge fund and the work is signal research.
#
# These multiply preference down, same as the keyword list above.
#
# DELIBERATELY NARROW. Only pure quant and high-frequency trading shops are
# listed, because that's a genuinely different career track. Banks,
# consultancies, and insurers hiring ordinary software interns — Amex,
# Blackstone, Goldman, PwC — are NOT here: those are real engineering
# internships and you said you'd take a good general one.
OUT_OF_SCOPE_COMPANIES = {
    # Trading firms whose name is the only clue. "Software Engineer Intern,
    # Commodities" at DV Group reads like an ordinary SWE role from the title
    # alone — the work is C++ and market maths, a different track entirely.
    "dv group": 0.25,
    "dv trading": 0.25,
    "garda capital": 0.30,
    "belvedere trading": 0.30,
    "wolverine trading": 0.30,
    "peak6": 0.30,
    "tower research": 0.30,
    "xtx markets": 0.30,
    "quantlab": 0.30,
    "headlands": 0.30,
    "cutler group": 0.30,
    "group one trading": 0.30,
    "vatic labs": 0.30,
    "radix trading": 0.30,
    "gts": 0.35,
    "virtu": 0.35,
    "flow traders": 0.30,
    "millennium": 0.35,
    "citadel": 0.35,
    "jane street": 0.35,
    "two sigma": 0.35,
    "hudson river trading": 0.30,
    "de shaw": 0.35,
    "point72": 0.35,
    "jump trading": 0.30,
    "optiver": 0.30,
    "imc trading": 0.30,
    "susquehanna": 0.30,
    "sig": 0.30,
    "akuna": 0.30,
    "drw": 0.30,
    "five rings": 0.30,
    "old mission": 0.30,
}


# =============================================================================
# 3. CANDIDACY — would they realistically interview you?  (0.0 to 1.0)
# =============================================================================
#
# This is the half the original tool was missing entirely, and the reason it
# recommended roles you had little chance at.
#
# Everything here should be answerable with "my résumé proves this". If you
# can't point at a bullet, it doesn't belong in CANDIDACY_EVIDENCE.
#
# AN HONEST LIMITATION: the source gives a job TITLE, not a description. This
# reliably catches blunt cases ("PhD", "Senior") and rewards visible stack
# overlap. It cannot know a role wants three years of Kubernetes. It's a
# sorting aid, not a verdict — never let it stop you applying to something you
# want.

# Where an unremarkable role starts. Not 1.0: you're an undergrad with two
# internships, which is strong but not a guaranteed interview anywhere.
#
# HONEST LIMITATION, worth understanding: most job TITLES contain no skill
# keywords at all ("Software Engineer Intern"), so for roughly two-thirds of
# postings candidacy never moves off this baseline. It does real work at the
# extremes — blockers like "PhD" sink a posting, and a title naming your
# actual stack lifts one — but in the middle it's close to a constant.
#
# The fix isn't a cleverer number here: it's pasting the real job description
# on the application-prep page, where the full text is available. Treat this
# factor as a filter against obvious mismatches, not a precise estimate.
CANDIDACY_BASELINE = 0.60

# Things your résumé demonstrably proves. Added to the baseline, capped at
# CANDIDACY_MAX_EVIDENCE.
#
# THESE ARE WEIGHTS, NOT CLAIMS. A keyword here only earns points if the term
# ALSO appears in profile.md — see REQUIRE_EVIDENCE_IN_PROFILE below. That
# way the list can't quietly credit you for things your résumé doesn't say,
# and adding a skill to your résumé starts crediting it automatically.
CANDIDACY_EVIDENCE = {
    # Directly evidenced by shipped work
    "python": 0.15,
    "data pipeline": 0.24,
    "pipelines": 0.18,
    "ml infrastructure": 0.24,
    "ml platform": 0.24,
    "data platform": 0.24,
    "inference": 0.16,
    "model serving": 0.16,
    "etl": 0.15,
    "backend": 0.15,
    "sql": 0.14,
    "postgres": 0.14,
    "database": 0.12,
    "aws": 0.12,
    "docker": 0.12,
    "microservice": 0.15,
    "microservices": 0.15,
    "internal tools": 0.20,
    "developer experience": 0.20,
    "developer tools": 0.20,
    "platform": 0.12,
    "infrastructure": 0.12,

    # LLM/AI application work — Spotlight and the subagent code review
    "llm": 0.16,
    "genai": 0.14,
    "applied ai": 0.16,
    "ai engineer": 0.14,
    "prompt": 0.12,

    # Web stack
    "typescript": 0.12,
    "javascript": 0.10,
    "react": 0.12,
    "full stack": 0.10,
    "fullstack": 0.10,
    "node": 0.10,

    # ML — engineering side only. You've built pipelines that FEED models,
    # which is a different thing from training them. Weighted lower than the
    # data-engineering entries on purpose.
    "pytorch": 0.10,
    "machine learning": 0.06,
    "ml": 0.06,
}

CANDIDACY_MAX_EVIDENCE = 0.40

# Only count evidence keywords that actually appear in profile.md.
#
# WHY THIS EXISTS: an audit found 15 of 36 keywords above were hand-written
# from what the résumé seemed to imply — "ml infrastructure", "model
# serving", "developer experience" — none of which appear in it. Those were
# crediting candidacy for experience that couldn't be pointed at, which is
# the same mistake the cover letters are explicitly forbidden from making.
#
# Set to False to go back to trusting the list alone.
REQUIRE_EVIDENCE_IN_PROFILE = True

# Technologies and specialties that show up in job titles and that your
# résumé does NOT support. A title naming one is a signal you'd be competing
# against people who've done exactly it.
#
# These multiply candidacy down. They're separate from CANDIDACY_BLOCKERS
# because they're softer — a role wanting Go isn't closed to you, it's just
# a worse use of an application than one wanting Python.
#
# Anything listed here that LATER appears in profile.md stops counting
# automatically, same rule as the evidence list.
UNSUPPORTED_TECH = {
    "c++": 0.45,
    "rust": 0.50,
    "golang": 0.55,
    "scala": 0.55,
    "kubernetes": 0.65,
    "terraform": 0.70,
    "kafka": 0.70,
    "spark": 0.70,
    "hadoop": 0.65,
    "cuda": 0.40,
    "verilog": 0.30,
    "assembly": 0.40,
    "低": 0.60,          # placeholder-safe; harmless if never matched
    "kernel": 0.40,
    "firmware": 0.35,
    "unity": 0.45,
    "unreal": 0.45,
    "solidity": 0.45,
    "blockchain": 0.50,
}

# Things that make you a poor or ineligible candidate. These MULTIPLY, so a
# single hard blocker sinks the posting no matter how good the match looks.
#
# Tuned for an undergraduate targeting a summer internship between junior and
# senior year. If you start a master's, delete the degree entries.
CANDIDACY_BLOCKERS = {
    # Degree requirements — genuine disqualifiers
    "phd": 0.05, "doctoral": 0.05, "masters": 0.10,
    "master's": 0.10, "mba": 0.10, "graduate student": 0.15,

    # Not internships
    "new grad": 0.15, "senior": 0.10, "staff": 0.10,
    "principal": 0.10, "manager ii": 0.15,

    # Deep specializations your résumé doesn't evidence. Not zero — you could
    # still be considered — but honestly long odds against people who've done
    # exactly this.
    "research scientist": 0.20,
    "research intern": 0.35,
    "applied scientist": 0.25,
    "phd research": 0.10,
    "compiler": 0.35,
    "kernel": 0.30,
    "cryptography": 0.30,
    "robotics": 0.35,
    "computer vision": 0.45,
    "nlp research": 0.30,
    "formal verification": 0.25,
}

# The repo's 🎓 marker, applied on top of the keyword blockers above. It's set
# deliberately by maintainers, so it's more reliable than title text.
ADVANCED_DEGREE_MULTIPLIER = 0.15

# Highly competitive employers (the repo's 🔥 marker). Deliberately mild —
# long odds are not closed doors, and burying every good company would make
# this tool useless in a different way. Set to 1.0 to ignore entirely.
COMPETITIVE_EMPLOYER_MULTIPLIER = 0.85


# =============================================================================
# 3b. HOW THE THREE FACTORS ARE WEIGHTED
# =============================================================================
#
# Each factor is raised to its exponent before multiplying. Lower exponent =
# less influence. 1.0 = full influence. 0.0 = ignored entirely.
#
# The current setting says: rank by whether I can GET it, discounted by how
# stale it is, nudged by whether I want it.
SCORE_WEIGHTS = {
    "preference": 0.35,   # a nudge, not a decision
    "candidacy":  1.00,   # the thing being optimized
    "freshness":  0.75,   # still matters a lot; rolling hiring is real
}


# =============================================================================
# 3c. CANDIDACY SIGNALS BEYOND THE TITLE
# =============================================================================
#
# Candidacy has to actually VARY to be worth weighting heavily. Measured
# against real data, 54% of postings sat exactly at the baseline, because
# most job titles contain no skill keywords at all. Two extra signals fix
# that, and both come from data already in hand.

# 1. WHICH CATEGORY THE ROLE IS IN.
#
# Your résumé is two software engineering internships. That's direct evidence
# for a SWE role, adjacent evidence for data/ML (you've built pipelines that
# feed models, which is not the same as training them), and thin evidence for
# product management, where you have no prior title.
#
# This is the honest version of "would they take you" — a PM team reading a
# pure-SWE résumé sees a career switcher, however good the engineering is.
CANDIDACY_CATEGORY = {
    "Software Engineering": 0.15,
    "Data Science, AI & Machine Learning": 0.06,
    "Product Management": -0.12,
    "Uncategorized": 0.0,
}

# 2. HOW MANY ROLES THE COMPANY IS POSTING.
#
# A company listing 117 internships runs a large structured program and takes
# many interns. A company listing one is a lottery with a single ticket. This
# is real odds information, and it's derivable from the data we already have.
#
# It also happens to favour exactly the pipelines that work without a
# referral — big structured programs screen on an online assessment, whereas a
# one-role startup usually hires someone a founder already knows.
CANDIDACY_VOLUME_TIERS = [
    (50, 0.14),   # 50+ roles posted: hires at real scale
    (15, 0.10),
    (5,  0.05),
    (0,  0.00),
]

# =============================================================================
# 4. FRESHNESS — is it still open?  (0.0 to 1.0)
# =============================================================================
#
# WHAT THE EVIDENCE SAYS
# ----------------------
# The direction is well supported and matches how rolling internship hiring
# works: recruiters review top-of-funnel first, and postings close once a
# shortlist forms. Popular roles draw 100-250 applications in 24-48 hours.
#
# The widely quoted multipliers ("8x within 96 hours", "90% of interviews go
# to 24-hour applicants") come mostly from companies selling application-speed
# tools, and the most-cited study is from a firm that no longer exists. Treat
# the magnitudes as marketing and the direction as real.
#
# Recruiters are also consistent that older postings ARE still worth applying
# to — roles stay open for slow processes, and some companies must keep a
# listing up until an offer is signed. That's why the curve has a FLOOR and
# nothing is ever excluded by age.

# =============================================================================
# COMPANY TIER — how fast does THIS company's pipeline close?
# =============================================================================
#
# A role at a household-name company and one at a 30-person startup do not
# decay at the same rate, and treating them identically wastes applications
# at both ends: you miss big-tech roles by a day, and you skip startup roles
# that were still wide open.
#
# WHAT THE EVIDENCE SAYS
# ----------------------
# Startup postings draw roughly 100-400 applications over about two weeks.
# Big, brand-name entry-level postings hit similar or larger numbers inside
# the FIRST week, and big programs review on a rolling basis and close once
# a shortlist forms. Same total volume, very different clocks.
#
# So the practical rule: for big tech, apply the day it appears. For smaller
# and nicher companies, three to seven days is usually still fine.
#
# HOW A TIER IS DECIDED
# ---------------------
# From data already in hand, not a hand-maintained company list:
#   - the source's FAANG+/competitive marker, or
#   - how many roles the company is currently posting.
# A company listing 50+ roles is running a big structured program whatever
# its name recognition.

COMPANY_TIERS = {
    # Big, brand-name, high-volume. Fills fastest — apply same day.
    "big": {
        "label": "Big tech",
        "min_postings": 40,
        "curve": [
            (0, 1.00),
            (1, 0.62),    # already meaningfully behind by day one
            (2, 0.38),
            (3, 0.22),
            (5, 0.10),
            (7, 0.05),
            (14, 0.03),
            (30, 0.02),
            (999, 0.01),   # distinct tail steps keep the curve strictly
        ],               # decreasing, so "older" always means "lower"
    },
    # Mid-size: recognizable, structured hiring, but not a brand-name rush.
    "mid": {
        "label": "Mid-size",
        "min_postings": 5,
        "curve": [
            (0, 1.00),
            (1, 0.88),
            (3, 0.62),
            (5, 0.42),
            (7, 0.28),
            (10, 0.14),
            (21, 0.08),
            (30, 0.05),
            (999, 0.03),
        ],
    },
    # Small and niche. Slower, batchier review — a week can still be fine,
    # and these are also where a thoughtful application stands out most.
    "niche": {
        "label": "Small / niche",
        "min_postings": 0,
        "curve": [
            (0, 1.00),
            (1, 0.96),
            (3, 0.85),
            (5, 0.72),
            (7, 0.58),
            (14, 0.35),
            (21, 0.20),
            (30, 0.12),
            (999, 0.06),
        ],
    },
}

# Fallback curve, used when a posting's tier can't be worked out.
# (days old, multiplier) — first row whose threshold is >= the age wins.
#
# STEEP ON PURPOSE. Internship hiring is rolling: recruiters work top-of-funnel
# first and close a posting once a shortlist forms. A role posted today and the
# same role posted a week ago are not the same opportunity, and a gentle curve
# hides that.
#
# Where the evidence actually lands: the strongest study puts the prime window
# at about 96 hours, not 24. So day 3 is still genuinely worth applying to —
# it's inside the window — while day 10 mostly is not. The curve reflects that
# shape: near-flat for the first two days, falling off a cliff after four.
FRESHNESS_CURVE = [
    (0,  1.00),   # today — the reason to check every morning
    (1,  0.82),
    (2,  0.66),
    (3,  0.52),   # still inside the prime window, but clearly worse
    (5,  0.34),
    (7,  0.20),
    (10, 0.10),
    (14, 0.05),
    (999, 0.02),  # effectively dead, and filtered out by default anyway
]

# Unknown age is treated as middling rather than punished — a parsing failure
# on our side shouldn't cost a posting its rank.
UNKNOWN_AGE_FRESHNESS = 0.45

# HARD CUTOFF: postings older than this are hidden by default.
#
# This is the one setting that removes things from the tool rather than just
# ranking them lower, so it deserves a clear-eyed look.
#
# WHAT EACH VALUE COSTS YOU, measured against the current 408 postings:
#
#      cutoff    kept    PM roles    forward-deployed roles
#         3d      126           2                         0
#         7d      166           4                         0
#        14d      250          18                         0
#        30d      408          28                         2
#
# Note the last column. Both forward-deployed roles currently listed are 29
# days old, so ANY cutoff under 29 hides your highest-preference category
# completely. That's the real price of a tight window, and it's why the
# dashboard keeps a "Show stale" checkbox — the postings still exist, they're
# just out of the way until you ask.
#
# Raise this if the daily list feels too thin; lower it toward 3 if you only
# ever want same-week postings.
MAX_AGE_DAYS = 7

# Postings at or under this age get a "fresh" badge.
FRESH_DAYS = 3

# Default dashboard sort:
#   "score"       preference × candidacy × freshness  (what to do today)
#   "preference"  how much you want it, ignoring odds and age
#   "candidacy"   where you're strongest
#   "recency"     newest first
#
# WHAT THE DASHBOARD OPENS WITH
# -----------------------------
# These three apply on a bare page load. The moment you touch the filter
# form they stop applying and your choices are used instead — including
# unticking a box that defaults to on, which needs the hidden `f=1` marker
# to distinguish "unticked" from "never submitted". See _filtered() in app.py.
DEFAULT_SORT = "candidacy"

# Only show postings at most this many days old on a bare page load.
# 0 = today only. None = fall back to MAX_AGE_DAYS.
#
# Note this is tight: on a typical day it leaves about 5-7 roles. That's the
# point if you only apply same-day — but if the daily list feels too thin,
# 1 (today or yesterday) roughly triples it and is still well inside the
# window the evidence supports.
DEFAULT_WITHIN_DAYS = 0

# ================================================1=============================
# APPLICATION PIPELINE
# =============================================================================
#
# The stages an application moves through. Order matters — it's the order
# shown in the dropdown and used for sorting the tracker.
#
# `key` is stored in the database, so renaming one orphans existing records.
# Change `label` freely; change `key` only if you're prepared to migrate.
APPLICATION_STAGES = [
    {"key": "",          "label": "Not applied",     "active": False},
    {"key": "applied",   "label": "Applied",         "active": True},
    {"key": "oa",        "label": "Online assessment", "active": True},
    {"key": "interview", "label": "Interview",       "active": True},
    {"key": "offer",     "label": "Offer",           "active": False},
    {"key": "rejected",  "label": "Rejected",        "active": False},
    {"key": "ghosted",   "label": "No response",     "active": False},
]

# After this many days with no stage change, an application is "gone quiet".
# Not a failure — most never respond — but worth seeing, because it tells you
# whether your pipeline is actually moving or just accumulating.
STALE_APPLICATION_DAYS = 21


# Hide co-ops by default — full-time during a school term, so you'd take a
# semester off rather than working over the summer.
DEFAULT_HIDE_COOP = True


# =============================================================================
# 5. THE INTERNSHIP GATE
# =============================================================================

REQUIRE_INTERNSHIP = True

# Words that mean "full-time during a school term", i.e. you take a semester
# off rather than working over the summer.
#
# These only BADGE a posting, they don't hide it — plenty of listings say
# "Intern/Co-op" and are perfectly normal summer internships. Use the
# dashboard's co-op filter to hide them when you want a clean summer-only
# list.
COOP_KEYWORDS = ["co-op", "coop", "cooperative education"]

# Roles for a term OTHER than summer. A "Winter 2027" internship runs during
# the academic year, so taking it means not being in class — the same
# practical problem as a co-op, from a different direction.
#
# Only matched against the title, and only badges/filters — never scores.
# Whether you can take a term off is a fact about you, not about the role.
OFF_SEASON_KEYWORDS = [
    "winter", "fall", "autumn", "spring",
    "off-season", "off season", "january start",
]

# Hide off-season roles by default. You're looking for Summer 2027.
DEFAULT_HIDE_OFFSEASON = True

INTERNSHIP_KEYWORDS = [
    "intern", "internship", "co-op", "coop", "summer", "student",
]


# =============================================================================
# 6. DISPLAY — cosmetic only
# =============================================================================

# Score bands.
#
# CALIBRATED AGAINST THE REAL DISTRIBUTION, not picked as round numbers.
# Multiplying three fractions compresses the range — 0.8 x 0.7 x 0.9 is only
# 50 — so a "50" here is not a mediocre score, it's near the top of what
# actually exists. Measured across the current 404 postings, the best
# available role scores in the low 50s and the median is under 20.
#
# Read a score as "how good is this compared to what's realistically out
# there today", not as a percentage of some perfect job that isn't on the
# list. If you widen INGEST_CATEGORIES or your résumé grows, re-check these.
STRONG_FIT_THRESHOLD = 42
GOOD_FIT_THRESHOLD = 28
LOW_FIT_THRESHOLD = 14

DATABASE_PATH = "internships.db"


# =============================================================================
# 7. AUTOMATION
# =============================================================================

# How long a dashboard "visit" lasts, in minutes. Page loads within this
# window count as the same visit, so NEW badges don't vanish mid-browse.
VISIT_SESSION_MINUTES = 30

# Notify when a scheduled refresh finds new postings worth acting on.
NOTIFY_ON_STRONG_FIT = True

# Score a NEW posting must reach to be worth interrupting you for.
#
# Separate from STRONG_FIT_THRESHOLD on purpose: that decides badge colour,
# this decides whether to interrupt. Tying them together once left the feature
# silent for days while relevant roles arrived unannounced.
NOTIFY_THRESHOLD = GOOD_FIT_THRESHOLD

# Where the dashboard lives, used to make a phone notification tappable.
DASHBOARD_URL = "http://127.0.0.1:5000"

# PHONE NOTIFICATIONS (optional, free, no account).
#
# Install the "ntfy" app, subscribe to a topic, and put the same topic
# string here. See push.py for the full setup.
#
# PICK SOMETHING UNGUESSABLE. On ntfy's free tier, knowing the topic name IS
# the credential — anyone who guesses it receives your notifications and can
# send you fake ones. Only company names and job titles are ever sent, all
# of which are already public listings; nothing about you is transmitted.
#
# Leave empty to keep Mac-only notifications.
PUSH_TOPIC = ""

# WHEN THE REFRESH RUNS.
#
# Several times a day, not once. The research on posting times is consistent:
# listings typically go up late morning, and applications start arriving in
# volume a few hours later. A single 8am run therefore MISSES the entire
# posting window — a role posted at 11am wouldn't reach you until 8am
# tomorrow, roughly 21 hours late, which is most of the advantage gone.
#
# Three runs cost 0.74 seconds each and cover the day: early, just after the
# posting window opens, and late afternoon for anything that slipped out.
# A notification only fires when something NEW clears the threshold, so extra
# runs don't mean extra interruptions — most find nothing.
REFRESH_TIMES = [
    (7, 30),    # before class: yesterday afternoon's postings
    (11, 30),   # the posting window — the important one
    (16, 30),   # afternoon sweep
]


# =============================================================================
# 8. COVER LETTERS AND WORK EXPERIENCE
# =============================================================================

# Your résumé, in the form the generator reads. Gitignored. Start from
# profile_example.md.
PROFILE_PATH = "profile.md"

# Where generated drafts are written. Also gitignored.
LETTERS_DIR = "letters"

# NOTHING HERE COSTS MONEY. The prompts are assembled locally and
# copied into whatever assistant you already use. There is no API
# client installed, no key to set, and no request that could be
# billed. tests.py asserts letters.py never regains an API call.


# =============================================================================
# 9. WHAT EACH KIND OF ROLE WANTS TO READ
# =============================================================================
#
# A cover letter for a forward-deployed role and one for a backend SWE role
# are not the same document, even from the same person with the same résumé.
# An FDE reviewer is scanning for evidence you can sit with a customer and
# survive ambiguity; a SWE reviewer wants depth on the hardest thing you've
# shipped. The same Backstage project is the lead story for one and a footnote
# for the other.
#
# Keys MUST match the "name" values in ROLE_FAMILIES above.

ROLE_FAMILY_GUIDANCE = {

    "Forward-Deployed / Solutions": {
        "emphasis": (
            "Judged on whether you can work directly with customers and "
            "operate in ambiguity, not on algorithmic depth. Lead with "
            "evidence of building something other people had to adopt, "
            "translating between technical and non-technical audiences, and "
            "shipping into an environment you didn't fully control. Technical "
            "credibility is the floor, not the pitch — show you can deploy "
            "AND explain."
        ),
        "experience_framing": (
            "Frame each item around who you served and what problem of theirs "
            "you solved. Name the stakeholders, the ambiguity you worked "
            "through, and how adoption or usage changed. Technical detail "
            "supports the story rather than being the story."
        ),
    },

    "Technical PM / APM": {
        "emphasis": (
            "Judged on product judgment: why you built a thing, who it was "
            "for, what you chose not to do, and how you knew it worked. Lead "
            "with user impact and decisions rather than implementation. Where "
            "you influenced scope or reconciled competing views, say so — "
            "that's the job. Stay technical enough to be credible; this is a "
            "TECHNICAL PM role, not a generalist one."
        ),
        "experience_framing": (
            "Frame each item as: the user or problem, the decision you made, "
            "the tradeoff you accepted, the measurable outcome. Lead with the "
            "'why' and the result; compress the 'how'. Where you worked "
            "across people with different opinions, make it visible."
        ),
    },

    "Software Engineering": {
        "emphasis": (
            "Judged on technical depth. Pick the single hardest engineering "
            "problem in the profile that's relevant to this posting and go "
            "deep — the system, the failure mode, the specific fix, the "
            "measured result. Depth on one thing beats a tour of everything. "
            "Don't soften technical detail for readability; the reader is an "
            "engineer."
        ),
        "experience_framing": (
            "Frame each item around the technical problem and what you did: "
            "the system, the constraint, the approach, the measurable result. "
            "Keep the specifics — concrete details are the evidence. Name "
            "real technologies rather than categories."
        ),
    },

    "Technical Consulting / Strategy": {
        "emphasis": (
            "Judged on structured thinking and communication. Show you can "
            "take an ambiguous problem, break it down, and explain the "
            "reasoning to someone non-technical. Evidence of working across "
            "teams with competing priorities is worth more here than raw "
            "implementation depth."
        ),
        "experience_framing": (
            "Frame each item as problem → approach → outcome, with the "
            "reasoning visible. Emphasize working across groups, handling "
            "ambiguity, and communicating decisions. Quantify wherever the "
            "profile supports it."
        ),
    },
}

DEFAULT_FAMILY_GUIDANCE = {
    "emphasis": (
        "Lead with the experience in the profile that most directly matches "
        "this posting, and be concrete about what was built and what resulted."
    ),
    "experience_framing": (
        "Frame each item around the problem, what you did, and the measurable "
        "result."
    ),
}
