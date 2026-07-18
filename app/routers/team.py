"""Team lifecycle + Phase 1-2 (recommend / edit) routes: /api/team/*."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional

from app import db
from app.ratelimit import llm_rate_limit
from app.services import team_service
from app.services.team_service import TeamNotFound

router = APIRouter()


class RecommendIn(BaseModel):
    use_case: Optional[str] = None
    brief: Optional[dict] = None
    intake_session_id: Optional[str] = None


class AddAgentIn(BaseModel):
    role_id: int
    stage: int
    squad: Optional[str] = None
    produces: Optional[str] = None
    consumes: Optional[str] = None


class UpdateAgentIn(BaseModel):
    stage: Optional[int] = None
    squad: Optional[str] = None
    produces: Optional[str] = None
    consumes: Optional[str] = None
    role_id: Optional[int] = None
    anchor: Optional[int] = None
    skill_overrides: Optional[dict] = None
    skill_disabled: Optional[list] = None


@router.post("/api/team/recommend", dependencies=[Depends(llm_rate_limit)])
def recommend(body: RecommendIn):
    return team_service.recommend(
        use_case=body.use_case, brief=body.brief,
        intake_session_id=body.intake_session_id,
    )


@router.get("/api/teams")
def list_teams():
    return db.query(
        "SELECT team_id, name, use_case, status, created_at "
        "FROM teams ORDER BY created_at DESC LIMIT 50"
    )


@router.get("/api/team/{team_id}")
def get_team(team_id: str):
    try:
        return team_service.get_team(team_id)
    except TeamNotFound:
        raise HTTPException(status_code=404, detail="team not found")


@router.delete("/api/team/{team_id}")
def delete_team(team_id: str):
    n = db.execute("DELETE FROM teams WHERE team_id = ?", (team_id,))
    if not n:
        raise HTTPException(status_code=404, detail="team not found")
    return {"deleted": True, "team_id": team_id}


@router.put("/api/team/{team_id}/agents/{agent_id}")
def update_agent(team_id: str, agent_id: str, body: UpdateAgentIn):
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    try:
        return team_service.update_agent(team_id, agent_id, **fields)
    except TeamNotFound:
        raise HTTPException(status_code=404, detail="agent not found")


@router.post("/api/team/{team_id}/agents")
def add_agent(team_id: str, body: AddAgentIn):
    try:
        return team_service.add_agent(
            team_id, role_id=body.role_id, stage=body.stage,
            squad=body.squad, produces=body.produces, consumes=body.consumes,
        )
    except TeamNotFound:
        raise HTTPException(status_code=404, detail="team not found")


@router.delete("/api/team/{team_id}/agents/{agent_id}")
def delete_agent(team_id: str, agent_id: str):
    try:
        return team_service.delete_agent(team_id, agent_id)
    except TeamNotFound:
        raise HTTPException(status_code=404, detail="agent not found")


@router.get("/api/team/{team_id}/skills/{agent_id}")
def skills(team_id: str, agent_id: str):
    try:
        return team_service.skills(team_id, agent_id)
    except TeamNotFound:
        raise HTTPException(status_code=404, detail="agent not found")
