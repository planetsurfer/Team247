"""Closed-beta token auth (PRODUCTION_ROADMAP.md P0 #1).

Plaintext tokens are shown to an operator exactly once, at mint time
(`python -m app.mint_token`). The DB only ever stores a salted SHA-256 hash
(`beta_tokens.token_hash`); nothing in this module logs, prints, or persists
a plaintext token beyond that single mint-time print.

Two FastAPI dependencies drive the request path:

    require_beta(request)   — gate on every LLM-driving endpoint. Accepts a
                               valid active beta token OR the admin token
                               (admin is a strict superset). Stamps
                               `request.state.token_hash` / `.is_admin` /
                               `.daily_quota` for downstream use (rate
                               limiting, quota).
    consume_quota(request)  — gate on the expensive generation endpoints only
                               (recommend). Atomically bumps today's
                               `beta_token_usage` row and 429s once the
                               token's `daily_quota` would be exceeded. No-op
                               for admin / unlimited tokens.

Both are no-ops when `settings.BETA_AUTH` is off (local/dev/test default).
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timezone

from fastapi import HTTPException, Request

from app import db, settings

_TOKEN_PREFIX = "t247_"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _today() -> str:
    """UTC calendar day, 'YYYY-MM-DD' — the daily-quota rollover boundary."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _hash(token: str) -> str:
    """Salted SHA-256 of a plaintext token. This is the only form ever stored."""
    return hashlib.sha256((settings.BETA_TOKEN_SALT + token).encode("utf-8")).hexdigest()


def _bearer_token(request: Request) -> str | None:
    """Extract the raw bearer token from the Authorization header, or None."""
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer "):
        return None
    return header[7:].strip() or None


# ── minting / management (used by app.mint_token; no plaintext ever stored) ──
def mint(label: str, daily_quota: int | None = None,
         total_quota: int | None = None) -> str:
    """Create a new active beta token, store only its hash, return the plaintext once.

    daily_quota caps generations per UTC day; total_quota caps them for the
    token's lifetime (both NULL = unlimited, enforced in consume_quota).
    """
    token = _TOKEN_PREFIX + secrets.token_urlsafe(24)
    db.execute(
        "INSERT INTO beta_tokens (token_hash, label, active, created_at, last_used_at, "
        "daily_quota, total_quota) VALUES (?, ?, 1, ?, NULL, ?, ?)",
        (_hash(token), label, _now(), daily_quota, total_quota),
    )
    return token


def revoke(label_or_hashprefix: str) -> int:
    """Deactivate every active token matching by exact label or a hash prefix.

    Returns the number of tokens revoked. Never touches or logs plaintext —
    matching against a hash prefix is how an operator identifies a token
    without the plaintext (list_tokens() only ever shows the prefix).
    """
    rows = db.query(
        "SELECT token_hash FROM beta_tokens WHERE active = 1 AND (label = ? OR token_hash LIKE ?)",
        (label_or_hashprefix, label_or_hashprefix + "%"),
    )
    if not rows:
        return 0
    db.executemany(
        "UPDATE beta_tokens SET active = 0 WHERE token_hash = ?",
        [(r["token_hash"],) for r in rows],
    )
    return len(rows)


def list_tokens() -> list[dict]:
    """All tokens, newest first. Hashes are truncated to an 8-char prefix —
    enough to identify a token to `revoke()` without ever exposing the full hash.
    """
    rows = db.query(
        "SELECT t.token_hash, t.label, t.active, t.created_at, t.last_used_at, "
        "t.daily_quota, t.total_quota, COALESCE(SUM(u.count), 0) AS used "
        "FROM beta_tokens t LEFT JOIN beta_token_usage u ON u.token_hash = t.token_hash "
        "GROUP BY t.token_hash ORDER BY t.created_at DESC"
    )
    return [
        {
            "hash_prefix": r["token_hash"][:8],
            "label": r["label"],
            "active": bool(r["active"]),
            "created_at": r["created_at"],
            "last_used_at": r["last_used_at"],
            "daily_quota": r["daily_quota"],
            "total_quota": r["total_quota"],
            "used": r["used"],
        }
        for r in rows
    ]


# ── request-time dependencies ────────────────────────────────────────────────
def require_beta(request: Request) -> None:
    """FastAPI dependency: 401s unless the request carries a valid beta token
    or the admin token. No-op entirely when BETA_AUTH is off.

    On success, stamps request.state so downstream code (consume_quota, the
    rate limiter's key_func) can key off the caller's identity without
    re-parsing the header or re-hashing the token.
    """
    if not settings.BETA_AUTH:
        return

    auth_header = request.headers.get("authorization", "")
    if settings.admin_token_ok(auth_header):
        request.state.is_admin = True
        request.state.token_hash = "admin"
        request.state.daily_quota = None
        request.state.total_quota = None
        return

    token = _bearer_token(request)
    if not token:
        raise HTTPException(status_code=401, detail="beta token required")

    token_hash = _hash(token)
    row = db.query(
        "SELECT active, daily_quota, total_quota FROM beta_tokens WHERE token_hash = ?",
        (token_hash,),
        one=True,
    )
    if not row or not row["active"]:
        raise HTTPException(status_code=401, detail="beta token required")

    request.state.is_admin = False
    request.state.token_hash = token_hash
    request.state.daily_quota = row["daily_quota"]
    request.state.total_quota = row["total_quota"]

    try:
        db.execute(
            "UPDATE beta_tokens SET last_used_at = ? WHERE token_hash = ?",
            (_now(), token_hash),
        )
    except Exception:  # noqa: BLE001 — last_used_at bookkeeping is best-effort
        pass


def is_authenticated(request: Request) -> bool:
    """True if the request carries a currently-valid beta or admin token.
    Does NOT raise, does NOT touch last_used_at — used by GET /api/auth/status.
    """
    auth_header = request.headers.get("authorization", "")
    if settings.admin_token_ok(auth_header):
        return True
    token = _bearer_token(request)
    if not token:
        return False
    row = db.query(
        "SELECT active FROM beta_tokens WHERE token_hash = ?",
        (_hash(token),),
        one=True,
    )
    return bool(row and row["active"])


def consume_quota(request: Request) -> None:
    """FastAPI dependency for GENERATION endpoints only (team/recommend,
    intake/{sid}/recommend). Must run after require_beta on the same request
    (reads request.state.token_hash / .daily_quota it stamps).

    No-op when BETA_AUTH is off, for the admin token, or for a token with no
    configured quota (daily_quota IS NULL = unlimited). Otherwise atomically
    upserts today's (UTC) beta_token_usage row and 429s once the increment
    would exceed the quota.
    """
    if not settings.BETA_AUTH:
        return
    if getattr(request.state, "is_admin", False):
        return

    token_hash = getattr(request.state, "token_hash", None)
    quota = getattr(request.state, "daily_quota", None)
    total_quota = getattr(request.state, "total_quota", None)
    if not token_hash or (quota is None and total_quota is None):
        return  # unlimited token, or require_beta didn't run (nothing to meter)

    day = _today()
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT count FROM beta_token_usage WHERE token_hash = ? AND day = ?",
            (token_hash, day),
        ).fetchone()
        current = row["count"] if row else 0
        if quota is not None and current + 1 > quota:
            raise HTTPException(status_code=429, detail="daily quota exceeded")
        if total_quota is not None:
            lifetime = conn.execute(
                "SELECT COALESCE(SUM(count), 0) AS n FROM beta_token_usage "
                "WHERE token_hash = ?", (token_hash,),
            ).fetchone()["n"]
            if lifetime + 1 > total_quota:
                raise HTTPException(status_code=429, detail="generation quota exhausted")
        if row:
            conn.execute(
                "UPDATE beta_token_usage SET count = count + 1 WHERE token_hash = ? AND day = ?",
                (token_hash, day),
            )
        else:
            conn.execute(
                "INSERT INTO beta_token_usage (token_hash, day, count) VALUES (?, ?, 1)",
                (token_hash, day),
            )


def consume_chat_turn(request: Request) -> None:
    """FastAPI dependency for the try-your-agent CHAT endpoint only
    (team/{id}/agents/{id}/chat). Must run after require_beta on the same
    request (reads request.state.token_hash it stamps).

    Separate budget from consume_quota's generation daily_quota: a chat turn
    is a single llm_chat call, not a full recommend/verify, so it is metered
    against its own flat per-token daily allowance (settings.CHAT_TURNS_PER_DAY,
    beta_chat_usage — mirrors beta_token_usage) rather than eating into the
    token's configured generation quota.

    No-op when BETA_AUTH is off or for the admin token. Otherwise atomically
    upserts today's (UTC) beta_chat_usage row and 429s
    ("daily chat allowance exhausted") once the increment would exceed
    settings.CHAT_TURNS_PER_DAY.
    """
    if not settings.BETA_AUTH:
        return
    if getattr(request.state, "is_admin", False):
        return

    token_hash = getattr(request.state, "token_hash", None)
    if not token_hash:
        return  # require_beta didn't run — nothing to meter

    day = _today()
    limit = settings.CHAT_TURNS_PER_DAY
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT count FROM beta_chat_usage WHERE token_hash = ? AND day = ?",
            (token_hash, day),
        ).fetchone()
        current = row["count"] if row else 0
        if current + 1 > limit:
            raise HTTPException(status_code=429, detail="daily chat allowance exhausted")
        if row:
            conn.execute(
                "UPDATE beta_chat_usage SET count = count + 1 WHERE token_hash = ? AND day = ?",
                (token_hash, day),
            )
        else:
            conn.execute(
                "INSERT INTO beta_chat_usage (token_hash, day, count) VALUES (?, ?, 1)",
                (token_hash, day),
            )
