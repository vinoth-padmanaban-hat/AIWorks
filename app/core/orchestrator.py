"""
Orchestrator — control-plane entry point per execution.

Generic flow:
  1. Resolve persona.
  2. Load effective policy.
  3. Load available skills, filter by policy.
  4. Call Planner → get skill execution plan.
  5. Dispatch plan steps via Execution Engine.
  6. Aggregate results → return ExecuteResponse.

The orchestrator does NOT contain domain logic. All work is delegated to
agents discovered via the Agent Registry.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.execution_engine import ExecutionEngine
from app.core.langfuse_setup import langfuse_trace
from app.domain.models.invocation import (
    AgentInvocationResult,
    CostMetrics,
    ExecuteRequest,
    ExecuteResponse,
    ExecuteStepResult,
)
from app.domain.personas import (
    PersonaRepository,
    PersonaSnapshot,
    build_persona_summary,
)
from app.domain.policy.engine import PolicyEngine
from app.domain.registries.skill_registry import SkillRegistry
from app.orchestration.planner import Planner, PlanStep

logger = logging.getLogger(__name__)


class Orchestrator:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._engine = ExecutionEngine(db)
        self._policy = PolicyEngine(db)
        self._skills = SkillRegistry(db)

    async def execute(self, request: ExecuteRequest) -> ExecuteResponse:
        """
        Generic execution entry point.

        Flow:
          Persona → Policy → Skills (filtered) → Planner → Execution Engine → Results
        """
        execution_id = uuid.uuid4()
        logger.info(
            "Orchestrator.execute  execution_id=%s  tenant_id=%s  goal=%.100s",
            execution_id,
            request.tenant_id,
            request.goal,
        )

        # Root Langfuse / OTel observation so inline_eval scores and any future
        # control-plane guardrail scores attach to this trace (session_id=execution_id).
        with langfuse_trace(
            tenant_id=request.tenant_id,
            execution_id=execution_id,
            service="control_plane",
        ):
            return await self._execute_inner(request, execution_id)

    async def _execute_inner(
        self, request: ExecuteRequest, execution_id: uuid.UUID
    ) -> ExecuteResponse:
        # ── Step 0: resolve persona ──────────────────────────────────────────
        persona_repo = PersonaRepository(self._db)
        persona_row = await persona_repo.resolve_for_execution(
            request.tenant_id, request.persona_id
        )
        if persona_row is None:
            msg = (
                "No active persona for this tenant. "
                "Insert at least one row in `personas` and set is_default=TRUE."
            )
            if request.persona_id is not None:
                msg = f"Persona {request.persona_id} not found or inactive for tenant."
            logger.warning(msg)
            return ExecuteResponse(
                execution_id=execution_id,
                tenant_id=request.tenant_id,
                status="ERROR",
                goal=request.goal,
                error=msg,
            )

        snapshot = PersonaSnapshot.from_record(persona_row)
        persona_summary = build_persona_summary(snapshot)
        logger.info(
            "Persona resolved  persona_id=%s  name=%s",
            snapshot.persona_id,
            snapshot.display_name,
        )

        # ── Step 1: resolve effective policy ─────────────────────────────────
        effective_policy = await self._policy.get_effective_policy(request.tenant_id)

        # ── Step 2: load skills, filter by policy ────────────────────────────
        all_skills = await self._skills.list_skills(active_only=True)
        allowed_skills = await self._skills.filter_by_policy(all_skills, effective_policy)

        if request.skill_ids:
            allowed_skills = [
                s for s in allowed_skills if s.skill_id in request.skill_ids
            ]

        if not allowed_skills:
            msg = "No skills available after policy filtering."
            logger.warning(msg)
            return ExecuteResponse(
                execution_id=execution_id,
                tenant_id=request.tenant_id,
                status="ERROR",
                goal=request.goal,
                error=msg,
            )

        logger.info(
            "Skills available: %s",
            [s.skill_id for s in allowed_skills],
        )

        # ── Step 3: plan ─────────────────────────────────────────────────────
        planner = Planner()
        plan_steps = await planner.plan(
            goal=request.goal,
            persona=snapshot,
            available_skills=allowed_skills,
            effective_policy=effective_policy,
            tenant_id=request.tenant_id,
            execution_id=execution_id,
        )

        if not plan_steps:
            msg = "Planner returned an empty plan for the given goal."
            logger.warning(msg)
            return ExecuteResponse(
                execution_id=execution_id,
                tenant_id=request.tenant_id,
                status="ERROR",
                goal=request.goal,
                error=msg,
            )

        plan_dicts = [
            {"step_id": str(s.step_id), "skill_id": s.skill_id, "input_spec": s.input_spec}
            for s in plan_steps
        ]
        logger.info("Plan: %d steps → %s", len(plan_steps), [s.skill_id for s in plan_steps])

        # ── Step 4: execute plan steps ───────────────────────────────────────
        step_results: list[ExecuteStepResult] = []
        total_cost = CostMetrics()
        overall_status = "SUCCESS"

        for plan_step in plan_steps:
            policy_check = await self._policy.check_skill(
                skill_id=plan_step.skill_id,
                tenant_id=request.tenant_id,
                effective_policy=effective_policy,
            )
            if not policy_check.allowed:
                reasons = "; ".join(v.reason for v in policy_check.violations)
                logger.warning(
                    "Policy blocked skill=%s: %s", plan_step.skill_id, reasons
                )
                step_results.append(
                    ExecuteStepResult(
                        step_id=plan_step.step_id,
                        skill_id=plan_step.skill_id,
                        status="ERROR",
                        error=f"Policy violation: {reasons}",
                    )
                )
                overall_status = "PARTIAL"
                continue

            skill_input: dict[str, Any] = {
                "tenant_id": str(request.tenant_id),
                "execution_id": str(execution_id),
                "persona_id": str(snapshot.persona_id),
                "goal": request.goal,
                **(plan_step.input_spec or {}),
            }

            try:
                result: AgentInvocationResult = await self._engine.execute_skill(
                    skill_id=plan_step.skill_id,
                    skill_input=skill_input,
                    tenant_id=request.tenant_id,
                    execution_id=execution_id,
                    persona_id=snapshot.persona_id,
                    persona=snapshot,
                    persona_summary=persona_summary,
                    effective_policy=effective_policy,
                    goal=request.goal,
                )

                step_results.append(
                    ExecuteStepResult(
                        step_id=plan_step.step_id,
                        skill_id=plan_step.skill_id,
                        status=result.status,
                        output=result.output,
                        cost=result.cost_metrics,
                        error=result.error,
                        inline_eval=result.inline_eval,
                    )
                )

                total_cost.tokens_in += result.cost_metrics.tokens_in
                total_cost.tokens_out += result.cost_metrics.tokens_out
                total_cost.cost_usd += result.cost_metrics.cost_usd
                total_cost.duration_ms += result.cost_metrics.duration_ms

                if result.status == "ERROR":
                    overall_status = "PARTIAL"

            except Exception as exc:
                logger.exception(
                    "Execution failed for skill=%s: %s", plan_step.skill_id, exc
                )
                step_results.append(
                    ExecuteStepResult(
                        step_id=plan_step.step_id,
                        skill_id=plan_step.skill_id,
                        status="ERROR",
                        error=str(exc),
                    )
                )
                overall_status = "PARTIAL"

        if all(s.status == "ERROR" for s in step_results):
            overall_status = "ERROR"

        logger.info(
            "Orchestrator done  execution_id=%s  status=%s  cost=$%.6f",
            execution_id,
            overall_status,
            total_cost.cost_usd,
        )

        return ExecuteResponse(
            execution_id=execution_id,
            tenant_id=request.tenant_id,
            status=overall_status,
            goal=request.goal,
            plan=plan_dicts,
            steps=step_results,
            cost=total_cost,
        )

    # ── Backward compatibility ────────────────────────────────────────────────

    async def run_content_ingestion(
        self,
        tenant_id: uuid.UUID,
        persona_id: uuid.UUID | None = None,
    ) -> AgentInvocationResult:
        """Legacy wrapper — delegates to the generic execute flow."""
        response = await self.execute(
            ExecuteRequest(
                tenant_id=tenant_id,
                persona_id=persona_id,
                goal="Run content ingestion: scrape configured sources, normalize, tag, and format articles.",
                skill_ids=["content_ingestion"],
            )
        )

        step = response.steps[0] if response.steps else None
        return AgentInvocationResult(
            execution_id=response.execution_id,
            step_id=step.step_id if step else uuid.uuid4(),
            skill_id=step.skill_id if step else "content_ingestion",
            status=response.status,
            output=step.output if step else {},
            cost_metrics=response.cost,
            error=response.error or (step.error if step else None),
            inline_eval=step.inline_eval if step else None,
        )
