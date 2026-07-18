"""Alembic environment for the AgentProof production app.

Runs migrations against the SQLite database selected by `app.settings.APP_DB_PATH`
(the `sqlalchemy.url` in alembic.ini is overridden here). Supports both the
offline (--sql) and online modes. `compare_type=True` keeps autogenerate honest
about type changes; `literal_binds=True` makes offline SQL directly executable.
"""
from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# Resolve the app package. `app/` lives under the repo root; when Alembic is
# launched from the repo root (the documented workflow: `PYTHONPATH=. alembic …`)
# `from app import settings` works directly. When launched from elsewhere
# (e.g. `cd app/alembic && alembic …`), fall back to adding the repo root.
try:
    from app import settings  # type: ignore
except ImportError:  # pragma: no cover - dev convenience
    import pathlib
    _repo_root = pathlib.Path(__file__).resolve().parents[2]
    import sys
    if str(_repo_root) not in sys.path:
        sys.path.insert(0, str(_repo_root))
    from app import settings  # type: ignore

# Alembic config object (built from alembic.ini + CLI).
config = context.config

# Override the configured URL with the settings-derived path so the env var
# (APP_DB_PATH) always wins, regardless of what alembic.ini says.
config.set_main_option("sqlalchemy.url", f"sqlite:///{settings.APP_DB_PATH}")

# Configure Python logging from alembic.ini if present.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# This app's schema is raw SQL (schema.sql), not a SQLAlchemy MetaData. We do
# not use autogenerate against models; migrations are hand-written revisions
# (the initial migration executes schema.sql). `compare_type=True` is still
# passed so future autogenerate runs are type-aware.
target_metadata = None


def run_migrations_offline() -> None:
    """Generate SQL to stdout (no DB connection)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        literal_binds=True,
        compare_type=True,
        dialect_opts={"paramstyle": "named"},
        transaction_per_migration=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against the live DB."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            compare_type=True,
            transaction_per_migration=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
