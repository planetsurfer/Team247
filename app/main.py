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
from fastapi.staticfiles import StaticFiles

from app import settings, db
from app.logging_setup import configure_logging
from app.ratelimit import setup_ratelimit
from app.routers import catalog, feedback, gallery, health, intake, team

# The SPA shell lives one directory up from this file (repo-root demo.html),
# and still opens standalone when double-clicked. Serving it through the app
# just adds a `GET /` that returns its text.
_DEMO_HTML = pathlib.Path(__file__).resolve().parent.parent / "demo.html"

# The React + Vite chat frontend builds its bundle into `app/static/`
# (`web/` → `npm run build`). When present, FastAPI serves it at `/` and the
# hashed assets under `/assets`. The legacy dark-theme SPA stays at `/legacy`.
_STATIC = pathlib.Path(__file__).resolve().parent / "static"
_STATIC_INDEX = _STATIC / "index.html"
_STATIC_ASSETS = _STATIC / "assets"


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
    app.include_router(catalog.router)
    app.include_router(intake.router)
    app.include_router(team.router)
    app.include_router(feedback.router)
    app.include_router(gallery.router)

    # Serve the SPA at `/`. Prefer the built Vite bundle (`app/static/`,
    # produced by `web/` → `npm run build`); fall back to the legacy demo.html
    # so the app still works without a frontend build. Both expose the SPA
    # through the running app so the SPA's fetch('/api/…') calls are same-origin.
    def _spa_html_text() -> str:
        if _STATIC_INDEX.exists():
            return _STATIC_INDEX.read_text()
        return _DEMO_HTML.read_text() if _DEMO_HTML.exists() else ""

    @app.get("/", include_in_schema=False)
    def _spa_root() -> HTMLResponse:
        return HTMLResponse(_spa_html_text())

    # Vite emits hashed JS/CSS under /assets/; mount them when a build exists.
    if _STATIC_ASSETS.is_dir():
        app.mount("/assets", StaticFiles(directory=str(_STATIC_ASSETS)), name="assets")

    # Keep the legacy dark-theme SPA accessible at /legacy.
    if _DEMO_HTML.exists():
        @app.get("/legacy", include_in_schema=False)
        def _legacy() -> HTMLResponse:
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
