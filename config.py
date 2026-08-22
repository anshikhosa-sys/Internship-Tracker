"""
==============================================================================
 CONFIG — THIS IS THE FILE YOU EDIT
==============================================================================

Everything that decides how a posting gets ranked lives here. No scoring
numbers exist anywhere else in the codebase.

HOW SCORING WORKS
-----------------
A posting's score is three factors MULTIPLIED, each between 0 and 1:

    score = PREFERENCE × CANDIDACY × FRESHNESS × 100

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
        "name": "Forward-Deployed / Solutions",
        "preference": 1.00,
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
        "name": "Technical PM / APM",
        "preference": 0.92,
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
        # 0.50 is the FLOOR for a generic SWE role, not the ceiling. The
        # FOCUS_LIFT values below raise it toward 1.0 when the role is in an
        # area you actually want — infrastructure, data platform, developer
        # tools. A plain "Software Engineer Intern" stays at 0.50; a "Software
        # Engineer Intern, Data Platform" lands around 0.80.
        "preference": 0.50,
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
    "infrastructure": 0.18,
    "infra": 0.18,
    "platform": 0.15,
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
    "data platform": 0.20,
    "data infrastructure": 0.20,
    "data engineering": 0.16,
    "data pipeline": 0.18,
    "database": 0.12,
    "data": 0.08,

    # AI, applied rather than research — see CANDIDACY_BLOCKERS for why
    # research-heavy roles are treated differently
    "llm": 0.18,
    "genai": 0.16,
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

    "hardware": 0.20, "embedded": 0.25, "fpga": 0.10, "asic": 0.10,
    "verilog": 0.10, "rtl": 0.15, "silicon": 0.15, "analog": 0.15,
    "circuit": 0.15, "mechanical": 0.10, "electrical": 0.20,

    "recruiting": 0.15, "marketing": 0.25, "accounting": 0.10,
    "sales development": 0.25,
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
# 1.0. KEEP THIS IN SYNC WITH YOUR RÉSUMÉ — it's the part of this file that
# goes stale as you learn things.
CANDIDACY_EVIDENCE = {
    # Directly evidenced by shipped work
    "python": 0.15,
    "data pipeline": 0.20,
    "pipelines": 0.15,
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
DEFAULT_SORT = "score"


# =============================================================================
# 5. THE INTERNSHIP GATE
# =============================================================================

REQUIRE_INTERNSHIP = True

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


# =============================================================================
# 8. COVER LETTERS AND WORK EXPERIENCE
# =============================================================================

# Your résumé, in the form the generator reads. Gitignored. Start from
# profile_example.md.
PROFILE_PATH = "profile.md"

# Where generated drafts are written. Also gitignored.
LETTERS_DIR = "letters"

# YOU DON'T HAVE TO PAY FOR THIS. Every letter page offers a "copy this
# prompt" option that costs nothing — paste it into claude.ai, ChatGPT, or
# anything else. The settings below only affect the one-click button.
#
# MEASURED COST per letter (~1,700 input, ~2,300 output including thinking
# tokens, which bill as output):
#
#     claude-opus-5     effort=high     $0.066     $3.31 per 50
#     claude-opus-5     effort=low      $0.039     $1.93 per 50
#     claude-sonnet-5   effort=high     $0.026     $1.32 per 50
#     claude-haiku-4-5  effort=high     $0.013     $0.66 per 50
#     copy-paste                        free       free
LETTER_MODEL = "claude-opus-5"
LETTER_EFFORT = "high"
LETTER_MAX_TOKENS = 8000
LETTER_COST_ESTIMATE = "about 7¢"


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
