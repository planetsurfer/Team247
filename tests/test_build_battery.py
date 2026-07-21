"""Battery top-20 + gallery receipts — Iteration 6 (user-value loop):
`python -m app.build_battery` (role selection, skip/force, zero-executable
skip, the generate+validate retry loop with deterministic bare-GRADE-format
repair added 2026-07-22 after a live check found 0/5 real generations
validated) and the gallery receipts summary read by GET /api/gallery[/{slug}]
from app.services.verify_service.get_receipts.

NO real LLM/sandbox call anywhere in this file: battery.generate_skill and
build_battery._run_reference_grader (the driver's own sandbox-execution
helper — see build_battery.py) and framework.get_context / framework.get_ka
are all monkeypatched to canned fast fakes (see `stub_battery`), mirroring
tests/test_gallery.py's `stub_pipeline` pattern for app.build_gallery. The
deterministic-repair unit tests (`_repair_bare_grade_grader` /
`_diagnose`) DO execute real (but trivial, hand-written, non-LLM) Python via
plain `exec()` — no sandbox subprocess, no LLM — to prove the text
transformation itself is correct. The gallery-receipts tests seed
verify_runs rows directly rather than running a real verify() (that's
covered by tests/test_gallery.py + verify_service's own tests) — this file
only covers get_receipts()'s shaping and the router's honest-absence
behaviour.
"""
from __future__ import annotations

import contextlib
import io
import json
import uuid

import pytest

import assess


# ── seeding helpers (mirror tests/test_gallery.py's `_seed_gallery_row`) ────
def _seed_role(role_id, role=None, sector="Sector", track="Track",
               now="2026-07-20T00:00:00+00:00"):
    from app import db

    role = role or f"Test Role {role_id}"  # roles.role is UNIQUE — default must vary per id
    db.execute(
        "INSERT INTO roles(role_id, role, sector, track, seeded_at) VALUES (?, ?, ?, ?, ?)",
        (role_id, role, sector, track, now),
    )


def _seed_role_skill(role_id, code, skill, required_level, is_executable=1):
    from app import db

    db.execute(
        "INSERT INTO role_skills(role_id, code, skill, skill_type, required_level, "
        "is_executable) VALUES (?, ?, ?, ?, ?, ?)",
        (role_id, code, skill, "Technical Skills", required_level, is_executable),
    )


def _seed_team(team_id, use_case="test use case", status="wired",
               now="2026-07-20T00:00:00+00:00"):
    from app import db

    db.execute(
        "INSERT INTO teams(team_id, use_case, status, recommendation_json, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (team_id, use_case, status, "{}", now),
    )


def _seed_team_agent(team_id, agent_id, role_id, stage=1, sort_order=1):
    from app import db

    db.execute(
        "INSERT INTO team_agents(team_id, agent_id, role_id, stage, sort_order) "
        "VALUES (?, ?, ?, ?, ?)",
        (team_id, agent_id, role_id, stage, sort_order),
    )


def _seed_battery_item(role_id, code, skill, required_level, status="ready",
                       source="seed", now="2026-07-20T00:00:00+00:00"):
    from app import db

    db.execute(
        "INSERT INTO card_battery_items(role_id, code, skill, required_level, task_prompt, "
        "grader_code, reference_code, source, status, created_at) "
        "VALUES (?, ?, ?, ?, 'old prompt', 'old grader', 'old ref', ?, ?, ?)",
        (role_id, code, skill, required_level, source, status, now),
    )


def _seed_gallery_row(slug, team_id, agent_id, **overrides):
    from app import db

    row = {
        "slug": slug, "label": "L", "blurb": "B", "use_case": "u",
        "team_id": team_id, "agent_id": agent_id,
        "bundle_md": "---\nname: t\ndescription: t. Use when t.\n---\n\nBody\n",
        "created_at": "2026-07-20T00:00:00+00:00",
    }
    row.update(overrides)
    db.execute(
        "INSERT INTO gallery_agents(slug, label, blurb, use_case, team_id, agent_id, "
        "bundle_md, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (row["slug"], row["label"], row["blurb"], row["use_case"], row["team_id"],
         row["agent_id"], row["bundle_md"], row["created_at"]),
    )
    return row


def _seed_verify_run(team_id, agent_id, *, exec_results, rubric_results, coverage_pct,
                     status="done", started_at="2026-07-20T00:00:00+00:00",
                     finished_at="2026-07-20T00:05:00+00:00"):
    from app import db

    # verify_runs.team_id REFERENCES teams(team_id) — seed a minimal parent
    # row first (idempotent: only if this team_id hasn't been seeded yet).
    if db.query("SELECT 1 FROM teams WHERE team_id = ?", (team_id,), one=True) is None:
        _seed_team(team_id)

    db.execute(
        "INSERT INTO verify_runs(verify_run_id, team_id, agent_id, status, results_json, "
        "rubric_json, coverage_pct, started_at, finished_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (uuid.uuid4().hex, team_id, agent_id, status, json.dumps(exec_results),
         json.dumps(rubric_results), coverage_pct, started_at, finished_at),
    )


# ── fake generation+validation (no real LLM/sandbox) ────────────────────────
@pytest.fixture()
def stub_battery(monkeypatch):
    """Canned fast fakes for battery.generate_skill / build_battery's OWN
    _run_reference_grader (the driver's sandbox-execution helper — see
    build_battery.py; battery._validate_item is untouched/still real, but the
    retry-aware driver doesn't call it) / framework.get_context /
    framework.get_ka — patched on the shared module objects
    `build_battery.battery` / `build_battery.framework` reference, and on
    `build_battery` itself, so no real LLM call and no real xlsx scan or
    sandbox spawn happens in this file.

    Returns (calls, control):
      calls["generate_skill"] / calls["run_reference_grader"] count invocations.
      calls["hints"] / calls["purposes"] record each generate_skill call's
        retry_hint / purpose kwargs, in order — lets a test assert the
        failure-reason-as-feedback and purpose="battery_gen" routing.
      control["results"]: an optional QUEUE of (score, raw_stdout, err)
        3-tuples consumed one per _run_reference_grader call, in order — lets
        a test script an exact attempt sequence (e.g. bare-format-then-fixed,
        or self-inconsistent-every-time).
      control["default"]: the (score, raw, err) returned once the queue is
        empty (defaults to a passing `GRADE:{"score": 1.0}` result, i.e.
        'ready' on the very first attempt unless a test pushes onto results).
    """
    from app import build_battery

    calls = {"generate_skill": 0, "run_reference_grader": 0, "hints": [], "purposes": []}
    control = {"results": [], "default": (1.0, 'GRADE:{"score": 1.0}', None)}

    def _fake_generate_skill(role, skill, required_level, context, ka, task_material,
                             retry_hint=None, purpose=None):
        calls["generate_skill"] += 1
        calls["hints"].append(retry_hint)
        calls["purposes"].append(purpose)
        return {
            "task_prompt": f"solve a {skill} task",
            "grader_code": 'print(\'GRADE:{"score": 1.0}\')',
            "reference_code": "def solve():\n    return 1\n",
        }

    def _fake_run_reference_grader(runner, item):
        calls["run_reference_grader"] += 1
        if control["results"]:
            return control["results"].pop(0)
        return control["default"]

    def _fake_get_context(role):
        return {"description": "", "performance_expectation": "", "critical_work_functions": []}

    def _fake_get_ka(code, level):
        return {"proficiency_description": "", "items": []}

    monkeypatch.setattr(build_battery.battery, "generate_skill", _fake_generate_skill)
    monkeypatch.setattr(build_battery, "_run_reference_grader", _fake_run_reference_grader)
    monkeypatch.setattr(build_battery.framework, "get_context", _fake_get_context)
    monkeypatch.setattr(build_battery.framework, "get_ka", _fake_get_ka)
    return calls, control


# ── role selection ───────────────────────────────────────────────────────────
def test_top_role_ids_orders_by_usage_desc(tmp_db):
    from app import build_battery

    for rid in (100, 200, 300):
        _seed_role(rid)
    for i, (team_id, role_id) in enumerate([
        ("t1", 100), ("t2", 100), ("t3", 100),   # role 100: 3 uses
        ("t4", 200), ("t5", 200),                # role 200: 2 uses
        ("t6", 300),                              # role 300: 1 use
    ]):
        _seed_team(team_id)
        _seed_team_agent(team_id, f"a{i}", role_id)

    assert build_battery._top_role_ids(2) == [100, 200]
    assert build_battery._top_role_ids(10) == [100, 200, 300]


def test_select_role_ids_top_n_plus_gallery_union(tmp_db):
    from app import build_battery

    for rid in (100, 200, 300):
        _seed_role(rid)
    for i, (team_id, role_id) in enumerate([
        ("t1", 100), ("t2", 100), ("t3", 100),   # role 100: 3 uses -> top
        ("t4", 200), ("t5", 200),                # role 200: 2 uses -> top
        ("t6", 300),                              # role 300: 1 use -> NOT in top-2, but gallery-linked
    ]):
        _seed_team(team_id)
        _seed_team_agent(team_id, f"a{i}", role_id)

    # Gallery row points at (t6, a5) -> role_id 300.
    _seed_gallery_row("some-archetype", "t6", "a5")

    selected = build_battery.select_role_ids(top=2)
    # top-2 by usage (100, 200) preserved in rank order, gallery-only 300 appended.
    assert selected == [100, 200, 300]


def test_select_role_ids_only_bypasses_top_and_gallery(tmp_db):
    from app import build_battery

    for rid in (100, 200):
        _seed_role(rid)
    for i, (team_id, role_id) in enumerate([("t1", 100), ("t2", 100), ("t3", 200)]):
        _seed_team(team_id)
        _seed_team_agent(team_id, f"a{i}", role_id)
    _seed_gallery_row("g", "t3", "a2")

    assert build_battery.select_role_ids(top=1, only={7, 5}) == [5, 7]


def test_parse_only_flattens_comma_and_repeats():
    from app import build_battery

    assert build_battery._parse_only(["1,2", "3"]) == {1, 2, 3}
    assert build_battery._parse_only(None) is None
    assert build_battery._parse_only([]) is None


# ── per-role build ────────────────────────────────────────────────────────────
def test_build_role_zero_executable_skills_reports_and_skips(tmp_db, stub_battery):
    from app import build_battery, db

    calls, _ = stub_battery
    _seed_role(1, role="No Exec Role")
    _seed_role_skill(1, "TSC-1", "Non-executable Skill", 3, is_executable=0)

    r = build_battery._build_role(1, limit_skills=3, force=False)

    assert r["no_executable"] is True
    assert calls["generate_skill"] == 0
    rows = db.query("SELECT * FROM card_battery_items WHERE role_id = ?", (1,))
    assert rows == []  # never fabricated


def test_build_role_unknown_role_id_reports_error(tmp_db, stub_battery):
    from app import build_battery

    r = build_battery._build_role(999, limit_skills=3, force=False)
    assert r["error"] == "unknown role_id"
    assert r["role"] is None


def test_build_role_validation_pass_status_ready(tmp_db, stub_battery):
    from app import build_battery, db

    _seed_role(1, role="Data Engineer")
    _seed_role_skill(1, "TSC-1", "Data Engineering", 4, is_executable=1)

    r = build_battery._build_role(1, limit_skills=3, force=False)

    assert r["generated"] == 1
    assert r["validated"] == 1
    assert r["invalid"] == 0
    row = db.query(
        "SELECT * FROM card_battery_items WHERE role_id = ? AND code = ?",
        (1, "TSC-1"), one=True,
    )
    assert row["status"] == "ready"
    assert row["source"] == "generated"


def test_build_role_validation_fail_status_invalid(tmp_db, stub_battery):
    """Every attempt is a genuine (proper-JSON) self-consistency miss — the
    retry loop exhausts all MAX_ATTEMPTS, ends 'invalid', and the LAST
    attempt's content is still persisted for audit (never dropped)."""
    from app import build_battery, db

    calls, control = stub_battery
    control["default"] = (0.2, 'GRADE:{"score": 0.2}', None)  # proper format, just fails
    _seed_role(1, role="Data Engineer")
    _seed_role_skill(1, "TSC-1", "Data Engineering", 4, is_executable=1)

    r = build_battery._build_role(1, limit_skills=3, force=False)

    assert r["generated"] == 1  # one SKILL attempted (internally retried 3x)
    assert r["validated"] == 0
    assert r["invalid"] == 1
    assert calls["generate_skill"] == build_battery.MAX_ATTEMPTS
    assert calls["run_reference_grader"] == build_battery.MAX_ATTEMPTS
    row = db.query(
        "SELECT * FROM card_battery_items WHERE role_id = ? AND code = ?",
        (1, "TSC-1"), one=True,
    )
    assert row["status"] == "invalid"  # kept for audit, never dropped silently


def test_build_role_generation_exception_counted_invalid_no_row(tmp_db, stub_battery, monkeypatch):
    """The LLM call itself always errors (never even produces content) — the
    retry loop still exhausts MAX_ATTEMPTS, ends 'invalid', but with no item
    to persist (nothing was ever generated to audit)."""
    from app import build_battery, db

    def _boom(*a, **k):
        raise RuntimeError("llm hiccup")

    monkeypatch.setattr(build_battery.battery, "generate_skill", _boom)
    _seed_role(1, role="Data Engineer")
    _seed_role_skill(1, "TSC-1", "Data Engineering", 4, is_executable=1)

    r = build_battery._build_role(1, limit_skills=3, force=False)

    assert r["generated"] == 1  # one SKILL attempted, even though it never yielded content
    assert r["validated"] == 0
    assert r["invalid"] == 1
    rows = db.query("SELECT * FROM card_battery_items WHERE role_id = ?", (1,))
    assert rows == []  # nothing to persist — no fabricated content


def test_build_role_skips_existing_ready_row_unless_force(tmp_db, stub_battery):
    from app import build_battery, db

    calls, _ = stub_battery
    _seed_role(1, role="Data Engineer")
    _seed_role_skill(1, "TSC-1", "Data Engineering", 4, is_executable=1)
    _seed_battery_item(1, "TSC-1", "Data Engineering", 4, status="ready")

    r = build_battery._build_role(1, limit_skills=3, force=False)
    assert r["skipped"] == 1
    assert r["generated"] == 0
    assert calls["generate_skill"] == 0
    row = db.query(
        "SELECT * FROM card_battery_items WHERE role_id = ? AND code = ?",
        (1, "TSC-1"), one=True,
    )
    assert row["task_prompt"] == "old prompt"  # untouched by the skip


def test_build_role_force_regenerates_existing_ready_row(tmp_db, stub_battery):
    from app import build_battery, db

    calls, _ = stub_battery
    _seed_role(1, role="Data Engineer")
    _seed_role_skill(1, "TSC-1", "Data Engineering", 4, is_executable=1)
    _seed_battery_item(1, "TSC-1", "Data Engineering", 4, status="ready")

    r = build_battery._build_role(1, limit_skills=3, force=True)
    assert r["generated"] == 1
    assert r["validated"] == 1
    assert calls["generate_skill"] == 1
    rows = db.query("SELECT * FROM card_battery_items WHERE role_id = ? AND code = ?", (1, "TSC-1"))
    assert len(rows) == 1  # replaced, not duplicated (UNIQUE(role_id, code))
    assert rows[0]["task_prompt"] != "old prompt"


def test_build_role_limit_skills_caps_generation_count(tmp_db, stub_battery):
    from app import build_battery, db

    calls, _ = stub_battery
    _seed_role(1, role="Data Engineer")
    for i in range(5):
        _seed_role_skill(1, f"TSC-{i}", f"Skill {i}", 3, is_executable=1)

    r = build_battery._build_role(1, limit_skills=2, force=False)
    assert r["generated"] == 2
    assert calls["generate_skill"] == 2
    rows = db.query("SELECT * FROM card_battery_items WHERE role_id = ?", (1,))
    assert len(rows) == 2


# ── retry loop + deterministic bare-GRADE-format repair (2026-07-22 follow-up) ──
# Diagnosed live: 5/5 real kimi-k2.6 generations landed 'invalid', split
# between two failure modes — grader_code's last line was a BARE number
# (`GRADE:0.5`) instead of the required `GRADE:{"score": 0.5}` JSON, or
# reference_code genuinely didn't satisfy its own grader_code. These tests
# cover both the pure diagnostic/repair functions (real exec(), no
# LLM/sandbox) and the retry-loop driver (`_generate_and_validate`, fully
# monkeypatched via `stub_battery`).
def test_diagnose_classifies_bare_grade_format():
    from app import build_battery

    assert build_battery._diagnose("some setup\nGRADE:0.1667\n") == "bare_grade_format"
    assert build_battery._diagnose("GRADE:1\n") == "bare_grade_format"
    assert build_battery._diagnose("GRADE:-0.5\n") == "bare_grade_format"


def test_diagnose_classifies_self_inconsistent():
    from app import build_battery

    assert build_battery._diagnose('GRADE:{"score": 0.2}\n') == "self_inconsistent"
    assert build_battery._diagnose('noise\nGRADE:{"score": 1.0}\n') == "self_inconsistent"


def test_diagnose_classifies_unknown():
    from app import build_battery

    assert build_battery._diagnose("no grade line here at all") == "unknown"
    assert build_battery._diagnose("") == "unknown"
    assert build_battery._diagnose('GRADE:["not", "a", "dict"]') == "unknown"


def test_repair_bare_grade_grader_fixes_bare_format():
    """Real exec() of the repaired code (no sandbox, no LLM — pure, fast,
    deterministic text transform): a grader whose own last print is a bare
    float becomes parseable by the SAME assess.parse_grade the real exec
    track / battery._validate_item use, without altering the original score."""
    from app import build_battery

    bare_grader = (
        "def run_tests():\n"
        "    passed, total = 1, 1\n"
        "    score = passed / total\n"
        '    print(f"GRADE:{score}")\n'
        "\n"
        "run_tests()\n"
    )
    # Unrepaired: garbled (parse_grade requires a JSON dict with a 'score' key).
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        exec(bare_grader, {})
    unrepaired_score, unrepaired_err = assess.parse_grade(buf.getvalue())
    assert unrepaired_score == 0.0
    assert unrepaired_err is not None

    repaired = build_battery._repair_bare_grade_grader(bare_grader)
    buf2 = io.StringIO()
    with contextlib.redirect_stdout(buf2):
        exec(repaired, {})
    score, err = assess.parse_grade(buf2.getvalue())
    assert score == 1.0
    assert err is None


def test_repair_bare_grade_grader_leaves_compliant_grader_unaffected():
    """A grader that's ALREADY compliant must parse to the identical score
    after being wrapped — the repair harness is a no-op for good input."""
    from app import build_battery

    good_grader = (
        "import json\n"
        'print("GRADE:" + json.dumps({"score": 0.75}))\n'
    )
    wrapped = build_battery._repair_bare_grade_grader(good_grader)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        exec(wrapped, {})
    score, err = assess.parse_grade(buf.getvalue())
    assert score == 0.75
    assert err is None


def test_generate_and_validate_ready_on_first_attempt(tmp_db, stub_battery):
    from app import build_battery

    calls, control = stub_battery
    result = build_battery._generate_and_validate(
        "Data Engineer", "Data Engineering", "TSC-1", 4, {}, {}, None, runner=object(),
    )
    assert result["status"] == "ready"
    assert result["attempts"] == 1
    assert result["reason"] is None
    assert calls["generate_skill"] == 1
    assert calls["hints"] == [None]  # first attempt never carries a retry hint


def test_generate_and_validate_deterministic_repair_avoids_extra_llm_call(tmp_db, stub_battery):
    """The #1 diagnosed failure mode: bare-GRADE-format on attempt 1. The
    driver must repair + re-validate WITHOUT a second LLM call — i.e.
    generate_skill is called exactly ONCE even though two sandbox runs
    happened (raw, then repaired)."""
    from app import build_battery

    calls, control = stub_battery
    control["results"] = [
        (0.0, "GRADE:0.1667", None),                  # attempt 1, raw: bare format
        (1.0, 'GRADE:{"score": 1.0}', None),           # attempt 1, post-repair: fixed
    ]
    result = build_battery._generate_and_validate(
        "Data Engineer", "Data Engineering", "TSC-1", 4, {}, {}, None, runner=object(),
    )
    assert result["status"] == "ready"
    assert result["attempts"] == 1
    assert calls["generate_skill"] == 1  # repair succeeded — no extra LLM call needed
    assert calls["run_reference_grader"] == 2  # raw + repaired


def test_generate_and_validate_repair_fails_then_llm_retries_and_succeeds(tmp_db, stub_battery):
    """Bare-format on attempt 1, but the repair doesn't fix it (reference is
    ALSO wrong) — falls through to a genuine LLM retry, which succeeds on
    attempt 2. The failure reason from attempt 1 must be fed back as
    generate_skill's retry_hint on attempt 2."""
    from app import build_battery

    calls, control = stub_battery
    control["results"] = [
        (0.0, "GRADE:0.1667", None),                  # attempt 1, raw: bare format
        (0.3, "GRADE:0.1667", None),                  # attempt 1, post-repair: still fails
        (1.0, 'GRADE:{"score": 1.0}', None),           # attempt 2, raw: fixed
    ]
    result = build_battery._generate_and_validate(
        "Data Engineer", "Data Engineering", "TSC-1", 4, {}, {}, None, runner=object(),
    )
    assert result["status"] == "ready"
    assert result["attempts"] == 2
    assert calls["generate_skill"] == 2
    assert calls["hints"][0] is None
    assert calls["hints"][1] is not None
    assert "bare" in calls["hints"][1] or "GRADE" in calls["hints"][1]


def test_generate_and_validate_retries_on_self_inconsistent_then_succeeds(tmp_db, stub_battery):
    from app import build_battery

    calls, control = stub_battery
    control["results"] = [
        (0.2, 'GRADE:{"score": 0.2}', None),   # attempt 1: proper format, just wrong
        (1.0, 'GRADE:{"score": 1.0}', None),   # attempt 2: fixed
    ]
    result = build_battery._generate_and_validate(
        "Data Engineer", "Data Engineering", "TSC-1", 4, {}, {}, None, runner=object(),
    )
    assert result["status"] == "ready"
    assert result["attempts"] == 2
    assert calls["generate_skill"] == 2
    assert calls["hints"][1] is not None
    assert "self-consist" in calls["hints"][1] or "own grader" in calls["hints"][1]


def test_generate_and_validate_exhausts_max_attempts_then_invalid(tmp_db, stub_battery):
    from app import build_battery

    calls, control = stub_battery
    control["default"] = (0.1, 'GRADE:{"score": 0.1}', None)  # never passes
    result = build_battery._generate_and_validate(
        "Data Engineer", "Data Engineering", "TSC-1", 4, {}, {}, None, runner=object(),
    )
    assert result["status"] == "invalid"
    assert result["attempts"] == build_battery.MAX_ATTEMPTS
    assert calls["generate_skill"] == build_battery.MAX_ATTEMPTS
    assert result["item"] is not None  # last attempt's content kept for audit
    assert result["reason"] is not None


def test_generate_and_validate_purpose_battery_gen_routed(tmp_db, stub_battery):
    """Purpose routing (Iteration 6 follow-up): the driver's generation calls
    use their own purpose="battery_gen" (independent LLM_MODEL_BATTERY_GEN
    override), distinct from battery.rubric_score's "battery_rubric" and the
    original battery.build_battery call site's default "battery_grader"."""
    from app import build_battery

    calls, control = stub_battery
    build_battery._generate_and_validate(
        "Data Engineer", "Data Engineering", "TSC-1", 4, {}, {}, None, runner=object(),
    )
    assert calls["purposes"] == ["battery_gen"]


def test_run_one_role_failure_does_not_abort_others(tmp_db, stub_battery, monkeypatch):
    from app import build_battery, db

    _seed_role(1, role="Role A")
    _seed_role_skill(1, "A-1", "Skill A", 3, is_executable=1)
    _seed_role(2, role="Role B")
    _seed_role_skill(2, "B-1", "Skill B", 3, is_executable=1)

    orig = build_battery._build_role

    def _boom_for_role_1(role_id, limit_skills, force):
        if role_id == 1:
            raise RuntimeError("boom — simulated per-role failure")
        return orig(role_id, limit_skills, force)

    monkeypatch.setattr(build_battery, "_build_role", _boom_for_role_1)

    totals = build_battery.run(only={1, 2}, limit_skills=1)
    assert totals["failed_roles"] == 1
    assert totals["roles"] == 1
    assert totals["generated"] == 1
    rows = db.query("SELECT * FROM card_battery_items WHERE role_id = ?", (2,))
    assert len(rows) == 1
    assert db.query("SELECT * FROM card_battery_items WHERE role_id = ?", (1,)) == []


def test_run_totals_aggregate_across_roles(tmp_db, stub_battery):
    from app import build_battery

    _seed_role(1, role="Role A")
    _seed_role_skill(1, "A-1", "Skill A", 3, is_executable=1)
    _seed_role(2, role="Role B")
    # role 2 has no executable skills at all

    totals = build_battery.run(only={1, 2}, limit_skills=3)
    assert totals["roles"] == 2
    assert totals["generated"] == 1
    assert totals["validated"] == 1
    assert totals["no_executable"] == 1
    assert totals["failed_roles"] == 0
    assert len(totals["per_role"]) == 2


# ── gallery receipts: verify_service.get_receipts ───────────────────────────
def test_get_receipts_none_when_no_verify_run(tmp_db):
    from app.services import verify_service

    assert verify_service.get_receipts("team-x", "a1") is None


def test_get_receipts_none_when_status_not_done(tmp_db):
    from app.services import verify_service

    _seed_verify_run("team-x", "a1", exec_results=[], rubric_results=[], coverage_pct=None,
                     status="failed")
    assert verify_service.get_receipts("team-x", "a1") is None


def test_get_receipts_shapes_full_summary(tmp_db):
    from app.services import verify_service

    exec_results = [
        {"skill": "Data Engineering", "required_level": 4, "held_level": 4, "score": 1.0},
    ]
    rubric_results = [
        {"skill": "Rubric A", "pct": 80.0},
        {"skill": "Rubric B", "pct": 60.0},
        {"skill": "Rubric C", "error": "llm error"},  # excluded from the mean
    ]
    _seed_verify_run("team-1", "a1", exec_results=exec_results, rubric_results=rubric_results,
                     coverage_pct=100.0)

    r = verify_service.get_receipts("team-1", "a1")
    assert r == {
        "proven": True,
        "exec_skills": 1,
        "exec_avg_pct": 100.0,
        "rubric_pct": 70.0,
        "verified_at": "2026-07-20T00:05:00+00:00",
    }


def test_get_receipts_not_proven_when_only_rubric_track_ran(tmp_db):
    """§ documented pick: 'proven' requires actually-executed evidence — a
    verify run that only ran the rubric (LLM-judged) track, with zero
    executed skills, is never marked proven no matter how good its rubric
    score looks."""
    from app.services import verify_service

    _seed_verify_run("team-2", "a1", exec_results=[], rubric_results=[{"skill": "X", "pct": 90.0}],
                     coverage_pct=None)
    r = verify_service.get_receipts("team-2", "a1")
    assert r["proven"] is False
    assert r["exec_skills"] == 0
    assert r["exec_avg_pct"] is None
    assert r["rubric_pct"] == 90.0


# ── gallery receipts: HTTP surface (GET /api/gallery[/{slug}]) ─────────────
@pytest.fixture()
def beta_client(tmp_db, monkeypatch):
    """Mirrors tests/test_gallery.py's beta_client fixture (own copy — pytest
    fixtures aren't shared across files without a conftest change, and this
    keeps the two gallery test files independent)."""
    from app import settings

    monkeypatch.setattr(settings, "BETA_AUTH", True, raising=False)
    monkeypatch.setattr(settings, "BETA_TOKEN_SALT", "battery-test-salt", raising=False)
    monkeypatch.setattr(settings, "APP_ADMIN_TOKEN", "battery-test-admin-token", raising=False)

    from fastapi.testclient import TestClient

    from app.main import app as fastapi_app

    return TestClient(fastapi_app)


def test_gallery_list_shows_proven_and_exec_skills_when_present(beta_client):
    _seed_gallery_row("proven-agent", "team-1", "a1")
    _seed_verify_run(
        "team-1", "a1",
        exec_results=[{"skill": "X", "required_level": 3, "held_level": 3, "score": 1.0}],
        rubric_results=[], coverage_pct=100.0,
    )
    r = beta_client.get("/api/gallery")
    assert r.status_code == 200, r.text
    item = r.json()[0]
    assert item["proven"] is True
    assert item["exec_skills"] == 1
    # list gets ONLY the two-field teaser, never the full receipts summary.
    assert "exec_avg_pct" not in item
    assert "rubric_pct" not in item
    assert "verified_at" not in item


def test_gallery_list_omits_receipts_keys_when_absent(beta_client):
    """Documented pick: absent receipts means NO 'proven'/'exec_skills' keys
    at all on the list item (never a fabricated proven: false)."""
    _seed_gallery_row("unverified-agent", "team-2", "a1")
    r = beta_client.get("/api/gallery")
    assert r.status_code == 200, r.text
    item = r.json()[0]
    assert "proven" not in item
    assert "exec_skills" not in item
    assert set(item.keys()) == {"slug", "label", "blurb"}


def test_gallery_detail_full_receipts_when_present(beta_client):
    from app import auth

    _seed_gallery_row("proven-agent", "team-1", "a1")
    _seed_verify_run(
        "team-1", "a1",
        exec_results=[{"skill": "X", "required_level": 3, "held_level": 3, "score": 1.0}],
        rubric_results=[{"skill": "Y", "pct": 70.0}], coverage_pct=100.0,
    )
    token = auth.mint("battery-tester")
    r = beta_client.get("/api/gallery/proven-agent", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    receipts = r.json()["receipts"]
    assert receipts == {
        "proven": True, "exec_skills": 1, "exec_avg_pct": 100.0,
        "rubric_pct": 70.0, "verified_at": "2026-07-20T00:05:00+00:00",
    }


def test_gallery_detail_no_receipts_key_when_absent(beta_client):
    """Documented pick (mirrors the list): no 'receipts' key at all — never
    a fabricated {proven: false, ...} placeholder."""
    from app import auth

    _seed_gallery_row("unverified-agent", "team-2", "a1")
    token = auth.mint("battery-tester-2")
    r = beta_client.get("/api/gallery/unverified-agent", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    assert "receipts" not in r.json()
