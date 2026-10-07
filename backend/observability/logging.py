"""Structured logging: one JSON object per line, stdlib only.

Every record carries an `event` name plus typed fields, and whatever
request context is bound (`request_id`, `thread_id`) — set once per chat
turn via `bind()`, inherited by every log call in that turn, including
the ones inside graph nodes. `LOG_FORMAT=console` renders the same fields
as `key=value` for local reading; the data is identical either way.

User-written text is never logged (only its length): a troubleshooting
request can describe internal infrastructure, and the tool args already
say which resources were looked at.
"""

import contextvars
import json
import logging
import sys
from contextlib import contextmanager
from datetime import UTC, datetime

from pydantic_settings import BaseSettings, SettingsConfigDict


class LogSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", env_prefix="LOG_")

    level: str = "INFO"
    format: str = "console"  # "json" | "console"


_context: contextvars.ContextVar[dict] = contextvars.ContextVar("log_context", default={})  # noqa: B039

# Attributes every LogRecord has; anything else on a record came from `extra=`.
# `color_message` is uvicorn's ANSI-coloured duplicate of the message.
_RESERVED = set(logging.LogRecord("", 0, "", 0, "", None, None).__dict__) | {"message", "asctime", "color_message"}


@contextmanager
def bind(**fields):
    """Attach fields to every log record emitted in this context."""
    token = _context.set({**_context.get(), **fields})
    try:
        yield
    finally:
        _context.reset(token)


def _fields(record: logging.LogRecord) -> dict:
    extra = {k: v for k, v in record.__dict__.items() if k not in _RESERVED}
    return {**_context.get(), **extra}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname.lower(),
            "logger": record.name,
            "event": record.getMessage(),
            **_fields(record),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


class ConsoleFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.fromtimestamp(record.created).strftime("%H:%M:%S.%f")[:-3]
        fields = " ".join(f"{k}={v}" for k, v in _fields(record).items())
        line = f"{ts} {record.levelname:<7} {record.getMessage()}" + (f"  {fields}" if fields else "")
        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)
        return line


def configure_logging(settings: LogSettings | None = None) -> None:
    """Route the root logger, and uvicorn's, through one structured handler.

    Called at API import. Uvicorn configures its own loggers before it
    imports the app, so they're re-pointed here rather than left printing
    in a second, unstructured format.
    """
    settings = settings or LogSettings()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter() if settings.format == "json" else ConsoleFormatter())

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(settings.level.upper())

    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        logger.handlers = []
        logger.propagate = True
    # Per-request HTTP chatter from the SDKs would drown the agent's own
    # events. sse_starlette also logs every response chunk at DEBUG, which
    # would put the full reply text in the logs.
    for name in ("httpx", "httpcore", "anthropic", "urllib3", "kubernetes", "aiosqlite", "sse_starlette"):
        logging.getLogger(name).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
