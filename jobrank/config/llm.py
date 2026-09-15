"""
LLM backend settings.

Zero cost is a hard rule: the only model backend is a LOCAL Ollama server, and
its host must be a loopback address (enforced in jobrank/llm/ollama.py). With
no server running, every task falls back to deterministic rule-based
extraction. There is no setting that can point this at a paid API.
"""

# "auto": use Ollama when it answers, rules otherwise. "rules": never call a
# model. "ollama": require the model and fail loudly without it.
BACKEND = "auto"

OLLAMA_HOST = "http://127.0.0.1:11434"
OLLAMA_MODEL = "llama3.1:8b"
OLLAMA_TIMEOUT_SECONDS = 120
OLLAMA_PROBE_TIMEOUT_SECONDS = 1.5
# Structured output needs determinism, not creativity.
OLLAMA_TEMPERATURE = 0.0
# One retry when the model returns JSON that fails the schema.
SCHEMA_RETRIES = 1
# Characters of input sent to the model; longer text is truncated.
MAX_INPUT_CHARS = 24000

CACHE_PATH = ".cache/llm.db"

# Bump when a task's prompt or schema changes, so stale cached answers are not
# reused for a different question.
TASK_VERSIONS = {
    "resume_extract": 1,
    "posting_enrich": 1,
    "resume_tailor": 1,
}
