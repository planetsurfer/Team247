"""Initial schema — executes app/schema.sql (all tables, indexes, FTS5, triggers).

Revision ID: 0001
Revises:
Create Date: 2026-07-19 00:00:00

Schema is raw SQL (no SQLAlchemy MetaData); this migration reads the canonical
schema.sql and replays it verbatim. schema.sql uses `IF NOT EXISTS` throughout,
so `upgrade` is idempotent and safe to re-run. The seed (data) is handled
separately by `app.seed_catalog` and is NOT a migration.
"""
from __future__ import annotations

import pathlib
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# All schema objects in drop order (reverse of creation). Triggers and virtual
# FTS5 tables come first (they reference base tables), then base tables. Indexes
# are dropped implicitly with their tables. Each DROP uses IF EXISTS so
# downgrade is safe on partial installs.
_DROP_OBJECTS_SQL: tuple[str, ...] = (
    # --- FTS5 triggers (content-sync) ---
    "DROP TRIGGER IF EXISTS roles_au;",
    "DROP TRIGGER IF EXISTS roles_ad;",
    "DROP TRIGGER IF EXISTS roles_ai;",
    "DROP TRIGGER IF EXISTS role_skills_au;",
    "DROP TRIGGER IF EXISTS role_skills_ad;",
    "DROP TRIGGER IF EXISTS role_skills_ai;",
    # --- FTS5 virtual tables ---
    "DROP TABLE IF EXISTS roles_fts;",
    "DROP TABLE IF EXISTS role_skills_fts;",
    # --- Base tables (children before parents) ---
    "DROP TABLE IF EXISTS intake_messages;",
    "DROP TABLE IF EXISTS intake_sessions;",
    "DROP TABLE IF EXISTS verify_runs;",
    "DROP TABLE IF EXISTS team_spec_versions;",
    "DROP TABLE IF EXISTS team_handoffs;",
    "DROP TABLE IF EXISTS team_agents;",
    "DROP TABLE IF EXISTS teams;",
    "DROP TABLE IF EXISTS card_learned_guidance;",
    "DROP TABLE IF EXISTS card_battery_items;",
    "DROP TABLE IF EXISTS cards;",
    "DROP TABLE IF EXISTS ka_items;",
    "DROP TABLE IF EXISTS role_skills;",
    "DROP TABLE IF EXISTS roles;",
)


def _schema_sql() -> str:
    """Read the canonical schema.sql sibling of the alembic/ directory."""
    schema_path = pathlib.Path(__file__).resolve().parents[2] / "schema.sql"
    return schema_path.read_text(encoding="utf-8")


def upgrade() -> None:
    """Create all schema objects by executing app/schema.sql verbatim.

    Delegates to app.db.bootstrap(), which uses sqlite3.executescript on its
    own connection — necessary because schema.sql contains multi-statement
    blocks and CREATE TRIGGER … BEGIN…END; bodies that Alembic's single-statement
    op.execute cannot parse. IF NOT EXISTS makes this idempotent. alembic then
    stamps version 0001 on its own connection.
    """
    from app import db
    db.bootstrap()


def downgrade() -> None:
    """Drop every object the migration may have created (best-effort, IF EXISTS).

    DROP statements are single-statement (no BEGIN…END bodies), so op.execute
    per statement is safe here.
    """
    op.execute("PRAGMA foreign_keys = OFF;")
    try:
        for stmt in _DROP_OBJECTS_SQL:
            op.execute(stmt)
    finally:
        op.execute("PRAGMA foreign_keys = ON;")
