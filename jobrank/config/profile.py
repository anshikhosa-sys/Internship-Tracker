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

# Stated target roles express desire; résumé evidence expresses track record.
# Blend: affinity = STATED_SHARE * stated + (1 - STATED_SHARE) * evidence.
# When the user states no targets, evidence alone decides.
STATED_TARGET_SHARE = 0.6
# A family the user did not name, when they did name others.
UNSTATED_FAMILY_STATED_VALUE = 0.15
# Nobody's affinity for a family drops below this; the scorer multiplies, and a
# hard zero would hide roles the user never thought to name.
AFFINITY_FLOOR = 0.05

# ---- Preferences --------------------------------------------------------------
# Users rate each scoring factor 1-5; the rating becomes that factor's exponent
# in the multiplicative score. 3 is neutral (the default when unstated).
PRIORITY_EXPONENTS = {1: 0.25, 2: 0.5, 3: 0.75, 4: 1.0, 5: 1.25}
DEFAULT_PRIORITY = 3

PRIORITY_FACTORS = ["skills", "seniority", "role", "preferences", "freshness", "semantic"]

REMOTE_CHOICES = ["any", "remote_only", "remote_or_hybrid", "onsite_ok"]
COMPANY_SIZE_CHOICES = ["startup", "mid", "large"]

# Bounds on free text from forms and CLI.
MAX_LIST_ITEMS = 25
MAX_ITEM_CHARS = 80
MAX_RESUME_BYTES = 5 * 1024 * 1024
MIN_RESUME_CHARS = 200
