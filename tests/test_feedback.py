"""Beta feedback capture — Iteration 1 (user-value loop): POST /api/feedback.

TestClient against the real app.main.app, BETA_AUTH forced on via the same
fixture pattern as tests/test_beta_auth.py's beta_client. No LLM calls
anywhere in this file (submit_feedback is a single SQLite upsert).
"""
from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient


def _seed_team(team_id: str = "team-fb-1") -> str:
    from app import db

    db.execute(
        "INSERT INTO teams (team_id, name, use_case, status, recommendation_json, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (team_id, "Test Team", "test use case", "recommend", "{}", "2026-07-21T00:00:00+00:00"),
    )
    return team_id


@pytest.fixture()
def beta_client(tmp_db, monkeypatch):
    """TestClient wired to a temp DB with BETA_AUTH forced on (mirrors
    test_beta_auth.py's beta_client fixture)."""
    from app import settings

    monkeypatch.setattr(settings, "BETA_AUTH", True, raising=False)
    monkeypatch.setattr(settings, "BETA_TOKEN_SALT", "feedback-test-salt", raising=False)
    monkeypatch.setattr(settings, "APP_ADMIN_TOKEN", "feedback-test-admin-token", raising=False)

    from app.main import app as fastapi_app

    return TestClient(fastapi_app)


def _feedback_rows(tmp_db, team_id: str) -> list[tuple]:
    conn = sqlite3.connect(tmp_db)
    try:
        return conn.execute(
            "SELECT token_hash, team_id, verdict, comment FROM generation_feedback WHERE team_id = ?",
            (team_id,),
        ).fetchall()
    finally:
        conn.close()


def test_feedback_401_without_token(beta_client):
    team_id = _seed_team()
    r = beta_client.post("/api/feedback", json={"team_id": team_id, "verdict": "up"})
    assert r.status_code == 401
    assert r.json()["detail"] == "beta token required"


def test_feedback_insert_with_minted_token(beta_client, tmp_db):
    from app import auth

    team_id = _seed_team()
    token = auth.mint("feedback-tester")
    r = beta_client.post(
        "/api/feedback",
        json={"team_id": team_id, "verdict": "up", "comment": "exactly what I needed"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code in (200, 201), r.text

    rows = _feedback_rows(tmp_db, team_id)
    assert len(rows) == 1
    token_hash, tid, verdict, comment = rows[0]
    assert token_hash == auth._hash(token)
    assert tid == team_id
    assert verdict == "up"
    assert comment == "exactly what I needed"


def test_feedback_upsert_not_duplicate_on_second_post(beta_client, tmp_db):
    from app import auth

    team_id = _seed_team()
    token = auth.mint("feedback-tester-2")
    headers = {"Authorization": f"Bearer {token}"}

    r1 = beta_client.post("/api/feedback", json={"team_id": team_id, "verdict": "up"}, headers=headers)
    assert r1.status_code in (200, 201)

    r2 = beta_client.post(
        "/api/feedback",
        json={"team_id": team_id, "verdict": "down", "comment": "actually, no"},
        headers=headers,
    )
    assert r2.status_code in (200, 201)

    rows = _feedback_rows(tmp_db, team_id)
    assert len(rows) == 1, "second POST from the same (token, team) must UPDATE, not insert"
    _, _, verdict, comment = rows[0]
    assert verdict == "down"
    assert comment == "actually, no"


def test_feedback_404_unknown_team(beta_client):
    from app import auth

    token = auth.mint("feedback-tester-3")
    r = beta_client.post(
        "/api/feedback",
        json={"team_id": "does-not-exist", "verdict": "up"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 404
    assert r.json()["detail"] == "team not found"


def test_feedback_422_bad_verdict(beta_client):
    from app import auth

    team_id = _seed_team()
    token = auth.mint("feedback-tester-4")
    r = beta_client.post(
        "/api/feedback",
        json={"team_id": team_id, "verdict": "sideways"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 422


def test_feedback_422_comment_too_long(beta_client):
    from app import auth

    team_id = _seed_team()
    token = auth.mint("feedback-tester-5")
    r = beta_client.post(
        "/api/feedback",
        json={"team_id": team_id, "verdict": "up", "comment": "x" * 2001},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 422


def test_feedback_admin_token_path(beta_client, tmp_db):
    team_id = _seed_team()
    r = beta_client.post(
        "/api/feedback",
        json={"team_id": team_id, "verdict": "up"},
        headers={"Authorization": "Bearer feedback-test-admin-token"},
    )
    assert r.status_code in (200, 201), r.text

    rows = _feedback_rows(tmp_db, team_id)
    assert len(rows) == 1
    assert rows[0][0] == "admin"


def test_feedback_anonymous_token_hash_when_beta_auth_off(tmp_db, monkeypatch):
    """BETA_AUTH off (local dev/test default): require_beta is a no-op and
    never stamps request.state.token_hash, so the endpoint must still work,
    keyed as 'anonymous' rather than crashing on a missing attribute."""
    from app import settings

    monkeypatch.setattr(settings, "BETA_AUTH", False, raising=False)
    from app.main import app as fastapi_app

    client = TestClient(fastapi_app)
    team_id = _seed_team()
    r = client.post("/api/feedback", json={"team_id": team_id, "verdict": "down"})
    assert r.status_code in (200, 201), r.text

    rows = _feedback_rows(tmp_db, team_id)
    assert len(rows) == 1
    assert rows[0][0] == "anonymous"
