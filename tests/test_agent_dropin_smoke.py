"""Thin pytest wrapper for the agent drop-in eval — opt-in only.

The full harness is run via `python -m tests.agent_dropin` (see that
package); this wrapper exists so CI can smoke one archetype end-to-end when
explicitly asked. Skipped unless RUN_DROPIN=1: it makes real LLM calls
(bundle generation + comprehensiveness judge) via the app's own service
layer — no HTTP server needed. Test 2 (the full claude-CLI execution path)
additionally requires RUN_DROPIN_EXEC=1 and a working `claude` binary.

    RUN_DROPIN=1 pytest tests/test_agent_dropin_smoke.py -v
"""
from __future__ import annotations

import os

import pytest

# Keep harness imports inside the test: importing tests.agent_dropin pulls in
# root config.py, which needs .env at import time.
pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_DROPIN") != "1",
    reason="agent drop-in smoke is opt-in — set RUN_DROPIN=1 (real LLM calls)",
)


class _Args:
    """Minimal stand-in for the argparse.Namespace harness.run_archetype reads."""
    def __init__(self, **kw):
        self.claude_bin = "claude"
        self.claude_model = "sonnet"
        self.timeout = 300
        self.skip_exec = True
        self.force_regen = False
        for k, v in kw.items():
            setattr(self, k, v)


def test_credit_ops_skip_exec_end_to_end():
    """Comprehensiveness path only — no claude CLI, no server. Exercises
    team_service.recommend -> handoff_service.wire -> compose_team_bundles ->
    deterministic_checks -> judge_comprehensiveness (purpose
    "dropin_comp_judge") against the live app DB (data/agentproof.db)."""
    from tests.agent_dropin import harness as harness_mod
    from tests.agent_dropin import scenarios as scenarios_mod

    scenario = scenarios_mod.BY_ID["credit_ops"]
    args = _Args(skip_exec=True)
    record = harness_mod.run_archetype(scenario, args, run_id="smoke")

    assert record["status"] == "ok", record.get("error")
    assert record["deterministic"] is not None
    assert record["deterministic"]["checks"].get("well_formed"), record["deterministic"]["errors"]
    assert record["harness"]["status"] == "skipped"
    assert record["deliverable"] is None
    assert record["work_judge"] is None
    if record["comp_judge"] is not None:
        assert 1 <= record["comp_judge"]["overall"] <= 5


def test_one_archetype_full_path_end_to_end():
    """Full path: also runs the claude CLI headless and the work judge.
    Extra gate on top of RUN_DROPIN=1: RUN_DROPIN_EXEC=1, since this needs a
    working `claude` binary + auth and is slower/costlier."""
    if os.environ.get("RUN_DROPIN_EXEC") != "1":
        pytest.skip("full claude-CLI path is opt-in — set RUN_DROPIN_EXEC=1")

    from tests.agent_dropin import harness as harness_mod
    from tests.agent_dropin import scenarios as scenarios_mod

    scenario = scenarios_mod.BY_ID["credit_ops"]
    args = _Args(skip_exec=False)
    record = harness_mod.run_archetype(scenario, args, run_id="smoke-exec")

    assert record["status"] == "ok", record.get("error")
    assert record["harness"]["status"] in ("ok", "harness_error", "harness_timeout")
    if record["harness"]["status"] == "ok":
        assert record["deliverable"]
        if record["work_judge"] is not None:
            assert 1 <= record["work_judge"]["overall"] <= 5
