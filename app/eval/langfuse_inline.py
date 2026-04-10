"""Optional Langfuse scores for inline evaluation (when tracing is active)."""

from __future__ import annotations

import logging
from typing import Any

from app.core.config import settings
from app.core.langfuse_logs import emit_langfuse_log_event

logger = logging.getLogger(__name__)


def _otel_trace_active() -> bool:
    try:
        from opentelemetry import trace as otel_trace

        return otel_trace.get_current_span().get_span_context().is_valid
    except Exception:
        return False


def emit_inline_eval_score(
    *,
    passed: bool,
    score: float | None,
    attempt_index: int,
    skill_id: str,
    metadata: dict[str, Any],
    reason: str | None = None,
) -> None:
    if not settings.langfuse_enabled or not _otel_trace_active():
        return
    try:
        from app.core.langfuse_setup import init_langfuse
        from langfuse import get_client

        init_langfuse()
        lf = get_client()
        comment = f"attempt={attempt_index} skill={skill_id}"
        lf.score_current_span(
            name="inline_eval:goal_adequacy",
            value=1.0 if passed else 0.0,
            data_type="BOOLEAN",
            comment=comment[:500],
            metadata={**metadata, "passed": passed, "score": score},
        )
        lines = [
            f"inline_eval attempt={attempt_index} skill={skill_id}",
            f"passed={passed} score={score}",
        ]
        if reason:
            lines.append(reason[:8000])
        emit_langfuse_log_event(
            name="inline_eval:goal_adequacy",
            output="\n".join(lines),
            metadata={
                **metadata,
                "skill_id": skill_id,
                "attempt_index": attempt_index,
                "passed": passed,
                "score": score,
            },
            level="WARNING" if not passed else "DEFAULT",
        )
    except Exception:
        logger.debug("Langfuse inline_eval score skipped", exc_info=True)
