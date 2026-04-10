"""
Shared contract between the Execution Engine (control plane)
and every agent service (data plane).

Both sides import from this module so the HTTP interface stays in sync.
"""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, Field

from app.domain.personas.models import PersonaSnapshot


# ── Request / Response for the generic /execute endpoint ────────────────────


class ExecuteRequest(BaseModel):
    """POST /execute body — the generic entry point for all tenant work."""

    tenant_id: uuid.UUID
    persona_id: uuid.UUID | None = None
    goal: str = Field(
        ...,
        description="Natural-language goal or query describing what the coworker should do.",
        min_length=1,
    )
    skill_ids: list[str] | None = Field(
        default=None,
        description="Optional explicit skill IDs to use. If omitted, the planner decides.",
    )


class ExecuteStepResult(BaseModel):
    step_id: uuid.UUID
    skill_id: str
    status: str
    output: dict[str, Any] = Field(default_factory=dict)
    cost: CostMetrics = Field(default_factory=lambda: CostMetrics())
    error: str | None = None
    inline_eval: dict[str, Any] | None = Field(
        default=None,
        description="Inline LLM-judge metadata and per-attempt scores when enabled.",
    )


class ExecuteResponse(BaseModel):
    """Returned by POST /execute."""

    execution_id: uuid.UUID
    tenant_id: uuid.UUID
    status: str  # "SUCCESS" | "PARTIAL" | "ERROR"
    goal: str = ""
    plan: list[dict[str, Any]] = Field(default_factory=list)
    steps: list[ExecuteStepResult] = Field(default_factory=list)
    cost: CostMetrics = Field(default_factory=lambda: CostMetrics())
    error: str | None = None


# ── Agent invocation contracts (Execution Engine ↔ Agent Service) ──────────


class AgentInvocationContext(BaseModel):
    """
    Built by the Execution Engine and POSTed to the agent's /invoke endpoint.

    Carries tenant_id, execution_id, the resolved skill + its input, and the
    effective_policy snapshot so agents can enforce capability rules internally
    without a second DB round-trip.
    """

    execution_id: uuid.UUID
    step_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    tenant_id: uuid.UUID
    skill_id: str
    skill_input: dict[str, Any] = Field(default_factory=dict)
    goal: str = ""
    persona_id: uuid.UUID | None = None
    persona: PersonaSnapshot | None = None
    persona_summary: str | None = None
    trace_id: str | None = None
    effective_policy: dict[str, Any] = Field(default_factory=dict)


class CostMetrics(BaseModel):
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    duration_ms: int = 0


class AgentInvocationResult(BaseModel):
    """Returned from the agent service back to the Execution Engine."""

    execution_id: uuid.UUID
    step_id: uuid.UUID
    skill_id: str
    status: str  # "SUCCESS" | "ERROR"
    output: dict[str, Any] = Field(default_factory=dict)
    cost_metrics: CostMetrics = Field(default_factory=CostMetrics)
    error: str | None = None
    inline_eval: dict[str, Any] | None = Field(
        default=None,
        description="Inline LLM-judge summary when control-plane eval is enabled.",
    )
