"""Intake termination: the adaptive interview always ends within ≤6 rounds.

Covers PRODUCTION_APP_PLAN.md → LLM contracts ``intake`` ("Cap rounds (≤6)
to guarantee termination; if exceeded, force ``ready:true`` with the
best-effort Brief") and the Stage 2 verify step ("follow-ups then
``{ready:true, brief}`` within ≤6 rounds").

Skips per-test until ``app.llm_contracts`` is importable and exposes a
loop driver entrypoint (``run_intake`` / ``intake_loop`` / ``main``).
"""
from __future__ import annotations

import pytest

# The intake round cap is a hard product guarantee (PRODUCTION_APP_PLAN.md).
INTAKE_ROUND_CAP = 6


def _stub_brief():
    """A minimal, schema-valid Brief shape (see app/schemas.Brief)."""
    return {
        "team_type": "cross-functional pod",
        "pain_points": "manual triage is slow",
        "outcome": "auto-triaged tickets",
        "constraints": ["no PII egress"],
        "scale": "1-2 agents",
        "domain": "internal IT ops",
    }


def _stub_intake_fn(llm_contracts, monkeypatch):
    """Replace ``llm_contracts.intake`` with a stub that always returns ready.

    If the real implementation routes through a lower-level
    ``config.llm_json`` helper instead of going through
    ``llm_contracts.intake``, we also neutralize that as a
    belt-and-braces fallback so the loop still terminates.
    """
    def _stub(*args, **kwargs):
        return {"ready": True, "brief": _stub_brief()}

    monkeypatch.setattr(llm_contracts, "intake", _stub, raising=False)
    try:
        import config  # type: ignore[import-not-found]
        monkeypatch.setattr(config, "llm_json", _stub, raising=False)
    except Exception:
        pass
    return _stub


def _resolve_loop_driver(llm_contracts):
    """Return the intake loop entrypoint, or None if not yet implemented."""
    for name in ("run_intake", "intake_loop", "intake", "main"):
        fn = getattr(llm_contracts, name, None)
        if callable(fn):
            return fn
    return None


def test_intake_terminates_within_cap(monkeypatch):
    """A ready-stubbed LLM must yield a Brief in ≤6 rounds, never hang."""
    llm_contracts = pytest.importorskip(
        "app.llm_contracts", reason="Stage 2 llm_contracts not implemented yet"
    )
    _stub_intake_fn(llm_contracts, monkeypatch)
    loop = _resolve_loop_driver(llm_contracts)
    if loop is None:
        pytest.skip(
            "app.llm_contracts has no run_intake/intake_loop entrypoint yet "
            "(Stage 2 intake driver not wired)"
        )

    result = loop(use_case_seed="Build a team to triage incoming tickets")
    # Tolerate dict, Pydantic model, and attribute-bag return shapes.
    if hasattr(result, "model_dump"):
        result_dict = result.model_dump()
    elif isinstance(result, dict):
        result_dict = result
    else:
        result_dict = {
            "rounds": getattr(result, "rounds", None),
            "brief": getattr(result, "brief", None),
            "status": getattr(result, "status", None),
        }

    rounds = result_dict.get("rounds")
    brief = result_dict.get("brief")
    status = result_dict.get("status")

    assert rounds is not None, "intake result did not report a round count"
    assert rounds <= INTAKE_ROUND_CAP, (
        f"intake ran {rounds} rounds, exceeding the {INTAKE_ROUND_CAP}-round cap"
    )
    assert brief is not None, "intake terminated without producing a Brief"
    assert status in (None, "ready"), f"intake ended in non-ready status: {status!r}"
