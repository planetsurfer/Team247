"""Add beta_tokens.total_quota — lifetime generation cap (NULL = unlimited).

Complements the per-day daily_quota: total_quota caps a token's generations
across its whole lifetime (SUM over beta_token_usage), for closed-beta tokens
issued as "N generations total".
"""
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Guard: a FRESH db gets total_quota via 0001's schema.sql replay (the
    # bootstrap pattern), so ADD COLUMN must be conditional — only pre-0003
    # databases created before the column existed need the ALTER.
    conn = op.get_bind()
    cols = [r[1] for r in conn.exec_driver_sql("PRAGMA table_info(beta_tokens)")]
    if "total_quota" not in cols:
        op.execute("ALTER TABLE beta_tokens ADD COLUMN total_quota INTEGER")


def downgrade() -> None:
    op.execute("ALTER TABLE beta_tokens DROP COLUMN total_quota")
