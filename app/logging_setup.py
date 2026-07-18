"""structlog JSON logging setup for the AgentProof production app.

- `configure_logging()` (idempotent) routes everything through structlog with a
  JSONRenderer writing to stdout.
- `log_llm(...)` emits a structured "llm_call" event for every LLM call.
- `log_slow_query(sql, latency_ms)` emits a "slow_query" warning when latency > 100ms.
- `request_logging_middleware(app)` is a Starlette/FastAPI middleware factory
  logging method/path/status/latency_ms for every request.
"""
from __future__ import annotations

import logging
import time
from typing import Any

import structlog

_CONFIGURED = False


def configure_logging() -> None:
    """Idempotently configure structlog for JSON output to stdout.

    - Builds a structlog processor chain ending in JSONRenderer.
    - Routes stdlib logging through structlog so `logging.getLogger(...)` calls
      (including those from uvicorn / slowapi / fastapi) come out as JSON too.
    - Safe to call multiple times; only the first call has effect.
    """
    global _CONFIGURED
    if _CONFIGURED:
        return

    processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
        structlog.processors.JSONRenderer(),
    ]

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        logger_factory=structlog.PrintLoggerFactory(file=None),  # stdout
        cache_logger_on_first_use=True,
    )

    # Route stdlib logging through structlog so library logs (uvicorn, slowapi,
    # fastapi) also come out as JSON on stdout. Install a stdlib handler that
    # re-emits through structlog's ProcessorFormatter.
    stdlib_processor = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=[
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.add_log_level,
        ],
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.JSONRenderer(),
        ],
    )

    root = logging.getLogger()
    # Clear any pre-existing handlers so we own the output stream.
    for h in list(root.handlers):
        root.removeHandler(h)
    lib_handler = logging.StreamHandler()  # stdout
    lib_handler.setFormatter(stdlib_processor)
    root.addHandler(lib_handler)
    root.setLevel(logging.INFO)

    _CONFIGURED = True


_log = structlog.get_logger("app")


def log_llm(
    purpose: str,
    model: str,
    attempt: int,
    latency_ms: int | float,
    status: str,
    **extra: Any,
) -> None:
    """Emit a structured `llm_call` event.

    Fields: event="llm_call", purpose, model, attempt, latency_ms, status, plus any
    extra kwargs (e.g. tokens_in, tokens_out, error).
    """
    _log.info(
        "llm_call",
        purpose=purpose,
        model=model,
        attempt=attempt,
        latency_ms=latency_ms,
        status=status,
        **extra,
    )


def log_slow_query(sql: str, latency_ms: int | float) -> None:
    """Emit a `slow_query` warning when a DB query takes longer than 100ms."""
    if latency_ms <= 100:
        return
    _log.warning(
        "slow_query",
        latency_ms=latency_ms,
        sql=sql,
    )


def request_logging_middleware(app: Any) -> Any:
    """Return a Starlette BaseHTTPMiddleware dispatch function.

    Registered via `app.middleware("http")(request_logging_middleware(app))`.
    Logs {method, path, status_code, latency_ms} for every request, and re-raises
    exceptions (status logged as 500 on unhandled errors).
    """
    async def dispatch(request, call_next):
        method = request.method
        path = request.url.path
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            latency_ms = int((time.perf_counter() - started) * 1000)
            _log.error(
                "request",
                method=method,
                path=path,
                status_code=500,
                latency_ms=latency_ms,
                error="unhandled_exception",
            )
            raise
        latency_ms = int((time.perf_counter() - started) * 1000)
        _log.info(
            "request",
            method=method,
            path=path,
            status_code=response.status_code,
            latency_ms=latency_ms,
        )
        return response

    return dispatch
