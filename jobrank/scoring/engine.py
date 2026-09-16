"""
The scoring engine: compose per-user factors multiplicatively.

    score = 100 × Π factorᵢ ^ wᵢ        wᵢ = profile.factor_weights[i] × FACTOR_WEIGHT_SCALE[i]

WHY MULTIPLY, NOT ADD
---------------------
An application is worth making only when every condition holds at once: the
user wants this kind of role, is at the right level for it, has the skills,
and it is still open. Addition averages those — a role three levels too senior
still collects most of its points from a strong title match. Multiplication
lets a near-zero on any critical factor sink the result, which is the
behavior wanted: "junior-titled, requires 8 years" should not rank because the
title looked good.

The first version of this project added a large constant for a wanted title,
and the top of the list filled with month-old postings that could not be won.

WHY EXPONENTS FOR WEIGHTS
-------------------------
In a product, a weight cannot be a coefficient (scaling a factor by a constant
changes every score by the same ratio and reorders nothing). An exponent below
1 compresses a factor toward 1, so it still moves the result but cannot
dominate; above 1 sharpens it. Each user's 1-5 priority rating chooses the
exponent, so "freshness matters most to me" changes the ranking without a
single per-person constant in code.

WHY NOT A LEARNED RANKER
------------------------
There is no training data per user, and a recruiting tool has to explain why
each posting is where it is. Every factor here is inspectable and every change
is measured on a golden set (evaluate.py) before it ships. A learned model
becomes the right tool once labels exist at scale; this structure produces the
features it would use.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date

from jobrank import postings
from jobrank.config import scoring as cfg
from jobrank.log import get_logger
from jobrank.models import Profile
from jobrank.scoring import factors

decision_log = get_logger("jobrank.scoring.decisions")


@dataclass
class ScoreResult:
    posting_id: str
    score: int
    raw: float
    factors: dict[str, factors.FactorResult] = field(default_factory=dict)
    weights: dict[str, float] = field(default_factory=dict)
    role_family: str = ""
    company_size: str = ""
    age_days: int | None = None

    def contributions(self) -> dict[str, float]:
        """factor -> value ** weight: how much each factor multiplied the score by."""
        return {name: (f.value ** self.weights[name] if f.value > 0 else 0.0)
                for name, f in self.factors.items()}

    def to_log_record(self) -> dict:
        """The per-factor breakdown without prose reasons: ~0.4 KB per posting."""
        contributions = self.contributions()
        return {
            "posting_id": self.posting_id,
            "score": self.score,
            "role_family": self.role_family,
            "factors": {name: {"v": round(f.value, 4), "w": self.weights[name], "c": round(contributions[name], 4),
                               **({"neutral": True} if f.neutral else {})}
                        for name, f in self.factors.items()},
        }

    def to_dict(self) -> dict:
        return {
            "posting_id": self.posting_id,
            "score": self.score,
            "raw": round(self.raw, 5),
            "role_family": self.role_family,
            "company_size": self.company_size,
            "age_days": self.age_days,
            "factors": {
                name: {"value": round(f.value, 4), "weight": self.weights[name],
                       "contribution": round(self.contributions()[name], 4), "neutral": f.neutral,
                       "reasons": f.reasons}
                for name, f in self.factors.items()
            },
        }


@dataclass
class Context:
    profile: Profile
    today: date
    volumes: dict[str, int]
    enrichments: dict[str, dict] = field(default_factory=dict)
    similarities: dict[str, float] = field(default_factory=dict)
    semantic_model: str | None = None
    disabled: frozenset = frozenset()


def effective_weights(profile: Profile) -> dict[str, float]:
    return {name: profile.factor_weights.get(name, 1.0) * cfg.FACTOR_WEIGHT_SCALE.get(name, 1.0)
            for name in cfg.FACTORS}


def compose(results: dict[str, factors.FactorResult], weights: dict[str, float]) -> float:
    total = 1.0
    for name, result in results.items():
        weight = weights[name]
        if weight == 0:
            continue
        if result.value <= 0:
            return 0.0      # 0 ** w would be 0 anyway, but 0 ** 0 == 1 in Python
        total *= result.value ** weight
    return total


def score_posting(posting, ctx: Context) -> ScoreResult:
    posting_id = postings.field(posting, "id")
    enrichment = (ctx.enrichments.get(posting_id) or {}).get("data")
    results: dict[str, factors.FactorResult] = {
        "skills": factors.skills(posting, ctx.profile, enrichment),
        "seniority": factors.seniority(posting, ctx.profile, enrichment),
        "role": factors.role(posting, ctx.profile),
        "preferences": factors.preferences(posting, ctx.profile, enrichment, ctx.volumes),
        "freshness": factors.freshness(posting, ctx.today, ctx.volumes),
    }
    semantic = factors.semantic(ctx.similarities.get(posting_id), ctx.semantic_model)
    if semantic is not None:
        results["semantic"] = semantic
    for name in ctx.disabled:
        results.pop(name, None)

    weights = effective_weights(ctx.profile)
    raw = compose(results, weights)
    return ScoreResult(
        posting_id=posting_id,
        score=round(raw * 100),
        raw=raw,
        factors=results,
        weights={name: weights[name] for name in results},
        role_family=results["role"].detail.get("family", "") if "role" in results else "",
        company_size=postings.company_size(posting, ctx.volumes),
        age_days=postings.days_old(posting, ctx.today),
    )


def build_context(profile: Profile, all_postings: list, today: date | None = None,
                  enrichments: dict | None = None, semantic: bool = True,
                  disabled: set[str] | None = None) -> Context:
    ctx = Context(profile=profile, today=today or date.today(), volumes=postings.company_volumes(all_postings),
                  enrichments=enrichments or {}, disabled=frozenset(disabled or ()))
    if semantic and "semantic" not in ctx.disabled:
        from jobrank.semantic import similarities_for
        ctx.similarities, ctx.semantic_model = similarities_for(profile, all_postings, ctx.enrichments)
    return ctx


def score_all(profile: Profile, all_postings: list, today: date | None = None, enrichments: dict | None = None,
              semantic: bool = True, disabled: set[str] | None = None, log_decisions: bool = False,
              run_id: str | None = None) -> list[ScoreResult]:
    """Score and rank every posting for one profile, best first."""
    ctx = build_context(profile, all_postings, today, enrichments, semantic, disabled)
    results = [score_posting(p, ctx) for p in all_postings]
    results.sort(key=lambda r: (-r.raw, r.posting_id))
    if log_decisions:
        for rank, result in enumerate(results, start=1):
            decision_log.log(logging.INFO, "score", extra={"fields": {
                "run_id": run_id, "profile": profile.user_id, "rank": rank, **result.to_log_record()}})
    return results


def fit_label(score: int) -> str:
    if score >= cfg.STRONG_FIT_THRESHOLD:
        return "strong"
    if score >= cfg.GOOD_FIT_THRESHOLD:
        return "good"
    if score >= cfg.LOW_FIT_THRESHOLD:
        return "fair"
    return "low"
