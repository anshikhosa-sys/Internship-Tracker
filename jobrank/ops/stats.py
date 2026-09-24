"""
Operational statistics: LLM usage, caches, enrichment coverage, embedding
coverage, and recent ranking runs. Read-only — collecting stats never
changes what it measures.
"""

from __future__ import annotations

import json
import os
import sqlite3
from collections import Counter

from jobrank import storage
from jobrank.config import llm as llm_cfg
from jobrank.config import semantic as semantic_cfg
from jobrank.llm import cache
from jobrank.log import LOG_DIR


def llm_usage() -> list[dict]:
    if not os.path.exists(llm_cfg.CACHE_PATH):
        return []
    conn = cache.connect()
    try:
        return cache.stats(conn)
    finally:
        conn.close()


def llm_cache_entries() -> dict[str, int]:
    if not os.path.exists(llm_cfg.CACHE_PATH):
        return {}
    conn = sqlite3.connect(llm_cfg.CACHE_PATH)
    try:
        return dict(conn.execute("SELECT task || ' / ' || backend, COUNT(*) FROM responses GROUP BY task, backend"))
    finally:
        conn.close()


def enrichment_coverage() -> dict:
    conn = storage.connect()
    try:
        active = conn.execute("SELECT COUNT(*) FROM postings WHERE is_active = 1").fetchone()[0]
        by_extractor = Counter(v["extractor"] for v in storage.get_enrichments(conn).values())
        contradictions = sum(1 for v in storage.get_enrichments(conn).values()
                             if v["data"].get("title_contradiction"))
    finally:
        conn.close()
    return {"active_postings": active, "enriched_by": dict(by_extractor), "title_contradictions": contradictions}


def embedding_coverage() -> dict[str, int]:
    """
    How many vectors exist per model, or {} when the store cannot be read.

    Opened read-only and briefly: embedding the corpus takes minutes, and a
    long-lived dashboard process can hold the write lock the whole time. A
    statistic is not worth a 500 — reporting "unknown" is the honest answer,
    and this page is also what a health probe hits.
    """
    if not os.path.exists(semantic_cfg.VECTOR_DB_PATH):
        return {}
    try:
        conn = sqlite3.connect(f"file:{semantic_cfg.VECTOR_DB_PATH}?mode=ro", uri=True, timeout=2.0)
    except sqlite3.Error:
        return {}
    try:
        return dict(conn.execute("SELECT model, COUNT(*) FROM vectors GROUP BY model"))
    except sqlite3.Error:
        return {}
    finally:
        conn.close()


def recent_events(name: str, limit: int = 10, path: str | None = None) -> list[dict]:
    """The last `limit` structured log events with this name (newest first)."""
    path = path or os.path.join(LOG_DIR, "jobrank.jsonl")
    if not os.path.exists(path):
        return []
    found = []
    with open(path, "rb") as handle:
        handle.seek(0, os.SEEK_END)
        size = handle.tell()
        handle.seek(max(0, size - 2_000_000))
        for raw in handle.read().splitlines():
            try:
                record = json.loads(raw)
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            if record.get("event") == name:
                found.append(record)
    return list(reversed(found[-limit:]))


def collect() -> dict:
    runs = recent_events("ranking_computed", limit=10)
    return {
        "llm_usage": llm_usage(),
        "llm_cache_entries": llm_cache_entries(),
        "enrichment": enrichment_coverage(),
        "embeddings": embedding_coverage(),
        "ranking_runs": runs,
        "warnings": recent_events("llm_fallback_to_rules", 5) + recent_events("semantic_unavailable", 5)
        + recent_events("notification_failed", 5) + recent_events("catch_up_refresh_failed", 5),
    }


def format_text(data: dict) -> str:
    lines = ["LLM USAGE (every request, cache hits included)"]
    if data["llm_usage"]:
        lines.append(f"  {'task':<16} {'backend':<22} {'requests':>8} {'hit rate':>8} {'fail':>5} "
                     f"{'tokens in':>9} {'tokens out':>10} {'avg ms':>7} {'max ms':>7}")
        for row in data["llm_usage"]:
            lines.append(f"  {row['task']:<16} {row['backend']:<22} {row['requests']:>8} {row['hit_rate']:>8.0%} "
                         f"{row['failures']:>5} {row['prompt_tokens'] or 0:>9} {row['completion_tokens'] or 0:>10} "
                         f"{(row['avg_latency_ms'] or 0):>7.1f} {(row['max_latency_ms'] or 0):>7.1f}")
    else:
        lines.append("  none recorded yet")
    lines.append("\nCACHE")
    for key, count in data["llm_cache_entries"].items():
        lines.append(f"  {key:<40} {count:>6} cached responses")
    e = data["enrichment"]
    lines.append("\nENRICHMENT")
    lines.append(f"  {sum(e['enriched_by'].values())} postings enriched of {e['active_postings']} active "
                 f"({', '.join(f'{k}: {v}' for k, v in e['enriched_by'].items()) or 'none'}); "
                 f"{e['title_contradictions']} contradict their own title")
    lines.append("\nEMBEDDINGS")
    for model, count in data["embeddings"].items() or [("none", 0)]:
        lines.append(f"  {model:<30} {count:>6} vectors")
    lines.append("\nRECENT RANKING RUNS")
    for run in data["ranking_runs"] or []:
        lines.append(f"  {run['ts'][:19]}  {run.get('trigger', ''):<9} profile {run.get('profile')}: "
                     f"{run.get('postings')} postings in {run.get('duration_ms')} ms, top {run.get('top_score')}, "
                     f"semantic {run.get('semantic_model') or 'off'}")
    if not data["ranking_runs"]:
        lines.append("  none logged yet")
    if data["warnings"]:
        lines.append("\nRECENT WARNINGS")
        for w in data["warnings"]:
            detail = w.get("reason") or w.get("error") or ""
            lines.append(f"  {w['ts'][:19]}  {w['event']}  {detail}")
    return "\n".join(lines)
