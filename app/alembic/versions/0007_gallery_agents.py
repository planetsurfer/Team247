"""Starter gallery — Iteration 4 (user-value loop).

Revision ID: 0007
Revises: 0006
Create Date: 2026-07-21 00:00:00

Adds `gallery_agents`: one row per pre-built starter-gallery archetype
(collections-chaser, contract-reviewer, quotation-writer,
onboarding-coordinator, campaign-planner), seeded by
`python -m app.build_gallery` (app/build_gallery.py) via the same
recommend -> wire -> compose_bundle pipeline a real user's first task runs.
Read by GET /api/gallery (open, metadata only) and GET /api/gallery/{slug}
(beta-gated, full row incl. bundle_md) — app/routers/gallery.py. Purely
additive — the real DB gains one empty table, nothing existing is touched.

Like 0004/0005, this delegates to app.db.bootstrap(), which replays the
(now-updated) app/schema.sql verbatim; every CREATE TABLE in it uses
IF NOT EXISTS, so re-running against a DB that already has this table is a
safe no-op. The explicit existence check below is belt-and-suspenders,
mirroring 0004/0005's guard-then-act pattern.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0007"
down_revision: Union[str, Sequence[str], None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create gallery_agents by replaying app/schema.sql (guarded)."""
    conn = op.get_bind()
    exists = conn.exec_driver_sql(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='gallery_agents'"
    ).fetchone()
    if exists:
        return  # already created (fresh DB via 0001's schema.sql replay)

    from app import db
    db.bootstrap()


def downgrade() -> None:
    """Drop only the table this migration owns (best-effort, IF EXISTS)."""
    op.execute("DROP TABLE IF EXISTS gallery_agents;")
