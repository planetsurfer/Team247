"""Bundle-generation flow, agent selection, deterministic checks, and the
`claude` CLI runner — the machinery `__main__.py` drives per archetype.
"""
from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import tempfile
import time

from app import db, llm_contracts
from app.services import handoff_service, skill_bundle_service, team_service

# Exact heading text `skill_bundle_service.generate_task_overlay` emits (see
# app/services/skill_bundle_service.py:468-475) — kept verbatim so a wording
# drift in the generator breaks this check rather than silently rotting it.
REQUIRED_HEADINGS = (
    "## For This Task",
    "### Inputs",
    "### Deliverable",
    "### Required real inputs (not included in this scaffold)",
    "### Applying this capability to the task",
    "### Success criteria",
)

# Exact fallback strings from _inputs_section/_deliverable_section
# (skill_bundle_service.py:319-320, 331) — their presence means the overlay
# had nothing wired, which is itself a bundle-quality finding.
INPUTS_FALLBACK = (
    "No upstream artifacts or user-provided inputs were identified "
    "for this task."
)
DELIVERABLE_FALLBACK = "No downstream handoff artifact is wired for this agent yet."

# Coherence keys echoed into the record (full detail lists kept small on
# purpose — team sizes here are 1-8 agents, never large).
COHERENCE_ECHO_KEYS = (
    "n_agents", "n_handoffs", "coherent_pct",
    "isolated_agents", "self_loop_only_agents", "no_inbound", "no_outbound",
)

CLAUDE_DISALLOWED_TOOLS = "WebSearch,WebFetch"


# ── bundle generation ───────────────────────────────────────────────────────
def generate_bundle(scenario, *, force_regen: bool = False,
                     reuse_team_id: str | None = None) -> dict:
    """team_service.recommend -> handoff_service.wire -> compose_team_bundles.

    When reuse_team_id is set (--reuse-teams), SKIP team_service.recommend and
    handoff_service.wire entirely and reuse that team's existing staffing +
    wiring instead — this is the paired-generator A/B mode: fresh bundles
    composed on the SAME team/wiring as a prior run, so a diff between two
    runs isolates the generator/prompt change rather than team-composition
    noise. The team is read straight from app.db (team_agents joined to
    roles) and handoff_service.get_handoffs; both are verified non-empty
    before proceeding, since a stale/foreign team_id would otherwise silently
    compose bundles for zero agents or with no I/O contract.

    Returns {team_id, roles, agents, artifacts_needed, coherence, bundles,
    gen_seconds}. Raises whatever the pipeline raises (LLMError, ValueError,
    TeamNotFound, or "team has no agents") — the caller (`run_archetype`)
    turns that into a status="error" record. When reuse_team_id is set and
    the team can't actually be reused (no team_agents rows, or no wired
    handoffs), raises ValueError naming the cause, same treatment.
    """
    t0 = time.monotonic()

    if reuse_team_id:
        team_id = reuse_team_id
        rows = db.query(
            "SELECT ta.agent_id, r.role, ta.stage, ta.squad FROM team_agents ta "
            "JOIN roles r ON r.role_id = ta.role_id "
            "WHERE ta.team_id = ? ORDER BY ta.sort_order, ta.agent_id",
            (team_id,),
        )
        if not rows:
            raise ValueError(
                f"--reuse-teams: team {team_id!r} has no team_agents rows in "
                "the DB (team missing or not staffed) — cannot reuse"
            )
        agents = [{"agent_id": r["agent_id"], "role": r["role"],
                   "stage": r["stage"], "squad": r["squad"]} for r in rows]

        handoffs = handoff_service.get_handoffs(team_id)
        if not handoffs:
            raise ValueError(
                f"--reuse-teams: team {team_id!r} has no wired handoffs "
                "(handoff_service.get_handoffs returned empty) — cannot reuse"
            )

        # Best-effort re-derivation of artifacts_needed: older results.jsonl
        # records (before this field was persisted at the top level — see
        # __main__.py) never saved it, so recompute it fresh via the same
        # identify_artifacts call team_service.recommend originally made. This
        # is a NEW LLM call, not a replay of the original one, so treat it as
        # APPROXIMATE — it can differ from what the original recommend() call
        # saw. Runs from now on persist artifacts_needed directly, making
        # future reuse exact instead of approximate.
        try:
            artifacts_needed = llm_contracts.identify_artifacts(scenario.use_case)
        except Exception:  # noqa: BLE001 — best-effort, never block reuse on this
            artifacts_needed = []
    else:
        team = team_service.recommend(use_case=scenario.use_case)
        team_id = team["team_id"]
        artifacts_needed = team.get("artifacts_needed") or []
        agents = team["agents"]

        handoff_service.wire(team_id, use_case=scenario.use_case)

    roles = [a["role"] for a in agents]

    if force_regen:
        # Force both LLM caches to regenerate, then let compose_bundle do the
        # actual assembly — it owns the composition structure (task-overlay
        # first, base demoted to reference), and duplicating that here rotted
        # once already when the structure changed.
        bundles = []
        for a in agents:
            ctx = skill_bundle_service.build_agent_context(
                team_id, a["agent_id"], artifacts_needed)
            base_md = skill_bundle_service.generate_base_skill(ctx["role"], force=True)
            skill_bundle_service.generate_task_overlay(
                scenario.use_case, ctx, base_md, force=True)  # warms the fresh cache
            skill_md = skill_bundle_service.compose_bundle(
                team_id, a["agent_id"], scenario.use_case, artifacts_needed)
            bundles.append({"agent_id": a["agent_id"], "role": a["role"], "skill_md": skill_md})
    else:
        result = skill_bundle_service.compose_team_bundles(
            team_id, scenario.use_case, artifacts_needed)
        bundles = result["bundles"]

    coherence = skill_bundle_service.check_team_coherence(team_id)

    return {
        "team_id": team_id,
        "roles": roles,
        "agents": agents,
        "artifacts_needed": artifacts_needed,
        "coherence": coherence,
        "bundles": bundles,
        "gen_seconds": round(time.monotonic() - t0, 1),
    }


# ── agent selection ─────────────────────────────────────────────────────────
def select_target_agent(bundles: list[dict], role_keywords: tuple[str, ...]) -> dict:
    """First keyword (priority order) with a case-insensitive substring match
    on an agent's role. No match -> first agent + selection="fallback_primary"
    (itself a finding: the team lacked the expected role for this archetype).

    Returns {bundle, selection, matched_keyword}.
    """
    for kw in role_keywords:
        kw_lower = kw.lower()
        for b in bundles:
            if kw_lower in (b.get("role") or "").lower():
                return {"bundle": b, "selection": "keyword_match", "matched_keyword": kw}
    return {"bundle": bundles[0], "selection": "fallback_primary", "matched_keyword": None}


# ── deterministic checks ────────────────────────────────────────────────────
def deterministic_checks(skill_md: str, coherence: dict, agent_id: str) -> dict:
    """Pure-data checks over one agent's composed SKILL.md + the team's
    coherence report. No LLM. Returns {passed, checks{}, errors[]}."""
    checks: dict[str, bool] = {}
    errors: list[str] = []

    well_formed, wf_errors = skill_bundle_service.validate_skill_md(skill_md)
    checks["well_formed"] = well_formed
    errors.extend(f"well_formed: {e}" for e in wf_errors)

    for heading in REQUIRED_HEADINGS:
        present = heading in (skill_md or "")
        checks[f"heading:{heading}"] = present
        if not present:
            errors.append(f"missing heading {heading!r}")

    checks["inputs_wired"] = INPUTS_FALLBACK not in (skill_md or "")
    if not checks["inputs_wired"]:
        errors.append("inputs section fell back to the 'no upstream artifacts' placeholder")

    checks["deliverable_wired"] = DELIVERABLE_FALLBACK not in (skill_md or "")
    if not checks["deliverable_wired"]:
        errors.append("deliverable section fell back to the 'not wired yet' placeholder")

    isolated = set(coherence.get("isolated_agents") or [])
    self_loop = set(coherence.get("self_loop_only_agents") or [])
    checks["agent_connected"] = agent_id not in isolated and agent_id not in self_loop
    if not checks["agent_connected"]:
        errors.append(f"agent {agent_id!r} is isolated or self-loop-only in the team's handoff wiring")

    return {"passed": not errors, "checks": checks, "errors": errors}


# ── claude CLI runner ───────────────────────────────────────────────────────
def run_claude(skill_md: str, prompt: str, fixtures: dict[str, str], *,
                claude_bin: str = "claude", model: str = "sonnet",
                timeout: int = 300) -> dict:
    """Run the bundle as an agent via `claude -p` headless, in a fresh temp
    cwd (no project CLAUDE.md contamination). Degrade, never crash.

    Verified argv for claude CLI v2.1.170 — no `--max-turns` (does not exist
    in this CLI version); `timeout` is the runaway guard instead.

    Returns {status, returncode, stderr_tail, deliverable, deliverable_chars,
    seconds}. status is one of "ok" / "harness_timeout" / "harness_error".
    """
    tmpdir = tempfile.mkdtemp(prefix="dropin_")
    t0 = time.monotonic()
    try:
        for fname, content in fixtures.items():
            (pathlib.Path(tmpdir) / fname).write_text(content)
        (pathlib.Path(tmpdir) / "SKILL.md").write_text(skill_md)

        argv = [
            claude_bin, "-p", prompt,
            "--append-system-prompt", skill_md,
            "--model", model,
            "--output-format", "text",
            "--disallowedTools", CLAUDE_DISALLOWED_TOOLS,
        ]
        # Sanitized child env: when this eval itself runs inside a Claude Code
        # session, the parent exports ANTHROPIC_BASE_URL / CLAUDE_CODE_* vars
        # scoped to the harness's own auth context; a child `claude -p`
        # inheriting them 401s ("Invalid authentication credentials"). Stripping
        # them makes the child authenticate from the user's own stored CLI
        # credentials, exactly as in a fresh terminal — the honest drop-in test.
        child_env = {k: v for k, v in os.environ.items()
                     if not (k.startswith("ANTHROPIC") or k.startswith("CLAUDE"))}
        try:
            cp = subprocess.run(
                argv, cwd=tmpdir, capture_output=True, text=True,
                timeout=timeout, env=child_env,
            )
        except subprocess.TimeoutExpired:
            return {
                "status": "harness_timeout", "returncode": None, "stderr_tail": None,
                "deliverable": None, "deliverable_chars": 0,
                "seconds": round(time.monotonic() - t0, 1),
            }
        except FileNotFoundError:
            return {
                "status": "harness_error", "returncode": None,
                "stderr_tail": f"claude binary not found: {claude_bin!r}",
                "deliverable": None, "deliverable_chars": 0,
                "seconds": round(time.monotonic() - t0, 1),
            }

        seconds = round(time.monotonic() - t0, 1)
        stdout = (cp.stdout or "").strip()
        if cp.returncode != 0 or not stdout:
            # claude -p writes auth/quota errors to STDOUT (observed: the 401
            # message), so include both streams in the diagnostic tail.
            diag = ((cp.stderr or "")[-400:] + " | stdout: " + (cp.stdout or "")[-400:]).strip(" |")
            return {
                "status": "harness_error", "returncode": cp.returncode,
                "stderr_tail": diag,
                "deliverable": None, "deliverable_chars": 0, "seconds": seconds,
            }
        return {
            "status": "ok", "returncode": cp.returncode, "stderr_tail": None,
            "deliverable": stdout, "deliverable_chars": len(stdout), "seconds": seconds,
        }
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ── per-archetype orchestration ─────────────────────────────────────────────
def run_archetype(scenario, args, run_id: str, *, reuse_team_id: str | None = None,
                   reuse_requested: bool = False) -> dict:
    """generate -> select -> deterministic -> comp judge -> (unless
    --skip-exec) prompt + run_claude -> work judge.

    reuse_team_id/reuse_requested implement --reuse-teams (see __main__.py):
    reuse_requested is True whenever --reuse-teams was passed at all;
    reuse_team_id is the team_id resolved for THIS archetype from that source
    file's records, or None if no matching record was found. Passing
    reuse_requested=True with reuse_team_id=None is itself a hard error (the
    paired A/B is meaningless without the SAME team) rather than silently
    falling back to a fresh team_service.recommend.

    Bundle-gen exceptions (LLMError, wire ValueError, empty team, reuse
    ValueError, ...) -> status="error" (retried on --resume). A failed claude
    run keeps status="ok" with a degraded harness.status — the
    comprehensiveness half of the record stays valid and is not re-paid on
    --resume.
    """
    # Local import: judge.py pulls in app.llm_contracts at import time, which
    # needs config.py's .env-derived client — keep it out of module-import
    # time the same way tests/simulation keeps its harness imports inside
    # the test function.
    from tests.agent_dropin import judge as judge_mod
    from tests.agent_dropin import scenarios as scenarios_mod

    record = {
        "run_id": run_id, "archetype": scenario.id, "label": scenario.label,
        "use_case": scenario.use_case, "status": "ok", "error": None,
        "timing": {}, "team": None, "skill_md_chars": None,
        "artifacts_needed": None, "reused_team": False,
        "deterministic": None, "comp_judge": None, "harness": None,
        "deliverable": None, "work_judge": None,
    }
    t0 = time.monotonic()

    if reuse_requested and reuse_team_id is None:
        record["status"] = "error"
        record["error"] = (
            f"--reuse-teams: no status=ok record for archetype {scenario.id!r} "
            "found in the reuse source file — cannot pair without a team_id"
        )[:400]
        record["timing"]["seconds"] = round(time.monotonic() - t0, 1)
        return record

    try:
        gen = generate_bundle(scenario, force_regen=args.force_regen,
                              reuse_team_id=reuse_team_id)
    except Exception as e:  # noqa: BLE001 — LLMError/ValueError/TeamNotFound/etc all -> status=error
        record["status"] = "error"
        record["error"] = f"{type(e).__name__}: {e}"[:400]
        record["timing"]["seconds"] = round(time.monotonic() - t0, 1)
        return record

    record["timing"]["gen_seconds"] = gen["gen_seconds"]
    record["artifacts_needed"] = gen["artifacts_needed"]
    record["reused_team"] = bool(reuse_team_id)

    sel = select_target_agent(gen["bundles"], scenario.role_keywords)
    bundle = sel["bundle"]
    skill_md = bundle["skill_md"]
    coherence = gen["coherence"]

    record["team"] = {
        "team_id": gen["team_id"],
        "roles": gen["roles"],
        "n_agents": len(gen["roles"]),
        "selected_agent_id": bundle["agent_id"],
        "selected_role": bundle["role"],
        "selection": sel["selection"],
        "coherence": {k: coherence.get(k) for k in COHERENCE_ECHO_KEYS},
    }
    record["skill_md_chars"] = len(skill_md)
    # Transient, not part of the persisted record schema (only skill_md_chars
    # is) — __main__.py pops this to write skills/<id>.SKILL.md, the "exact
    # bundle tested" artifact, then drops it before the record is dumped to
    # results.jsonl.
    record["_skill_md"] = skill_md

    record["deterministic"] = deterministic_checks(skill_md, coherence, bundle["agent_id"])
    record["comp_judge"] = judge_mod.judge_comprehensiveness(scenario.use_case, skill_md)

    if args.skip_exec:
        record["harness"] = {
            "status": "skipped", "returncode": None, "stderr_tail": None,
            "deliverable_chars": 0, "seconds": 0.0,
        }
    else:
        ctx = skill_bundle_service.build_agent_context(
            gen["team_id"], bundle["agent_id"], gen["artifacts_needed"])
        prompt = scenarios_mod.build_scenario_prompt(scenario, ctx.get("produces"))

        harness_result = run_claude(
            skill_md, prompt, scenario.fixtures,
            claude_bin=args.claude_bin, model=args.claude_model, timeout=args.timeout,
        )
        record["harness"] = {k: harness_result[k] for k in
                              ("status", "returncode", "stderr_tail",
                               "deliverable_chars", "seconds")}
        record["timing"]["exec_seconds"] = harness_result["seconds"]
        record["deliverable"] = harness_result.get("deliverable")

        if harness_result["status"] == "ok":
            record["work_judge"] = judge_mod.judge_work_product(
                scenario, skill_md, record["deliverable"])

    record["timing"]["seconds"] = round(time.monotonic() - t0, 1)
    return record
