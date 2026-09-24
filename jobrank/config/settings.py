"""
Runtime settings: storage paths, dashboard defaults, notifications, the
refresh schedule, and letter-writing guidance. General to every user; nothing
here scores anything.
"""

from jobrank.config.scoring import GOOD_FIT_THRESHOLD

# =============================================================================
# Sources
# =============================================================================

SOURCE_URL = (
    "https://raw.githubusercontent.com/"
    "SimplifyJobs/Summer2027-Internships/dev/README.md"
)


SOURCE_NAME = "Summer2027-Internships"


# Anything above this is almost certainly a salary or a typo, not an
# hourly rate — ignore rather than let it dominate.
MAX_PLAUSIBLE_HOURLY = 400


# =============================================================================
# Dashboard
# =============================================================================

# HARD CUTOFF: postings older than this are hidden by default.
#
# The one setting that removes things from view rather than ranking them
# lower. Its cost is uneven: rare role types are posted infrequently, so a
# tight window can hide an entire category that happens to have nothing
# recent. That is why the dashboard always offers "show them" — the postings
# still exist, they're just out of the way until asked for.
MAX_AGE_DAYS = 7


# Postings at or under this age get a "fresh" badge.
FRESH_DAYS = 3


# Default dashboard sort: "score" (every factor combined), "role", "skills",
# "recency" or "pay".
#
# These defaults apply on a bare page load. The moment the filter form is
# touched they stop applying — including unticking a box that defaults to on,
# which needs the hidden `f=1` marker to tell "unticked" from "never
# submitted". See _filtered() in web/app.py.
#
# Score, not a single factor. An earlier version opened on one factor alone,
# which put a 26 at the top of the page and left a 65 below the fold. The
# landing view opens on the number the product computes.
DEFAULT_SORT = "score"


# Only show postings at most this many days old on a bare page load.
# 0 = today only. None = fall back to MAX_AGE_DAYS.
#
# 3, not 0: a session-sized list. Urgency isn't lost by widening it —
# size-aware freshness already ranks a same-day large-company role above a
# 3-day-old one. The window decides how much is on the list; the ranking
# decides what comes first.
DEFAULT_WITHIN_DAYS = 3


# The age windows offered in the dashboard's dropdown.
#
# A "today only" option was REMOVED and then RESTORED, and the reason is
# worth keeping. With the original five sources it could never match: the
# freshest age any of them published was 1 day, because those lists are
# bot-generated on a lag. The option returned an empty page every time.
#
# Adding Chieler and DereC4 changed the fact on the ground — both carry
# same-day rows, and there are now real postings at age 0. So the window is
# back.
#
# The durable lesson is not "never offer a today filter". It is that a
# filter must not be able to look broken: every option now shows the number
# it would return ("Today (17)"), so an empty one is visibly empty before
# you pick it, and an empty result explains itself and links to the nearest
# window that isn't. That property holds whatever the sources do next.
#
# (days, label). None = no age limit beyond MAX_AGE_DAYS.
AGE_WINDOWS = [
    (None, "Any age (within cutoff)"),
    (0, "Today"),
    (1, "Today or yesterday"),
    (3, "Last 3 days"),
    (7, "Last 7 days"),
]


# =============================================================================
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


# Co-ops are full-time during a school term. Off by default: whether someone
# can take a term off is a fact about them, and a platform default should not
# decide it.


# Words that mean "full-time during a school term", i.e. you take a semester
# off rather than working over the summer.
#
# These only BADGE a posting, they don't hide it — plenty of listings say
# "Intern/Co-op" and are perfectly normal summer internships. The
# dashboard's term filter separates them.
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


# Off by default for the same reason; a user's earliest start date
# (preferences) already sinks roles that begin too soon.


# =============================================================================
# Storage
# =============================================================================

DATABASE_PATH = "internships.db"


# YOUR applications live in a SEPARATE FILE, deliberately.
#
# internships.db is rebuildable — delete it any time and refresh.py restores
# it in under a second. This file is not: nothing can regenerate which roles
# you applied to.
#
# An earlier version kept both in one file, in separate tables. That protects
# against a careless UPDATE but not against `rm`, and deleting the database
# to rebuild the schema destroyed real records more than once. Separate files
# make that impossible.
APPLICATIONS_PATH = "applications.db"


# A plain-text mirror, rewritten after every change. Readable in any editor,
# so even losing both databases leaves something to restore from by hand.
APPLICATIONS_EXPORT = "applications.json"


# =============================================================================
# Automation
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

# The profile the dashboard, refresh report and notifications use when none is
# named. Overridden by the dashboard's profile switcher (stored in app_state).
DEFAULT_PROFILE_ID = "default"


# Where the dashboard lives, used to make a phone notification tappable.
DASHBOARD_URL = "http://127.0.0.1:5000"


# Liveness probe. A SEPARATE endpoint from the dashboard on purpose: fetching
# "/" registers a visit, which is what decides your NEW badges. Health checks
# were fetching "/" and silently clearing badges you had never seen — the
# check was destroying what it was meant to report on.
DASHBOARD_HEALTH_URL = "http://127.0.0.1:5000/healthz"


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
# Catch-up refresh: if the data is older than this when you open the
# dashboard, refresh before rendering.
#
# WHY: launchd reliably fires a missed run when the Mac WAKES from sleep,
# but a machine that was powered OFF through a scheduled slot is less
# certain, and a laptop shut for a weekend can miss several. Rather than
# depend on that, the dashboard checks the data's age itself. Opening the
# page is the moment you actually need it to be current.
#
# Costs under a second, and only triggers when the data is genuinely stale.
# Three scheduled runs a day is one every ~5 waking hours, and launchd
# reliably lags behind them. 3 hours means opening the dashboard nearly
# always shows data fetched since you last looked, at a cost of under a
# second. Big-tech postings lose a third of their freshness in a day, so
# erring tight is the right side to err on.
STALE_DATA_HOURS = 3


REFRESH_TIMES = [
    (7, 30),    # before class: yesterday afternoon's postings
    (11, 30),   # the posting window — the important one
    (16, 30),   # afternoon sweep
]


# =============================================================================
# Application prompts
# =============================================================================

# Where generated drafts are written. Gitignored.
LETTERS_DIR = "letters"

# NOTHING HERE COSTS MONEY. Prompts are assembled locally and pasted into
# whatever assistant the user already has. tests/test_llm.py asserts no paid
# API client exists anywhere in the package.

# A cover letter for a forward-deployed role and one for a backend role are not
# the same document, even from the same résumé. A forward-deployed reviewer
# scans for evidence of working with customers through ambiguity; a SWE
# reviewer wants depth on the hardest thing shipped. The same project is the
# lead story for one and a footnote for the other.
#
# Keys are taxonomy role families. GUIDANCE_FOR_FAMILY maps families that
# share a pitch onto one entry.
ROLE_FAMILY_GUIDANCE = {

    "forward_deployed": {
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

    "product_management": {
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

    "software_engineering": {
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

    "consulting": {
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


GUIDANCE_FOR_FAMILY = {
    "backend": "software_engineering",
    "frontend": "software_engineering",
    "fullstack": "software_engineering",
    "mobile": "software_engineering",
    "infrastructure": "software_engineering",
    "ai_engineering": "software_engineering",
    "ml_engineering": "software_engineering",
    "data_engineering": "software_engineering",
    "security": "software_engineering",
    "embedded_hardware": "software_engineering",
    "qa_test": "software_engineering",
    "research": "software_engineering",
    "data_science": "consulting",
    "quant": "software_engineering",
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
