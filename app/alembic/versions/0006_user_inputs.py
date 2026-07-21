"""User-provided real inputs — Iteration 3 (user-value loop, real-inputs intake).

Revision ID: 0006
Revises: 0005
Create Date: 2026-07-21 00:00:00

Adds `teams.user_inputs` (nullable TEXT, JSON [{kind, name, content}]): the
user's own real inputs (price list, policy, past letters...) captured via
PUT /api/team/{team_id}/inputs (app/routers/team.py ->
app.services.team_service.set_user_inputs), baked verbatim into the
generated SKILL.md by app.services.skill_bundle_service.generate_task_overlay
so they flow automatically into agent chat (the bundle IS the system
prompt). Purely additive — no existing column, row, or index is touched, and
the column stays NULL for every team created before this migration.

Unlike 0002-0005 (new tables, safely re-created by replaying app/schema.sql's
`CREATE TABLE IF NOT EXISTS`), this is a new COLUMN on an EXISTING table:
`IF NOT EXISTS` on the table is a no-op once `teams` already exists, so
`app.db.bootstrap()` alone would never add the column to a real (already
-bootstrapped) database. This migration therefore issues an explicit
`ALTER TABLE ... ADD COLUMN`, guarded by a `PRAGMA table_info` check so it
stays idempotent (safe to re-run, and a no-op on a fresh DB that already got
the column straight from the updated schema.sql via 0001's replay).
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0006"
down_revision: Union[str, Sequence[str], None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add teams.user_inputs (nullable TEXT) if it isn't already there."""
    conn = op.get_bind()
    cols = conn.exec_driver_sql("PRAGMA table_info(teams)").fetchall()
    col_names = {row[1] for row in cols}  # row: (cid, name, type, notnull, dflt_value, pk)
    if "user_inputs" in col_names:
        return  # already added (fresh DB via 0001's schema.sql replay)

    op.execute("ALTER TABLE teams ADD COLUMN user_inputs TEXT;")


def downgrade() -> None:
    """SQLite has no DROP COLUMN pre-3.35 in a portable form Alembic can rely
    on here; this migration only ever ADDs a nullable column, so leaving it in
    place on downgrade is the safe, non-destructive choice (mirrors the
    project's other additive migrations, which drop only whole tables they
    own — this migration owns a column, not a table)."""
    pass
