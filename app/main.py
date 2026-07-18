"""FastAPI application entrypoint for the AgentProof production app.

Launch with the repo root on PYTHONPATH (so the flat root modules — framework,
config, teamspec, … — import without sys.path shimming):

    PYTHONPATH=. uvicorn app.main:app --reload

This module only wires the Stage 0 skeleton: logging, rate limiting, the health
router, the SPA root, and a bootstrap-only startup handler. Catalog/intake/team
routers are added in later stages.
"""
from __future__ import annotations

import pathlib

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from app import settings, db
from app.logging_setup import configure_logging
from app.ratelimit import setup_ratelimit
from app.routers import health

# The SPA shell lives one directory up from this file (repo-root demo.html),
# and still opens standalone when double-clicked. Serving it through the app
# just adds a `GET /` that returns its text.
_DEMO_HTML = pathlib.Path(__file__).resolve().parent.parent / "demo.html"


def create_app() -> FastAPI:
    """Construct the FastAPI app: logging, rate limiting, health, SPA root.

    Importable without side effects beyond `configure_logging()` (idempotent)
    and module-level singleton construction in slowapi. Safe to call once.
    """
    configure_logging()

    app = FastAPI(title="AgentProof")

    setup_ratelimit(app)

    # Request logging middleware: best-effort. `request_logging_middleware` is a
    # factory returning an ASGI middleware callable, so we wrap it as a pure
    # ASGI middleware (Starlette supports `add_middleware` with a bare class or
    # a function taking the app). Wrap in try/except so a signature mismatch
    # or import failure never crashes startup.
    try:
        from app.logging_setup import request_logging_middleware

        app.middleware("http")(request_logging_middleware(app))
    except Exception:  # noqa: BLE001 — startup must never depend on middleware wiring
        pass

    app.include_router(health.router)

    # Serve the SPA shell at `/`. demo.html stays a standalone-openable file;
    # this just exposes it through the running app so the API + SPA share one
    # origin (the SPA's fetch('/api/…') calls need same-origin in real deploys).
    if _DEMO_HTML.exists():
        @app.get("/", include_in_schema=False)
        def _spa_root() -> HTMLResponse:
            return HTMLResponse(_DEMO_HTML.read_text())

    @app.on_event("startup")
    def _on_startup() -> None:  # noqa: WPS430 — handler, intentionally module-local
        """Bootstrap-only startup: create the schema + warm the K&A index.

        The full deterministic catalog seed (1910 roles) is run out-of-band
        via `python -m app.seed_catalog`; we do NOT run it here (it warms
        _ka_index itself). Here we only (a) ensure the schema exists so
        /api/health can read `roles` without error, and (b) warm the
        process-global `_ka_index()` lru_cache so the first request isn't
        paying the 15–30s build. Both are best-effort: a failure logs but
        does not block the server from accepting traffic.
        """
        try:
            db.bootstrap()
        except Exception:  # noqa: BLE001 — schema bootstrap must not crash uvicorn
            pass
        try:
            import framework  # noqa: WPS433 — root module, on PYTHONPATH at runtime
            framework._ka_index()  # warms the process-global lru_cache
        except Exception:  # noqa: BLE001 — K&A warmup is best-effort
            pass

    return app


app = create_app()
