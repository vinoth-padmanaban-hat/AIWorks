"""Async LLM-as-judge for inline (online) evaluation using DeepEval GEval."""

from __future__ import annotations

import logging
import os

# Avoid PostHog telemetry on the hot execution path.
os.environ.setdefault("DEEPEVAL_TELEMETRY_OPT_OUT", "1")

from dataclasses import dataclass
from typing import Any

from deepeval.metrics import GEval
from deepeval.test_case import LLMTestCase, LLMTestCaseParams

from app.core.config import settings
from app.eval.output_text import extract_output_text_for_eval

logger = logging.getLogger(__name__)

_DEFAULT_CRITERIA = (
    "Given the user goal and the agent's response, decide if the response adequately "
    "addresses the goal: it should be on-topic, actionable or informative as appropriate, "
    "and should not be trivially wrong, evasive, or empty. Be strict but fair."
)


@dataclass
class InlineJudgeResult:
    passed: bool
    score: float | None
    threshold: float
    reason: str
    metric_name: str
    evaluation_model: str | None
    evaluation_cost: float | None
    error: str | None


def _ensure_openai_env() -> bool:
    key = (settings.openai_api_key or "").strip()
    if key and not os.getenv("OPENAI_API_KEY"):
        os.environ["OPENAI_API_KEY"] = key
    return bool(os.getenv("OPENAI_API_KEY"))


async def run_inline_judge(
    *,
    goal: str,
    skill_id: str,
    output: dict[str, Any],
    criteria: str | None = None,
) -> InlineJudgeResult:
    """
    Run a single GEval judgment on goal + structured output.

    On unexpected errors, returns passed=True (fail-open) so execution is not blocked.
    """
    if not _ensure_openai_env():
        return InlineJudgeResult(
            passed=True,
            score=None,
            threshold=settings.inline_eval_threshold,
            reason="",
            metric_name=settings.inline_eval_metric_name,
            evaluation_model=None,
            evaluation_cost=None,
            error="missing_openai_api_key",
        )

    actual_text = extract_output_text_for_eval(output)
    if not actual_text.strip():
        return InlineJudgeResult(
            passed=False,
            score=0.0,
            threshold=settings.inline_eval_threshold,
            reason="No evaluable text in agent output.",
            metric_name=settings.inline_eval_metric_name,
            evaluation_model=settings.openai_model,
            evaluation_cost=None,
            error=None,
        )

    metric = GEval(
        name=settings.inline_eval_metric_name,
        criteria=criteria or _DEFAULT_CRITERIA,
        evaluation_params=[
            LLMTestCaseParams.INPUT,
            LLMTestCaseParams.ACTUAL_OUTPUT,
        ],
        model=settings.openai_model,
        threshold=settings.inline_eval_threshold,
        async_mode=True,
        verbose_mode=False,
    )

    test_case = LLMTestCase(
        input=f"Goal:\n{goal}\n\nSkill: {skill_id}",
        actual_output=actual_text,
    )

    try:
        await metric.a_measure(test_case, _show_indicator=False)
    except Exception as exc:
        logger.warning("Inline judge failed (fail-open): %s", exc, exc_info=True)
        return InlineJudgeResult(
            passed=True,
            score=None,
            threshold=settings.inline_eval_threshold,
            reason="",
            metric_name=settings.inline_eval_metric_name,
            evaluation_model=settings.openai_model,
            evaluation_cost=None,
            error=str(exc)[:500],
        )

    passed = bool(metric.is_successful())
    return InlineJudgeResult(
        passed=passed,
        score=metric.score,
        threshold=settings.inline_eval_threshold,
        reason=(metric.reason or "")[:8000],
        metric_name=settings.inline_eval_metric_name,
        evaluation_model=getattr(metric, "evaluation_model", None) or settings.openai_model,
        evaluation_cost=metric.evaluation_cost,
        error=(metric.error or None),
    )
