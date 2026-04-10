"""
Attach human-readable guardrail / eval lines to Langfuse as EVENT observations.

Requires an active OpenTelemetry span (e.g. control_plane trace or agent graph trace)
and Langfuse API keys. Scores alone are not full logs; events show up in the trace
timeline with input/output text.
"""

from __future__ import annotations

import logging
from typing import Any

from opentelemetry import trace as otel_trace
from opentelemetry.trace import format_span_id, format_trace_id

from app.core.config import settings

logger = logging.getLogger(__name__)


def emit_langfuse_log_event(
    name: str,
    *,
    output: str | None = None,
    metadata: dict[str, Any] | None = None,
    level: str | None = None,
) -> None:
    """
    Create a Langfuse Event linked to the current trace/span (child of parent span).

    No-op when Langfuse is disabled or no valid OTel context.
    """
    if not settings.langfuse_enabled:
        return
    span = otel_trace.get_current_span()
    ctx = span.get_span_context()
    if not ctx.is_valid:
        return
    try:
        from app.core.langfuse_setup import init_langfuse
        from langfuse import get_client
        from langfuse.types import TraceContext

        init_langfuse()
        trace_context: TraceContext = {
            "trace_id": format_trace_id(ctx.trace_id),
            "parent_span_id": format_span_id(ctx.span_id),
        }
        lf = get_client()
        lf.create_event(
            name=name[:200],
            trace_context=trace_context,
            output=(output or "")[:16000] or None,
            metadata=metadata,
            level=level,  # type: ignore[arg-type]
        )
    except Exception:
        logger.debug("Langfuse log event skipped", exc_info=True)
