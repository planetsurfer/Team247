"""CLI for inspecting beta feedback captured by POST /api/feedback.

    python -m app.feedback --list [--recent N]   # rows newest-first
    python -m app.feedback --stats                # up/down counts + recent comments

Reads `generation_feedback` (app/schema.sql; app/routers/feedback.py writes
it, one upserted row per (token_hash, team_id)). Token hashes are shown as an
8-char prefix only — never the full hash, never a plaintext token — matching
`app.mint_token.list_tokens()`'s convention. 'admin' and 'anonymous' are
literal token_hash values (not real hashes): the admin token and BETA_AUTH=off
callers respectively (see app/routers/feedback.py).
"""
from __future__ import annotations

import argparse

from app import db


def _label_for(token_hash: str) -> str:
    """Best-effort label lookup via beta_tokens; 'admin'/'anonymous' pass
    through as-is (they have no beta_tokens row), unminted/revoked hashes
    fall back to '-'."""
    if token_hash in ("admin", "anonymous"):
        return token_hash
    row = db.query("SELECT label FROM beta_tokens WHERE token_hash = ?", (token_hash,), one=True)
    return row["label"] if row else "-"


def _truncate(s: str | None, n: int = 80) -> str:
    if not s:
        return ""
    s = s.replace("\n", " ").strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def _cmd_list(recent: int | None) -> None:
    sql = "SELECT * FROM generation_feedback ORDER BY created_at DESC"
    params: tuple = ()
    if recent:
        sql += " LIMIT ?"
        params = (recent,)
    rows = db.query(sql, params)
    if not rows:
        print("no feedback yet")
        return
    for r in rows:
        prefix = r["token_hash"][:8]
        label = _label_for(r["token_hash"])
        team_short = r["team_id"][:8]
        print(
            f"{prefix}  {label:<24} team={team_short}  {r['verdict']:<5} "
            f"{_truncate(r['comment']):<80}  created={r['created_at']}"
        )


def _cmd_stats() -> None:
    rows = db.query("SELECT verdict, COUNT(*) AS n FROM generation_feedback GROUP BY verdict")
    counts = {r["verdict"]: r["n"] for r in rows}
    up, down = counts.get("up", 0), counts.get("down", 0)
    print(f"up={up}  down={down}  total={up + down}")

    top = db.query(
        "SELECT verdict, comment, created_at FROM generation_feedback "
        "WHERE comment IS NOT NULL AND comment != '' ORDER BY created_at DESC LIMIT 10"
    )
    if top:
        print("\nrecent comments:")
        for r in top:
            print(f"  [{r['verdict']}] {_truncate(r['comment'])}  ({r['created_at']})")


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect Team247 beta feedback.")
    parser.add_argument("--list", action="store_true", help="list feedback rows newest-first")
    parser.add_argument("--recent", type=int, default=None, metavar="N", help="limit --list to the N most recent rows")
    parser.add_argument("--stats", action="store_true", help="print up/down counts + recent comments")
    args = parser.parse_args()

    db.bootstrap()  # safe no-op if the schema (incl. generation_feedback) already exists

    if args.stats:
        _cmd_stats()
        return
    if args.list:
        _cmd_list(args.recent)
        return
    parser.error("--list or --stats is required")


if __name__ == "__main__":
    main()
