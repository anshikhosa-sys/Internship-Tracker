"""
Human-readable explanation of one score: every factor, its value, the
exponent the user's priorities gave it, what it multiplied the score by, and
the evidence behind it. The same data as the dashboard's "Why this score?",
laid out for a terminal.
"""

from __future__ import annotations

from jobrank import postings
from jobrank.config import taxonomy
from jobrank.models import Profile
from jobrank.scoring.engine import ScoreResult

LABELS = {
    "skills": "Skill overlap",
    "seniority": "Seniority fit",
    "role": "Role affinity",
    "freshness": "Freshness",
    "semantic": "Semantic similarity",
}


def explain(profile: Profile, posting: dict, result: ScoreResult, rank: int | None = None,
            total: int | None = None) -> str:
    lines = []
    head = f"{posting.get('company', '')} — {posting.get('role', '')}"
    lines.append(head)
    lines.append("=" * min(len(head), 78))
    facts = [posting.get("location") or "location not stated", posting.get("category") or "",
             f"posted {posting.get('date_posted')}" if posting.get("date_posted") else "date unknown",
             f"{postings.company_size(posting)} company"]
    industry = postings.industry(posting)
    if industry:
        facts.append(f"industry: {industry}")
    lines.append(" · ".join(f for f in facts if f))
    lines.append(f"id {result.posting_id}")
    lines.append("")
    where = f", rank {rank} of {total}" if rank and total else ""
    lines.append(f"Score {result.score} for profile '{profile.user_id}'{where}")
    lines.append("")

    formula = " × ".join(f"{f.value:.2f}^{result.weights[name]:g}" for name, f in result.factors.items())
    lines.append(f"  {formula} × 100 = {result.raw * 100:.1f}")
    lines.append("")

    contributions = result.contributions()
    width = max(len(LABELS[name]) for name in result.factors)
    for name, factor in result.factors.items():
        tag = "  (no data: neutral)" if factor.neutral else ""
        lines.append(f"  {LABELS[name]:<{width}}  value {factor.value:.2f}  weight {result.weights[name]:g}  "
                     f"→ ×{contributions[name]:.3f}{tag}")
        for reason in factor.reasons:
            lines.append(f"  {'':<{width}}    · {reason}")
    lines.append("")

    ordered = sorted(contributions.items(), key=lambda kv: kv[1])
    weakest, value = ordered[0]
    lines.append(f"Biggest drag: {LABELS[weakest]} (×{value:.2f}). "
                 f"Raising it to 1.0 would make this {min(100, round(result.raw / value * 100)) if value else 'n/a'}.")

    missing = sorted({"freshness", "semantic"} - set(result.factors))
    if missing:
        lines.append(f"Not scored: {', '.join(missing)} (disabled or unavailable).")

    family = result.role_family
    if family:
        detail = result.factors["role"].detail if "role" in result.factors else {}
        market = detail.get("market_affinity")
        note = f" You cover {market:.0%} of what these postings currently ask for." if market is not None else ""
        lines.append(f"Classified as {taxonomy.ROLE_FAMILIES[family]['label']}.{note}")
    return "\n".join(lines)
