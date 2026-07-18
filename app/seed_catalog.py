"""Offline + lazy seeding of the AgentProof card catalog.

Phases:
  A (offline, 0 LLM)  — roles + role_skills + ka_items from the SFw xlsx
  B (offline, 0 LLM)  — migrate data/role_enrichment.json into cards
  C (lazy, LLM)       — distill_role(role_id): condense responsibilities_raw into
                        responsibilities_distilled; cached forever; synthesises
                        context bullets from critical_work_functions when raw is empty
  D (offline, 0 LLM)  — seed the known battery items from data/battery_seed.json

Idempotent throughout (INSERT OR IGNORE / OR REPLACE). Two-track honesty: un-enriched
roles simply carry NULL enrichment columns — nothing is invented.

CLI:
  python -m app.seed_catalog                  # bootstrap + seed + distill_popular
  python -m app.seed_catalog --idempotent     # bootstrap + seed ONLY (no LLM; Docker entrypoint)
  python -m app.seed_catalog --pre-distill-popular  # bootstrap + seed + distill_popular
  python -m app.seed_catalog --distill-all    # bootstrap + seed + distill_all (admin)
"""
import json
import os
import datetime
import argparse

from app import db, settings


ROLE_ENRICHMENT_JSON = os.getenv("ROLE_ENRICHMENT_JSON", "data/role_enrichment.json")
BATTERY_SEED_JSON = os.getenv("BATTERY_SEED_JSON", "data/battery_seed.json")
DEMO_TEAM_JSON = os.getenv("DEMO_TEAM_JSON", "data/demo_team.json")


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


# ──────────────────────────────────────────────────────────────────────────────
# Phase A — offline seed: roles + role_skills + ka_items
# ──────────────────────────────────────────────────────────────────────────────
def _seed_roles(framework) -> int:
    """Insert every SFw role (1910). Returns number of roles now in the table."""
    rows = framework._sheet("Job Role_Description")
    now = _now()
    params = []
    for r in rows:
        if not r or not r[2]:
            continue
        sector, track, role = (r[0] or ""), (r[1] or ""), r[2]
        desc = r[3] if len(r) > 3 and r[3] else None
        perf = r[4] if len(r) > 4 and r[4] else None
        params.append((role, sector, track, desc, perf, now))
    # Stable role_id via enumerate+1 (SQLite INTEGER PK autoincrement would also work,
    # but stable ids make role_skills/ka resolution reproducible across re-seeds).
    params = list(dict.fromkeys(params))  # dedup by role (preserves first occurrence)
    with db.get_conn() as conn:
        for i, p in enumerate(params, start=1):
            role = p[0]
            conn.execute(
                "INSERT OR IGNORE INTO roles(role_id, role, sector, track, description, "
                "performance_expectation, seeded_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (i, role, p[1], p[2], p[3], p[4], p[5]),
            )
        conn.commit()
        n = conn.execute("SELECT COUNT(*) FROM roles").fetchone()[0]
    return n


def _seed_role_skills(framework) -> int:
    """For each role, insert its skill rows (dedup by (role_id, code)) and update
    roles.n_skills / n_executable. Returns total skill rows inserted this pass."""
    exec_codes_by_role = {}
    skill_params = []
    # Pull all roles + role_ids up front (one query).
    roles = db.query("SELECT role_id, role FROM roles")
    for r in roles:
        role_id, role = r["role_id"], r["role"]
        skills = framework.get_skills(role)
        # Dedup by code exactly like teamspec.skill_rows (first occurrence wins).
        seen = set()
        ded = []
        for s in skills:
            if s["code"] not in seen:
                seen.add(s["code"])
                ded.append(s)
        exec_set = {x["code"] for x in framework.select_executable(ded, k=99)}
        exec_codes_by_role[role_id] = (len(ded), len([s for s in ded if s["code"] in exec_set]))
        for s in ded:
            skill_params.append((
                role_id, s["code"], s["skill"], s.get("type"),
                int(s["required_level"]), 1 if s["code"] in exec_set else 0,
            ))
    if skill_params:
        db.executemany(
            "INSERT OR IGNORE INTO role_skills(role_id, code, skill, skill_type, "
            "required_level, is_executable) VALUES (?, ?, ?, ?, ?, ?)",
            skill_params,
        )
    # Update counts on roles.
    with db.get_conn() as conn:
        for role_id, (n_sk, n_ex) in exec_codes_by_role.items():
            conn.execute(
                "UPDATE roles SET n_skills = ?, n_executable = ? WHERE role_id = ?",
                (n_sk, n_ex, role_id),
            )
        conn.commit()
    return len(skill_params)


def _seed_ka_items(framework) -> int:
    """One pass over framework._ka_index() → ka_items (dedup by UNIQUE(code,level,kind,item))."""
    idx = framework._ka_index()  # warmup 15-30s on first touch — fine
    params = []
    for (code, level), entry in idx.items():
        prof = entry.get("proficiency_description") or ""
        for it in entry.get("items", []):
            item = it.get("item", "")
            kind = (it.get("kind") or "other") or "other"
            if not item:
                continue
            params.append((code, int(level), kind, str(item), prof))
    if params:
        db.executemany(
            "INSERT OR IGNORE INTO ka_items(code, level, kind, item, proficiency_description) "
            "VALUES (?, ?, ?, ?, ?)",
            params,
        )
    return len(params)


# ──────────────────────────────────────────────────────────────────────────────
# Phase B — migrate data/role_enrichment.json → cards
# ──────────────────────────────────────────────────────────────────────────────
def _seed_cards() -> int:
    if not os.path.exists(ROLE_ENRICHMENT_JSON):
        return 0
    with open(ROLE_ENRICHMENT_JSON, "r", encoding="utf-8") as f:
        data = json.load(f)
    now = _now()
    n = 0
    for role_name, rec in data.items():
        row = db.query("SELECT role_id FROM roles WHERE role = ?", (role_name,), one=True)
        if not row:
            # Unknown role — skip rather than fabricate a role row.
            continue
        role_id = row["role_id"]
        sb = rec.get("salary_band") or {}
        raw = rec.get("responsibilities") or []
        distilled = rec.get("responsibilities_distilled")  # may be present from a prior distill run
        tools = rec.get("tools") or []
        src_urls = rec.get("source_urls") or []
        matched = rec.get("matched_via")
        n_post = rec.get("n_postings")
        fetched = rec.get("fetched_at") or now
        has_distilled = 1 if distilled else 0
        db.execute(
            "INSERT OR REPLACE INTO cards(role_id, has_enrichment, n_postings, matched_via, "
            "salary_low, salary_median, salary_high, salary_currency, tools, source_urls, "
            "responsibilities_raw, responsibilities_distilled, distilled_at, distilled_status, "
            "fetched_at) VALUES (?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                role_id, n_post, matched,
                sb.get("low"), sb.get("median"), sb.get("high"), sb.get("currency"),
                json.dumps(tools, ensure_ascii=False),
                json.dumps(src_urls, ensure_ascii=False),
                json.dumps(raw, ensure_ascii=False),
                json.dumps(distilled, ensure_ascii=False) if distilled else None,
                now if has_distilled else None,
                "done" if has_distilled else None,
                fetched,
            ),
        )
        n += 1
    return n


# ──────────────────────────────────────────────────────────────────────────────
# Phase D — seed known battery items
# ──────────────────────────────────────────────────────────────────────────────
def _resolve_code_to_role_id(code: str, demo_code_map: dict) -> int | None:
    """Resolve which role_id owns a given skill code. Prefer the demo team's
    explicit code→role mapping; fall back to the first role_skills row."""
    role_name = demo_code_map.get(code)
    if role_name:
        row = db.query("SELECT role_id FROM roles WHERE role = ?", (role_name,), one=True)
        if row:
            return row["role_id"]
    # Fallback: any role_skills row carrying this code.
    row = db.query(
        "SELECT role_id FROM role_skills WHERE code = ? ORDER BY role_id LIMIT 1",
        (code,), one=True,
    )
    return row["role_id"] if row else None


def _seed_battery() -> int:
    if not os.path.exists(BATTERY_SEED_JSON):
        return 0
    with open(BATTERY_SEED_JSON, "r", encoding="utf-8") as f:
        items = json.load(f)
    # Build code→role mapping from the demo team manifest (canonical owner per code).
    demo_code_map = {}
    if os.path.exists(DEMO_TEAM_JSON):
        try:
            with open(DEMO_TEAM_JSON, "r", encoding="utf-8") as f:
                team = json.load(f).get("demo", {})
            for agent in team.get("agents", []):
                role = agent.get("role")
                for bi in agent.get("battery_items", []) or []:
                    if bi.get("code") and role:
                        demo_code_map.setdefault(bi["code"], role)
        except Exception:
            pass
    now = _now()
    n = 0
    for it in items:
        code = it.get("code")
        if not code:
            continue
        role_id = _resolve_code_to_role_id(code, demo_code_map)
        if not role_id:
            continue
        db.execute(
            "INSERT OR IGNORE INTO card_battery_items(role_id, code, skill, required_level, "
            "task_prompt, grader_code, source, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 'seed', 'ready', ?)",
            (
                role_id, code, it.get("skill", ""), int(it.get("required_level", 0)),
                it.get("task_prompt", ""), it.get("grader_code", ""), now,
            ),
        )
        n += 1
    return n


def _seed_catalog() -> dict:
    """Phase A + B + D. Fully idempotent, NO LLM calls. Returns a counts dict."""
    import framework  # lazy — heavy on first import (xlsx read)

    n_roles = _seed_roles(framework)
    n_skills = _seed_role_skills(framework)
    n_ka = _seed_ka_items(framework)
    n_cards = _seed_cards()
    n_batt = _seed_battery()
    return {
        "roles": n_roles,
        "role_skills": n_skills,
        "ka_items": n_ka,
        "cards": n_cards,
        "battery": n_batt,
    }


# ──────────────────────────────────────────────────────────────────────────────
# Phase C — lazy distillation (one LLM call per cold role, cached forever)
# ──────────────────────────────────────────────────────────────────────────────
def _synthesize_raw_from_context(role: str) -> list[str]:
    """When responsibilities_raw is empty, build pseudo-raw bullets from the official
    critical_work_functions so the distiller still has role-specific material to condense."""
    import framework
    ctx = framework.get_context(role) or {}
    cwfs = ctx.get("critical_work_functions") or []
    bullets = []
    for cwf in cwfs:
        name = cwf.get("cwf", "").strip()
        tasks = [t for t in cwf.get("key_tasks", []) if t]
        if not name:
            continue
        if tasks:
            bullets.append(f"{name}: " + "; ".join(t.rstrip(".") for t in tasks))
        else:
            bullets.append(name)
    return bullets


def distill_role(role_id: int) -> bool:
    """Distill one role's responsibilities into imperative bullets, cached in cards.
    Returns True if distilled (or already distilled), False on failure."""
    import distill_enrichment

    row = db.query(
        "SELECT c.role_id, c.responsibilities_raw, c.responsibilities_distilled, r.role "
        "FROM cards c JOIN roles r ON r.role_id = c.role_id WHERE c.role_id = ?",
        (role_id,), one=True,
    )
    if not row:
        # No card row yet — nothing to distill. (Un-enriched roles have no card row.)
        return False
    if row["responsibilities_distilled"]:
        return True  # already cached

    # Mark pending immediately so concurrent requests see the in-flight state.
    db.execute(
        "UPDATE cards SET distilled_status = 'pending' WHERE role_id = ?",
        (role_id,),
    )

    raw = []
    if row["responsibilities_raw"]:
        try:
            parsed = json.loads(row["responsibilities_raw"])
            if isinstance(parsed, list):
                raw = [str(x) for x in parsed if str(x).strip()]
        except Exception:
            raw = []
    if not raw:
        raw = _synthesize_raw_from_context(row["role"])
    if not raw:
        # Truly nothing to say about this role — mark done with empty bullets rather
        # than spin on an empty LLM call.
        db.execute(
            "UPDATE cards SET responsibilities_distilled = ?, distilled_status = 'done', "
            "distilled_at = ? WHERE role_id = ?",
            (json.dumps([], ensure_ascii=False), _now(), role_id),
        )
        return True

    try:
        bullets = distill_enrichment.distill(row["role"], raw)
        db.execute(
            "UPDATE cards SET responsibilities_distilled = ?, distilled_status = 'done', "
            "distilled_at = ? WHERE role_id = ?",
            (json.dumps(bullets, ensure_ascii=False), _now(), role_id),
        )
        return True
    except Exception:
        db.execute(
            "UPDATE cards SET distilled_status = 'failed' WHERE role_id = ?",
            (role_id,),
        )
        return False


def _demo_team_enrichment_keys() -> list[str]:
    if not os.path.exists(DEMO_TEAM_JSON):
        return []
    try:
        with open(DEMO_TEAM_JSON, "r", encoding="utf-8") as f:
            team = json.load(f).get("demo", {})
        return [a["enrichment_key"] for a in team.get("agents", []) if a.get("enrichment_key")]
    except Exception:
        return []


def distill_popular(n: int | None = None) -> int:
    """Pre-warm the top-N most popular cold roles by n_postings, topped up with the
    demo team's enrichment_keys if fewer than n qualify. Best-effort; per-role failures
    are swallowed. Returns the number of roles successfully distilled."""
    if n is None:
        n = settings.PRE_DISTILL_POPULAR
    target_ids: list[int] = []

    # Top-N by n_postings desc.
    rows = db.query(
        "SELECT role_id FROM cards WHERE responsibilities_distilled IS NULL "
        "ORDER BY COALESCE(n_postings, 0) DESC, role_id ASC LIMIT ?",
        (n,),
    )
    target_ids = [r["role_id"] for r in rows]

    # Top up with the demo team's enrichment keys (the canonical common path).
    if len(target_ids) < n:
        seen = set(target_ids)
        for key in _demo_team_enrichment_keys():
            if len(target_ids) >= n:
                break
            row = db.query("SELECT role_id FROM roles WHERE role = ?", (key,), one=True)
            if row and row["role_id"] not in seen:
                # Only include if it actually has a card row worth distilling.
                card = db.query(
                    "SELECT responsibilities_distilled FROM cards WHERE role_id = ?",
                    (row["role_id"],), one=True,
                )
                if card and card["responsibilities_distilled"] is None:
                    target_ids.append(row["role_id"])
                    seen.add(row["role_id"])

    distilled = 0
    for rid in target_ids:
        try:
            if distill_role(rid):
                distilled += 1
        except Exception:
            # Best-effort; never abort the batch on one failure.
            continue
    return distilled


def distill_all() -> int:
    """Distill every role whose responsibilities_distilled is NULL. Admin-only; long."""
    rows = db.query(
        "SELECT role_id FROM cards WHERE responsibilities_distilled IS NULL ORDER BY role_id"
    )
    distilled = 0
    for r in rows:
        try:
            if distill_role(r["role_id"]):
                distilled += 1
        except Exception:
            continue
    return distilled


# ──────────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────────
def main() -> None:
    p = argparse.ArgumentParser(description="Seed the AgentProof card catalog.")
    p.add_argument("--idempotent", action="store_true",
                   help="bootstrap + seed only (no LLM calls; safe for Docker entrypoint re-runs)")
    p.add_argument("--pre-distill-popular", action="store_true",
                   help="bootstrap + seed + distill_popular (top-N pre-warm)")
    p.add_argument("--distill-all", action="store_true",
                   help="bootstrap + seed + distill_all (admin; long)")
    args = p.parse_args()

    db.bootstrap()
    counts = _seed_catalog()
    print(f"[seed] roles={counts['roles']} role_skills={counts['role_skills']} "
          f"ka_items={counts['ka_items']} cards={counts['cards']} "
          f"battery={counts['battery']}")

    if args.idempotent:
        return
    if args.distill_all:
        n = distill_all()
        print(f"[distill-all] distilled={n}")
        return
    # Default and --pre-distill-popular both pre-warm the popular path.
    n = distill_popular()
    print(f"[distill-popular] distilled={n}")


if __name__ == "__main__":
    main()
