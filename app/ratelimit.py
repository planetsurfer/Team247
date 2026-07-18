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
