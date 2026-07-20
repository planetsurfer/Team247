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
def recommend_from_intake(sid: str):
    """Auto-chain: once the intake is ready, build a team from its brief."""
    s = intake_service.get(sid)
    if s["status"] != "ready":
        raise HTTPException(status_code=409, detail="intake not ready")
    return team_service.recommend(brief=s["brief"], intake_session_id=sid)
