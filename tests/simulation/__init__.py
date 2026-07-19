"""Human-user simulation harness for Team247.

Simulates plain-language human users (one persona set spanning every catalog
sector) driving the live app over HTTP — both the adaptive intake interview
and the SPA's one-shot recommend — then judges every recommended team with a
deterministic structural pass plus an LLM rubric, and aggregates the results
into a per-sector markdown report.

Run (server must be up; raise its rate limit for full runs):
    RATE_LIMIT_PER_MIN=60 uvicorn app.main:app          # terminal 1
    python -m tests.simulation --personas 100 --track both --resume

Not collected by pytest (no test_* module names); the thin smoke wrapper
lives at tests/test_simulation_smoke.py and is skipped unless RUN_SIM=1.

Simulator/judge LLM calls reuse the project's endpoint + key via
app.llm_contracts.call_llm_json with purposes sim_persona / sim_user /
sim_judge, so models are overridable with LLM_MODEL_SIM_* env rows.
"""
