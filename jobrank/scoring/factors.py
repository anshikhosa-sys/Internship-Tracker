"""
Scoring factors. Each is a pure function of (posting facts, profile) returning
a value in [0, 1] and the reasons behind it. No constants live here — every
number comes from config/scoring.py, every user-specific value from the
derived Profile.

A factor with nothing to go on returns its configured NEUTRAL value and says
so. Missing data must never read as a mismatch: a posting that lists no skills
is not a posting that wants skills the user lacks.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from jobrank import postings, textmatch
from jobrank.config import scoring as cfg
from jobrank.config import taxonomy
from jobrank.models import Profile
from jobrank.resume.parser import canonical_skills


@dataclass
class FactorResult:
    name: str
    value: float
    reasons: list[str] = field(default_factory=list)
    neutral: bool = False
    detail: dict = field(default_factory=dict)


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


# ---------------------------------------------------------------------------
# 1. Skill overlap
# ---------------------------------------------------------------------------

def posting_skills(posting, enrichment: dict | None) -> list[str]:
    text = " ".join(filter(None, [postings.field(posting, "role"), postings.field(posting, "description")]))
    found = set(canonical_skills(text))
    if enrichment:
        found.update(enrichment.get("tech_stack") or [])
    return sorted(found)


def skills(posting, profile: Profile, enrichment: dict | None = None) -> FactorResult:
    wanted = posting_skills(posting, enrichment)
    if not wanted:
        return FactorResult("skills", cfg.SKILLS_NEUTRAL, ["Posting names no specific skills"], neutral=True)
    have = {s: profile.skills.get(s, 0.0) for s in wanted}
    coverage = sum(have.values()) / len(wanted)
    value = cfg.SKILLS_FLOOR + (1 - cfg.SKILLS_FLOOR) * coverage
    matched = [s for s, w in have.items() if w > 0]
    missing = [s for s, w in have.items() if w == 0]
    reasons = []
    if matched:
        reasons.append("Your résumé shows: " + ", ".join(f"{s} ({have[s]:.2f})" for s in matched))
    if missing:
        reasons.append("Not on your résumé: " + ", ".join(missing))
    return FactorResult("skills", _clamp(value), reasons, detail={"coverage": round(coverage, 3),
                                                                    "matched": matched, "missing": missing})


# ---------------------------------------------------------------------------
# 2. Seniority fit
# ---------------------------------------------------------------------------

def seniority(posting, profile: Profile, enrichment: dict | None = None) -> FactorResult:
    level, how = postings.seniority(posting, enrichment)
    reasons: list[str] = []
    if level is None:
        value = cfg.SENIORITY_UNKNOWN
        reasons.append("Posting does not state a level")
        neutral = True
    else:
        neutral = False
        levels = taxonomy.SENIORITY_LEVELS
        eligible = profile.seniority.eligible_levels or [profile.seniority.level]
        distances = [levels.index(level) - levels.index(e) for e in eligible]
        distance = min(distances, key=abs)
        if distance == 0:
            value = cfg.SENIORITY_FIT[0]
            reasons.append(f"{level} role; you are eligible for {', '.join(eligible)}")
        else:
            key = f"{'above' if distance > 0 else 'below'}_{min(abs(distance), 2)}"
            value = cfg.SENIORITY_FIT[key]
            direction = "more senior than" if distance > 0 else "more junior than"
            reasons.append(f"{level} role is {direction} your level ({profile.seniority.level})")
        if how != "title":
            reasons.append(f"Level from {how}")

    if enrichment:
        years = enrichment.get("required_years")
        gap = (years or 0) - profile.seniority.years_experience
        if years is not None and gap >= 1:
            multiplier = cfg.SENIORITY_MISSING_YEAR_MULTIPLIER ** int(gap)
            value *= multiplier
            reasons.append(f"Requires {years}+ years; you have {profile.seniority.years_experience:g} (×{multiplier:.2f})")
            neutral = False
        degree = enrichment.get("degree_required")
        if degree and degree != "none" and \
                taxonomy.DEGREE_ORDER.index(degree) > taxonomy.DEGREE_ORDER.index(profile.seniority.degree_level):
            value *= cfg.DEGREE_REQUIREMENT_UNMET
            reasons.append(f"Requires a {degree} degree (×{cfg.DEGREE_REQUIREMENT_UNMET:.2f})")
            neutral = False

    if postings.field(posting, "needs_advanced_degree") and \
            taxonomy.DEGREE_ORDER.index(profile.seniority.degree_level) < taxonomy.DEGREE_ORDER.index("master"):
        value *= cfg.DEGREE_REQUIREMENT_UNMET
        reasons.append(f"Source marks an advanced degree as required (×{cfg.DEGREE_REQUIREMENT_UNMET:.2f})")
        neutral = False
    return FactorResult("seniority", _clamp(value), reasons, neutral=neutral, detail={"posting_level": level})


# ---------------------------------------------------------------------------
# 3. Role affinity
# ---------------------------------------------------------------------------

def role(posting, profile: Profile) -> FactorResult:
    families = postings.role_families(posting)
    if not families:
        return FactorResult("role", cfg.ROLE_UNKNOWN, ["Role type not recognized from the title"], neutral=True,
                            detail={"families": []})
    best = max(families, key=lambda f: profile.role_affinity.get(f, 0.0))
    value = profile.role_affinity.get(best, cfg.ROLE_UNKNOWN)
    label = taxonomy.ROLE_FAMILIES[best]["label"]
    stated = "a stated target" if best in profile.target_families else "not a stated target"
    return FactorResult("role", _clamp(value), [f"{label}: your affinity {value:.2f} ({stated})"],
                        detail={"families": families, "family": best})


# ---------------------------------------------------------------------------
# 4. Preference match
# ---------------------------------------------------------------------------

def _location_matches(location: str, wanted: list[str]) -> bool:
    for place in wanted:
        variants = cfg.LOCATION_ALIASES.get(place.strip().lower(), [place])
        if textmatch.find_all(location, variants):
            return True
    return False


def preferences(posting, profile: Profile, enrichment: dict | None = None,
                volumes: dict | None = None) -> FactorResult:
    prefs = profile.preferences
    value = 1.0
    reasons: list[str] = []
    detail: dict = {}

    industry = postings.industry(posting)
    detail["industry"] = industry
    if industry and industry in prefs.exclude_industries:
        value *= cfg.INDUSTRY_EXCLUDED
        reasons.append(f"Industry you excluded: {industry} (×{cfg.INDUSTRY_EXCLUDED})")

    mode = postings.work_mode(posting, enrichment)
    detail["work_mode"] = mode
    if mode:
        multiplier = cfg.REMOTE_MATRIX.get(prefs.remote, cfg.REMOTE_MATRIX["any"]).get(mode, 1.0)
        if multiplier < 1.0:
            value *= multiplier
            reasons.append(f"{mode.capitalize()} role; you prefer {prefs.remote.replace('_', ' ')} (×{multiplier:.2f})")

    location = postings.field(posting, "location") or ""
    if prefs.locations and location.strip():
        wants_remote = any(p.strip().lower() == "remote" for p in prefs.locations)
        if not (_location_matches(location, prefs.locations) or (wants_remote and mode == "remote")):
            value *= cfg.LOCATION_MISMATCH
            reasons.append(f"{location} is not in your locations (×{cfg.LOCATION_MISMATCH:.2f})")

    size = postings.company_size(posting, volumes)
    detail["company_size"] = size
    if prefs.company_sizes and size not in prefs.company_sizes:
        value *= cfg.COMPANY_SIZE_MISMATCH
        reasons.append(f"{size.capitalize()} company; you prefer {', '.join(prefs.company_sizes)} (×{cfg.COMPANY_SIZE_MISMATCH:.2f})")

    level, _ = postings.seniority(posting, enrichment)
    if prefs.seniority and level and level not in prefs.seniority:
        value *= cfg.SENIORITY_NOT_WANTED
        reasons.append(f"{level} level is not one you asked for (×{cfg.SENIORITY_NOT_WANTED:.2f})")

    start = postings.term_start(posting)
    detail["term_start"] = start
    if prefs.earliest_start and start and start < prefs.earliest_start:
        value *= cfg.START_BEFORE_AVAILABLE
        reasons.append(f"Starts {start}, before you are available ({prefs.earliest_start}) (×{cfg.START_BEFORE_AVAILABLE:.2f})")

    if not reasons:
        reasons.append("Matches every stated preference it can be checked against")
    return FactorResult("preferences", _clamp(value), reasons, detail=detail)


# ---------------------------------------------------------------------------
# 5. Freshness
# ---------------------------------------------------------------------------

def freshness(posting, today, volumes: dict | None = None) -> FactorResult:
    age = postings.days_old(posting, today)
    size = postings.company_size(posting, volumes)
    if age is None:
        return FactorResult("freshness", cfg.FRESHNESS_UNKNOWN_AGE, ["Posting date unknown"], neutral=True,
                            detail={"age_days": None, "company_size": size})
    half_life = cfg.FRESHNESS_HALF_LIFE_DAYS[size]
    value = max(cfg.FRESHNESS_FLOOR, 0.5 ** (age / half_life))
    label = "Posted today" if age == 0 else "Posted yesterday" if age == 1 else f"Posted {age} days ago"
    return FactorResult("freshness", value, [f"{label}; {size} employers' roles halve in value every {half_life:g} days"],
                        detail={"age_days": age, "label": label, "company_size": size})


# ---------------------------------------------------------------------------
# 6. Semantic similarity
# ---------------------------------------------------------------------------

def semantic(cosine: float | None, model_name: str | None) -> FactorResult | None:
    """None when no embedding exists: the factor is skipped, not faked."""
    if cosine is None or not model_name:
        return None
    calibration = cfg.SEMANTIC_CALIBRATION.get(model_name, {"floor": 0.0, "ceil": 1.0})
    span = max(1e-6, calibration["ceil"] - calibration["floor"])
    value = max(cfg.SEMANTIC_MIN, min(1.0, (cosine - calibration["floor"]) / span))
    return FactorResult("semantic", value, [f"Résumé-to-posting similarity {cosine:.3f} ({model_name})"],
                        detail={"cosine": round(cosine, 4), "model": model_name})
