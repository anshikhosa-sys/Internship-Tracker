"""
Semantic matching: embed the profile and postings locally, compare by cosine.

`similarities_for` never raises for a missing model or index problem; it
returns ({}, None) and the scorer skips the factor, so ranking still works on
keyword and structured factors alone.
"""

from __future__ import annotations

import logging

from jobrank import postings
from jobrank.log import event, get_logger
from jobrank.models import Profile

log = get_logger(__name__)


def similarities_for(profile: Profile, all_postings: list, enrichments: dict | None = None,
                     allow_embed: bool = True) -> tuple[dict[str, float], str | None]:
    """
    Cosine similarity between the résumé and every posting.

    `allow_embed=False` means "compare against the vectors that already exist".
    Embedding is minutes of CPU: one describe run rewrote 692 posting texts,
    and the next page load tried to re-embed all of them inside the request,
    which is a two-minute page. Postings with no vector yet simply have no
    semantic factor that day (invariant 6 — missing data is neutral), and the
    pipeline fills them in on its next run.
    """
    if not profile.semantic_text.strip():
        return {}, None
    try:
        from jobrank.semantic.embedder import get_embedder
        from jobrank.semantic.index import VectorIndex

        embedder = get_embedder()
        index = VectorIndex(embedder)
        try:
            texts = {
                postings.field(p, "id"): postings.text_for_embedding(
                    p, ((enrichments or {}).get(postings.field(p, "id")) or {}).get("data"))
                for p in all_postings
            }
            embedded = index.upsert(texts) if allow_embed else 0
            sims = index.similarities(profile.semantic_text, list(texts))
        finally:
            index.close()
    except Exception as exc:  # noqa: BLE001 - semantic matching is optional by design
        event(log, "semantic_unavailable", level=logging.WARNING, reason=f"{type(exc).__name__}: {exc}"[:300])
        return {}, None
    if embedded:
        event(log, "embeddings_updated", model=embedder.name, embedded=embedded, total=len(texts))
    return sims, embedder.name
