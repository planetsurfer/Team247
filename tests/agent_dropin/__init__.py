"""Agent drop-in eval: test generated SKILL.md bundles as working agents.

For 5 diverse agent archetypes, this harness (1) checks bundle
comprehensiveness with an LLM rubric, (2) actually RUNS the bundle as an
agent via the `claude` CLI headless on a realistic scenario with sample
inputs, and (3) judges the work product, emitting actionable
generator-improvement feedback (`skill_gaps`).

Run (repo root, DB at data/agentproof.db already seeded):
    python -m tests.agent_dropin --archetypes credit_ops
    python -m tests.agent_dropin --resume
    python -m tests.agent_dropin --report-only

Not collected by pytest (no test_* module names); the thin smoke wrapper
lives at tests/test_agent_dropin_smoke.py and is skipped unless
RUN_DROPIN=1.

LLM calls (bundle generation + both judges) reuse the project's endpoint +
key via app.llm_contracts.call_llm_json / config.llm_chat with purposes
skill_base / skill_overlay / dropin_comp_judge / dropin_work_judge, so
models are overridable with LLM_MODEL_<PURPOSE> env rows.
"""
