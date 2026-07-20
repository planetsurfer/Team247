"""Health check router: GET /api/health.

Returns {ok: true, roles_count: <int>, ka_warmed: <bool>}. Reads roles_count
from the SQLite catalog at settings.APP_DB_PATH via a short-lived connection
(0 if the DB or table is missing). ka_warmed probes framework._ka_index(),
guarded to False on any failure. Handlers are sync (`def`) so the sqlite3 +
framework calls run in FastAPI's threadpool.
"""
from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Request

from app import settings
from app.auth import is_authenticated

router = APIRouter()


def _roles_count() -> int:
    """Count rows in `roles`; return 0 if the DB or table is missing."""
    try:
        conn = sqlite3.connect(settings.APP_DB_PATH, timeout=2.0)
    except (sqlite3.Error, OSError):
        return 0
    try:
        cur = conn.execute("SELECT COUNT(*) FROM roles")
        row = cur.fetchone()
        return int(row[0]) if row else 0
    except sqlite3.Error:
        # roles table not yet created (pre-seed) — treat as empty catalog.
        return 0
    finally:
        try:
            conn.close()
        except sqlite3.Error:
            pass


def _ka_warmed() -> bool:
    """True if framework's K&A index is built (non-empty). False on any failure."""
    try:
        import framework  # noqa: WPS433 — root module, on PYTHONPATH at runtime
    except Exception:
        return False
    try:
        index = framework._ka_index()
        return bool(index)
    except Exception:
        return False


@router.get("/api/health")
def health() -> dict:
    return {
        "ok": True,
        "roles_count": _roles_count(),
        "ka_warmed": _ka_warmed(),
    }


@router.get("/api/auth/status")
def auth_status(request: Request) -> dict:
    """Open (unauthenticated) probe the UI polls to decide whether to show the
    beta-access gate: {beta_auth, authenticated}. Cheap — no DB seed reads, no
    LLM. `authenticated` reflects whatever Authorization header (if any) came
    with this request, without mutating last_used_at (see app.auth.is_authenticated).
    """
    return {
        "beta_auth": settings.BETA_AUTH,
        "authenticated": is_authenticated(request) if settings.BETA_AUTH else True,
    }
