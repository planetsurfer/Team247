"""Seed idempotency: running ``app.seed_catalog`` twice changes no row counts.

Covers PRODUCTION_APP_PLAN.md → Seeding ("Seed is idempotent") and the
Stage 0 verify step ("``--idempotent`` re-run = no change").

Skips per-test until ``app.seed_catalog`` is importable and exposes a
runnable entrypoint (``run``/``seed``/``main``); the ``seeded_db`` fixture
handles the import-skip, and this test additionally guards the second
re-run.
"""
from __future__ import annotations

import sqlite3

import pytest

# Tables whose cardinality must be stable across a re-seed.
_IDEMPOTENT_TABLES = ("roles", "role_skills", "ka_items")


def _count(db_path: str, table: str) -> int:
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    finally:
        conn.close()


def test_seed_twice_changes_nothing(seeded_db):
    """After a seed, counts are non-zero and stable across a second seed."""
    seed_catalog = pytest.importorskip(
        "app.seed_catalog", reason="Stage 0 seed module not implemented yet"
    )

    runner = (
        getattr(seed_catalog, "run", None)
        or getattr(seed_catalog, "seed", None)
        or getattr(seed_catalog, "main", None)
    )
    if not callable(runner):
        pytest.skip("app.seed_catalog has no run/seed/main entrypoint yet")

    before = {t: _count(seeded_db, t) for t in _IDEMPOTENT_TABLES}

    # Seed actually ran for the first time inside ``seeded_db``; assert it
    # produced rows (a vacuous green on an empty seed would hide regressions).
    assert before["roles"] > 0, "first seed produced zero roles"
    assert before["role_skills"] > 0, "first seed produced zero role_skills"
    assert before["ka_items"] > 0, "first seed produced zero ka_items"

    # Re-run: idempotent — every count must be byte-for-byte unchanged.
    runner()

    after = {t: _count(seeded_db, t) for t in _IDEMPOTENT_TABLES}
    assert after == before, f"re-seed changed row counts: {before} -> {after}"
