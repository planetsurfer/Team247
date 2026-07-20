"""Catalog router: browse the 1910-role verified catalogue + per-role cards.

Endpoints (all sync `def` handlers — DB + lazy-distill run in FastAPI's
threadpool; LLM calls dominate latency, DB is local sqlite3):

    GET  /api/catalog                       ?sector&track&q&page=1&size=20
    GET  /api/catalog/sectors
    GET  /api/catalog/{role_id}
    GET  /api/catalog/{role_id}/card         (cold roles distill synchronously)
    GET  /api/catalog/{role_id}/battery
    POST /api/catalog/{role_id}/distill      (beta-token-gated; admin token also accepted)

All reads delegate to `app.services.card_service`; this router is a thin
HTTP adapter — validation, 404s, beta/admin gating. The router is registered by
the orchestrator in `app.main` (do NOT register it here).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.auth import require_beta
from app.services import card_service

router = APIRouter()


def _parse_role_id(role_id: str) -> int:
    """Coerce the path role_id to int; 422 on anything non-numeric."""
    try:
        return int(role_id)
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="role_id must be an integer",
        )


@router.get("/api/catalog")
def list_roles(
    sector: str | None = Query(default=None),
    track: str | None = Query(default=None),
    q: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
) -> dict:
    """Paginated catalog list with FTS5 search (q) and sector/track filters."""
    return card_service.list_roles(sector=sector, track=track, q=q, page=page, size=size)


@router.get("/api/catalog/sectors")
def list_sectors() -> list:
    """[{sector, n_roles}] ordered by role count desc."""
    return card_service.sectors()


@router.get("/api/catalog/{role_id}")
def get_role(role_id: str) -> dict:
    """Single role row from the catalog; 404 if unknown."""
    rid = _parse_role_id(role_id)
    role = card_service.get_role(rid)
    if role is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="role not found")
    return role


@router.get("/api/catalog/{role_id}/card")
def get_card(role_id: str) -> dict:
    """Full card DTO. Cold roles distill synchronously here (200, ~1-3s first
    hit, instant after). The 202 async-spinner is a later stage — keep 200.
    404 if the role itself is unknown.
    """
    rid = _parse_role_id(role_id)
    payload = card_service.card_payload(rid)
    if payload is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="role not found")
    return payload


@router.get("/api/catalog/{role_id}/battery")
def get_battery(role_id: str) -> list:
    """Cached battery items for a role (empty list if none yet seeded)."""
    rid = _parse_role_id(role_id)
    return card_service.battery_items(rid)


@router.post("/api/catalog/{role_id}/distill", dependencies=[Depends(require_beta)])
def distill_role(role_id: str) -> dict:
    """BETA-GATED: force-distill a role's responsibilities synchronously.

    Requires `Authorization: Bearer <beta token>` (or the admin token, which
    is a strict superset — see app.auth.require_beta). 401 if missing/unknown/
    revoked. Returns {status, distilled} where status is card_service's
    distill result ('done'|'failed'|…). role_id validated to int (422 on bad).
    """
    rid = _parse_role_id(role_id)
    status_value, bullets = card_service.distill_role_sync(rid)
    return {"status": status_value, "distilled": bullets}
