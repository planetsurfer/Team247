"""Beta-token auth — PRODUCTION_ROADMAP.md P0 #1 (closed-beta, app layer only).

Two tiers:

* **Unit** — `app.auth` functions called directly (mint/hash/revoke round-trip,
  `require_beta`/`consume_quota` dependency logic incl. daily rollover) against
  the `tmp_db` fixture. No HTTP, no LLM.
* **API** — `TestClient` against the real `app.main.app`, verifying every
  gated endpoint 401s without a token, `/api/auth/status` reflects a minted /
  revoked token, and the admin token remains accepted (superset). Endpoints
  hit here are ones whose 401 short-circuits before any LLM call (require_beta
  runs before the view body), or whose view body is itself LLM-free
  (`intake_service.start()` — see its docstring: fixed seed questions, no
  LLM), so this file makes zero real LLM calls.

The quota-429 behavior is exercised at the dependency level directly
(`consume_quota`, per the task's explicit escape hatch) rather than through
`/api/team/recommend`, since driving that endpoint end-to-end would require a
real LLM call for every request just to reach the (already-passed) quota gate.
"""
from __future__ import annotations

import sqlite3
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient


# ── unit: mint / hash / revoke / list round-trip ────────────────────────────
def test_mint_produces_prefixed_plaintext_and_stores_only_a_hash(tmp_db, monkeypatch):
    from app import auth, settings

    monkeypatch.setattr(settings, "BETA_TOKEN_SALT", "unit-test-salt", raising=False)

    token = auth.mint("acme-pilot", daily_quota=5)

    assert token.startswith("t247_")
    assert len(token) > len("t247_") + 20  # secrets.token_urlsafe(24) is long

    conn = sqlite3.connect(tmp_db)
    try:
        rows = conn.execute("SELECT token_hash, label, active, daily_quota FROM beta_tokens").fetchall()
    finally:
        conn.close()
    assert len(rows) == 1
    stored_hash, label, active, quota = rows[0]
    assert label == "acme-pilot"
    assert active == 1
    assert quota == 5
    # the plaintext token is never in the row — only its salted hash is
    assert stored_hash != token
    assert stored_hash == auth._hash(token)


def test_hash_is_salted_different_salts_diverge(monkeypatch):
    from app import auth, settings

    monkeypatch.setattr(settings, "BETA_TOKEN_SALT", "salt-a", raising=False)
    h_a = auth._hash("same-plaintext")
    monkeypatch.setattr(settings, "BETA_TOKEN_SALT", "salt-b", raising=False)
    h_b = auth._hash("same-plaintext")
    assert h_a != h_b


def test_revoke_by_label_deactivates_and_401s(tmp_db, monkeypatch):
    from app import auth, settings

    monkeypatch.setattr(settings, "BETA_AUTH", True, raising=False)
    monkeypatch.setattr(settings, "BETA_TOKEN_SALT", "unit-test-salt", raising=False)
    monkeypatch.setattr(settings, "APP_ADMIN_TOKEN", "", raising=False)

    token = auth.mint("revoke-me")
    n = auth.revoke("revoke-me")
    assert n == 1

    req = SimpleNamespace(headers={"authorization": f"Bearer {token}"}, state=SimpleNamespace())
    with pytest.raises(HTTPException) as exc:
        auth.require_beta(req)
    assert exc.value.status_code == 401
    assert exc.value.detail == "beta token required"


def test_revoke_by_hash_prefix(tmp_db, monkeypatch):
    from app import auth, settings

    monkeypatch.setattr(settings, "BETA_TOKEN_SALT", "unit-test-salt", raising=False)
    token = auth.mint("prefix-target")
    prefix = auth._hash(token)[:8]
    assert auth.revoke(prefix) == 1
    assert all(not r["active"] for r in auth.list_tokens() if r["label"] == "prefix-target")


def test_list_tokens_never_exposes_more_than_an_8char_prefix(tmp_db, monkeypatch):
    from app import auth, settings

    monkeypatch.setattr(settings, "BETA_TOKEN_SALT", "unit-test-salt", raising=False)
    token = auth.mint("listed")
    full_hash = auth._hash(token)

    rows = auth.list_tokens()
    row = next(r for r in rows if r["label"] == "listed")
    assert row["hash_prefix"] == full_hash[:8]
    assert len(row["hash_prefix"]) == 8
    # nothing in the row is the plaintext or the full hash
    assert token not in str(row.values())
    assert full_hash not in str(row.values())


# ── unit: require_beta dependency ────────────────────────────────────────────
def test_require_beta_noop_when_beta_auth_off(tmp_db, monkeypatch):
    from app import auth, settings

    monkeypatch.setattr(settings, "BETA_AUTH", False, raising=False)
    req = SimpleNamespace(headers={}, state=SimpleNamespace())
    auth.require_beta(req)  # must not raise, must not touch request.state
    assert not hasattr(req.state, "token_hash")


def test_require_beta_missing_header_401(tmp_db, monkeypatch):
    from app import auth, settings

    monkeypatch.setattr(settings, "BETA_AUTH", True, raising=False)
    req = SimpleNamespace(headers={}, state=SimpleNamespace())
    with pytest.raises(HTTPException) as exc:
        auth.require_beta(req)
    assert exc.value.status_code == 401
    assert exc.value.detail == "beta token required"


def test_require_beta_valid_token_stamps_state_and_last_used(tmp_db, monkeypatch):
    from app import auth, settings

    monkeypatch.setattr(settings, "BETA_AUTH", True, raising=False)
    monkeypatch.setattr(settings, "BETA_TOKEN_SALT", "unit-test-salt", raising=False)
    monkeypatch.setattr(settings, "APP_ADMIN_TOKEN", "", raising=False)

    token = auth.mint("valid-caller", daily_quota=7)
    req = SimpleNamespace(headers={"authorization": f"Bearer {token}"}, state=SimpleNamespace())
    auth.require_beta(req)

    assert req.state.is_admin is False
    assert req.state.token_hash == auth._hash(token)
    assert req.state.daily_quota == 7

    row = next(r for r in auth.list_tokens() if r["label"] == "valid-caller")
    assert row["last_used_at"] is not None


def test_require_beta_admin_token_is_superset(tmp_db, monkeypatch):
    from app import auth, settings

    monkeypatch.setattr(settings, "BETA_AUTH", True, raising=False)
    monkeypatch.setattr(settings, "APP_ADMIN_TOKEN", "the-admin-secret", raising=False)

    req = SimpleNamespace(headers={"authorization": "Bearer the-admin-secret"}, state=SimpleNamespace())
    auth.require_beta(req)
    assert req.state.is_admin is True


def test_require_beta_unknown_token_401(tmp_db, monkeypatch):
    from app import auth, settings

    monkeypatch.setattr(settings, "BETA_AUTH", True, raising=False)
    monkeypatch.setattr(settings, "BETA_TOKEN_SALT", "unit-test-salt", raising=False)
    monkeypatch.setattr(settings, "APP_ADMIN_TOKEN", "", raising=False)

    req = SimpleNamespace(headers={"authorization": "Bearer t247_totally-made-up"}, state=SimpleNamespace())
    with pytest.raises(HTTPException) as exc:
        auth.require_beta(req)
    assert exc.value.status_code == 401


# ── unit: consume_quota dependency (incl. daily rollover) ──────────────────
def test_consume_quota_second_call_over_quota_429s(tmp_db, monkeypatch):
    from app import auth, settings

    monkeypatch.setattr(settings, "BETA_AUTH", True, raising=False)
    monkeypatch.setattr(auth, "_today", lambda: "2026-07-20")

    req = SimpleNamespace(state=SimpleNamespace(is_admin=False, token_hash="tok-quota-1", daily_quota=1))
    auth.consume_quota(req)  # 1st call: 0 -> 1, allowed

    with pytest.raises(HTTPException) as exc:
        auth.consume_quota(req)  # 2nd call: would be 2 > quota(1)
    assert exc.value.status_code == 429
    assert exc.value.detail == "daily quota exceeded"

    conn = sqlite3.connect(tmp_db)
    try:
        count = conn.execute(
            "SELECT count FROM beta_token_usage WHERE token_hash = ? AND day = ?",
            ("tok-quota-1", "2026-07-20"),
        ).fetchone()[0]
    finally:
        conn.close()
    assert count == 1  # the rejected call never wrote its increment


def test_consume_quota_rolls_over_at_utc_day_boundary(tmp_db, monkeypatch):
    from app import auth, settings

    monkeypatch.setattr(settings, "BETA_AUTH", True, raising=False)
    req = SimpleNamespace(state=SimpleNamespace(is_admin=False, token_hash="tok-rollover", daily_quota=1))

    monkeypatch.setattr(auth, "_today", lambda: "2026-07-20")
    auth.consume_quota(req)  # day 1: uses up the quota
    with pytest.raises(HTTPException):
        auth.consume_quota(req)

    monkeypatch.setattr(auth, "_today", lambda: "2026-07-21")
    auth.consume_quota(req)  # new UTC day: quota resets, this succeeds


def test_consume_quota_admin_bypasses(tmp_db, monkeypatch):
    from app import auth, settings

    monkeypatch.setattr(settings, "BETA_AUTH", True, raising=False)
    req = SimpleNamespace(state=SimpleNamespace(is_admin=True, token_hash="admin", daily_quota=None))
    for _ in range(5):
        auth.consume_quota(req)  # never raises for admin


def test_consume_quota_unlimited_when_quota_is_none(tmp_db, monkeypatch):
    from app import auth, settings

    monkeypatch.setattr(settings, "BETA_AUTH", True, raising=False)
    req = SimpleNamespace(state=SimpleNamespace(is_admin=False, token_hash="tok-unlimited", daily_quota=None))
    for _ in range(5):
        auth.consume_quota(req)  # never raises — no quota configured


def test_consume_quota_noop_when_beta_auth_off(tmp_db, monkeypatch):
    from app import auth, settings

    monkeypatch.setattr(settings, "BETA_AUTH", False, raising=False)
    req = SimpleNamespace(state=SimpleNamespace())  # no is_admin/token_hash at all
    auth.consume_quota(req)  # must not raise / must not touch missing attrs


# ── API: TestClient against the real app, BETA_AUTH forced on ──────────────
@pytest.fixture()
def beta_client(tmp_db, monkeypatch):
    """TestClient wired to a temp DB with BETA_AUTH forced on. tmp_db already
    monkeypatches settings.APP_DB_PATH; app.db.connect() re-reads it per call,
    so this is safe regardless of when app.main was first imported.
    """
    from app import settings

    monkeypatch.setattr(settings, "BETA_AUTH", True, raising=False)
    monkeypatch.setattr(settings, "BETA_TOKEN_SALT", "api-test-salt", raising=False)
    monkeypatch.setattr(settings, "APP_ADMIN_TOKEN", "api-test-admin-token", raising=False)

    from app.main import app as fastapi_app

    return TestClient(fastapi_app)


@pytest.mark.parametrize(
    "method,path,json_body",
    [
        ("POST", "/api/team/recommend", {"use_case": "build a sales dashboard"}),
        ("POST", "/api/team/some-fake-team/wire", {}),
        ("POST", "/api/team/some-fake-team/render", None),
        ("POST", "/api/team/some-fake-team/skill-bundles", {}),
        ("POST", "/api/intake/start", None),
        ("POST", "/api/intake/some-fake-session/answer", {"answers": ["x"]}),
        ("POST", "/api/catalog/1/distill", None),
    ],
)
def test_gated_endpoints_401_without_token(beta_client, method, path, json_body):
    r = beta_client.request(method, path, json=json_body)
    assert r.status_code == 401, f"{method} {path} -> {r.status_code}: {r.text}"
    assert r.json()["detail"] == "beta token required"


def test_verify_endpoints_stay_admin_only_not_relaxed_by_beta_token(beta_client, tmp_db, monkeypatch):
    """A valid (non-admin) beta token must NOT unlock /verify — it still needs
    the admin token, exactly as before beta auth existed."""
    from app import auth

    token = auth.mint("verify-cant-touch-this")
    r = beta_client.post(
        "/api/team/some-fake-team/agents/some-fake-agent/verify",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 401
    assert r.json()["detail"] == "admin token required"

    r = beta_client.get(
        "/api/team/some-fake-team/agents/some-fake-agent/verify",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 401
    assert r.json()["detail"] == "admin token required"

    # the admin token itself still works for verify (unchanged behavior)
    r = beta_client.get(
        "/api/team/some-fake-team/agents/some-fake-agent/verify",
        headers={"Authorization": "Bearer api-test-admin-token"},
    )
    assert r.status_code != 401


def test_auth_status_flips_with_minted_and_revoked_token(beta_client):
    from app import auth

    r = beta_client.get("/api/auth/status")
    assert r.json() == {"beta_auth": True, "authenticated": False}

    token = auth.mint("status-check")
    r = beta_client.get("/api/auth/status", headers={"Authorization": f"Bearer {token}"})
    assert r.json() == {"beta_auth": True, "authenticated": True}

    auth.revoke("status-check")
    r = beta_client.get("/api/auth/status", headers={"Authorization": f"Bearer {token}"})
    assert r.json() == {"beta_auth": True, "authenticated": False}

    # admin token also authenticates
    r = beta_client.get("/api/auth/status", headers={"Authorization": "Bearer api-test-admin-token"})
    assert r.json() == {"beta_auth": True, "authenticated": True}


def test_valid_token_passes_through_to_an_llm_free_view(beta_client):
    """intake_service.start() only inserts the 3 fixed seed questions — no LLM
    call (see its docstring) — so this proves require_beta really lets a good
    token continue into the view, not just that it blocks bad ones."""
    from app import auth

    token = auth.mint("passthrough-check")
    r = beta_client.post("/api/intake/start", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    body = r.json()
    assert "session_id" in body and len(body["questions"]) == 3


def test_auth_status_open_and_cheap_when_beta_auth_off(tmp_db, monkeypatch):
    """With BETA_AUTH off (the local/test default), /api/auth/status reports
    beta_auth False and authenticated True unconditionally — no gate."""
    from app import settings

    monkeypatch.setattr(settings, "BETA_AUTH", False, raising=False)
    from app.main import app as fastapi_app

    r = TestClient(fastapi_app).get("/api/auth/status")
    assert r.json() == {"beta_auth": False, "authenticated": True}


# ── migration: applies cleanly to a throwaway copy, never the real DB ──────
def test_alembic_upgrade_head_applies_cleanly_to_a_temp_db(tmp_path):
    """Runs the real `alembic upgrade head` (0001 -> 0002) against a brand-new
    temp sqlite file — never data/agentproof.db. Confirms the additive
    migration lands both new tables with the right columns.
    """
    import os
    import pathlib
    import subprocess

    repo_root = pathlib.Path(__file__).resolve().parent.parent
    temp_db = tmp_path / "alembic_head_check.db"

    env = dict(os.environ)
    env["PYTHONPATH"] = str(repo_root)
    env["APP_DB_PATH"] = str(temp_db)

    result = subprocess.run(
        ["alembic", "-c", "app/alembic/alembic.ini", "upgrade", "head"],
        cwd=str(repo_root),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, f"alembic upgrade head failed:\n{result.stdout}\n{result.stderr}"
    assert temp_db.exists()

    conn = sqlite3.connect(str(temp_db))
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"beta_tokens", "beta_token_usage", "roles", "teams"} <= tables

        cols = {r[1] for r in conn.execute("PRAGMA table_info(beta_tokens)")}
        assert cols == {"token_hash", "label", "active", "created_at", "last_used_at",
                        "daily_quota", "total_quota"}

        cols = {r[1] for r in conn.execute("PRAGMA table_info(beta_token_usage)")}
        assert cols == {"token_hash", "day", "count"}
    finally:
        conn.close()
