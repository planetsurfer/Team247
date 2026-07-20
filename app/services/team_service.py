"""Team lifecycle business logic for the AgentProof app.

Phase 1 (recommend) → Phase 2 (edit: get/update/add/delete agent + skills).
Persistence goes through app.db (SQLite). LLM orchestration reuses
app.llm_contracts.team_recommend; role retrieval reuses classify.recommend_roles
(the guardrail — the LLM never names a role from scratch); per-skill K&A rows
reuse teamspec.skill_rows (reads framework's official K&A index).
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from uuid import uuid4

import structlog

from app import db, llm_contracts, schemas
import classify, framework, teamspec

_log = structlog.get_logger("app")


class TeamNotFound(Exception):
    """Raised when a team_id or (team_id, agent_id) pair is unknown to the caller."""
    def __init__(self, message: str, status_code: int = 404):
        super().__init__(message)
        self.status_code = status_code


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ──────────────────────────────────────────────────────────────────────────────
# Phase B — function taxonomy + per-function role seeding (decompose-then-retrieve)
# ──────────────────────────────────────────────────────────────────────────────
_TAXONOMY_PATH = os.getenv("FUNCTION_TAXONOMY_JSON", "data/function_taxonomy.json")
_TAXONOMY_CACHE = None


def _load_taxonomy():
    """Load+cache data/function_taxonomy.json (Phase A). Module-level cache — the
    file is committed/static at process lifetime, same pattern as classify._INDEX.
    Returns [] on any failure (missing file, bad JSON) so callers degrade to the
    pre-Phase-B behavior rather than raising."""
    global _TAXONOMY_CACHE
    if _TAXONOMY_CACHE is None:
        try:
            with open(_TAXONOMY_PATH, "r") as f:
                _TAXONOMY_CACHE = json.load(f)
        except Exception:  # noqa: BLE001
            _TAXONOMY_CACHE = []
    return _TAXONOMY_CACHE


def _seed_candidates_for_functions(function_ids, preferred_sectors):
    """B2: for each identified function_id, look up roles tagged with it via
    role_functions (Phase A's tagging table) joined to roles, rank preferred-
    sector matches first then by role name for stability, and take the top ~3
    per function as seed (role, sector) pairs.

    Returns (must_include, uncovered) where must_include is a deduped list of
    (role, sector) pairs across all functions, and uncovered is the subset of
    function_ids that matched zero roles in role_functions (either because
    tagging hasn't reached those roles yet — the background job is mid-run —
    or because no role in the catalog performs that function).
    """
    preferred = set(preferred_sectors or ())
    must_include, seen_pairs = [], set()
    uncovered = []
    for fid in function_ids:
        rows = db.query(
            "SELECT r.role_id, r.role, r.sector FROM role_functions rf "
            "JOIN roles r ON r.role_id = rf.role_id WHERE rf.function_id = ?",
            (fid,),
        )
        if not rows:
            uncovered.append(fid)
            continue
        rows.sort(key=lambda r: (r["sector"] not in preferred, r["role"]))
        for r in rows[:3]:
            pair = (r["role"], r["sector"])
            if pair not in seen_pairs:
                seen_pairs.add(pair)
                must_include.append(pair)
    return must_include, uncovered


def _coverage_gaps(function_ids, team_role_ids):
    """B3 (deterministic, no LLM): which of the identified functions have NO
    covering role on the FINAL team, via role_functions. Returns the list of
    uncovered function_ids.

    This is the composer-selection signal. Retrieval is solved — per-function
    recall is 100%, so the covering role is provably in the slate `must_include`
    seeded above; a gap here means the composer (llm_contracts.team_recommend)
    looked at the right role and didn't pick it. Callers should exclude
    functions already in `functions_uncovered` (no catalog role performs them —
    not the composer's fault) before treating a gap as a real miss.
    """
    if not function_ids or not team_role_ids:
        return list(function_ids or [])
    placeholders = ",".join("?" for _ in team_role_ids)
    rows = db.query(
        "SELECT DISTINCT function_id FROM role_functions "
        f"WHERE role_id IN ({placeholders})",
        tuple(team_role_ids),
    )
    covered = {r["function_id"] for r in rows}
    return [fid for fid in function_ids if fid not in covered]


def _role_id_for(role_name):
    """role name → catalog role_id (None if not in the catalog)."""
    row = db.query("SELECT role_id FROM roles WHERE role = ?", (role_name,), one=True)
    return row["role_id"] if row else None


def _team_role_ids_of(team_agents, candidates):
    """Resolve composer agents (1-based `n` into candidates) to catalog role_ids,
    applying the same guardrails as the persist loop (valid n, real role)."""
    ids = []
    for a in team_agents:
        if a.n < 1 or a.n > len(candidates):
            continue
        rid = _role_id_for(candidates[a.n - 1]["role"])
        if rid is not None:
            ids.append(rid)
    return ids


def _slate_covering_ns(function_id, candidates):
    """Candidate list-numbers (n) whose role covers function_id, in slate order.
    These are the exact numbers the composer could have picked (recall is 100%,
    so this is non-empty whenever the function is coverable)."""
    out = []
    for c in candidates:
        rid = _role_id_for(c["role"])
        if rid is None:
            continue
        if db.query("SELECT 1 FROM role_functions WHERE role_id = ? AND function_id = ?",
                    (rid, function_id), one=True):
            out.append(c["n"])
    return out


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
    """Compose a 1-8 agent team from the brief (or use_case) via the guardrailed
    classify.recommend_roles → llm_contracts.team_recommend pipeline. Persists
    `teams` + `team_agents` and returns the team_id + agent list + raw recommendation.
    """
    brief = brief or ({"outcome": use_case, "team_type": use_case} if use_case else {})
    # Retrieval query concatenates the brief's functional fields (not just the
    # first non-empty one): pain points carry the verbs ("rostering", "chasing
    # payments") that keyword retrieval needs to surface function-matched roles.
    query_parts = [brief.get("outcome"), brief.get("team_type"),
                   " ".join(brief.get("pain_points", []))]
    seen_parts = set()
    query = " ".join(
        p for p in query_parts
        if p and p.strip() and not (p in seen_parts or seen_parts.add(p))
    ).strip() or (use_case or "")

    # Ground retrieval in TWO sector signals, unioned — never one overriding
    # the other:
    #   1. the user's stated industry (brief.sector/domain) — the employer
    #      context that disambiguates industry-specific tasks a bare utterance
    #      cannot ("driver roster" → trucking vs buses);
    #   2. the sectors whose ROLES functionally perform the task (infer_sectors)
    #      — the right signal for role-agnostic work, where the employer
    #      industry is irrelevant (a farm chasing unpaid invoices needs
    #      Accountancy roles, not Farm Workers).
    # Hard-overriding retrieval with the stated industry alone regressed exactly
    # these cross-functional tasks, so both go into preferred_sectors and the
    # composer picks by function. Best-effort: inference failure must never
    # break the 100%-team guarantee.
    try:
        sector_names = sorted({r[0] for r in framework._sheet("Job Role_Description") if r[0]})
        stated = (brief.get("sector") or brief.get("domain") or "").strip()
        infer_text = query + (f"\n(The user says they work in: {stated})" if stated else "")
        inferred = llm_contracts.infer_sectors(infer_text, sector_names)
        preferred_sectors = ([stated] if stated in sector_names else []) + [
            s for s in inferred if s != stated
        ]
    except Exception:  # noqa: BLE001
        preferred_sectors = []

    # Phase B — decompose-then-retrieve: decompose the query into canonical
    # business functions (data/function_taxonomy.json), look up seed roles per
    # function via role_functions (Phase A's tagging table), and force those
    # roles into the candidate slate so it's CONSTRUCTED to cover every
    # function the task needs rather than hoping keyword overlap surfaces them.
    # Best-effort end to end: identify_functions failing, the taxonomy being
    # unavailable, or role_functions having no rows yet (the background
    # tagging job may be mid-run) must all degrade silently to functions=[]/
    # must_include=[] — the pipeline then behaves EXACTLY as it did before
    # this feature existed. The 100%-team guarantee never depends on this.
    functions_needed = []      # list of {id, name} — identified functions
    functions_uncovered = []   # subset with zero seed candidates in role_functions
    must_include = []
    try:
        taxonomy = _load_taxonomy()
        if taxonomy:
            by_id = {e["id"]: e for e in taxonomy}
            ident = llm_contracts.identify_functions(query or (use_case or ""), taxonomy)
            fn_ids = [fid for fid in ident.get("functions", []) if fid in by_id]
            functions_needed = [{"id": fid, "name": by_id[fid]["name"]} for fid in fn_ids]
            if fn_ids:
                must_include, uncovered_ids = _seed_candidates_for_functions(
                    fn_ids, preferred_sectors,
                )
                functions_uncovered = [{"id": fid, "name": by_id[fid]["name"]} for fid in uncovered_ids]
    except Exception:  # noqa: BLE001
        functions_needed, functions_uncovered, must_include = [], [], []

    recs = classify.recommend_roles(
        query, k=12, preferred_sectors=preferred_sectors, must_include=must_include,
    )
    candidates = [
        {"n": i + 1, "role": r["role"], "sector": r.get("sector"),
         "confidence": r.get("confidence"), "matched_on": r.get("matched_on")}
        for i, r in enumerate(recs)
    ]

    # NOTE: functions_needed is deliberately NOT passed to team_recommend — the
    # composer is no longer mandated to staff one agent per identified function
    # (that mandate was the confirmed cause of team bloat/genericization).
    # functions_needed still seeded `must_include` into `candidates` above (the
    # slate), and is still returned/persisted below for observability.
    tr = llm_contracts.team_recommend(brief, candidates)  # TeamRecommend

    # B4 — PRIMARY-function repair. If the composer left the KEY function (the
    # most-distinguishing, first-listed, coverable one) unstaffed, give it ONE
    # corrective shot (targeted re-prompt), then force-add the top covering slate
    # role. Scoped to the PRIMARY function ONLY on purpose: mandating coverage of
    # every identified function regressed into bloat before (SESSION_LOG it1
    # 2026-07-19), and a missing SECONDARY function may be an intentional lean
    # choice. Best-effort — any failure leaves the composer's team as-is.
    _coverable = [f["id"] for f in functions_needed
                  if f["id"] not in {u["id"] for u in functions_uncovered}]
    if _coverable and os.getenv("COMPOSER_REPAIR", "1") != "0":
        primary = _coverable[0]
        still_missing = primary in set(_coverage_gaps([primary], _team_role_ids_of(tr.team, candidates)))
        if still_missing:
            cover_ns = _slate_covering_ns(primary, candidates)
            if cover_ns:
                try:  # 1) targeted re-prompt, one shot
                    tr2 = llm_contracts.team_recommend(brief, candidates, must_cover=cover_ns)
                    if primary not in set(_coverage_gaps([primary], _team_role_ids_of(tr2.team, candidates))):
                        tr = tr2
                        _log.info("composer_repair", primary=primary, method="reprompt")
                except Exception:  # noqa: BLE001 — repair must never break the guarantee
                    pass
                # 2) force-add fallback if the key role is still absent
                if primary in set(_coverage_gaps([primary], _team_role_ids_of(tr.team, candidates))):
                    tr.team.append(schemas.TeamRecommendAgent(
                        n=cover_ns[0],
                        stage=(max((a.stage for a in tr.team), default=0) + 1),
                        squad="Coverage",
                        skill_level_overrides={},
                        rationale=f"Staffs the core function '{primary}' the brief requires.",
                    ))
                    _log.info("composer_repair", primary=primary, method="force_add", n=cover_ns[0])

    team_id = uuid4().hex
    recommendation_json = json.dumps({
        "team": [a.model_dump() for a in tr.team],
        "candidates": candidates,
        "functions_needed": functions_needed,
        "functions_uncovered": functions_uncovered,
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
    seen_role_ids = set()
    for i, a in enumerate(tr.team):
        n = a.n  # 1-based index into candidates (guardrail)
        if n < 1 or n > len(candidates):
            # Skip any agent whose n is out of range — never trust model-supplied numbers.
            continue
        role = candidates[n - 1]["role"]
        cand = candidates[n - 1]
        role_row = db.query("SELECT role_id FROM roles WHERE role = ?", (role,), one=True)
        if role_row is None:
            # Candidate role not in the catalog — skip rather than crash (defensive).
            continue
        role_id = role_row["role_id"]
        if role_id in seen_role_ids:
            # Duplicate-role guard: the prompt forbids reusing a list number, but
            # never trust it — identical clone agents add no capability.
            continue
        seen_role_ids.add(role_id)
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
            "confidence": cand.get("confidence"),
            "matched_on": cand.get("matched_on"),
        })

    # B3 — deterministic composer-coverage check: of the coverable functions
    # (functions_needed minus functions_uncovered), which did the composer leave
    # with no covering role on the final team? Retrieval already put the covering
    # role in the slate, so a gap here is a composer omission (the residual
    # missing_key_role). Logged + returned for observability; the repair step
    # (B4) acts on it. Pure DB — never breaks the team guarantee.
    _team_role_ids = [a["role_id"] for a in agents_out]
    _uncovered_ids = {f["id"] for f in functions_uncovered}
    functions_missing_in_team = [
        f for f in functions_needed
        if f["id"] in set(_coverage_gaps([x["id"] for x in functions_needed], _team_role_ids))
        and f["id"] not in _uncovered_ids
    ]
    if functions_missing_in_team:
        _log.info(
            "composer_coverage_gap",
            team_id=team_id,
            use_case=(use_case or "")[:120],
            missing=[f["id"] for f in functions_missing_in_team],
            team_role_ids=_team_role_ids,
        )

    # Slate-aware KEY-role miss: the true `missing_key_role` metric. The PRIMARY
    # (most-distinguishing) coverable function that HAD covering roles in the
    # slate but is still absent from the final team — i.e. a genuine composer
    # omission, EXCLUDING false-positives whose covering roles never reached the
    # slate (e.g. advocacy-dispute-resolution tagged on Financial-Forensics roles
    # for a hotel complaint). Returned for measurement.
    key_role_missing = bool(
        _coverable
        and _coverable[0] in set(_coverage_gaps([_coverable[0]], _team_role_ids))
        and _slate_covering_ns(_coverable[0], candidates)
    )

    # Identify the input artifacts the user should provide (the "is a sample needed?"
    # capability). Best-effort — a failure here must never break the team guarantee;
    # fall back to an empty list so /recommend still returns a team.
    try:
        artifacts = llm_contracts.identify_artifacts(query or (use_case or ""))
    except Exception:  # noqa: BLE001 — the 100%-team guarantee must not depend on this
        artifacts = []

    return {
        "team_id": team_id,
        "agents": agents_out,
        "artifacts_needed": artifacts,
        "functions_needed": functions_needed,
        "functions_uncovered": functions_uncovered,
        "functions_missing_in_team": functions_missing_in_team,
        "key_role_missing": key_role_missing,
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
