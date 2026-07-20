"""Two LLM judges for the drop-in eval (clone of tests/simulation/judge.py's
call_llm_json + validator pattern):

  - judge_comprehensiveness (purpose "dropin_comp_judge"): does the SKILL.md
    itself read as a usable, honest, paste-in agent definition? Input is the
    use case + the bundle text alone — no execution required, so this half
    runs even with --skip-exec.
  - judge_work_product (purpose "dropin_work_judge"): given the deliverable
    the claude-CLI run actually produced, did it do the job? This is
    deliberately CROSS-MODEL — kimi (the project default) judging Claude's
    output — less self-bias than the sim harness's same-model judge.

Both return None (never raise) on LLMError, mirroring sim's judge_conversation
so the runner can degrade a record instead of crashing the whole archetype.
"""
from __future__ import annotations

from app.llm_contracts import LLMError, call_llm_json

COMP_DIMS = (
    "capability_coverage", "io_clarity", "role_boundaries",
    "honest_gaps", "dropin_readiness",
)
WORK_DIMS = ("deliverable_match", "input_use", "boundary_respect", "output_quality")

MAX_STRENGTHS = 3
MAX_WEAKNESSES = 3
MAX_SKILL_GAPS = 5


# ── comprehensiveness judge ─────────────────────────────────────────────────
COMP_SYSTEM = """You are a strict evaluator of a generated SKILL.md agent-definition
bundle — a markdown "job description" meant to be dropped, largely as-is,
into an AI coding/agent harness (e.g. Claude Code) as that agent's system
prompt for a specific business task.

Score each dimension 1-5 (integers):
- capability_coverage: do the described capabilities actually cover what
  this role would need to do for the stated use case?
  Anchors: 5 = covers the core work end to end, nothing obviously missing;
  4 = covers the core work, one minor gap; 3 = covers the basics but skips a
  clearly-needed capability; 2 = mostly generic, misses most of what the
  task needs; 1 = capabilities are unrelated to the use case.
- io_clarity: are the Inputs and Deliverable sections concrete and
  unambiguous about what comes in and what must go out?
  Anchors: 5 = an agent could start work with zero clarifying questions;
  4 = clear with one ambiguous point; 3 = generally clear but a key input or
  the deliverable format is vague; 2 = inputs/deliverable are hand-wavy;
  1 = no usable I/O contract at all.
- role_boundaries: does the document stay inside this one role/agent's
  actual job, without claiming another agent's work or the whole team's job?
  Anchors: 5 = tightly scoped to this role; 4 = scoped, one minor overreach;
  3 = occasionally strays into adjacent-role territory; 2 = frequently reads
  like a generic "do everything" document; 1 = no discernible role boundary.
- honest_gaps: does the document explicitly name what is NOT grounded here
  (missing real inputs, org-specific policy/tools/thresholds) rather than
  inventing specifics to fill the gap?
  Anchors: 5 = every real gap is named plainly, nothing fabricated; 4 = gaps
  mostly named, one invented specific slipped in; 3 = some gaps named, some
  fabricated detail present; 2 = mostly fabricated specifics, gaps glossed
  over; 1 = confidently invents concrete tools/systems/numbers with no
  disclosure.
- dropin_readiness: overall, could you paste this into an agent harness and
  get useful, actionable work out of it on this task with only minor tweaks?
  Anchors: 5 = paste-in actionable as written; 4 = actionable after a small
  tweak; 3 = usable as a starting point but needs real editing; 2 = would
  need substantial rewriting; 1 = not usable as an agent definition.

Return STRICT JSON only:
{"scores": {"capability_coverage": int, "io_clarity": int,
 "role_boundaries": int, "honest_gaps": int, "dropin_readiness": int},
 "overall": int, "rationale": str}
overall is 1-5, weighted toward the worst dimension. rationale is ONE
sentence naming the biggest strength or weakness."""


def _validate_comp_judge(obj):
    scores = obj.get("scores") or {}
    for dim in COMP_DIMS:
        v = scores.get(dim)
        if not isinstance(v, int) or isinstance(v, bool) or not 1 <= v <= 5:
            raise ValueError(f"score {dim}={v!r} not int 1-5")
    overall = obj.get("overall")
    if not isinstance(overall, int) or isinstance(overall, bool) or not 1 <= overall <= 5:
        raise ValueError(f"overall={overall!r} not int 1-5")
    if not obj.get("rationale") or not str(obj["rationale"]).strip():
        raise ValueError("missing rationale")
    return obj


def judge_comprehensiveness(use_case: str, skill_md: str) -> dict | None:
    """One call_llm_json call, purpose="dropin_comp_judge", temperature=0.1.
    Returns the verdict dict or None (LLMError -> degrade, don't raise)."""
    try:
        return call_llm_json(
            "dropin_comp_judge",
            [
                {"role": "system", "content": COMP_SYSTEM},
                {"role": "user", "content": (
                    f"USE CASE: {use_case!r}\n\n"
                    f"SKILL.md BUNDLE:\n\n{skill_md}"
                )},
            ],
            _validate_comp_judge,
            temperature=0.1,
        )
    except LLMError:
        return None


# ── work-product judge ──────────────────────────────────────────────────────
WORK_SYSTEM = """You are a strict evaluator of one work product an AI agent produced
after being run headless with a SKILL.md bundle as its system prompt, given
a realistic task with sample input files.

Score each dimension 1-5 (integers):
- deliverable_match: is the output actually the deliverable artifact the
  agent's skill said it would produce (right kind of document, not a
  different one)?
  Anchors: 5 = exactly the wired deliverable; 4 = right deliverable, minor
  format mismatch; 3 = recognizably the right kind of document but
  incomplete; 2 = only loosely resembles the intended deliverable; 1 = wrong
  deliverable entirely (e.g. a status update instead of the actual document).
- input_use: did the agent correctly use the SPECIFIC facts in the provided
  input files, per the ground-truth notes below (not just gesture at them)?
  Anchors: 5 = every planted specific correctly used; 4 = nearly all used,
  one minor miss; 3 = the gist is right but several planted specifics are
  missing or wrong; 2 = only superficial use of the inputs; 1 = ignores the
  actual input content, generic or fabricated instead.
- boundary_respect: when a REAL input was genuinely missing (not covered by
  the fixtures), did the agent say so explicitly rather than invent it? (If
  nothing was missing, score on whether it avoided inventing unrequested
  specifics.)
  Anchors: 5 = missing info clearly flagged, nothing invented; 4 = mostly
  honest, one small invented detail; 3 = some fabrication mixed with honest
  flags; 2 = mostly invents rather than flags; 1 = confidently fabricates
  throughout with no disclosure.
- output_quality: is the deliverable usable as-is by the business, in tone,
  structure, and completeness?
  Anchors: 5 = ready to send/use as-is; 4 = minor polish needed; 3 = usable
  draft, needs real editing; 2 = rough, missing key parts; 1 = not usable.

Also return:
- strengths: up to 3 short strings, what the deliverable got right.
- weaknesses: up to 3 short strings, what it got wrong or missed.
- skill_gaps: up to 5 short strings, each phrased as "The SKILL.md should
  have included ..." — concrete, actionable feedback for the person who
  writes the SKILL.md generator, naming what was missing FROM THE SKILL
  DEFINITION (not from the deliverable) that would have produced a better
  result.
- overall: 1-5 integer, weighted toward the worst dimension.
- rationale: ONE sentence naming the biggest problem (or strength if clean).

Return STRICT JSON only:
{"scores": {"deliverable_match": int, "input_use": int,
 "boundary_respect": int, "output_quality": int}, "overall": int,
 "strengths": [str, ...], "weaknesses": [str, ...],
 "skill_gaps": [str, ...], "rationale": str}"""


def _clean_str_list(v, cap):
    if not isinstance(v, list):
        return []
    out = [str(x).strip() for x in v if isinstance(x, (str, int, float)) and str(x).strip()]
    return out[:cap]


def _validate_work_judge(obj):
    scores = obj.get("scores") or {}
    for dim in WORK_DIMS:
        v = scores.get(dim)
        if not isinstance(v, int) or isinstance(v, bool) or not 1 <= v <= 5:
            raise ValueError(f"score {dim}={v!r} not int 1-5")
    overall = obj.get("overall")
    if not isinstance(overall, int) or isinstance(overall, bool) or not 1 <= overall <= 5:
        raise ValueError(f"overall={overall!r} not int 1-5")
    if not obj.get("rationale") or not str(obj["rationale"]).strip():
        raise ValueError("missing rationale")

    strengths = _clean_str_list(obj.get("strengths"), MAX_STRENGTHS)
    weaknesses = _clean_str_list(obj.get("weaknesses"), MAX_WEAKNESSES)
    skill_gaps = _clean_str_list(obj.get("skill_gaps"), MAX_SKILL_GAPS)
    if not strengths and not weaknesses and not skill_gaps:
        raise ValueError("strengths/weaknesses/skill_gaps all empty")

    obj["strengths"] = strengths
    obj["weaknesses"] = weaknesses
    obj["skill_gaps"] = skill_gaps
    return obj


def judge_work_product(scenario, skill_md: str, deliverable: str) -> dict | None:
    """One call_llm_json call, purpose="dropin_work_judge", temperature=0.1.
    Cross-model by construction (kimi judging claude's output, per the
    project's LLM_MODEL_DROPIN_WORK_JUDGE / LLM_MODEL routing).

    Returns the verdict dict or None (LLMError -> degrade, don't raise)."""
    fixtures_block = "\n\n".join(
        f"## FILE: {name}\n```\n{content}\n```" for name, content in scenario.fixtures.items()
    )
    try:
        return call_llm_json(
            "dropin_work_judge",
            [
                {"role": "system", "content": WORK_SYSTEM},
                {"role": "user", "content": (
                    f"USE CASE: {scenario.use_case!r}\n\n"
                    f"TASK GIVEN TO THE AGENT: {scenario.task_instruction}\n\n"
                    f"INPUT FILES PROVIDED:\n\n{fixtures_block}\n\n"
                    f"GROUND TRUTH (what a correct deliverable must reflect — "
                    f"NOT shown to the agent, judge against this):\n"
                    f"{scenario.expected_content_notes}\n\n"
                    f"THE AGENT'S SKILL.md (for reference — what it was told "
                    f"its job was):\n\n{skill_md}\n\n"
                    f"THE DELIVERABLE THE AGENT PRODUCED:\n\n{deliverable}"
                )},
            ],
            _validate_work_judge,
            temperature=0.1,
        )
    except LLMError:
        return None
