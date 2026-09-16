"""
Structured extraction with a local model when available, rules otherwise.

    result = structured(task="resume_extract", text=resume_text, schema=SCHEMA,
                        system=PROMPT, build_prompt=fn, rules=fallback_fn)

Order of operations:
  1. Cache lookup for (task, version, backend, text). A hit never calls anything.
  2. Model call, if the configured backend is reachable. Output must pass the
     schema; one retry, then fall through.
  3. Rule-based fallback — deterministic, free, always available.

The result carries which extractor produced it, so a profile built by rules is
visibly different from one built by a model.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Callable

from jobrank.config import llm as llm_config
from jobrank.llm import cache
from jobrank.llm.ollama import BackendUnavailable, OllamaBackend
from jobrank.llm.schema import SchemaError, validate
from jobrank.log import event, get_logger

log = get_logger(__name__)

RULES = "rules"
_backend_probe: dict[str, object] = {}


@dataclass
class Extraction:
    data: dict
    extractor: str
    cache_hit: bool


def _model_backend() -> OllamaBackend | None:
    if llm_config.BACKEND == RULES:
        return None
    key = f"{llm_config.OLLAMA_HOST}|{llm_config.OLLAMA_MODEL}"
    if key not in _backend_probe:
        backend = OllamaBackend()
        _backend_probe[key] = backend if backend.available() else None
        if _backend_probe[key] is None:
            event(log, "llm_backend_unavailable", level=logging.INFO, backend=backend.name,
                  fallback=RULES)
    backend = _backend_probe[key]
    if backend is None and llm_config.BACKEND == "ollama":
        raise BackendUnavailable(f"Ollama model {llm_config.OLLAMA_MODEL} is not reachable at {llm_config.OLLAMA_HOST}")
    return backend  # type: ignore[return-value]


def model_available() -> bool:
    """Whether structured tasks will go to a model (False means rules)."""
    try:
        return _model_backend() is not None
    except BackendUnavailable:
        return False


def reset_probe() -> None:
    _backend_probe.clear()


def structured(task: str, text: str, schema: dict, system: str,
               build_prompt: Callable[[str], str], rules: Callable[[str], dict],
               conn=None) -> Extraction:
    own_conn = conn is None
    conn = conn or cache.connect()
    try:
        backend = _model_backend()
        if backend is not None:
            result = _via_model(conn, backend, task, text, schema, system, build_prompt)
            if result is not None:
                return result
        return _via_rules(conn, task, text, schema, rules)
    finally:
        if own_conn:
            conn.close()


def _via_model(conn, backend, task, text, schema, system, build_prompt) -> Extraction | None:
    key = cache.cache_key(task, backend.name, text)
    hit = cache.get(conn, key)
    if hit is not None:
        cache.record(conn, task, backend.name, cache_hit=True, ok=True)
        return Extraction(hit, backend.name, True)

    prompt = build_prompt(text)
    last_error = ""
    for attempt in range(1 + llm_config.SCHEMA_RETRIES):
        try:
            data, usage = backend.complete_json(system, prompt, schema)
            validate(data, schema)
        except (SchemaError, json.JSONDecodeError) as exc:
            last_error = f"invalid output: {exc}"
            cache.record(conn, task, backend.name, cache_hit=False, ok=False, error=last_error)
            prompt = f"{build_prompt(text)}\n\nYour previous answer was invalid ({exc}). Return JSON matching the schema exactly."
            continue
        except BackendUnavailable as exc:
            last_error = str(exc)
            cache.record(conn, task, backend.name, cache_hit=False, ok=False, error=last_error)
            break
        cache.put(conn, key, task, backend.name, data)
        cache.record(conn, task, backend.name, cache_hit=False, ok=True, usage=usage)
        event(log, "llm_extraction", task=task, backend=backend.name, attempt=attempt + 1,
              prompt_tokens=usage.prompt_tokens, completion_tokens=usage.completion_tokens,
              latency_ms=round(usage.latency_ms, 1))
        return Extraction(data, backend.name, False)

    event(log, "llm_fallback_to_rules", level=logging.WARNING, task=task, backend=backend.name, reason=last_error)
    return None


def _via_rules(conn, task, text, schema, rules) -> Extraction:
    key = cache.cache_key(task, RULES, text)
    hit = cache.get(conn, key)
    if hit is not None:
        cache.record(conn, task, RULES, cache_hit=True, ok=True)
        return Extraction(hit, RULES, True)
    started = time.perf_counter()
    data = rules(text)
    validate(data, schema)
    cache.put(conn, key, task, RULES, data)
    cache.record(conn, task, RULES, cache_hit=False, ok=True,
                 usage=cache.Usage(latency_ms=(time.perf_counter() - started) * 1000))
    return Extraction(data, RULES, False)
