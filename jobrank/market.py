"""
What the job market is asking for, right now, learned from the corpus itself.

WHY
---
Two numbers in the scorer used to be asserted rather than measured.

*Skill overlap* counted every skill equally, so a posting wanting Python and a
posting wanting CUDA credited a résumé the same way. Most postings want
Python; almost none want CUDA. An overlap that ignores that is mostly
measuring how common a word is.

*Role affinity* came from hand-written evidence lists in the taxonomy. Data
engineering's list was spark, kafka, airflow, dbt, etl, snowflake, bigquery —
all specialist tools. A résumé with Python, SQL, PostgreSQL and pandas
therefore scored the 0.05 floor for data engineering, and real data-engineering
applications sank to rank ~4,000 of 4,753. The corpus knows better: it can be
asked what data-engineering postings actually demand.

Both are now read off the live postings. Nothing here is written by hand, and
nothing is specific to a user — the same model serves every profile, and it
changes on its own as the market does.

DEGRADES
--------
With too few postings (a fresh clone, an empty database) a family's profile is
not trusted and the affected term falls back to résumé evidence alone, exactly
as before. The model is never required (invariant 7).
"""

from __future__ import annotations

import json
import math
import os
from functools import lru_cache
from dataclasses import dataclass, field

from jobrank import postings as posting_facts
from jobrank.config import market as cfg
from jobrank.config import taxonomy
from jobrank.log import get_logger
from jobrank.resume.parser import canonical_skills

log = get_logger(__name__)


@dataclass(eq=False)
class MarketModel:
    """Skill demand across the corpus, and per role family."""

    total_postings: int = 0
    skill_postings: dict[str, int] = field(default_factory=dict)          # skill -> postings naming it
    family_postings: dict[str, int] = field(default_factory=dict)         # family -> postings in it
    family_skill_postings: dict[str, dict[str, int]] = field(default_factory=dict)
    described_postings: int = 0
    _averages: dict | None = field(default=None, repr=False, compare=False)

    # -- how much a skill tells you -------------------------------------
    def idf(self, skill: str) -> float:
        """
        Rarity weight. A skill in most postings separates nobody; a skill in
        few separates a lot. Clamped so one rare token cannot decide a score.
        """
        if not self.total_postings:
            return 1.0
        seen = self.skill_postings.get(skill, 0)
        raw = math.log(self.total_postings / (1 + seen))
        return max(cfg.IDF_MIN, min(cfg.IDF_MAX, raw))

    # -- what a family demands --------------------------------------------
    @lru_cache(maxsize=64)
    def demand(self, family: str) -> dict[str, float]:
        """skill -> share of that family's postings naming it, above the floor."""
        total = self.family_postings.get(family, 0)
        if total < cfg.MIN_POSTINGS_PER_FAMILY:
            return {}
        counts = self.family_skill_postings.get(family, {})
        return {skill: n / total for skill, n in counts.items() if n / total >= cfg.FAMILY_DEMAND_FLOOR}

    def _average_shares(self) -> dict[str, float]:
        """
        skill -> its mean share across families with a usable sample.

        Computed once and reused: doing it per (family, skill) makes the whole
        model quadratic in families, which took minutes on the live corpus.
        """
        if self._averages is None:
            usable = [f for f, n in self.family_postings.items() if n >= cfg.MIN_POSTINGS_PER_FAMILY]
            averages: dict[str, float] = {}
            if usable:
                for skill in self.skill_postings:
                    total = sum(self.family_skill_postings.get(f, {}).get(skill, 0) / self.family_postings[f]
                                for f in usable)
                    averages[skill] = total / len(usable)
            self._averages = averages
        return self._averages

    def _distinctiveness(self, skill: str, share: float) -> float:
        """How much more this family wants a skill than families do on average."""
        average = self._average_shares().get(skill, 0.0)
        if average <= 0:
            return 1.0
        return min(cfg.DISTINCTIVENESS_MAX, share / average)

    def affinity(self, user_skills: dict[str, float]) -> dict[str, float]:
        """
        family -> how well this résumé covers what that family demands, in [0,1].

        Each demanded skill is weighted by how much the family wants it, how
        rare it is, and how distinctive it is to this family. The result is the
        share of that weight the résumé can account for — so a Python-and-SQL
        résumé gets real credit for data engineering, but a family that wants
        Kafka and Airflow still separates someone who has them.
        """
        raw: dict[str, float] = {}
        for family in taxonomy.ROLE_FAMILIES:
            demanded = self.demand(family)
            if not demanded:
                continue
            earned = total = 0.0
            for skill, share in demanded.items():
                weight = share * self.idf(skill) * self._distinctiveness(skill, share)
                total += weight
                earned += weight * min(1.0, user_skills.get(skill, 0.0))
            if total > 0:
                raw[family] = earned / total
        if not raw:
            return {}

        # Rescale onto [0,1] against a FIXED observed range. A coverage ratio
        # never approaches 1 — nobody has every skill a family asks for — so
        # using it raw reads a good fit as a poor one and drags the whole
        # product down. Normalising against this résumé's own maximum instead
        # would be worse: it makes whatever someone is least-bad at score 1.0.
        span = max(1e-6, cfg.MARKET_CALIBRATION_CEILING - cfg.MARKET_CALIBRATION_FLOOR)
        return {family: round(max(cfg.MARKET_CALIBRATION_MIN,
                                  min(1.0, (value - cfg.MARKET_CALIBRATION_FLOOR) / span)), 4)
                for family, value in raw.items()}

    # -- persistence -------------------------------------------------------
    def to_dict(self) -> dict:
        return {"version": cfg.CACHE_VERSION, "total_postings": self.total_postings,
                "described_postings": self.described_postings,
                "skill_postings": self.skill_postings, "family_postings": self.family_postings,
                "family_skill_postings": self.family_skill_postings}

    @classmethod
    def from_dict(cls, data: dict) -> "MarketModel":
        return cls(total_postings=data.get("total_postings", 0),
                   described_postings=data.get("described_postings", 0),
                   skill_postings=data.get("skill_postings", {}),
                   family_postings=data.get("family_postings", {}),
                   family_skill_postings=data.get("family_skill_postings", {}))


def build(all_postings) -> MarketModel:
    """Scan the corpus once, counting which skills each role family asks for."""
    model = MarketModel()
    for posting in list(all_postings)[:cfg.MAX_POSTINGS_SCANNED]:
        role = posting_facts.field(posting, "role") or ""
        description = posting_facts.field(posting, "description") or ""
        skills = set(canonical_skills(f"{role}\n{description}"))
        families = posting_facts.role_families(posting)
        model.total_postings += 1
        if description:
            model.described_postings += 1
        for skill in skills:
            model.skill_postings[skill] = model.skill_postings.get(skill, 0) + 1
        for family in families:
            model.family_postings[family] = model.family_postings.get(family, 0) + 1
            bucket = model.family_skill_postings.setdefault(family, {})
            for skill in skills:
                bucket[skill] = bucket.get(skill, 0) + 1
    return model


def load(all_postings=None, cache_path: str | None = None, fingerprint: str = "",
         allow_build: bool = True) -> MarketModel:
    """
    The cached model, rebuilt when the corpus fingerprint changes.

    `allow_build=False` means "use the cache or go without". Serving a web
    request must never pay for a full corpus scan: it takes seconds, and it
    takes a database lock that a concurrent refresh already holds, which turns
    a slow page into a hung one. The pipeline builds the model; readers read it.

    Returns an empty model rather than raising when nothing can be built; every
    caller treats an empty model as "no market information", not as zero demand
    (invariant 9).
    """
    path = cache_path or cfg.CACHE_PATH
    try:
        with open(path) as handle:
            cached = json.load(handle)
        if cached.get("version") == cfg.CACHE_VERSION and cached.get("fingerprint") == fingerprint:
            return MarketModel.from_dict(cached)
    except (OSError, ValueError):
        pass

    if all_postings is None or not allow_build:
        return MarketModel()
    model = build(all_postings)
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w") as handle:
            json.dump({**model.to_dict(), "fingerprint": fingerprint}, handle)
    except OSError as exc:
        log.debug("market model not cached", extra={"fields": {"error": str(exc)}})
    return model
