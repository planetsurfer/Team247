"""One-time bootstrap of data/function_taxonomy.json — Phase A1 of FUNCTION_INDEX_PLAN.md.

Samples the top skill titles per sector from role_skills (raw material), then drafts a
canonical, cross-sector business-function taxonomy via a handful of llm_json(purpose=
"function_tag") calls: one per batch of sectors (each batch reuses ids already collected
so the same function isn't re-invented under a different id), followed by one consolidation
pass that merges any near-duplicate ids the batches still produced.

Not resumable / not meant to run 1910 times like build_function_index.py — this is a small,
one-off drafting script, rerun by hand when the taxonomy needs a refresh (e.g. after
build_function_index.py --audit reports orphan functions or an obvious gap). Writes pretty
JSON to data/function_taxonomy.json (backs up any existing file to .bak first).

Usage: python build_taxonomy.py [--batch-size N] [--top-n N] [--out PATH]
"""
import json
import os
import re
import shutil
import sys
import sqlite3
import argparse

DB_PATH = os.getenv("APP_DB_PATH", "data/agentproof.db")
OUT_PATH_DEFAULT = "data/function_taxonomy.json"

_KEBAB_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


def fetch_sector_skills(top_n=20):
    conn = sqlite3.connect(DB_PATH)
    sectors = [r[0] for r in conn.execute("select distinct sector from roles order by sector")]
    out = {}
    for s in sectors:
        rows = conn.execute(
            """
            select rs.skill, count(*) c
            from role_skills rs join roles r on r.role_id = rs.role_id
            where r.sector = ?
            group by rs.skill order by c desc limit ?
            """,
            (s, top_n),
        ).fetchall()
        out[s] = [r[0] for r in rows]
    conn.close()
    return sectors, out


def _kebab(s):
    s = s.strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return re.sub(r"-+", "-", s).strip("-")


_BATCH_PROMPT = """You are drafting a canonical taxonomy of cross-industry BUSINESS FUNCTIONS
(not job titles, not skills-framework names) from a sample of skill titles observed in
these industry sectors:

{sector_block}

A "function" here means a general business capability that recurs across industries —
e.g. "Accounts Receivable / Collections", "Rostering & Scheduling", "Vendor Management",
"Compliance & Regulatory Reporting". It is NOT a sector-specific skill name and NOT a job
title. Multiple sectors doing the "same" thing (e.g. scheduling shifts) must map to ONE
function, not one per sector.

Functions already collected from earlier sectors (id: name) — REUSE an existing id if a
skill below is really the same function; only add a NEW id if it is genuinely not covered:
{existing_block}

From the skill titles above, propose the NEW canonical functions still missing (do not
re-emit ones already covered by the existing list unless you are refining that exact one).
Aim for ~15-30 new functions from this batch. For each, return:
  id: kebab-case, stable, 2-5 words (e.g. "accounts-receivable")
  name: short human title (e.g. "Accounts Receivable / Collections")
  definition: one sentence, what this function covers
  everyday_phrasings: 3-6 plain-language phrasings a non-expert would type when asking for
    help with this function (e.g. "chase unpaid invoices", "who owes us money") — NOT
    jargon, NOT the skill-framework wording.

Return STRICT JSON: {{"functions": [{{"id": "...", "name": "...", "definition": "...",
"everyday_phrasings": ["...", "..."]}}, ...]}}
"""

_MERGE_PROMPT = """Below is a draft business-function taxonomy (id: name pairs) built up across
several batches. Some ids may be near-duplicates of each other (same underlying business
function, different wording/id). Identify ONLY genuine duplicates/near-duplicates — do not
merge functions that are merely related but distinct (e.g. "accounts-receivable" and
"accounts-payable" are DIFFERENT, keep both).

{id_name_block}

Return STRICT JSON: {{"merges": [{{"keep": "id-to-keep", "drop": ["id-a", "id-b"]}}, ...]}}
Only include entries where drop is non-empty. If there are no duplicates, return
{{"merges": []}}.
"""


def _chunk(lst, n):
    for i in range(0, len(lst), n):
        yield lst[i : i + n]


def draft_taxonomy(batch_size=7, top_n=20):
    from config import llm_json

    sectors, sector_skills = fetch_sector_skills(top_n=top_n)
    print(f"sampled top-{top_n} skills across {len(sectors)} sectors")

    collected = {}  # id -> entry
    for batch_i, batch in enumerate(_chunk(sectors, batch_size)):
        sector_block = "\n".join(
            f"## {s}\n" + "\n".join(f"- {sk}" for sk in sector_skills[s]) for s in batch
        )
        existing_block = "\n".join(f"- {i}: {e['name']}" for i, e in collected.items()) or "(none yet)"
        prompt = _BATCH_PROMPT.format(sector_block=sector_block, existing_block=existing_block)

        def _validate(o):
            if not isinstance(o.get("functions"), list) or not o["functions"]:
                return False
            for f in o["functions"]:
                if not isinstance(f, dict):
                    return False
                if not all(k in f for k in ("id", "name", "definition", "everyday_phrasings")):
                    return False
                if not isinstance(f["everyday_phrasings"], list) or not f["everyday_phrasings"]:
                    return False
            return True

        try:
            obj = llm_json(
                [{"role": "user", "content": prompt}],
                temperature=0.2,
                purpose="function_tag",
                max_tokens=6000,
                validate=_validate,
            )
        except Exception as e:
            print(f"batch {batch_i} ({batch}) FAILED: {e}")
            continue

        n_new = 0
        for f in obj["functions"]:
            fid = _kebab(f["id"])
            if not fid:
                continue
            if fid in collected:
                continue  # keep first-seen definition
            collected[fid] = {
                "id": fid,
                "name": str(f["name"]).strip(),
                "definition": str(f["definition"]).strip(),
                "everyday_phrasings": [str(p).strip() for p in f["everyday_phrasings"] if str(p).strip()],
            }
            n_new += 1
        print(f"batch {batch_i} sectors={batch} -> +{n_new} new (total {len(collected)})")

    return collected


def consolidate(collected):
    """One merge pass: ask the LLM to flag near-duplicate ids, then union-merge them."""
    from config import llm_json

    id_name_block = "\n".join(f"- {i}: {e['name']}" for i, e in collected.items())

    def _validate(o):
        return isinstance(o.get("merges"), list) and all(
            isinstance(m, dict) and "keep" in m and isinstance(m.get("drop"), list) for m in o["merges"]
        )

    try:
        obj = llm_json(
            [{"role": "user", "content": _MERGE_PROMPT.format(id_name_block=id_name_block)}],
            temperature=0.1,
            purpose="function_tag",
            max_tokens=6000,
            validate=_validate,
        )
    except Exception as e:
        print(f"consolidation pass FAILED (skipping merge): {e}")
        return collected

    merges = obj.get("merges", [])
    n_merged = 0
    for m in merges:
        keep = _kebab(m["keep"])
        if keep not in collected:
            continue
        for drop in m.get("drop", []):
            drop = _kebab(drop)
            if drop in collected and drop != keep:
                # fold drop's phrasings into keep (dedup), then remove drop
                keep_phr = set(collected[keep]["everyday_phrasings"])
                for p in collected[drop]["everyday_phrasings"]:
                    if p not in keep_phr:
                        collected[keep]["everyday_phrasings"].append(p)
                        keep_phr.add(p)
                del collected[drop]
                n_merged += 1
    print(f"consolidation: merged away {n_merged} duplicate ids (proposed {len(merges)} groups)")
    return collected


def validate_taxonomy(entries):
    ids = [e["id"] for e in entries]
    problems = []
    if len(ids) != len(set(ids)):
        dupes = {i for i in ids if ids.count(i) > 1}
        problems.append(f"duplicate ids: {dupes}")
    for e in entries:
        if not _KEBAB_RE.match(e["id"]):
            problems.append(f"non-kebab id: {e['id']!r}")
        if not e.get("name", "").strip():
            problems.append(f"empty name for {e['id']}")
        if not e.get("definition", "").strip():
            problems.append(f"empty definition for {e['id']}")
        if not e.get("everyday_phrasings"):
            problems.append(f"empty everyday_phrasings for {e['id']}")
    if not (150 <= len(entries) <= 250):
        problems.append(f"entry count {len(entries)} outside 150-250 (warning, not fatal)")
    return problems


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch-size", type=int, default=7)
    ap.add_argument("--top-n", type=int, default=20)
    ap.add_argument("--out", default=OUT_PATH_DEFAULT)
    ap.add_argument("--skip-consolidate", action="store_true")
    args = ap.parse_args()

    collected = draft_taxonomy(batch_size=args.batch_size, top_n=args.top_n)
    if not args.skip_consolidate:
        collected = consolidate(collected)

    entries = sorted(collected.values(), key=lambda e: e["id"])
    problems = validate_taxonomy(entries)
    for p in problems:
        print("VALIDATION:", p)

    if os.path.exists(args.out):
        shutil.copy2(args.out, args.out + ".bak")
    with open(args.out, "w") as f:
        json.dump(entries, f, indent=2, ensure_ascii=False)
    print(f"\nwrote {len(entries)} functions -> {args.out}")


if __name__ == "__main__":
    main()
