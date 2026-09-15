"""
Vector store: SQLite rows + exact cosine search in numpy.

WHY NOT FAISS OR CHROMA
-----------------------
The corpus is a few thousand postings × 384 dimensions ≈ 6 MB. Exact search
over that is one matrix multiply — about a millisecond — so an approximate
index buys nothing and costs recall. SQLite is already the storage layer, adds
no native dependency, and keeps vectors next to the text hash that produced
them, so a changed posting re-embeds and an unchanged one never does.

The tradeoff is scale: exact search is O(n) per query and loads every vector
into memory. Past roughly 10^6 vectors, or with many concurrent queries, this
becomes FAISS (IVF/HNSW) or sqlite-vec with an ANN index. The interface below
(`upsert`, `similarities`) is the seam where that swap would happen.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3

import numpy as np

from jobrank.config import semantic as cfg

_SCHEMA = """
CREATE TABLE IF NOT EXISTS vectors (
    key TEXT NOT NULL,
    model TEXT NOT NULL,
    text_hash TEXT NOT NULL,
    dim INTEGER NOT NULL,
    vector BLOB NOT NULL,
    PRIMARY KEY (key, model)
);
"""


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]


class VectorIndex:
    def __init__(self, embedder, path: str | None = None):
        self.embedder = embedder
        self.path = path or cfg.VECTOR_DB_PATH
        if os.path.dirname(self.path):
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.executescript(_SCHEMA)

    def close(self) -> None:
        self.conn.close()

    def upsert(self, items: dict[str, str]) -> int:
        """Embed any key whose text changed. Returns how many were (re)embedded."""
        if not items:
            return 0
        model = self.embedder.name
        existing = dict(self.conn.execute(
            "SELECT key, text_hash FROM vectors WHERE model = ?", (model,)).fetchall())
        stale = [(k, t) for k, t in items.items() if existing.get(k) != _hash(t)]
        for start in range(0, len(stale), cfg.BATCH_SIZE):
            batch = stale[start:start + cfg.BATCH_SIZE]
            vectors = self.embedder.embed([t for _, t in batch])
            self.conn.executemany(
                "INSERT OR REPLACE INTO vectors (key, model, text_hash, dim, vector) VALUES (?, ?, ?, ?, ?)",
                [(k, model, _hash(t), vectors.shape[1], vectors[i].astype(np.float32).tobytes())
                 for i, (k, t) in enumerate(batch)],
            )
        self.conn.commit()
        return len(stale)

    def vectors(self, keys: list[str]) -> dict[str, np.ndarray]:
        model = self.embedder.name
        out: dict[str, np.ndarray] = {}
        wanted = set(keys)
        for key, blob in self.conn.execute("SELECT key, vector FROM vectors WHERE model = ?", (model,)):
            if key in wanted:
                out[key] = np.frombuffer(blob, dtype=np.float32)
        return out

    def similarities(self, query_text: str, keys: list[str]) -> dict[str, float]:
        """Cosine similarity of the query to each stored key (vectors are unit-length)."""
        stored = self.vectors(keys)
        if not stored:
            return {}
        query = self.embedder.embed([query_text])[0]
        order = list(stored)
        matrix = np.vstack([stored[k] for k in order])
        scores = matrix @ query
        return {k: float(s) for k, s in zip(order, scores)}
