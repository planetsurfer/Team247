"""Handoff DAG cycle semantics: non-feedback cycles rejected, feedback
back-edges accepted.

Covers PRODUCTION_APP_PLAN.md → LLM contracts ``wire_handoffs``:
"Cycle semantics: build a directed graph from all non-``feedback loop``
handoffs and run a topological sort — it must be *acyclic*. ``feedback
loop`` handoffs are the only allowed back-edges and are excluded from the
topo sort ... any other cycle is rejected with ``422``."

Also covers Stage 3 verify: "a non-feedback cycle rejected ``422``".

Skips per-test until ``app.llm_contracts`` is importable and exposes a
handoff validator entrypoint (``validate_handoffs`` /
``validate_handoff_graph`` / ``wire_handoffs``).
"""
from __future__ import annotations

import inspect

import pytest

# The only ceremony allowed to form a back-edge, per PRODUCTION_APP_PLAN.md.
_FEEDBACK_CEREMONY = "feedback loop"


def _resolve_validator(llm_contracts):
    """Return (callable, name) for the handoff-graph validator, or (None, None).

    Accepts any of the conventional names so the test stays valid as the
    module crystalizes: ``validate_handoffs``, ``wire_handoffs``,
    ``validate_handoff_graph``.
    """
    for name in ("validate_handoffs", "validate_handoff_graph", "wire_handoffs"):
        fn = getattr(llm_contracts, name, None)
        if callable(fn):
            return fn, name
    return None, None


def _call_validator(fn, handoffs, use_case="demo"):
    """Call the validator with whatever signature it actually exposes.

    Tries the rich form (handoffs, use_case) first, falling back to
    handoffs-only, so we exercise real cycle-detection logic regardless of
    which Stage-3 shape landed.
    """
    sig = inspect.signature(fn)
    if len(sig.parameters) >= 2:
        return fn(handoffs, use_case)
    return fn(handoffs)


def test_nonfeedback_cycle_rejected():
    """A 3-agent cycle made of non-feedback ceremonies must be rejected."""
    llm_contracts = pytest.importorskip(
        "app.llm_contracts", reason="Stage 3 llm_contracts not implemented yet"
    )
    fn, _name = _resolve_validator(llm_contracts)
    if fn is None:
        pytest.skip(
            "app.llm_contracts exposes no validate_handoffs/wire_handoffs "
            "helper yet (Stage 3 cycle validator not implemented)"
        )

    handoffs = [
        {"from": "a1", "to": "a2", "ceremony": "artifact handoff", "artifact": "spec"},
        {"from": "a2", "to": "a3", "ceremony": "review gate", "artifact": "review"},
        {"from": "a3", "to": "a1", "ceremony": "sign-off", "artifact": "signoff"},
        # Closing back-edge via a NON-feedback ceremony → must trip the validator.
    ]
    with pytest.raises(Exception):
        _call_validator(fn, handoffs)


def test_feedback_loop_backedge_accepted():
    """A ``feedback loop`` back-edge is the only allowed cycle and must pass."""
    llm_contracts = pytest.importorskip(
        "app.llm_contracts", reason="Stage 3 llm_contracts not implemented yet"
    )
    fn, _name = _resolve_validator(llm_contracts)
    if fn is None:
        pytest.skip(
            "app.llm_contracts exposes no validate_handoffs/wire_handoffs "
            "helper yet (Stage 3 cycle validator not implemented)"
        )

    handoffs = [
        {"from": "a1", "to": "a2", "ceremony": "artifact handoff", "artifact": "spec"},
        {"from": "a2", "to": "a3", "ceremony": "review gate", "artifact": "review"},
        {"from": "a3", "to": "a1", "ceremony": _FEEDBACK_CEREMONY, "artifact": "retro"},
        # The only back-edge is a feedback loop → DAG over non-feedback edges
        # is acyclic, so the validator must NOT raise.
    ]
    # Should not raise.
    _call_validator(fn, handoffs)
