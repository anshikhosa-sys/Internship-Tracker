"""
Ranking metrics over a ranked list of ids and graded relevance labels.

    relevance: 0 not relevant · 1 marginal · 2 relevant · 3 highly relevant

Unlabeled items count as relevance 0. With labels seeded from real
applications that makes precision a LOWER BOUND: an unlabeled posting in the
top 10 may be perfectly good, just not yet applied to. Recall is exact with
respect to the labeled set.
"""

from __future__ import annotations

import math

RELEVANT_AT = 2   # binary metrics treat relevance >= this as relevant
DEFAULT_KS = (10, 20, 50, 100, 500)


def _is_relevant(rel: int) -> bool:
    return rel >= RELEVANT_AT


def precision_at_k(ranked: list[str], labels: dict[str, int], k: int) -> float:
    top = ranked[:k]
    if not top:
        return 0.0
    return sum(1 for i in top if _is_relevant(labels.get(i, 0))) / k


def recall_at_k(ranked: list[str], labels: dict[str, int], k: int) -> float:
    relevant = {i for i, rel in labels.items() if _is_relevant(rel)}
    if not relevant:
        return 0.0
    return len(relevant.intersection(ranked[:k])) / len(relevant)


def reciprocal_rank(ranked: list[str], labels: dict[str, int]) -> float:
    for position, item in enumerate(ranked, start=1):
        if _is_relevant(labels.get(item, 0)):
            return 1.0 / position
    return 0.0


def dcg_at_k(gains: list[int], k: int) -> float:
    return sum((2 ** rel - 1) / math.log2(position + 1) for position, rel in enumerate(gains[:k], start=1))


def ndcg_at_k(ranked: list[str], labels: dict[str, int], k: int) -> float:
    """Graded: a highly relevant item near the top is worth more than a marginal one."""
    actual = dcg_at_k([labels.get(i, 0) for i in ranked], k)
    ideal = dcg_at_k(sorted(labels.values(), reverse=True), k)
    return actual / ideal if ideal > 0 else 0.0


def mean_rank_of_relevant(ranked: list[str], labels: dict[str, int]) -> float | None:
    positions = {item: position for position, item in enumerate(ranked, start=1)}
    found = [positions[i] for i, rel in labels.items() if _is_relevant(rel) and i in positions]
    return sum(found) / len(found) if found else None


def auc(ranked: list[str], labels: dict[str, int]) -> float | None:
    """
    Probability a relevant item is ranked above a non-relevant one (ROC AUC).

    The right headline metric when labels are mostly positives drawn from real
    behavior (applications): it does not assume every unlabeled posting is bad,
    only that relevant ones should, on average, come first. 0.5 is random.
    """
    relevant_seen = 0
    pairs_won = 0
    n_relevant = sum(1 for i in ranked if _is_relevant(labels.get(i, 0)))
    n_other = len(ranked) - n_relevant
    if not n_relevant or not n_other:
        return None
    # Walk from the bottom: each non-relevant item is beaten by every relevant item above it.
    for item in reversed(ranked):
        if _is_relevant(labels.get(item, 0)):
            relevant_seen += 1
        else:
            pairs_won += n_relevant - relevant_seen
    return pairs_won / (n_relevant * n_other)


def random_baseline(ranked: list[str], labels: dict[str, int], ks: tuple[int, ...] = DEFAULT_KS,
                    trials: int = 100, seed: int = 7) -> dict:
    """
    The same metrics for a random ordering of the same pool, averaged over
    seeded shuffles. A metric means little without it: with 36 relevant items
    in 4,000, a precision@10 of 0.1 is ten times better than chance.
    """
    import random

    rng = random.Random(seed)
    pool = list(ranked)
    totals: dict[str, float] = {}
    for _ in range(trials):
        rng.shuffle(pool)
        for name, value in summarize(pool, labels, ks).items():
            totals[name] = totals.get(name, 0.0) + (value or 0.0)
    return {name: value / trials for name, value in totals.items()}


def summarize(ranked: list[str], labels: dict[str, int], ks: tuple[int, ...] = DEFAULT_KS) -> dict:
    out = {"mrr": reciprocal_rank(ranked, labels)}
    for k in ks:
        out[f"precision@{k}"] = precision_at_k(ranked, labels, k)
        out[f"recall@{k}"] = recall_at_k(ranked, labels, k)
        out[f"ndcg@{k}"] = ndcg_at_k(ranked, labels, k)
    out["mean_rank_relevant"] = mean_rank_of_relevant(ranked, labels)
    out["auc"] = auc(ranked, labels)
    return out
