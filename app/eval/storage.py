"""Persist inline eval attempts to the tenant database (best-effort)."""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError, SQLAlchemyError

from app.core.tenant_db import get_tenant_db_session

logger = logging.getLogger(__name__)


async def persist_inline_eval_attempt(
    tenant_id: uuid.UUID,
    *,
    execution_id: uuid.UUID,
    step_id: uuid.UUID,
    skill_id: str,
    attempt_index: int,
    passed: bool,
    score: float | None,
    threshold: float | None,
    metric_name: str,
    reason: str,
    judge_model: str | None,
    output_snippet: str,
    details: dict[str, Any],
) -> None:
    """Insert one row into inline_eval_runs. Ignores failures (missing table, DB down)."""
    sql = text(
        """
        INSERT INTO inline_eval_runs (
            execution_id, step_id, skill_id, attempt_index,
            passed, score, threshold, metric_name, reason, judge_model,
            output_snippet, details_json
        ) VALUES (
            :execution_id, :step_id, :skill_id, :attempt_index,
            :passed, :score, :threshold, :metric_name, :reason, :judge_model,
            :output_snippet, CAST(:details_json AS JSONB)
        )
        """
    )
    payload = {
        "execution_id": execution_id,
        "step_id": step_id,
        "skill_id": skill_id,
        "attempt_index": attempt_index,
        "passed": passed,
        "score": score,
        "threshold": threshold,
        "metric_name": metric_name[:200],
        "reason": (reason or "")[:12000],
        "judge_model": (judge_model or "")[:200],
        "output_snippet": (output_snippet or "")[:4000],
        "details_json": json.dumps(details, default=str),
    }
    try:
        async with get_tenant_db_session(tenant_id) as session:
            await session.execute(sql, payload)
            await session.commit()
    except ProgrammingError as exc:
        logger.debug(
            "inline_eval_runs insert skipped (schema?): %s",
            exc,
        )
    except SQLAlchemyError as exc:
        logger.warning("inline_eval_runs insert failed: %s", exc)
