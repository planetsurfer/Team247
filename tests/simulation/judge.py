"""Judging for the simulation harness.

Two independent passes per conversation:
  - structural_checks: deterministic, mirrors tests/test_chat_flow_corpus.py —
    real team, real catalog roles, skills present, valid artifact kinds,
    intake actually refined. Cheap, unbiased, runs on the enriched record.
  - judge_conversation: one LLM call (purpose "sim_judge") scoring fit on an
    anchored 1-5 rubric with a fixed issue taxonomy. NOTE the judge shares a
    model with the system under test (self-judging leniency is expected);
    override with LLM_MODEL_SIM_JUDGE to swap in an independent model.
"""
from __future__ import annotations

from app.llm_contracts import LLMError, call_llm_json

VALID_ARTIFACT_KINDS = {"sample", "blank_format", "past_documents", "database", "none"}
SCORE_DIMS = ("role_fit", "coverage", "parsimony", "stage_squad_coherence",
              "artifact_correctness", "intake_quality")
ISSUE_TAGS = (
    "wrong_sector", "generic_team", "missing_key_role", "redundant_role",
    "oversized_team", "artifact_mismatch", "artifact_missing",
    "question_irrelevant", "question_repetitive", "question_jargon",
    "brief_mismatch", "judge_failed",
)
SEED_QUESTION_COUNT = 3  # intake always opens with 3 fixed seed questions


# ── deterministic pass ──────────────────────────────────────────────────────
def structural_checks(record: dict) -> dict:
    """Pure-data checks over an enriched record (runner resolves catalog/cards)."""
    checks: dict[str, bool] = {}
    errors: list[str] = []
    team = record.get("team") or {}
    agents = team.get("agents") or []

    checks["team_returned"] = bool(team.get("team_id"))
    checks["has_agents"] = len(agents) >= 1
    checks["agent_fields_present"] = all(
        a.get("agent_id") and a.get("role_id") and a.get("role") for a in agents
    ) if agents else False
    checks["role_ids_resolve"] = all(a.get("catalog_found") for a in agents) if agents else False
    checks["role_names_match"] = all(a.get("catalog_name_match") for a in agents) if agents else False
    checks["roles_have_skills"] = all((a.get("n_skills") or 0) >= 1 for a in agents) if agents else False

    arts = team.get("artifacts_needed") or []
    kinds = {a.get("kind") for a in arts if isinstance(a, dict)}
    checks["artifacts_nonempty"] = len(arts) >= 1
    checks["artifact_kinds_valid"] = bool(kinds) and kinds <= VALID_ARTIFACT_KINDS
    checks["artifact_need_identified"] = bool(kinds - {"none"})

    if record.get("track") == "intake":
        checks["intake_ready"] = bool(record.get("brief"))
        followups = (record.get("intake_questions") or [])[SEED_QUESTION_COUNT:]
        checks["intake_asked_followups"] = len(followups) >= 1
        # The one industry-confirming exchange: the interview must ask for the
        # official sector, and (since the sim-user answers with their official
        # sector name) the brief must carry it verbatim.
        checks["intake_sector_exchange_asked"] = any(
            "official SkillsFuture sector" in q for q in (record.get("intake_questions") or [])
        )
        checks["intake_sector_confirmed"] = (
            (record.get("brief") or {}).get("sector") == record.get("sector")
        )

    for name, ok in checks.items():
        if not ok:
            errors.append(name)
    return {"passed": not errors, "checks": checks, "errors": errors}


# ── LLM rubric pass ─────────────────────────────────────────────────────────
JUDGE_SYSTEM = """You are a strict evaluator of an app that recommends multi-agent
TEAMS of official occupational roles for a user's business task. Judge whether
the recommendation actually fits the user — not whether it is well-formatted.

Score each dimension 1-5 (integers):
- role_fit: does each role plausibly do part of THIS task?
  Anchors: 5 = every role obviously belongs (invoice task → Accounts
  Executive); 4 = all roles defensible, one is a stretch; 3 = core role right
  but 1+ roles only tangential; 2 = most roles generic or off-task; 1 = roles
  are from the wrong line of work entirely.
- coverage: is any obviously-needed role missing for the task? (5 = nothing
  missing ... 1 = the central role for the task is absent)
- parsimony: redundant/overlapping/oversized team? (5 = lean ... 1 = bloated
  with near-duplicates)
- stage_squad_coherence: do stage assignments + squad groupings read as a
  sensible workflow for the task? (5 = clear pipeline ... 1 = arbitrary)
- artifact_correctness: are the requested input artifacts the right ones for
  this task? (5 = exactly what you'd ask for ... 1 = wrong or missing)
- intake_quality: ONLY when interview questions are provided — were the
  follow-ups relevant, non-repetitive, answerable by this persona in plain
  words? Otherwise null.

Also return:
- issues: array of zero+ tags, ONLY from: wrong_sector, generic_team,
  missing_key_role, redundant_role, oversized_team, artifact_mismatch,
  artifact_missing, question_irrelevant, question_repetitive,
  question_jargon, brief_mismatch. Tag only clear problems.
- rationale: ONE sentence naming the biggest problem (or strength if clean).
- overall: 1-5 integer, weighted toward the worst dimension (a team with
  role_fit 2 cannot score overall 4).

Return STRICT JSON only:
{"scores": {"role_fit": int, "coverage": int, "parsimony": int,
 "stage_squad_coherence": int, "artifact_correctness": int,
 "intake_quality": int|null}, "issues": [...], "rationale": str,
 "overall": int}"""


def _validate_judge(obj):
    scores = obj.get("scores") or {}
    for dim in SCORE_DIMS:
        v = scores.get(dim)
        if dim == "intake_quality" and v is None:
            continue
        if not isinstance(v, int) or not 1 <= v <= 5:
            raise ValueError(f"score {dim}={v!r} not int 1-5")
    if not isinstance(obj.get("overall"), int) or not 1 <= obj["overall"] <= 5:
        raise ValueError(f"overall={obj.get('overall')!r} not int 1-5")
    if not obj.get("rationale"):
        raise ValueError("missing rationale")
    # Keep known tags, drop hallucinated ones (lenient — tags are aggregates).
    obj["issues"] = [t for t in (obj.get("issues") or []) if t in ISSUE_TAGS]
    return obj


def _team_summary(team: dict) -> str:
    lines = []
    for a in team.get("agents") or []:
        skills = ", ".join((a.get("top_skills") or [])[:5]) or "(none)"
        lines.append(
            f"- {a.get('role')} [sector: {a.get('sector') or '?'}; "
            f"stage: {a.get('stage') or '?'}; squad: {a.get('squad') or '?'}; "
            f"skills: {skills}]"
        )
    arts = ", ".join(
        f"{x.get('kind')}({x.get('description', '')[:60]})"
        for x in team.get("artifacts_needed") or [] if isinstance(x, dict)
    ) or "(none)"
    return "TEAM:\n" + ("\n".join(lines) or "(no agents)") + f"\nARTIFACTS REQUESTED: {arts}"


def judge_conversation(persona: dict, record: dict) -> dict | None:
    """Run the LLM rubric; returns the verdict dict or None (tagged judge_failed)."""
    parts = [
        "USER PERSONA:",
        f"- Occupation: {persona['occupation']} ({persona['sector']})",
        f"- Business: {persona['business_context']}",
        f"- Task as typed: {persona['task_utterance']!r}",
        "",
        _team_summary(record.get("team") or {}),
    ]
    if record.get("track") == "intake":
        qs = record.get("intake_questions") or []
        followups = qs[SEED_QUESTION_COUNT:]
        parts += [
            "",
            "INTERVIEW FOLLOW-UP QUESTIONS ASKED:",
            "\n".join(f"- {q}" for q in followups) or "(none)",
            "",
            f"BRIEF PRODUCED: {record.get('brief')}",
        ]
    else:
        parts += ["", "(one-shot mode: no interview questions — intake_quality must be null)"]
    try:
        return call_llm_json(
            "sim_judge",
            [{"role": "system", "content": JUDGE_SYSTEM},
             {"role": "user", "content": "\n".join(parts)}],
            _validate_judge,
            temperature=0.1,
        )
    except LLMError:
        return None
