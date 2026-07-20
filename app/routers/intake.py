"""Intake interview routes (Phase 0): /api/intake/*."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.auth import consume_quota, require_beta
from app.ratelimit import llm_rate_limit
from app.services import intake_service, team_service

router = APIRouter()


class AnswerIn(BaseModel):
    answers: list[str]


@router.post(
    "/api/intake/start",
    dependencies=[Depends(require_beta), Depends(llm_rate_limit)],
)
def start():
    return intake_service.start()


@router.post(
    "/api/intake/{sid}/answer",
    dependencies=[Depends(require_beta), Depends(llm_rate_limit)],
)
def answer(sid: str, body: AnswerIn):
    try:
        return intake_service.answer(sid, body.answers)
    except ValueError:
        raise HTTPException(status_code=404, detail="unknown session")


@router.get("/api/intake/{sid}")
def get(sid: str):
    try:
        return intake_service.get(sid)
    except ValueError:
        raise HTTPException(status_code=404, detail="unknown session")


@router.post(
    "/api/intake/{sid}/recommend",
    dependencies=[Depends(require_beta), Depends(llm_rate_limit), Depends(consume_quota)],
)
def recommend_from_intake(sid: str, async_mode: bool = False):
    """Auto-chain: once the intake is ready, build a team from its brief.

    async_mode mirrors /api/team/recommend (iteration 4 — async recommend):
    require_beta / llm_rate_limit / consume_quota above still run
    synchronously in this request either way, so quota is charged at submit
    time, never inside the job thread.
    """
    s = intake_service.get(sid)
    if s["status"] != "ready":
        raise HTTPException(status_code=409, detail="intake not ready")
    if async_mode:
        jid = team_service.recommend_async(brief=s["brief"], intake_session_id=sid)
        return {"job_id": jid, "poll": f"/api/jobs/{jid}", "async": True}
    return team_service.recommend(brief=s["brief"], intake_session_id=sid)
