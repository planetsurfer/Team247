"""Beta feedback capture — Iteration 1 (user-value loop).

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-21 00:00:00

Adds `generation_feedback`: one thumbs up/down (+ optional comment) per
(token_hash, team_id), written by POST /api/feedback (app/routers/feedback.py)
which upserts on that pair's UNIQUE constraint rather than inserting a
duplicate. Purely additive — the real DB gains one empty table, nothing
existing is touched.

Like 0001/0002, this delegates to app.db.bootstrap(), which replays the
(now-updated) app/schema.sql verbatim; every CREATE TABLE in it uses
IF NOT EXISTS, so re-running against a DB that already has this table (e.g.
a fresh DB that got it via 0001's replay) is a safe no-op. The explicit
existence check below is belt-and-suspenders (mirrors 0003's "guard, then
act" pattern) on top of that — not strictly required since bootstrap() is
already idempotent, but makes upgrade() read the same way 0003 does rather
than relying solely on schema.sql's IF NOT EXISTS.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0004"
down_revision: Union[str, Sequence[str], None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create generation_feedback by replaying app/schema.sql (guarded)."""
    conn = op.get_bind()
    exists = conn.exec_driver_sql(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='generation_feedback'"
    ).fetchone()
    if exists:
        return  # already created (fresh DB via 0001's schema.sql replay)

    from app import db
    db.bootstrap()


def downgrade() -> None:
    """Drop only the table this migration owns (best-effort, IF EXISTS)."""
    op.execute("DROP TABLE IF EXISTS generation_feedback;")
