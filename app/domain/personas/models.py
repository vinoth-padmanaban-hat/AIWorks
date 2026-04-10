"""Persona Store domain models (control plane)."""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, Field


class PersonaRecord(BaseModel):
    """Row from `personas` — control plane."""

    persona_id: uuid.UUID
    tenant_id: uuid.UUID
    display_name: str
    slug: str = ""
    role_description: str = ""
    tone_style: str = ""
    goals: list[Any] = Field(default_factory=list)
    constraints: dict[str, Any] = Field(default_factory=dict)
    default_skills: list[str] = Field(default_factory=list)
    guardrail_profile: str = ""
    active: bool = True
    is_default: bool = False


class PersonaSnapshot(BaseModel):
    """Subset sent to agents on the wire (AgentInvocationContext.persona)."""

    persona_id: uuid.UUID
    tenant_id: uuid.UUID
    display_name: str
    slug: str = ""
    role_description: str = ""
    tone_style: str = ""
    goals: list[Any] = Field(default_factory=list)
    constraints: dict[str, Any] = Field(default_factory=dict)
    default_skills: list[str] = Field(default_factory=list)
    guardrail_profile: str = ""

    @classmethod
    def from_record(cls, r: PersonaRecord) -> PersonaSnapshot:
        return cls(
            persona_id=r.persona_id,
            tenant_id=r.tenant_id,
            display_name=r.display_name,
            slug=r.slug,
            role_description=r.role_description,
            tone_style=r.tone_style,
            goals=list(r.goals) if r.goals else [],
            constraints=dict(r.constraints) if r.constraints else {},
            default_skills=list(r.default_skills),
            guardrail_profile=r.guardrail_profile,
        )


def build_persona_summary(snapshot: PersonaSnapshot) -> str:
    """Compact string for logs, tracing, and agents that only need a summary."""
    goals_txt = ""
    if snapshot.goals:
        if isinstance(snapshot.goals[0], str):
            goals_txt = "; ".join(str(g) for g in snapshot.goals[:5])
        else:
            goals_txt = str(snapshot.goals[:3])
    parts = [
        f"{snapshot.display_name}",
        snapshot.role_description.strip() or "coworker",
        f"tone: {snapshot.tone_style}" if snapshot.tone_style else "",
        f"goals: {goals_txt}" if goals_txt else "",
    ]
    return " | ".join(p for p in parts if p)
