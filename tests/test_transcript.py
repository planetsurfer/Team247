"""Transcript extraction — Iteration 3 (OPERATIONS-INTAKE loop):
llm_contracts.extract_ops_brief / _validate_ops_brief / _chunk_transcript /
_merge_extractions, and POST /api/team/{team_id}/transcript.

TestClient against the real app.main.app, BETA_AUTH forced on via the same
fixture pattern as tests/test_ops_questions.py's / tests/test_user_inputs.py's
beta_client. No real LLM calls in the endpoint/validator/chunker tests: those
monkeypatch app.llm_contracts.extract_ops_brief directly (same pattern as
test_ops_questions.py's _fake_generate). ONE test at the bottom calls the
real LLM (gated behind RUN_LIVE_LLM=1, same opt-in convention as
test_simulation_smoke.py's RUN_SIM / test_chat_flow_corpus.py's live-server
gating) against tests/fixtures/synthetic_transcript.txt.

ABSOLUTE PII note: nothing in this module asserts on, stores, or prints the
transcript text outside of what the code under test itself does (which is:
never persist it, never log it — see the logging assertion test at the
bottom, and llm_contracts.py's module-level PII note on this section).
"""
from __future__ import annotations

import os
import pathlib

import pytest
from fastapi.testclient import TestClient

FIXTURES_DIR = pathlib.Path(__file__).resolve().parent / "fixtures"
SYNTHETIC_TRANSCRIPT_PATH = FIXTURES_DIR / "synthetic_transcript.txt"


# ── seeding helpers (mirrors tests/test_ops_questions.py) ───────────────────
def _seed_team(team_id: str = "team-transcript-1", use_case: str = "prepare a quotation") -> str:
    from app import db

    db.execute(
        "INSERT INTO teams (team_id, name, use_case, status, recommendation_json, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (team_id, "Test Team", use_case, "recommend", "{}", "2026-07-21T00:00:00+00:00"),
    )
    return team_id


@pytest.fixture()
def beta_client(tmp_db, monkeypatch):
    """TestClient wired to a temp DB with BETA_AUTH forced on (mirrors
    test_ops_questions.py's / test_user_inputs.py's beta_client fixture)."""
    from app import settings

    monkeypatch.setattr(settings, "BETA_AUTH", True, raising=False)
    monkeypatch.setattr(settings, "BETA_TOKEN_SALT", "transcript-test-salt", raising=False)
    monkeypatch.setattr(settings, "APP_ADMIN_TOKEN", "transcript-test-admin-token", raising=False)

    from app.main import app as fastapi_app

    return TestClient(fastapi_app)


# ═════════════════════════════════════════════════════════════════════════
# Validator unit tests — llm_contracts._validate_ops_brief (no LLM, no DB)
# ═════════════════════════════════════════════════════════════════════════
def test_validate_ops_brief_empty_is_valid():
    from app.llm_contracts import _validate_ops_brief

    out = _validate_ops_brief({"items": [], "open_questions": []})
    assert out == {"items": [], "open_questions": []}


def test_validate_ops_brief_accepts_up_to_caps():
    from app.llm_contracts import TRANSCRIPT_MAX_ITEMS, TRANSCRIPT_MAX_OPEN_QUESTIONS, _validate_ops_brief

    items = [
        {"kind": "threshold", "name": f"Item {i}", "content": f"Content {i}"}
        for i in range(TRANSCRIPT_MAX_ITEMS)
    ]
    oqs = [
        {"kind": "constraint", "name": f"Q {i}", "question": f"What about {i}?"}
        for i in range(TRANSCRIPT_MAX_OPEN_QUESTIONS)
    ]
    out = _validate_ops_brief({"items": items, "open_questions": oqs})
    assert len(out["items"]) == TRANSCRIPT_MAX_ITEMS
    assert len(out["open_questions"]) == TRANSCRIPT_MAX_OPEN_QUESTIONS


def test_validate_ops_brief_rejects_over_max_items():
    from app.llm_contracts import TRANSCRIPT_MAX_ITEMS, _validate_ops_brief

    items = [
        {"kind": "threshold", "name": f"Item {i}", "content": "x"}
        for i in range(TRANSCRIPT_MAX_ITEMS + 1)
    ]
    with pytest.raises(ValueError):
        _validate_ops_brief({"items": items, "open_questions": []})


def test_validate_ops_brief_rejects_over_max_open_questions():
    from app.llm_contracts import TRANSCRIPT_MAX_OPEN_QUESTIONS, _validate_ops_brief

    oqs = [
        {"kind": "constraint", "name": f"Q {i}", "question": "x?"}
        for i in range(TRANSCRIPT_MAX_OPEN_QUESTIONS + 1)
    ]
    with pytest.raises(ValueError):
        _validate_ops_brief({"items": [], "open_questions": oqs})


def test_validate_ops_brief_rejects_bad_item_kind():
    from app.llm_contracts import _validate_ops_brief

    with pytest.raises(ValueError):
        _validate_ops_brief({
            "items": [{"kind": "not-a-kind", "name": "A", "content": "c"}],
            "open_questions": [],
        })


def test_validate_ops_brief_accepts_artifact_kinds_for_items():
    """Items may use the artifact kinds (sample/blank_format/past_documents/
    database) in addition to the ops taxonomy, per TRANSCRIPT_ITEM_KINDS."""
    from app.llm_contracts import _validate_ops_brief

    out = _validate_ops_brief({
        "items": [
            {"kind": "database", "name": "CRM", "content": "Salesforce is the system of record."},
        ],
        "open_questions": [],
    })
    assert out["items"][0]["kind"] == "database"


def test_validate_ops_brief_rejects_missing_shape():
    from app.llm_contracts import _validate_ops_brief

    with pytest.raises(ValueError):
        _validate_ops_brief({"items": []})  # no open_questions key
    with pytest.raises(ValueError):
        _validate_ops_brief({"items": "not-a-list", "open_questions": []})
    with pytest.raises(ValueError):
        _validate_ops_brief("not-a-dict")


def test_validate_ops_brief_rejects_empty_name_or_content():
    from app.llm_contracts import _validate_ops_brief

    with pytest.raises(ValueError):
        _validate_ops_brief({
            "items": [{"kind": "threshold", "name": "", "content": "c"}],
            "open_questions": [],
        })
    with pytest.raises(ValueError):
        _validate_ops_brief({
            "items": [{"kind": "threshold", "name": "A", "content": "   "}],
            "open_questions": [],
        })


def test_validate_ops_brief_rejects_content_too_long():
    from app.llm_contracts import TRANSCRIPT_ITEM_CONTENT_MAX_CHARS, _validate_ops_brief

    with pytest.raises(ValueError):
        _validate_ops_brief({
            "items": [{
                "kind": "threshold", "name": "A",
                "content": "c" * (TRANSCRIPT_ITEM_CONTENT_MAX_CHARS + 1),
            }],
            "open_questions": [],
        })


def test_validate_ops_brief_rejects_name_too_long():
    from app.llm_contracts import TRANSCRIPT_ITEM_NAME_MAX_CHARS, _validate_ops_brief

    with pytest.raises(ValueError):
        _validate_ops_brief({
            "items": [{
                "kind": "threshold", "name": "n" * (TRANSCRIPT_ITEM_NAME_MAX_CHARS + 1),
                "content": "c",
            }],
            "open_questions": [],
        })


def test_validate_ops_brief_dedupes_items_by_name_case_insensitive():
    from app.llm_contracts import _validate_ops_brief

    out = _validate_ops_brief({
        "items": [
            {"kind": "threshold", "name": "Discount cap", "content": "First version"},
            {"kind": "threshold", "name": "discount cap", "content": "Second version"},
        ],
        "open_questions": [],
    })
    assert len(out["items"]) == 1
    assert out["items"][0]["content"] == "First version"


def test_validate_ops_brief_rejects_bad_open_question_kind():
    from app.llm_contracts import _validate_ops_brief

    with pytest.raises(ValueError):
        _validate_ops_brief({
            "items": [],
            "open_questions": [{"kind": "not-a-real-kind", "name": "A", "question": "q?"}],
        })


# ═════════════════════════════════════════════════════════════════════════
# Chunker unit tests — llm_contracts._chunk_transcript (no LLM, no DB)
# ═════════════════════════════════════════════════════════════════════════
def test_chunk_transcript_empty_returns_empty_list():
    from app.llm_contracts import _chunk_transcript

    assert _chunk_transcript("") == []
    assert _chunk_transcript("   \n  ") == []


def test_chunk_transcript_small_text_returns_single_chunk():
    from app.llm_contracts import _chunk_transcript

    text = "line one\nline two\nline three\n"
    chunks = _chunk_transcript(text, max_bytes=1024)
    assert chunks == [text]


def test_chunk_transcript_splits_on_line_boundaries():
    from app.llm_contracts import _chunk_transcript

    # Each line is exactly 10 bytes incl. newline; cap at 25 bytes -> 2 lines/chunk max
    lines = [f"line{i:04d}\n" for i in range(10)]  # "line0000\n" == 9 bytes
    text = "".join(lines)
    chunks = _chunk_transcript(text, max_bytes=25)
    assert len(chunks) > 1
    # every chunk must be within the byte cap
    for c in chunks:
        assert len(c.encode("utf-8")) <= 25
    # no line is torn mid-way — every original line appears verbatim in exactly one chunk
    reassembled = "".join(chunks)
    assert reassembled == text


def test_chunk_transcript_never_exceeds_cap_even_with_oversized_line():
    from app.llm_contracts import _chunk_transcript

    # One pathological line far bigger than the cap
    text = "short line\n" + ("x" * 100) + "\n" + "another short line\n"
    chunks = _chunk_transcript(text, max_bytes=20)
    assert len(chunks) > 1
    for c in chunks:
        assert len(c.encode("utf-8")) <= 20


def test_chunk_transcript_real_fixture_stays_single_chunk():
    """The committed fixture (~3KB) is well under the 30KB chunk threshold."""
    from app.llm_contracts import _chunk_transcript

    text = SYNTHETIC_TRANSCRIPT_PATH.read_text()
    chunks = _chunk_transcript(text)
    assert len(chunks) == 1
    assert chunks[0] == text


def test_chunk_transcript_over_30kb_splits_into_multiple_chunks():
    from app.llm_contracts import TRANSCRIPT_CHUNK_BYTES, _chunk_transcript

    line = "This is a representative transcript line of ops chatter.\n"
    # enough repeats to comfortably exceed 30KB
    reps = (TRANSCRIPT_CHUNK_BYTES // len(line.encode("utf-8"))) * 3
    text = line * reps
    assert len(text.encode("utf-8")) > TRANSCRIPT_CHUNK_BYTES
    chunks = _chunk_transcript(text)
    assert len(chunks) > 1
    for c in chunks:
        assert len(c.encode("utf-8")) <= TRANSCRIPT_CHUNK_BYTES


# ═════════════════════════════════════════════════════════════════════════
# Merge unit tests — llm_contracts._merge_extractions (no LLM, no DB)
# ═════════════════════════════════════════════════════════════════════════
def test_merge_extractions_dedupes_by_name_first_wins():
    from app.llm_contracts import _merge_extractions

    r1 = {"items": [{"kind": "threshold", "name": "Discount cap", "content": "From chunk 1"}],
          "open_questions": []}
    r2 = {"items": [{"kind": "threshold", "name": "discount cap", "content": "From chunk 2"}],
          "open_questions": []}
    out = _merge_extractions([r1, r2])
    assert len(out["items"]) == 1
    assert out["items"][0]["content"] == "From chunk 1"


def test_merge_extractions_unions_distinct_items_across_chunks():
    from app.llm_contracts import _merge_extractions

    r1 = {"items": [{"kind": "threshold", "name": "A", "content": "a"}], "open_questions": []}
    r2 = {"items": [{"kind": "handoff", "name": "B", "content": "b"}], "open_questions": []}
    out = _merge_extractions([r1, r2])
    names = {it["name"] for it in out["items"]}
    assert names == {"A", "B"}


def test_merge_extractions_caps_totals():
    from app.llm_contracts import TRANSCRIPT_MAX_ITEMS, TRANSCRIPT_MAX_OPEN_QUESTIONS, _merge_extractions

    results = [
        {
            "items": [{"kind": "threshold", "name": f"chunk{c}-item{i}", "content": "x"}
                      for i in range(TRANSCRIPT_MAX_ITEMS)],
            "open_questions": [{"kind": "constraint", "name": f"chunk{c}-q{i}", "question": "x?"}
                                for i in range(TRANSCRIPT_MAX_OPEN_QUESTIONS)],
        }
        for c in range(3)
    ]
    out = _merge_extractions(results)
    assert len(out["items"]) == TRANSCRIPT_MAX_ITEMS
    assert len(out["open_questions"]) == TRANSCRIPT_MAX_OPEN_QUESTIONS


def test_merge_extractions_merges_open_questions_independently_of_items():
    from app.llm_contracts import _merge_extractions

    r1 = {"items": [], "open_questions": [{"kind": "constraint", "name": "Refund policy", "question": "q1?"}]}
    r2 = {"items": [], "open_questions": [{"kind": "constraint", "name": "Refund policy", "question": "q2?"}]}
    out = _merge_extractions([r1, r2])
    assert len(out["open_questions"]) == 1
    assert out["open_questions"][0]["question"] == "q1?"


# ═════════════════════════════════════════════════════════════════════════
# extract_ops_brief: chunking dispatch (monkeypatched chunk-level call)
# ═════════════════════════════════════════════════════════════════════════
def test_extract_ops_brief_empty_transcript_returns_empty_no_llm_call(monkeypatch):
    from app import llm_contracts

    calls = []
    monkeypatch.setattr(
        llm_contracts, "_extract_ops_brief_chunk",
        lambda use_case, chunk: calls.append(chunk) or {"items": [], "open_questions": []},
    )
    out = llm_contracts.extract_ops_brief("prepare a quotation", "   ")
    assert out == {"items": [], "open_questions": []}
    assert calls == []  # no LLM call for empty/whitespace-only text


def test_extract_ops_brief_single_chunk_passthrough(monkeypatch):
    from app import llm_contracts

    def _fake_chunk(use_case, chunk):
        return {"items": [{"kind": "threshold", "name": "A", "content": "c"}], "open_questions": []}

    monkeypatch.setattr(llm_contracts, "_extract_ops_brief_chunk", _fake_chunk)
    out = llm_contracts.extract_ops_brief("prepare a quotation", "a short transcript")
    assert out["items"][0]["name"] == "A"


def test_extract_ops_brief_multi_chunk_calls_per_chunk_and_merges(monkeypatch):
    from app import llm_contracts

    calls = []

    def _fake_chunk(use_case, chunk):
        calls.append(chunk)
        idx = len(calls)
        return {
            "items": [{"kind": "threshold", "name": f"Item {idx}", "content": "c"}],
            "open_questions": [],
        }

    monkeypatch.setattr(llm_contracts, "_extract_ops_brief_chunk", _fake_chunk)
    # Real text over the default 30KB chunk threshold — NOT monkeypatching
    # TRANSCRIPT_CHUNK_BYTES itself: _chunk_transcript's `max_bytes` default
    # argument is bound at function-definition time, so patching the module
    # constant afterwards wouldn't actually change chunking behavior here.
    line = "This is a representative line of ops chatter for chunking.\n"
    long_text = line * ((llm_contracts.TRANSCRIPT_CHUNK_BYTES // len(line.encode("utf-8"))) * 3)
    assert len(long_text.encode("utf-8")) > llm_contracts.TRANSCRIPT_CHUNK_BYTES
    out = llm_contracts.extract_ops_brief("prepare a quotation", long_text)
    assert len(calls) > 1
    assert len(out["items"]) == len(calls)


# ═════════════════════════════════════════════════════════════════════════
# Endpoint tests — POST /api/team/{team_id}/transcript
# ═════════════════════════════════════════════════════════════════════════
def _fake_brief():
    return {
        "items": [
            {"kind": "threshold", "name": "Discount approval cap",
             "content": "Discounts above $2,000 need written approval from the sales director."},
            {"kind": "handoff", "name": "Qualification to billing",
             "content": "After qualification, Dana hands the file to billing."},
        ],
        "open_questions": [
            {"kind": "constraint", "name": "Refund policy",
             "question": "Should refunds go to the original payment method only, or can store credit be offered?"},
        ],
    }


def test_transcript_401_without_token(beta_client):
    team_id = _seed_team("team-transcript-401")
    r = beta_client.post(f"/api/team/{team_id}/transcript", json={"text": "some transcript text"})
    assert r.status_code == 401


def test_transcript_404_unknown_team(beta_client):
    from app import auth

    token = auth.mint("transcript-tester-404")
    r = beta_client.post(
        "/api/team/does-not-exist/transcript",
        json={"text": "some transcript text"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 404
    assert r.json()["detail"] == "team not found"


def test_transcript_422_empty_text(beta_client):
    from app import auth

    team_id = _seed_team("team-transcript-422-empty")
    token = auth.mint("transcript-tester-422-empty")
    r = beta_client.post(
        f"/api/team/{team_id}/transcript",
        json={"text": "   "},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 422


def test_transcript_413_oversize(beta_client):
    from app import auth
    from app.routers.team import TRANSCRIPT_MAX_BYTES

    team_id = _seed_team("team-transcript-413")
    token = auth.mint("transcript-tester-413")
    oversize_text = "x" * (TRANSCRIPT_MAX_BYTES + 1)
    r = beta_client.post(
        f"/api/team/{team_id}/transcript",
        json={"text": oversize_text},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 413


def test_transcript_200_returns_extraction_proposal(beta_client, monkeypatch):
    from app import auth, llm_contracts

    calls = []

    def _fake_extract(use_case, transcript_text):
        calls.append((use_case, transcript_text))
        return _fake_brief()

    monkeypatch.setattr(llm_contracts, "extract_ops_brief", _fake_extract)

    team_id = _seed_team("team-transcript-200")
    token = auth.mint("transcript-tester-200")
    transcript = SYNTHETIC_TRANSCRIPT_PATH.read_text()
    r = beta_client.post(
        f"/api/team/{team_id}/transcript",
        json={"text": transcript},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["items"][0]["kind"] == "threshold"
    assert "2,000" in body["items"][0]["content"]
    assert body["open_questions"][0]["kind"] == "constraint"

    assert len(calls) == 1
    use_case, sent_text = calls[0]
    assert use_case == "prepare a quotation"
    assert sent_text == transcript  # exact transcript reaches the extractor, untouched


def test_transcript_502_on_llm_failure(beta_client, monkeypatch):
    from app import auth, llm_contracts

    def _fake_extract(use_case, transcript_text):
        raise llm_contracts.LLMError("ops_extract failed after 3 attempts: boom")

    monkeypatch.setattr(llm_contracts, "extract_ops_brief", _fake_extract)

    team_id = _seed_team("team-transcript-502")
    token = auth.mint("transcript-tester-502")
    r = beta_client.post(
        f"/api/team/{team_id}/transcript",
        json={"text": "some transcript text"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 502


def test_transcript_result_never_persisted_on_teams_row(beta_client, monkeypatch, tmp_db):
    """PII guard: after a successful extraction, nothing resembling the raw
    transcript text is stored on the teams row (the endpoint never writes to
    the DB at all — this asserts that invariant end to end)."""
    from app import auth, db, llm_contracts

    monkeypatch.setattr(llm_contracts, "extract_ops_brief", lambda use_case, text: _fake_brief())

    sentinel = "zzqx-transcript-should-never-be-stored-4f8a1c"
    team_id = _seed_team("team-transcript-no-persist")
    token = auth.mint("transcript-tester-no-persist")
    r = beta_client.post(
        f"/api/team/{team_id}/transcript",
        json={"text": f"some transcript text containing {sentinel}"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text

    row = db.query("SELECT * FROM teams WHERE team_id = ?", (team_id,), one=True)
    for value in dict(row).values():
        assert sentinel not in str(value)


# ═════════════════════════════════════════════════════════════════════════
# Logging assertion — the sentinel must never appear in captured log output
# ═════════════════════════════════════════════════════════════════════════
def test_transcript_sentinel_absent_from_log_output(beta_client, monkeypatch, capsys):
    """Drives the real request-logging middleware (app.logging_setup) with a
    monkeypatched LLM call, and asserts a sentinel planted in the request
    body never appears in anything printed to stdout (structlog's
    PrintLoggerFactory writes JSON log lines to stdout) — the concrete
    regression guard for the ABSOLUTE PII RULE that request bodies and
    transcript content are never logged."""
    from app import auth, llm_contracts

    monkeypatch.setattr(llm_contracts, "extract_ops_brief", lambda use_case, text: _fake_brief())

    sentinel = "zzqx-log-sentinel-do-not-log-9d21ff"
    team_id = _seed_team("team-transcript-log-sentinel")
    token = auth.mint("transcript-tester-log-sentinel")

    capsys.readouterr()  # drain anything buffered before this test's request
    r = beta_client.post(
        f"/api/team/{team_id}/transcript",
        json={"text": f"transcript text with the sentinel {sentinel} embedded in it"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text

    captured = capsys.readouterr()
    assert sentinel not in captured.out
    assert sentinel not in captured.err


# ═════════════════════════════════════════════════════════════════════════
# ONE live-LLM test — real extract_ops_brief() against the committed fixture.
# Gated behind RUN_LIVE_LLM=1 (opt-in, mirrors test_simulation_smoke.py's
# RUN_SIM gating) since it makes a real, billed LLM call. Tolerant: matches
# on substrings/keywords rather than exact model phrasing, since wording
# varies run to run.
# ═════════════════════════════════════════════════════════════════════════
@pytest.mark.skipif(
    os.environ.get("RUN_LIVE_LLM") != "1",
    reason="set RUN_LIVE_LLM=1 to run the real-LLM transcript extraction test",
)
def test_extract_ops_brief_live_llm_against_synthetic_transcript():
    from app import llm_contracts

    transcript = SYNTHETIC_TRANSCRIPT_PATH.read_text()
    result = llm_contracts.extract_ops_brief(
        "prepare and process a customer quotation", transcript,
    )

    items = result["items"]
    open_questions = result["open_questions"]

    # (a) the $2,000 discount-approval threshold must land in items
    assert any(
        "2,000" in it["content"] or "2000" in it["content"]
        for it in items
    ), f"$2,000 threshold not found in items: {items}"

    # (b) the Dana -> billing handoff must land in items. Tolerant on the
    # exact wording (the model may paraphrase "Dana"/"the sales associate"
    # away from the literal name): require a handoff-kind item naming billing
    # as the receiving side, OR one that names Dana explicitly.
    assert any(
        (it["kind"] == "handoff" and "billing" in it["content"].lower())
        or "dana" in it["content"].lower()
        for it in items
    ), f"Dana handoff not found in items: {items}"

    # (d) the refund-policy disagreement must land in open_questions, NOT items
    assert any("refund" in q["question"].lower() for q in open_questions), (
        f"refund disagreement not found in open_questions: {open_questions}"
    )
    assert not any("refund" in it["content"].lower() for it in items), (
        f"refund disagreement leaked into items (should be an open_question): {items}"
    )

    # (e) the PII/irrelevance sentinel must appear NOWHERE in the result
    serialized = str(result).lower()
    assert "ficus" not in serialized, f"sentinel 'ficus' leaked into extraction result: {result}"
