import json
from config import llm_json, make_runner, SANDBOX_TIMEOUT
import framework
from assess import parse_grade

def rubric_score(skill, level, ka, agent_spec):
    """LLM-as-judge over the skill's OWN K&A checklist. Honest, framework-anchored,
    and NEVER blended with executed bars. Judge reads the FULL spec (§0.5 bug 2);
    K&A items are already atomic + artifact-assessable (§0.5 bug 3)."""
    items = ka["items"]
    if not items:
        return None
    listing = "\n".join(f"{i+1}. [{it['kind']}] {it['item']}"
                        for i, it in enumerate(items))
    prompt = f"""You are grading an AI agent SPECIFICATION document against Singapore's
SkillsFuture Knowledge & Ability checklist for the skill "{skill}" at level L{level}
("{ka['proficiency_description']}").

Checklist — {len(items)} atomic items, judge each independently:
{listing}

Agent specification (FULL text — judge only what this document contains, never
operational process such as uptime, sign-offs, or audit trails):
---
{agent_spec}
---

For each item, covered=true only if the specification's content would plausibly direct the
agent to demonstrate that item.
Return STRICT JSON: {{"covered": [true or false, exactly {len(items)} entries, in order]}}"""
    obj = llm_json([{"role": "user", "content": prompt}], temperature=0.0,
                    purpose="battery_rubric",
                    validate=lambda o: (isinstance(o.get("covered"), list)
                                        and len(o["covered"]) == len(items)
                                        and all(isinstance(c, bool) for c in o["covered"])),
                    max_tokens=8192)   # reasoning tokens + long checklists blow a 2048 cap
    n = sum(1 for c in obj["covered"] if c)
    return {"skill": skill, "level": level, "covered": n, "total": len(items),
            "pct": round(100 * n / len(items), 1), "execution_verified": False}

_REQ_KEYS = {"task_prompt", "grader_code", "reference_code"}

def generate_skill(role, skill, required_level, context, ka, task_material):
    ka_lines = "\n".join(f"- [{it['kind']}] {it['item']}" for it in ka["items"][:10])
    key_tasks = "; ".join(kt for c in context["critical_work_functions"][:3]
                          for kt in c["key_tasks"][:2])
    prompt = f"""Generate ONE graded coding assessment for an AI agent.

Role: {role}
Skill: {skill} — required proficiency level L{required_level} (SkillsFuture framework)
Official proficiency description: {ka['proficiency_description']}
Official Knowledge & Ability items — the task MUST exercise 2-4 of these, faithfully:
{ka_lines}
Role key tasks (flavour): {key_tasks}
Current market demand from live postings (optional flavour): {task_material or '(none)'}

Return STRICT JSON with exactly these keys:
- "task_prompt": self-contained task asking for a Python function `def solve(...)`.
  Pure stdlib, deterministic, no I/O or network. Spell out exact rules, rule ORDER,
  edge cases, and output format precisely.
- "grader_code": self-contained Python defining test cases; it calls solve(...) which is
  already defined in the same namespace (do NOT import or redefine solve); wraps every call
  in try/except; and its LAST printed line is exactly:
  GRADE:{{"score": <fraction of cases passed, 0..1>}}
- "reference_code": a correct reference implementation of solve(...).
Task must be solvable in <=60 lines and graded purely by running the code."""
    return llm_json([{"role": "user", "content": prompt}], temperature=0.2,
                     purpose="battery_grader",
                     validate=lambda o: _REQ_KEYS <= set(o), max_tokens=4096)

def _validate_item(runner, item):
    """§0.5 bug 5: static check, then run the item's own reference against its grader in a
    sandbox — an item its own reference can't solve is a bad item; drop it."""
    try:
        compile(item["grader_code"], "<grader>", "exec")
        compile(item["reference_code"], "<ref>", "exec")
    except SyntaxError:
        return False
    sb = runner.create()
    try:
        resp = sb.process.code_run(item["reference_code"] + "\n\n" + item["grader_code"],
                                   timeout=SANDBOX_TIMEOUT)
        score, _ = parse_grade(getattr(resp, "result", ""))
        return score >= 0.99
    except Exception:
        return False
    finally:
        try:
            sb.delete()
        except Exception:
            pass

def build_battery(role, executable, context, task_material=None):
    runner = make_runner()
    out = []
    for s in executable:
        ka = framework.get_ka(s["code"], s["required_level"])
        try:
            gen = generate_skill(role, s["skill"], s["required_level"],
                                 context, ka, task_material)
            item = {"skill": s["skill"], "code": s["code"],
                    "required_level": s["required_level"],
                    "task_prompt": gen["task_prompt"],
                    "grader_code": gen["grader_code"]}
            if _validate_item(runner, {**item, "reference_code": gen["reference_code"]}):
                out.append(item)
                print(f"battery: generated + self-validated '{s['skill']}' L{s['required_level']}")
            else:
                print(f"battery: DROPPED invalid item for '{s['skill']}'")
        except Exception as e:
            print(f"battery: generation failed for '{s['skill']}': {e}")
    if not out:                                            # §8: never dead in the water
        print("battery: nothing survived — falling back to data/battery_seed.json")
        seed = json.load(open("data/battery_seed.json"))
        role_codes = {s["code"] for s in framework.get_skills(role)}
        out = [i for i in seed if i["code"] in role_codes]
    return out
