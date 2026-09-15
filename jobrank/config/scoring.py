"""
The scoring model's shape, identical for every user.

    score = 100 × Π factorᵢ ^ (profile_weightᵢ × FACTOR_WEIGHT_SCALEᵢ)

Each factor is in [0, 1]. The per-user exponent comes from that user's stated
priorities (config/profile.py PRIORITY_EXPONENTS); the scale here lets the
platform turn a whole factor down for everyone — e.g. the semantic layer while
its calibration is being measured on the golden set.

Nothing in this file is a statement about what any one person wants.
"""

FACTORS = ["skills", "seniority", "role", "preferences", "freshness", "semantic"]

FACTOR_WEIGHT_SCALE = {
    "skills": 1.0,
    "seniority": 1.0,
    "role": 1.0,
    "preferences": 1.0,
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

# ---- Preference match ---------------------------------------------------------
# Sub-factors multiply. A hard exclusion is near zero so the multiplicative
# score sinks it rather than averaging it away.
INDUSTRY_EXCLUDED = 0.03
LOCATION_MISMATCH = 0.60
COMPANY_SIZE_MISMATCH = 0.70
SENIORITY_NOT_WANTED = 0.50
START_BEFORE_AVAILABLE = 0.30
# user remote preference -> posting work mode -> multiplier
REMOTE_MATRIX = {
    "any": {"remote": 1.0, "hybrid": 1.0, "onsite": 1.0},
    "onsite_ok": {"remote": 1.0, "hybrid": 1.0, "onsite": 1.0},
    "remote_or_hybrid": {"remote": 1.0, "hybrid": 1.0, "onsite": 0.45},
    "remote_only": {"remote": 1.0, "hybrid": 0.50, "onsite": 0.20},
}
LOCATION_ALIASES = {
    "nyc": ["new york", "ny", "brooklyn", "manhattan"],
    "new york": ["new york", "nyc", "ny"],
    "sf": ["san francisco"],
    "bay area": ["san francisco", "san jose", "palo alto", "mountain view", "sunnyvale", "menlo park",
                 "redwood city", "oakland", "cupertino", "santa clara", "south san francisco"],
    "la": ["los angeles"],
    "dc": ["washington, dc", "washington dc", "arlington", "reston"],
    "remote": ["remote"],
}
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
