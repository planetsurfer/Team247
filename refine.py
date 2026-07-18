from config import llm_chat
from agent import ANCHOR

def make_injection(gap_item):
    """One concrete, reusable instruction to pass this CLASS of task (the v1 coach prompt)."""
    prompt = f"""An AI agent scored {gap_item['score']:.2f} (needs >= 0.7) on this graded task:

---
{gap_item['task_prompt'][:3000]}
---

Write ONE concrete, reusable instruction (2-4 sentences, imperative voice) that would help the
agent pass tasks OF THIS KIND. Focus on likely failure modes: exact output formats, applying
rules in the stated order, edge cases (missing values, duplicates, case sensitivity, rounding,
tie-breaks, sort stability). Do not mention this specific task's data or test values.
Return only the instruction text."""
    return llm_chat([{"role": "user", "content": prompt}], temperature=0.3, purpose="refine").strip()

def patch_agent(agent_spec, gaps):
    """Append guidance under the anchor. Everything after the anchor is refine's territory
    (§0.5 bug 4) — other sections must never be inserted below it."""
    if ANCHOR not in agent_spec:
        agent_spec = agent_spec.rstrip() + f"\n\n{ANCHOR}\n\n_None yet._\n"
    head, _, tail = agent_spec.partition(ANCHOR)
    tail = tail.replace("_None yet._", "").rstrip()
    for g in gaps:
        tail += (f"\n\n### {g['skill']} (required L{g['required_level']})\n"
                 f"{make_injection(g)}")
    return head + ANCHOR + tail + "\n"
