"""Starter gallery — Iteration 4 (user-value loop): GET /api/gallery[/{slug}].

Rows are seeded offline by `python -m app.build_gallery` (app/build_gallery.py)
into `gallery_agents` (migration 0007) via the same recommend -> wire ->
compose_bundle pipeline a real user's first task runs. This router only ever
reads that table — it never calls an LLM itself.

GET /api/gallery         — OPEN (no auth): metadata only (slug/label/blurb),
                            so the starter gallery is visible pre-login on
                            Landing. Plus, when a verify run exists for that
                            agent (app.build_battery + an admin-run
                            verify_service.verify — Iteration 6), a receipts
                            teaser: {proven, exec_skills}.
GET /api/gallery/{slug}  — beta-gated: the full row, including bundle_md (the
                            composed SKILL.md) and the team_id/agent_id used
                            to wire "Try this agent" / "Download" against the
                            existing team endpoints. Plus, when present, the
                            full receipts summary under "receipts".

Receipts (Iteration 6 — battery top-20 + gallery receipts): read-only, via
app.services.verify_service.get_receipts(team_id, agent_id) — never computed
here, never an LLM call from this router. Absent (no verify run stored yet)
means no "proven"/"exec_skills"/"receipts" key at all — this router never
fabricates a receipts summary, matching the two-track honesty rule verify()
itself follows.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app import db
from app.auth import require_beta
from app.services import verify_service

router = APIRouter()


@router.get("/api/gallery")
def list_gallery() -> list[dict]:
    rows = db.query(
        "SELECT slug, label, blurb, team_id, agent_id FROM gallery_agents ORDER BY created_at"
    )
    out = []
    for r in rows:
        item = {"slug": r["slug"], "label": r["label"], "blurb": r["blurb"]}
        receipts = verify_service.get_receipts(r["team_id"], r["agent_id"])
        if receipts is not None:
            item["proven"] = receipts["proven"]
            item["exec_skills"] = receipts["exec_skills"]
        out.append(item)
    return out


@router.get("/api/gallery/{slug}", dependencies=[Depends(require_beta)])
def get_gallery_agent(slug: str) -> dict:
    row = db.query(
        "SELECT slug, label, blurb, use_case, team_id, agent_id, bundle_md "
        "FROM gallery_agents WHERE slug = ?",
        (slug,), one=True,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="unknown gallery agent")
    receipts = verify_service.get_receipts(row["team_id"], row["agent_id"])
    if receipts is not None:
        row["receipts"] = receipts
    return row
