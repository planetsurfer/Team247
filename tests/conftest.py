"""Pytest configuration shared across the AgentProof suite.

Puts the repo root on ``sys.path`` (so ``import app.…`` and the flat root
modules like ``framework``/``config`` resolve the same way the server sees
them under ``PYTHONPATH=.``) and provides two DB fixtures:

* ``tmp_db``    — a temp SQLite file with ``app/schema.sql`` executed, and
  ``settings.APP_DB_PATH`` monkeypatched to point at it.
* ``seeded_db`` — ``tmp_db`` plus (if importable) ``app.seed_catalog`` run
  against it. Skips the whole suite that needs it when seeding isn't
  implemented yet.

The fixtures are written so that the suite runs green today (everything
skips) and starts asserting real behaviour the moment the staged
implementations land.
"""
from __future__ import annotations

import pathlib
import sqlite3
import sys

import pytest

# ── repo root on sys.path (matches `PYTHONPATH=. uvicorn app.main:app`) ──────
REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

SCHEMA_PATH = REPO_ROOT / "app" / "schema.sql"


def _apply_schema(db_path: str) -> None:
    """Execute app/schema.sql (DDL + FTS5 + triggers) against a fresh DB."""
    sql = SCHEMA_PATH.read_text()
    # autocommit-style DDL is fine; executescript wraps it in a transaction.
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(sql)
        conn.commit()
    finally:
        conn.close()


@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    """A temp SQLite file with the production schema applied.

    Also redirects ``app.settings.APP_DB_PATH`` at the temp file so any
    code that reads the setting (rather than the connection we hand it)
    lands on the throwaway DB. Silently no-ops if ``app.settings`` is not
    importable yet (Stage 0 not landed).
    """
    db_path = tmp_path / "agentproof_test.db"
    _apply_schema(str(db_path))

    try:
        from app import settings  # noqa: WPS433 (intentional local import)
    except Exception:
        settings = None  # type: ignore[assignment]

    if settings is not None:
        monkeypatch.setattr(settings, "APP_DB_PATH", str(db_path), raising=False)
        # Some code reads via os.environ; mirror it for completeness.
        monkeypatch.setenv("APP_DB_PATH", str(db_path))

    yield str(db_path)


def _resolve_seed_entrypoint(seed_module):
    """Return the callable that drives a full idempotent seed, or None.

    The plan names ``app.seed_catalog`` and says it's runnable via
    ``python -m app.seed_catalog``; we accept any of the conventional
    entrypoint names so the test keeps working as the module crystalizes.
    """
    for name in ("run", "seed", "seed_all", "main"):
        fn = getattr(seed_module, name, None)
        if callable(fn):
            return fn
    return None


@pytest.fixture()
def seeded_db(tmp_db):
    """``tmp_db`` after a seed run. Skips if seed_catalog isn't landed."""
    seed_module = pytest.importorskip("app.seed_catalog")
    runner = _resolve_seed_entrypoint(seed_module)
    if runner is None:
        pytest.skip(
            "app.seed_catalog imported but exposes no run/seed/main entrypoint "
            "yet (Stage 0 seed not implemented)"
        )
    runner()
    yield tmp_db
