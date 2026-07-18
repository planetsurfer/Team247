"""One-time LLM distillation of role_enrichment.json.

Turns each role's raw scraped `responsibilities` (verbatim posting sentences like
"We are seeking ...", "JOB SUMMARY: ...") into 3-5 generic, instructional bullet points
stored as `responsibilities_distilled`. build_team renders that field instead of the raw
sentences, so per-agent specs describe the role generically in the imperative voice.

Idempotent: roles already carrying `responsibilities_distilled` are skipped unless --force.
Offline build is preserved — distillation runs here, once; teamspec.build_team stays
deterministic and LLM-free.
"""
import json, os, sys, shutil

ROLE_ENRICHMENT_JSON = os.getenv("ROLE_ENRICHMENT_JSON", "data/role_enrichment.json")

_PROMPT = (
    'You are condensing raw sentences scraped from job postings for the role '
    '"{role}" into a generic, instructional role description.\n\n'
    'Raw posting sentences:\n{list}\n\n'
    'Rewrite into 3-5 concise bullet points describing what a person in this role DOES, '
    'in the imperative voice (e.g., "Design and maintain data pipelines ..."). Rules:\n'
    '- Generic: no company names; no "we are seeking" / "you will" / "JOB SUMMARY"; '
    'no candidate-facing or seniority language; no years-of-experience framing.\n'
    '- Do NOT copy sentences verbatim; rephrase into role duties.\n'
    '- One duty per bullet, <=20 words.\n'
    'Return STRICT JSON: {{"bullets": ["...", "..."]}} (3-5 strings).'
)

def distill(role, raw):
    from config import llm_json
    listing = "\n".join(f"- {s}" for s in raw)
    prompt = _PROMPT.format(role=role, list=listing)
    obj = llm_json([{"role": "user", "content": prompt}], temperature=0.2, purpose="distill",
                   validate=lambda o: (isinstance(o.get("bullets"), list)
                                       and 3 <= len(o["bullets"]) <= 6
                                       and all(isinstance(b, str) and b.strip() for b in o["bullets"])))
    return [b.strip().lstrip("- ").strip() for b in obj["bullets"]]

def main():
    import sys
    try:
        sys.stdout.reconfigure(line_buffering=True)   # show per-role progress live
    except Exception:
        pass
    force = "--force" in sys.argv
    team = next((sys.argv[i + 1] for i, a in enumerate(sys.argv) if a == "--team"), None)
    only = None
    if team:
        d = json.load(open(team))["demo"]
        only = {a["enrichment_key"] for a in d["agents"]}
        print(f"limiting distillation to {len(only)} team roles from {team}")
    data = json.load(open(ROLE_ENRICHMENT_JSON))
    n_done = n_skip = n_fail = 0
    for role, rec in data.items():
        if only is not None and role not in only:
            continue
        raw = rec.get("responsibilities") or []
        if not raw:
            continue
        if rec.get("responsibilities_distilled") and not force:
            n_skip += 1
            print(f"skip (already distilled): {role}")
            continue
        try:
            bullets = distill(role, raw)
            rec["responsibilities_distilled"] = bullets
            n_done += 1
            print(f"distilled: {role} -> {len(bullets)} bullets")
        except Exception as e:
            n_fail += 1
            print(f"FAILED {role}: {e}")
    if n_done:
        shutil.copy2(ROLE_ENRICHMENT_JSON, ROLE_ENRICHMENT_JSON + ".bak")
        tmp = ROLE_ENRICHMENT_JSON + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, ROLE_ENRICHMENT_JSON)
    print(f"\n done={n_done}  skipped={n_skip}  failed={n_fail}  (backup: {ROLE_ENRICHMENT_JSON}.bak)")

if __name__ == "__main__":
    main()
