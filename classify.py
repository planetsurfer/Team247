import re
from collections import Counter
import framework
from config import llm_json

_STOP = set(("a an the and or of to for in on with is are be this that it i we my our need "
             "want into from by as at").split())

def _stem(w):
    """Porter-lite suffix strip so 'invoices'/'invoicing'/'invoice' and
    'scheduling'/'schedule' collide. Applied to queries and docs alike, so
    crude stems only need to be consistent, not linguistically correct."""
    for suf in ("ing", "ies", "es", "ed", "s"):
        if w.endswith(suf) and len(w) - len(suf) >= 3:
            w = w[: -len(suf)] + ("y" if suf == "ies" else "")
            break
    if w.endswith("e") and len(w) > 4:
        w = w[:-1]
    return w


def _tokens(text):
    return [_stem(w) for w in re.findall(r"[a-z]+", (text or "").lower())
            if w not in _STOP and len(w) > 2]


_INDEX = None

def _index():
    """Retrieval index, built once per process: (sector, role) -> stem set, plus
    IDF weights over the corpus.

    Each role's doc is its name + description + its K&A skill titles. Skill
    titles are curated FUNCTIONAL labels ('Billing and Settlement
    Administration', 'Transport Route and Schedule Planning') — vocabulary the
    role descriptions often lack, letting a task phrased by function reach the
    right role. IDF keeps that from backfiring: without it, text-heavy roles
    win on generic stems ('monthly', 'client') instead of contentful ones
    ('invoic', 'roster')."""
    global _INDEX
    if _INDEX is None:
        import math
        skills = {}
        for sec, _trk, role, title, *_rest in framework._sheet("Job Role_TSC_CCS"):
            if role and title:
                skills.setdefault((sec, role), []).append(str(title))
        docs = {}
        for sector, _trk, role, desc, _perf in framework._sheet("Job Role_Description"):
            if not role:
                continue
            key = (sector, role)
            text = f"{role} {desc or ''} {' '.join(skills.get(key, ()))}"
            docs.setdefault(key, set()).update(_tokens(text))
        n = len(docs)
        df = Counter(w for toks in docs.values() for w in toks)
        idf = {w: math.log(n / c) for w, c in df.items()}
        _INDEX = (docs, idf)
    return _INDEX

def recommend_roles(task, k=3, preferred_sectors=None):
    """Retrieve-and-rank over REAL roles only (keyword overlap -> LLM choosing among the
    top slate). The LLM never names a role from scratch — the guardrail the proof depends on.

    preferred_sectors: optional list of sector names the user's task most likely
    belongs to. Same-sector roles get a retrieval boost and reserved slots in the
    slate, so the chooser is never starved of in-industry candidates — but the
    slate always keeps cross-sector slots, since tasks like invoicing legitimately
    pull roles from other sectors.
    """
    preferred = set(preferred_sectors or ())
    q = set(_tokens(task))
    docs, idf = _index()
    # IDF-weighted distinct-stem match; docs are already deduped by
    # (sector, role) — the sheet lists some roles under several tracks (up to
    # 11 rows for one role), which used to flood the slate with clones.
    scored = []
    for (sector, role), toks in docs.items():
        score = sum(idf[w] for w in q if w in toks)
        if score > 0:
            scored.append((score, role, sector))
    # Sort by score only — a (score, role, ...) tuple sort would break ties
    # reverse-alphabetically and bias the slate toward 'W...' role names.
    scored.sort(key=lambda s: s[0], reverse=True)
    if not scored:
        raise SystemExit("Task matched no dataset role — rephrase, or use --role (Mode A).")

    slate_size = 15
    if preferred:
        # Reserve up to 8 slots for the user's sector(s), fill the rest globally.
        in_sector = [s for s in scored if s[2] in preferred][:8]
        rest = [s for s in scored if s not in in_sector]
        top = (in_sector + rest)[:slate_size]
        top.sort(key=lambda s: s[0], reverse=True)
    else:
        top = scored[:slate_size]

    sector_hint = (f"\nThe user most likely works in: {', '.join(sorted(preferred))}. "
                   f"Prefer roles from their industry when equally plausible, but a "
                   f"cross-sector role that clearly does the task is fine.\n"
                   if preferred else "")
    listing = "\n".join(f"{i+1}. {r}  ({s})" for i, (_, r, s) in enumerate(top))
    obj = llm_json([{"role": "user", "content":
        f'A user describes this task: "{task}"\n{sector_hint}\n'
        f"Which of these REAL SkillsFuture roles fit best? Choose ONLY from this list:\n"
        f"{listing}\n\n"
        f'Return STRICT JSON: {{"picks": [{{"n": <list number>, "confidence": <0-100>, '
        f'"matched_on": ["duty", ...]}}]}} — best {k} picks, best first, no repeated n.'}],
        temperature=0.2,
        purpose="classify",
        validate=lambda o: (isinstance(o.get("picks"), list) and o["picks"]
                            and all(isinstance(p.get("n"), int)
                                    and 1 <= p["n"] <= len(top) for p in o["picks"])))
    out, seen = [], set()
    for p in obj["picks"]:
        n = int(p["n"])
        if n in seen:  # a repeated pick must not clone a candidate
            continue
        seen.add(n)
        _, role, sector = top[n - 1]
        out.append({"role": role, "sector": sector,
                    "confidence": p.get("confidence", 0),
                    "matched_on": p.get("matched_on", [])})
        if len(out) >= k:
            break
    return out
