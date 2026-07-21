"""Starter gallery — Iteration 4 (user-value loop):
GET /api/gallery, GET /api/gallery/{slug}, and `python -m app.build_gallery`.

TestClient against the real app.main.app, BETA_AUTH forced on via the same
fixture pattern as tests/test_feedback.py's / tests/test_agent_chat.py's /
tests/test_user_inputs.py's beta_client fixture. No real LLM calls anywhere
in this file: the API tests seed `gallery_agents` rows directly; the
build_gallery tests monkeypatch team_service.recommend / handoff_service.wire
/ skill_bundle_service.compose_bundle to canned fast fakes.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

_CANNED_BUNDLE_MD = (
    "---\n"
    "name: test-gallery-skill\n"
    "description: A canned test skill. Use when testing.\n"
    "---\n\n"
    "## Role capability reference\n\nDo the task well.\n"
)


def _seed_gallery_row(slug: str = "collections-chaser", **overrides) -> dict:
    from app import db

    row = {
        "slug": slug,
        "label": "Collections Chaser",
        "blurb": "Chases overdue invoices with the right urgency per account",
        "use_case": "chase up customers who owe us money",
        "team_id": "team-gallery-1",
        "agent_id": "a1",
        "bundle_md": _CANNED_BUNDLE_MD,
        "created_at": "2026-07-21T00:00:00+00:00",
    }
    row.update(overrides)
    db.execute(
        "INSERT INTO gallery_agents(slug, label, blurb, use_case, team_id, agent_id, "
        "bundle_md, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (row["slug"], row["label"], row["blurb"], row["use_case"], row["team_id"],
         row["agent_id"], row["bundle_md"], row["created_at"]),
    )
    return row


@pytest.fixture()
def beta_client(tmp_db, monkeypatch):
    """TestClient wired to a temp DB with BETA_AUTH forced on (mirrors
    test_feedback.py's / test_agent_chat.py's / test_user_inputs.py's
    beta_client fixture)."""
    from app import settings

    monkeypatch.setattr(settings, "BETA_AUTH", True, raising=False)
    monkeypatch.setattr(settings, "BETA_TOKEN_SALT", "gallery-test-salt", raising=False)
    monkeypatch.setattr(settings, "APP_ADMIN_TOKEN", "gallery-test-admin-token", raising=False)

    from app.main import app as fastapi_app

    return TestClient(fastapi_app)


# ── GET /api/gallery — open, metadata only ─────────────────────────────────
def test_gallery_list_open_no_auth_metadata_only(beta_client):
    _seed_gallery_row()
    r = beta_client.get("/api/gallery")
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body) == 1
    item = body[0]
    assert item["slug"] == "collections-chaser"
    assert item["label"] == "Collections Chaser"
    assert "blurb" in item
    # metadata only — never the composed bundle or the internal team/agent refs
    assert "bundle_md" not in item
    assert "use_case" not in item
    assert "team_id" not in item
    assert "agent_id" not in item


def test_gallery_list_empty_when_no_rows(beta_client):
    r = beta_client.get("/api/gallery")
    assert r.status_code == 200
    assert r.json() == []


def test_gallery_list_multiple_rows_all_metadata_only(beta_client):
    _seed_gallery_row("collections-chaser")
    _seed_gallery_row("contract-reviewer", label="Contract Reviewer",
                       blurb="Clause-by-clause risk review", team_id="team-gallery-2")
    r = beta_client.get("/api/gallery")
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 2
    slugs = {item["slug"] for item in body}
    assert slugs == {"collections-chaser", "contract-reviewer"}
    for item in body:
        assert set(item.keys()) == {"slug", "label", "blurb"}


# ── GET /api/gallery/{slug} — beta-gated, full row ─────────────────────────
def test_gallery_detail_401_without_token(beta_client):
    _seed_gallery_row()
    r = beta_client.get("/api/gallery/collections-chaser")
    assert r.status_code == 401
    assert r.json()["detail"] == "beta token required"


def test_gallery_detail_200_with_token_full_row(beta_client):
    row = _seed_gallery_row()
    from app import auth

    token = auth.mint("gallery-tester")
    r = beta_client.get(
        "/api/gallery/collections-chaser",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["slug"] == row["slug"]
    assert body["label"] == row["label"]
    assert body["blurb"] == row["blurb"]
    assert body["use_case"] == row["use_case"]
    assert body["team_id"] == row["team_id"]
    assert body["agent_id"] == row["agent_id"]
    assert body["bundle_md"] == row["bundle_md"]


def test_gallery_detail_200_with_admin_token(beta_client):
    _seed_gallery_row()
    r = beta_client.get(
        "/api/gallery/collections-chaser",
        headers={"Authorization": "Bearer gallery-test-admin-token"},
    )
    assert r.status_code == 200, r.text


def test_gallery_detail_404_unknown_slug(beta_client):
    from app import auth

    token = auth.mint("gallery-tester-404")
    r = beta_client.get(
        "/api/gallery/no-such-slug",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 404
    assert r.json()["detail"] == "unknown gallery agent"


# ── python -m app.build_gallery — idempotency + isolation ──────────────────
@pytest.fixture()
def stub_pipeline(monkeypatch):
    """Replace team_service.recommend / handoff_service.wire /
    skill_bundle_service.compose_bundle with canned fast fakes — no real LLM
    call anywhere in this fixture. compose_bundle's canned content changes
    on every call (a call counter baked into the body) so a rebuild is
    observably different from the first build."""
    from app import build_gallery

    calls = {"recommend": 0, "wire": 0, "compose": 0}

    def _fake_recommend(use_case=None, brief=None, intake_session_id=None):
        calls["recommend"] += 1
        return {
            "team_id": f"team-{calls['recommend']}",
            "agents": [{"agent_id": "a1", "role_id": 1, "role": "Test Role"}],
        }

    def _fake_wire(team_id, use_case=None):
        calls["wire"] += 1
        return {"team_id": team_id, "handoffs": []}

    def _fake_compose(team_id, agent_id, use_case, artifacts_needed=None):
        calls["compose"] += 1
        return (
            "---\n"
            "name: test-gallery-skill\n"
            "description: A canned test skill. Use when testing.\n"
            "---\n\n"
            f"## Role capability reference\n\nBody v{calls['compose']}\n"
        )

    monkeypatch.setattr(build_gallery.team_service, "recommend", _fake_recommend)
    monkeypatch.setattr(build_gallery.handoff_service, "wire", _fake_wire)
    monkeypatch.setattr(build_gallery.skill_bundle_service, "compose_bundle", _fake_compose)
    return calls


def test_build_gallery_only_builds_requested_slug(tmp_db, stub_pipeline):
    from app import build_gallery, db

    stats = build_gallery.run(only={"collections-chaser"})
    assert stats["built"] == 1
    assert stats["skipped"] == 0
    assert stats["failed"] == 0

    rows = db.query("SELECT slug FROM gallery_agents")
    assert [r["slug"] for r in rows] == ["collections-chaser"]
    assert stub_pipeline["recommend"] == 1
    assert stub_pipeline["wire"] == 1
    assert stub_pipeline["compose"] == 1


def test_build_gallery_second_run_skips_existing(tmp_db, stub_pipeline):
    from app import build_gallery, db

    build_gallery.run(only={"collections-chaser"})
    first_md = db.query(
        "SELECT bundle_md FROM gallery_agents WHERE slug = ?",
        ("collections-chaser",), one=True,
    )["bundle_md"]

    stats2 = build_gallery.run(only={"collections-chaser"})
    assert stats2["built"] == 0
    assert stats2["skipped"] == 1
    assert stats2["failed"] == 0

    rows = db.query("SELECT * FROM gallery_agents WHERE slug = ?", ("collections-chaser",))
    assert len(rows) == 1  # never duplicated
    assert rows[0]["bundle_md"] == first_md  # untouched by the skipped run
    # the pipeline was never re-invoked for the skip
    assert stub_pipeline["compose"] == 1


def test_build_gallery_force_rebuilds(tmp_db, stub_pipeline):
    from app import build_gallery, db

    build_gallery.run(only={"collections-chaser"})
    first_md = db.query(
        "SELECT bundle_md FROM gallery_agents WHERE slug = ?",
        ("collections-chaser",), one=True,
    )["bundle_md"]

    stats2 = build_gallery.run(only={"collections-chaser"}, force=True)
    assert stats2["built"] == 1
    assert stats2["skipped"] == 0

    rows = db.query("SELECT * FROM gallery_agents WHERE slug = ?", ("collections-chaser",))
    assert len(rows) == 1  # replaced, not duplicated
    assert rows[0]["bundle_md"] != first_md  # rebuilt — new canned content
    assert stub_pipeline["compose"] == 2


def test_build_gallery_builds_all_five_by_default(tmp_db, stub_pipeline):
    from app import build_gallery, db

    stats = build_gallery.run()
    assert stats["built"] == 5
    assert stats["failed"] == 0
    rows = db.query("SELECT slug FROM gallery_agents")
    assert {r["slug"] for r in rows} == {a["slug"] for a in build_gallery.ARCHETYPES}


def test_build_gallery_one_failure_does_not_abort_others(tmp_db, monkeypatch):
    from app import build_gallery, db

    failing_use_case = build_gallery.ARCHETYPES[0]["use_case"]

    def _fake_recommend(use_case=None, brief=None, intake_session_id=None):
        if use_case == failing_use_case:
            raise RuntimeError("boom — simulated composer failure")
        return {"team_id": f"team-{use_case}", "agents": [{"agent_id": "a1"}]}

    monkeypatch.setattr(build_gallery.team_service, "recommend", _fake_recommend)
    monkeypatch.setattr(build_gallery.handoff_service, "wire", lambda *a, **k: None)
    monkeypatch.setattr(
        build_gallery.skill_bundle_service, "compose_bundle",
        lambda *a, **k: (
            "---\nname: t\ndescription: t. Use when t.\n---\n\nBody\n"
        ),
    )

    stats = build_gallery.run()
    assert stats["failed"] == 1
    assert stats["built"] == len(build_gallery.ARCHETYPES) - 1
    rows = db.query("SELECT slug FROM gallery_agents")
    assert build_gallery.ARCHETYPES[0]["slug"] not in {r["slug"] for r in rows}
    assert len(rows) == len(build_gallery.ARCHETYPES) - 1
