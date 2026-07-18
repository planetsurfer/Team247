"""LLM contracts for the AgentProof app — thin functions over config.llm_json
(Alibaba ModelStudio), each with a Pydantic validate, wrapped in retry/backoff.

Contracts:
  intake(transcript)            -> IntakeTurn   (Phase 0)
  team_recommend(brief, candidates) -> TeamRecommend   (Phase 1)

wire_handoffs (Phase 3) is added in a later stage. All calls log via
logging_setup.log_llm (purpose, model, attempt, latency, status).
"""
from __future__ import annotations

import time

import config  # root config.py — Alibaba LLM client (llm_json, _model_for)

from app.logging_setup import log_llm
from app.schemas import Brief, IntakeTurn, TeamRecommend, WireHandoffs

CEREMONIES = (
    "artifact handoff", "review gate", "sprint demo", "sign-off", "feedback loop",
)


def validate_handoff_graph(handoffs, agent_ids):
    """Server-side graph check. `agent_ids` is the set of valid agent ids.

    - from/to must be a real agent_id or 'external'.
    - ceremony must be in the enum.
    - >=1 handoff.
    - The graph of NON-'feedback loop' edges must be acyclic (topological
      sort). 'feedback loop' is the ONLY allowed back-edge and is excluded
      from the sort (it renders as a curved return arc). Any other cycle → reject.
    Returns the validated list of Handoff dicts.
    """
    if not handoffs:
        raise ValueError("at least one handoff is required")
    nodes = set(agent_ids) | {"external"}
    for h in handoffs:
        if h.get("from_agent") not in nodes or h.get("to_agent") not in nodes:
            raise ValueError(f"handoff references unknown agent: {h}")
        if h.get("ceremony") not in CEREMONIES:
            raise ValueError(f"unknown ceremony: {h.get('ceremony')}")

    # Topological sort over non-feedback edges; detect a cycle.
    adj = {a: set() for a in agent_ids}
    indeg = {a: 0 for a in agent_ids}
    for h in handoffs:
        f, t, c = h["from_agent"], h["to_agent"], h["ceremony"]
        if c == "feedback loop":
            continue  # allowed back-edge, excluded from the DAG
        if f == "external" or t == "external":
            continue
        if t not in adj[f]:
            adj[f].add(t)
            indeg[t] += 1
    # Kahn
    queue = [a for a in agent_ids if indeg[a] == 0]
    seen = 0
    while queue:
        n = queue.pop()
        seen += 1
        for m in adj[n]:
            indeg[m] -= 1
            if indeg[m] == 0:
                queue.append(m)
    if seen != len(agent_ids):
        raise ValueError("non-feedback handoff graph has a cycle")
    return handoffs


# The 3 fixed seed questions (app constants — NOT LLM-generated).
SEED_QUESTIONS = [
    "What kind of agentic team are you trying to build?",
    "What are your main pain points or bottlenecks today?",
    "What outcome or deliverable would define success?",
]

MAX_INTAKE_ROUNDS = 6


class LLMError(Exception):
    """Raised when an LLM contract fails all retry attempts."""


def call_llm_json(purpose, messages, validate, *, temperature=0.2, max_tokens=4096, attempts=3):
    """Retry/backoff wrapper over config.llm_json. Logs every attempt."""
    model = config._model_for(purpose)
    last = None
    for attempt in range(1, attempts + 1):
        t0 = time.perf_counter()
        try:
            obj = config.llm_json(
                messages, temperature=temperature, validate=validate,
                max_tokens=max_tokens, purpose=purpose,
            )
            log_llm(purpose=purpose, model=model, attempt=attempt,
                    latency_ms=int((time.perf_counter() - t0) * 1000), status="ok")
            return obj
        except Exception as e:  # noqa: BLE001 — retry any failure (parse/validate/transport)
            last = e
            log_llm(purpose=purpose, model=model, attempt=attempt,
                    latency_ms=int((time.perf_counter() - t0) * 1000), status="error",
                    error=str(e)[:200])
            if attempt < attempts:
                time.sleep(2 ** (attempt - 1))  # 1s, 2s
    raise LLMError(f"{purpose} failed after {attempts} attempts: {last}") from last


def _validate_intake_turn(o):
    """Validate the raw dict the LLM returns for an intake turn."""
    t = IntakeTurn.model_validate(o)
    if t.ready:
        if t.brief is None:
            # LLM said ready but gave no brief — accept only if there's at least a free-form field
            raise ValueError("ready=true requires a brief")
        return t
    if not (1 <= len(t.questions) <= 3) or not all(q.strip() for q in t.questions):
        raise ValueError("ready=false requires 1-3 non-empty questions")
    return t


def intake(transcript):
    """Phase 0: given the conversation transcript so far, return the next IntakeTurn.

    transcript: list[dict] of {role: 'fixed'|'assistant'|'user', content, round}
    """
    convo = "\n\n".join(
        f"{'INTERVIEWER' if m['role'] in ('fixed', 'assistant') else 'USER'}: {m['content']}"
        for m in transcript
    )
    prompt = (
        "You are conducting an adaptive intake interview to gather enough context to "
        "assemble an optimal multi-agent team for a user's use case. You have the "
        "conversation so far. Decide ONE of:\n"
        "  A) Ask 1-3 MORE tailored follow-up questions to close gaps (e.g. scale, "
        "domain, existing tools, compliance, timeline, team size, must-haves). Return "
        '{"ready": false, "questions": ["...", ...]}.\n'
        "  B) If you have enough context, stop and produce a structured brief. Return "
        '{"ready": true, "brief": {"team_type": "...", "pain_points": ["..."], '
        '"outcome": "...", "domain": "...", "scale": "...", "constraints": ["..."]}} '
        "(omit any field you cannot fill; you may add others).\n\n"
        f"Conversation so far:\n{convo}\n\n"
        "Return STRICT JSON only — one of the two shapes above."
    )
    raw = call_llm_json(
        "intake",
        [{"role": "user", "content": prompt}],
        validate=_validate_intake_turn,
        temperature=0.2,
        max_tokens=1024,
    )
    return IntakeTurn.model_validate(raw)


def _validate_team_recommend(o):
    tr = TeamRecommend.model_validate(o)
    if not (3 <= len(tr.team) <= 8):
        raise ValueError("team must have 3-8 agents")
    for a in tr.team:
        if a.stage < 1:
            raise ValueError("stage must be >= 1")
        for code, lvl in a.skill_level_overrides.items():
            if not (1 <= int(lvl) <= 6):
                raise ValueError(f"override level out of range: {code}={lvl}")
    return tr


def team_recommend(brief, candidates):
    """Phase 1: assemble a 3-8 agent team from ONLY the given candidate roles.

    brief: dict (the intake Brief). candidates: list[{n, role, sector, matched_on}].
    Returns TeamRecommend. The LLM never names a role not in `candidates`.
    """
    listing = "\n".join(
        f"{c['n']}. {c['role']}  ({c.get('sector', '?')})"
        for c in candidates
    )
    brief_txt = str(brief)
    prompt = (
        "A user wants to staff a multi-agent team. Their intake brief (JSON):\n"
        f"{brief_txt}\n\n"
        "REAL SkillsFuture roles available — choose ONLY from these, by list number:\n"
        f"{listing}\n\n"
        "Assemble a team of 3-8 agents to deliver the brief end-to-end. For each agent:\n"
        "- n: the list number of the chosen role (must be one of the numbers above)\n"
        "- stage: 1 = first, increasing along the delivery flow\n"
        "- squad: a short squad name (e.g. 'Data & AI', 'Build & Platform', 'Delivery')\n"
        "- skill_level_overrides: {code: level} ONLY for the few skills whose default\n"
        "  required level the brief clearly changes; empty {} if none\n"
        "- rationale: one short sentence\n"
        'Return STRICT JSON: {"team": [{"n": <int>, "stage": <int>, "squad": "<str>", '
        '"skill_level_overrides": {"<code>": <int>}, "rationale": "<str>"}]}'
    )
    raw = call_llm_json(
        "team_recommend",
        [{"role": "user", "content": prompt}],
        validate=_validate_team_recommend,
        temperature=0.2,
        max_tokens=2048,
    )
    return TeamRecommend.model_validate(raw)


def _validate_wire_handoffs(o):
    wh = WireHandoffs.model_validate(o)
    # ceremony enum + from/to are strings (graph check is server-side in the service
    # which knows the real agent_ids; here we only enforce the enum + non-empty).
    for h in wh.handoffs:
        if h.ceremony not in CEREMONIES:
            raise ValueError(f"unknown ceremony: {h.ceremony}")
    return True


def wire_handoffs(composition, use_case, agent_ids):
    """Phase 3: wire handoffs/ceremonies between the team's agents.

    composition: list[{agent_id, role, stage, squad, produces, consumes}]
    use_case: str. agent_ids: set of valid agent ids (for the graph check).
    Returns WireHandoffs (validated + graph-acyclic-checked).
    """
    listing = "\n".join(
        f"- {a['agent_id']} | stage {a['stage']} | {a['squad']} | {a['role']}"
        f" | produces: {a.get('produces') or '-'} | consumes: {a.get('consumes') or '-'}"
        for a in composition
    )
    prompt = (
        "Team composition (after user edits):\n" + listing + "\n\n"
        "Use case:\n" + (use_case or "(unspecified)") + "\n\n"
        "Wire the seams between agents: for each meaningful producer→consumer pair "
        "(including external input at stage 1 and final delivery at the last stage), "
        "name the CEREMONY that connects them. Use exactly one of: "
        + ", ".join(f'\"{c}\"' for c in CEREMONIES) + ".\n"
        "- 'artifact handoff': one agent's produces feeds the next's consumes\n"
        "- 'review gate': consumer reviews producer's artifact before continuing\n"
        "- 'sprint demo': synchronous show-and-tell at a stage boundary\n"
        "- 'sign-off': governance/approval gate\n"
        "- 'feedback loop': consumer sends corrections back to producer (the ONLY\n"
        "  allowed back-edge; everything else must form a DAG)\n\n"
        'Return STRICT JSON: {"handoffs": [{"from": "<agent_id|external>", '
        '"to": "<agent_id|external>", "ceremony": "<one of the above>", '
        '"artifact": "<what flows>", "description": "<<=20 words>"}]}\n'
        "Every (from,to,ceremony) must be unique; aim for 3-10 handoffs."
    )
    raw = call_llm_json(
        "wire_handoffs",
        [{"role": "user", "content": prompt}],
        validate=_validate_wire_handoffs,
        temperature=0.2,
        max_tokens=2048,
    )
    wh = WireHandoffs.model_validate(raw)
    handoff_dicts = [
        {"from_agent": h.from_agent, "to_agent": h.to_agent, "ceremony": h.ceremony,
         "artifact": h.artifact, "description": h.description}
        for h in wh.handoffs
    ]
    validate_handoff_graph(handoff_dicts, agent_ids)  # raises on a non-feedback cycle
    return wh
