"""SQLite data access for AgentProof. Local sqlite3, sync (FastAPI runs `def` handlers in its
threadpool). Reuses app.settings + app.logging_setup. The schema lives in app/schema.sql and
is executed idempotently by bootstrap() (and by the Alembic initial migration).

Public API (used by routers/services/seed_catalog/main):
  connect()        -> sqlite3.Connection (row_factory=Row, foreign_keys ON)
  get_conn()       -> context manager (commits on exit, closes always)
  bootstrap()      -> execute schema.sql (idempotent; creates tables + FTS5 + triggers)
  query(sql, params=(), one=False) -> list[dict] | dict | None   (logs queries >100ms)
  execute(sql, params=()) -> lastrowid
  executemany(sql, params_seq) -> rowcount
"""
import sqlite3
import time
import pathlib
from contextlib import contextmanager

from app import settings
from app.logging_setup import log_slow_query

SCHEMA_PATH = pathlib.Path(__file__).resolve().parent / "schema.sql"


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(settings.APP_DB_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def get_conn():
    conn = connect()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def bootstrap():
    """Create all tables / FTS5 virtual tables / triggers from schema.sql. Idempotent."""
    with get_conn() as conn:
        conn.executescript(SCHEMA_PATH.read_text())


def query(sql, params=(), one=False):
    t0 = time.perf_counter()
    with get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    ms = (time.perf_counter() - t0) * 1000
    if ms > 100:
        log_slow_query(sql, ms)
    if one:
        return dict(rows[0]) if rows else None
    return [dict(r) for r in rows]


def execute(sql, params=()):
    with get_conn() as conn:
        cur = conn.execute(sql, params)
        conn.commit()
        return cur.lastrowid


def executemany(sql, params_seq):
    with get_conn() as conn:
        cur = conn.executemany(sql, list(params_seq))
        conn.commit()
        return cur.rowcount
