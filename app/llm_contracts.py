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

from app import settings
from app.logging_setup import log_llm
from app.schemas import ARTIFACT_KINDS, Artifact, Brief, IntakeTurn, TeamRecommend, WireHandoffs

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
        "conversation so far. Everything you write is shown directly to the user — "
        "never cite internal skill codes, job codes, SSOC, SkillsFuture, or any "
        "competency-framework name; refer to roles, sectors, and skills by plain "
        "name only. Decide ONE of:\n"
        "  A) Ask 1-3 MORE tailored follow-up questions to close gaps (e.g. scale, "
        "domain, existing tools, compliance, timeline, team size, must-haves). Return "
        '{"ready": false, "questions": ["...", ...]}.\n'
        "     NOTE: the system separately asks the user for their official industry "
        "sector — do NOT ask your own industry/line-of-business question; ask about "
        "other gaps.\n"
        "  B) If you have enough context, stop and produce a structured brief. Return "
        '{"ready": true, "brief": {"team_type": "...", "sector": "...", '
        '"pain_points": ["..."], '
        '"outcome": "...", "domain": "...", "scale": "...", "constraints": ["..."], '
        '"artifacts_needed": [{"kind": "...", "description": "..."}]}} '
        "(omit any field you cannot fill; you may add others). `sector` is the "
        "user's industry in their own words (e.g. \"freight trucking\").\n"
        "  For artifacts_needed, list the input artifacts the build will need from the "
        "user to actually do the work. `kind` MUST be one of: "
        + ", ".join(ARTIFACT_KINDS) + " —\n"
        "    sample = a worked example to mimic; blank_format = a blank template/form "
        "to fill; past_documents = a corpus of past documents to mine; database = a "
        "live database/system of record to read or write; none = pure reasoning, no "
        "external artifact. Only list artifacts genuinely required; use [{\"kind\": "
        "\"none\"}] if none.\n\n"
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


def infer_sectors(text, sector_names):
    """Classify the user's task text to the 1-2 most likely industry sectors.

    Feeds classify.recommend_roles(preferred_sectors=...) so the candidate
    slate isn't sector-blind. Returns a (possibly empty) list of names drawn
    strictly from sector_names; callers must treat failure as [] — sector
    grounding is an improvement, never a gate on the 100%-team guarantee.
    """
    names = list(sector_names)
    listing = "\n".join(f"- {s}" for s in names)

    def _validate(o):
        got = o.get("sectors")
        if not isinstance(got, list):
            raise ValueError("sectors must be a list")
        picked = [s for s in got if s in names][:2]
        return {"sectors": picked}

    prompt = (
        f'A user describes a work task: "{text}"\n\n'
        "Which 1-2 sectors from this exact list would contain the OCCUPATIONAL "
        "ROLES best suited to PERFORM this task? Weigh the functional nature of "
        "the work over the user's own industry when they differ — bookkeeping "
        "tasks are performed by Accountancy roles even if the user runs a "
        "restaurant; logging patent disclosures is Intellectual Property work "
        "even inside a manufacturing firm. When the task IS industry-specific "
        "(e.g. rostering truck drivers), the user's industry is the right "
        "answer. Verbatim names only; empty list if genuinely unclear:\n"
        + listing + "\n\n"
        'Return STRICT JSON: {"sectors": ["<name>", ...]}'
    )
    obj = call_llm_json(
        "sector_infer",
        [{"role": "user", "content": prompt}],
        validate=_validate,
        temperature=0.0,
        max_tokens=256,
    )
    return obj["sectors"]


def _validate_team_recommend(o):
    tr = TeamRecommend.model_validate(o)
    if not (1 <= len(tr.team) <= 8):
        raise ValueError("team must have 1-8 agents")
    for a in tr.team:
        if a.stage < 1:
            raise ValueError("stage must be >= 1")
        for code, lvl in a.skill_level_overrides.items():
            if not (1 <= int(lvl) <= 6):
                raise ValueError(f"override level out of range: {code}={lvl}")
    return tr


def team_recommend(brief, candidates, functions_needed=None, must_cover=None):
    """Phase 1: assemble a 1-8 agent team from ONLY the given candidate roles.

    brief: dict (the intake Brief). candidates: list[{n, role, sector, matched_on}].
    functions_needed: unused — kept only for call-signature stability. The
    per-function seed roles (B1/B2) already influence this call indirectly by
    being present in `candidates`; the composer is deliberately NOT told to
    cover every identified function (mandating one-agent-per-function was the
    confirmed cause of team bloat/genericization in an earlier iteration).
    Team sizing is governed purely by the lean-team guidance below.

    must_cover: optional list of candidate list-numbers (n) that each cover a
    single KEY function the first composition left unstaffed (B4 repair). When
    set, the composer is told its team MUST include at least one of them. Kept
    narrow (one key function) on purpose — see the bloat caveat above.
    Returns TeamRecommend. The LLM never names a role not in `candidates`.
    """
    listing = "\n".join(
        f"{c['n']}. {c['role']}  ({c.get('sector', '?')})"
        for c in candidates
    )
    brief_txt = str(brief)
    prompt = (
        "A user wants to staff a multi-agent team. Their intake brief (JSON):\n"
        f"{brief_txt}\n"
        "REAL catalogue roles available — choose ONLY from these, by list number:\n"
        f"{listing}\n\n"
        "Assemble a team of 1-8 agents to deliver the brief end-to-end. For each agent:\n"
        "- n: the list number of the chosen role (must be one of the numbers above);\n"
        "  NEVER use the same list number twice — every agent must be a different role\n"
        "- stage: 1 = first, increasing along the delivery flow\n"
        "- squad: a short squad name (e.g. 'Data & AI', 'Build & Platform', 'Delivery')\n"
        "- skill_level_overrides: {code: level} ONLY for the few skills whose default\n"
        "  required level the brief clearly changes; empty {} if none\n"
        "- rationale: one short sentence. User-facing — never cite internal skill\n"
        "  codes, job codes, SSOC, SkillsFuture, or any competency-framework name;\n"
        "  refer to roles and skills by name only\n"
        "Team sizing: first list (mentally) the DISTINCT FUNCTIONS the brief needs\n"
        "— e.g. gather inputs, do the core work, check/report — then staff ONE agent\n"
        "per distinct function. Use the smallest team that covers every function:\n"
        "one agent only when one role genuinely covers them all; never pad with\n"
        "extra agents to look thorough, and never leave a needed function unstaffed.\n"
        "Prefer hands-on operational/executive-level roles; include director/head-\n"
        "level roles ONLY when the brief genuinely needs governance.\n"
        + (
            "MANDATORY: the brief needs a core function that only these list "
            f"numbers cover: {must_cover}. Your team MUST include at least one of "
            "them — pick the single best fit and staff it.\n"
            if must_cover else ""
        )
        + 'Return STRICT JSON: {"team": [{"n": <int>, "stage": <int>, "squad": "<str>", '
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


def _relabel_backedges_to_feedback(wh, agent_ids):
    """Deterministically relabel the minimal set of cycle-closing edges in
    `wh.handoffs` as the ceremony 'feedback loop', so the graph of remaining
    non-feedback edges is acyclic. Mutates `wh` in place. Returns the count
    of handoffs relabeled.

    Only edges that count toward the DAG are considered: ceremony !=
    'feedback loop' AND both endpoints are real agent_ids (not 'external').
    This includes self-loops (from == to), which are trivially back-edges.

    A single DFS pass over the internal non-feedback edges, visiting nodes
    and adjacency lists in sorted order for determinism, finds every edge
    that closes a cycle (points at a node currently on the recursion stack)
    and relabels the underlying Handoff objects.
    """
    nodes = sorted(agent_ids)
    node_set = set(agent_ids)

    # Build adjacency: node -> sorted list of (to_node, handoff_index) for
    # internal, non-feedback edges (self-loops included).
    adj = {a: [] for a in nodes}
    for idx, h in enumerate(wh.handoffs):
        if h.ceremony == "feedback loop":
            continue
        if h.from_agent not in node_set or h.to_agent not in node_set:
            continue
        adj[h.from_agent].append((h.to_agent, idx))
    for a in nodes:
        adj[a].sort(key=lambda pair: pair[0])

    state = {a: 0 for a in nodes}  # 0=unvisited, 1=on-stack, 2=done
    backedge_indices = set()

    def dfs(u):
        state[u] = 1
        for v, idx in adj[u]:
            if idx in backedge_indices:
                continue
            if state[v] == 1 or u == v:
                backedge_indices.add(idx)
            elif state[v] == 0:
                dfs(v)
        state[u] = 2

    for a in nodes:
        if state[a] == 0:
            dfs(a)

    for idx in backedge_indices:
        wh.handoffs[idx].ceremony = "feedback loop"

    # Safety net: confirm the remaining non-feedback subgraph is acyclic.
    remaining_adj = {a: set() for a in nodes}
    indeg = {a: 0 for a in nodes}
    for idx, h in enumerate(wh.handoffs):
        if idx in backedge_indices or h.ceremony == "feedback loop":
            continue
        if h.from_agent not in node_set or h.to_agent not in node_set:
            continue
        if h.to_agent not in remaining_adj[h.from_agent]:
            remaining_adj[h.from_agent].add(h.to_agent)
            indeg[h.to_agent] += 1
    queue = [a for a in nodes if indeg[a] == 0]
    seen = 0
    while queue:
        n = queue.pop()
        seen += 1
        for m in remaining_adj[n]:
            indeg[m] -= 1
            if indeg[m] == 0:
                queue.append(m)
    assert seen == len(nodes), "back-edge relabeling failed to make the graph acyclic"

    return len(backedge_indices)


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
    # The LLM sometimes produces a cyclic handoff graph. Deterministically and
    # losslessly relabel the minimal cycle-closing edges as 'feedback loop'
    # (the ceremony validate_handoff_graph already exempts as the allowed
    # back-edge) instead of dropping edges or crashing the wire.
    _relabel_backedges_to_feedback(wh, agent_ids)
    handoff_dicts = [
        {"from_agent": h.from_agent, "to_agent": h.to_agent, "ceremony": h.ceremony,
         "artifact": h.artifact, "description": h.description}
        for h in wh.handoffs
    ]
    validate_handoff_graph(handoff_dicts, agent_ids)  # safety net: should now pass
    return wh


_FUNCTION_DF_CACHE = None


def _function_df():
    """Document-frequency of each function_id over role_functions (Phase A's
    tagging table): fraction of ALL tagged roles carrying that function_id.
    Cached once per process (same pattern as classify._INDEX — the tagging
    table is effectively static at process lifetime).

    Used as a deterministic guard against ubiquitous generic functions
    (client-relationship-management, business-intelligence-reporting, etc.)
    that identify_functions over-picks despite prompt instructions: any
    function tagged on a large fraction of the whole catalog carries near
    zero distinguishing signal for role retrieval.

    Returns {} on any failure (missing table, empty catalog, import cycle) —
    callers MUST treat that as "no drop" so this never breaks the pipeline.
    """
    global _FUNCTION_DF_CACHE
    if _FUNCTION_DF_CACHE is None:
        try:
            from app import db  # local import — avoids a hard app.db dependency at module load
            total_row = db.query(
                "SELECT COUNT(DISTINCT role_id) AS n FROM role_functions", one=True,
            )
            total = (total_row["n"] if total_row else 0) or 0
            if total <= 0:
                _FUNCTION_DF_CACHE = {}
            else:
                rows = db.query(
                    "SELECT function_id, COUNT(DISTINCT role_id) AS n "
                    "FROM role_functions GROUP BY function_id",
                )
                _FUNCTION_DF_CACHE = {r["function_id"]: r["n"] / total for r in rows}
        except Exception:  # noqa: BLE001 — degrade to "no drop", never break the pipeline
            _FUNCTION_DF_CACHE = {}
    return _FUNCTION_DF_CACHE


def _validate_identify_functions(taxonomy_ids):
    """Return a validator bound to the closed taxonomy id set (B1). Drops any id
    not in the taxonomy; raises if nothing valid remains so call_llm_json retries
    (and eventually the caller's own try/except falls back to plain retrieval).

    After taxonomy filtering, also applies the function-IDF guard: drops any
    function whose document-frequency fraction (over role_functions) is > 0.20
    — a code-level backstop against near-signal-free generic functions that
    survives even if the LLM ignores the prompt's "don't pad with generic
    functions" instruction. If dropping would leave zero functions, the single
    lowest-DF (most distinguishing) one is kept instead of raising.
    """
    def _validate(o):
        if not isinstance(o, dict) or not isinstance(o.get("functions"), list):
            raise ValueError('expected {"functions": [...]}')
        picked = [f for f in o["functions"] if f in taxonomy_ids]
        # de-dup, keep order, cap at 2
        seen = set()
        out = []
        for f in picked:
            if f not in seen:
                seen.add(f)
                out.append(f)
            if len(out) >= 2:
                break
        if not out:
            raise ValueError("no valid function ids after validation against taxonomy")
        df = _function_df()
        if df:
            kept = [f for f in out if df.get(f, 0.0) <= 0.20]
            if kept:
                out = kept
            else:
                # everything returned is generic — keep the least-generic one
                out = [min(out, key=lambda f: df.get(f, 0.0))]
        return {"functions": out}
    return _validate


def identify_functions(brief_text, taxonomy):
    """B1: decompose a task/brief into the 1-2 CORE canonical business FUNCTIONS
    the work requires (data/function_taxonomy.json), INCLUDING implied functions
    not stated verbatim (e.g. "auditor coming in 2 weeks" implies a compliance/
    audit-prep function even though the brief never says "audit").

    This is a RECALL SAFETY-NET, not a team-size driver: it exists so the
    candidate slate is seeded with the one specific role the task actually
    needs, not so team_recommend staffs one agent per returned function. Kept
    deliberately narrow (1-2 ids) — over-decomposing into 3-4 functions per
    task, padded with generic cross-cutting functions, was the confirmed root
    cause of team bloat/genericization in an earlier iteration.

    taxonomy: the loaded list[{id, name, definition, everyday_phrasings}]
    (Phase A's data/function_taxonomy.json). Returns {"functions": [id, ...]},
    1-2 ids strictly drawn from taxonomy ids (after the taxonomy + DF-guard
    validation in _validate_identify_functions).

    Failure mode: this raises (LLMError or the last validation error) on total
    failure — by design. The 100%-team guarantee is sacred, so the CALLER
    (team_service.recommend) must wrap this in try/except and treat any
    exception as functions=[], falling back to plain keyword retrieval exactly
    as before this feature existed. This function itself never swallows errors.
    """
    taxonomy_ids = {e["id"] for e in taxonomy}
    listing = "\n".join(f"- {e['id']} — {e['name']}" for e in taxonomy)
    prompt = (
        f'A user describes a task/brief for an agentic team:\n"""\n{brief_text}\n"""\n\n'
        "From the CANONICAL BUSINESS FUNCTION list below, identify the 1-2 CORE "
        "functions that DISTINGUISH this specific task — the actual work being "
        "requested. Do NOT include generic cross-cutting support/management "
        "functions (stakeholder engagement, people/team management, project "
        "management, data analytics, business intelligence/reporting, continuous "
        "improvement, quality management, compliance) UNLESS the task is "
        "SPECIFICALLY and primarily about that function.\n\n"
        "Include a function that is IMPLIED by the work even if not stated "
        "outright — e.g. \"our auditor is coming in 2 weeks\" implies a "
        "compliance/audit-preparation function even though the word \"audit\" isn't "
        "the task itself; \"chase up people who owe me money\" implies accounts-"
        "receivable/collections even though the brief never says \"accounts receivable\".\n\n"
        "Every id you return MUST be copied exactly (the part before the — ) from "
        "this list:\n" + listing + "\n\n"
        'Return STRICT JSON: {"functions": ["id-1", "id-2"]} — 1 to 2 ids, the '
        "most important (most distinguishing, least generic) first."
    )
    raw = call_llm_json(
        "identify_functions",
        [{"role": "user", "content": prompt}],
        validate=_validate_identify_functions(taxonomy_ids),
        temperature=0.1,
        max_tokens=512,
    )
    return raw


def _validate_artifacts(o):
    """Validate the raw dict the LLM returns for identify_artifacts."""
    if not isinstance(o, dict) or "artifacts_needed" not in o:
        raise ValueError("expected {artifacts_needed: [...] }")
    arts = o["artifacts_needed"]
    if not isinstance(arts, list):
        raise ValueError("artifacts_needed must be a list")
    out = []
    for a in arts:
        am = Artifact.model_validate(a)
        if am.kind not in ARTIFACT_KINDS:
            raise ValueError(f"artifact kind must be one of {ARTIFACT_KINDS}, got {am.kind!r}")
        out.append({"kind": am.kind, "description": am.description})
    if not out:
        out = [{"kind": "none", "description": "No external artifact required."}]
    return {"artifacts_needed": out}


def identify_artifacts(use_case):
    """One-shot: classify what input artifacts the user should provide for a task.

    Returns a list of {kind, description} dicts (kind in ARTIFACT_KINDS). Used by
    team_service.recommend so the one-shot chat path can surface a structured
    "what to provide" prompt — the hardening over free-text intake questions.
    """
    prompt = (
        "A user described a task they want an agentic team to handle:\n\n"
        f"\"\"\"\n{use_case}\n\"\"\"\n\n"
        "Identify what input ARTIFACTS the build will genuinely need from the user "
        "to do this work well. `kind` MUST be one of: "
        + ", ".join(ARTIFACT_KINDS) + " —\n"
        "  sample = a worked example to mimic (e.g. a past quote, a sample excel);\n"
        "  blank_format = a blank template/form/format to fill (e.g. the report "
        "format management expects, a KYC checklist form);\n"
        "  past_documents = a corpus of past documents to mine (e.g. past proposals, "
        "an old contract repository);\n"
        "  database = a live database/system of record to read or write (e.g. the "
        "CRM export, the billing system, a shipment DB);\n"
        "  none = pure reasoning, no external artifact needed.\n\n"
        "Only list artifacts genuinely required for THIS task. If none, return "
        '[{"kind": "none", "description": "No external artifact required."}].\n'
        'Return STRICT JSON: {"artifacts_needed": [{"kind": "...", '
        '"description": "<short, <=18 words>"}]}'
    )
    raw = call_llm_json(
        "identify_artifacts",
        [{"role": "user", "content": prompt}],
        validate=_validate_artifacts,
        temperature=0.1,
        max_tokens=512,
    )
    return _validate_artifacts(raw)["artifacts_needed"]


# ──────────────────────────────────────────────────────────────────────────────
# Iteration 1 (OPERATIONS-INTAKE loop) — post-reveal ops-question interview.
# ──────────────────────────────────────────────────────────────────────────────
# The extraction taxonomy an ops-question answer gets tagged with (also
# app.services.team_service.USER_INPUT_KINDS' ops-specific subset — every
# `kind` this contract can propose is a valid user_inputs kind the SPA can
# PUT straight back to /api/team/{team_id}/inputs once the user answers).
OPS_QUESTION_KINDS = (
    "procedure",   # steps, decision rules, escalation, approvals
    "handoff",     # roles & handoffs — who does what, who signs off
    "threshold",   # numeric cutoffs, approval limits, thresholds
    "constraint",  # compliance / must-not rules, hard limits
    "metric",      # SLAs, targets, turnaround times
    "workaround",  # tribal knowledge — exceptions, "what we actually do"
)

OPS_QUESTION_NAME_MAX_CHARS = 60
OPS_QUESTION_TEXT_MAX_CHARS = 240


def _validate_ops_questions(max_questions):
    """Return a validator bound to `max_questions` (settings.OPS_QUESTIONS_MAX
    read at call time, not import time, so tests can monkeypatch it).

    0 questions is a VALID result — it means the LLM judged nothing left
    worth asking (every high-value taxonomy field is already covered by
    known_inputs). Anything beyond max_questions, a bad kind, or an
    over-length name/question fails validation and triggers call_llm_json's
    corrective retry.
    """
    def _validate(o):
        if not isinstance(o, dict) or not isinstance(o.get("questions"), list):
            raise ValueError('expected {"questions": [...]}')
        qs = o["questions"]
        if len(qs) > max_questions:
            raise ValueError(f"questions must be <= {max_questions} (got {len(qs)})")
        out = []
        seen_names = set()
        for i, q in enumerate(qs):
            if not isinstance(q, dict):
                raise ValueError(f"questions[{i}] must be an object")
            kind = (q.get("kind") or "").strip()
            name = (q.get("name") or "").strip()
            question = (q.get("question") or "").strip()
            if kind not in OPS_QUESTION_KINDS:
                raise ValueError(
                    f"questions[{i}].kind must be one of {OPS_QUESTION_KINDS} (got {kind!r})"
                )
            if not name:
                raise ValueError(f"questions[{i}].name is required")
            if len(name) > OPS_QUESTION_NAME_MAX_CHARS:
                raise ValueError(
                    f"questions[{i}].name must be <= {OPS_QUESTION_NAME_MAX_CHARS} chars"
                )
            if not question:
                raise ValueError(f"questions[{i}].question is required")
            if len(question) > OPS_QUESTION_TEXT_MAX_CHARS:
                raise ValueError(
                    f"questions[{i}].question must be <= {OPS_QUESTION_TEXT_MAX_CHARS} chars"
                )
            name_key = name.lower()
            if name_key in seen_names:
                continue  # de-dup by name, keep first occurrence — not a hard failure
            seen_names.add(name_key)
            out.append({"kind": kind, "name": name, "question": question})
        return {"questions": out}
    return _validate


def generate_ops_questions(use_case, team_roles, known_inputs):
    """Iteration 1 (OPERATIONS-INTAKE loop): after the team is revealed, ask
    AT MOST settings.OPS_QUESTIONS_MAX questions in ONE round — each one
    targeting a single missing HIGH-VALUE piece of operational knowledge from
    the extraction taxonomy (OPS_QUESTION_KINDS above) that the user hasn't
    already given us via known_inputs.

    use_case: str — the team's task/use case.
    team_roles: list[str] — the role names on the revealed team.
    known_inputs: list[{kind, name, content}] — the user's existing
    user_inputs (real-inputs intake + any prior ops-question answers); only
    kind/name/first-80-chars-of-content are shown to the LLM so the prompt
    stays small and PII-light-ish, and it's told never to re-ask about them.

    Returns {"questions": [{"kind", "name", "question"}, ...]} — 0..MAX
    entries. Zero is valid (nothing left worth asking / everything's already
    converged). Every question is business-plain, jargon-free, and
    answerable in 1-2 sentences (enforced by prompt instruction, not by a
    hard validator — length is only capped, not word-counted).
    """
    max_q = max(0, int(getattr(settings, "OPS_QUESTIONS_MAX", 3)))
    if max_q == 0:
        return {"questions": []}

    roles_listing = "\n".join(f"- {r}" for r in (team_roles or [])) or "(no roles listed)"
    known_listing = "\n".join(
        f"- [{(k.get('kind') or '?')}] {(k.get('name') or '?')}: "
        f"{(k.get('content') or '')[:80]}"
        for k in (known_inputs or [])
    ) or "(nothing provided yet)"

    prompt = (
        "A team of agents has just been assembled for a user's task. Before the "
        "team starts, we get ONE round of follow-up questions to fill in "
        "operational knowledge the team can't infer on its own — the specific "
        "rules, numbers, and know-how of how THIS user's operation actually "
        "runs, not general knowledge.\n\n"
        f"Task:\n\"\"\"\n{use_case or '(unspecified)'}\n\"\"\"\n\n"
        f"Team roles:\n{roles_listing}\n\n"
        f"Already known (do NOT ask about any of these again):\n{known_listing}\n\n"
        "From the taxonomy below, propose the 1 to " + str(max_q) + " HIGHEST-VALUE "
        "MISSING pieces of operational knowledge — the ones that would most "
        "change how the team actually does the work. Every question you propose "
        "MUST target something NOT already covered above. If everything of real "
        "value is already known, return zero questions — do not invent a "
        "question just to fill a slot.\n\n"
        "Taxonomy — `kind` MUST be one of:\n"
        "  procedure  = the steps, decision rules, escalation path, or approval "
        "flow for how a task gets done;\n"
        "  handoff    = which role does what, and who hands off to whom;\n"
        "  threshold  = a specific number that changes what happens — a cutoff, "
        "a limit, an approval trigger;\n"
        "  constraint = a hard rule the team must never break — compliance, "
        "policy, a must-not;\n"
        "  metric     = a target or turnaround time the work is measured "
        "against (an SLA, a deadline);\n"
        "  workaround = tribal knowledge — an exception or "
        "\"what we actually do\" that isn't written down anywhere.\n\n"
        "Writing rules for every question — these are shown DIRECTLY to a "
        "business owner, not a technical audience:\n"
        "  - plain business language only; NEVER use jargon, framework names, "
        "internal codes, or any competency/skills-taxonomy terminology;\n"
        "  - each question must be answerable in ONE or TWO sentences;\n"
        "  - `name` is a short label for the answer (<=8 words, e.g. "
        "\"Approval threshold\"); `question` is the actual question "
        "(one sentence, plain English).\n\n"
        'Return STRICT JSON: {"questions": [{"kind": "<one of the taxonomy '
        'kinds>", "name": "<short label>", "question": "<one plain-English '
        'question>"}]} — an empty list is a valid answer.'
    )
    raw = call_llm_json(
        "ops_questions",
        [{"role": "user", "content": prompt}],
        validate=_validate_ops_questions(max_q),
        temperature=0.3,
        max_tokens=1024,
    )
    return raw
