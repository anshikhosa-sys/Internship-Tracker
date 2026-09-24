"""
The scoring engine: compose per-user factors multiplicatively.

    score = 100 × Π factorᵢ ^ wᵢ        wᵢ = cfg.FACTOR_EXPONENTS[i]

WHY MULTIPLY, NOT ADD
---------------------
An application is worth making only when every condition holds at once: the
résumé is evidence for this kind of role, the user is at the right level for
it, they have the skills it asks for, and it is still open. Addition averages those — a role three levels too senior
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
dominate; above 1 sharpens it. The exponents are fitted on the golden set by
coordinate search, so each factor's influence is measured. They were once a
per-user 1-5 priority rating, which is a preference wearing a weight's
clothes: it moved the ranking with no evidence that it improved the odds.

WHY NO PREFERENCES
------------------
Nothing here reads what the user said they want. The score estimates whether
an application is worth making, and a stated wish does not change that: it
cannot add a skill to a résumé or make a posting less contested. Preferences
still filter the view (see invariant 16 — a display cutoff is never a
deletion); they do not reorder it.

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


def _corpus_fingerprint(all_postings: list) -> str:
    """
    Identifies the corpus the market model was built from, so the cache is
    rebuilt when postings change and reused when they have not.

    Identity alone is not enough. The model is built from what postings SAY,
    and a posting's text changes without its id changing — fetching 692
    descriptions in one run took coverage from 26% to 40% while leaving every
    id, and the count, exactly as it was. A fingerprint over ids alone would
    have gone on serving skill demand learned from the thinner corpus, right
    at the moment the data improved. So the text is fingerprinted too.
    """
    import hashlib
    ids = sorted(postings.field(p, "id") or "" for p in all_postings)
    described = sum(1 for p in all_postings if postings.field(p, "description"))
    text_size = sum(len(postings.field(p, "description") or "") for p in all_postings)
    digest = hashlib.sha256(
        f"{len(ids)}:{ids[-1] if ids else ''}:{ids[0] if ids else ''}:{described}:{text_size}".encode())
    return digest.hexdigest()[:16]


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
    market: object | None = None              # MarketModel: what the corpus demands
    market_affinity: dict = field(default_factory=dict)   # family -> résumé's coverage of it


def effective_weights(profile: Profile) -> dict[str, float]:
    """
    The same exponents for everybody. They are fitted on the golden set, not
    stated by the user: a priority rating is a preference, and preferences do
    not change whether an application succeeds.
    """
    return dict(cfg.FACTOR_EXPONENTS)


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
        "skills": factors.skills(posting, ctx.profile, enrichment, ctx.market),
        "seniority": factors.seniority(posting, ctx.profile, enrichment),
        "role": factors.role(posting, ctx.profile, ctx.market, ctx.market_affinity),
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
                  disabled: set[str] | None = None, use_market: bool = True,
                  build_market: bool = True) -> Context:
    ctx = Context(profile=profile, today=today or date.today(), volumes=postings.company_volumes(all_postings),
                  enrichments=enrichments or {}, disabled=frozenset(disabled or ()))
    if use_market and "market" not in ctx.disabled:
        from jobrank import market as market_model
        ctx.market = market_model.load(all_postings, fingerprint=_corpus_fingerprint(all_postings),
                                       allow_build=build_market)
        if not ctx.market.total_postings:
            ctx.market = None                 # no model is no information, not zero demand
        else:
            ctx.market_affinity = ctx.market.affinity(profile.skills)
    if semantic and "semantic" not in ctx.disabled:
        from jobrank.semantic import similarities_for
        # Embedding is pipeline work, for the same reason the market model is:
        # it takes minutes and it takes a write lock.
        ctx.similarities, ctx.semantic_model = similarities_for(
            profile, all_postings, ctx.enrichments, allow_embed=build_market)
    return ctx


def score_all(profile: Profile, all_postings: list, today: date | None = None, enrichments: dict | None = None,
              semantic: bool = True, disabled: set[str] | None = None, log_decisions: bool = False,
              run_id: str | None = None, use_market: bool = True,
              build_market: bool = True) -> list[ScoreResult]:
    """Score and rank every posting for one profile, best first."""
    ctx = build_context(profile, all_postings, today, enrichments, semantic, disabled, use_market,
                        build_market)
    results = [score_posting(p, ctx) for p in all_postings]
    results.sort(key=lambda r: (-r.raw, r.posting_id))
    if log_decisions:
        for rank, result in enumerate(results, start=1):
            decision_log.log(logging.INFO, "score", extra={"fields": {
                "run_id": run_id, "profile": profile.user_id, "rank": rank, **result.to_log_record()}})
    return results


def fit_label(score: int) -> str:
    """
    A label for one score in isolation — used by notifications, which have no
    ranking to compare against. Prefer fit_labels_for() wherever the whole
    ranking is in hand.
    """
    if score >= cfg.STRONG_FIT_THRESHOLD:
        return "strong"
    if score >= cfg.GOOD_FIT_THRESHOLD:
        return "good"
    if score >= cfg.LOW_FIT_THRESHOLD:
        return "fair"
    return "low"


def fit_thresholds(scores: list[int]) -> dict[str, int]:
    """
    Turn a ranking's own score distribution into cut-offs for its labels.

    The score is a product of factors that are each below 1, so it is bounded
    well below 100 and its useful range shifts with the corpus and the résumé.
    A percentile means the same thing in every one of those worlds; a fixed
    number does not, and the fixed ones had drifted until three postings out
    of 4,779 qualified as strong.

    The absolute minimums stop a thin ranking from promoting its best rows:
    being in the top 1% of a list you match nothing in is not a strong fit.
    """
    if not scores:
        return {"strong": cfg.STRONG_FIT_MINIMUM, "good": cfg.GOOD_FIT_MINIMUM,
                "fair": cfg.FAIR_FIT_MINIMUM}
    ordered = sorted(scores)

    def at(percentile: float) -> int:
        index = min(len(ordered) - 1, int(len(ordered) * percentile / 100))
        return ordered[index]

    return {
        "strong": max(at(cfg.STRONG_FIT_PERCENTILE), cfg.STRONG_FIT_MINIMUM),
        "good": max(at(cfg.GOOD_FIT_PERCENTILE), cfg.GOOD_FIT_MINIMUM),
        "fair": max(at(cfg.FAIR_FIT_PERCENTILE), cfg.FAIR_FIT_MINIMUM),
    }


def fit_labels_for(score: int, thresholds: dict[str, int]) -> str:
    if score >= thresholds["strong"]:
        return "strong"
    if score >= thresholds["good"]:
        return "good"
    if score >= thresholds["fair"]:
        return "fair"
    return "low"
