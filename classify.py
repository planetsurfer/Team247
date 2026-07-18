import re
from collections import Counter
import framework
from config import llm_json

_STOP = set(("a an the and or of to for in on with is are be this that it i we my our need "
             "want into from by as at").split())

def _tokens(text):
    return [w for w in re.findall(r"[a-z]+", (text or "").lower())
            if w not in _STOP and len(w) > 2]

def recommend_roles(task, k=3):
    """Retrieve-and-rank over REAL roles only (keyword overlap -> LLM choosing among the
    top-10). The LLM never names a role from scratch — the guardrail the proof depends on."""
    q = Counter(_tokens(task))
    scored = []
    for sector, _trk, role, desc, _perf in framework._sheet("Job Role_Description"):
        if not role:
            continue
        doc = Counter(_tokens(f"{role} {desc or ''}"))
        overlap = sum(min(q[w], doc[w]) for w in q)
        if overlap:
            scored.append((overlap, role, sector))
    top = sorted(scored, reverse=True)[:10]
    if not top:
        raise SystemExit("Task matched no dataset role — rephrase, or use --role (Mode A).")
    listing = "\n".join(f"{i+1}. {r}  ({s})" for i, (_, r, s) in enumerate(top))
    obj = llm_json([{"role": "user", "content":
        f'A user describes this task: "{task}"\n\n'
        f"Which of these REAL SkillsFuture roles fit best? Choose ONLY from this list:\n"
        f"{listing}\n\n"
        f'Return STRICT JSON: {{"picks": [{{"n": <list number>, "confidence": <0-100>, '
        f'"matched_on": ["duty", ...]}}]}} — best {k} picks, best first.'}],
        temperature=0.2,
        purpose="classify",
        validate=lambda o: (isinstance(o.get("picks"), list) and o["picks"]
                            and all(isinstance(p.get("n"), int)
                                    and 1 <= p["n"] <= len(top) for p in o["picks"])))
    out = []
    for p in obj["picks"][:k]:
        _, role, sector = top[int(p["n"]) - 1]
        out.append({"role": role, "sector": sector,
                    "confidence": p.get("confidence", 0),
                    "matched_on": p.get("matched_on", [])})
    return out
