"""Team lifecycle business logic for the AgentProof app.

Phase 1 (recommend) → Phase 2 (edit: get/update/add/delete agent + skills).
Persistence goes through app.db (SQLite). LLM orchestration reuses
app.llm_contracts.team_recommend; role retrieval reuses classify.recommend_roles
(the guardrail — the LLM never names a role from scratch); per-skill K&A rows
reuse teamspec.skill_rows (reads framework's official K&A index).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import uuid4

from app import db, llm_contracts
import classify, framework, teamspec


class TeamNotFound(Exception):
    """Raised when a team_id or (team_id, agent_id) pair is unknown to the caller."""
    def __init__(self, message: str, status_code: int = 404):
        super().__init__(message)
        self.status_code = status_code


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _loads(raw, default):
    if not raw:
        return default
    try:
        return json.loads(raw)
    except Exception:
        return default


# ──────────────────────────────────────────────────────────────────────────────
# Phase 1 — recommend
# ──────────────────────────────────────────────────────────────────────────────
def recommend(use_case=None, brief=None, intake_session_id=None):
    """Compose a 3-8 agent team from the brief (or use_case) via the guardrailed
    classify.recommend_roles → llm_contracts.team_recommend pipeline. Persists
    `teams` + `team_agents` and returns the team_id + agent list + raw recommendation.
    """
    brief = brief or ({"outcome": use_case, "team_type": use_case} if use_case else {})
    query = (
        brief.get("outcome")
        or brief.get("team_type")
        or " ".join(brief.get("pain_points", []))
        or use_case
        or ""
    )

    recs = classify.recommend_roles(query, k=10)  # [{role, sector, confidence, matched_on}]
    candidates = [
        {"n": i + 1, "role": r["role"], "sector": r.get("sector"),
         "matched_on": r.get("matched_on")}
        for i, r in enumerate(recs)
    ]

    tr = llm_contracts.team_recommend(brief, candidates)  # TeamRecommend

    team_id = uuid4().hex
    recommendation_json = json.dumps({
        "team": [a.model_dump() for a in tr.team],
        "candidates": candidates,
    })
    db.execute(
        "INSERT INTO teams(team_id, name, use_case, intake_session_id, brief, "
        "status, recommendation_json, created_at) "
        "VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
        (team_id, None,
         (use_case or json.dumps(brief)),
         intake_session_id,
         json.dumps(brief),
         "recommend",
         recommendation_json,
         _now()),
    )

    agents_out = []
    for i, a in enumerate(tr.team):
        n = a.n  # 1-based index into candidates (guardrail)
        if n < 1 or n > len(candidates):
            # Skip any agent whose n is out of range — never trust model-supplied numbers.
            continue
        role = candidates[n - 1]["role"]
        role_row = db.query("SELECT role_id FROM roles WHERE role = ?", (role,), one=True)
        if role_row is None:
            # Candidate role not in the catalog — skip rather than crash (defensive).
            continue
        role_id = role_row["role_id"]
        agent_id = f"a{i + 1}"
        db.execute(
            "INSERT INTO team_agents(team_id, agent_id, role_id, stage, squad, "
            "produces, consumes, anchor, skill_overrides, skill_disabled, "
            "rationale, sort_order) "
            "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (team_id, agent_id, role_id, a.stage, a.squad,
             None, None, 0,
             json.dumps(a.skill_level_overrides), "[]",
             a.rationale, i),
        )
        agents_out.append({
            "agent_id": agent_id,
            "role_id": role_id,
            "role": role,
            "stage": a.stage,
            "squad": a.squad,
            "skill_level_overrides": a.skill_level_overrides,
            "rationale": a.rationale,
        })

    return {
        "team_id": team_id,
        "agents": agents_out,
        "recommendation_raw": {"team": [a.model_dump() for a in tr.team],
                               "candidates": candidates},
    }


# ──────────────────────────────────────────────────────────────────────────────
# Phase 2 — read / edit
# ──────────────────────────────────────────────────────────────────────────────
def get_team(team_id):
    """Full team DTO: teams row + team_agents joined to roles. 404 if unknown."""
    team = db.query("SELECT * FROM teams WHERE team_id = ?", (team_id,), one=True)
    if team is None:
        raise TeamNotFound(f"team {team_id} not found")
    rows = db.query(
        "SELECT ta.agent_id, ta.role_id, r.role, ta.stage, ta.squad, "
        "ta.produces, ta.consumes, ta.anchor, ta.skill_overrides, "
        "ta.skill_disabled, ta.rationale, ta.sort_order "
        "FROM team_agents ta JOIN roles r ON r.role_id = ta.role_id "
        "WHERE ta.team_id = ? ORDER BY ta.sort_order, ta.agent_id",
        (team_id,),
    )
    agents = [
        {
            "agent_id": row["agent_id"],
            "role_id": row["role_id"],
            "role": row["role"],
            "stage": row["stage"],
            "squad": row["squad"],
            "produces": row["produces"],
            "consumes": row["consumes"],
            "anchor": row["anchor"],
            "skill_overrides": _loads(row["skill_overrides"], {}),
            "skill_disabled": _loads(row["skill_disabled"], []),
            "rationale": row["rationale"],
        }
        for row in rows
    ]
    return {
        "team_id": team["team_id"],
        "use_case": team["use_case"],
        "brief": _loads(team["brief"], {}),
        "status": team["status"],
        "agents": agents,
    }


def update_agent(team_id, agent_id, **fields):
    """Patch a team_agents row. Allowed fields: stage, squad, produces, consumes,
    role_id, anchor, skill_overrides (dict), skill_disabled (list). 404 if unknown.
    Returns the updated agent (same shape as get_team's agent entries)."""
    existing = db.query(
        "SELECT * FROM team_agents WHERE team_id = ? AND agent_id = ?",
        (team_id, agent_id), one=True,
    )
    if existing is None:
        raise TeamNotFound(f"agent {agent_id} not found in team {team_id}")

    allowed = ("stage", "squad", "produces", "consumes", "role_id", "anchor",
               "skill_overrides", "skill_disabled")
    sets, params = [], []
    for k in allowed:
        if k not in fields or fields[k] is None:
            continue
        v = fields[k]
        if k == "skill_overrides":
            v = json.dumps(v)
        elif k == "skill_disabled":
            v = json.dumps(v)
        sets.append(f"{k} = ?")
        params.append(v)
    if sets:
        params += [team_id, agent_id]
        db.execute(
            f"UPDATE team_agents SET {', '.join(sets)} "
            "WHERE team_id = ? AND agent_id = ?",
            tuple(params),
        )
    return _agent_dto(team_id, agent_id)


def _agent_dto(team_id, agent_id):
    row = db.query(
        "SELECT ta.agent_id, ta.role_id, r.role, ta.stage, ta.squad, "
        "ta.produces, ta.consumes, ta.anchor, ta.skill_overrides, "
        "ta.skill_disabled, ta.rationale "
        "FROM team_agents ta JOIN roles r ON r.role_id = ta.role_id "
        "WHERE ta.team_id = ? AND ta.agent_id = ?",
        (team_id, agent_id), one=True,
    )
    if row is None:
        return None
    return {
        "agent_id": row["agent_id"],
        "role_id": row["role_id"],
        "role": row["role"],
        "stage": row["stage"],
        "squad": row["squad"],
        "produces": row["produces"],
        "consumes": row["consumes"],
        "anchor": row["anchor"],
        "skill_overrides": _loads(row["skill_overrides"], {}),
        "skill_disabled": _loads(row["skill_disabled"], []),
        "rationale": row["rationale"],
    }


def add_agent(team_id, role_id, stage, squad=None, produces=None, consumes=None):
    """Append a new agent to a team. agent_id = a{max_existing+1}. skill_overrides
    is left empty — defaults live in role_skills; overrides are edits. Returns
    the new agent DTO. 404 if the team is unknown."""
    team = db.query("SELECT team_id FROM teams WHERE team_id = ?", (team_id,), one=True)
    if team is None:
        raise TeamNotFound(f"team {team_id} not found")
    existing = db.query(
        "SELECT agent_id FROM team_agents WHERE team_id = ? ORDER BY agent_id",
        (team_id,),
    )
    max_n = 0
    for r in existing:
        aid = r["agent_id"]
        if aid.startswith("a") and aid[1:].isdigit():
            max_n = max(max_n, int(aid[1:]))
    agent_id = f"a{max_n + 1}"
    sort_order = len(existing)
    db.execute(
        "INSERT INTO team_agents(team_id, agent_id, role_id, stage, squad, "
        "produces, consumes, anchor, skill_overrides, skill_disabled, "
        "rationale, sort_order) "
        "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (team_id, agent_id, int(role_id), int(stage), squad,
         produces, consumes, 0, "{}", "[]", None, sort_order),
    )
    return _agent_dto(team_id, agent_id)


def delete_agent(team_id, agent_id):
    """Delete a team_agents row. team_handoffs has no FK to team_agents, so:
    (1) delete handoffs touching this agent_id, (2) re-point other agents' consumes
    that referenced this agent_id to 'external', (3) delete the team_agents row.
    Returns {deleted: true}. 404 if the agent is unknown."""
    existing = db.query(
        "SELECT team_id FROM team_agents WHERE team_id = ? AND agent_id = ?",
        (team_id, agent_id), one=True,
    )
    if existing is None:
        raise TeamNotFound(f"agent {agent_id} not found in team {team_id}")

    # (1) drop handoffs touching this agent
    db.execute(
        "DELETE FROM team_handoffs WHERE team_id = ? AND "
        "(from_agent = ? OR to_agent = ?)",
        (team_id, agent_id, agent_id),
    )
    # (2) re-point consumes references to 'external'
    db.execute(
        "UPDATE team_agents SET consumes = 'external' "
        "WHERE team_id = ? AND consumes = ?",
        (team_id, agent_id),
    )
    # (3) delete the agent row
    db.execute(
        "DELETE FROM team_agents WHERE team_id = ? AND agent_id = ?",
        (team_id, agent_id),
    )
    return {"deleted": True, "team_id": team_id, "agent_id": agent_id}


def skills(team_id, agent_id):
    """Per-skill K&A rows for one agent, with overrides + disabled applied.
    Reuses teamspec.skill_rows (framework's official K&A index). 404 if unknown."""
    agent = db.query(
        "SELECT ta.agent_id, ta.role_id, ta.skill_overrides, ta.skill_disabled, "
        "r.role FROM team_agents ta JOIN roles r ON r.role_id = ta.role_id "
        "WHERE ta.team_id = ? AND ta.agent_id = ?",
        (team_id, agent_id), one=True,
    )
    if agent is None:
        raise TeamNotFound(f"agent {agent_id} not found in team {team_id}")

    role = agent["role"]
    overrides = _loads(agent["skill_overrides"], {})
    disabled = set(_loads(agent["skill_disabled"], []))

    rows = teamspec.skill_rows(role, {})  # authoritative K&A-grounded rows
    sk = []
    for r in rows:
        if r["code"] in disabled:
            continue
        row = dict(r)
        if r["code"] in overrides:
            row["lvl"] = overrides[r["code"]]
        sk.append(row)
    return {"agent_id": agent_id, "role": role, "sk": sk}
