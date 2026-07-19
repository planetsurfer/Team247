"""One-time LLM tagging of roles -> canonical business functions. Phase A2 of
FUNCTION_INDEX_PLAN.md.

Populates SQLite table `role_functions(role_id, function_id)` so query-time retrieval can
look up roles by canonical function (data/function_taxonomy.json) instead of keyword
overlap. For each role not yet tagged, builds a prompt from the role's name/sector/
description, its skill titles (role_skills), a sample of its K&A items (ka_items, joined on
role_skills.code), and the full taxonomy id+name list; asks purpose="function_tag" for
STRICT JSON {"functions": ["id", ...]} (3-8 ids), validates the ids are a subset of the
taxonomy (drops unknowns; retries once on an empty result), and inserts rows.

Idempotent / resumable (skips role_ids already in role_functions unless --force). Mirrors
distill_enrichment.py's one-time-script pattern; plain sqlite3 (no ORM) matching the
existing inline-DDL style — CREATE TABLE IF NOT EXISTS, no alembic revision.

Usage:
  python build_function_index.py                 # tag all untagged roles, resumable
  python build_function_index.py --limit 12       # tag only the first 12 untagged roles
  python build_function_index.py --force          # re-tag everything (clears + retags)
  python build_function_index.py --audit          # report-only, no writes
"""
import argparse
import collections
import json
import os
import sqlite3
import sys

DB_PATH = os.getenv("APP_DB_PATH", "data/agentproof.db")
TAXONOMY_PATH = os.getenv("FUNCTION_TAXONOMY_JSON", "data/function_taxonomy.json")

DDL = """
CREATE TABLE IF NOT EXISTS role_functions (
  role_id     INTEGER NOT NULL REFERENCES roles(role_id) ON DELETE CASCADE,
  function_id TEXT    NOT NULL,
  PRIMARY KEY (role_id, function_id)
);
CREATE INDEX IF NOT EXISTS role_functions_function_idx ON role_functions(function_id);
"""

_PROMPT = """You are tagging a job role with the canonical BUSINESS FUNCTIONS it performs, so
it can later be looked up by function instead of free-text search.

ROLE: {role}
SECTOR: {sector}
DESCRIPTION: {description}

SKILLS this role requires (skills-framework titles):
{skills}

Sample knowledge/ability statements for this role:
{ka_items}

CANONICAL FUNCTION LIST (id: name) — you may ONLY use ids from this list:
{taxonomy}

Pick the 3-8 functions from the list above that this role actually performs day-to-day.
Prefer specific/operational functions over vague catch-alls. Every id you return MUST be
copied exactly from the list (the part before the colon).

Return STRICT JSON: {{"functions": ["id-1", "id-2", ...]}}
"""


def load_taxonomy():
    entries = json.load(open(TAXONOMY_PATH))
    ids = {e["id"] for e in entries}
    id_name_block = "\n".join(f"- {e['id']}: {e['name']}" for e in entries)
    return entries, ids, id_name_block


def ensure_schema(conn):
    conn.executescript(DDL)
    conn.commit()


def fetch_untagged_roles(conn, limit=None):
    sql = """
        select r.role_id, r.role, r.sector, r.description
        from roles r
        where r.role_id not in (select distinct role_id from role_functions)
        order by r.role_id
    """
    if limit:
        sql += f" limit {int(limit)}"
    return conn.execute(sql).fetchall()


def fetch_all_roles(conn, limit=None):
    sql = "select r.role_id, r.role, r.sector, r.description from roles r order by r.role_id"
    if limit:
        sql += f" limit {int(limit)}"
    return conn.execute(sql).fetchall()


def role_prompt_material(conn, role_id, max_ka=10):
    skills = conn.execute(
        "select distinct skill from role_skills where role_id = ? order by required_level desc",
        (role_id,),
    ).fetchall()
    skill_titles = [s[0] for s in skills]
    codes = [
        r[0]
        for r in conn.execute(
            "select distinct code from role_skills where role_id = ?", (role_id,)
        ).fetchall()
    ]
    ka_items = []
    if codes:
        qmarks = ",".join("?" * len(codes))
        ka_items = [
            r[0]
            for r in conn.execute(
                f"select item from ka_items where code in ({qmarks}) and item is not null and trim(item) != '' limit ?",
                (*codes, max_ka),
            ).fetchall()
        ]
    return skill_titles, ka_items


def tag_role(role_row, skill_titles, ka_items, taxonomy_ids, id_name_block, retried=False):
    from config import llm_json

    role, sector, description = role_row["role"], role_row["sector"], role_row["description"] or ""
    prompt = _PROMPT.format(
        role=role,
        sector=sector,
        description=description[:600],
        skills="\n".join(f"- {s}" for s in skill_titles[:40]) or "(none listed)",
        ka_items="\n".join(f"- {k}" for k in ka_items) or "(none listed)",
        taxonomy=id_name_block,
    )

    def _validate(o):
        return isinstance(o.get("functions"), list)

    obj = llm_json(
        [{"role": "user", "content": prompt}],
        temperature=0.1,
        purpose="function_tag",
        max_tokens=500,
        validate=_validate,
    )
    raw = obj.get("functions", [])
    valid = [f for f in raw if f in taxonomy_ids]
    if not valid and not retried:
        # one retry with a slightly more insistent nudge (empty/garbage result)
        return tag_role(role_row, skill_titles, ka_items, taxonomy_ids, id_name_block, retried=True)
    return valid


def run_tagging(conn, taxonomy_ids, id_name_block, limit=None, force=False):
    if force:
        print("--force: clearing role_functions table")
        conn.execute("delete from role_functions")
        conn.commit()

    roles = fetch_untagged_roles(conn, limit=limit)
    print(f"{len(roles)} roles to tag" + (f" (limit={limit})" if limit else ""))

    n_ok = n_empty = n_fail = 0
    for i, r in enumerate(roles, 1):
        role_id = r["role_id"]
        try:
            skill_titles, ka_items = role_prompt_material(conn, role_id)
            fns = tag_role(r, skill_titles, ka_items, taxonomy_ids, id_name_block)
            if not fns:
                n_empty += 1
                print(f"[{i}/{len(roles)}] role_id={role_id} {r['role']!r} -> EMPTY (no valid functions)")
                continue
            conn.executemany(
                "insert or ignore into role_functions (role_id, function_id) values (?, ?)",
                [(role_id, fid) for fid in fns],
            )
            conn.commit()
            n_ok += 1
            print(f"[{i}/{len(roles)}] role_id={role_id} {r['role']!r} ({r['sector']}) -> {fns}")
        except Exception as e:
            n_fail += 1
            print(f"[{i}/{len(roles)}] role_id={role_id} {r['role']!r} FAILED: {e}")
            continue

    print(f"\ndone: tagged={n_ok} empty={n_empty} failed={n_fail} (of {len(roles)} attempted)")


def run_audit(conn, taxonomy_entries, taxonomy_ids):
    print("=== AUDIT (read-only) ===\n")

    total_roles = conn.execute("select count(*) from roles").fetchone()[0]
    tagged_roles = conn.execute("select count(distinct role_id) from role_functions").fetchone()[0]
    print(f"roles: {tagged_roles}/{total_roles} have >=1 function tagged")

    untagged = conn.execute(
        """
        select r.role_id, r.role, r.sector from roles r
        where r.role_id not in (select distinct role_id from role_functions)
        order by r.role_id
        """
    ).fetchall()
    print(f"\nroles with 0 functions ({len(untagged)}):")
    for r in untagged[:50]:
        print(f"  - [{r['role_id']}] {r['role']} ({r['sector']})")
    if len(untagged) > 50:
        print(f"  ... and {len(untagged) - 50} more")

    counts = collections.Counter(
        r[0] for r in conn.execute("select function_id from role_functions").fetchall()
    )
    zero_fns = [e["id"] for e in taxonomy_entries if counts.get(e["id"], 0) == 0]
    print(f"\nfunctions with 0 roles ({len(zero_fns)} of {len(taxonomy_entries)}):")
    for fid in zero_fns:
        print(f"  - {fid}")

    print("\nper-function role-count histogram (top 30):")
    for fid, c in counts.most_common(30):
        print(f"  {c:5d}  {fid}")

    unknown = set(counts) - taxonomy_ids
    if unknown:
        print(f"\nWARNING: role_functions has {len(unknown)} function_ids not in current taxonomy: {unknown}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None, help="tag only first N untagged roles")
    ap.add_argument("--force", action="store_true", help="clear role_functions and re-tag all roles")
    ap.add_argument("--audit", action="store_true", help="report-only; no writes")
    args = ap.parse_args()

    try:
        sys.stdout.reconfigure(line_buffering=True)
    except Exception:
        pass

    taxonomy_entries, taxonomy_ids, id_name_block = load_taxonomy()
    print(f"loaded taxonomy: {len(taxonomy_entries)} functions from {TAXONOMY_PATH}")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    ensure_schema(conn)

    if args.audit:
        run_audit(conn, taxonomy_entries, taxonomy_ids)
    else:
        run_tagging(conn, taxonomy_ids, id_name_block, limit=args.limit, force=args.force)

    conn.close()


if __name__ == "__main__":
    main()
