"""Starter gallery — Iteration 4 (user-value loop): GET /api/gallery[/{slug}].

Rows are seeded offline by `python -m app.build_gallery` (app/build_gallery.py)
into `gallery_agents` (migration 0007) via the same recommend -> wire ->
compose_bundle pipeline a real user's first task runs. This router only ever
reads that table — it never calls an LLM itself.

GET /api/gallery         — OPEN (no auth): metadata only (slug/label/blurb),
                            so the starter gallery is visible pre-login on
                            Landing.
GET /api/gallery/{slug}  — beta-gated: the full row, including bundle_md (the
                            composed SKILL.md) and the team_id/agent_id used
                            to wire "Try this agent" / "Download" against the
                            existing team endpoints.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app import db
from app.auth import require_beta

router = APIRouter()


@router.get("/api/gallery")
def list_gallery() -> list[dict]:
    return db.query(
        "SELECT slug, label, blurb FROM gallery_agents ORDER BY created_at"
    )


@router.get("/api/gallery/{slug}", dependencies=[Depends(require_beta)])
def get_gallery_agent(slug: str) -> dict:
    row = db.query(
        "SELECT slug, label, blurb, use_case, team_id, agent_id, bundle_md "
        "FROM gallery_agents WHERE slug = ?",
        (slug,), one=True,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="unknown gallery agent")
    return row
