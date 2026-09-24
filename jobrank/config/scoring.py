"""
The scoring model's shape, identical for every user.

    score = 100 × Π factorᵢ ^ FACTOR_EXPONENTSᵢ

Each factor is in [0, 1]. Every factor answers a question about EVIDENCE — what
the résumé proves — or about the MARKET — what postings currently ask for and
how contested they are. No factor asks what the user says they want.

WHY THERE IS NO PREFERENCE FACTOR
---------------------------------
An earlier version scored a "preference match" and blended stated target roles
into role affinity at a 0.6 share. Measured on a real golden set, that inverted
the ranking against the résumé: a family with 0.713 of evidence (PyTorch, CUDA,
computer vision) scored 0.615 because it was not typed into a form, while a
family with 0.393 of evidence scored 0.757 because it was. The tool exists to
raise the odds of landing a job, and a stated wish does not change those odds.
Preferences now filter the view; they never move a score.

WHY THE EXPONENTS ARE GLOBAL, NOT PER USER
------------------------------------------
They used to come from each user's 1-5 priority ratings, which is a preference
by another name and has no evidence behind it. They are now fitted on the
golden set by coordinate search over evaluate.py's AUC, so the value of each
factor is measured rather than asserted. Changing one requires re-fitting and
re-running the gate.
"""

FACTORS = ["skills", "seniority", "role", "freshness", "semantic"]

# Exponents fitted by coordinate search on the golden set (see docs/design-notes.md).
# Below 1 compresses a factor toward 1 so it still moves the result but cannot
# dominate; above 1 sharpens it.
FACTOR_EXPONENTS = {
    "skills": 1.0,
    "seniority": 1.0,
    "role": 1.0,
    "freshness": 1.0,
    "semantic": 1.0,
}

# ---- Skill overlap ----------------------------------------------------------
# coverage = Σ user_weight(skill) / n, over the skills a posting names.
# value    = SKILLS_FLOOR + (1 - SKILLS_FLOOR) × coverage
# A posting that names no skills is NEUTRAL: missing data is not a mismatch.
SKILLS_NEUTRAL = 0.75
SKILLS_FLOOR = 0.35

# ---- Seniority fit ----------------------------------------------------------
# Distance between the posting's level and the nearest level the user is
# eligible for, in taxonomy.SENIORITY_LEVELS steps. "above" means the posting
# is more senior than the user (underqualified); "below" means overqualified.
SENIORITY_FIT = {
    0: 1.0,
    "above_1": 0.30,
    "above_2": 0.06,
    "below_1": 0.55,
    "below_2": 0.25,
}
SENIORITY_UNKNOWN = 0.80
# Years the posting requires beyond the user's credited years: each missing
# year multiplies by this (enrichment only; titles rarely state years).
SENIORITY_MISSING_YEAR_MULTIPLIER = 0.6
# A posting requiring a degree above the user's.
DEGREE_REQUIREMENT_UNMET = 0.25

# Sources that only list one level. Used when a title names no level.
SOURCE_DEFAULT_SENIORITY = {
    "Summer2027-Internships": "intern",
    "Vansh-Summer2027": "intern",
    "SpeedyApply-SWE": "intern",
    "SpeedyApply-AI": "intern",
    "Sndsh-Summer2027": "intern",
    "Chieler-Summer2027": "intern",
    "DereC4-Internships": "intern",
    "DreamWork-2027": "intern",
}

# ---- Role affinity ----------------------------------------------------------
# A title no role family recognizes.
ROLE_UNKNOWN = 0.30
# Source categories that name a family when the title alone does not.
CATEGORY_FAMILIES = {
    "software engineering": "software_engineering",
    "data science, ai & machine learning": "ml_engineering",
    "data science": "data_science",
    "ai/ml": "ml_engineering",
    "quantitative finance": "quant",
    "hardware engineering": "embedded_hardware",
    "product management": "product_management",
}

# ---- Posting facts (not preferences) ----------------------------------------
# These read a fact off the posting — is it remote, when does the term start.
# They are market description, used by jobrank/postings.py; nothing here scores.
REMOTE_MARKERS = ["remote", "work from home", "anywhere"]
HYBRID_MARKERS = ["hybrid"]
# Posting term from its title: "Summer 2027" -> 2027-06.
TERM_START_MONTH = {"winter": 1, "spring": 3, "summer": 6, "fall": 9, "autumn": 9}

# ---- Company size -------------------------------------------------------------
LARGE_MIN_POSTINGS = 40
MID_MIN_POSTINGS = 5

# ---- Freshness --------------------------------------------------------------
# value = max(FLOOR, 0.5 ^ (age_days / half_life)). Large employers fill roles
# fastest, so their postings go stale fastest.
FRESHNESS_HALF_LIFE_DAYS = {"large": 3.8, "mid": 4.5, "startup": 9.0}
FRESHNESS_FLOOR = 0.02
FRESHNESS_UNKNOWN_AGE = 0.45

# ---- Semantic similarity ------------------------------------------------------
# Raw cosine similarity is rescaled per embedding model, because each model has
# its own baseline: unrelated text scores ~0.5 with bge-small, ~0.0 with hashed
# token vectors. value = clamp((cos - floor) / (ceil - floor), MIN, 1).
SEMANTIC_CALIBRATION = {
    "BAAI/bge-small-en-v1.5": {"floor": 0.55, "ceil": 0.82},
    "hashing-v1": {"floor": 0.0, "ceil": 0.45},
}
SEMANTIC_MIN = 0.20

# ---- Display --------------------------------------------------------------------
STRONG_FIT_THRESHOLD = 42
GOOD_FIT_THRESHOLD = 28
LOW_FIT_THRESHOLD = 14
