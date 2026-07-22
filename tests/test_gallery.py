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
    team_service.set_user_inputs / skill_bundle_service.compose_bundle with
    canned fast fakes — no real LLM call anywhere in this fixture.
    compose_bundle's canned content changes on every call (a call counter
    baked into the body) so a rebuild is observably different from the
    first build.

    `calls["set_user_inputs_args"]` records every (team_id, items) pair
    passed to set_user_inputs, in order — used to assert the seed_inputs
    archetypes call it, with the right payload, before compose runs."""
    from app import build_gallery

    calls = {"recommend": 0, "wire": 0, "set_user_inputs": 0, "compose": 0,
              "set_user_inputs_args": [], "order": []}

    def _fake_recommend(use_case=None, brief=None, intake_session_id=None):
        calls["recommend"] += 1
        return {
            "team_id": f"team-{calls['recommend']}",
            "agents": [{"agent_id": "a1", "role_id": 1, "role": "Test Role"}],
        }

    def _fake_wire(team_id, use_case=None):
        calls["wire"] += 1
        return {"team_id": team_id, "handoffs": []}

    def _fake_set_user_inputs(team_id, items):
        calls["set_user_inputs"] += 1
        calls["set_user_inputs_args"].append((team_id, items))
        calls["order"].append("set_user_inputs")
        return {"ok": True, "count": len(items), "bytes": 0}

    def _fake_compose(team_id, agent_id, use_case, artifacts_needed=None):
        calls["compose"] += 1
        calls["order"].append("compose")
        return (
            "---\n"
            "name: test-gallery-skill\n"
            "description: A canned test skill. Use when testing.\n"
            "---\n\n"
            f"## Role capability reference\n\nBody v{calls['compose']}\n"
        )

    monkeypatch.setattr(build_gallery.team_service, "recommend", _fake_recommend)
    monkeypatch.setattr(build_gallery.handoff_service, "wire", _fake_wire)
    monkeypatch.setattr(build_gallery.team_service, "set_user_inputs", _fake_set_user_inputs)
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


def test_build_gallery_builds_all_by_default(tmp_db, stub_pipeline):
    from app import build_gallery, db

    stats = build_gallery.run()
    assert stats["built"] == len(build_gallery.ARCHETYPES)
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
    # _fake_recommend never inserts a `teams` row (unlike the real recommend),
    # so the seed_inputs archetypes' real set_user_inputs would 404 (TeamNotFound)
    # looking it up — stub it too, same as stub_pipeline does.
    monkeypatch.setattr(build_gallery.team_service, "set_user_inputs", lambda *a, **k: None)
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


# ── trades/renovation archetypes — seed_inputs wiring ───────────────────────
_TRADES_SLUGS = (
    "variation-order-capturer",
    "quote-followup-chaser",
    "maintenance-agreement-converter",
    "margin-by-job-reporter",
)


def test_seed_inputs_archetypes_call_set_user_inputs_before_compose(tmp_db, stub_pipeline):
    """Each of the 4 trades archetypes carries seed_inputs; building it must
    call team_service.set_user_inputs with exactly that payload, and that
    call must happen BEFORE compose_bundle so the stored bundle_md's
    '### Your provided inputs' section reflects it."""
    from app import build_gallery

    for slug in _TRADES_SLUGS:
        archetype = next(a for a in build_gallery.ARCHETYPES if a["slug"] == slug)
        assert archetype.get("seed_inputs"), f"{slug} must carry seed_inputs"

    stats = build_gallery.run(only=set(_TRADES_SLUGS))
    assert stats["built"] == len(_TRADES_SLUGS)
    assert stats["failed"] == 0

    assert stub_pipeline["set_user_inputs"] == len(_TRADES_SLUGS)
    called_items = [items for _team_id, items in stub_pipeline["set_user_inputs_args"]]
    expected_items = [
        next(a for a in build_gallery.ARCHETYPES if a["slug"] == slug)["seed_inputs"]
        for slug in _TRADES_SLUGS
    ]
    assert called_items == expected_items

    # order: every set_user_inputs call precedes its compose call
    order = stub_pipeline["order"]
    assert order.count("set_user_inputs") == order.count("compose") == len(_TRADES_SLUGS)
    su_positions = [i for i, ev in enumerate(order) if ev == "set_user_inputs"]
    compose_positions = [i for i, ev in enumerate(order) if ev == "compose"]
    for su_i, compose_i in zip(su_positions, compose_positions):
        assert su_i < compose_i


def test_archetypes_without_seed_inputs_never_call_set_user_inputs(tmp_db, stub_pipeline):
    from app import build_gallery

    build_gallery.run(only={"collections-chaser"})
    assert stub_pipeline["set_user_inputs"] == 0


def test_original_five_archetypes_are_byte_identical(tmp_db):
    """The first 5 archetypes (pre-existing) must be untouched: same slugs,
    in the same order, with no seed_inputs key added."""
    from app import build_gallery

    original = [
        {
            "slug": "collections-chaser",
            "label": "Collections Chaser",
            "blurb": "Chases overdue invoices with the right urgency per account",
            "use_case": "chase up customers who owe us money",
        },
        {
            "slug": "contract-reviewer",
            "label": "Contract Reviewer",
            "blurb": "Clause-by-clause risk review of vendor contracts before you sign",
            "use_case": "review a vendor contract before signing",
        },
        {
            "slug": "quotation-writer",
            "label": "Quotation Writer",
            "blurb": "Formal quotations from an RFQ and your price list",
            "use_case": "prepare a quotation for a corporate client",
        },
        {
            "slug": "onboarding-coordinator",
            "label": "Onboarding Coordinator",
            "blurb": "Structured onboarding plans for new hires",
            "use_case": "onboard a new hire",
        },
        {
            "slug": "campaign-planner",
            "label": "Campaign Planner",
            "blurb": "Launch campaign plans that respect your brand rules",
            "use_case": "run a social media campaign for a product launch",
        },
    ]
    assert build_gallery.ARCHETYPES[:5] == original


def test_trades_archetypes_present_with_required_fields(tmp_db):
    from app import build_gallery

    by_slug = {a["slug"]: a for a in build_gallery.ARCHETYPES}
    assert set(_TRADES_SLUGS) <= set(by_slug.keys())
    assert len(build_gallery.ARCHETYPES) == 9

    shared_names = {"Tools we already use", "Human review rule", "What stays human"}
    for slug in _TRADES_SLUGS:
        a = by_slug[slug]
        assert a["label"] and isinstance(a["label"], str)
        assert a["blurb"] and isinstance(a["blurb"], str)
        assert a["use_case"] and isinstance(a["use_case"], str)
        seed_inputs = a["seed_inputs"]
        assert isinstance(seed_inputs, list) and len(seed_inputs) == 4
        for item in seed_inputs:
            assert set(item.keys()) == {"kind", "name", "content"}
            assert item["kind"] in build_gallery.team_service.USER_INPUT_KINDS
            assert item["name"].strip()
            assert item["content"].strip()
        names = {item["name"] for item in seed_inputs}
        assert shared_names <= names
        # the one archetype-specific input beyond the 3 shared ones
        assert len(names - shared_names) == 1


# ── no client-identifying text anywhere in app/ or web/ source ─────────────
def test_no_client_identifying_strings_in_source():
    """The trades/renovation archetypes must be framed generically — no
    company names, UEN, or person names leaked in from whatever source
    document informed them. Cheap deterministic grep across app/ and
    web/ source (excluding node_modules/build output/caches). Deliberately
    excludes SkillsFuture/SSOC from the banned list — those legitimately
    appear elsewhere in the app (the framework's own K&A grounding) and
    are not client-identifying."""
    import pathlib

    repo_root = pathlib.Path(__file__).resolve().parent.parent
    banned = ("HRD", "UEN", "Raye", "Matthew")
    skip_dir_names = {"node_modules", "__pycache__", ".git", "dist", "build", ".venv"}
    scan_roots = [repo_root / "app", repo_root / "web" / "src"]

    offenders = []
    for root in scan_roots:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if any(part in skip_dir_names for part in path.parts):
                continue
            if path.suffix not in {".py", ".ts", ".tsx", ".js", ".jsx", ".md", ".json", ".sql"}:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            for term in banned:
                if term in text:
                    offenders.append(f"{path.relative_to(repo_root)}: {term!r}")

    assert not offenders, "client-identifying / skill-code strings found:\n" + "\n".join(offenders)
