"""Thin pytest wrapper for the simulation harness — opt-in only.

The full harness is run via `python -m tests.simulation` (see that package);
this wrapper exists so CI can smoke one persona end-to-end when explicitly
asked. Skipped unless RUN_SIM=1: it makes real LLM calls (persona + judge +
the app's own pipeline) against the live server.

    RUN_SIM=1 TEAM247_BASE_URL=http://127.0.0.1:8000 pytest tests/test_simulation_smoke.py -v
"""
from __future__ import annotations

import os
import threading

import pytest

# Keep harness imports inside the test: importing tests.simulation pulls in
# root config.py, which needs .env at import time.
pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_SIM") != "1",
    reason="simulation smoke is opt-in — set RUN_SIM=1 (real LLM calls)",
)


def test_one_persona_oneshot_end_to_end():
    from tests.simulation.client import SimClient
    from tests.simulation import personas as personas_mod
    from tests.simulation import runner as runner_mod

    base_url = os.environ.get("TEAM247_BASE_URL", "http://127.0.0.1:8000")
    client = SimClient(base_url, target_rpm=8)
    if not client.health_ok():
        pytest.skip(f"live server not reachable at {base_url}")

    people = personas_mod.get_personas(client, n=1)
    assert people, "persona cache/generation produced nothing"
    record = runner_mod.run_conversation(
        client, people[0], "oneshot", run_id="smoke", stop_flag=threading.Event()
    )
    assert record["status"] == "ok", record.get("error")
    assert record["structural"]["passed"], record["structural"]["errors"]
    if record["judge"] is not None:
        assert 1 <= record["judge"]["overall"] <= 5
