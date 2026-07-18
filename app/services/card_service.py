"""Card + catalog business logic for the AgentProof app.

Reads the catalog from the SQLite DB (seeded by app.seed_catalog) and lazily
distills a role's responsibilities on first view. Per-skill K&A rows reuse
teamspec.skill_rows (which reads framework's official K&A index) so the card's
`sk` payload is identical to what the CLI dashboard emits.
"""
import json
import threading

from app import db

# Lazy-distill guard: only one distill per role at a time (per-process).
_distill_locks: dict = {}
_distill_locks_guard = threading.Lock()


def _lock_for(role_id):
    with _distill_locks_guard:
        if role_id not in _distill_locks:
            _distill_locks[role_id] = threading.Lock()
        return _distill_locks[role_id]


def _loads(raw, default):
    if not raw:
        return default
    try:
        return json.loads(raw)
    except Exception:
        return default


def sectors():
    """[{sector, n_roles}] ordered by role count desc."""
    return db.query(
        "SELECT sector, COUNT(*) AS n_roles FROM roles GROUP BY sector "
        "ORDER BY n_roles DESC"
    )


def list_roles(sector=None, track=None, q=None, page=1, size=20):
    """Paginated catalog list. Uses FTS5 when q is given (falls back to LIKE on error)."""
    page = max(1, int(page))
    size = max(1, min(int(size), 100))
    offset = (page - 1) * size
    base_cols = (
        "r.role_id, r.role, r.sector, r.track, r.n_skills, r.n_executable, "
        "(c.role_id IS NOT NULL) AS has_enrichment"
    )
    join = "LEFT JOIN cards c ON c.role_id = r.role_id"
    if q:
        try:
            items = db.query(
                f"SELECT {base_cols} FROM roles_fts f JOIN roles r ON r.role_id = f.rowid "
                f"{join} WHERE roles_fts MATCH ? ORDER BY rank LIMIT ? OFFSET ?",
                (q, size, offset),
            )
            total = db.query(
                "SELECT COUNT(*) AS n FROM roles_fts WHERE roles_fts MATCH ?",
                (q,), one=True,
            )["n"]
        except Exception:
            like = f"%{q}%"
            where = "WHERE r.role LIKE ? OR r.description LIKE ?"
            items = db.query(
                f"SELECT {base_cols} FROM roles r {join} {where} ORDER BY r.role LIMIT ? OFFSET ?",
                (like, like, size, offset),
            )
            total = db.query(
                f"SELECT COUNT(*) AS n FROM roles r {where}", (like, like), one=True
            )["n"]
    else:
        where, params = [], []
        if sector:
            where.append("r.sector = ?"); params.append(sector)
        if track:
            where.append("r.track = ?"); params.append(track)
        wsql = ("WHERE " + " AND ".join(where)) if where else ""
        items = db.query(
            f"SELECT {base_cols} FROM roles r {join} {wsql} ORDER BY r.role LIMIT ? OFFSET ?",
            tuple(params + [size, offset]),
        )
        total = db.query(f"SELECT COUNT(*) AS n FROM roles r {wsql}", tuple(params), one=True)["n"]
    return {"items": items, "total": total, "page": page, "size": size}


def get_role(role_id):
    return db.query("SELECT * FROM roles WHERE role_id = ?", (int(role_id),), one=True)


def distill_state(role_id):
    """(status, distilled_bullets) for a card. status ∈ 'done'|'pending'|'failed'|'cold'|'none'."""
    row = db.query(
        "SELECT responsibilities_distilled, distilled_status FROM cards WHERE role_id = ?",
        (int(role_id),), one=True,
    )
    if row is None:
        return ("none", None)  # no enrichment row at all → framework-sourced only
    if row["responsibilities_distilled"]:
        return ("done", _loads(row["responsibilities_distilled"], []))
    return (row["distilled_status"] or "cold", None)


def distill_role_sync(role_id):
    """Synchronously distill one role (one LLM call). Returns (status, bullets)."""
    from app import seed_catalog  # app.seed_catalog — lazy import (avoids framework warm at import time)
    with _lock_for(role_id):
        # re-check after acquiring lock (another request may have just finished)
        st, bullets = distill_state(role_id)
        if st == "done":
            return (st, bullets)
        try:
            seed_catalog.distill_role(int(role_id))
        except Exception:
            return ("failed", None)
    return distill_state(role_id)


def card_payload(role_id):
    """Full card DTO for GET /api/catalog/{role_id}/card. Triggers lazy distill if cold."""
    role = get_role(role_id)
    if role is None:
        return None
    card = db.query("SELECT * FROM cards WHERE role_id = ?", (int(role_id),), one=True) or {}

    st, distilled = distill_state(role_id)
    if st in ("cold", None, "", "none") or (st != "done" and not distilled):
        # cold → distill synchronously (one call ~1-3s), then re-read
        st, distilled = distill_role_sync(role_id)
    distilled = distilled or []

    # Per-skill K&A rows reuse the CLI's authoritative renderer (reads framework xlsx).
    import teamspec
    sk = teamspec.skill_rows(role["role"], {})

    return {
        "role_id": role["role_id"],
        "role": role["role"],
        "sector": role["sector"],
        "track": role["track"],
        "description": role.get("description"),
        "performance_expectation": role.get("performance_expectation"),
        "critical_work_functions": _loads(role.get("critical_work_functions"), []),
        "n_skills": role["n_skills"],
        "n_executable": role["n_executable"],
        "distilled_status": st,
        "distilled": distilled,
        "tools": _loads(card.get("tools"), []),
        "salary_band": {
            "low": card.get("salary_low"),
            "median": card.get("salary_median"),
            "high": card.get("salary_high"),
            "currency": card.get("salary_currency"),
        } if card.get("salary_median") else None,
        "n_postings": card.get("n_postings"),
        "matched_via": card.get("matched_via"),
        "has_enrichment": bool(card),
        "sk": sk,
    }


def battery_items(role_id):
    return db.query(
        "SELECT item_id, code, skill, required_level, source, status FROM "
        "card_battery_items WHERE role_id = ? ORDER BY code",
        (int(role_id),),
    )
