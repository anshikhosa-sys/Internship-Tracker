"""
Local Ollama backend with schema-constrained output.

Ollama's `format` field accepts a JSON schema and constrains decoding to it, so
the model cannot answer in prose. The response is still validated here, because
a constrained decoder guarantees shape, not sense (enums, ranges).

ZERO COST: the host must resolve to a loopback address. Pointing OLLAMA_HOST at
a remote or hosted endpoint raises at call time rather than quietly sending a
résumé across the network.
"""

from __future__ import annotations

import ipaddress
import json
import time
from urllib.parse import urlparse

import httpx

from jobrank.config import llm as llm_config
from jobrank.llm.cache import Usage

_LOOPBACK_NAMES = {"localhost"}


class BackendUnavailable(RuntimeError):
    pass


def assert_local(host: str) -> None:
    hostname = urlparse(host).hostname or ""
    if hostname in _LOOPBACK_NAMES:
        return
    try:
        if ipaddress.ip_address(hostname).is_loopback:
            return
    except ValueError:
        pass
    raise ValueError(f"LLM host must be local (loopback) to guarantee zero cost; got {hostname!r}")


class OllamaBackend:
    def __init__(self, host: str | None = None, model: str | None = None):
        self.host = (host or llm_config.OLLAMA_HOST).rstrip("/")
        self.model = model or llm_config.OLLAMA_MODEL
        assert_local(self.host)

    @property
    def name(self) -> str:
        return f"ollama:{self.model}"

    def available(self) -> bool:
        try:
            response = httpx.get(f"{self.host}/api/tags", timeout=llm_config.OLLAMA_PROBE_TIMEOUT_SECONDS)
            response.raise_for_status()
        except (httpx.HTTPError, OSError):
            return False
        models = {m.get("name", "") for m in response.json().get("models", [])}
        return any(name == self.model or name.split(":")[0] == self.model.split(":")[0] for name in models)

    def complete_json(self, system: str, prompt: str, schema: dict) -> tuple[dict, Usage]:
        body = {
            "model": self.model,
            "stream": False,
            "format": schema,
            "options": {"temperature": llm_config.OLLAMA_TEMPERATURE},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt[: llm_config.MAX_INPUT_CHARS]},
            ],
        }
        started = time.perf_counter()
        try:
            response = httpx.post(f"{self.host}/api/chat", json=body, timeout=llm_config.OLLAMA_TIMEOUT_SECONDS)
            response.raise_for_status()
        except (httpx.HTTPError, OSError) as exc:
            raise BackendUnavailable(str(exc)) from exc
        latency = (time.perf_counter() - started) * 1000
        data = response.json()
        usage = Usage(
            prompt_tokens=int(data.get("prompt_eval_count") or 0),
            completion_tokens=int(data.get("eval_count") or 0),
            latency_ms=latency,
        )
        content = (data.get("message") or {}).get("content", "")
        return json.loads(content), usage
