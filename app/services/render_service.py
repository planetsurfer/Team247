"""Phase 3 deliver — render per-agent spec markdown (adapted to the team's use
case + handoffs), version it with a content-hash + TTL dedup, and expose the
read paths the SPA needs: per-team specs list, per-agent spec, team zip, and
the org-chart payload for `renderTeam`/`drawTeamLines`.

Reuses `agent.generate_agent` (adapt_spec contract via `custom_instructions`),
`framework.get_skills/get_context/get_sector`, `team_service.get_team`, and
`handoff_service.get_handoffs`. Persistence goes through `app.db` (SQLite).

Dedup rule (PRODUCTION_APP_PLAN Risk 12 / `team_spec_versions`): a row is reused
iff (team_id, agent_id, render_inputs_hash) matches AND rendered_at is within
`settings.SPEC_TTL_DAYS` of now. `force=True` bypasses the TTL and re-renders a
new version. A model swap should be followed by a one-time `--force-all` job.
"""
from __future__ import annotations

import hashlib
import io
import json
import time
import datetime
import zipfile

from app import db, jobs, settings
from app.services import team_service, handoff_service
from app.services.team_service import TeamNotFound
import agent, framework, teamspec


_DEFAULT_HONESTY_NOTE = (
    "Two-track honesty: execution-verified skills are proven by running real "
    "code in isolated local subprocesses, graded against the official K&A "
    "rubric; rubric skills are LLM-judged against the same checklist and are "
    "never blended with executed scores."
)


def _hash(use_case, overrides, disabled, handoffs_touching) -> str:
    """sha256 hex of a canonical-JSON tuple of the render inputs. Same inputs →
    same hash → same spec is reused (within TTL)."""
    payload = json.dumps(
        [use_case, overrides, disabled, handoffs_touching],
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _parse_iso(s: str) -> datetime.datetime | None:
    if not s:
        return None
    try:
        return datetime.datetime.fromisoformat(s)
    except ValueError:
        return None


def _ttl_cutoff() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(
        days=settings.SPEC_TTL_DAYS
    )


def _compose_custom_instructions(use_case, a, hands) -> str:
    """Per-agent custom instructions: use case + stage/squad/produces/consumes +
    the handoff seams touching this agent (named by ceremony + artifact)."""
    stage = a.get("stage")
    squad = a.get("squad") or "—"
    produces = a.get("produces") or "—"
    consumes = a.get("consumes") or "—"
    seam = ", ".join(
        f"{h.get('ceremony', '')} -> {h.get('artifact', '') or '(no artifact)'}"
        f" [{h.get('from_agent', '')} -> {h.get('to_agent', '')}]"
        for h in hands
    ) or "(none)"
    return (
        f"Use case: {use_case}\n"
        f"This agent (stage {stage}, squad {squad}) produces: {produces}; "
        f"consumes from: {consumes}.\n"
        f"Handoffs touching this agent: {seam}."
    )


def _handoff_fingerprint(hands):
    """Reduce a handoff list to the stable fields that affect the spec, so a
    handoff description edit alone (cosmetic) doesn't bust the cache, but a
    ceremony/artifact/from/to change does."""
    return [
        {k: v for k, v in h.items() if k in ("from_agent", "to_agent",
                                            "ceremony", "artifact")}
        for h in hands
    ]


def _max_version(team_id, agent_id) -> int:
    row = db.query(
        "SELECT COALESCE(MAX(version), 0) AS mv FROM team_spec_versions "
        "WHERE team_id = ? AND agent_id = ?",
        (team_id, agent_id), one=True,
    )
    return int(row["mv"]) if row else 0


def _latest_spec_row(team_id, agent_id):
    """Latest team_spec_versions row for an agent, or None."""
    return db.query(
        "SELECT team_id, agent_id, version, spec_md, rendered_at, "
        "render_inputs_hash FROM team_spec_versions "
        "WHERE team_id = ? AND agent_id = ? "
        "ORDER BY version DESC LIMIT 1",
        (team_id, agent_id), one=True,
    )


def _generate_with_retry(role, skills, context, custom_instructions, sector, attempts=3):
    """agent.generate_agent has no retry wrapper — a transient LLM error would drop
    an agent. Wrap it: 3 attempts with backoff. Returns the spec markdown."""
    last = None
    for attempt in range(1, attempts + 1):
        try:
            return agent.generate_agent(role, skills, context, custom_instructions, sector)
        except Exception as e:  # noqa: BLE001 — retry on any failure
            last = e
            if attempt < attempts:
                time.sleep(2 ** (attempt - 1))
    raise last


def render(team_id, force=False):
    """Render (or reuse) per-agent spec markdown for a team. Returns
    {team_id, versions:[{agent_id, role, version, render_inputs_hash,
    rendered_at, spec_md_preview}]}.

    Reuse rule: if a team_spec_versions row matches (team_id, agent_id,
    render_inputs_hash) AND was rendered within settings.SPEC_TTL_DAYS, keep it
    (version unchanged). Otherwise call agent.generate_agent with composed
    custom_instructions, bump the version, and INSERT. `force=True` always
    re-renders a new version.

    Raises TeamNotFound (propagates from team_service.get_team)."""
    team = team_service.get_team(team_id)          # TeamNotFound propagates
    use_case = team.get("use_case") or ""
    handoffs = handoff_service.get_handoffs(team_id)

    cutoff = _ttl_cutoff()
    versions = []
    for a in team["agents"]:
        aid = a["agent_id"]
        role = a["role"]
        skills = framework.get_skills(role)
        context = framework.get_context(role)
        sector = (framework.get_sector(role).get("sector")
                  or context.get("sector") or "")
        hands = [h for h in handoffs
                 if h.get("from_agent") == aid or h.get("to_agent") == aid]
        h = _hash(use_case, a.get("skill_overrides", {}),
                  a.get("skill_disabled", []), _handoff_fingerprint(hands))

        # Reuse within TTL unless forced.
        reuse = None
        if not force:
            for row in db.query(
                "SELECT version, rendered_at FROM team_spec_versions "
                "WHERE team_id = ? AND agent_id = ? AND render_inputs_hash = ? "
                "ORDER BY version DESC",
                (team_id, aid, h),
            ):
                rendered_at = _parse_iso(row.get("rendered_at", ""))
                if rendered_at is not None and rendered_at >= cutoff:
                    reuse = row
                    break

        if reuse is not None:
            version = int(reuse["version"])
            rendered_at = reuse["rendered_at"]
            latest = _latest_spec_row(team_id, aid) or {}
            spec_md_preview = (latest.get("spec_md") or "")[:120]
        else:
            custom_instructions = _compose_custom_instructions(
                use_case, a, hands)
            spec_md = _generate_with_retry(
                role, skills, context, custom_instructions, sector)
            version = _max_version(team_id, aid) + 1
            rendered_at = _now()
            db.execute(
                "INSERT INTO team_spec_versions("
                "team_id, agent_id, version, spec_md, rendered_at, "
                "render_inputs_hash) VALUES(?, ?, ?, ?, ?, ?)",
                (team_id, aid, version, spec_md, rendered_at, h),
            )
            spec_md_preview = spec_md[:120]

        versions.append({
            "agent_id": aid,
            "role": role,
            "version": version,
            "render_inputs_hash": h,
            "rendered_at": rendered_at,
            "spec_md_preview": spec_md_preview,
        })

    return {"team_id": team_id, "versions": versions}


def render_async(team_id, force=False):
    """Submit render() to the background job runner; returns a job_id to poll."""
    return jobs.submit("render", render, team_id, force)


def specs_list(team_id):
    """Latest spec version per agent, joined to roles for display. Returns
    [{agent_id, role, version, render_inputs_hash, rendered_at}]."""
    agents = db.query(
        "SELECT ta.agent_id, ta.sort_order, r.role "
        "FROM team_agents ta JOIN roles r ON r.role_id = ta.role_id "
        "WHERE ta.team_id = ? ORDER BY ta.sort_order, ta.agent_id",
        (team_id,),
    )
    out = []
    for a in agents:
        row = _latest_spec_row(team_id, a["agent_id"])
        if row is None:
            continue
        out.append({
            "agent_id": row["agent_id"],
            "role": a["role"],
            "version": int(row["version"]),
            "render_inputs_hash": row["render_inputs_hash"],
            "rendered_at": row["rendered_at"],
        })
    return out


def get_spec(team_id, agent_id):
    """Latest spec for one agent. Returns {agent_id, role, version, spec_md},
    or None if no spec (or no such agent) exists — router maps None → 404."""
    role_row = db.query(
        "SELECT r.role FROM team_agents ta "
        "JOIN roles r ON r.role_id = ta.role_id "
        "WHERE ta.team_id = ? AND ta.agent_id = ?",
        (team_id, agent_id), one=True,
    )
    if role_row is None:
        return None
    row = _latest_spec_row(team_id, agent_id)
    if row is None:
        return None
    return {
        "agent_id": agent_id,
        "role": role_row["role"],
        "version": int(row["version"]),
        "spec_md": row["spec_md"],
    }


def download_zip(team_id) -> bytes:
    """In-memory zip: one {agent_id}.md per agent (latest spec_md) + a
    README.md (team use_case + agent list). Returns the zip bytes."""
    team = db.query(
        "SELECT team_id, use_case, name FROM teams WHERE team_id = ?",
        (team_id,), one=True,
    )
    if team is None:
        raise TeamNotFound(f"team {team_id} not found")

    agents = db.query(
        "SELECT ta.agent_id, ta.sort_order, r.role "
        "FROM team_agents ta JOIN roles r ON r.role_id = ta.role_id "
        "WHERE ta.team_id = ? ORDER BY ta.sort_order, ta.agent_id",
        (team_id,),
    )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        # README first so it sits at the top of the archive.
        readme = ["# Team specs", "",
                  f"Team: {team.get('name') or team['team_id']}",
                  f"Use case: {team.get('use_case') or '(none)'}", "",
                  "Agents:", ""]
        for a in agents:
            row = _latest_spec_row(team_id, a["agent_id"])
            if row is None:
                readme.append(f"- {a['agent_id']} — {a['role']} (no spec yet)")
                continue
            readme.append(
                f"- {a['agent_id']} — {a['role']} (v{row['version']})")
            z.writestr(f"{a['agent_id']}.md", row["spec_md"])
        z.writestr("README.md", "\n".join(readme) + "\n")
    return buf.getvalue()


def chart(team_id):
    """Org-chart payload matching dashboard.load_real() team shape, for
    `renderTeam` + `drawTeamLines`. Reads teams + team_agents + team_handoffs
    + cards. Returns {role, sector, track, rounds, agents, handoffs,
    honesty_note}."""
    team = db.query(
        "SELECT team_id, use_case, name FROM teams WHERE team_id = ?",
        (team_id,), one=True,
    )
    if team is None:
        raise TeamNotFound(f"team {team_id} not found")

    agents = db.query(
        "SELECT ta.agent_id, ta.stage, ta.squad, ta.produces, ta.consumes, "
        "ta.anchor, r.role, r.sector, r.track "
        "FROM team_agents ta JOIN roles r ON r.role_id = ta.role_id "
        "WHERE ta.team_id = ? ORDER BY ta.sort_order, ta.agent_id",
        (team_id,),
    )
    handoffs = handoff_service.get_handoffs(team_id)

    # Top-level role/sector/track: the team's purpose + the first agent's
    # sector/track (dashboard.load_real emits a single role's context here;
    # for a team we surface the use case + the dominant sector).
    first = agents[0] if agents else {}
    use_case = team.get("use_case") or team.get("name") or ""

    return {
        "role": use_case,
        "sector": first.get("sector", ""),
        "track": first.get("track", ""),
        "rounds": len(agents),
        "agents": [
            {
                "id": a["agent_id"],
                "stage": a["stage"],
                "role": a["role"],
                "produces": a.get("produces") or "",
                "consumes": a.get("consumes") or "",
                "squad": a.get("squad") or "",
                "anchor": bool(a["anchor"]),
            }
            for a in agents
        ],
        "handoffs": [
            {
                "from_agent": h.get("from_agent", ""),
                "to_agent": h.get("to_agent", ""),
                "ceremony": h.get("ceremony", ""),
                "artifact": h.get("artifact") or "",
                "description": h.get("description") or "",
            }
            for h in handoffs
        ],
        "honesty_note": team.get("honesty_note") or _DEFAULT_HONESTY_NOTE,
    }
