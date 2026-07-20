"""Iteration 1 + 2 of the skill-bundle enhancement.

Iteration 1 (unchanged below): REUSABLE, role-level SKILL.md documents in the
canonical Anthropic Agent Skills format (YAML frontmatter + markdown body),
grounded ONLY in the framework's official Knowledge & Ability (K&A) checklist
for that role via teamspec.skill_rows / teamspec._split_ka.

Iteration 2 (this section): the TASK OVERLAY — a thin, per-team adapter
layered on top of the reusable base skill. Design lesson from the iter-0
probe: the I/O contract (who hands what to whom) is the solid, grounded part
of a task-specific skill; step-by-step PROCEDURE is where hallucination
creeps in once you leave the K&A checklist. So the overlay splits cleanly:

  - the I/O contract + "required real inputs" manifest is built DETERMINISTICALLY
    straight from team_agents/roles + team_handoffs — no LLM, nothing to hallucinate.
  - the LLM is used ONLY for a thin narrative ("how the base capability applies
    here") + success criteria, explicitly forbidden from inventing procedures,
    tools, systems, thresholds or metrics beyond what the base skill and the
    I/O contract already ground.

build_agent_context()   -- deterministic agent/handoff lookup (DB + handoff_service)
generate_task_overlay() -- deterministic I/O sections + one grounded LLM subsection
compose_bundle()        -- task overlay FIRST, then base skill, one markdown document
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import re

import yaml

import teamspec
from app import db
from app.services import handoff_service
from config import llm_chat

CACHE_DIR = pathlib.Path("data/skill_cache/base")
OVERLAY_CACHE_DIR = pathlib.Path("data/skill_cache/overlay")
MAX_SKILLS = 8          # cap on total skills fed into the grounding prompt
MAX_BACKGROUND = 2      # at most this many ability-less skills kept as background

# Bump whenever the overlay's fixed template (compose order, deterministic
# subsections, or the overlay LLM prompt's required sections) changes, so
# cached overlay LLM sections from an older template are never reused for a
# newer one — see _overlay_grounding_hash.
_OVERLAY_TEMPLATE_VERSION = 4

NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
_HASH_LINE_RE = re.compile(r"^<!--\s*grounding-hash:\s*([0-9a-f]+)\s*-->\s*$", re.M)
_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?\n?)---\s*\n?(.*)\Z", re.S)


def _base_grounding(role: str) -> dict:
    """Assemble the K&A-grounded context used to write one role's base skill.

    Pulls teamspec.skill_rows(role) — the same deduped, K&A-attached rows the
    dashboard/team-spec build already uses — and prioritizes skills that carry
    ability-kind K&A items (via teamspec._split_ka): those are what the role
    actually DOES, and are strong grounding for instructions. Skills with no
    ability items are weak grounding (knowledge only, no verb to act on), so at
    most MAX_BACKGROUND of them are kept, appended after every ability-bearing
    skill, purely as background context. The combined list is capped at
    MAX_SKILLS to keep the generation prompt focused.

    Returns {role, skills:[{name, level, exec, proficiency,
    abilities:[...], knowledge:[...]}]}. Deliberately excludes the internal
    skill `code` — this payload is the grounding fed into an LLM prompt whose
    output is user-facing, and the code must never surface there (presentation
    scrub only; `code` remains available internally via teamspec.skill_rows for
    anything keyed by it, e.g. skill_level_overrides).
    """
    rows = teamspec.skill_rows(role)
    with_ability, without_ability = [], []
    for r in rows:
        know, able, _other = teamspec._split_ka(r["ka"])
        entry = {
            "name": r["nm"],
            "level": r["lvl"],
            "exec": bool(r["exec"]),
            "proficiency": r["prof"],
            "abilities": able,
            "knowledge": know,
        }
        (with_ability if able else without_ability).append(entry)
    ordered = with_ability + without_ability[:MAX_BACKGROUND]
    return {"role": role, "skills": ordered[:MAX_SKILLS]}


def _slug(role: str) -> str:
    """kebab-case slug for the cache filename, truncated to a sane length."""
    s = re.sub(r"[^a-z0-9]+", "-", role.lower()).strip("-")
    return (s or "role")[:80]


def _cache_path(role: str) -> pathlib.Path:
    return CACHE_DIR / f"{_slug(role)}.md"


def _grounding_hash(grounding: dict) -> str:
    """Stable short hash of the grounding payload — changes iff the role's live
    framework skills/K&A content changes, which is what invalidates the cache."""
    blob = json.dumps(grounding, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _build_prompt(role: str, grounding: dict) -> list:
    skills_json = json.dumps(grounding["skills"], indent=2, ensure_ascii=False)
    system = (
        "You write Agent Skill scaffolds for AI task agents, in the exact format "
        "Anthropic's Agent Skills spec uses: a SKILL.md file with YAML frontmatter "
        "followed by a markdown body.\n\n"
        "STRICT GROUNDING RULES — follow these exactly:\n"
        "- Ground EVERY instruction in the ability statements given to you below. "
        "Do not invent tools, systems, software, APIs, file formats, metrics, or "
        "numeric thresholds that are not implied by an ability statement.\n"
        "- If the given abilities do not support a specific step-by-step "
        "procedure, describe the capability only at the level of generality the "
        "abilities support, and stop there — do not fill the gap with invented "
        "specifics.\n"
        "- This is a ROLE-LEVEL, REUSABLE skill. Do NOT reference any specific "
        "task, project, deliverable, artifact, or handoff to another agent — "
        "those belong to a later, task-specific layer, not here.\n"
        "- Knowledge-only items (listed separately from abilities) describe "
        "things the role should know, not things it does step-by-step; use them "
        "only as light background, never as the basis for an instruction.\n"
        "- Never mention internal skill codes, SSOC, SkillsFuture, or any "
        "competency-framework name in the output.\n\n"
        "OUTPUT FORMAT — return ONLY the SKILL.md file content, nothing else "
        "(no commentary, no code fence wrapping the whole file):\n\n"
        "---\n"
        "name: <lowercase-hyphenated skill name, <=64 chars, derived from the "
        "role's core capability, not a copy of the literal job title>\n"
        "description: <third person, in the form \"<what it does>. Use when "
        "<concrete triggers drawn from the abilities below>.\", <=1024 chars>\n"
        "---\n\n"
        "<markdown body: grounded capability instructions, organized under a few "
        "headings, one per cluster of related abilities>\n"
    )
    user = (
        f"Role: {role}\n\n"
        "Official framework skills for this role, with Knowledge & Ability (K&A) "
        "statements already split into abilities (things the role DOES) and "
        "knowledge (things the role KNOWS). Ability-bearing skills are listed "
        "first — lead the skill body with those; treat any knowledge-only "
        "entries as background at most:\n\n"
        f"{skills_json}\n\n"
        "Write the SKILL.md now."
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _strip_fence(text: str) -> str:
    """Strip a stray whole-file ``` code fence some models wrap output in."""
    t = (text or "").strip()
    if t.startswith("```"):
        lines = t.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        t = "\n".join(lines).strip()
    return t


def generate_base_skill(role: str, *, force: bool = False) -> str:
    """Generate (or return cached) role-level SKILL.md: canonical Anthropic Agent
    Skills format — YAML frontmatter (name, description) + a markdown body of
    grounded capability instructions. Reusable / role-level ONLY: no task,
    artifacts, or handoffs (a later iteration layers those on top).

    Grounded via exactly one LLM call over _base_grounding(role) (config.llm_chat,
    purpose="skill_base") — nothing else touches the network.

    Cached to data/skill_cache/base/<role-slug>.md, keyed by a hash of the live
    grounding. If the role's official skills/K&A haven't changed since the
    cached file was written, the cache is reused unless force=True.
    """
    grounding = _base_grounding(role)
    ghash = _grounding_hash(grounding)
    cache_fp = _cache_path(role)

    if not force and cache_fp.exists():
        cached = cache_fp.read_text()
        m = _HASH_LINE_RE.search(cached)
        if m and m.group(1) == ghash:
            return cached

    raw = llm_chat(_build_prompt(role, grounding), temperature=0.2, max_tokens=2048,
                    purpose="skill_base")
    md = _strip_fence(raw)
    if not md.endswith("\n"):
        md += "\n"
    md += f"<!-- grounding-hash: {ghash} -->\n"

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_fp.write_text(md)
    return md


def validate_skill_md(md: str) -> tuple[bool, list[str]]:
    """Deterministic well-formedness check for a SKILL.md string — no LLM.

    Checks: frontmatter delimited by '---' parses as YAML; 'name' and
    'description' are present and non-empty; 'name' matches
    ^[a-z0-9]+(-[a-z0-9]+)*$ and is <=64 chars; 'description' is <=1024 chars;
    there is non-empty markdown body after the frontmatter.

    Returns (ok, [error strings]) — the error list is empty iff ok is True.
    """
    errs: list[str] = []
    if md is None or not md.strip():
        return False, ["empty document"]

    m = _FRONTMATTER_RE.match(md)
    if not m:
        return False, ["no '---' delimited YAML frontmatter found at start of document"]

    fm_text, body = m.group(1), m.group(2)
    try:
        fm = yaml.safe_load(fm_text)
    except yaml.YAMLError as e:
        return False, [f"frontmatter is not valid YAML: {e}"]

    if not isinstance(fm, dict):
        return False, ["frontmatter did not parse to a YAML mapping"]

    name = fm.get("name")
    if not name or not isinstance(name, str) or not name.strip():
        errs.append("frontmatter 'name' is missing or empty")
    else:
        name = name.strip()
        if len(name) > 64:
            errs.append(f"frontmatter 'name' exceeds 64 chars ({len(name)})")
        if not NAME_RE.match(name):
            errs.append("frontmatter 'name' must match ^[a-z0-9]+(-[a-z0-9]+)*$ "
                        f"(got {name!r})")

    desc = fm.get("description")
    if not desc or not isinstance(desc, str) or not desc.strip():
        errs.append("frontmatter 'description' is missing or empty")
    elif len(desc) > 1024:
        errs.append(f"frontmatter 'description' exceeds 1024 chars ({len(desc)})")

    if not body or not body.strip():
        errs.append("no body content after frontmatter")

    return (len(errs) == 0), errs


# ─────────────────────────────────────────────────────────────────────────────
# Iteration 2 — task overlay
# ─────────────────────────────────────────────────────────────────────────────
def build_agent_context(team_id, agent_id, artifacts_needed=None) -> dict:
    """Deterministic per-task context for one team agent — no LLM, DB-only.

    role/stage/squad come from team_agents joined to roles (same shape
    team_service.get_team already queries). consumes/produces come from the
    team's wired handoffs (handoff_service.get_handoffs): an artifact this
    agent CONSUMES is one where it is `to_agent` on a handoff naming an
    artifact; an artifact it PRODUCES is one where it is `from_agent`. Each is
    an order-preserving, deduped (by artifact string) list of
    {artifact, description} — the description is the raw handoff description
    when the wiring supplied one, else None.

    artifacts_needed is taken as-is from the caller (e.g. the
    `artifacts_needed` team_service.recommend already computed via
    llm_contracts.identify_artifacts against the use case). This function
    never calls an LLM and never guesses: pass None/omit if the caller has
    none, and it stays [].

    Returns {role, stage, squad, consumes, produces, artifacts_needed}.
    Raises ValueError if the agent isn't on the team.
    """
    row = db.query(
        "SELECT r.role, ta.stage, ta.squad FROM team_agents ta "
        "JOIN roles r ON r.role_id = ta.role_id "
        "WHERE ta.team_id = ? AND ta.agent_id = ?",
        (team_id, agent_id), one=True,
    )
    if row is None:
        raise ValueError(f"agent {agent_id!r} not found on team {team_id!r}")

    handoffs = handoff_service.get_handoffs(team_id)

    def _collect(match_field):
        seen, out = set(), []
        for h in handoffs:
            if h.get(match_field) != agent_id or not h.get("artifact"):
                continue
            art = h["artifact"]
            if art in seen:
                continue
            seen.add(art)
            out.append({"artifact": art, "description": h.get("description")})
        return out

    return {
        "role": row["role"],
        "stage": row["stage"],
        "squad": row["squad"],
        "consumes": _collect("to_agent"),
        "produces": _collect("from_agent"),
        "artifacts_needed": list(artifacts_needed) if artifacts_needed else [],
    }


def _task_label(use_case: str) -> str:
    """Short, deterministic task-heading label — no LLM, just whitespace
    normalization and a length cap so a long use-case string stays readable
    as a markdown heading."""
    label = " ".join((use_case or "").split())
    if len(label) > 80:
        label = label[:77].rstrip() + "..."
    return label or "this task"


def _inputs_section(agent_context: dict) -> str:
    """### Inputs — deterministic: upstream handoff artifacts + user-supplied
    artifacts_needed. No LLM."""
    lines = ["### Inputs"]
    items = []
    for c in agent_context.get("consumes", []):
        suffix = f" — {c['description']}" if c.get("description") else ""
        items.append(f"- **{c['artifact']}** (from upstream agent){suffix}")
    for a in agent_context.get("artifacts_needed", []):
        items.append(f"- **{a.get('kind', 'artifact')}**: {a.get('description', '')}")
    if not items:
        items = ["- No upstream artifacts or user-provided inputs were identified "
                  "for this task."]
    lines.extend(items)
    return "\n".join(lines)


def _deliverable_section(agent_context: dict) -> str:
    """### Deliverable — deterministic: this agent's produced handoff
    artifact(s). No LLM."""
    lines = ["### Deliverable"]
    produces = agent_context.get("produces", [])
    if not produces:
        lines.append("- No downstream handoff artifact is wired for this agent yet.")
    else:
        for p in produces:
            suffix = f" — {p['description']}" if p.get("description") else ""
            lines.append(f"- **{p['artifact']}**{suffix} (handed off to the next agent)")
    return "\n".join(lines)


def _operating_mode_section() -> str:
    """### Operating mode — deterministic, fixed directive (no LLM, no
    agent_context — it doesn't depend on role/use case/contract). Eval
    finding: agents running this bundle standalone (not as part of the wired
    multi-agent team) were producing internal handoff/stage-gate artifacts
    instead of the user-facing deliverable, because nothing in the bundle
    said the Inputs/Deliverable contract above is team context rather than
    the standalone output format."""
    return (
        "### Operating mode\n"
        "If you are running as a STANDALONE agent for a user — not wired "
        "into the multi-agent team described above — produce the complete "
        "end deliverable for the user directly, in one response. Do not "
        "emit internal handoff records, stage-gate artifacts, or split the "
        "output into team-member handoffs: the Inputs/Deliverable contract "
        "above describes this agent's place in the team's handoff pipeline, "
        "not the standalone output format. The user needs the finished "
        "deliverable, not an artifact addressed to another agent."
    )


def _required_inputs_section(agent_context: dict) -> str:
    """### Required real inputs (not included in this scaffold) — the
    honest-gaps manifest. Deterministic/templated: restates artifacts_needed
    and flags that org-specific policy/templates/thresholds/tools are not
    grounded here and must come from the user. No LLM."""
    lines = [
        "### Required real inputs (not included in this scaffold)",
        "This scaffold is grounded only in the framework K&A checklist and the "
        "wired team handoffs. It does NOT include your organization's actual "
        "policies, templates, thresholds, tools, or systems of record — those "
        "must be supplied before this skill is used on a real task:",
    ]
    artifacts_needed = agent_context.get("artifacts_needed", [])
    if artifacts_needed:
        for a in artifacts_needed:
            lines.append(f"- **{a.get('kind', 'artifact')}**: {a.get('description', '')} "
                         "(must be supplied by the user)")
    else:
        lines.append("- No specific input artifacts were identified for this use case; "
                     "confirm with the user whether any are actually needed.")
    lines.append(
        "- Org-specific policy, document/message templates, numeric thresholds, "
        "and the specific tools/systems this agent should use are not grounded "
        "in the base skill and must be supplied by the user."
    )
    return "\n".join(lines)


def _overlay_cache_path(role: str, task_hash: str) -> pathlib.Path:
    return OVERLAY_CACHE_DIR / f"{_slug(role)}-{task_hash}.md"


def _overlay_grounding_hash(role, use_case, consumes, produces, artifacts_needed,
                             base_md) -> str:
    """Stable short hash of everything the LLM overlay subsection is grounded
    in (role, use case, I/O contract, base skill text, overlay template
    version) — changes iff any of those change, which is what should
    invalidate the cache. _OVERLAY_TEMPLATE_VERSION is mixed in so a fixed-
    template change (e.g. a new required overlay section) busts every cached
    overlay even when role/use_case/contract/base_md are unchanged."""
    blob = json.dumps(
        {"role": role, "use_case": use_case, "consumes": consumes,
         "produces": produces, "artifacts_needed": artifacts_needed,
         "base_md": base_md, "template_version": _OVERLAY_TEMPLATE_VERSION},
        sort_keys=True, ensure_ascii=False,
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _build_overlay_prompt(use_case: str, agent_context: dict, base_md: str) -> list:
    contract = json.dumps(
        {
            "role": agent_context.get("role"),
            "consumes": agent_context.get("consumes", []),
            "produces": agent_context.get("produces", []),
            "artifacts_needed": agent_context.get("artifacts_needed", []),
        },
        indent=2, ensure_ascii=False,
    )
    # Eval finding: when the wired contract has MULTIPLE `produces` artifacts,
    # a per-artifact Deliverable format template anchors a STANDALONE agent
    # into emitting separate pipeline artifacts — directly fighting the
    # ### Operating mode directive (one user-facing deliverable, no internal
    # handoff records). With >1 produces artifact, require ONE INTEGRATED
    # deliverable outline instead: a single document structure whose sections
    # incorporate/cover the wired artifacts, explicit that per-artifact
    # outputs are for team mode only. Exactly 1 produces artifact (or none)
    # keeps the original per-artifact template — there's nothing to integrate.
    multi_produces = len(agent_context.get("produces", []) or []) > 1
    if multi_produces:
        deliverable_format_block = (
            "### Deliverable format\n"
            "<a compact structural template for ONE INTEGRATED deliverable — "
            "the single document a STANDALONE run of this agent (see "
            "### Operating mode above) hands to the user. Give ONE section "
            "structure (section headings / table columns / bullet structure) "
            "whose sections incorporate/cover ALL of the 'produces' artifacts "
            "listed in the contract below — do NOT template each produces "
            "artifact as its own separate deliverable. Explicitly state, as "
            "part of this section, that separate per-artifact outputs "
            "(one per 'produces' entry) are for TEAM mode only, when this "
            "agent is wired into the multi-agent handoff pipeline — a "
            "standalone run instead produces this one integrated deliverable. "
            "Ground the structure ONLY in the produces artifacts' names, the "
            "task, and the base skill's capabilities above — structure only, "
            "no invented org specifics, thresholds, tool names, or example "
            "values presented as real data. If no produces artifact is in "
            "the contract, say so briefly instead of inventing one.>\n\n"
        )
    else:
        deliverable_format_block = (
            "### Deliverable format\n"
            "<a compact structural template for the 'produces' artifact named in "
            "the contract below — the section headings, table columns, or "
            "bullet structure this agent should emit. Ground it ONLY in the "
            "artifact's name, the task, and the base skill's capabilities above "
            "— structure only, no invented org specifics, thresholds, tool "
            "names, or example values presented as real data. If no produces "
            "artifact is in the contract, say so briefly instead of inventing "
            "one.>\n\n"
        )
    system = (
        "You write a short, grounded addendum to an existing Agent Skill "
        "document, adapting it to one specific task within a specific I/O "
        "contract. You are given the base skill's already-grounded "
        "capabilities and the deterministic inputs -> deliverable contract "
        "for this agent on this task.\n\n"
        "STRICT GROUNDING RULES — follow these exactly:\n"
        "- Do NOT invent procedures, tools, software, systems, APIs, file "
        "formats, metrics, or numeric thresholds that are not implied by the "
        "base skill's capabilities below or by the inputs/deliverable "
        "contract given to you.\n"
        "- Where completing this task for real would need a specific "
        "procedure, tool, or system that is NOT grounded in what you were "
        "given, say so briefly (e.g. 'the specific outreach channel and "
        "cadence are not specified here') rather than fabricate one.\n"
        "- This also applies to the Deliverable format section below: give "
        "STRUCTURE only (section headings / table columns / bullet "
        "structure). Do NOT invent org-specific field names, numeric "
        "thresholds, tool/system names, or example values presented as if "
        "they were real data.\n"
        "- Never mention internal skill codes, SSOC, SkillsFuture, or any "
        "competency-framework name in the output.\n"
        "- Write ONLY the following three sections, in this exact order, "
        "nothing else (no preamble, no extra sections):\n\n"
        "### Applying this capability to the task\n"
        "<3-6 sentences: how the base skill's grounded capabilities apply to "
        "turning the given inputs into the given deliverable for this task>\n\n"
        + deliverable_format_block +
        "### Success criteria\n"
        "<3-6 bullet points: observable, checkable criteria for the "
        "deliverable, grounded in the contract above — not invented metrics>\n"
    )
    user = (
        f"Task (use case): {use_case}\n\n"
        f"Agent role: {agent_context.get('role')}\n\n"
        f"Base skill this agent already has (its grounded capabilities):\n\n"
        f"{base_md}\n\n"
        f"Inputs -> Deliverable contract for THIS agent on THIS task:\n\n"
        f"{contract}\n\n"
        "Write the two sections now."
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def generate_task_overlay(use_case: str, agent_context: dict, base_md: str,
                           *, force: bool = False) -> str:
    """Build the per-task overlay SECTION for one agent (not a whole SKILL.md).

    Deterministic (no LLM) subsections built straight from agent_context, in
    order: Inputs, Deliverable, Operating mode (fixed standalone-vs-team
    directive), and the "Required real inputs" honest-gaps manifest. Exactly
    one LLM subsection (config.llm_chat, purpose="skill_overlay"), lands
    after all of those: a short narrative grounded in base_md + the I/O
    contract, a structural Deliverable format template, and success criteria.
    The Deliverable format template is per-artifact when the contract has 0-1
    `produces` artifacts, and ONE INTEGRATED deliverable outline (covering all
    wired artifacts, with per-artifact outputs called out as team-mode-only)
    when it has more than 1 — see _build_overlay_prompt's `multi_produces`
    branch; this stops a standalone run from being anchored into emitting
    separate pipeline artifacts, which fights ### Operating mode's directive
    of one user-facing deliverable. The prompt forbids inventing procedures/
    tools/systems/thresholds/metrics (structure only for Deliverable format)
    beyond what base_md and the I/O contract already ground, and asks it to
    say so briefly rather than fabricate when a real procedure would be
    needed but isn't grounded.

    The LLM subsection is cached per (role, task-context hash) under
    data/skill_cache/overlay/<role-slug>-<hash>.md, mirroring the iteration-1
    base-skill cache; the hash mixes in _OVERLAY_TEMPLATE_VERSION so template
    changes bust the cache even when role/use_case/contract/base_md haven't
    changed. The deterministic subsections are cheap to rebuild and are not
    cached.
    """
    role = agent_context.get("role", "")
    thash = _overlay_grounding_hash(
        role, use_case, agent_context.get("consumes", []),
        agent_context.get("produces", []), agent_context.get("artifacts_needed", []),
        base_md,
    )
    cache_fp = _overlay_cache_path(role, thash)

    if not force and cache_fp.exists():
        llm_section = cache_fp.read_text()
    else:
        raw = llm_chat(_build_overlay_prompt(use_case, agent_context, base_md),
                        temperature=0.2, max_tokens=1536, purpose="skill_overlay")
        llm_section = _strip_fence(raw)
        if not llm_section.endswith("\n"):
            llm_section += "\n"
        OVERLAY_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_fp.write_text(llm_section)

    parts = [
        f"## For This Task: {_task_label(use_case)}",
        _inputs_section(agent_context),
        _deliverable_section(agent_context),
        _operating_mode_section(),
        _required_inputs_section(agent_context),
        llm_section.strip(),
    ]
    return "\n\n".join(parts) + "\n"


def compose_bundle(team_id, agent_id, use_case, artifacts_needed=None) -> str:
    """Compose the full per-team-agent skill bundle: task-first, then the
    reusable base (role-level) skill, as one markdown document.

    Eval finding: appending the overlay after the full base skill buried the
    task-specific section under the (often much longer) role-capability
    listing, so agents leaned on the wrong section. Composition order is now:

      1. the base skill's YAML frontmatter (unchanged, stays at the very top
         so validate_skill_md(result) still passes)
      2. the task overlay ("## For This Task: ..." and its subsections) —
         FIRST, so it's what an agent reads before anything else
      3. a demotion header ("## Role capability reference") marking the base
         skill as background context, not the primary instructions
      4. the base skill's BODY, unchanged (including its own trailing
         grounding-hash comment)

    This reorder happens only here, at compose time — generate_base_skill and
    its cache are untouched, so the base-skill cache is unaffected.
    """
    ctx = build_agent_context(team_id, agent_id, artifacts_needed)
    base_md = generate_base_skill(ctx["role"])
    overlay_md = generate_task_overlay(use_case, ctx, base_md)

    m = _FRONTMATTER_RE.match(base_md)
    if m:
        frontmatter_block, base_body = base_md[:m.start(2)], m.group(2)
    else:
        # Defensive fallback — generate_base_skill always emits frontmatter,
        # but don't silently drop content if that ever changes.
        frontmatter_block, base_body = "", base_md

    demotion = (
        "## Role capability reference\n\n"
        "*Full role scope for context — draw on whichever capabilities this "
        "task needs; the task section above is primary.*"
    )

    parts = [frontmatter_block.rstrip("\n"), overlay_md.strip("\n"),
             demotion, base_body.strip("\n")]
    return "\n\n".join(parts) + "\n"


# ─────────────────────────────────────────────────────────────────────────────
# Iteration 3 — whole-team composition + deterministic team-coherence check
# ─────────────────────────────────────────────────────────────────────────────
def compose_team_bundles(team_id, use_case, artifacts_needed=None) -> dict:
    """compose_bundle() for every agent on the team, in one call.

    Agent order comes straight from team_agents (sort_order, then agent_id —
    same tiebreak team_service.get_team uses), so the bundle list matches the
    team's staffed order. Each agent gets the SAME use_case/artifacts_needed;
    compose_bundle already handles the per-agent I/O contract via
    build_agent_context. Not on the /recommend hot path — this is a report/
    export-time call, one LLM round trip (cache permitting) per agent.

    Returns {team_id, use_case, bundles: [{agent_id, role, skill_md}, ...]}.
    Raises ValueError if the team has no agents (mirrors build_agent_context's
    per-agent not-found error rather than silently returning an empty list).
    """
    rows = db.query(
        "SELECT ta.agent_id, r.role FROM team_agents ta "
        "JOIN roles r ON r.role_id = ta.role_id "
        "WHERE ta.team_id = ? ORDER BY ta.sort_order, ta.agent_id",
        (team_id,),
    )
    if not rows:
        raise ValueError(f"team {team_id!r} has no agents")

    bundles = []
    for row in rows:
        skill_md = compose_bundle(team_id, row["agent_id"], use_case, artifacts_needed)
        bundles.append({
            "agent_id": row["agent_id"],
            "role": row["role"],
            "skill_md": skill_md,
        })
    return {"team_id": team_id, "use_case": use_case, "bundles": bundles}


_EXTERNAL = "external"


def check_team_coherence(team_id) -> dict:
    """Deterministic (no LLM) team-coherence check over the team's wired
    handoffs (handoff_service.get_handoffs) and staffed agents (team_agents).

    Two different things are reported, and they are NOT the same signal:

    1. dangling_consumes / orphan_produces / coherent_pct — a STRUCTURAL,
       edge-paired check. Every wired handoff simultaneously names a producer
       and a consumer for its artifact, so in practice almost every consumed
       artifact already has a matching producer edge somewhere in the graph
       (and vice versa) by construction — these numbers trend near-perfect
       even when the actual wiring is poor. They exist as a sanity floor, not
       as the real quality signal.
    2. isolated_agents / self_loop_only_agents / no_inbound / no_outbound —
       the real handoff-WIRING-quality signals. wire_handoffs is LLM-
       generated per team, so an agent left with no edges at all, or wired
       only to itself, or missing genuine inbound/outbound flow, is a
       concrete, likely wiring gap that the edge-pairing check above cannot
       see (it only ever looks at whether an artifact string appears twice,
       never at whether every agent actually participates).

    'external' is a valid boundary producer/consumer (an edge external->A
    means A's consume is satisfied from outside the team; A->external means
    A's produce has a valid external sink) but is never counted as an agent.

    Returns {n_agents, n_handoffs, total_consumes, total_produces,
    dangling_consumes, orphan_produces, coherent_pct, isolated_agents,
    self_loop_only_agents, no_inbound, no_outbound, note}.
    """
    agent_rows = db.query(
        "SELECT agent_id FROM team_agents WHERE team_id = ? "
        "ORDER BY sort_order, agent_id",
        (team_id,),
    )
    agent_ids = [r["agent_id"] for r in agent_rows]
    agent_set = set(agent_ids)

    handoffs = handoff_service.get_handoffs(team_id)

    # Every artifact string that appears on a from_agent side ("producer
    # side") and every one that appears on a to_agent side ("consumer side"),
    # anywhere in the graph — including 'external' edges. These are the sets
    # the edge-pairing check below tests membership against. NOTE: because
    # every handoff row supplies both a from_agent and a to_agent for its
    # artifact, these two sets are identical by construction whenever an
    # artifact is only ever handed off once — which is exactly why the
    # structural check below trends trivially high; see the `note` field.
    from_side_artifacts = {h["artifact"] for h in handoffs if h.get("artifact")}
    to_side_artifacts = {h["artifact"] for h in handoffs if h.get("artifact")}

    # Per-agent consumes/produces, restricted to INTERNAL agents (to_agent /
    # from_agent is a real team agent, not 'external'), deduped by artifact
    # per agent — same shape as build_agent_context's _collect().
    consumes_by_agent = {a: [] for a in agent_ids}
    produces_by_agent = {a: [] for a in agent_ids}
    for h in handoffs:
        art = h.get("artifact")
        if not art:
            continue
        to_a, from_a = h.get("to_agent"), h.get("from_agent")
        if to_a in agent_set and art not in consumes_by_agent[to_a]:
            consumes_by_agent[to_a].append(art)
        if from_a in agent_set and art not in produces_by_agent[from_a]:
            produces_by_agent[from_a].append(art)

    total_consumes = sum(len(v) for v in consumes_by_agent.values())
    total_produces = sum(len(v) for v in produces_by_agent.values())

    dangling_consumes = []
    for agent_id, arts in consumes_by_agent.items():
        for art in arts:
            if art not in from_side_artifacts:
                dangling_consumes.append({"agent_id": agent_id, "artifact": art})

    orphan_produces = []
    for agent_id, arts in produces_by_agent.items():
        for art in arts:
            if art not in to_side_artifacts:
                orphan_produces.append({"agent_id": agent_id, "artifact": art})

    coherent_pct = (
        100.0 * (1 - len(dangling_consumes) / total_consumes)
        if total_consumes else None
    )

    # Wiring-quality signals: per-agent edge participation, external counting
    # as a valid endpoint but never as an agent.
    has_inbound, has_outbound, has_any_edge, has_nonself_edge = (
        set() for _ in range(4)
    )
    for h in handoffs:
        to_a, from_a = h.get("to_agent"), h.get("from_agent")
        if to_a in agent_set:
            has_inbound.add(to_a)
            has_any_edge.add(to_a)
        if from_a in agent_set:
            has_outbound.add(from_a)
            has_any_edge.add(from_a)
        if to_a in agent_set and from_a in agent_set and to_a != from_a:
            has_nonself_edge.add(to_a)
            has_nonself_edge.add(from_a)
        elif to_a in agent_set and from_a == _EXTERNAL:
            has_nonself_edge.add(to_a)
        elif from_a in agent_set and to_a == _EXTERNAL:
            has_nonself_edge.add(from_a)

    isolated_agents = [a for a in agent_ids if a not in has_any_edge]
    self_loop_only_agents = [
        a for a in agent_ids if a in has_any_edge and a not in has_nonself_edge
    ]
    no_inbound = [a for a in agent_ids if a not in has_inbound]
    no_outbound = [a for a in agent_ids if a not in has_outbound]

    note = (
        "coherent_pct/dangling_consumes/orphan_produces are a STRUCTURAL, "
        "edge-paired check: each wired handoff supplies both a producer and "
        "a consumer for its artifact by construction, so these numbers trend "
        "near-100% even for poorly-wired teams and are a sanity floor, not a "
        "semantic quality signal. isolated_agents/self_loop_only_agents/"
        "no_inbound/no_outbound are the meaningful handoff-WIRING-quality "
        "signals — they surface agents wire_handoffs (LLM-generated) left "
        "disconnected or self-looped despite the edge-pairing check reading "
        "clean."
    )

    return {
        "n_agents": len(agent_ids),
        "n_handoffs": len(handoffs),
        "total_consumes": total_consumes,
        "total_produces": total_produces,
        "dangling_consumes": dangling_consumes,
        "orphan_produces": orphan_produces,
        "coherent_pct": coherent_pct,
        "isolated_agents": isolated_agents,
        "self_loop_only_agents": self_loop_only_agents,
        "no_inbound": no_inbound,
        "no_outbound": no_outbound,
        "note": note,
    }
