import re
from config import llm_chat

_FENCE = re.compile(r"^```[a-zA-Z]*\s*\n|```\s*$", re.MULTILINE)

def candidate_solve(task_prompt, agent_spec):
    """The generated markdown IS the system prompt — no hidden base prompt (BUILD_GUIDE §4)."""
    code = llm_chat(
        [{"role": "system", "content": agent_spec},
         {"role": "user", "content": task_prompt +
          "\n\nReturn ONLY the Python code defining solve(...). "
          "No explanations, no markdown fences."}],
        temperature=0.2, purpose="candidate")
    return _FENCE.sub("", code or "").strip()   # None on token-exhaustion -> empty -> grader scores 0
