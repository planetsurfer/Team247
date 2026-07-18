"""slowapi rate limiting for LLM-driving endpoints.

- `limiter` is a process-global `slowapi.Limiter` keyed by client IP, with a
  default per-IP limit of `settings.RATE_LIMIT_PER_MIN` requests/minute.
- `rate_limited` is a decorator/dependency applying the same per-minute limit
  to individual LLM-driving endpoints (intake/answer, team/recommend, team/wire,
  team/render, catalog/{id}/distill, .../verify).
- `setup_ratelimit(app)` wires slowapi's middleware + exception handler onto a
  FastAPI app.
"""
from __future__ import annotations

from typing import Any

from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from app import settings

# Per-IP limiter. NO global default — limits apply only via the `@rate_limited`
# decorator on LLM-driving endpoints (intake/answer, recommend, wire, render,
# distill, verify). Catalog browse / health / SPA are unlimited.
limiter = Limiter(
    key_func=lambda req: (req.client.host if req.client else "anon"),
    default_limits=[],
)

# Decorator / dependency for LLM-driving endpoints. Applied per-endpoint so the
# default_limits above remains the global fallback.
rate_limited = limiter.limit(f"{settings.RATE_LIMIT_PER_MIN}/minute")


def setup_ratelimit(app: Any) -> None:
    """Wire slowapi onto a FastAPI app: state.limiter + exception handler + middleware.

    Must be called once at app construction time, before adding routes that use
    the `rate_limited` decorator / dependency.
    """
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.add_middleware(SlowAPIMiddleware)


# --- Reliable per-IP rate-limit dependency (used on LLM-driving endpoints) ---
# A simple sliding 60s window per client IP. Used via `Depends(llm_rate_limit)`.
# (slowapi's per-endpoint decorator requires a `request: Request` param on every
# endpoint + has version-specific quirks; this dependency is simpler and robust.)
import time
import collections
from fastapi import HTTPException, Request

_hits: dict = collections.defaultdict(list)


def llm_rate_limit(request: Request):
    """Per-IP sliding 60s window. 429 with Retry-After once RATE_LIMIT_PER_MIN is hit."""
    ip = request.client.host if request.client else "anon"
    now = time.time()
    window = [t for t in _hits[ip] if t > now - 60]
    if len(window) >= settings.RATE_LIMIT_PER_MIN:
        raise HTTPException(
            status_code=429,
            detail="rate limit exceeded",
            headers={"Retry-After": "60"},
        )
    window.append(now)
    _hits[ip] = window
