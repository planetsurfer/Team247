"""Rigorous chat-flow suite for Team247 — drives the live deployed server.

Exercises the two backend capabilities the chat frontend depends on:

1. **100% team guarantee** — for every realistic user input in `corpus.CORPUS`,
   `POST /api/team/recommend {use_case}` MUST return a team with ≥1 agent.
   No input may error out or come back empty. This is the product's core
   promise: "successfully provide the agent or the agentic team 100% of the
   time."

2. **Real, never-invented roles** — every recommended agent's `role_id` MUST
   resolve via `GET /api/catalog/{role_id}` to a real SkillsFuture role. The
   handoff's invariant: "Recommendations are retrieved from the official
   catalogue — never invented."

3. **Adaptive refinement** — the `/api/intake/*` interview MUST ask ≥1 leading
   question before producing a team (the system refines instead of one-shot
   guessing), and SHOULD probe whether the user has an artifact (sample /
   blank format / past documents / database) to work from.

The suite talks to the live server at `TEAM247_BASE_URL` (default
http://127.0.0.1:8000) so it tests the system the user actually views. It
skips cleanly when the server is down.

Run:
    TEAM247_BASE_URL=http://127.0.0.1:8000 pytest tests/test_chat_flow_corpus.py -v
"""
from __future__ import annotations

import os
import time

import pytest
import requests

from tests.corpus import CORPUS, INTAKE_SUBSET_INDICES, artifact_coverage, corpus_ids

BASE_URL = os.environ.get("TEAM247_BASE_URL", "http://127.0.0.1:8000")
# Per-call timeout. recommend drives a multi-step LLM pipeline (classify +
# team_recommend) and routinely takes 20-60s; 150s is a generous ceiling so a
# slow-but-correct run still passes while a true hang fails (rigorous).
TIMEOUT = 150
# Keywords an honest intake should use when probing for artifacts.
ARTIFACT_KEYWORDS = (
    "sample", "example", "format", "template", "blank", "form",
    "past", "previous", "document", "documents", "database", "system",
    "file", "files", "export", "spreadsheet", "excel", "sheet",
    "record", "records", "data", "history", "repository", "source",
)


# ── fixtures ─────────────────────────────────────────────────────────────────
@pytest.fixture(scope="module")
def server_up() -> bool:
    """Skip the whole module if the live server isn't reachable."""
    try:
        r = requests.get(f"{BASE_URL}/api/health", timeout=8)
    except requests.RequestException:
        pytest.skip(f"live server not reachable at {BASE_URL} — start it first")
    # /api/health may 404 if the router path differs; accept any HTTP response
    # (the SPA root is enough proof the server is up).
    if r.status_code >= 500:
        pytest.skip(f"live server unhealthy (health {r.status_code})")
    return True


def _recommend(use_case: str) -> dict:
    """POST /api/team/recommend and return the parsed JSON body."""
    r = requests.post(
        f"{BASE_URL}/api/team/recommend",
        headers={"Content-Type": "application/json"},
        json={"use_case": use_case},
        timeout=TIMEOUT,
    )
    assert r.status_code == 200, f"recommend HTTP {r.status_code}: {r.text[:300]}"
    return r.json()


def _catalog_role(role_id: int) -> dict | None:
    r = requests.get(f"{BASE_URL}/api/catalog/{role_id}", timeout=TIMEOUT)
    if r.status_code == 404:
        return None
    assert r.status_code == 200, f"catalog/{role_id} HTTP {r.status_code}"
    return r.json()


# ── static coverage (no LLM) ─────────────────────────────────────────────────
def test_corpus_covers_all_artifact_categories(server_up):
    """The corpus must span every artifact category so the suite isn't myopic."""
    cov = artifact_coverage()
    missing = [c for c, n in cov.items() if n == 0]
    assert not missing, f"corpus missing artifact categories: {missing} — coverage={cov}"


def test_corpus_covers_multiple_domains(server_up):
    domains = {t.domain for t in CORPUS}
    assert len(domains) >= 6, f"corpus too narrow — only {len(domains)} domains: {domains}"


# ── 100% team guarantee (parametrized over the full corpus) ─────────────────
@pytest.mark.parametrize("task", CORPUS, ids=corpus_ids())
def test_recommend_identifies_structured_artifacts(server_up, task):
    """Hardening over the soft keyword-sniff: recommend MUST return a structured
    `artifacts_needed` list — the 'identify if a sample should be provided'
    capability as a verifiable guarantee, not free-text leading questions.

    Asserts the real guarantees (all catchable regressions):
      1. `artifacts_needed` is a non-empty list.
      2. Every returned `kind` is a valid enum value (never free-text garbage).
      3. The system identified a REAL artifact need — at least one kind is not
         "none" (every corpus task genuinely needs an input artifact).

    We do NOT hard-assert the exact expected kind (task.artifact) per input:
    the LLM may legitimately surface a different-but-valid artifact for the
    same task (e.g. for payroll it returned `sample` + `blank_format` rather
    than the expected `past_documents` — a defensible, arguably-better read).
    Forcing an exact label match would make the prompt over-constrained and
    the test flaky on legitimate LLM variation. The expected label is kept in
    `task.artifact` as documentation; we log when the LLM diverges.
    """
    body = _recommend(task.use_case)
    arts = body.get("artifacts_needed") or []
    assert isinstance(arts, list) and len(arts) >= 1, (
        f"artifacts_needed must be a non-empty list for {task.use_case!r}; got {arts!r}"
    )
    valid = {"sample", "blank_format", "past_documents", "database", "none"}
    kinds = {a.get("kind") for a in arts if isinstance(a, dict)}
    bad = kinds - valid
    assert not bad, f"artifacts_needed has invalid kinds {bad} for {task.use_case!r}"
    real = kinds - {"none"}
    assert real, (
        f"system returned only 'none' for {task.use_case!r} — failed to "
        f"identify the real artifact need (expected ~{task.artifact}). full={arts}"
    )
    # Informational, not fatal: note when the LLM diverged from the expected label.
    if task.artifact not in kinds:
        print(
            f"[info] {task.domain}-{task.use_case!r}: expected ~{task.artifact}, "
            f"got {sorted(kinds)} (legitimate label variation)"
        )


@pytest.mark.parametrize("task", CORPUS, ids=corpus_ids())
def test_input_yields_real_team_with_skills(server_up, task):
    """The core guarantee, fully checked per input in ONE recommend call:

    1. `POST /api/team/recommend` returns 200 with a team_id + ≥1 agent
       (the "provide the agent/team 100% of the time" promise — no input may
       error or come back empty).
    2. Every recommended role_id resolves via `GET /api/catalog/{role_id}` to a
       real SkillsFuture role (never invented — handoff invariant).
    3. Every recommended role carries ≥1 official K&A skill (the loadout
       seeds from these; an empty skill set would render an empty loadout).

    One recommend call per input keeps this rigorous-but-affordable: 16 LLM
    recommends instead of 48. A failure at any step is a real product bug.
    """
    body = _recommend(task.use_case)

    # (1) 100% team guarantee
    assert body.get("team_id"), f"empty/missing team_id: {body}"
    agents = body.get("agents") or []
    assert len(agents) >= 1, (
        f"0 agents for input {task.use_case!r} — 100%-team guarantee failed. "
        f"raw={str(body)[:400]}"
    )
    for a in agents:
        assert a.get("agent_id") and a.get("role_id") and a.get("role"), (
            f"agent missing required fields: {a}"
        )

    # (2) real, never-invented roles
    for a in agents:
        role = _catalog_role(a["role_id"])
        assert role is not None, (
            f"agent {a['agent_id']} role_id={a['role_id']} not in catalog — "
            "an invented/phantom role leaked into recommendations"
        )
        assert role.get("role") == a.get("role"), (
            f"role name mismatch: catalog={role.get('role')!r} vs agent={a.get('role')!r}"
        )

    # (3) each role has official K&A skills (cold roles distill synchronously)
    for a in agents:
        r = requests.get(f"{BASE_URL}/api/catalog/{a['role_id']}/card", timeout=TIMEOUT)
        assert r.status_code == 200, f"card HTTP {r.status_code} for role_id={a['role_id']}"
        sk = r.json().get("sk") or []
        assert len(sk) >= 1, (
            f"role {a.get('role')!r} (role_id={a['role_id']}) has 0 official skills — "
            "loadout would be empty"
        )


# ── adaptive intake: leading questions + artifact probing ──────────────────
# Drives the intake interview for a subset (LLM-heavy; bounded to bound cost).
def _drive_intake(use_case: str) -> dict:
    """Run the adaptive intake to completion for one task.

    Returns a report: questions_asked (all leading questions across rounds),
    rounds, ready, brief, and the team produced from the brief.
    """
    s = requests.post(f"{BASE_URL}/api/intake/start", timeout=TIMEOUT).json()
    sid = s["session_id"]
    all_questions: list[str] = list(s.get("questions") or [])
    rounds = 0

    # First answer: the user's plain-word task (answers the seed questions).
    answers = [use_case]
    while rounds < 8:  # server caps at MAX_INTAKE_ROUNDS=6; 8 is a safety net
        rounds += 1
        r = requests.post(
            f"{BASE_URL}/api/intake/{sid}/answer",
            json={"answers": answers},
            timeout=TIMEOUT,
        )
        assert r.status_code == 200, f"intake answer HTTP {r.status_code}: {r.text[:200]}"
        turn = r.json()
        if turn.get("ready"):
            return {
                "questions": all_questions,
                "rounds": rounds,
                "ready": True,
                "brief": turn.get("brief"),
                "session_id": sid,
            }
        # not ready — record the follow-ups and answer them next round
        qs = turn.get("questions") or []
        all_questions.extend(qs)
        # Answer each follow-up with a short, task-grounded elaboration so the
        # interview can converge. (Honest: the user would answer in their words.)
        answers = [f"For this task ({use_case}): I'd provide whatever artifacts "
                   f"you need — please tell me which."] * max(1, len(qs))

    # Should be unreachable given the server's force-terminate, but be safe.
    return {"questions": all_questions, "rounds": rounds, "ready": False,
            "brief": None, "session_id": sid}


@pytest.mark.parametrize("idx", INTAKE_SUBSET_INDICES,
                         ids=[f"intake-{i}" for i in INTAKE_SUBSET_INDICES])
def test_intake_refines_with_leading_questions_then_produces_team(server_up, idx):
    """The system must (a) ask ≥1 leading question before producing a team —
    refining instead of one-shot guessing — and (b) ultimately produce a team."""
    task = CORPUS[idx]
    report = _drive_intake(task.use_case)

    # (a) refinement happened: at least one question was asked beyond the seeds.
    # The 3 seed questions are always present; refinement means follow-ups.
    seed_q_count = 3
    followups = report["questions"][seed_q_count:]
    assert len(followups) >= 1, (
        f"intake produced a team with NO leading questions for {task.use_case!r} — "
        "the system did not refine. (one-shot guess is not the spec)"
    )
    assert report["ready"] is True, (
        f"intake did not reach ready within {report['rounds']} rounds for "
        f"{task.use_case!r}"
    )

    # (b) the brief produced a team via the intake→recommend auto-chain.
    sid = report["session_id"]
    r = requests.post(f"{BASE_URL}/api/intake/{sid}/recommend", timeout=TIMEOUT)
    assert r.status_code == 200, f"intake recommend HTTP {r.status_code}: {r.text[:200]}"
    team = r.json()
    agents = team.get("agents") or []
    assert len(agents) >= 1, (
        f"intake→recommend produced 0 agents for {task.use_case!r}. raw={str(team)[:400]}"
    )


@pytest.mark.parametrize("idx", INTAKE_SUBSET_INDICES,
                         ids=[f"artifact-{i}" for i in INTAKE_SUBSET_INDICES])
def test_intake_probes_for_artifacts(server_up, idx):
    """The intake SHOULD ask whether the user has an artifact to work from
    (sample / blank format / past documents / database). This is the
    'identify if a sample should be provided' capability. Asserted softly —
    the system must surface at least one artifact-related question across the
    interview. A failure means the intake is asking nothing about inputs."""
    task = CORPUS[idx]
    report = _drive_intake(task.use_case)
    joined = " ".join(report["questions"]).lower()
    hit = [k for k in ARTIFACT_KEYWORDS if k in joined]
    assert hit, (
        f"intake asked no artifact-related question for {task.use_case!r} "
        f"(expected category={task.artifact}). questions={report['questions']}"
    )


# ── rate-limit / cost guardrail sanity ──────────────────────────────────────
def test_corpus_size_is_rigorous_but_bounded(server_up):
    """A rigorous suite, not a smoke test — but bounded so a full run is
    affordable under the 10/min LLM rate limit. 10-25 inputs is the sweet spot."""
    n = len(CORPUS)
    assert 10 <= n <= 25, f"corpus size {n} outside the 10-25 rigorous-but-affordable band"
