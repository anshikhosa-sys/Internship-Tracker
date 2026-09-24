"""
Numbers that turn a parsed résumé and stated preferences into a derived
profile. They define HOW evidence is weighed, identically for every user;
what the evidence is comes from each user's résumé.
"""

PROFILES_DIR = "profiles"
PROFILE_SCHEMA_VERSION = 1
USER_ID_PATTERN = r"^[a-z0-9][a-z0-9_-]{0,39}$"

# ---- Skills -----------------------------------------------------------------
# Where a skill was seen sets how strongly it counts. A skill used in a job
# bullet is demonstrated; one only listed in a skills section is claimed.
SKILL_EVIDENCE_WEIGHT = {
    "experience": 1.0,
    "project": 0.85,
    "skills_section": 0.7,
    "education": 0.5,
}
# Experience fades: a skill last used N years ago counts less, never below the
# floor, since a skill does not vanish.
SKILL_RECENCY_HALF_LIFE_YEARS = 3.0
SKILL_RECENCY_FLOOR = 0.5

# ---- Seniority --------------------------------------------------------------
# An internship or part-time month is worth less than a full-time month toward
# seniority.
INTERNSHIP_MONTH_CREDIT = 0.5
PART_TIME_MONTH_CREDIT = 0.5
# Years of (credited) experience at which a non-student reaches each level.
SENIORITY_YEARS_THRESHOLDS = [
    (0.0, "entry"),
    (2.5, "mid"),
    (5.5, "senior"),
    (9.0, "staff"),
]
# A student within this many months of graduating is also eligible for
# entry-level (new grad) roles.
NEW_GRAD_WINDOW_MONTHS = 12

# ---- Role affinity ----------------------------------------------------------
# Evidence per family accumulates from past titles (weighted by duration) and
# from topic hits in bullets and projects, then saturates:
#     evidence_affinity = 1 - exp(-evidence / AFFINITY_SATURATION)
AFFINITY_TITLE_WEIGHT_PER_YEAR = 1.2     # a year holding a title in the family
AFFINITY_TITLE_MIN_WEIGHT = 0.4          # even a short stint counts this much
AFFINITY_TOPIC_HIT_WEIGHT = 0.25         # each distinct evidence term found
AFFINITY_SATURATION = 1.0

# Affinity is evidence only. Stated target roles were once blended in at a 0.6
# share; on a real golden set that scored a family with 0.393 of evidence above
# one with 0.713, because the first had been typed into a form. Removed.
# Nobody's affinity for a family drops below this; the scorer multiplies, and a
# hard zero would hide roles the user never thought to name.
AFFINITY_FLOOR = 0.05

# ---- Preferences ------------------------------------------------------------
# Preferences no longer reach the scorer at all; they are filters over an
# already-objective ranking. A profile is complete with a résumé and nothing
# else, so an unmodified résumé from anyone is scored the same way.

# Bounds on free text from forms and CLI.
MAX_LIST_ITEMS = 25
MAX_ITEM_CHARS = 80
MAX_RESUME_BYTES = 5 * 1024 * 1024
MIN_RESUME_CHARS = 200
