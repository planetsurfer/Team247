"""Beta feedback capture — Iteration 1 (user-value loop): POST /api/feedback.

Beta-gated (`require_beta`, admin token accepted as a strict superset — same
convention as every other beta-gated route) but deliberately NOT
`consume_quota`-metered and NOT `llm_rate_limit`-ed: a thumbs up/down + short
comment is a single cheap SQLite upsert, not an LLM call, so it shouldn't eat
into a beta tester's generation quota. A light per-token/IP 30/min cap
(`feedback_rate_limit`, app/ratelimit.py) still guards against a runaway
client-side loop.

Idempotent by (token_hash, team_id): a second POST from the same caller for
the same team UPDATES the existing `generation_feedback` row (verdict,
comment, created_at) rather than inserting a duplicate — see that table's
UNIQUE(token_hash, team_id) constraint in app/schema.sql. This is how the UI's
"thumbs up, then add a one-line comment" two-step lands as a single row.

With BETA_AUTH off (local dev / tests), `require_beta` is a no-op and never
stamps `request.state.token_hash` — this endpoint falls back to the literal
string "anonymous" in that case, so local dev never breaks.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app import db
from app.auth import require_beta
from app.ratelimit import feedback_rate_limit

router = APIRouter()

_VALID_VERDICTS = {"up", "down"}
_MAX_COMMENT_LEN = 2000


class FeedbackIn(BaseModel):
    team_id: str
    verdict: str
    comment: Optional[str] = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@router.post(
    "/api/feedback",
    dependencies=[Depends(require_beta), Depends(feedback_rate_limit)],
)
def submit_feedback(body: FeedbackIn, request: Request) -> dict:
    if body.verdict not in _VALID_VERDICTS:
        raise HTTPException(status_code=422, detail="verdict must be 'up' or 'down'")
    if body.comment is not None and len(body.comment) > _MAX_COMMENT_LEN:
        raise HTTPException(
            status_code=422, detail=f"comment must be <= {_MAX_COMMENT_LEN} chars"
        )

    team = db.query("SELECT team_id FROM teams WHERE team_id = ?", (body.team_id,), one=True)
    if team is None:
        raise HTTPException(status_code=404, detail="team not found")

    # require_beta stamps request.state.token_hash on every gated request
    # ('admin' for the admin token); with BETA_AUTH off it's a no-op and never
    # touches request.state, hence the "anonymous" fallback.
    token_hash = getattr(request.state, "token_hash", None) or "anonymous"

    db.execute(
        "INSERT INTO generation_feedback (token_hash, team_id, created_at, verdict, comment) "
        "VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(token_hash, team_id) DO UPDATE SET "
        "verdict = excluded.verdict, comment = excluded.comment, created_at = excluded.created_at",
        (token_hash, body.team_id, _now(), body.verdict, body.comment),
    )
    return {"team_id": body.team_id, "verdict": body.verdict, "ok": True}
