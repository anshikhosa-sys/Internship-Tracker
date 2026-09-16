"""
Ranked postings for a profile, as the dashboard, refresh report, notifications
and CLI all need them.

Scoring 4,000 postings takes about a second once the embedding model is warm.
Results are cached in memory, keyed by everything that can change them: the
profile file, the last refresh, today's date (freshness), and the enrichment
table. Anything else changing means the process restarted anyway.
"""

from __future__ import annotations

import os
import time
import uuid
from dataclasses import dataclass
from datetime import date

from jobrank import postings, storage
from jobrank.log import event, get_logger
from jobrank.config import profile as profile_cfg
from jobrank.models import Profile
from jobrank.profile import active, store
from jobrank.scoring import engine
from jobrank.scoring.engine import ScoreResult


@dataclass
class Ranking:
    profile: Profile
    rows: list[dict]                   # every loaded posting row, annotated
    results: dict[str, ScoreResult]    # posting_id -> result
    ordered_ids: list[str]


_cache: dict[tuple, Ranking] = {}
log = get_logger(__name__)


def _profile_mtime(user_id: str) -> float:
    try:
        return os.path.getmtime(store.path_for(user_id, profile_cfg.PROFILES_DIR))
    except OSError:
        return 0.0


def annotate(row: dict, result: ScoreResult) -> dict:
    """Copy score facts onto a posting row for templates and reports."""
    row = dict(row)
    fresh = result.factors.get("freshness")
    row.update({
        "fit_score": result.score,
        "fit": engine.fit_label(result.score),
        "factor_values": {name: round(f.value, 3) for name, f in result.factors.items()},
        "factors": result.to_dict()["factors"],
        "role_family": result.role_family,
        "company_tier": result.company_size,
        "age_days": result.age_days,
        "freshness_label": (fresh.detail.get("label") if fresh else None) or "Age unknown",
        "industry": postings.industry(row),
        "is_tech_employer": postings.is_tech_employer(row),
        "is_coop": postings.is_coop(row),
        "is_off_season": postings.is_off_season(row),
        "hourly_pay": postings.hourly_pay(row),
    })
    return row


def rank(user_id: str | None = None, conn=None, today: date | None = None, semantic: bool = True,
         trigger: str = "dashboard") -> Ranking | None:
    own = conn is None
    conn = conn or storage.connect()
    try:
        user_id = active.resolve(user_id, conn)
        if not user_id:
            return None
        today = today or date.today()
        enrichments = storage.get_enrichments(conn)
        key = (user_id, _profile_mtime(user_id), storage.last_run_time(conn), today, len(enrichments), semantic)
        if key in _cache:
            cached = _cache[key]
            # Application status changes without a refresh; re-read the rows,
            # keep the (expensive) scores.
            fresh_rows = {r["id"]: r for r in storage.load_postings(conn)}
            rows = [annotate(fresh_rows[r["id"]], cached.results[r["id"]]) for r in cached.rows if r["id"] in fresh_rows]
            return Ranking(cached.profile, rows, cached.results, cached.ordered_ids)

        started = time.perf_counter()
        run_id = uuid.uuid4().hex[:12]
        profile = store.load(user_id)
        raw_rows = storage.load_postings(conn)
        results = engine.score_all(profile, raw_rows, today=today, enrichments=enrichments, semantic=semantic,
                                   log_decisions=True, run_id=run_id)
        top = results[0] if results else None
        event(log, "ranking_computed", run_id=run_id, trigger=trigger, profile=user_id, postings=len(results),
              duration_ms=round((time.perf_counter() - started) * 1000, 1),
              semantic_model=(top.factors["semantic"].detail["model"] if top and "semantic" in top.factors else None),
              top_score=top.score if top else None)
        by_id = {r.posting_id: r for r in results}
        rows = [annotate(row, by_id[row["id"]]) for row in raw_rows]
        ranking = Ranking(profile, rows, by_id, [r.posting_id for r in results])
        _cache.clear()
        _cache[key] = ranking
        return ranking
    finally:
        if own:
            conn.close()


def clear_cache() -> None:
    _cache.clear()
