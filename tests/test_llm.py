import os
import re

import pytest

from jobrank import llm
from jobrank.config import llm as llm_cfg
from jobrank.llm import cache, schema
from jobrank.llm.cache import Usage
from jobrank.llm.ollama import BackendUnavailable, assert_local

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCHEMA = {
    "type": "object",
    "required": ["name", "count"],
    "additionalProperties": False,
    "properties": {
        "name": {"type": "string"},
        "count": {"type": "integer", "minimum": 0},
        "kind": {"type": ["string", "null"], "enum": ["a", "b", None]},
        "tags": {"type": "array", "maxItems": 2, "items": {"type": "string"}},
    },
}


class TestSchema:
    def test_valid(self):
        schema.validate({"name": "x", "count": 1, "kind": None, "tags": ["t"]}, SCHEMA)

    def test_collects_every_problem(self):
        problems = schema.errors({"count": -1, "kind": "c", "tags": ["a", 2, "c"], "extra": 1}, SCHEMA)
        joined = " | ".join(problems)
        for fragment in ["missing required 'name'", "minimum", "not in", "more than 2", "expected string",
                         "unexpected property 'extra'"]:
            assert fragment in joined

    def test_bool_is_not_integer(self):
        assert schema.errors({"name": "x", "count": True}, SCHEMA)


class FakeBackend:
    name = "fake:model"

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def complete_json(self, system, prompt, schema_):
        self.calls += 1
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response, Usage(prompt_tokens=100, completion_tokens=20, latency_ms=12.5)


def _run(text="doc", rules=None, backend=None, monkeypatch=None):
    if backend is not None:
        monkeypatch.setattr(llm, "_model_backend", lambda: backend)
    calls = {"n": 0}

    def fallback(t):
        calls["n"] += 1
        return {"name": "rules", "count": 0}

    result = llm.structured("resume_extract", text, SCHEMA, "sys", lambda t: t, rules or fallback)
    return result, calls


def test_rules_result_is_cached_by_content_hash():
    first, calls = _run("same text")
    second, calls2 = _run("same text")
    assert first.extractor == "rules" and not first.cache_hit
    assert second.cache_hit and calls2["n"] == 0


def test_model_output_is_validated_cached_and_counted(monkeypatch):
    backend = FakeBackend([{"name": "model", "count": 3}])
    first, calls = _run("resume A", backend=backend, monkeypatch=monkeypatch)
    second, _ = _run("resume A", backend=backend, monkeypatch=monkeypatch)
    assert first.data["name"] == "model" and first.extractor == "fake:model"
    assert second.cache_hit and backend.calls == 1 and calls["n"] == 0

    conn = cache.connect()
    rows = {r["backend"]: r for r in cache.stats(conn)}
    assert rows["fake:model"]["requests"] == 2
    assert rows["fake:model"]["hit_rate"] == 0.5
    assert rows["fake:model"]["prompt_tokens"] == 100


def test_invalid_model_output_is_retried_then_falls_back(monkeypatch):
    backend = FakeBackend([{"name": 1}, {"wrong": True}])
    result, calls = _run("resume B", backend=backend, monkeypatch=monkeypatch)
    assert backend.calls == 1 + llm_cfg.SCHEMA_RETRIES
    assert result.extractor == "rules" and calls["n"] == 1


def test_unavailable_model_falls_back_without_raising(monkeypatch):
    backend = FakeBackend([BackendUnavailable("connection refused")])
    result, _ = _run("resume C", backend=backend, monkeypatch=monkeypatch)
    assert result.extractor == "rules"


def test_rules_backend_never_probes_network(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("network used")
    monkeypatch.setattr("httpx.get", boom)
    monkeypatch.setattr("httpx.post", boom)
    result, _ = _run("resume D")
    assert result.extractor == "rules"


def test_cache_key_separates_backend_task_version(monkeypatch):
    a = cache.cache_key("resume_extract", "rules", "x")
    assert a != cache.cache_key("resume_extract", "ollama:m", "x")
    monkeypatch.setitem(llm_cfg.TASK_VERSIONS, "resume_extract", 99)
    assert a != cache.cache_key("resume_extract", "rules", "x")


@pytest.mark.parametrize("host,ok", [
    ("http://127.0.0.1:11434", True),
    ("http://localhost:11434", True),
    ("http://[::1]:11434", True),
    ("https://api.example.com", False),
    ("http://10.0.0.5:11434", False),
])
def test_llm_host_must_be_loopback(host, ok):
    if ok:
        assert_local(host)
    else:
        with pytest.raises(ValueError):
            assert_local(host)


def test_zero_cost_no_paid_sdk_or_endpoint_anywhere():
    """Nothing in the shipped code can reach a paid model API."""
    banned = re.compile(r"^\s*(import|from)\s+(anthropic|openai|cohere|google\.generativeai|mistralai)\b|"
                        r"api\.openai\.com|api\.anthropic\.com|generativelanguage\.googleapis", re.M)
    offenders = []
    for folder, _, files in os.walk(os.path.join(ROOT, "jobrank")):
        for name in files:
            if name.endswith(".py"):
                path = os.path.join(folder, name)
                with open(path, encoding="utf-8") as handle:
                    if banned.search(handle.read()):
                        offenders.append(os.path.relpath(path, ROOT))
    with open(os.path.join(ROOT, "requirements.txt"), encoding="utf-8") as handle:
        requirements = handle.read().lower()
    assert offenders == []
    assert not re.search(r"^(anthropic|openai|cohere|mistralai)\b", requirements, re.M)
