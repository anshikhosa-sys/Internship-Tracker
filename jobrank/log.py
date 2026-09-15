"""
Structured logging: one JSON object per line.

`get_logger(name)` returns a stdlib logger. Pass structured fields with
`extra={"fields": {...}}`, or use `event(logger, "name", **fields)`. Lines go to
`logs/jobrank.jsonl` (rotated) and, at WARNING and above, to stderr in a
readable form. CLI entry points print user-facing output; libraries log.
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import os
import sys
from datetime import datetime, timezone

LOG_DIR = os.environ.get("JOBRANK_LOG_DIR", "logs")
_configured = False


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname.lower(),
            "logger": record.name,
            "event": record.getMessage(),
        }
        fields = getattr(record, "fields", None)
        if fields:
            payload.update(fields)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, ensure_ascii=False)


class ConsoleFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        fields = getattr(record, "fields", None) or {}
        tail = " ".join(f"{k}={v}" for k, v in fields.items())
        return f"[{record.levelname.lower()}] {record.name}: {record.getMessage()} {tail}".rstrip()


def _file_handler(filename: str, level: int) -> logging.Handler | None:
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(
            os.path.join(LOG_DIR, filename), maxBytes=10_000_000, backupCount=3, encoding="utf-8"
        )
    except OSError:
        return None
    handler.setLevel(level)
    handler.setFormatter(JsonFormatter())
    return handler


def configure(level: int = logging.INFO) -> None:
    global _configured
    if _configured:
        return
    root = logging.getLogger("jobrank")
    root.setLevel(level)
    root.propagate = False
    handler = _file_handler("jobrank.jsonl", level)
    if handler:
        root.addHandler(handler)
    console = logging.StreamHandler(sys.stderr)
    console.setLevel(logging.WARNING)
    console.setFormatter(ConsoleFormatter())
    root.addHandler(console)

    # Scoring decisions are high-volume; they get their own file so the main
    # log stays readable.
    scoring = logging.getLogger("jobrank.scoring.decisions")
    scoring.propagate = False
    scoring.setLevel(logging.INFO)
    scoring_handler = _file_handler("scoring.jsonl", logging.INFO)
    if scoring_handler:
        scoring.addHandler(scoring_handler)
    _configured = True


def get_logger(name: str) -> logging.Logger:
    configure()
    if not name.startswith("jobrank"):
        name = f"jobrank.{name}"
    return logging.getLogger(name)


def event(logger: logging.Logger, name: str, level: int = logging.INFO, **fields) -> None:
    logger.log(level, name, extra={"fields": fields})
