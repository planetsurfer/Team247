"""CLI for issuing / listing / revoking closed-beta access tokens.

    python -m app.mint_token <label> [--quota N] [--total N]   # mint a new token
    python -m app.mint_token --list                # list tokens (hash prefixes only)
    python -m app.mint_token --revoke <label>       # deactivate by label or hash prefix

The plaintext token is printed exactly once, right after minting, and is
never written anywhere else — the DB only ever stores its salted SHA-256
hash (app.auth._hash / BETA_TOKEN_SALT). Save it immediately; there is no
way to recover it later.
"""
from __future__ import annotations

import argparse

from app import auth, db


def _cmd_list() -> None:
    rows = auth.list_tokens()
    if not rows:
        print("no tokens minted yet")
        return
    for r in rows:
        status = "active" if r["active"] else "revoked"
        daily = r["daily_quota"] if r["daily_quota"] is not None else "unl"
        total = r["total_quota"] if r["total_quota"] is not None else "unl"
        print(
            f"{r['hash_prefix']}  {r['label']:<24} {status:<8} "
            f"daily={daily} total={r['used']}/{total} "
            f"created={r['created_at']} last_used={r['last_used_at']}"
        )


def _cmd_revoke(label_or_prefix: str) -> None:
    n = auth.revoke(label_or_prefix)
    print(f"revoked {n} token(s) matching '{label_or_prefix}'")


def _cmd_mint(label: str, quota: int | None, total: int | None = None) -> None:
    token = auth.mint(label, daily_quota=quota, total_quota=total)
    print(f"label: {label}")
    print(f"daily quota: {quota if quota is not None else 'unlimited'}"
          f" | total quota: {total if total is not None else 'unlimited'}")
    print(f"token: {token}")
    print("Save this now — it will not be shown again.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage Team247 closed-beta access tokens.")
    parser.add_argument("label", nargs="?", help="human-readable label for a new token")
    parser.add_argument("--quota", type=int, default=None, help="daily generation quota (omit = unlimited)")
    parser.add_argument("--total", type=int, default=None, help="LIFETIME generation cap (omit = unlimited)")
    parser.add_argument("--list", action="store_true", help="list all tokens (hash prefixes only)")
    parser.add_argument("--revoke", metavar="LABEL_OR_HASHPREFIX", help="deactivate matching active token(s)")
    args = parser.parse_args()

    db.bootstrap()  # safe no-op if the schema (incl. beta_tokens) already exists

    if args.list:
        _cmd_list()
        return
    if args.revoke:
        _cmd_revoke(args.revoke)
        return
    if not args.label:
        parser.error("label is required unless --list or --revoke is given")
    _cmd_mint(args.label, args.quota, args.total)


if __name__ == "__main__":
    main()
