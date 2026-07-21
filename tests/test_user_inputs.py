"""Real-inputs intake — Iteration 3 (user-value loop):
PUT /api/team/{team_id}/inputs, its roundtrip through GET /api/team/{team_id},
and skill_bundle_service's baking of those inputs into the generated overlay.

TestClient against the real app.main.app, BETA_AUTH forced on via the same
fixture pattern as tests/test_feedback.py's / tests/test_agent_chat.py's
beta_client. No real LLM calls: PUT /inputs never touches an LLM (pure
validate+store), and the overlay tests monkeypatch
skill_bundle_service.llm_chat to a canned narrative reply.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient


# ── seeding helpers ──────────────────────────────────────────────────────────
def _seed_role(role: str = "Test Sales Role") -> int:
    from app import db

    return db.execute(
        "INSERT INTO roles(role, sector, track, seeded_at) VALUES (?, ?, ?, ?)",
        (role, "Test Sector", "Test Track", "2026-07-21T00:00:00+00:00"),
    )


def _seed_team(team_id: str = "team-inputs-1") -> str:
    from app import db

    db.execute(
        "INSERT INTO teams (team_id, name, use_case, status, recommendation_json, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (team_id, "Test Team", "prepare a quotation", "recommend", "{}",
         "2026-07-21T00:00:00+00:00"),
    )
    return team_id


def _seed_team_with_agent(team_id: str = "team-inputs-agent-1",
                           agent_id: str = "a1") -> tuple[str, str]:
    from app import db

    _seed_team(team_id)
    role_id = _seed_role()
    db.execute(
        "INSERT INTO team_agents(team_id, agent_id, role_id, stage, squad, "
        "produces, consumes, anchor, skill_overrides, skill_disabled, "
        "rationale, sort_order) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (team_id, agent_id, role_id, 1, None, None, None, 0, "{}", "[]", None, 0),
    )
    return team_id, agent_id


@pytest.fixture()
def beta_client(tmp_db, monkeypatch):
    """TestClient wired to a temp DB with BETA_AUTH forced on (mirrors
    test_feedback.py's / test_agent_chat.py's beta_client fixture)."""
    from app import settings

    monkeypatch.setattr(settings, "BETA_AUTH", True, raising=False)
    monkeypatch.setattr(settings, "BETA_TOKEN_SALT", "user-inputs-test-salt", raising=False)
    monkeypatch.setattr(settings, "APP_ADMIN_TOKEN", "user-inputs-test-admin-token", raising=False)

    from app.main import app as fastapi_app

    return TestClient(fastapi_app)


def _put_inputs(client, token, team_id, items):
    return client.put(
        f"/api/team/{team_id}/inputs",
        json={"user_inputs": items},
        headers={"Authorization": f"Bearer {token}"},
    )


# ── PUT: auth ────────────────────────────────────────────────────────────────
def test_put_inputs_401_without_token(beta_client):
    team_id = _seed_team("team-inputs-401")
    r = beta_client.put(
        f"/api/team/{team_id}/inputs",
        json={"user_inputs": [{"kind": "sample", "name": "x", "content": "y"}]},
    )
    assert r.status_code == 401


# ── PUT: 404 unknown team ────────────────────────────────────────────────────
def test_put_inputs_404_unknown_team(beta_client):
    from app import auth

    token = auth.mint("inputs-tester-404")
    r = _put_inputs(
        beta_client, token, "does-not-exist",
        [{"kind": "sample", "name": "x", "content": "y"}],
    )
    assert r.status_code == 404
    assert r.json()["detail"] == "team not found"


# ── PUT: 422 validation ──────────────────────────────────────────────────────
def test_put_inputs_422_too_many_items(beta_client):
    from app import auth

    team_id = _seed_team("team-inputs-422-count")
    token = auth.mint("inputs-tester-422-count")
    items = [{"kind": "sample", "name": f"item-{i}", "content": "x"} for i in range(6)]
    r = _put_inputs(beta_client, token, team_id, items)
    assert r.status_code == 422
    assert "5" in r.json()["detail"]


def test_put_inputs_422_oversize_total_content(beta_client):
    from app import auth

    team_id = _seed_team("team-inputs-422-oversize")
    token = auth.mint("inputs-tester-422-oversize")
    # one item alone > 20KB
    r = _put_inputs(
        beta_client, token, team_id,
        [{"kind": "sample", "name": "big", "content": "x" * (20 * 1024 + 1)}],
    )
    assert r.status_code == 422
    assert "20480" in r.json()["detail"] or "bytes" in r.json()["detail"]


def test_put_inputs_422_name_too_long(beta_client):
    from app import auth

    team_id = _seed_team("team-inputs-422-name")
    token = auth.mint("inputs-tester-422-name")
    r = _put_inputs(
        beta_client, token, team_id,
        [{"kind": "sample", "name": "n" * 101, "content": "some content"}],
    )
    assert r.status_code == 422
    assert "100" in r.json()["detail"]


def test_put_inputs_422_empty_content(beta_client):
    from app import auth

    team_id = _seed_team("team-inputs-422-empty")
    token = auth.mint("inputs-tester-422-empty")
    r = _put_inputs(
        beta_client, token, team_id,
        [{"kind": "sample", "name": "n", "content": "   "}],
    )
    assert r.status_code == 422


# ── PUT: 200 happy path + roundtrip via get_team ────────────────────────────
def test_put_inputs_200_and_roundtrip_via_get_team(beta_client, tmp_db):
    from app import auth

    team_id = _seed_team("team-inputs-200")
    token = auth.mint("inputs-tester-200")
    items = [
        {"kind": "sample", "name": "Price list",
         "content": "Widget X: $437/unit, 40+ units: $391/unit"},
        {"kind": "database", "name": "Product catalogue", "content": "id,name,price\n1,Widget X,437"},
    ]
    r = _put_inputs(beta_client, token, team_id, items)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["count"] == 2
    assert body["bytes"] == sum(len(it["content"].encode("utf-8")) for it in items)

    g = beta_client.get(f"/api/team/{team_id}", headers={"Authorization": f"Bearer {token}"})
    assert g.status_code == 200, g.text
    got = g.json()["user_inputs"]
    assert len(got) == 2
    assert got[0]["kind"] == "sample"
    assert got[0]["name"] == "Price list"
    assert "437" in got[0]["content"]


def test_put_inputs_replaces_not_merges(beta_client):
    from app import auth

    team_id = _seed_team("team-inputs-replace")
    token = auth.mint("inputs-tester-replace")
    _put_inputs(beta_client, token, team_id,
                [{"kind": "sample", "name": "first", "content": "a"}])
    r = _put_inputs(beta_client, token, team_id,
                     [{"kind": "database", "name": "second", "content": "b"}])
    assert r.status_code == 200
    assert r.json()["count"] == 1

    g = beta_client.get(f"/api/team/{team_id}", headers={"Authorization": f"Bearer {token}"})
    got = g.json()["user_inputs"]
    assert len(got) == 1
    assert got[0]["name"] == "second"


def test_get_team_user_inputs_empty_before_any_put(beta_client):
    from app import auth

    team_id = _seed_team("team-inputs-empty")
    token = auth.mint("inputs-tester-empty")
    g = beta_client.get(f"/api/team/{team_id}", headers={"Authorization": f"Bearer {token}"})
    assert g.status_code == 200
    assert g.json()["user_inputs"] == []


# ── team_service unit-level validation (direct, no HTTP) ───────────────────
def test_set_user_inputs_404_unknown_team(tmp_db):
    from app.services import team_service

    with pytest.raises(team_service.TeamNotFound):
        team_service.set_user_inputs("does-not-exist", [])


def test_set_user_inputs_422_missing_kind(tmp_db):
    from app.services import team_service

    team_id = _seed_team("team-inputs-svc-1")
    with pytest.raises(team_service.UserInputsInvalid):
        team_service.set_user_inputs(team_id, [{"name": "x", "content": "y"}])


# ── skill_bundle_service overlay: provided-inputs baking + missing manifest ─
_CANNED_OVERLAY_NARRATIVE = (
    "### Applying this capability to the task\n"
    "This agent drafts the quotation using the base skill's pricing "
    "capability, grounded in the provided price list.\n\n"
    "### Deliverable format\n"
    "- Line items with quantity and unit price\n"
    "- Total\n\n"
    "### Success criteria\n"
    "- Every line item reflects the provided pricing\n"
    "- The deliverable matches the requested quantity breaks\n"
)


@pytest.fixture()
def stub_overlay_llm_chat(monkeypatch):
    """Replace skill_bundle_service.llm_chat (the overlay narrative call) with
    a canned reply — no real LLM call, matches tests/test_agent_chat.py's
    stub_llm_chat pattern for the chat-turn call."""
    from app.services import skill_bundle_service as sbs

    calls: list[list[dict]] = []

    def _fake(messages, temperature=0.2, max_tokens=1536, purpose=None):
        calls.append(messages)
        return _CANNED_OVERLAY_NARRATIVE

    monkeypatch.setattr(sbs, "llm_chat", _fake)
    return calls


_BASE_MD = (
    "---\n"
    "name: test-sales-skill\n"
    "description: A canned base skill. Use when testing.\n"
    "---\n\n"
    "## Pricing\nApply the correct unit price for the requested quantity.\n"
)

_PRICE_LIST_CONTENT = "Widget X: $437/unit, 40+ units: $391/unit"


def _agent_context(user_inputs):
    return {
        "role": "Sales Agent",
        "stage": 1,
        "squad": None,
        "consumes": [],
        "produces": [{"artifact": "quotation", "description": None}],
        "artifacts_needed": [
            {"kind": "sample", "description": "a worked sample quotation"},
            {"kind": "database", "description": "product catalogue"},
        ],
        "user_inputs": user_inputs,
    }


def test_overlay_contains_provided_inputs_section_and_fenced_content(stub_overlay_llm_chat):
    from app.services import skill_bundle_service as sbs

    ctx = _agent_context([
        {"kind": "sample", "name": "Price list", "content": _PRICE_LIST_CONTENT},
    ])
    overlay = sbs.generate_task_overlay("prepare a quotation", ctx, _BASE_MD, force=True)

    assert "### Your provided inputs" in overlay
    assert "#### Price list (sample)" in overlay
    # verbatim, fenced
    assert f"```\n{_PRICE_LIST_CONTENT}\n```" in overlay


def _required_inputs_block(overlay: str) -> str:
    """Isolate JUST the '### Required real inputs' subsection (not the whole
    overlay, which also has an unfiltered '### Inputs' listing every
    artifacts_needed entry by design — only the gap manifest is filtered)."""
    after = overlay.split("### Required real inputs (not included in this scaffold)")[1]
    # cut at the next top-level subsection (### Your provided inputs, or the
    # LLM section's ### Applying... when nothing was provided)
    for stop in ("### Your provided inputs", "### Applying this capability to the task"):
        if stop in after:
            after = after.split(stop)[0]
    return after


def test_overlay_required_inputs_drops_satisfied_kind_keeps_missing_kind(stub_overlay_llm_chat):
    from app.services import skill_bundle_service as sbs

    ctx = _agent_context([
        {"kind": "sample", "name": "Price list", "content": _PRICE_LIST_CONTENT},
    ])
    overlay = sbs.generate_task_overlay("prepare a quotation", ctx, _BASE_MD, force=True)

    required_block = _required_inputs_block(overlay)
    assert "**sample**" not in required_block, "satisfied kind must be dropped from the gap manifest"
    assert "**database**" in required_block, "still-missing kind must remain in the gap manifest"
    assert "Every identified input artifact has already been supplied" not in required_block


def test_overlay_section_order_required_then_provided_then_llm(stub_overlay_llm_chat):
    from app.services import skill_bundle_service as sbs

    ctx = _agent_context([
        {"kind": "sample", "name": "Price list", "content": _PRICE_LIST_CONTENT},
    ])
    overlay = sbs.generate_task_overlay("prepare a quotation", ctx, _BASE_MD, force=True)

    i_required = overlay.index("### Required real inputs")
    i_provided = overlay.index("### Your provided inputs")
    i_llm = overlay.index("### Applying this capability to the task")
    assert i_required < i_provided < i_llm


def test_overlay_omits_provided_inputs_section_when_none_saved(stub_overlay_llm_chat):
    from app.services import skill_bundle_service as sbs

    ctx = _agent_context([])
    overlay = sbs.generate_task_overlay("prepare a quotation", ctx, _BASE_MD, force=True)

    assert "### Your provided inputs" not in overlay
    # both artifact kinds remain in the gap manifest — nothing was supplied
    assert "**sample**" in overlay
    assert "**database**" in overlay


def test_overlay_all_satisfied_shows_no_gaps_message(stub_overlay_llm_chat):
    from app.services import skill_bundle_service as sbs

    ctx = _agent_context([
        {"kind": "sample", "name": "Price list", "content": _PRICE_LIST_CONTENT},
        {"kind": "database", "name": "Catalogue", "content": "id,name,price\n1,Widget X,437"},
    ])
    overlay = sbs.generate_task_overlay("prepare a quotation", ctx, _BASE_MD, force=True)

    required_block = _required_inputs_block(overlay)
    assert "Every identified input artifact has already been supplied" in required_block


# ── grounding hash: cache-busting on user_inputs change ────────────────────
def test_overlay_grounding_hash_changes_when_user_inputs_change():
    from app.services.skill_bundle_service import _overlay_grounding_hash

    common = ("Sales Agent", "prepare a quotation", [], [{"artifact": "quotation"}], [],
              _BASE_MD)
    h_none = _overlay_grounding_hash(*common, [])
    h_one = _overlay_grounding_hash(
        *common, [{"kind": "sample", "name": "Price list", "content": _PRICE_LIST_CONTENT}],
    )
    h_one_edited = _overlay_grounding_hash(
        *common, [{"kind": "sample", "name": "Price list", "content": _PRICE_LIST_CONTENT + " (v2)"}],
    )
    assert h_none != h_one
    assert h_one != h_one_edited
    assert h_none != h_one_edited


def test_generate_task_overlay_cache_busts_when_inputs_saved(stub_overlay_llm_chat, tmp_path, monkeypatch):
    """End-to-end: the SAME (use_case, role, contract) with vs. without saved
    user_inputs must land in DIFFERENT cache files, so a Save immediately
    changes what compose_bundle returns rather than serving a stale cached
    overlay (the UI's 'regenerate picks up saved inputs' guarantee)."""
    from app.services import skill_bundle_service as sbs

    monkeypatch.setattr(sbs, "OVERLAY_CACHE_DIR", tmp_path / "overlay")

    ctx_before = _agent_context([])
    ctx_after = _agent_context([
        {"kind": "sample", "name": "Price list", "content": _PRICE_LIST_CONTENT},
    ])

    sbs.generate_task_overlay("prepare a quotation", ctx_before, _BASE_MD, force=True)
    sbs.generate_task_overlay("prepare a quotation", ctx_after, _BASE_MD, force=True)

    cached_files = list((tmp_path / "overlay").glob("*.md"))
    assert len(cached_files) == 2, "different user_inputs must produce different cache entries"


# ── build_agent_context: loads user_inputs from the teams row ──────────────
def test_build_agent_context_loads_user_inputs_from_team_row(tmp_db):
    from app.services import team_service, skill_bundle_service as sbs

    team_id, agent_id = _seed_team_with_agent("team-inputs-ctx-1")
    team_service.set_user_inputs(team_id, [
        {"kind": "sample", "name": "Price list", "content": _PRICE_LIST_CONTENT},
    ])

    ctx = sbs.build_agent_context(team_id, agent_id)
    assert ctx["user_inputs"] == [
        {"kind": "sample", "name": "Price list", "content": _PRICE_LIST_CONTENT},
    ]


def test_build_agent_context_user_inputs_empty_when_none_saved(tmp_db):
    from app.services import skill_bundle_service as sbs

    team_id, agent_id = _seed_team_with_agent("team-inputs-ctx-2")
    ctx = sbs.build_agent_context(team_id, agent_id)
    assert ctx["user_inputs"] == []
