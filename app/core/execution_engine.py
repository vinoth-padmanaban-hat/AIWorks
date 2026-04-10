"""
Execution Engine — the runtime broker of the control plane.

Responsibilities:
  1. Accept a skill_id + skill_input + tenant_id.
  2. Query Agent Registry for the best available agent that supports that skill.
  3. Build an AgentInvocationContext.
  4. Call the agent service via HTTP POST /invoke.
  5. Optionally run inline (online) LLM-judge eval, persist scores, retry up to
     `inline_eval_max_retries` times when the judge fails.
  6. Return the AgentInvocationResult to the caller (Orchestrator).

Agent endpoints are discovered at runtime from the agent_registry table.
Nothing is hardcoded.
"""

from __future__ import annotations

import logging
import os
import uuid
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.domain.models.invocation import (
    AgentInvocationContext,
    AgentInvocationResult,
    CostMetrics,
)
from app.domain.personas.models import PersonaSnapshot
from app.domain.policy.models import EffectivePolicy
from app.domain.registries.agent_registry import AgentRegistry
from app.eval.inline_judge import run_inline_judge
from app.eval.langfuse_inline import emit_inline_eval_score
from app.eval.output_text import extract_output_text_for_eval
from app.eval.storage import persist_inline_eval_attempt

logger = logging.getLogger(__name__)

# How long to wait for an agent to complete a skill invocation.
# Content ingestion can scrape many pages — give it plenty of room.
_AGENT_TIMEOUT_SECONDS = 600.0

_FEEDBACK_KEY = "_inline_eval_feedback"


class ExecutionEngine:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def execute_skill(
        self,
        *,
        skill_id: str,
        skill_input: dict[str, Any],
        tenant_id: uuid.UUID,
        execution_id: uuid.UUID,
        persona_id: uuid.UUID | None = None,
        persona: PersonaSnapshot | None = None,
        persona_summary: str | None = None,
        trace_id: str | None = None,
        effective_policy: EffectivePolicy | dict[str, Any] | None = None,
        goal: str = "",
    ) -> AgentInvocationResult:
        """
        Core dispatch method:
          - Resolve skill → agent via Agent Registry.
          - Call agent HTTP endpoint (with optional inline-eval retry loop).
          - Return structured result.
        """
        registry = AgentRegistry(self._db)
        agents = await registry.find_agents_for_skill(skill_id, tenant_id)

        if not agents:
            raise RuntimeError(
                f"No active agent registered for skill_id='{skill_id}'. "
                "Run scripts/register_agents.py to register agents."
            )

        agent = agents[0]
        step_id = uuid.uuid4()

        logger.info(
            "ExecutionEngine dispatching skill=%s to agent=%s (endpoint=%s) "
            "execution_id=%s step_id=%s",
            skill_id,
            agent.display_name,
            agent.endpoint,
            execution_id,
            step_id,
        )

        policy_dict = (
            effective_policy.raw
            if isinstance(effective_policy, EffectivePolicy)
            else (effective_policy or {})
        )

        working_input: dict[str, Any] = dict(skill_input)
        total_cost = CostMetrics()
        attempts_meta: list[dict[str, Any]] = []
        last_result: AgentInvocationResult | None = None

        judge_available = self._has_openai_for_judge()
        use_inline_loop = settings.inline_eval_enabled and judge_available
        max_attempts = (
            settings.inline_eval_max_retries + 1 if use_inline_loop else 1
        )

        async with httpx.AsyncClient(timeout=_AGENT_TIMEOUT_SECONDS) as client:
            for attempt in range(max_attempts):
                ctx = AgentInvocationContext(
                    execution_id=execution_id,
                    step_id=step_id,
                    tenant_id=tenant_id,
                    skill_id=skill_id,
                    skill_input=working_input,
                    goal=goal,
                    persona_id=persona_id,
                    persona=persona,
                    persona_summary=persona_summary,
                    trace_id=trace_id,
                    effective_policy=policy_dict,
                )

                last_result = await self._invoke_agent(client, agent.endpoint, ctx)
                total_cost.tokens_in += last_result.cost_metrics.tokens_in
                total_cost.tokens_out += last_result.cost_metrics.tokens_out
                total_cost.cost_usd += last_result.cost_metrics.cost_usd
                total_cost.duration_ms += last_result.cost_metrics.duration_ms

                inline_eval_payload: dict[str, Any] | None = None

                if not settings.inline_eval_enabled:
                    last_result = last_result.model_copy(
                        update={"cost_metrics": total_cost, "inline_eval": None}
                    )
                    break

                if settings.inline_eval_enabled and not judge_available:
                    last_result = last_result.model_copy(
                        update={
                            "cost_metrics": total_cost,
                            "inline_eval": {
                                "enabled": False,
                                "skipped_reason": "no_openai_api_key",
                            },
                        }
                    )
                    break

                # Inline eval ON and OpenAI configured — judge successful invocations only
                if last_result.status != "SUCCESS":
                    last_result = last_result.model_copy(
                        update={
                            "cost_metrics": total_cost,
                            "inline_eval": {
                                "enabled": True,
                                "skipped": True,
                                "reason": "agent_status_not_success",
                            },
                        }
                    )
                    break

                attempt_meta = await self._evaluate_once(
                    tenant_id=tenant_id,
                    execution_id=execution_id,
                    step_id=step_id,
                    skill_id=skill_id,
                    goal=goal,
                    attempt_index=attempt,
                    output=last_result.output,
                )
                ecost = attempt_meta.get("evaluation_cost")
                if isinstance(ecost, (int, float)):
                    total_cost.cost_usd += float(ecost)
                attempts_meta.append(attempt_meta)

                emit_inline_eval_score(
                    passed=bool(attempt_meta.get("passed")),
                    score=attempt_meta.get("score"),
                    attempt_index=attempt,
                    skill_id=skill_id,
                    metadata={
                        "execution_id": str(execution_id),
                        "step_id": str(step_id),
                    },
                    reason=str(attempt_meta.get("reason") or ""),
                )

                passed = bool(attempt_meta.get("passed"))
                if passed or attempt >= max_attempts - 1:
                    last_result = last_result.model_copy(
                        update={
                            "cost_metrics": total_cost,
                            "inline_eval": self._build_inline_eval_payload(
                                attempts_meta, final_pass=passed
                            ),
                        }
                    )
                    break

                feedback = (attempt_meta.get("reason") or "").strip()
                if feedback:
                    working_input[_FEEDBACK_KEY] = (
                        "Previous attempt did not meet quality bar. Improve using this "
                        f"feedback:\n{feedback[:6000]}"
                    )
                else:
                    working_input[_FEEDBACK_KEY] = (
                        "Previous attempt did not meet the inline quality bar; revise "
                        "and fully address the goal."
                    )

        assert last_result is not None
        logger.info(
            "ExecutionEngine finished skill=%s status=%s execution_id=%s inline_eval=%s",
            skill_id,
            last_result.status,
            execution_id,
            last_result.inline_eval is not None,
        )
        return last_result

    @staticmethod
    def _has_openai_for_judge() -> bool:
        return bool((settings.openai_api_key or "").strip() or os.getenv("OPENAI_API_KEY"))

    async def _invoke_agent(
        self,
        client: httpx.AsyncClient,
        agent_endpoint: str,
        ctx: AgentInvocationContext,
    ) -> AgentInvocationResult:
        response = await client.post(
            f"{agent_endpoint}/invoke",
            json=ctx.model_dump(mode="json"),
            headers={"Content-Type": "application/json"},
        )
        response.raise_for_status()
        return AgentInvocationResult.model_validate(response.json())

    async def _evaluate_once(
        self,
        *,
        tenant_id: uuid.UUID,
        execution_id: uuid.UUID,
        step_id: uuid.UUID,
        skill_id: str,
        goal: str,
        attempt_index: int,
        output: dict[str, Any],
    ) -> dict[str, Any]:
        judge = await run_inline_judge(goal=goal, skill_id=skill_id, output=output)

        snippet = extract_output_text_for_eval(output)[:4000]
        details = {
            "attempt": attempt_index,
            "score": judge.score,
            "threshold": judge.threshold,
            "reason": judge.reason,
            "error": judge.error,
            "evaluation_cost": judge.evaluation_cost,
        }

        await persist_inline_eval_attempt(
            tenant_id,
            execution_id=execution_id,
            step_id=step_id,
            skill_id=skill_id,
            attempt_index=attempt_index,
            passed=judge.passed,
            score=judge.score,
            threshold=judge.threshold,
            metric_name=judge.metric_name,
            reason=judge.reason,
            judge_model=judge.evaluation_model,
            output_snippet=snippet,
            details=details,
        )

        return {
            "attempt": attempt_index,
            "passed": judge.passed,
            "score": judge.score,
            "threshold": judge.threshold,
            "reason": judge.reason,
            "metric_name": judge.metric_name,
            "evaluation_model": judge.evaluation_model,
            "evaluation_cost": judge.evaluation_cost,
            "error": judge.error,
        }

    @staticmethod
    def _build_inline_eval_payload(
        attempts: list[dict[str, Any]], *, final_pass: bool
    ) -> dict[str, Any]:
        retries_used = max(0, len(attempts) - 1)
        return {
            "enabled": True,
            "metric_name": settings.inline_eval_metric_name,
            "threshold": settings.inline_eval_threshold,
            "attempts": attempts,
            "final_passed": final_pass,
            "retries_used": retries_used,
            "max_retries": settings.inline_eval_max_retries,
        }
