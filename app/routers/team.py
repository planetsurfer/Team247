"""Team lifecycle + Phase 1-2 (recommend / edit) routes: /api/team/*."""
import io
import re
import zipfile

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import PlainTextResponse, StreamingResponse
from pydantic import BaseModel
from typing import Optional

from app import db, jobs, llm_contracts, settings
from app.auth import consume_quota, require_beta
from app.ratelimit import llm_rate_limit
from app.services import (
    handoff_service, render_service, skill_bundle_service, team_service, verify_service,
)
from app.services.team_service import TeamNotFound
import classify

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


class WireIn(BaseModel):
    use_case: Optional[str] = None


class SkillBundlesIn(BaseModel):
    use_case: Optional[str] = None
    artifacts_needed: Optional[list] = None
    format: Optional[str] = "json"


@router.post(
    "/api/team/recommend",
    dependencies=[Depends(require_beta), Depends(llm_rate_limit), Depends(consume_quota)],
)
def recommend(body: RecommendIn, async_mode: bool = False):
    # Reject empty input up front: without a use_case or a brief the pipeline runs
    # on nothing and returns a real-but-irrelevant team with 200 (the {"task": ...}
    # wrong-field trap). Fail loudly instead. This check — and the require_beta /
    # consume_quota dependencies declared above — run synchronously in THIS
    # request regardless of async_mode (FastAPI resolves route dependencies +
    # the function body before the async branch below ever touches the job
    # runner), so quota is always charged at submit time, never inside the job
    # thread.
    if not (body.use_case and body.use_case.strip()) and not body.brief:
        raise HTTPException(status_code=422, detail="use_case (or a brief) is required")
    if async_mode:
        jid = team_service.recommend_async(
            use_case=body.use_case, brief=body.brief,
            intake_session_id=body.intake_session_id,
        )
        return {"job_id": jid, "poll": f"/api/jobs/{jid}", "async": True}
    try:
        return team_service.recommend(
            use_case=body.use_case, brief=body.brief,
            intake_session_id=body.intake_session_id,
        )
    except classify.NoDatasetRoleMatch:
        raise HTTPException(
            status_code=422,
            detail="could not match your request to any role — please rephrase",
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


# ── Phase 3: wire + deliver ────────────────────────────────────────────────
@router.post(
    "/api/team/{team_id}/wire",
    dependencies=[Depends(require_beta), Depends(llm_rate_limit)],
)
def wire(team_id: str, body: WireIn):
    try:
        return handoff_service.wire(team_id, body.use_case)
    except TeamNotFound:
        raise HTTPException(status_code=404, detail="team not found")


@router.post(
    "/api/team/{team_id}/render",
    dependencies=[Depends(require_beta), Depends(llm_rate_limit)],
)
def render_team(team_id: str, force: bool = False, async_mode: bool = False):
    try:
        if async_mode:
            jid = render_service.render_async(team_id, force=force)
            return {"job_id": jid, "poll": f"/api/jobs/{jid}", "async": True}
        return render_service.render(team_id, force=force)
    except TeamNotFound:
        raise HTTPException(status_code=404, detail="team not found")


@router.get("/api/team/{team_id}/specs")
def specs(team_id: str):
    return render_service.specs_list(team_id)


@router.get("/api/team/{team_id}/specs/{agent_id}", response_class=PlainTextResponse)
def spec_md(team_id: str, agent_id: str):
    s = render_service.get_spec(team_id, agent_id)
    if s is None:
        raise HTTPException(status_code=404, detail="spec not found")
    return s["spec_md"]


@router.get("/api/team/{team_id}/download.zip")
def download(team_id: str):
    try:
        data = render_service.download_zip(team_id)
    except TeamNotFound:
        raise HTTPException(status_code=404, detail="team not found")
    return Response(
        content=data,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="team_{team_id[:8]}.zip"'},
    )


@router.get("/api/team/{team_id}/chart")
def chart(team_id: str):
    try:
        return render_service.chart(team_id)
    except TeamNotFound:
        raise HTTPException(status_code=404, detail="team not found")


# ── Stage 4: optional execution-verify (admin-gated, two-track) ────────────
@router.post("/api/team/{team_id}/agents/{agent_id}/verify", dependencies=[Depends(llm_rate_limit)])
def verify(team_id: str, agent_id: str, request: Request, async_mode: bool = False):
    if not settings.admin_token_ok(request.headers.get("authorization", "")):
        raise HTTPException(status_code=401, detail="admin token required")
    try:
        if async_mode:
            jid = verify_service.verify_async(team_id, agent_id)
            return {"job_id": jid, "poll": f"/api/jobs/{jid}", "async": True}
        return verify_service.verify(team_id, agent_id)
    except TeamNotFound:
        raise HTTPException(status_code=404, detail="agent not found")


@router.get("/api/jobs/{jid}")
def job_status(jid: str):
    return jobs.status(jid)


@router.get("/api/team/{team_id}/agents/{agent_id}/verify")
def get_verify(team_id: str, agent_id: str, request: Request):
    if not settings.admin_token_ok(request.headers.get("authorization", "")):
        raise HTTPException(status_code=401, detail="admin token required")
    r = verify_service.get_verify(team_id, agent_id)
    if r is None:
        raise HTTPException(status_code=404, detail="no verify run")
    return r


# ── Iteration 5: skill-bundle export (beta-gated — item 5, PRODUCTION_ROADMAP.md P0 #1) ──
def _slugify_role(role: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (role or "").lower()).strip("-")
    return slug or "role"


@router.post(
    "/api/team/{team_id}/skill-bundles",
    dependencies=[Depends(require_beta), Depends(llm_rate_limit)],
)
def skill_bundles(team_id: str, body: SkillBundlesIn):
    try:
        team = team_service.get_team(team_id)
    except TeamNotFound:
        raise HTTPException(status_code=404, detail="team not found")

    use_case = body.use_case or team.get("use_case") or ""

    if not handoff_service.get_handoffs(team_id):
        try:
            handoff_service.wire(team_id, use_case)
        except Exception:
            pass  # best-effort — bundles just won't have an I/O contract

    if body.artifacts_needed is not None:
        artifacts_needed = body.artifacts_needed
    else:
        try:
            artifacts_needed = llm_contracts.identify_artifacts(use_case)
        except Exception:
            artifacts_needed = []

    result = skill_bundle_service.compose_team_bundles(team_id, use_case, artifacts_needed)

    seen_slugs = {}
    for bundle in result["bundles"]:
        slug = _slugify_role(bundle["role"])
        if slug in seen_slugs:
            slug = f"{slug}-{bundle['agent_id']}"
        seen_slugs[slug] = True
        bundle["filename"] = f"{slug}/SKILL.md"

    if body.format == "zip":
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for bundle in result["bundles"]:
                zf.writestr(bundle["filename"], bundle["skill_md"])
        buf.seek(0)
        return StreamingResponse(
            buf,
            media_type="application/zip",
            headers={
                "Content-Disposition": f'attachment; filename="team-{team_id}-skills.zip"'
            },
        )

    return {
        "team_id": result["team_id"],
        "use_case": result["use_case"],
        "coherence": skill_bundle_service.check_team_coherence(team_id),
        "bundles": result["bundles"],
    }
