"""Beta-token auth tables — PRODUCTION_ROADMAP.md P0 #1 (closed-beta auth).

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-20 00:00:00

Adds `beta_tokens` (salted-hash token registry) and `beta_token_usage` (daily
quota counters). Purely additive — the real DB gains two empty tables, nothing
existing is touched. Like 0001, this delegates to app.db.bootstrap(), which
replays the (now-updated) app/schema.sql verbatim; every statement in it uses
`IF NOT EXISTS`, so re-running against a DB that already has 0001's tables is
a safe no-op for them and only creates the two new tables here.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: Union[str, Sequence[str], None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create beta_tokens + beta_token_usage by replaying app/schema.sql.

    Delegates to app.db.bootstrap() exactly like 0001 (schema.sql is the single
    source of truth, IF NOT EXISTS makes this idempotent). Existing tables/rows
    are untouched; only the two new beta-auth tables are added.
    """
    from app import db
    db.bootstrap()


def downgrade() -> None:
    """Drop only the two tables this migration owns (best-effort, IF EXISTS)."""
    op.execute("DROP TABLE IF EXISTS beta_token_usage;")
    op.execute("DROP TABLE IF EXISTS beta_tokens;")
