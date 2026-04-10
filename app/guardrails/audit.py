"""
Structured audit logging and optional Langfuse scores for guardrail outcomes.

Logs use event=guardrail so log pipelines / JSON formatters can filter.
When Langfuse is enabled and an OpenTelemetry span is active (e.g. inside
langfuse_trace), we also record a BOOLEAN score on the current span.
"""

from __future__ import annotations

import logging
from typing import Any

from app.core.config import settings
from app.core.langfuse_logs import emit_langfuse_log_event

_logger = logging.getLogger("app.guardrails")


def log_guardrail_result(
    guard_type: str,
    *,
    passed: bool,
    reason: str = "",
    tool_id: str | None = None,
    tenant_id: str = "",
    execution_id: str = "",
    extra: dict[str, Any] | None = None,
) -> None:
    """
    Log a single guardrail check outcome.

    - pass → INFO
    - fail → WARNING (includes truncated reason)
    """
    payload: dict[str, Any] = {
        "event": "guardrail",
        "guard_type": guard_type,
        "outcome": "pass" if passed else "fail",
    }
    if tool_id:
        payload["tool_id"] = tool_id
    if tenant_id:
        payload["tenant_id"] = tenant_id
    if execution_id:
        payload["execution_id"] = execution_id
    if extra:
        payload.update(extra)

    if passed:
        _logger.info(
            "[guardrail] %s pass",
            guard_type,
            extra=payload,
        )
    else:
        payload["failure_reason"] = (reason or "")[:500]
        _logger.warning(
            "[guardrail] %s fail: %s",
            guard_type,
            (reason or "")[:300],
            extra=payload,
        )

    meta_for_lf = {k: v for k, v in payload.items() if k != "event"}
    _emit_langfuse_score(
        guard_type=guard_type,
        passed=passed,
        reason=reason,
        metadata=meta_for_lf,
    )
    _emit_langfuse_guardrail_event(
        guard_type=guard_type,
        passed=passed,
        reason=reason,
        metadata=meta_for_lf,
    )


def _otel_trace_active() -> bool:
    """True when OpenTelemetry has a real current span (e.g. inside langfuse_trace)."""
    try:
        from opentelemetry import trace as otel_trace

        return otel_trace.get_current_span().get_span_context().is_valid
    except Exception:
        return False


def _emit_langfuse_guardrail_event(
    *,
    guard_type: str,
    passed: bool,
    reason: str,
    metadata: dict[str, Any],
) -> None:
    """Full-text line in Langfuse trace (EVENT), not only a score."""
    lines = [f"guardrail:{guard_type}", "PASS" if passed else "FAIL"]
    if reason:
        lines.append(reason[:8000])
    body = "\n".join(lines)
    emit_langfuse_log_event(
        name=f"guardrail:{guard_type}",
        output=body,
        metadata={**metadata, "guard_type": guard_type, "passed": passed},
        level="WARNING" if not passed else "DEFAULT",
    )


def _emit_langfuse_score(
    *,
    guard_type: str,
    passed: bool,
    reason: str,
    metadata: dict[str, Any],
) -> None:
    if not settings.langfuse_enabled or not _otel_trace_active():
        return
    try:
        from app.core.langfuse_setup import init_langfuse
        from langfuse import get_client

        init_langfuse()
        lf = get_client()
        lf.score_current_span(
            name=f"guardrail:{guard_type}",
            value=1.0 if passed else 0.0,
            data_type="BOOLEAN",
            comment=(reason[:500] if reason else ("ok" if passed else "blocked")),
            metadata=metadata,
        )
    except Exception:
        _logger.debug("Langfuse guardrail score skipped", exc_info=True)
