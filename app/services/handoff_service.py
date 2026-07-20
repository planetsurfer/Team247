"""Phase 3 — wire team handoffs/ceremonies via the LLM contract, persist to
team_handoffs, and flip the team status to 'wired'. Read path: get_handoffs.
"""
from __future__ import annotations

import json
import uuid
import datetime

from app import db, llm_contracts
from app.services.team_service import TeamNotFound
from app.services import team_service


def wire(team_id, use_case=None):
    """Wire handoffs for a team. Replaces any prior wiring. Returns
    {team_id, handoffs:[{handoff_id, from_agent, to_agent, ceremony, artifact,
    description, sort_order}]}. Raises TeamNotFound (propagates) or LLMError
    (on persistent LLM failure) or ValueError (on a non-feedback cycle)."""
    team = team_service.get_team(team_id)  # raises TeamNotFound -> propagate

    composition = [
        {
            "agent_id": a["agent_id"],
            "role": a["role"],
            "stage": a["stage"],
            "squad": a.get("squad"),
            "produces": a.get("produces"),
            "consumes": a.get("consumes"),
        }
        for a in team["agents"]
    ]
    agent_ids = {a["agent_id"] for a in team["agents"]}
    use_case = use_case or team.get("use_case") or ""

    wh = llm_contracts.wire_handoffs(composition, use_case, agent_ids)
    # WireHandoffs; raises on a non-feedback cycle (validate_handoff_graph).

    now = datetime.datetime.now(datetime.timezone.utc).isoformat()

    db.execute("DELETE FROM team_handoffs WHERE team_id = ?", (team_id,))
    seen, i = set(), 0
    for h in wh.handoffs:
        # The LLM occasionally emits two handoffs with the same
        # (from_agent, to_agent, ceremony) triple (differing only in artifact) —
        # the table's UNIQUE constraint would otherwise raise IntegrityError and
        # abort the whole wire. Keep the first, skip later duplicates.
        key = (h.from_agent, h.to_agent, h.ceremony)
        if key in seen:
            continue
        seen.add(key)
        db.execute(
            "INSERT INTO team_handoffs(team_id, handoff_id, from_agent, to_agent, "
            "ceremony, artifact, description, sort_order, wired_at) "
            "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (team_id, uuid.uuid4().hex, h.from_agent, h.to_agent, h.ceremony,
             h.artifact, h.description, i, now),
        )
        i += 1

    db.execute("UPDATE teams SET status = 'wired' WHERE team_id = ?", (team_id,))

    return {"team_id": team_id, "handoffs": get_handoffs(team_id)}


def get_handoffs(team_id):
    """Return the team's handoffs ordered by sort_order."""
    return db.query(
        "SELECT handoff_id, from_agent, to_agent, ceremony, artifact, "
        "description, sort_order FROM team_handoffs "
        "WHERE team_id = ? ORDER BY sort_order",
        (team_id,),
    )
