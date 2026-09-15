"""
Run the current scoring configuration against a golden set.

The pool is every posting that could be ranked: active postings in the
database plus any labeled posting (even one that has since closed), or a JSONL
fixture for a reproducible run on a fresh clone.

FRESHNESS IS EXCLUDED BY DEFAULT. A label says "this posting fits this person";
it was made on some past day. Scoring it with today's freshness would punish a
correct ranking for the calendar. `--with-freshness` includes it for the
"what should I do today" view.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from datetime import date, datetime, timezone

from jobrank import storage
from jobrank.config import scoring as scoring_cfg
from jobrank.eval import golden, metrics
from jobrank.profile import store
from jobrank.scoring import engine

BASELINE_DIR = os.path.join("eval", "baselines")
GATED_METRICS = ["auc", "recall@100", "recall@500", "ndcg@20", "mrr"]
DEFAULT_TOLERANCE = 0.02


def load_fixture(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def load_pool(labels: dict[str, golden.Label], fixture: str | None = None) -> list[dict]:
    if fixture:
        return load_fixture(fixture)
    conn = storage.connect()
    try:
        rows = storage.load_postings(conn, include_inactive=True)
    finally:
        conn.close()
    return [r for r in rows if r.get("is_active") or r["id"] in labels]


def config_fingerprint() -> str:
    """Hash of every config module that shapes a score, so a baseline records what produced it."""
    from jobrank.config import companies, profile, scoring, semantic, taxonomy

    digest = hashlib.sha256()
    for module in (scoring, taxonomy, companies, profile, semantic):
        with open(module.__file__, "rb") as handle:
            digest.update(handle.read())
    return digest.hexdigest()[:16]


def _git_commit() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                              timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def evaluate(profile_id: str, fixture: str | None = None, with_freshness: bool = False,
             disabled: set[str] | None = None, semantic: bool = True, today: date | None = None,
             ks: tuple[int, ...] = metrics.DEFAULT_KS) -> dict:
    labels = golden.load(profile_id)
    if not labels:
        raise ValueError(f"No labels for '{profile_id}'. Seed them with: python3 run.py label seed --profile {profile_id}")
    profile = store.load(profile_id)
    pool = load_pool(labels, fixture)
    pool_ids = {p["id"] for p in pool}
    usable = {pid: label.relevance for pid, label in labels.items() if pid in pool_ids}

    off = set(disabled or ())
    if not with_freshness:
        off.add("freshness")
    conn = storage.connect() if not fixture else None
    try:
        enrichments = storage.get_enrichments(conn) if conn else {}
    finally:
        if conn:
            conn.close()
    results = engine.score_all(profile, pool, today=today, enrichments=enrichments, semantic=semantic,
                               disabled=off)
    ranked = [r.posting_id for r in results]
    summary = metrics.summarize(ranked, usable, ks)
    random_summary = metrics.random_baseline(ranked, usable, ks)
    by_id = {r.posting_id: r for r in results}
    positions = {pid: i for i, pid in enumerate(ranked, start=1)}
    return {
        "profile": profile_id,
        "metrics": summary,
        "random_baseline": random_summary,
        "labels_total": len(labels),
        "labels_usable": len(usable),
        "labels_relevant": sum(1 for v in usable.values() if v >= metrics.RELEVANT_AT),
        "pool_size": len(pool),
        "disabled_factors": sorted(off),
        "active_factors": sorted(results[0].factors) if results else [],
        "semantic_model": next((r.factors["semantic"].detail["model"] for r in results if "semantic" in r.factors),
                               None),
        "config_fingerprint": config_fingerprint(),
        "git_commit": _git_commit(),
        "evaluated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "fixture": fixture,
        "worst_misses": sorted(
            ({"posting_id": pid, "rank": positions[pid], "relevance": rel, "score": by_id[pid].score,
              "company": labels[pid].company, "role": labels[pid].role}
             for pid, rel in usable.items() if rel >= metrics.RELEVANT_AT),
            key=lambda m: -m["rank"])[:5],
    }


def ablation(profile_id: str, metric: str = "auc", **kwargs) -> list[dict]:
    """How much each factor contributes: the metric with that factor removed, versus all factors."""
    base_disabled = set(kwargs.pop("disabled", None) or ())
    full = evaluate(profile_id, disabled=base_disabled, **kwargs)
    base = full["metrics"][metric]
    rows = []
    factors = [f for f in scoring_cfg.FACTORS if f in full["active_factors"]]
    for factor in factors:
        result = evaluate(profile_id, disabled=base_disabled | {factor}, **kwargs)
        rows.append({"factor": factor, metric: result["metrics"][metric], "delta": base - result["metrics"][metric]})
    return [{"factor": "(all factors)", metric: base, "delta": 0.0}] + sorted(rows, key=lambda r: -r["delta"])


def baseline_path(profile_id: str) -> str:
    return os.path.join(BASELINE_DIR, f"{store.validate_user_id(profile_id)}.json")


def save_baseline(result: dict) -> str:
    path = baseline_path(result["profile"])
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump({k: v for k, v in result.items() if k != "worst_misses"}, handle, indent=2)
        handle.write("\n")
    return path


def load_baseline(profile_id: str) -> dict | None:
    path = baseline_path(profile_id)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def compare(current: dict, baseline: dict, tolerance: float = DEFAULT_TOLERANCE) -> dict:
    rows, regressions = [], []
    for name, value in current["metrics"].items():
        before = baseline["metrics"].get(name)
        if value is None or before is None:
            continue
        delta = value - before
        # mean_rank_relevant: lower is better; everything else higher is better.
        worse = delta > tolerance * max(1.0, before) if name == "mean_rank_relevant" else delta < -tolerance
        gated = name in GATED_METRICS
        rows.append({"metric": name, "baseline": before, "current": value, "delta": delta,
                     "regressed": worse and gated})
        if worse and gated:
            regressions.append(name)
    comparable = baseline.get("labels_usable") == current["labels_usable"] and \
        baseline.get("fixture") == current["fixture"]
    return {"rows": rows, "regressions": regressions, "comparable": comparable,
            "config_changed": baseline.get("config_fingerprint") != current["config_fingerprint"]}
