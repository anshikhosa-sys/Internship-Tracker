"""
Response cache and usage ledger for structured extraction.

Keyed by sha256(task, task version, backend, input). Including the backend
means a rule-based answer cached before Ollama was installed does not block
the better model answer afterwards; including the task version means a changed
prompt or schema never reuses an answer to a different question.

Every request — hit or miss, model or rules — is recorded in `usage`, which is
what the stats view reads for token counts, latency and cache hit rate.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from dataclasses import dataclass

from jobrank.config import llm as llm_config

_SCHEMA = """
CREATE TABLE IF NOT EXISTS responses (
    key TEXT PRIMARY KEY,
    task TEXT NOT NULL,
    backend TEXT NOT NULL,
    response TEXT NOT NULL,
    created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    task TEXT NOT NULL,
    backend TEXT NOT NULL,
    cache_hit INTEGER NOT NULL,
    ok INTEGER NOT NULL,
    prompt_tokens INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    latency_ms REAL NOT NULL DEFAULT 0,
    error TEXT
);
CREATE INDEX IF NOT EXISTS idx_usage_task ON usage(task, ts);
"""


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: float = 0.0


def content_hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def cache_key(task: str, backend: str, text: str) -> str:
    version = llm_config.TASK_VERSIONS.get(task, 0)
    return content_hash(f"{task}|v{version}|{backend}|{content_hash(text)}")


def connect(path: str | None = None) -> sqlite3.Connection:
    path = path or llm_config.CACHE_PATH
    if os.path.dirname(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(_SCHEMA)
    return conn


def get(conn: sqlite3.Connection, key: str) -> dict | None:
    row = conn.execute("SELECT response FROM responses WHERE key = ?", (key,)).fetchone()
    return json.loads(row[0]) if row else None


def put(conn: sqlite3.Connection, key: str, task: str, backend: str, response: dict) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO responses (key, task, backend, response, created) VALUES (?, ?, ?, ?, ?)",
        (key, task, backend, json.dumps(response), time.time()),
    )
    conn.commit()


def record(conn: sqlite3.Connection, task: str, backend: str, cache_hit: bool, ok: bool,
           usage: Usage | None = None, error: str | None = None) -> None:
    usage = usage or Usage()
    conn.execute(
        "INSERT INTO usage (ts, task, backend, cache_hit, ok, prompt_tokens, completion_tokens, latency_ms, error)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (time.time(), task, backend, int(cache_hit), int(ok), usage.prompt_tokens,
         usage.completion_tokens, usage.latency_ms, error),
    )
    conn.commit()


def stats(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """
        SELECT task, backend, COUNT(*) AS requests, SUM(cache_hit) AS hits,
               SUM(CASE WHEN ok = 0 THEN 1 ELSE 0 END) AS failures,
               SUM(prompt_tokens) AS prompt_tokens, SUM(completion_tokens) AS completion_tokens,
               AVG(CASE WHEN cache_hit = 0 THEN latency_ms END) AS avg_latency_ms,
               MAX(CASE WHEN cache_hit = 0 THEN latency_ms END) AS max_latency_ms
        FROM usage GROUP BY task, backend ORDER BY task, backend
        """
    ).fetchall()
    keys = ["task", "backend", "requests", "hits", "failures", "prompt_tokens",
            "completion_tokens", "avg_latency_ms", "max_latency_ms"]
    out = []
    for row in rows:
        item = dict(zip(keys, row))
        item["hit_rate"] = (item["hits"] or 0) / item["requests"] if item["requests"] else 0.0
        out.append(item)
    return out
