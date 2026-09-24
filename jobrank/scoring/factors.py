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

from jobrank import postings
from jobrank.config import market as market_cfg
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
    """
    The skills a posting asks for.

    When an enrichment exists it already holds the skills extracted from the
    description — enrichment runs over the same text and is cached by content
    hash. Re-scanning the description here repeats that work on every ranking:
    with 1,932 descriptions averaging 5 KB, it was 19 of the 52 seconds a cold
    dashboard load took, for a result identical to the one already stored. So
    only the title is scanned when the cache can supply the rest.
    """
    if enrichment is not None:
        found = set(enrichment.get("tech_stack") or [])
        found.update(canonical_skills(postings.field(posting, "role") or ""))
        return sorted(found)

    text = " ".join(filter(None, [postings.field(posting, "role"),
                                  postings.field(posting, "description")]))
    return sorted(set(canonical_skills(text)))


def skills(posting, profile: Profile, enrichment: dict | None = None, market=None) -> FactorResult:
    """
    How much of what this posting asks for the résumé can actually show.

    Skills are weighted by how rare they are in the current corpus. Counting
    them equally means mostly counting Python: almost every posting asks for
    it, so matching it says nothing, while matching CUDA says a great deal.
    Without a market model every weight is 1 and this is a plain average.
    """
    wanted = posting_skills(posting, enrichment)
    if not wanted:
        return FactorResult("skills", cfg.SKILLS_NEUTRAL, ["Posting names no specific skills"], neutral=True)
    have = {s: profile.skills.get(s, 0.0) for s in wanted}
    weights = {s: (market.idf(s) if market else 1.0) for s in wanted}
    total = sum(weights.values()) or 1.0
    coverage = sum(have[s] * weights[s] for s in wanted) / total
    value = cfg.SKILLS_FLOOR + (1 - cfg.SKILLS_FLOOR) * coverage
    matched = [s for s, w in have.items() if w > 0]
    missing = [s for s, w in have.items() if w == 0]
    reasons = []
    if matched:
        reasons.append("Your résumé shows: " + ", ".join(f"{s} ({have[s]:.2f})" for s in matched))
    if missing:
        reasons.append("Not on your résumé: " + ", ".join(missing))
    if market and missing:
        scarce = max(missing, key=lambda s: weights[s])
        if weights[scarce] > 1.0:
            reasons.append(f"The gap that costs most here is {scarce} — few candidates have it")
    return FactorResult("skills", _clamp(value), reasons,
                        detail={"coverage": round(coverage, 3), "matched": matched, "missing": missing,
                                "idf_weighted": bool(market)})


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
# 3. Role affinity — what the résumé proves the user does, not what they asked for
# ---------------------------------------------------------------------------

def role(posting, profile: Profile, market=None, market_affinity: dict | None = None) -> FactorResult:
    """
    Whether the résumé is evidence for this kind of work.

    Two sources, blended. Résumé evidence is direct proof — you held the title,
    you used the tools the family is defined by. Market overlap is transfer:
    what these postings actually demand, learned from the corpus, matched
    against what the résumé shows. Evidence leads, because it is the stronger
    claim, but transfer stops a hand-written keyword list from being the only
    say. Data engineering's list was all specialist tools, so a Python-and-SQL
    résumé floored at 0.05 and real applications sank to rank ~4,000.

    Nothing here reads a stated target. A family the résumé proves outranks one
    the user merely named.
    """
    families = postings.role_families(posting)
    if not families:
        return FactorResult("role", cfg.ROLE_UNKNOWN, ["Role type not recognized from the title"], neutral=True,
                            detail={"families": []})

    def affinity_of(family: str) -> float:
        evidence = profile.role_affinity.get(family, cfg.ROLE_UNKNOWN)
        demand = (market_affinity or {}).get(family)
        if demand is None:
            return evidence
        return market_cfg.EVIDENCE_SHARE * evidence + (1 - market_cfg.EVIDENCE_SHARE) * demand

    best = max(families, key=affinity_of)
    value = affinity_of(best)
    label = taxonomy.ROLE_FAMILIES[best]["label"]
    evidence = (profile.evidence.get("role_affinity") or {}).get(best) or {}
    proof = (evidence.get("titles") or []) + (evidence.get("topics") or [])
    because = ("your résumé shows " + ", ".join(proof[:4])) if proof else "your résumé shows no direct evidence"
    reasons = [f"{label}: affinity {value:.2f} — {because}"]
    demand = (market_affinity or {}).get(best)
    if demand is not None:
        reasons.append(f"You cover {demand:.0%} of what {label.lower()} postings currently ask for")
    return FactorResult("role", _clamp(value), reasons,
                        detail={"families": families, "family": best,
                                "evidence_affinity": round(profile.role_affinity.get(best, 0.0), 3),
                                "market_affinity": round(demand, 3) if demand is not None else None})


# ---------------------------------------------------------------------------
# 4. Freshness
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
# 5. Semantic similarity
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
