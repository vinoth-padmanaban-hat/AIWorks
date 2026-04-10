"""
Planner — decides which skills to run and in what order.

Given:
  - goal (user/task request)
  - persona context
  - available skills (already filtered by policy)

Produces:
  - Ordered list of PlanStep (skill DAG — sequential for PoC).

Implementation:
  - LLM-based planning with structured JSON output.
  - Falls back to heuristic matching if LLM fails.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

from langchain_openai import ChatOpenAI

from app.core.config import settings
from app.core.langfuse_setup import langfuse_trace
from app.domain.personas.models import PersonaSnapshot
from app.domain.policy.models import EffectivePolicy
from app.domain.registries.skill_registry import SkillManifest

logger = logging.getLogger(__name__)


@dataclass
class PlanStep:
    step_id: uuid.UUID = field(default_factory=uuid.uuid4)
    skill_id: str = ""
    input_spec: dict[str, Any] = field(default_factory=dict)
    depends_on: list[uuid.UUID] = field(default_factory=list)


_PLANNER_PROMPT = """\
You are a task planner for a multi-tenant AI coworker platform.

Given a GOAL, a PERSONA context, and a list of AVAILABLE SKILLS,
produce an ordered plan of skills to execute.

IMPORTANT RULES:
- Only use skills from the AVAILABLE SKILLS list.
- Each step has a skill_id and optional input_spec (key-value hints).
- Order matters: steps run sequentially.
- Be concise: use the minimum number of steps needed.
- If only one skill matches the goal, return a single-step plan.
- If the goal requires content ingestion + curation, chain them appropriately.

PERSONA:
{persona_block}

AVAILABLE SKILLS:
{skills_block}

GOAL:
{goal}

Respond with ONLY a valid JSON array. Each element:
{{"skill_id": "...", "input_spec": {{}}}}

No markdown fences. No explanation. Just the JSON array."""


class Planner:
    def __init__(self) -> None:
        self._llm = ChatOpenAI(
            model=settings.openai_model,
            api_key=settings.openai_api_key,
            temperature=0,
        )

    async def plan(
        self,
        *,
        goal: str,
        persona: PersonaSnapshot,
        available_skills: list[SkillManifest],
        effective_policy: EffectivePolicy | dict[str, Any],
        tenant_id: uuid.UUID | None = None,
        execution_id: uuid.UUID | None = None,
    ) -> list[PlanStep]:
        """Produce an ordered plan of skills to execute for the given goal."""
        if not available_skills:
            return []

        # If only one skill available, skip LLM planning
        if len(available_skills) == 1:
            return [PlanStep(skill_id=available_skills[0].skill_id)]

        try:
            return await self._llm_plan(
                goal,
                persona,
                available_skills,
                tenant_id=tenant_id,
                execution_id=execution_id,
            )
        except Exception as exc:
            logger.warning("LLM planning failed (%s), falling back to heuristic", exc)
            return self._heuristic_plan(goal, available_skills)

    async def _llm_plan(
        self,
        goal: str,
        persona: PersonaSnapshot,
        skills: list[SkillManifest],
        *,
        tenant_id: uuid.UUID | None,
        execution_id: uuid.UUID | None,
    ) -> list[PlanStep]:
        persona_block = (
            f"Name: {persona.display_name}\n"
            f"Role: {persona.role_description}\n"
            f"Tone: {persona.tone_style}\n"
            f"Goals: {', '.join(str(g) for g in persona.goals[:3])}"
        )
        skills_block = "\n".join(
            f"- {s.skill_id}: {s.description} (domain: {s.domain}, tags: {s.tags})"
            for s in skills
        )

        prompt = _PLANNER_PROMPT.format(
            persona_block=persona_block,
            skills_block=skills_block,
            goal=goal,
        )

        if tenant_id is not None and execution_id is not None:
            with langfuse_trace(
                tenant_id=tenant_id,
                execution_id=execution_id,
                service="control-plane",
                skill_id="planner",
            ) as lf_cfg:
                response = await self._llm.ainvoke(prompt, config=lf_cfg)
        else:
            response = await self._llm.ainvoke(prompt)
        content = response.content.strip()

        # Strip markdown fences if present
        if content.startswith("```"):
            lines = content.split("\n")
            content = "\n".join(
                l for l in lines if not l.strip().startswith("```")
            )

        parsed: list[dict[str, Any]] = json.loads(content)

        # Validate: only allow skills from the available set
        valid_ids = {s.skill_id for s in skills}
        steps: list[PlanStep] = []
        for item in parsed:
            sid = item.get("skill_id", "")
            if sid in valid_ids:
                steps.append(
                    PlanStep(
                        skill_id=sid,
                        input_spec=item.get("input_spec", {}),
                    )
                )
            else:
                logger.warning("Planner hallucinated skill_id=%s, skipping", sid)

        if not steps:
            logger.warning("LLM plan produced no valid steps, falling back")
            return self._heuristic_plan("", skills)

        return steps

    def _heuristic_plan(
        self,
        goal: str,
        skills: list[SkillManifest],
    ) -> list[PlanStep]:
        """Simple fallback: match skills by keyword overlap with the goal."""
        goal_lower = goal.lower()
        scored: list[tuple[int, SkillManifest]] = []

        for skill in skills:
            score = 0
            for word in skill.skill_id.split("_"):
                if word in goal_lower:
                    score += 2
            for tag in skill.tags:
                if tag in goal_lower:
                    score += 1
            if skill.name.lower() in goal_lower:
                score += 3
            scored.append((score, skill))

        scored.sort(key=lambda x: x[0], reverse=True)

        # Take skills with score > 0, or all if none match
        matched = [s for score, s in scored if score > 0]
        if not matched:
            matched = [s for _, s in scored]

        return [PlanStep(skill_id=s.skill_id) for s in matched]
