"""Pydantic models — single source of truth for the JSON shapes exchanged with the
LLM and the SPA. Permissive where the LLM's output varies (extra='allow') so model
variation doesn't break validation, but strict on the fields the guardrails depend on.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class Brief(BaseModel):
    """The structured brief produced at the end of the intake interview."""
    model_config = ConfigDict(extra="allow")
    team_type: Optional[str] = None
    pain_points: list[str] = Field(default_factory=list)
    outcome: Optional[str] = None
    domain: Optional[str] = None
    scale: Optional[str] = None
    constraints: list[str] = Field(default_factory=list)


class IntakeTurn(BaseModel):
    """One LLM turn in the intake interview: either ask more, or declare ready."""
    ready: bool
    questions: list[str] = Field(default_factory=list)
    brief: Optional[Brief] = None


class TeamRecommendAgent(BaseModel):
    n: int                       # 1-based index into the candidates list (guardrail)
    stage: int                   # 1 = first, increasing
    squad: str
    skill_level_overrides: dict[str, int] = Field(default_factory=dict)  # {code: 1..6}
    rationale: Optional[str] = None


class TeamRecommend(BaseModel):
    team: list[TeamRecommendAgent]


class Handoff(BaseModel):
    """One wired seam between agents (Stage 3)."""
    from_agent: str = Field(alias="from")
    to_agent: str = Field(alias="to")
    ceremony: str
    artifact: Optional[str] = None
    description: Optional[str] = None

    model_config = ConfigDict(populate_by_name=True)


class WireHandoffs(BaseModel):
    handoffs: list[Handoff]
