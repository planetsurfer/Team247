"""Ops-question generation — Iteration 1 (OPERATIONS-INTAKE loop):
llm_contracts.generate_ops_questions / _validate_ops_questions, and
POST /api/team/{team_id}/ops-questions.

TestClient against the real app.main.app, BETA_AUTH forced on via the same
fixture pattern as tests/test_user_inputs.py's / tests/test_agent_chat.py's
beta_client. No real LLM calls: the endpoint tests monkeypatch
app.llm_contracts.generate_ops_questions directly (same pattern as
test_agent_chat.py's stub_llm_chat).
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


# ── seeding helpers (mirrors tests/test_user_inputs.py) ─────────────────────
def _seed_role(role: str = "Test Sales Role") -> int:
    from app import db

    return db.execute(
        "INSERT INTO roles(role, sector, track, seeded_at) VALUES (?, ?, ?, ?)",
        (role, "Test Sector", "Test Track", "2026-07-21T00:00:00+00:00"),
    )


def _seed_team(team_id: str = "team-ops-q-1") -> str:
    from app import db

    db.execute(
        "INSERT INTO teams (team_id, name, use_case, status, recommendation_json, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (team_id, "Test Team", "prepare a quotation", "recommend", "{}",
         "2026-07-21T00:00:00+00:00"),
    )
    return team_id


def _seed_team_with_agent(team_id: str = "team-ops-q-agent-1",
                           agent_id: str = "a1", role: str = "Test Sales Role") -> str:
    from app import db

    _seed_team(team_id)
    role_id = _seed_role(role)
    db.execute(
        "INSERT INTO team_agents(team_id, agent_id, role_id, stage, squad, "
        "produces, consumes, anchor, skill_overrides, skill_disabled, "
        "rationale, sort_order) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (team_id, agent_id, role_id, 1, None, None, None, 0, "{}", "[]", None, 0),
    )
    return team_id


@pytest.fixture()
def beta_client(tmp_db, monkeypatch):
    """TestClient wired to a temp DB with BETA_AUTH forced on (mirrors
    test_user_inputs.py's / test_agent_chat.py's beta_client fixture)."""
    from app import settings

    monkeypatch.setattr(settings, "BETA_AUTH", True, raising=False)
    monkeypatch.setattr(settings, "BETA_TOKEN_SALT", "ops-questions-test-salt", raising=False)
    monkeypatch.setattr(settings, "APP_ADMIN_TOKEN", "ops-questions-test-admin-token", raising=False)

    from app.main import app as fastapi_app

    return TestClient(fastapi_app)


# ═════════════════════════════════════════════════════════════════════════
# Validator unit tests — llm_contracts._validate_ops_questions (no LLM, no DB)
# ═════════════════════════════════════════════════════════════════════════
def test_validate_zero_questions_is_valid():
    from app.llm_contracts import _validate_ops_questions

    v = _validate_ops_questions(3)
    assert v({"questions": []}) == {"questions": []}


def test_validate_accepts_up_to_max():
    from app.llm_contracts import _validate_ops_questions

    v = _validate_ops_questions(3)
    qs = [
        {"kind": "threshold", "name": "Discount cap", "question": "What discount needs manager approval?"},
        {"kind": "procedure", "name": "Approval steps", "question": "What happens after a quote is drafted?"},
        {"kind": "metric", "name": "Turnaround", "question": "How fast should a quote go out?"},
    ]
    out = v({"questions": qs})
    assert len(out["questions"]) == 3


def test_validate_rejects_over_max():
    from app.llm_contracts import _validate_ops_questions

    v = _validate_ops_questions(2)
    qs = [
        {"kind": "threshold", "name": "A", "question": "Q1?"},
        {"kind": "procedure", "name": "B", "question": "Q2?"},
        {"kind": "metric", "name": "C", "question": "Q3?"},
    ]
    with pytest.raises(ValueError):
        v({"questions": qs})


def test_validate_rejects_bad_kind():
    from app.llm_contracts import _validate_ops_questions

    v = _validate_ops_questions(3)
    with pytest.raises(ValueError):
        v({"questions": [{"kind": "sample", "name": "A", "question": "Q?"}]})


def test_validate_rejects_missing_shape():
    from app.llm_contracts import _validate_ops_questions

    v = _validate_ops_questions(3)
    with pytest.raises(ValueError):
        v({"not_questions": []})
    with pytest.raises(ValueError):
        v({"questions": "not-a-list"})


def test_validate_rejects_empty_name_or_question():
    from app.llm_contracts import _validate_ops_questions

    v = _validate_ops_questions(3)
    with pytest.raises(ValueError):
        v({"questions": [{"kind": "threshold", "name": "", "question": "Q?"}]})
    with pytest.raises(ValueError):
        v({"questions": [{"kind": "threshold", "name": "A", "question": "  "}]})


def test_validate_rejects_name_too_long():
    from app.llm_contracts import _validate_ops_questions, OPS_QUESTION_NAME_MAX_CHARS

    v = _validate_ops_questions(3)
    with pytest.raises(ValueError):
        v({"questions": [
            {"kind": "threshold", "name": "n" * (OPS_QUESTION_NAME_MAX_CHARS + 1), "question": "Q?"},
        ]})


def test_validate_rejects_question_too_long():
    from app.llm_contracts import _validate_ops_questions, OPS_QUESTION_TEXT_MAX_CHARS

    v = _validate_ops_questions(3)
    with pytest.raises(ValueError):
        v({"questions": [
            {"kind": "threshold", "name": "A", "question": "q" * (OPS_QUESTION_TEXT_MAX_CHARS + 1)},
        ]})


def test_validate_dedupes_by_name_case_insensitive():
    from app.llm_contracts import _validate_ops_questions

    v = _validate_ops_questions(3)
    out = v({"questions": [
        {"kind": "threshold", "name": "Discount cap", "question": "First version?"},
        {"kind": "procedure", "name": "discount cap", "question": "Second version?"},
    ]})
    assert len(out["questions"]) == 1
    assert out["questions"][0]["question"] == "First version?"


def test_generate_ops_questions_returns_empty_when_max_is_zero(monkeypatch):
    from app import settings, llm_contracts

    monkeypatch.setattr(settings, "OPS_QUESTIONS_MAX", 0, raising=False)
    out = llm_contracts.generate_ops_questions("prepare a quotation", ["Sales Agent"], [])
    assert out == {"questions": []}


# ═════════════════════════════════════════════════════════════════════════
# Endpoint tests — POST /api/team/{team_id}/ops-questions
# ═════════════════════════════════════════════════════════════════════════
def test_ops_questions_401_without_token(beta_client):
    team_id = _seed_team("team-ops-q-401")
    r = beta_client.post(f"/api/team/{team_id}/ops-questions")
    assert r.status_code == 401


def test_ops_questions_404_unknown_team(beta_client):
    from app import auth

    token = auth.mint("ops-q-tester-404")
    r = beta_client.post(
        "/api/team/does-not-exist/ops-questions",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 404
    assert r.json()["detail"] == "team not found"


def test_ops_questions_200_returns_stubbed_questions(beta_client, monkeypatch):
    from app import auth, llm_contracts

    calls = []

    def _fake_generate(use_case, team_roles, known_inputs):
        calls.append((use_case, team_roles, known_inputs))
        return {"questions": [
            {"kind": "threshold", "name": "Discount cap",
             "question": "What discount level needs manager approval?"},
        ]}

    monkeypatch.setattr(llm_contracts, "generate_ops_questions", _fake_generate)

    team_id = _seed_team_with_agent("team-ops-q-200", role="Quotation Specialist")
    token = auth.mint("ops-q-tester-200")
    r = beta_client.post(
        f"/api/team/{team_id}/ops-questions",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["questions"][0]["kind"] == "threshold"
    assert body["questions"][0]["name"] == "Discount cap"

    # generate_ops_questions was called with the team's use_case + role names
    assert len(calls) == 1
    use_case, team_roles, known_inputs = calls[0]
    assert use_case == "prepare a quotation"
    assert team_roles == ["Quotation Specialist"]
    assert known_inputs == []


def test_ops_questions_200_passes_existing_user_inputs(beta_client, monkeypatch):
    """known_inputs must be threaded through from the team's saved user_inputs
    (so the LLM can avoid re-asking about them)."""
    from app import auth, llm_contracts
    from app.services import team_service

    calls = []

    def _fake_generate(use_case, team_roles, known_inputs):
        calls.append(known_inputs)
        return {"questions": []}

    monkeypatch.setattr(llm_contracts, "generate_ops_questions", _fake_generate)

    team_id = _seed_team_with_agent("team-ops-q-known", role="Quotation Specialist")
    team_service.set_user_inputs(team_id, [
        {"kind": "sample", "name": "Price list", "content": "Widget X: $437/unit"},
    ])
    token = auth.mint("ops-q-tester-known")
    r = beta_client.post(
        f"/api/team/{team_id}/ops-questions",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"questions": []}
    assert len(calls) == 1
    assert calls[0][0]["name"] == "Price list"


def test_ops_questions_502_on_llm_failure(beta_client, monkeypatch):
    from app import auth, llm_contracts

    def _fake_generate(use_case, team_roles, known_inputs):
        raise llm_contracts.LLMError("ops_questions failed after 3 attempts: boom")

    monkeypatch.setattr(llm_contracts, "generate_ops_questions", _fake_generate)

    team_id = _seed_team_with_agent("team-ops-q-502")
    token = auth.mint("ops-q-tester-502")
    r = beta_client.post(
        f"/api/team/{team_id}/ops-questions",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 502


# ═════════════════════════════════════════════════════════════════════════
# user_inputs: new ops kinds + raised caps (PUT /api/team/{team_id}/inputs)
# ═════════════════════════════════════════════════════════════════════════
def test_user_inputs_accepts_ops_taxonomy_kinds(beta_client):
    from app import auth

    team_id = _seed_team("team-ops-q-inputs-kinds")
    token = auth.mint("ops-q-tester-inputs-kinds")
    items = [
        {"kind": "procedure", "name": "Approval steps", "content": "Manager signs off over $500."},
        {"kind": "threshold", "name": "Discount cap", "content": "Max 10% without director approval."},
        {"kind": "constraint", "name": "Compliance rule", "content": "Never quote below cost."},
        {"kind": "handoff", "name": "Who approves", "content": "Sales lead reviews before sending."},
        {"kind": "metric", "name": "Turnaround SLA", "content": "Quotes go out within 24 hours."},
        {"kind": "workaround", "name": "Rush orders", "content": "Call the warehouse to confirm stock."},
    ]
    r = beta_client.put(
        f"/api/team/{team_id}/inputs",
        json={"user_inputs": items},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["count"] == 6


def test_user_inputs_raised_cap_allows_up_to_ten_items(beta_client):
    from app import auth

    team_id = _seed_team("team-ops-q-inputs-cap10")
    token = auth.mint("ops-q-tester-inputs-cap10")
    items = [{"kind": "sample", "name": f"item-{i}", "content": "x"} for i in range(10)]
    r = beta_client.put(
        f"/api/team/{team_id}/inputs",
        json={"user_inputs": items},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["count"] == 10


def test_user_inputs_raised_cap_rejects_eleven_items(beta_client):
    from app import auth

    team_id = _seed_team("team-ops-q-inputs-cap11")
    token = auth.mint("ops-q-tester-inputs-cap11")
    items = [{"kind": "sample", "name": f"item-{i}", "content": "x"} for i in range(11)]
    r = beta_client.put(
        f"/api/team/{team_id}/inputs",
        json={"user_inputs": items},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 422
    assert "10" in r.json()["detail"]


def test_user_inputs_raised_byte_cap_rejects_over_40kb(beta_client):
    from app import auth

    team_id = _seed_team("team-ops-q-inputs-bytes")
    token = auth.mint("ops-q-tester-inputs-bytes")
    r = beta_client.put(
        f"/api/team/{team_id}/inputs",
        json={"user_inputs": [{"kind": "sample", "name": "big", "content": "x" * (40 * 1024 + 1)}]},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 422


def test_user_inputs_rejects_unknown_kind(beta_client):
    from app import auth

    team_id = _seed_team("team-ops-q-inputs-badkind")
    token = auth.mint("ops-q-tester-inputs-badkind")
    r = beta_client.put(
        f"/api/team/{team_id}/inputs",
        json={"user_inputs": [{"kind": "bogus", "name": "n", "content": "c"}]},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 422
