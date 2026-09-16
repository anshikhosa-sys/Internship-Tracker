"""
Text -> unit vectors, locally.

Primary: `BAAI/bge-small-en-v1.5` through fastembed (ONNX runtime, CPU, no
torch). It is what makes "SDE" and "Software Development Engineer" similar.

Fallback: hashed token and character-trigram vectors. No model, no download,
no dependency beyond numpy. It captures shared vocabulary and spelling
variants but not meaning, so it is a strictly weaker signal — which is why the
scorer rescales each backend's similarities separately
(config/scoring.py SEMANTIC_CALIBRATION) rather than pretending they are
comparable.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re

import numpy as np

from jobrank.config import semantic as cfg
from jobrank.log import event, get_logger

log = get_logger(__name__)
_TOKEN = re.compile(r"[a-z0-9+#.]+")


class HashingEmbedder:
    name = cfg.HASHING_NAME

    def __init__(self, dim: int = cfg.HASHING_DIM):
        self.dim = dim

    def _features(self, text: str) -> list[str]:
        tokens = _TOKEN.findall((text or "").lower())
        grams = [f"#{t[i:i + 3]}" for t in tokens for i in range(max(1, len(t) - 2))]
        bigrams = [f"{a}_{b}" for a, b in zip(tokens, tokens[1:])]
        return tokens + grams + bigrams

    def embed(self, texts: list[str]) -> np.ndarray:
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for row, text in enumerate(texts):
            for feature in self._features(text):
                digest = hashlib.blake2b(feature.encode(), digest_size=8).digest()
                index = int.from_bytes(digest[:4], "little") % self.dim
                sign = 1.0 if digest[4] & 1 else -1.0
                out[row, index] += sign
        return _normalize(out)


class ModelEmbedder:
    def __init__(self, model_name: str = cfg.MODEL_NAME):
        from fastembed import TextEmbedding  # imported lazily: optional dependency

        self.name = model_name
        kwargs = {"cache_dir": cfg.MODEL_CACHE_DIR}
        # Once downloaded, load from disk only. Otherwise the hub library
        # re-checks the network on every process start, which stalls on a slow
        # or captive connection.
        if not cfg.ALLOW_MODEL_DOWNLOAD or _model_cached():
            kwargs["local_files_only"] = True
        self._model = TextEmbedding(model_name, **kwargs)

    def embed(self, texts: list[str]) -> np.ndarray:
        vectors = np.array(list(self._model.embed(list(texts), batch_size=cfg.BATCH_SIZE)), dtype=np.float32)
        return _normalize(vectors) if len(vectors) else np.zeros((0, 1), dtype=np.float32)


def _model_cached() -> bool:
    if not os.path.isdir(cfg.MODEL_CACHE_DIR):
        return False
    return any(os.path.isdir(os.path.join(cfg.MODEL_CACHE_DIR, name, "snapshots"))
               for name in os.listdir(cfg.MODEL_CACHE_DIR) if name.startswith("models--"))


def _normalize(matrix: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return matrix / norms


_cached: dict[str, object] = {}


def get_embedder():
    """The best embedder available right now; never raises in "auto" mode."""
    key = f"{cfg.BACKEND}|{cfg.MODEL_NAME}"
    if key in _cached:
        return _cached[key]
    embedder = None
    if cfg.BACKEND in ("auto", "model"):
        try:
            # Quiets the tokenizer's fork warning when used under Flask.
            os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
            embedder = ModelEmbedder()
        except Exception as exc:  # noqa: BLE001 - any failure to load means "fall back"
            if cfg.BACKEND == "model":
                raise
            event(log, "embedding_model_unavailable", level=logging.WARNING, model=cfg.MODEL_NAME,
                  fallback=cfg.HASHING_NAME, reason=f"{type(exc).__name__}: {exc}"[:300])
    if embedder is None:
        embedder = HashingEmbedder()
    _cached[key] = embedder
    return embedder


def reset() -> None:
    _cached.clear()
