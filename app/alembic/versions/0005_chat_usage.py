"""Beta chat-turn allowance — Iteration 2 (user-value loop, try-your-agent chat).

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-21 00:00:00

Adds `beta_chat_usage`: mirrors `beta_token_usage` (one row per
(token_hash, day)), incremented by app.auth.consume_chat_turn on each
successful call to POST /api/team/{tid}/agents/{aid}/chat
(app/routers/team.py). Purely additive — the real DB gains one empty table,
nothing existing is touched.

Like 0002/0003/0004, this delegates to app.db.bootstrap(), which replays the
(now-updated) app/schema.sql verbatim; every CREATE TABLE in it uses
IF NOT EXISTS, so re-running against a DB that already has this table is a
safe no-op. The explicit existence check below is belt-and-suspenders,
mirroring 0004's guard-then-act pattern.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0005"
down_revision: Union[str, Sequence[str], None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create beta_chat_usage by replaying app/schema.sql (guarded)."""
    conn = op.get_bind()
    exists = conn.exec_driver_sql(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='beta_chat_usage'"
    ).fetchone()
    if exists:
        return  # already created (fresh DB via 0001's schema.sql replay)

    from app import db
    db.bootstrap()


def downgrade() -> None:
    """Drop only the table this migration owns (best-effort, IF EXISTS)."""
    op.execute("DROP TABLE IF EXISTS beta_chat_usage;")
