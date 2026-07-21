"""Try-your-agent chat — Iteration 2 (user-value loop):
POST /api/team/{team_id}/agents/{agent_id}/chat.

TestClient against the real app.main.app, BETA_AUTH forced on via the same
fixture pattern as tests/test_beta_auth.py's / tests/test_feedback.py's
beta_client. No real LLM calls anywhere in this file:

  - skill_bundle_service.compose_bundle is monkeypatched to a canned SKILL.md
    string (it otherwise calls config.llm_chat itself while building the base
    skill / task overlay — patching it out keeps this file independent of the
    live framework dataset / role catalog).
  - app.routers.team.llm_chat (the chat-turn call itself) is monkeypatched to
    a canned reply.

The daily-allowance 429 and admin-bypass behavior are exercised at the
app.auth.consume_chat_turn dependency level directly (mirrors
test_beta_auth.py's consume_quota tests) rather than through the HTTP
endpoint, since driving that end-to-end for every allowance case would need a
real team+agent for each request just to reach the (already-passed)
allowance gate.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

_CANNED_SKILL_MD = (
    "---\n"
    "name: test-agent-skill\n"
    "description: A canned test skill. Use when testing.\n"
    "---\n\n"
    "## Role capability reference\n\nDo the task well.\n"
)


def _seed_team(team_id: str = "team-chat-1") -> str:
    from app import db

    db.execute(
        "INSERT INTO teams (team_id, name, use_case, status, recommendation_json, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (team_id, "Test Team", "build a sales dashboard", "recommend", "{}",
         "2026-07-21T00:00:00+00:00"),
    )
    return team_id


@pytest.fixture()
def beta_client(tmp_db, monkeypatch):
    """TestClient wired to a temp DB with BETA_AUTH forced on (mirrors
    test_feedback.py's / test_beta_auth.py's beta_client fixture)."""
    from app import settings

    monkeypatch.setattr(settings, "BETA_AUTH", True, raising=False)
    monkeypatch.setattr(settings, "BETA_TOKEN_SALT", "chat-test-salt", raising=False)
    monkeypatch.setattr(settings, "APP_ADMIN_TOKEN", "chat-test-admin-token", raising=False)

    from app.main import app as fastapi_app

    return TestClient(fastapi_app)


@pytest.fixture()
def stub_compose_bundle(monkeypatch):
    """Replace skill_bundle_service.compose_bundle with a canned SKILL.md, so
    the chat endpoint never touches the live framework dataset / makes an LLM
    call while building the bundle. Returns the call-args recorder list."""
    from app.services import skill_bundle_service

    calls: list[tuple] = []

    def _fake_compose_bundle(team_id, agent_id, use_case, artifacts_needed=None):
        calls.append((team_id, agent_id, use_case))
        return _CANNED_SKILL_MD

    monkeypatch.setattr(skill_bundle_service, "compose_bundle", _fake_compose_bundle)
    return calls


@pytest.fixture()
def stub_llm_chat(monkeypatch):
    """Replace app.routers.team.llm_chat (the chat-turn call) with a canned
    reply. Returns the call-args recorder list (each entry is the `messages`
    list passed in)."""
    from app.routers import team as team_router

    calls: list[list[dict]] = []

    def _fake_llm_chat(messages, temperature=0.4, max_tokens=1500, purpose=None):
        calls.append(messages)
        return "Sure — here is my reply, grounded in the task."

    monkeypatch.setattr(team_router, "llm_chat", _fake_llm_chat)
    return calls


# ── 401: no token ───────────────────────────────────────────────────────────
def test_chat_401_without_token(beta_client):
    team_id = _seed_team()
    r = beta_client.post(
        f"/api/team/{team_id}/agents/some-agent/chat",
        json={"messages": [{"role": "user", "content": "hi"}]},
    )
    assert r.status_code == 401
    assert r.json()["detail"] == "beta token required"


# ── 404: unknown team ───────────────────────────────────────────────────────
def test_chat_404_unknown_team(beta_client):
    from app import auth

    token = auth.mint("chat-tester-404")
    r = beta_client.post(
        "/api/team/does-not-exist/agents/some-agent/chat",
        json={"messages": [{"role": "user", "content": "hi"}]},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 404
    assert r.json()["detail"] == "team not found"


# ── 404: unknown agent on a real team ───────────────────────────────────────
def test_chat_404_unknown_agent(beta_client, monkeypatch):
    from app import auth
    from app.services import skill_bundle_service

    def _raise_not_found(team_id, agent_id, use_case, artifacts_needed=None):
        raise ValueError(f"agent {agent_id!r} not found on team {team_id!r}")

    monkeypatch.setattr(skill_bundle_service, "compose_bundle", _raise_not_found)

    team_id = _seed_team("team-chat-404-agent")
    token = auth.mint("chat-tester-404-agent")
    r = beta_client.post(
        f"/api/team/{team_id}/agents/no-such-agent/chat",
        json={"messages": [{"role": "user", "content": "hi"}]},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 404
    assert r.json()["detail"] == "agent not found"


# ── 422: bad body ────────────────────────────────────────────────────────────
def test_chat_422_empty_messages(beta_client):
    from app import auth

    team_id = _seed_team("team-chat-422-empty")
    token = auth.mint("chat-tester-422-empty")
    r = beta_client.post(
        f"/api/team/{team_id}/agents/some-agent/chat",
        json={"messages": []},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 422
    assert r.json()["detail"] == "messages must be non-empty"


def test_chat_422_last_message_not_user(beta_client):
    from app import auth

    team_id = _seed_team("team-chat-422-lastrole")
    token = auth.mint("chat-tester-422-lastrole")
    r = beta_client.post(
        f"/api/team/{team_id}/agents/some-agent/chat",
        json={"messages": [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello, how can I help?"},
        ]},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 422
    assert r.json()["detail"] == "the last message must be from the user"


def test_chat_422_content_too_long(beta_client):
    from app import auth

    team_id = _seed_team("team-chat-422-toolong")
    token = auth.mint("chat-tester-422-toolong")
    r = beta_client.post(
        f"/api/team/{team_id}/agents/some-agent/chat",
        json={"messages": [{"role": "user", "content": "x" * 4001}]},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 422
    assert "4000" in r.json()["detail"]


def test_chat_422_too_many_messages(beta_client):
    from app import auth

    team_id = _seed_team("team-chat-422-toomany")
    token = auth.mint("chat-tester-422-toomany")
    msgs = []
    for i in range(10):
        msgs.append({"role": "user", "content": f"msg {i}"})
        msgs.append({"role": "assistant", "content": f"reply {i}"})
    msgs.append({"role": "user", "content": "one too many"})  # 21 total
    r = beta_client.post(
        f"/api/team/{team_id}/agents/some-agent/chat",
        json={"messages": msgs},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 422
    assert "20" in r.json()["detail"]


def test_chat_422_bad_role(beta_client):
    from app import auth

    team_id = _seed_team("team-chat-422-badrole")
    token = auth.mint("chat-tester-422-badrole")
    r = beta_client.post(
        f"/api/team/{team_id}/agents/some-agent/chat",
        json={"messages": [{"role": "system", "content": "sneaky"}]},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 422


# ── 200: happy path, canned reply ───────────────────────────────────────────
def test_chat_200_returns_reply_grounded_in_bundle(
    beta_client, stub_compose_bundle, stub_llm_chat
):
    from app import auth

    team_id = _seed_team("team-chat-200")
    token = auth.mint("chat-tester-200")
    r = beta_client.post(
        f"/api/team/{team_id}/agents/agent-1/chat",
        json={"messages": [{"role": "user", "content": "Here's my situation: ..."}]},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["reply"] == "Sure — here is my reply, grounded in the task."

    # compose_bundle was called for THIS team/agent with the team's stored use_case
    assert stub_compose_bundle == [(team_id, "agent-1", "build a sales dashboard")]

    # llm_chat got a system message carrying the composed skill bundle, then
    # the user's turn verbatim
    sent = stub_llm_chat[0]
    assert sent[0]["role"] == "system"
    assert _CANNED_SKILL_MD in sent[0]["content"]
    assert sent[-1] == {"role": "user", "content": "Here's my situation: ..."}


def test_chat_200_multi_turn_history_forwarded(
    beta_client, stub_compose_bundle, stub_llm_chat
):
    from app import auth

    team_id = _seed_team("team-chat-200-multi")
    token = auth.mint("chat-tester-200-multi")
    r = beta_client.post(
        f"/api/team/{team_id}/agents/agent-1/chat",
        json={"messages": [
            {"role": "user", "content": "What do you need from me to start?"},
            {"role": "assistant", "content": "Tell me your target audience."},
            {"role": "user", "content": "Small business owners."},
        ]},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    sent = stub_llm_chat[0]
    # system + 3 forwarded turns
    assert len(sent) == 4
    assert [m["role"] for m in sent] == ["system", "user", "assistant", "user"]


# ── allowance: unit-level (consume_chat_turn dependency directly) ──────────
def test_consume_chat_turn_over_allowance_429s(tmp_db, monkeypatch):
    from app import auth, settings

    monkeypatch.setattr(settings, "BETA_AUTH", True, raising=False)
    monkeypatch.setattr(settings, "CHAT_TURNS_PER_DAY", 2, raising=False)
    monkeypatch.setattr(auth, "_today", lambda: "2026-07-21")

    req = SimpleNamespace(state=SimpleNamespace(is_admin=False, token_hash="tok-chat-1"))
    auth.consume_chat_turn(req)  # 1st: 0 -> 1
    auth.consume_chat_turn(req)  # 2nd: 1 -> 2, still allowed (limit is 2)

    with pytest.raises(HTTPException) as exc:
        auth.consume_chat_turn(req)  # 3rd: would be 3 > 2
    assert exc.value.status_code == 429
    assert exc.value.detail == "daily chat allowance exhausted"

    import sqlite3

    conn = sqlite3.connect(tmp_db)
    try:
        count = conn.execute(
            "SELECT count FROM beta_chat_usage WHERE token_hash = ? AND day = ?",
            ("tok-chat-1", "2026-07-21"),
        ).fetchone()[0]
    finally:
        conn.close()
    assert count == 2  # the rejected 3rd call never wrote its increment


def test_consume_chat_turn_rolls_over_at_utc_day_boundary(tmp_db, monkeypatch):
    from app import auth, settings

    monkeypatch.setattr(settings, "BETA_AUTH", True, raising=False)
    monkeypatch.setattr(settings, "CHAT_TURNS_PER_DAY", 1, raising=False)

    req = SimpleNamespace(state=SimpleNamespace(is_admin=False, token_hash="tok-chat-rollover"))

    monkeypatch.setattr(auth, "_today", lambda: "2026-07-20")
    auth.consume_chat_turn(req)  # day 1: uses up the allowance
    with pytest.raises(HTTPException):
        auth.consume_chat_turn(req)

    monkeypatch.setattr(auth, "_today", lambda: "2026-07-21")
    auth.consume_chat_turn(req)  # new UTC day: allowance resets, this succeeds


def test_consume_chat_turn_admin_bypasses(tmp_db, monkeypatch):
    from app import auth, settings

    monkeypatch.setattr(settings, "BETA_AUTH", True, raising=False)
    monkeypatch.setattr(settings, "CHAT_TURNS_PER_DAY", 1, raising=False)
    req = SimpleNamespace(state=SimpleNamespace(is_admin=True, token_hash="admin"))
    for _ in range(5):
        auth.consume_chat_turn(req)  # never raises for admin


def test_consume_chat_turn_noop_when_beta_auth_off(tmp_db, monkeypatch):
    from app import auth, settings

    monkeypatch.setattr(settings, "BETA_AUTH", False, raising=False)
    req = SimpleNamespace(state=SimpleNamespace())  # no is_admin/token_hash at all
    auth.consume_chat_turn(req)  # must not raise / must not touch missing attrs


def test_consume_chat_turn_noop_without_token_hash(tmp_db, monkeypatch):
    """require_beta didn't run (or ran but stamped nothing) — nothing to meter."""
    from app import auth, settings

    monkeypatch.setattr(settings, "BETA_AUTH", True, raising=False)
    req = SimpleNamespace(state=SimpleNamespace(is_admin=False, token_hash=None))
    auth.consume_chat_turn(req)  # must not raise


# ── admin token path through the real endpoint (superset, unmetered) ───────
def test_chat_admin_token_bypasses_allowance_through_endpoint(
    beta_client, stub_compose_bundle, stub_llm_chat, monkeypatch
):
    from app import settings

    monkeypatch.setattr(settings, "CHAT_TURNS_PER_DAY", 1, raising=False)
    team_id = _seed_team("team-chat-admin")

    for _ in range(3):  # would 429 a beta token at CHAT_TURNS_PER_DAY=1
        r = beta_client.post(
            f"/api/team/{team_id}/agents/agent-1/chat",
            json={"messages": [{"role": "user", "content": "hi"}]},
            headers={"Authorization": "Bearer chat-test-admin-token"},
        )
        assert r.status_code == 200, r.text
