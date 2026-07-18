from config import llm_chat

ANCHOR = "## Learned skill guidance"

def generate_agent(role, skills, context, custom_instructions="", sector=""):
    skills_tbl = "\n".join(f"| {s['skill']} | {s['code']} | L{s['required_level']} |"
                           for s in skills)
    cwf = "\n".join(f"- {c['cwf']}: " + "; ".join(c["key_tasks"][:3])
                    for c in context["critical_work_functions"][:6])
    prompt = f"""Write an agent specification in markdown for an AI agent performing this role.

Role: {role} (source: SkillsFuture Skills Framework, sector: {sector})
Role description: {context['description']}

Official required skills — reproduce EXACTLY this table; never add, drop, or rename a skill:
| Skill | Code | Required level |
|---|---|---|
{skills_tbl}

Critical work functions (distill a working method from these):
{cwf}

Custom instructions from the requester (include verbatim in their own section):
{custom_instructions or '(none)'}

Structure — exactly these sections, in order:
# Agent Specification — {role}
## Role
## Official required skills
## Operating instructions
## Custom instructions
{ANCHOR}

Rules:
- "Operating instructions": careful, methodical; when a task gives an exact function signature,
  output format, or rule set, follow it LITERALLY; clean dependency-free Python (stdlib only);
  read the entire task before coding; no explanations unless asked.
- Keep competency guidance TERSE — role + skills, minimal tactics. (A refinement pass adds
  tactical guidance later; a terse v0 is intentional.)
- The final section "{ANCHOR}" must contain only the line: _None yet._
Return ONLY the markdown."""
    spec = llm_chat([{"role": "user", "content": prompt}], temperature=0.3, purpose="agent") or ""
    # reasoning models can return None/empty when tokens run out — empty spec falls
    # through to run.py's agent_seed.md fallback (len check) instead of crashing here
    if ANCHOR not in spec:                       # the anchor is load-bearing for refine
        spec = spec.rstrip() + f"\n\n{ANCHOR}\n\n_None yet._\n"
    return spec
