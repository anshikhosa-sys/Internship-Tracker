"""
==============================================================================
 CONFIG — THIS IS THE FILE YOU EDIT
==============================================================================

Everything that decides how a posting gets ranked lives in this file. No
scoring numbers are hidden anywhere else in the codebase. If a role is ranked
too high or too low, the fix is here.

HOW SCORING WORKS (read this once, then the rest is self-explanatory):

    Every posting starts at 0 points and accumulates:

      1. ROLE TIER      — the big one. Which of your target roles is this?
                          Only the BEST-matching tier counts (see note below).
      2. CATEGORY       — which section of the repo it was listed under.
      3. FOCUS BONUSES  — topic keywords you care about (AI, infra, data...).
      4. OUT OF SCOPE   — fields this search isn't covering (quant,
                          hardware...), so they sort to the bottom.

    Higher total = better fit = higher in the dashboard.

WHY ONLY THE BEST TIER COUNTS:
    A posting titled "Solutions Engineer Intern" contains the word "engineer",
    so it would also match the generic Software Engineer tier. If tiers
    stacked, generic SWE roles with padded titles would outrank a true
    top-priority match. So we take the single highest tier a posting matches
    and ignore the rest. Focus bonuses DO stack — those are additive by design.

TO TUNE IT:
    - Role ranked too low?   Add a keyword to its tier, or raise that tier's
                             `points`.
    - Irrelevant roles high? Add a keyword to OUT_OF_SCOPE_KEYWORDS.
    - Want a topic to matter more?  Raise its number in FOCUS_BONUSES.

    Then re-run:  python3 refresh.py
    Scores are recomputed from scratch every run, so edits take effect
    immediately. You never need to delete the database.
"""

# =============================================================================
# 1. WHICH SOURCE TO PULL FROM
# =============================================================================

# The raw README URL. GitHub serves raw markdown from raw.githubusercontent.
# 'dev' is the branch this repo actively updates.
SOURCE_URL = (
    "https://raw.githubusercontent.com/"
    "SimplifyJobs/Summer2027-Internships/dev/README.md"
)

SOURCE_NAME = "Summer2027-Internships"

# Which category sections to actually ingest.
#
# The repo has five sections. These are matched loosely (case-insensitive
# substring), so "Product Management" matches the real heading
# "📱 Product Management Internship Roles".
#
# Sections deliberately left out: "Quantitative Finance" and "Hardware
# Engineering" — different career tracks from the ones this search targets.
# To pull them in anyway, add the string to this list and re-run. Nothing
# else needs to change.
INGEST_CATEGORIES = [
    "Software Engineering",
    "Product Management",
    "Data Science, AI & Machine Learning",
]


# =============================================================================
# 2. ROLE TIERS — the heaviest weight in the model
# =============================================================================
#
# Each tier is checked against the ROLE TITLE. A posting can match several
# tiers ("Solutions Engineer" contains the word "engineer", so it matches both
# the Solutions tier and the Software Engineering tier) — when that happens the
# HIGHEST-VALUE matching tier wins and the others are ignored.
#
# That means the `points` value decides priority, not the position in this
# list. You can reorder these freely without changing behavior; to change
# priority, change the number.
#
# The gaps between tiers are what actually produce the ranking, so keep them
# wide enough that a top-tier role with no bonuses still beats a bottom-tier
# role stacked with bonuses.
#
# The tiers below are a starting configuration, not a fixed part of the tool.
# Rewrite the names, keywords, and values to match whatever roles you're
# searching for — the scoring logic reads them generically.

ROLE_TIERS = [
    {
        "name": "Forward-Deployed / Solutions",
        # WHY 150 AND NOT 100 — the one value here with real reasoning behind
        # it, and a useful lesson in how weighted models go wrong:
        #
        # Roles in this tier are listed under Software Engineering, which
        # earns only the +10 category bonus, while the tier below it earns
        # +30. At 100 points, a bare top-tier match scored 110, but a
        # bonus-laden second-tier match could reach 155 — so the lower tier
        # systematically outranked the higher one. The tier gap said one
        # thing; the bonuses quietly overrode it.
        #
        # 150 guarantees the ordering instead of hoping for it: the WORST a
        # top-tier role can score (150 + 10) still beats the BEST the tier
        # below can score (85 + 30 + 40 = 155).
        #
        # Lower this toward 100 if you'd rather a heavily-matched second-tier
        # role sometimes place above a bare top-tier one.
        "points": 150,
        "keywords": [
            "forward deployed",
            "forward-deployed",
            "fde",
            "solutions engineer",
            "solutions architect",
            "solution engineer",
            "solution architect",
            "sales engineer",          # often the same job under another name
            "customer engineer",       # Google's term for it
            "implementation engineer",
            "deployment engineer",
            "field engineer",
        ],
    },
    {
        "name": "Technical PM / APM",
        "points": 85,
        "keywords": [
            "product manager",
            "product management",
            "associate product manager",
            "apm",
            "technical product",
            "product intern",
            "pm intern",
        ],
    },
    {
        "name": "Technical Consulting / Strategy",
        "points": 60,
        "keywords": [
            "technical consultant",
            "technology consultant",
            "consultant",
            "consulting",
            "technical strategy",
            "product strategy",
            "business technology",
            "technical program manager",
            "tpm",
        ],
    },
    {
        "name": "Software Engineering",
        "points": 40,
        # Deliberately the lowest tier, because the focus bonuses below are
        # what lift the specialized SWE roles (systems, infra, data, AI) above
        # the generic ones. A plain "Software Engineer Intern" scores 40; an
        # "Infrastructure Engineer Intern" scores 40 + bonuses. That's the
        # intended behavior — it's what prevents a wall of interchangeable SWE
        # listings from filling the top of the list.
        "keywords": [
            "software engineer",
            "software engineering",
            "swe",
            "developer",
            "programmer",
            "data engineer",
            "machine learning engineer",
            "ml engineer",
            "research engineer",
            "data scientist",
            "backend",
            "full stack",
            "fullstack",
        ],
    },
]


# =============================================================================
# 3. CATEGORY BONUS — which section of the repo it came from
# =============================================================================
#
# The section a posting is filed under is itself a signal, independent of what
# the title says. Matched as a case-insensitive substring against the
# section heading.

CATEGORY_BONUS = {
    "Product Management": 30,       # small pool, weighted up to surface it
    "Data Science, AI & Machine Learning": 15,
    "Software Engineering": 10,
}


# =============================================================================
# 4. FOCUS BONUSES — topics you want, these STACK
# =============================================================================
#
# Checked against role title + category together. Each keyword scores at most
# once, but different keywords add up. Grouped only for readability — the
# groups have no special meaning, it's one flat pool of keyword -> points.

FOCUS_BONUSES = {
    # --- AI / ML ---
    "ai": 12,
    "artificial intelligence": 12,
    "ml": 12,
    "machine learning": 12,
    "llm": 15,
    "genai": 15,
    "generative": 12,
    "nlp": 10,
    "deep learning": 10,

    # --- Infrastructure / systems ---
    "infrastructure": 12,
    "infra": 12,
    "systems": 10,
    "platform": 10,
    "distributed": 10,
    "cloud": 8,
    "backend": 6,
    "devops": 8,
    "site reliability": 8,
    "sre": 8,

    # --- Data ---
    "data": 8,
    "data platform": 12,
    "analytics": 6,
    "database": 8,

    # --- Customer-facing / technical-but-people-facing ---
    "customer": 12,
    "client": 10,
    "field": 8,
    "partner": 6,
    "technical": 6,
    "solutions": 10,
}

# Ceiling on the TOTAL focus bonus a single posting can collect.
#
# Why this exists: the keywords above overlap on purpose ("ai" and "artificial
# intelligence", "ml" and "machine learning", "data" and "data platform").
# Without a cap, a title like "AI/ML Data Platform Infrastructure Intern"
# collects six overlapping bonuses and outranks a genuine Forward-Deployed
# role. The cap keeps bonuses as a tie-breaker between similar roles rather
# than something that can overturn the role tiers.
#
# Raise it if you want topic keywords to matter more; set it very high to
# effectively disable the cap.
MAX_FOCUS_BONUS = 40


# =============================================================================
# 5. OUT OF SCOPE — fields this particular search isn't covering
# =============================================================================
#
# The source repo mixes several career tracks into the same listings. These
# keywords mark the ones outside the scope of THIS search, so they sort to the
# bottom instead of crowding out the roles being looked for.
#
# To be clear about what this is: a relevance filter for one person's job
# search, not a judgment about the work. Quantitative finance, chip design, and
# marketing are all excellent careers — they're simply not the ones this tool
# is pointed at. Someone searching for those roles would flip these numbers
# positive and move the software keywords down here instead.
#
# The values are negative so matches sort downward. They stack, and they're
# matched against the role title.

OUT_OF_SCOPE_KEYWORDS = {
    # --- Quantitative finance: a separate track with its own pipeline ---
    "quantitative": -40,
    "quant": -40,
    "trading": -35,
    "trader": -40,
    "hedge fund": -30,

    # --- Hardware, embedded, and other engineering disciplines ---
    "hardware": -30,
    "embedded": -25,
    "fpga": -35,
    "asic": -35,
    "verilog": -35,
    "rtl": -30,
    "silicon": -30,
    "chip": -25,
    "analog": -30,
    "circuit": -30,
    "mechanical": -35,
    "electrical": -30,

    # --- Non-engineering roles that appear in these tech listings ---
    "recruiting": -30,
    "marketing": -25,
    "sales development": -25,
    "accounting": -35,
}

# Advanced-degree adjustment.
#
# The repo marks roles requiring a Master's/PhD/MBA with a 🎓 emoji. Those
# are outside the scope of this search, so they sort down rather than being
# hidden entirely. SET THIS TO 0 to rank them normally.
ADVANCED_DEGREE_PENALTY = -15

# The repo marks FAANG+ companies with 🔥. No score effect by default — it's
# displayed in the dashboard as a badge. Raise this if brand matters to you.
FAANG_BONUS = 0


# =============================================================================
# 6. THE INTERNSHIP GATE
# =============================================================================
#
# Your spec says internship-level only. The source repo is internship-only, so
# this is a safety net rather than a real filter — it matters more later when
# you add New-Grad-Positions as a second source.
#
# A posting must contain at least one of these in its title to be kept.
# Set REQUIRE_INTERNSHIP = False to disable the gate entirely.

REQUIRE_INTERNSHIP = True

INTERNSHIP_KEYWORDS = [
    "intern",
    "internship",
    "co-op",
    "coop",
    "summer",
    "student",
]


# =============================================================================
# 7. DISPLAY SETTINGS — cosmetic only, no effect on ranking
# =============================================================================

# Score at or above this gets a "STRONG FIT" badge in the dashboard.
STRONG_FIT_THRESHOLD = 100

# Score at or above this gets a "GOOD FIT" badge.
GOOD_FIT_THRESHOLD = 60

# Postings scoring below this are hidden behind the "show low-fit" toggle.
# They're still stored and scored — just collapsed by default.
LOW_FIT_THRESHOLD = 20

# Where the local database file lives (created automatically on first run).
DATABASE_PATH = "internships.db"
