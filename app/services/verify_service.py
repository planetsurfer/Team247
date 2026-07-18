"""Optional execution-verify (Stage 4). Two-track, NEVER blended:
  - Executed track: a role's card_battery_items run in a LocalRunner sandbox via
    assess.assess_skill (candidate code from the team spec + grader) → score
    0..1, held_level, sandbox_id, execution_verified=True.
  - Rubric track: the role's non-executable skills (capped) judged by
    battery.rubric_score → covered/total/pct, execution_verified=False.

Persisted to verify_runs (one per team+agent). Admin-gated at the router.
"""
import json
import uuid
import datetime

from app import db
from app.services import team_service, render_service
from app.services.team_service import TeamNotFound

import config      # root config: make_runner (LocalRunner)
import assess      # assess_skill, held_level
import battery     # rubric_score
import framework   # get_skills, select_executable, get_ka

RUBRIC_CAP = 6


def verify(team_id, agent_id, battery_item_ids=None):
    team = team_service.get_team(team_id)          # TeamNotFound propagates
    agent = next((a for a in team["agents"] if a["agent_id"] == agent_id), None)
    if agent is None:
        raise TeamNotFound()
    spec = render_service.get_spec(team_id, agent_id)
    spec_md = spec["spec_md"] if spec else ""
    role_id = agent["role_id"]
    role = agent["role"]

    runner = config.make_runner()

    # ── Executed track ────────────────────────────────────────────────────
    if battery_item_ids:
        placeholders = ",".join("?" * len(battery_item_ids))
        items = db.query(
            f"SELECT * FROM card_battery_items WHERE role_id = ? AND item_id IN ({placeholders}) "
            f"AND status = 'ready'",
            [role_id, *battery_item_ids],
        )
    else:
        items = db.query(
            "SELECT * FROM card_battery_items WHERE role_id = ? AND status = 'ready'",
            (role_id,),
        )
    exec_results = []
    for it in items:
        item = {
            "skill": it["skill"], "code": it["code"],
            "required_level": it["required_level"],
            "task_prompt": it["task_prompt"], "grader_code": it["grader_code"],
        }
        try:
            r = assess.assess_skill(runner, item, spec_md)
            exec_results.append({
                "skill": r["skill"], "code": r.get("code"),
                "required_level": r["required_level"], "score": r["score"],
                "held_level": r["held_level"], "gap": r["gap"],
                "sandbox_id": r["sandbox_id"], "error": r.get("error"),
                "execution_verified": True,
            })
        except Exception as e:  # noqa: BLE001 — one skill's failure must not kill the run
            exec_results.append({
                "skill": it["skill"], "code": it["code"],
                "required_level": it["required_level"], "score": 0.0,
                "held_level": 0, "gap": it["required_level"],
                "sandbox_id": None, "error": str(e)[:120],
                "execution_verified": True,  # it WAS executed (and failed)
            })

    # ── Rubric track (non-exec skills, capped) ─────────────────────────────
    skills = framework.get_skills(role)
    exec_codes = {s["code"] for s in framework.select_executable(skills)}
    non_exec = [s for s in skills if s["code"] not in exec_codes][:RUBRIC_CAP]
    rubric_results = []
    for s in non_exec:
        try:
            ka = framework.get_ka(s["code"], s["required_level"])
            r = battery.rubric_score(s["skill"], s["required_level"], ka, spec_md)
            if r:
                rubric_results.append({
                    "skill": r["skill"], "level": r["level"],
                    "covered": r["covered"], "total": r["total"], "pct": r["pct"],
                    "execution_verified": False,
                })
        except Exception as e:  # noqa: BLE001
            rubric_results.append({
                "skill": s["skill"], "level": s["required_level"],
                "error": str(e)[:120], "execution_verified": False,
            })

    # Coverage = EXECUTED track only (held/required). Never blended with rubric.
    if exec_results:
        coverage_pct = round(
            100 * sum(min(er["held_level"], er["required_level"]) for er in exec_results)
            / sum(er["required_level"] for er in exec_results), 1,
        )
    else:
        coverage_pct = None

    # Persist (one active verify per team+agent).
    vrid = uuid.uuid4().hex
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    db.execute(
        "DELETE FROM verify_runs WHERE team_id = ? AND agent_id = ?",
        (team_id, agent_id),
    )
    db.execute(
        "INSERT INTO verify_runs(verify_run_id, team_id, agent_id, status, "
        "results_json, rubric_json, coverage_pct, started_at, finished_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (vrid, team_id, agent_id, "done",
         json.dumps(exec_results), json.dumps(rubric_results), coverage_pct, now, now),
    )
    return {
        "verify_run_id": vrid, "team_id": team_id, "agent_id": agent_id,
        "status": "done", "exec_results": exec_results,
        "rubric_results": rubric_results, "coverage_pct": coverage_pct,
        "two_track_note": "exec_results are execution-verified; rubric_results are "
                          "LLM-judged and never blended into coverage_pct.",
    }


def get_verify(team_id, agent_id):
    r = db.query(
        "SELECT * FROM verify_runs WHERE team_id = ? AND agent_id = ?",
        (team_id, agent_id), one=True,
    )
    if not r:
        return None
    return {
        "verify_run_id": r["verify_run_id"], "team_id": team_id, "agent_id": agent_id,
        "status": r["status"], "coverage_pct": r["coverage_pct"],
        "exec_results": json.loads(r["results_json"] or "[]"),
        "rubric_results": json.loads(r["rubric_json"] or "[]"),
    }
