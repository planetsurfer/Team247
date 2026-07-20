"""Iteration 1 of the skill-bundle enhancement — role-level Agent Skill scaffolds.

Generates REUSABLE, role-level SKILL.md documents in the canonical Anthropic
Agent Skills format (YAML frontmatter + markdown body), grounded ONLY in the
framework's official Knowledge & Ability (K&A) checklist for that role via
teamspec.skill_rows / teamspec._split_ka (same source teamspec.py already
uses — nothing invented here either). This is a REFRAMED, scaffold-only scope:
no sandbox-proof pillar, no task specifics, no artifacts, no handoffs — those
are a later iteration's job. This module builds only the base (role-level)
skill generator.

One LLM call per (role, grounding) via config.llm_chat, cached to disk under
data/skill_cache/base/<role-slug>.md so repeat calls for an unchanged role are
free and offline. Deterministic well-formedness of the output is checked
separately (validate_skill_md) without any LLM involvement.
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import re

import yaml

import teamspec
from config import llm_chat

CACHE_DIR = pathlib.Path("data/skill_cache/base")
MAX_SKILLS = 8          # cap on total skills fed into the grounding prompt
MAX_BACKGROUND = 2      # at most this many ability-less skills kept as background

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

    Returns {role, skills:[{name, code, level, exec, proficiency,
    abilities:[...], knowledge:[...]}]}.
    """
    rows = teamspec.skill_rows(role)
    with_ability, without_ability = [], []
    for r in rows:
        know, able, _other = teamspec._split_ka(r["ka"])
        entry = {
            "name": r["nm"],
            "code": r["code"],
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
        "only as light background, never as the basis for an instruction.\n\n"
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
